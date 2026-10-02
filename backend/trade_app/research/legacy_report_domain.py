"""Read the actual ftbt-1.0 protocol without fabricating runnable new research.

Original HTML/XLSX are opaque untrusted attachments. Only validated JSON facts
are displayed; old performance and timing remain unverified source claims.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import html
import io
import re
import stat
import struct
import zipfile
import zlib

from trade_app.platform.types import TradeError
from trade_app.research.report_domain import digest, encode, strict_json

MAX_BYTES = 16 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_MEMBERS = 2006
MAX_CENTRAL_BYTES = 1024 * 1024
FORMAT = 'trade.legacy-readonly-report-v1'
KEY = re.compile(r'^[A-Za-z0-9._-]{4,96}$')
DETAIL = re.compile(r'^plateau_point_detail__([A-Za-z0-9._-]{4,96})\.json$')
BASE_FILES = {'run_request.json', 'run_result.json', 'report.html', 'report.xlsx'}
PARAMS = ('window_days', 'min_score', 'stop_loss', 'take_profit', 'trailing_stop_pct',
          'max_positions', 'position_pct', 'max_symbols', 'priority_topk_per_day')
LIMITATIONS = [
    '旧报告仅供只读查阅；指标、成交和排名是来源记录，未在新版重新计算。',
    '原包没有完整冻结行情与新版代码/执行版本证据链，不能创建、恢复或继续新版回测/平原任务。',
    '源发生日、成交日、费用、信号可得性及旧排名公式按原记录保留；未证明满足新版时点规则。',
    '原 HTML 与 Excel 仅作为不可信原件保存，不在应用中执行脚本、外部链接或公式。',
]


def _invalid(path: str, message: str):
    raise TradeError('LEGACY_REPORT_INVALID', f'{path}: {message}')


def _required(value, names, path, missing):
    if not isinstance(value, dict):
        missing.append(path)
        return False
    absent = [f'{path}.{name}' for name in names if name not in value or value[name] is None]
    missing.extend(absent)
    return not absent


def _number(value, path, *, integer=False):
    try:
        if isinstance(value, bool) or len(str(value)) > 64:
            raise ValueError()
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) > Decimal('1e18') or number.as_tuple().exponent < -18:
            raise ValueError()
        if integer and (type(value) is not int or number < 0):
            raise ValueError()
    except (ValueError, InvalidOperation) as exc:
        raise TradeError('LEGACY_REPORT_INVALID', f'{path}: 数值无效') from exc


def _day(value, path):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError as exc:
        raise TradeError('LEGACY_REPORT_INVALID', f'{path}: 日期须为 YYYY-MM-DD') from exc


def _timestamp(value, path):
    _text(value, path, 64)
    try:
        if 'T' not in value: raise ValueError()
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise TradeError('LEGACY_REPORT_INVALID', f'{path}: 原时间戳格式无效') from exc


def _text(value, path, maximum=256):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or any(ord(ch) < 32 for ch in value):
        _invalid(path, '文本为空、无效或超过长度限制')


def _rows(value, path, maximum=5000):
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(row, dict) for row in value):
        _invalid(path, f'须为对象数组，最多 {maximum} 条')
    return value


def _request(value, path, missing):
    if not _required(value, ('date_from', 'date_to', 'strategy_id', 'strategy_params', 'initial_capital'), path, missing):
        return
    for key in ('date_from', 'date_to'): _day(value[key], path + '.' + key)
    if value['date_from'] > value['date_to']: _invalid(path, '起始日期晚于结束日期')
    _text(value['strategy_id'], path + '.strategy_id', 96)
    _number(value['initial_capital'], path + '.initial_capital')
    if Decimal(str(value['initial_capital'])) <= 0: _invalid(path + '.initial_capital', '初始资金须大于零')
    if not isinstance(value['strategy_params'], dict): _invalid(path + '.strategy_params', '策略参数须为对象')


def _stats(value, path, missing):
    if not _required(value, ('win_rate', 'total_return', 'max_drawdown', 'avg_pnl_ratio'), path, missing): return
    for key in ('win_rate', 'total_return', 'max_drawdown', 'avg_pnl_ratio', 'profit_factor'):
        if key in value: _number(value[key], path + '.' + key)
    for key in ('trade_count', 'win_count', 'loss_count'):
        if key in value: _number(value[key], path + '.' + key, integer=True)


def _run(request, result, path, missing, flags):
    _request(request, path + '.run_request', missing)
    if not _required(result, ('stats', 'trades', 'range'), path + '.run_result', missing): return
    _stats(result['stats'], path + '.run_result.stats', missing)
    if _required(result['range'], ('date_from', 'date_to'), path + '.run_result.range', missing):
        for key in ('date_from', 'date_to'): _day(result['range'][key], path + '.run_result.range.' + key)
        if isinstance(request, dict) and any(key in request and request[key] != result['range'][key] for key in ('date_from', 'date_to')):
            flags.add('source_request_result_date_range_differs')
    trades = _rows(result['trades'], path + '.run_result.trades')
    for i, trade in enumerate(trades):
        item = f'{path}.run_result.trades[{i}]'
        if not _required(trade, ('symbol', 'signal_date', 'entry_date', 'exit_date', 'quantity', 'entry_price', 'exit_price', 'pnl_amount', 'pnl_ratio'), item, missing): continue
        _text(trade['symbol'], item + '.symbol', 32)
        for key in ('signal_date', 'entry_date', 'exit_date'): _day(trade[key], item + '.' + key)
        _number(trade['quantity'], item + '.quantity', integer=True)
        for key in ('entry_price', 'exit_price', 'pnl_amount', 'pnl_ratio'): _number(trade[key], item + '.' + key)
    if isinstance(result['stats'], dict) and result['stats'].get('trade_count') is not None and result['stats']['trade_count'] != len(trades):
        flags.add('source_trade_count_differs_from_rows')
    for key, field in (('equity_curve', 'equity'), ('drawdown_curve', 'drawdown')):
        if key not in result:
            flags.add(key + '_not_provided')
            continue
        previous = None
        for i, point in enumerate(_rows(result[key], path + '.run_result.' + key)):
            name = f'{path}.run_result.{key}[{i}]'
            if not _required(point, ('date', field), name, missing): continue
            _day(point['date'], name + '.date'); _number(point[field], name + '.' + field)
            if previous is not None and point['date'] <= previous: _invalid(name, '日期须严格升序且不可重复')
            previous = point['date']
    if result.get('effective_run_request') is not None:
        _request(result['effective_run_request'], path + '.run_result.effective_run_request', missing)
        if result['effective_run_request'] != request: flags.add('source_effective_request_differs')


def _params(value, path, missing):
    if not _required(value, PARAMS, path, missing): return
    for key in PARAMS: _number(value[key], path + '.' + key)
    if 'intraday_trailing_reduce_ratio' in value: _number(value['intraday_trailing_reduce_ratio'], path + '.intraday_trailing_reduce_ratio')


def _plateau(value, details, missing, flags):
    if not _required(value, ('base_payload', 'total_combinations', 'evaluated_combinations', 'points', 'generated_at'), 'plateau_result', missing): return
    _request(value['base_payload'], 'plateau_result.base_payload', missing)
    for key in ('total_combinations', 'evaluated_combinations'): _number(value[key], 'plateau_result.' + key, integer=True)
    _timestamp(value['generated_at'], 'plateau_result.generated_at')
    for key in ('regions', 'correlations'):
        if key in value: _rows(value[key], 'plateau_result.' + key)
    if 'notes' in value and (not isinstance(value['notes'], list) or any(not isinstance(note, str) for note in value['notes'])):
        _invalid('plateau_result.notes', '须为文本数组')
    points = _rows(value['points'], 'plateau_result.points', 2000)
    by_key = {}
    for i, point in enumerate(points):
        path = f'plateau_result.points[{i}]'
        if not _required(point, ('params', 'stats'), path, missing): continue
        _params(point['params'], path + '.params', missing)
        _stats(point['stats'], path + '.stats', missing)
        for key in ('score', 'point_score', 'local_score', 'plateau_score', 'neighbor_pass_rate', 'neighbor_median_score', 'neighbor_p25_score', 'sensitivity_penalty'):
            if key in point: _number(point[key], path + '.' + key)
        key = point.get('detail_key')
        if key is not None:
            if not isinstance(key, str) or not KEY.fullmatch(key) or key in by_key: _invalid(path + '.detail_key', '明细键无效或重复')
            by_key[key] = point
    if value['evaluated_combinations'] != len(points): flags.add('source_evaluated_count_differs_from_points')
    if not points: flags.add('source_plateau_has_no_points')
    for key, detail in details.items():
        path = 'plateau_point_detail__' + key
        if key not in by_key: _invalid(path, '明细无法关联到原平原参数点')
        if not _required(detail, ('detail_key', 'params', 'run_request', 'run_result'), path, missing): continue
        if detail['detail_key'] != key or detail['params'] != by_key[key]['params']:
            _invalid(path, '文件名、明细键或参数与平原点不一致')
        _run(detail['run_request'], detail['run_result'], path, missing, flags)
        if isinstance(detail['run_request'], dict):
            for parameter, expected in detail['params'].items():
                if parameter not in detail['run_request']:
                    missing.append(path + '.run_request.' + parameter)
                elif detail['run_request'][parameter] != expected:
                    _invalid(path + '.run_request.' + parameter, '明细请求参数与平原点不一致')
    if set(by_key) - set(details): flags.add('some_plateau_point_details_not_in_package')
    flags.add('legacy_plateau_scores_not_recomputed')


def _zip_preflight(contents):
    """Bound both advertised and actual metadata before ZipFile builds ZipInfo."""
    if len(contents) < 22 or contents[-22:-18] != b'PK\x05\x06': _invalid('archive', '缺少 ZIP 尾部，或含不支持的注释/尾随内容')
    _, disk, cd_disk, disk_count, count, size, offset, comment = struct.unpack('<4s4H2LH', contents[-22:])
    if disk or cd_disk or disk_count != count or comment: _invalid('archive', '不支持分卷或注释 ZIP')
    boundary = len(contents) - 22
    locator = contents[boundary - 20:boundary] if boundary >= 20 else b''
    has_zip64 = locator[:4] == b'PK\x06\x07'
    if (count == 0xFFFF or size == 0xFFFFFFFF or offset == 0xFFFFFFFF) and not has_zip64:
        _invalid('archive', 'ZIP64 定位记录缺失')
    if has_zip64:
        legacy = count, size, offset
        _, disk, position, disks = struct.unpack('<4sLQL', locator)
        if disk or disks != 1 or position + 56 != boundary - 20: _invalid('archive', 'ZIP64 定位记录无效')
        sig, record_size, _, _, disk, cd_disk, disk_count, count, size, offset = struct.unpack('<4sQ2H2L4Q', contents[position:position + 56])
        if sig != b'PK\x06\x06' or record_size != 44 or disk or cd_disk or disk_count != count:
            _invalid('archive', 'ZIP64 尾部无效')
        if any(old != sentinel and old != new for old, new, sentinel in zip(legacy, (count, size, offset), (0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF))):
            _invalid('archive', 'ZIP64 尾部与旧尾部矛盾')
        boundary = position
    if not 1 <= count <= MAX_MEMBERS or size > MAX_CENTRAL_BYTES or offset + size != boundary:
        _invalid('archive', 'ZIP 元数据数量、大小或边界超限')
    position, actual = offset, 0
    while position < boundary:
        if contents[position:position + 4] != b'PK\x01\x02' or position + 46 > boundary: _invalid('archive', 'ZIP 目录记录无效')
        name_size, extra_size, comment_size = struct.unpack_from('<3H', contents, position + 28)
        position += 46 + name_size + extra_size + comment_size
        actual += 1
        if actual > MAX_MEMBERS or position > boundary: _invalid('archive', 'ZIP 目录数量或长度超限')
    if actual != count: _invalid('archive', 'ZIP 目录数量与尾部不符')


def _ftbt(contents):
    try:
        _zip_preflight(contents)
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= MAX_MEMBERS: _invalid('archive', '文件数量超过 2006 或为空')
            allowed, expanded = {}, 0
            for item in infos:
                name = item.filename
                if item.orig_filename != name or name.casefold() in {key.casefold() for key in allowed} or (name not in BASE_FILES | {'manifest.json', 'plateau_result.json'} and not DETAIL.fullmatch(name)):
                    _invalid('archive', '存在重复、越界或未知路径')
                if item.is_dir() or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG) or item.flag_bits & 1 or item.external_attr & 0x400:
                    _invalid('archive', '不接受目录、链接、特殊或加密文件')
                if item.file_size > MAX_BYTES or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED): _invalid(name, '单文件超过16 MiB或压缩类型不支持')
                expanded += item.file_size
                allowed[name] = item
            if expanded > MAX_EXPANDED: _invalid('archive', '解压总量超过64 MiB')
            if 'manifest.json' not in allowed: _invalid('manifest.json', '必需清单缺失，无法校验原包')
            manifest = strict_json(archive.read('manifest.json'), maximum=MAX_BYTES)
            if manifest.get('schema_version') != 'ftbt-1.0' or manifest.get('package_type') != 'backtest_report': _invalid('manifest', '不是支持的 ftbt-1.0 旧报告')
            if not isinstance(manifest.get('report_id'), str) or not KEY.fullmatch(manifest['report_id']): _invalid('manifest.report_id', '原报告 ID 无效')
            _timestamp(manifest.get('created_at'), 'manifest.created_at')
            if not isinstance(manifest.get('app'), dict): _invalid('manifest.app', '原应用信息缺失')
            for field in ('name', 'version'): _text(manifest['app'].get(field), 'manifest.app.' + field)
            if not isinstance(manifest.get('files'), list): _invalid('manifest.files', '缺少文件清单')
            registered, parts = set(), {}
            for item in manifest['files']:
                if not isinstance(item, dict) or set(item) != {'path', 'sha256', 'bytes'}: _invalid('manifest.files', '文件摘要字段无效')
                name = item['path']
                if not isinstance(name, str) or name == 'manifest.json' or name in registered or name not in allowed: _invalid('manifest.files.path', '路径缺失或重复')
                registered.add(name)
                if type(item['bytes']) is not int or item['bytes'] != allowed[name].file_size: _invalid(name, '字节数不符')
                raw = archive.read(name)
                if not isinstance(item['sha256'], str) or digest(raw) != item['sha256']: _invalid(name, 'SHA-256 不符')
                parts[name] = raw
            if registered != set(allowed) - {'manifest.json'}: _invalid('manifest.files', '包内文件未全部登记')
            missing_files = sorted(BASE_FILES - registered)
            decoded = {name: strict_json(raw, maximum=MAX_BYTES) for name, raw in parts.items() if name.endswith('.json')}
            return manifest, parts, decoded, missing_files
    except (zipfile.BadZipFile, zlib.error, ValueError, OverflowError, EOFError, RuntimeError, struct.error) as exc:
        raise TradeError('LEGACY_REPORT_INVALID', '旧报告压缩包损坏或格式无效') from exc


def prepare_import(contents: bytes, filename: str) -> dict:
    if not contents or len(contents) > MAX_BYTES: raise TradeError('LEGACY_REPORT_SIZE', '旧报告须非空且不超过 16 MiB')
    filename = str(filename or 'legacy-report').replace('\\', '/').split('/')[-1]
    _text(filename, 'filename', 180)
    missing, flags, attachments, manifest, request, result, plateau, details = [], set(), [], None, None, None, None, {}
    source_format = 'opaque_html'
    if contents.startswith(b'PK') or filename.lower().endswith(('.ftbt', '.zip')):
        manifest, parts, decoded, missing = _ftbt(contents)
        source_format = 'final_trade_ftbt_1.0'
        request, result, plateau = decoded.get('run_request.json'), decoded.get('run_result.json'), decoded.get('plateau_result.json')
        details = {DETAIL.fullmatch(name).group(1): value for name, value in decoded.items() if DETAIL.fullmatch(name)}
        attachments = [{'name': name, 'bytes': len(raw), 'sha256': digest(raw), 'untrusted': name in {'report.html', 'report.xlsx'}} for name, raw in parts.items()]
        _run(request, result, 'report', missing, flags)
        if plateau is not None: _plateau(plateau, details, missing, flags)
        elif details: _invalid('plateau_result.json', '有参数点明细但缺少平原结果')
    elif contents.lstrip().startswith(b'{') or filename.lower().endswith('.json'):
        value = strict_json(contents, maximum=MAX_BYTES)
        if 'base_payload' in value or 'evaluated_combinations' in value:
            source_format, plateau = 'final_trade_plateau_json', value
            _plateau(plateau, {}, missing, flags)
        elif isinstance(value.get('data'), dict) and isinstance(value.get('day'), str):
            source_format = 'laimiu_print_json'
            missing.extend(['run_request.json', 'run_result.json', 'manifest.json'])
        else:
            source_format = 'unrecognized_json'
            missing.extend(['ftbt-1.0 manifest', 'run_request.json', 'run_result.json', '或完整 BacktestPlateauResponse'])
    elif not filename.lower().endswith(('.html', '.htm')):
        _invalid('file', '仅接受旧 FTBT/ZIP、平原 JSON 或 HTML 原件')
    else:
        missing.extend(['run_request.json', 'run_result.json', 'manifest.json'])
    if not attachments:
        attachments = [{'name': filename, 'bytes': len(contents), 'sha256': digest(contents), 'untrusted': True}]
    if len(missing) > 500: missing = missing[:500] + ['其余缺失字段超过显示上限；不进行结构转换']
    missing = sorted(set(missing))
    if missing: flags.add('structured_conversion_unavailable')
    flags.update(('legacy_source_claims_unverified', 'frozen_market_inputs_absent', 'reexecution_unavailable'))
    base = request or (plateau or {}).get('base_payload') or {}
    if not isinstance(base, dict): base = {}
    stats = (result or {}).get('stats') or {}
    if not isinstance(stats, dict): stats = {}
    count = lambda value: len(value) if isinstance(value, (list, dict)) else None
    summary = {'strategy_id': base.get('strategy_id'), 'date_from': base.get('date_from'), 'date_to': base.get('date_to'),
               'initial_capital': base.get('initial_capital'), 'total_return': stats.get('total_return'),
               'max_drawdown': stats.get('max_drawdown'), 'win_rate': stats.get('win_rate'), 'reported_trade_count': stats.get('trade_count'),
               'trade_rows': count((result or {}).get('trades')), 'equity_points': count((result or {}).get('equity_curve')),
               'point_count': count((plateau or {}).get('points')), 'point_detail_count': len(details)}
    # Incomplete sources may contain malformed unvalidated fields. Preserve them
    # in the original/payload, but never promote nested data into list summaries.
    summary = {key: value if value is None or type(value) in (int, float) or isinstance(value, str) and len(value) <= 256 else None
               for key, value in summary.items()}
    payload = {'format': FORMAT, 'source_format': source_format, 'source_sha256': digest(contents), 'manifest': manifest,
               'run_request': request, 'run_result': result, 'plateau_result': plateau, 'point_details': details,
               'missing_fields': missing, 'quality_flags': sorted(flags), 'summary': summary, 'limitations': LIMITATIONS}
    preview = {'source_format': source_format, 'source_sha256': digest(contents), 'source_bytes': len(contents),
               'filename': filename, 'title': ('旧报告 · ' + (manifest['report_id'] if manifest else filename))[:120],
               'can_convert': not missing, 'missing_fields': missing, 'quality_flags': sorted(flags),
               'summary': summary, 'attachments': attachments, 'limitations': LIMITATIONS}
    preview['preview_sha256'] = digest(encode(preview))
    return {'preview': preview, 'payload': payload, 'contents': contents}


def attachment(contents: bytes, filename: str, name: str) -> bytes:
    prepared = prepare_import(contents, filename)
    if name not in {item['name'] for item in prepared['preview']['attachments']}: raise TradeError('LEGACY_REPORT_ATTACHMENT_NOT_FOUND', '该原件不存在', 404)
    if prepared['preview']['source_format'] == 'final_trade_ftbt_1.0':
        with zipfile.ZipFile(io.BytesIO(contents)) as archive: return archive.read(name)
    return contents


def render_safe_html(report):
    esc = lambda value: html.escape(str(value if value is not None else '未知'), quote=True)
    payload = report['payload']
    summary = ''.join('<tr><th>' + esc(key) + '</th><td>' + esc(value) + '</td></tr>' for key, value in payload['summary'].items())
    issues = ''.join('<li>' + esc(value) + '</li>' for value in payload['limitations'] + payload['missing_fields'])
    source_points = (payload.get('plateau_result') or {}).get('points', []) if report['mode'] == 'legacy_readonly' else []
    points = ''.join('<tr><td>' + str(i + 1) + '</td><td>' + esc(point.get('score')) + '</td><td>' + esc(point.get('plateau_score')) + '</td><td>' + esc(point.get('error')) + '</td></tr>' for i, point in enumerate(source_points))
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>' + esc(report['title']) + '</title><body><h1>' + esc(report['title']) + '</h1><p>' + esc(report['mode']) + ' · 旧来源记录，未重新计算</p><ul>' + issues + '</ul><table>' + summary + '</table><h2>原平原点得分（按源顺序）</h2><table><tr><th>序号</th><th>源得分</th><th>源平原分</th><th>源错误</th></tr>' + points + '</table><p>完整原始请求、结果、曲线和参数点请查看导出的 JSON；本页未执行原 HTML。</p></body></html>').encode('utf-8')
