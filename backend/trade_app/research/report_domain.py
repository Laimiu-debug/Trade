"""Bounded, data-only report package parsing and self-contained safe HTML."""
from __future__ import annotations

import hashlib
import html
import io
import json
import math
import re
import stat
import zipfile
import zlib
from datetime import datetime
from decimal import Decimal, DecimalException

from trade_app.market.domain import normalize_bars
from trade_app.platform.types import TradeError
from trade_app.research.runtime import SINGLE_SYMBOL_STRATEGIES


REPORT_FORMAT = 'trade.single-symbol-backtest'
PACKAGE_FORMAT = 'trade.report-package'
MAX_PACKAGE_BYTES = 16 * 1024 * 1024
PACKAGE_MEMBERS = {'manifest.json', 'report.json', 'report.html', 'report.xlsx'}
RUN_FIELDS = {'id', 'dataset_id', 'strategy_id', 'strategy_version', 'execution_version', 'calculation_version',
              'code_sha256', 'result_sha256', 'attempt_number', 'params', 'config', 'state', 'created_at',
              'updated_at', 'result'}
DATASET_FIELDS = {'id', 'symbol', 'provider', 'adjustment', 'first_date', 'last_date', 'bar_count',
                  'availability_quality', 'bars'}


def encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def strict_json(contents: bytes, *, maximum: int = MAX_PACKAGE_BYTES) -> dict:
    def unique(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                raise ValueError('Duplicate JSON key')
            value[key] = child
        return value

    def finite(_):
        raise ValueError('Nonfinite number')

    def bounded(value, level=0):
        if level > 32:
            raise ValueError('Nested JSON limit')
        if isinstance(value, dict):
            for key, child in value.items():
                if len(key) > 256:
                    raise ValueError('JSON key limit')
                bounded(child, level + 1)
        elif isinstance(value, list):
            if len(value) > 5000:
                raise ValueError('JSON array limit')
            for child in value:
                bounded(child, level + 1)
        elif isinstance(value, str) and len(value) > 262144:
            raise ValueError('JSON text limit')
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Nonfinite JSON number')

    try:
        if not contents or len(contents) > maximum:
            raise ValueError('JSON size limit')
        value = json.loads(contents.decode('utf-8'), object_pairs_hook=unique, parse_constant=finite)
        bounded(value)
        if not isinstance(value, dict):
            raise ValueError('JSON object required')
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise TradeError('INVALID_REPORT_JSON', '报告 JSON 无效、包含重复字段或超过大小/层级限制') from exc


def _keys(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError('Unexpected report fields')


def _text(value, maximum=256):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or any(ord(ch) < 32 for ch in value):
        raise ValueError('Invalid report text')


def _number(value):
    if isinstance(value, bool) or len(str(value)) > 64:
        raise ValueError('Invalid numeric field')
    number = Decimal(str(value))
    if not number.is_finite() or abs(number) > Decimal('1e18') or number.as_tuple().exponent < -18:
        raise ValueError('Unbounded numeric field')
    return number


def validate_report(value: dict) -> dict:
    """Validate transport shape/integrity without rerunning or trusting performance."""
    try:
        _keys(value, {'format', 'version', 'title', 'created_at', 'scope', 'run', 'dataset', 'bars_sha256'})
        if value['format'] != REPORT_FORMAT or type(value['version']) is not int or value['version'] != 1 or value['scope'] != 'single_symbol_backtest':
            raise ValueError('Unsupported report format')
        _text(value['title'], 120)
        if datetime.fromisoformat(value['created_at']).tzinfo is None:
            raise ValueError('Report time requires timezone')
        run, dataset = value['run'], value['dataset']
        _keys(run, RUN_FIELDS)
        _keys(dataset, DATASET_FIELDS)
        if run['state'] != 'succeeded' or run['strategy_id'] not in SINGLE_SYMBOL_STRATEGIES:
            raise ValueError('Only completed single-symbol runs are supported')
        for key in ('id', 'dataset_id', 'strategy_id', 'strategy_version', 'execution_version', 'calculation_version', 'code_sha256'):
            _text(run[key])
        for key in ('id', 'symbol', 'provider', 'adjustment', 'availability_quality'):
            _text(dataset[key])
        if run['dataset_id'] != dataset['id'] or not isinstance(run['params'], dict) or not isinstance(run['config'], dict):
            raise ValueError('Invalid frozen run metadata')
        if type(run['attempt_number']) is not int or run['attempt_number'] < 0:
            raise ValueError('Invalid attempt number')
        result = run['result']
        if not isinstance(result, dict) or result.get('strategy_id') != run['strategy_id'] or result.get('symbol') != dataset['symbol']:
            raise ValueError('Result identity mismatch')
        analysis = result.get('advanced_analysis')
        if analysis is not None:
            if not isinstance(analysis, dict) or analysis.get('status') not in {'generated', 'not_generated'}:
                raise ValueError('Invalid advanced analysis')
            if analysis['status'] == 'generated':
                for field in ('risk', 'stability', 'regimes', 'monte_carlo', 'methodology', 'walk_forward'):
                    if not isinstance(analysis.get(field), dict):
                        raise ValueError('Invalid analysis section')
                for rows in (analysis.get('completed_trades'), analysis['stability'].get('months'), analysis['regimes'].get('buckets')):
                    if not isinstance(rows, list) or len(rows) > 2000 or any(not isinstance(row, dict) for row in rows):
                        raise ValueError('Invalid analysis rows')
        if result.get('execution_profile_version') != run['execution_version'] or result.get('calculation_version') != run['calculation_version']:
            raise ValueError('Result version mismatch')
        if not isinstance(run['result_sha256'], str) or not re.fullmatch('[a-f0-9]{64}', run['result_sha256']) or digest(encode(result)) != run['result_sha256']:
            raise ValueError('Result digest mismatch')
        for key, cap in (('trades', 4000), ('equity', 2000), ('decisions', 2000)):
            if not isinstance(result.get(key), list) or len(result[key]) > cap or any(not isinstance(row, dict) for row in result[key]):
                raise ValueError('Invalid result rows')
        if not isinstance(result.get('quality_flags'), list) or not isinstance(result.get('limitations'), list):
            raise ValueError('Missing quality metadata')
        if any(not isinstance(item, str) for key in ('quality_flags', 'limitations') for item in result[key]):
            raise ValueError('Invalid quality metadata')
        if len(result['quality_flags']) > 128 or any(len(item) > 200 for item in result['quality_flags']):
            raise ValueError('Quality summary limit')
        if type(result.get('trade_count')) is not int or not 0 <= result['trade_count'] <= 2000:
            raise ValueError('Invalid trade count')
        if result.get('win_rate') is not None:
            _number(result['win_rate'])
        for key in ('initial_capital', 'ending_assets', 'total_return', 'max_drawdown', 'realized_pnl'):
            _number(result[key])
        for row in result['equity']:
            _text(row['date'])
            _number(row['total_assets'])
            _number(row['cash'])
            if type(row['quantity']) is not int or row['quantity'] < 0:
                raise ValueError('Invalid equity quantity')
        for row in result['trades']:
            _text(row['date'])
            if row['side'] not in ('buy', 'sell') or type(row['quantity']) is not int or row['quantity'] <= 0:
                raise ValueError('Invalid trade')
            for key in ('price', 'fees'):
                _number(row[key])
        if not isinstance(dataset['bars'], list) or not 32 <= len(dataset['bars']) <= 2000:
            raise ValueError('Invalid bar count')
        normalized = normalize_bars(dataset['bars'])
        if normalized != dataset['bars'] or dataset['bar_count'] != len(normalized):
            raise ValueError('Invalid frozen bars')
        if dataset['first_date'] != normalized[0]['event_date'] or dataset['last_date'] != normalized[-1]['event_date']:
            raise ValueError('Invalid bar period')
        if value['bars_sha256'] != digest(encode(dataset['bars'])):
            raise ValueError('Bar digest mismatch')
        return value
    except (KeyError, TypeError, ValueError, DecimalException, TradeError) as exc:
        raise TradeError('INVALID_REPORT_DATA', '报告结构、版本、行情或结果校验失败；仅支持当前单股完整报告包') from exc


def freeze_report(run: dict, dataset: dict, *, title: str, created_at: str) -> dict:
    if run.get('state') != 'succeeded' or not run.get('result'):
        raise TradeError('BACKTEST_NOT_READY', '回测尚未成功完成，不能保存报告', 409)
    frozen_run = {key: run.get(key) for key in RUN_FIELDS}
    # Earlier local completed jobs did not persist a result digest; freeze one now.
    actual_hash = digest(encode(run['result']))
    if frozen_run['result_sha256'] is not None and frozen_run['result_sha256'] != actual_hash:
        raise TradeError('BACKTEST_RESULT_CORRUPT', '回测结果与已保存摘要不一致', 409)
    frozen_run['result_sha256'] = actual_hash
    frozen_run['attempt_number'] = frozen_run['attempt_number'] or 0
    payload = {'format': REPORT_FORMAT, 'version': 1, 'scope': 'single_symbol_backtest',
               'title': title.strip(), 'created_at': created_at, 'run': frozen_run,
               'dataset': {key: dataset[key] for key in DATASET_FIELDS}, 'bars_sha256': digest(encode(dataset['bars']))}
    return validate_report(strict_json(encode(payload)))


def package_report(payload: dict, workbook: bytes, *, origin: str = 'local', scope: str = 'single_symbol_backtest',
                   validator=None, renderer=None) -> bytes:
    """Shared bounded transport; callers choose a trusted data validator/renderer."""
    (validator or validate_report)(payload)
    files = {'report.json': encode(payload), 'report.html': (renderer or render_html)(payload, origin=origin).encode('utf-8'), 'report.xlsx': workbook}
    manifest = {'format': PACKAGE_FORMAT, 'version': 1, 'scope': scope,
                'files': {name: {'sha256': digest(data), 'size': len(data)} for name, data in files.items()}}
    files['manifest.json'] = encode(manifest)
    if sum(map(len, files.values())) > MAX_PACKAGE_BYTES:
        raise TradeError('REPORT_PACKAGE_TOO_LARGE', '报告包解压总大小超过 16 MiB 限制')
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in files.items():
            archive.writestr(name, contents)
    return target.getvalue()


def unpack_report(contents: bytes, *, scope: str = 'single_symbol_backtest', validator=None) -> dict:
    if not contents or len(contents) > MAX_PACKAGE_BYTES:
        raise TradeError('REPORT_PACKAGE_TOO_LARGE', '报告包超过 16 MiB 限制')
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            entries = archive.infolist()
            if len(entries) != 4 or {item.filename for item in entries} != PACKAGE_MEMBERS:
                raise ValueError('Package members mismatch')
            total = 0
            for item in entries:
                mode = item.external_attr >> 16
                if (item.is_dir() or item.flag_bits & 1 or stat.S_ISLNK(mode)
                        or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise ValueError('Unsupported package entry')
                total += item.file_size
                if item.file_size < 0 or total > MAX_PACKAGE_BYTES:
                    raise ValueError('Package size limit')
            files = {}
            for item in entries:
                with archive.open(item) as stream:
                    data = stream.read(item.file_size + 1)
                if len(data) != item.file_size:
                    raise ValueError('Package declared size mismatch')
                files[item.filename] = data
        manifest = strict_json(files['manifest.json'], maximum=64 * 1024)
        _keys(manifest, {'format', 'version', 'scope', 'files'})
        if manifest['format'] != PACKAGE_FORMAT or type(manifest['version']) is not int or manifest['version'] != 1 or manifest['scope'] != scope:
            raise ValueError('Manifest format mismatch')
        _keys(manifest['files'], PACKAGE_MEMBERS - {'manifest.json'})
        for name, spec in manifest['files'].items():
            _keys(spec, {'sha256', 'size'})
            if type(spec['size']) is not int or spec['size'] != len(files[name]) or spec['sha256'] != digest(files[name]):
                raise ValueError('Manifest integrity mismatch')
        payload = strict_json(files['report.json'])
        if encode(payload) != files['report.json']:
            raise ValueError('Report JSON must be canonical')
        # Embedded HTML/XLSX are checksum-checked evidence only. All previews and
        # downloads are generated anew from the validated JSON; neither is executed.
        return (validator or validate_report)(payload)
    except TradeError:
        raise
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile, zipfile.LargeZipFile,
            zlib.error, RuntimeError, EOFError) as exc:
        raise TradeError('INVALID_REPORT_PACKAGE', '报告包文件清单、大小或 SHA256 校验失败') from exc


def render_html(payload: dict, *, origin: str = 'local') -> str:
    validate_report(payload)
    run, dataset = payload['run'], payload['dataset']
    result = run['result']

    def esc(value):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        return html.escape('未知' if value is None else str(value), quote=True)

    def table(headers, rows):
        return '<div class="table"><table><thead><tr>' + ''.join(f'<th>{esc(title)}</th>' for _, title in headers) + '</tr></thead><tbody>' + ''.join(
            '<tr>' + ''.join(f'<td>{esc(row.get(key))}</td>' for key, _ in headers) + '</tr>' for row in rows) + '</tbody></table></div>'

    metrics = [('期初资金', result['initial_capital']), ('期末资产', result['ending_assets']),
               ('总收益率', f"{Decimal(str(result['total_return'])) * 100:.2f}%"),
               ('最大回撤', f"{Decimal(str(result['max_drawdown'])) * 100:.2f}%"),
               ('完成交易', result.get('trade_count', 0)), ('固定滑点', result.get('slippage_rate', '0'))]
    values = [Decimal(str(row['total_assets'])) for row in result['equity']]
    chart = ''
    if values:
        low, high = min(values), max(values)
        spread = high - low or Decimal(1)
        points = ' '.join(f'{20 + index * 920 / max(1, len(values)-1):.2f},{180 - float((value-low)/spread)*150:.2f}' for index, value in enumerate(values))
        chart = f'<svg viewBox="0 0 960 210" role="img" aria-label="资产曲线"><line x1="20" y1="180" x2="940" y2="180" stroke="#cbd5d1"/><polyline points="{points}" fill="none" stroke="#0a6b54" stroke-width="3"/></svg>'
    metadata = [{'key': key, 'value': run[key]} for key in ('id', 'strategy_id', 'strategy_version', 'execution_version', 'calculation_version', 'code_sha256', 'result_sha256')]
    trades = table([('date', '成交日'), ('side', '方向'), ('quantity', '数量'), ('reference_price', '开盘参考价'),
                    ('price', '成交价'), ('slippage_rate', '滑点比例'), ('fees', '费用'), ('realized_pnl', '已实现盈亏'),
                    ('known_at', '已知时间'), ('decision_at', '决策时间'), ('reason', '成交原因')], result['trades'])
    decisions = table([('date', '执行日'), ('source_date', '信号源日期'), ('known_at', '已知时间'), ('signal', '观察信号'),
                       ('draft_eligible', '买入条件'), ('draft_block_reason', '阻止原因'), ('quality_flags', '质量标记')], result['decisions'])
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"><title>{esc(payload['title'])}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#eef3f0;color:#18342c;font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1200px;margin:auto;padding:36px 24px}}header{{border-top:5px solid #0a6b54;padding:24px 0}}h1{{font-size:30px;margin:4px 0}}h2{{font-size:20px;margin:24px 0 14px}}.muted{{color:#62736c}}section{{background:white;border:1px solid #dce5df;border-radius:12px;padding:24px;margin:18px 0}}.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:16px}}.metric b{{display:block;font-size:24px;font-variant-numeric:tabular-nums}}.table{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid #e5ece8;vertical-align:top;overflow-wrap:anywhere}}th{{background:#f3f7f5;white-space:nowrap}}svg{{width:100%;height:auto}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}@media print{{body{{background:white}}main{{padding:0}}section{{break-inside:avoid;border-color:#bbb}}.table{{overflow:visible}}}}
</style></head><body><main><header><div class="muted">TRADE · 冻结研究报告 · 单股回测</div><h1>{esc(payload['title'])}</h1><p>{esc(dataset['symbol'])} · {esc(dataset['first_date'])} — {esc(dataset['last_date'])} · {esc(dataset['bar_count'])} 根日线</p><p class="muted">{'导入报告，未重新计算' if origin == 'import' else '本地已完成回测快照'} · 保存于 {esc(payload['created_at'])}</p></header>
<section><div class="metrics">{''.join(f'<div class="metric"><span>{esc(label)}</span><b>{esc(value)}</b></div>' for label, value in metrics)}</div>{chart}</section>
<section><h2>范围与数据质量</h2><p>当前单股执行配置；与旧矩阵或传统回测的执行结果尚未逐项对照。</p><p>{esc(result['quality_flags'])}</p><ul>{''.join(f'<li>{esc(item)}</li>' for item in result['limitations'])}</ul></section>
<section><h2>参数与执行设置</h2>{table([('key','参数'),('value','值')],[{'key':key,'value':value} for key,value in run['params'].items()])}<details><summary>冻结执行配置与事件模板</summary><pre>{esc(run['config'])}</pre></details></section>
<section><h2>成交明细 · {len(result['trades'])} 条</h2>{trades}</section><section><h2>逐日信号与决策</h2>{decisions}</section>
<section><h2>高级分析与计算口径</h2><p>指标、抽样范围与样本不足原因随结果冻结。蒙特卡洛为历史样本情景分析。</p><pre>{esc(result.get('advanced_analysis') or {'status': 'not_generated', 'reason': '旧结果未生成高级分析'})}</pre></section>
<section><h2>可追溯信息</h2>{table([('key','字段'),('value','内容')],metadata)}<p class="muted">行情内容摘要：{esc(payload['bars_sha256'])}。包内摘要用于内容完整性核对，不表示第三方报告已独立验证。</p></section></main></body></html>'''
