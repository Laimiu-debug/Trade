"""Deterministic legacy-to-current field mapping; no database or filesystem writes."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from trade_app.legacy_import.reader import MAX_ROWS, canonical, digest, parse_json
from trade_app.market.symbols import normalize_market_symbol
from trade_app.platform.types import TradeError, money_minor, price_units
from trade_app.reviews.periods import FIELDS, bounds
from trade_app.reviews.scores import DIMENSIONS
from trade_app.trading.nav import FlowFact, SnapshotFact, calculate_nav

VERSION = 'legacy-logical-import-v1'
MAPPINGS = {
    'capital_flows': {'flow_date': 'flow_date', 'kind': 'kind', 'amount': 'amount_minor (exact cents)', 'note': 'note'},
    'trades': {'trade_date': 'trade_date', 'code': 'symbol (canonical exchange)', 'qty': 'quantity', 'price': 'price_units (exact 1/10000)', 'fee_commission + fee_stamp + fee_transfer': 'fee_minor (manual historic fee)', 'name': 'name', 'note': 'note', 'id': 'new trade id; per-day sequence ordered by old integer id'},
    'pending_trades': {'trade_date/code/qty/price/name': 'pending fields', 'raw_text': 'source_text; pending remains unconfirmed'},
    'snapshots': {'snap_date': 'snap_date', 'total_assets/available_cash/position_value': 'exact cents; null preserved', 'positions': 'symbol/name/quantity/market_value'},
    'daily_reviews': {'market_observation/decision_review/mistakes': 'same named manual fields', 'next_market_forecast/next_position_plan/next_risk_plan': 'same named plan fields', 'next_watchlist/next_position_rehearsal': 'validated JSON fields', 'scores/trade_scores': 'versioned score sheets; old trade id remapped'},
    'weekly_reviews': {field: field for field in ('right_things', 'wrong_things', 'market_review', 'next_strategy')},
    'monthly_reviews': {field: field for field in ('system_iteration', 'next_goal')},
}
NOTES = [
    '只保存已识别表/JSON内容的脱敏逻辑档案与原文件 SHA256，不保存原始字节、SQLite 空闲页或 WAL。原文件不会被修改。',
    '已知 credential 字段及嵌套 JSON 设置会脱敏；自由文本中的任意密钥无法穷尽识别，请上传前检查笔记。',
    'LaimiuTrade 核心事实只进入独立的新实盘账户；final-trade 先存只读旧档案。完整模拟状态可另行逐笔核对后显式建立独立新模拟账户，研究资料保持只读。',
    '核心导入步骤不写旧 AI/行情/费用/目标设置、灵感卡或回合摘要；保存档案后可逐项预览补充映射。不覆盖全局人工标注或事件模板。旧研究结果不冒充有冻结行情和代码 hash 的新研究。',
    'round_reviews、flash_cards、settings、AI 摘要及未知字段保留在档案中。回合补充映射须完整成交集合与唯一身份一致，无法验证的不猜测关联。',
    'images 先保留旧路径文字，图片二进制不在 JSON/SQLite 中；可在附件映射入口明确选择本机文件再预览迁入，服务从不读取旧路径。旧 JSON 备份本身缺失的 round_reviews 也无法恢复。',
    '日期不推断时区，旧 created_at/updated_at 原文仅留档；新记录时间表示本次导入时间。金额不静默四舍五入，同日成交按旧整数 id 排序。',
]


def _text(value, maximum=50000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f'文本须为字符串且不超过 {maximum} 字')
    return value


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('日期必须是 YYYY-MM-DD，不截断或猜测时间')
    return value


def _int(value, maximum=100_000_000, minimum=1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'整数须在 {minimum}..{maximum} 范围内')
    return value


def _json(value, kind):
    value = parse_json(value) if isinstance(value, str) else value
    if not isinstance(value, kind):
        raise ValueError('嵌套 JSON 类型不正确')
    return value


def _symbol(value):
    if not isinstance(value, str):
        raise ValueError('证券代码须为字符串以保留前导零')
    return ''.join(normalize_market_symbol(value))


def _money(value, nullable=False, positive=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or value is None:
        raise ValueError('金额缺失或类型无效')
    return money_minor(value, '旧资料金额', allow_zero=not positive)


def _scores(raw, scope):
    entries = _json(raw, dict)
    if set(entries) - set(DIMENSIONS[scope]):
        raise ValueError('旧评分包含当前范围不支持的维度；不会静默转换分值')
    result = {}
    for key, value in entries.items():
        if not isinstance(value, dict) or set(value) - {'ai', 'final', 'comment'}:
            raise ValueError('旧评分字段无效')
        result[key] = {part: _int(value[part], 10, 0) if value.get(part) is not None else None for part in ('ai', 'final')}
        result[key]['comment'] = _text(value.get('comment', ''), 2000)
        if result[key]['ai'] is not None:
            result[key]['ai_source'] = 'legacy_import_unverified'
    return result


def _record(section, row):
    if not isinstance(row, dict):
        raise ValueError('记录必须为对象')
    source_id = str(_int(row.get('id'), 2**63 - 1))
    data = {'source_id': source_id}
    if section in ('trades', 'pending_trades'):
        if row.get('side') not in ('buy', 'sell'):
            raise ValueError('交易方向只能是 buy/sell')
        data.update(trade_date=_day(row.get('trade_date')), symbol=_symbol(row.get('code')),
                    name=_text(row.get('name', ''), 80), side=row['side'],
                    quantity=_int(row.get('qty')), price_units=price_units(row.get('price')),
                    note=_text(row.get('note', ''), 4000))
        if section == 'trades':
            fees = {field: _money(row.get(field, 0)) for field in ('fee_commission', 'fee_stamp', 'fee_transfer')}
            data['fee_minor'] = sum(fees.values())
            _money(str(Decimal(data['fee_minor']) / 100))
            data['fee_breakdown_json'] = canonical({key: str(Decimal(value) / 100) for key, value in zip(('commission', 'stamp', 'transfer'), fees.values())})
        else:
            data.update(fee_minor=0, fee_breakdown_json=None, source_text=_text(row.get('raw_text', ''), 50000))
    elif section == 'capital_flows':
        if row.get('kind') not in ('initial', 'deposit', 'withdraw'):
            raise ValueError('资金类型无效')
        data.update(flow_date=_day(row.get('flow_date')), kind=row['kind'], amount_minor=_money(row.get('amount'), positive=True), note=_text(row.get('note', ''), 4000))
    elif section == 'snapshots':
        positions = _json(row.get('positions', '[]'), list)
        if len(positions) > 500:
            raise ValueError('快照持仓超过 500 项')
        data.update(snap_date=_day(row.get('snap_date')), total_assets_minor=_money(row.get('total_assets')),
                    available_cash_minor=_money(row.get('available_cash'), nullable=True),
                    position_value_minor=_money(row.get('position_value'), nullable=True), note=_text(row.get('note', ''), 4000))
        normalized = []
        for item in positions:
            if not isinstance(item, dict):
                raise ValueError('持仓须为对象')
            normalized.append({'symbol': _symbol(item.get('code', item.get('symbol'))),
                               'name': _text(item.get('name', ''), 80),
                               'quantity': _int(item.get('qty', item.get('quantity'))),
                               'market_value_minor': _money(item.get('market_value'))})
        if len({item['symbol'] for item in normalized}) != len(normalized):
            raise ValueError('快照内存在同一证券别名重复')
        if normalized:
            detail = sum(item['market_value_minor'] for item in normalized)
            if data['position_value_minor'] is None:
                # Preserve unknown old total instead of filling it from other evidence.
                pass
            elif abs(detail - data['position_value_minor']) > 1:
                raise ValueError('持仓明细与总持仓市值不一致')
        cash, position = data['available_cash_minor'], data['position_value_minor']
        if cash is not None and position is not None and abs(cash + position - data['total_assets_minor']) > 1:
            raise ValueError('现金与持仓市值之和不等于总资产')
        data['positions'] = normalized
    elif section == 'daily_reviews':
        data['review_date'] = _day(row.get('review_date'))
        for field in ('market_observation', 'decision_review', 'mistakes', 'next_market_forecast', 'next_position_plan', 'next_risk_plan'):
            data[field] = _text(row.get(field, ''))
        for field, allowed in [('next_watchlist', {'code', 'name', 'condition', 'action'}), ('next_position_rehearsal', {'code', 'name', 'qty', 'note'})]:
            items = _json(row.get(field, '[]'), list)
            if len(items) > 100:
                raise ValueError('计划明细超过 100 项')
            for item in items:
                if not isinstance(item, dict) or set(item) - allowed:
                    raise ValueError('计划字段不匹配')
                _symbol(item.get('code'))
                for key, value in item.items():
                    _int(value, minimum=0) if key == 'qty' else _text(value, 2000)
                if field == 'next_position_rehearsal':
                    _int(item.get('qty'), minimum=0)
            data[field + '_json'] = canonical(items)
        data['scores'] = _scores(row.get('scores', '{}'), 'daily')
        data['trade_scores'] = {str(key): _scores(value, 'trade') for key, value in _json(row.get('trade_scores', '{}'), dict).items()}
    else:
        kind = 'weekly' if section == 'weekly_reviews' else 'monthly'
        year = _int(row.get('year'), 9999)
        part = _int(row.get('week' if kind == 'weekly' else 'month'), 53 if kind == 'weekly' else 12)
        key = f'{year:04d}-W{part:02d}' if kind == 'weekly' else f'{year:04d}-{part:02d}'
        bounds(kind, key)
        data.update(kind=kind, period_key=key, sections_json=canonical({field: _text(row[field]) for field in FIELDS[kind] if field in row}))
    return data


def prepare(upload: dict, *, mode: str, account_name: str = '') -> dict:
    if mode not in {'archive_only', 'new_real_account'}:
        raise TradeError('LEGACY_INVALID_MODE', '导入方式无效')
    laimiu = upload['source'].startswith('laimiu_')
    if mode == 'new_real_account' and not laimiu:
        raise TradeError('LEGACY_SOURCE_IS_NOT_REAL', '旧模拟和研究资料只能保存只读档案')
    if mode == 'new_real_account' and (not isinstance(account_name, str) or not account_name.strip() or len(account_name.strip()) > 80):
        raise TradeError('LEGACY_ACCOUNT_NAME', '请填写不超过 80 字的新账户名称')
    payload = upload['payload']
    rows, inventory, errors = [], [], []
    total = 0
    for section, value in payload.items():
        count = len(value) if isinstance(value, (list, dict)) else 1
        total += count
        supported = laimiu and section in MAPPINGS
        inventory.append({'section': section, 'count': count, 'target': section if supported and mode == 'new_real_account' else 'archive_only',
                          'field_map': MAPPINGS.get(section, {}) if supported else {},
                          'note': '映射核心字段；其余字段保留逻辑档案' if supported and mode == 'new_real_account' else '只读逻辑档案，不写当前业务配置或研究结果'})
        if not supported or mode != 'new_real_account':
            continue
        if not isinstance(value, list):
            errors.append({'section': section, 'source_id': '', 'message': '该表必须为记录数组'})
            continue
        seen, source_ids = set(), set()
        for index, row in enumerate(value):
            try:
                data = _record(section, row)
                identity = data.get('snap_date', data.get('review_date', data.get('period_key', data['source_id'])))
                if identity in seen or data['source_id'] in source_ids:
                    raise ValueError('重复 ID/日期/周期不允许覆盖')
                seen.add(identity)
                source_ids.add(data['source_id'])
                rows.append({'section': section, 'source_id': data['source_id'], 'data': data})
            except (ValueError, TypeError, KeyError, TradeError) as exc:
                errors.append({'section': section, 'source_id': str(index + 1), 'message': exc.message if isinstance(exc, TradeError) else str(exc)})
    if total > MAX_ROWS:
        raise TradeError('LEGACY_TOO_MANY_ROWS', f'每批最多 {MAX_ROWS} 条，不能静默截断')
    if not errors and mode == 'new_real_account':
        trades = {row['source_id']: row['data'] for row in rows if row['section'] == 'trades'}
        for row in rows:
            if row['section'] == 'daily_reviews':
                for old_id in row['data']['trade_scores']:
                    if old_id not in trades or trades[old_id]['trade_date'] != row['data']['review_date']:
                        errors.append({'section': 'daily_reviews', 'source_id': row['source_id'], 'message': '逐笔评分引用不存在或日期不一致的旧成交'})
        try:
            calculate_nav([FlowFact(row['source_id'], row['data']['flow_date'], row['data']['kind'], row['data']['amount_minor'], f"{int(row['source_id']):020d}") for row in rows if row['section'] == 'capital_flows'],
                          [SnapshotFact(row['data']['snap_date'], row['data']['total_assets_minor']) for row in rows if row['section'] == 'snapshots'])
        except TradeError as exc:
            errors.append({'section': 'capital_flows/snapshots', 'source_id': '', 'message': exc.message})
        if not rows:
            errors.append({'section': '', 'source_id': '', 'message': '没有可映射的实盘核心记录；请选择只读档案'})
    result = {key: value for key, value in upload.items() if key != 'payload'}
    result.update(version=VERSION, mode=mode, account_name=account_name.strip() if mode == 'new_real_account' else None,
                  inventory=inventory, records=rows, errors=errors, notes=NOTES,
                  mapped_record_count=len(rows), can_import=not errors,
                  archive_format='sanitized-logical-json-not-original-bytes')
    result['preview_sha256'] = digest(result)
    return result
