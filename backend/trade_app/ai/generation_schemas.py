"""Strict structured results. Unknown or missing facts remain explicit nulls."""
from __future__ import annotations

from datetime import date
import json
import math
import re

from trade_app.ai.contexts import text_field
from trade_app.platform.types import TradeError, money_minor, money_text, price_text, price_units


DAILY_FIELDS = ('title', 'market_observation', 'decision_review', 'mistakes', 'tomorrow_plan',
                'overall_summary', 'reflection', 'next_market_forecast', 'next_position_plan', 'next_risk_plan')
REHEARSAL_FIELDS = ('next_market_forecast', 'next_position_plan', 'next_risk_plan')
STOCK_FIELDS = {'summary', 'conclusion', 'confidence', 'breakout_date', 'trend_bull_type', 'theme_name', 'rise_reasons'}


def strict_json(raw: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        if not isinstance(raw, str) or len(raw) > 131072:
            raise ValueError()
        parsed = json.loads(raw, object_pairs_hook=pairs,
                            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        pending = [(parsed, 0)]
        count = 0
        while pending:
            value, depth = pending.pop()
            count += 1
            if depth > 32 or count > 20000:
                raise ValueError()
            children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
            pending.extend((child, depth + 1) for child in children)
        return parsed
    except (ValueError, TypeError, RecursionError):
        raise TradeError('AI_INVALID_JSON', '模型回复须为单一 JSON，不能带代码围栏、重复字段或非有限数') from None


def _object(raw, fields: set, description: str) -> dict:
    if not isinstance(raw, dict) or set(raw) != fields:
        raise TradeError('AI_INVALID_STRUCTURE', description + '字段不完整或包含多余字段')
    return raw


def day(raw, nullable=False):
    if raw is None and nullable:
        return None
    try:
        if date.fromisoformat(raw).isoformat() != raw:
            raise ValueError()
        return raw
    except (TypeError, ValueError):
        raise TradeError('AI_INVALID_DATE', '日期须为 YYYY-MM-DD，缺失日期须保留 null') from None


def _symbol(raw):
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise TradeError('AI_INVALID_SYMBOL', '证券代码须为字符串以保留前导零')
    match = re.fullmatch(r'(?:(SH|SZ|BJ))?([0-9]{6})(?:\.(SH|SZ|BJ))?', raw.strip().upper())
    if match is None or (match[1] and match[3] and match[1] != match[3]):
        raise TradeError('AI_INVALID_SYMBOL', '识别代码须为六位数字或明确交易所别名')
    return match[2]


def _quantity(raw):
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 100000000:
        raise TradeError('AI_INVALID_QUANTITY', '识别数量须为 0 至 1 亿整数，缺失时保留 null')
    return raw


def _money(raw):
    return None if raw is None else money_text(money_minor(raw, '识别金额', allow_zero=True))


def _list(raw, maximum: int, field: str):
    if not isinstance(raw, list) or len(raw) > maximum:
        raise TradeError('AI_INVALID_STRUCTURE', f'{field} 最多 {maximum} 项')
    return raw


def normalize_output(kind: str, raw, source: dict) -> dict:
    if kind == 'review_scores':
        _object(raw, {'summary', 'subjects'}, '评分建议')
        subjects = _list(raw['subjects'], 100, '评分目标')
        frozen = source['score_targets']
        by_id = {}
        for item in subjects:
            _object(item, {'subject_id', 'scope', 'trade_ids', 'scores', 'comment'}, '评分项')
            if not isinstance(item['subject_id'], str) or item['subject_id'] in by_id:
                raise TradeError('AI_SCORE_SUBJECT_CHANGED', '评分目标重复或无效')
            by_id[item['subject_id']] = item
        if set(by_id) != {item['subject_id'] for item in frozen}:
            raise TradeError('AI_SCORE_SUBJECT_CHANGED', '评分结果必须完整对应所选目标')
        normalized = []
        for expected in frozen:
            item = by_id[expected['subject_id']]
            if (item['scope'] != expected['scope'] or not isinstance(item['trade_ids'], list) or
                    any(not isinstance(value, str) for value in item['trade_ids']) or
                    sorted(item['trade_ids']) != expected['trade_ids']):
                raise TradeError('AI_SCORE_SUBJECT_CHANGED', '评分结果不能改换关联成交或范围')
            _object(item['scores'], set(expected['dimensions']), '评分维度')
            scores = {}
            for dimension, entry in item['scores'].items():
                _object(entry, {'score', 'comment'}, '评分维度内容')
                value = entry['score']
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
                    raise TradeError('AI_INVALID_SCORE', 'AI 评分必须是0至10整数')
                scores[dimension] = {'score': value, 'comment': text_field(entry['comment'], '评分依据', 2000)}
            normalized.append({'subject_id': expected['subject_id'], 'scope': expected['scope'],
                'trade_ids': expected['trade_ids'], 'scores': scores, 'comment': text_field(item['comment'], '评分点评', 4000)})
        return {'summary': text_field(raw['summary'], '评分总评', 8000), 'subjects': normalized}
    if kind == 'stock_analysis':
        _object(raw, STOCK_FIELDS, '个股分析')
        confidence = raw['confidence']
        if isinstance(confidence, bool) or not isinstance(confidence, (float, int)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise TradeError('AI_INVALID_CONFIDENCE', '置信度须为 0 至 1')
        if raw['conclusion'] not in ('发酵中', '高潮', '退潮', 'Unknown'):
            raise TradeError('AI_INVALID_CONCLUSION', '结论须使用既定研究状态或 Unknown')
        breakout = day(raw['breakout_date'], True)
        if breakout is not None and breakout not in source['breakout_candidates']:
            raise TradeError('AI_INVALID_BREAKOUT_DATE', '起爆日必须属于本次可得的候选日')
        return {'summary': text_field(raw['summary'], '摘要', 2000, True), 'conclusion': raw['conclusion'],
                'confidence': confidence, 'breakout_date': breakout,
                **{key: text_field(raw[key], key, 200) if raw[key] is not None else None
                   for key in ('trend_bull_type', 'theme_name')},
                'rise_reasons': [text_field(item, '原因', 200, True) for item in _list(raw['rise_reasons'], 5, '原因')]}
    if kind == 'review_draft':
        _object(raw, {'sections'}, '复盘草稿')
        _object(raw['sections'], set(source['target']['fields']), '所选复盘栏目')
        return {'sections': {key: text_field(value, key, 50000) for key, value in raw['sections'].items()}}
    if kind == 'ocr_trades':
        _object(raw, {'trades', 'warnings'}, '成交识别')
        rows = []
        for item in _list(raw['trades'], 100, '成交记录'):
            _object(item, {'trade_date', 'symbol', 'name', 'side', 'quantity', 'price', 'fee'}, '成交行')
            if item['side'] not in ('buy', 'sell', 'hold', None):
                raise TradeError('AI_INVALID_SIDE', '识别方向须为 buy、sell、hold 或 null')
            rows.append({'trade_date': day(item['trade_date'], True), 'symbol': _symbol(item['symbol']),
                         'name': text_field(item['name'], '名称', 120), 'side': item['side'],
                         'quantity': _quantity(item['quantity']),
                         'price': price_text(price_units(item['price'])) if item['price'] is not None else None,
                         'fee': _money(item['fee'])})
        return {'trades': rows, 'warnings': [text_field(item, '识别提示', 500) for item in _list(raw['warnings'], 20, '提示')]}
    if kind == 'ocr_assets':
        _object(raw, {'snap_date', 'total_assets', 'available_cash', 'positions', 'warnings'}, '资产识别')
        rows = []
        for item in _list(raw['positions'], 100, '持仓'):
            _object(item, {'symbol', 'name', 'quantity', 'market_value'}, '持仓行')
            rows.append({'symbol': _symbol(item['symbol']), 'name': text_field(item['name'], '名称', 120),
                         'quantity': _quantity(item['quantity']), 'market_value': _money(item['market_value'])})
        codes = [row['symbol'] for row in rows if row['symbol'] is not None]
        if len(codes) != len(set(codes)):
            raise TradeError('AI_DUPLICATE_POSITION', '持仓存在重复代码，请核对后合并')
        return {'snap_date': day(raw['snap_date'], True), 'total_assets': _money(raw['total_assets']),
                'available_cash': _money(raw['available_cash']), 'positions': rows,
                'warnings': [text_field(item, '识别提示', 500) for item in _list(raw['warnings'], 20, '提示')]}
    raise TradeError('AI_GENERATION_KIND', '生成类型无效')


def output_prompt(kind: str, source: dict) -> str:
    common = '仅输出一个严格 JSON 对象，不附代码围栏或其他说明。缺失事实用 null，禁止猜造交易、资金或日期。'
    if kind == 'review_scores':
        return ('仅输出严格JSON对象 {"summary":总评字符串,"subjects":[{"subject_id":冻结目标ID,'
                '"scope":冻结范围,"trade_ids":冻结成交ID数组,"scores":{维度:{"score":0至10整数,'
                '"comment":依据字符串}},"comment":目标点评字符串}]}。必须完整覆盖score_targets且不能改换ID。'
                '整日为仓位、回撤、纪律、买点、卖点、情绪六维；单笔与做T为时机、纪律、情绪三维。'
                '买卖时机根据成交方向解释；同股做T结合完整买卖节奏。'
                '缺乏市场价格或计划证据时应在评语中明确不确定性，不得假装知道未来收益。评分仅是建议。')
    if kind == 'stock_analysis':
        return common + ('字段为 summary、conclusion（发酵中/高潮/退潮/Unknown）、confidence（0至1）、'
                         'breakout_date（只选给定候选日或null）、trend_bull_type、theme_name、rise_reasons（字符串数组）。'
                         '分析是基于所选历史资料的研究，不是实时新闻；缺乏公司事件证据时明确未知。')
    if kind == 'review_draft':
        return common + '结构为 {"sections":{栏目名称:文本}}。栏目恰为：' + ','.join(source['target']['fields'])
    if kind == 'ocr_trades':
        return common + ('结构为 {"trades":[{"trade_date":日期或null,"symbol":六位代码或null,"name":名称字符串,'
                         '"side":"buy/sell/hold或null","quantity":整数或null,"price":价格字符串或null,'
                         '"fee":费用字符串或null}],"warnings":[提示字符串]}。持仓截图没有方向时只能标记hold；'
                         '没有费用不能填0，不要把成本价当作已成交价格。无记录返回空trades。')
    return common + ('结构为 {"snap_date":日期或null,"total_assets":金额字符串或null,"available_cash":金额字符串或null,'
                     '"positions":[{"symbol":六位代码或null,"name":名称字符串,"quantity":整数或null,'
                     '"market_value":市值字符串或null}],"warnings":[提示字符串]}。'
                     '必须读取持仓市值，不能用成本价推断当前市值；无数据保留null及空数组。')
