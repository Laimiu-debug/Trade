"""AI score suggestions stay separate from explicitly accepted final scores."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select

from trade_app.platform.models import AuditEvent
from trade_app.platform.symbols import market_symbol_key
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.reviews.score_models import ReviewScoreSheet
from trade_app.reviews.scores import DIMENSIONS, _current_trades, _data
from trade_app.reviews.service import validate_day
from trade_app.trading.service import account_or_error, list_trades


def _revision(actual: int, expected) -> None:
    if isinstance(expected, bool) or not isinstance(expected, int) or actual != expected:
        raise TradeError('REVISION_CONFLICT', '评分版本已变化，请刷新后重试', 409)


def _find(session, account_id: str, day: str, scope: str, subject_id: str):
    return session.scalar(select(ReviewScoreSheet).where(ReviewScoreSheet.account_id == account_id,
        ReviewScoreSheet.review_date == day, ReviewScoreSheet.scope == scope,
        ReviewScoreSheet.subject_id == subject_id))


def _audit(session, account_id: str, row: ReviewScoreSheet, action: str, before: dict | None, after: dict):
    session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type='review_score_sheet',
        entity_id=row.id, operation=action,
        before_json=json.dumps(before, ensure_ascii=False) if before is not None else None,
        after_json=json.dumps(after, ensure_ascii=False), created_at=utc_now()))


def freeze_score_targets(session, account_id: str, target: dict) -> dict:
    account_or_error(session, account_id, real=True)
    if not isinstance(target, dict) or set(target) - {'scope', 'key', 'trade_ids'}:
        raise TradeError('INVALID_SCORE_TARGET', '评分目标字段无效')
    day, scope, ids = target.get('key'), target.get('scope'), target.get('trade_ids', [])
    if not isinstance(day, str):
        raise TradeError('INVALID_DATE', '评分日期须为 YYYY-MM-DD')
    validate_day(day)
    if (scope not in ('daily', 'trade', 'batch', 't_group') or not isinstance(ids, list)
            or len(ids) > 100 or any(not isinstance(item, str) for item in ids) or len(ids) != len(set(ids))):
        raise TradeError('INVALID_SCORE_TARGET', '请选择有效范围及最多100个不重复成交')
    ids = sorted(ids)
    trades = _current_trades(session, account_id, day, ids)
    if scope == 'daily':
        if ids:
            raise TradeError('INVALID_SCORE_TRADES', '整日评分不接受部分成交选择')
        specifications = [('daily', day, [])]
    elif scope in ('trade', 'batch'):
        if not ids or (scope == 'trade' and len(ids) != 1):
            raise TradeError('INVALID_SCORE_TRADES', '单笔须选择一条成交，批量至少选择一条')
        specifications = [('trade', item, [item]) for item in ids]
    else:
        if len(ids) < 2 or len({market_symbol_key(row.symbol) for row in trades}) != 1 or {row.side for row in trades} != {'buy', 'sell'}:
            raise TradeError('INVALID_T_GROUP', '做T评分须为同日同股且包含买卖双方')
        specifications = [('t_group', 'group:' + hashlib.sha256('|'.join(ids).encode()).hexdigest()[:24], ids)]
    targets = []
    for target_scope, subject_id, target_ids in specifications:
        row = _find(session, account_id, day, target_scope, subject_id)
        targets.append({'scope': target_scope, 'subject_id': subject_id, 'trade_ids': target_ids,
                        'current_revision': row.revision if row else 0,
                        'before': _data(row, target_ids) if row else None,
                        'dimensions': list(DIMENSIONS[target_scope])})
    day_trades = [item for item in list_trades(session, account_id) if item['trade_date'] == day]
    if len(day_trades) > 1000:
        raise TradeError('AI_SCORE_CONTEXT_TOO_LARGE', '单日成交超过1000条，请先缩小复盘范围')
    return {'target': {'scope': scope, 'key': day, 'trade_ids': ids}, 'subjects': targets,
            'day_trades': [{key: item[key] for key in ('id', 'symbol', 'side', 'quantity', 'price', 'fee', 'revision')}
                           for item in day_trades],
            'selected_trade_ids': ids if scope != 'daily' else [item['id'] for item in day_trades]}


def apply_score_suggestions(session, account_id: str, day: str, frozen: list[dict], subjects: list[dict],
                            *, generation_id: str, run_id: str) -> dict:
    account = account_or_error(session, account_id, real=True)
    current = []
    # Validate the entire batch before any sheet is written.
    for source, proposal in zip(frozen, subjects, strict=True):
        _current_trades(session, account_id, day, source['trade_ids'])
        row = _find(session, account_id, day, source['scope'], source['subject_id'])
        _revision(row.revision if row else 0, source['current_revision'])
        if (proposal['subject_id'] != source['subject_id'] or proposal['scope'] != source['scope'] or
                sorted(proposal['trade_ids']) != source['trade_ids']):
            raise TradeError('AI_SCORE_SUBJECT_CHANGED', '评分结果与冻结目标不一致')
        current.append(row)
    accepted = []
    for source, proposal, row in zip(frozen, subjects, current, strict=True):
        before = _data(row, source['trade_ids']) if row else None
        now = utc_now()
        if row is None:
            row = ReviewScoreSheet(id=new_id(), account_id=account_id, review_date=day,
                scope=source['scope'], subject_id=source['subject_id'], trade_ids_json=json.dumps(source['trade_ids']),
                scores_json='{}', comment='', revision=1, created_at=now, updated_at=now)
            session.add(row)
        else:
            row.revision += 1
        entries = json.loads(row.scores_json)
        for dimension, suggestion in proposal['scores'].items():
            previous = entries.get(dimension, {'final': None, 'comment': '', 'final_source': None})
            entries[dimension] = {**previous, 'ai': suggestion['score'], 'ai_comment': suggestion['comment'],
                'ai_subject_comment': proposal['comment'], 'ai_generation_id': generation_id,
                'ai_run_id': run_id, 'ai_account_revision': account.input_revision}
        row.scores_json, row.updated_at = json.dumps(entries, ensure_ascii=False), now
        session.flush()
        after = _data(row, source['trade_ids'])
        _audit(session, account_id, row, 'ai_suggestion', before, after)
        accepted.append({'before': before, 'after': after, 'target_revision': row.revision})
    return {'sheets': accepted}


def retract_score_suggestions(session, account_id: str, accepted: list[dict]) -> dict:
    account_or_error(session, account_id, real=True)
    records = []
    for entry in accepted:
        row = session.get(ReviewScoreSheet, entry['after']['id'])
        if row is None or row.account_id != account_id:
            raise TradeError('SCORE_SHEET_NOT_FOUND', '评分不存在', 404)
        _revision(row.revision, entry['target_revision'])
        records.append(row)
    results = []
    for entry, row in zip(accepted, records, strict=True):
        current = _data(row, json.loads(row.trade_ids_json))
        previous = entry['before'] or {'scores': {}, 'comment': ''}
        row.scores_json, row.comment = json.dumps(previous['scores'], ensure_ascii=False), previous['comment']
        row.revision, row.updated_at = row.revision + 1, utc_now()
        after = _data(row, json.loads(row.trade_ids_json))
        _audit(session, account_id, row, 'ai_suggestion_retract', current, after)
        results.append(after)
    return {'sheets': results}


def copy_ai_to_final(session, account_id: str, sheet_id: str, body: dict) -> dict:
    account = account_or_error(session, account_id, real=True)
    if not isinstance(body, dict) or set(body) != {'expected_revision', 'dimensions'}:
        raise TradeError('INVALID_SCORE_COPY', '须提供当前评分版本和明确选择的维度')
    row = session.get(ReviewScoreSheet, sheet_id)
    if row is None or row.account_id != account_id:
        raise TradeError('SCORE_SHEET_NOT_FOUND', '评分不存在于该账户', 404)
    _revision(row.revision, body['expected_revision'])
    ids = json.loads(row.trade_ids_json)
    _current_trades(session, account_id, row.review_date, ids)
    dimensions = body['dimensions']
    if (not isinstance(dimensions, list) or not dimensions or any(not isinstance(item, str) for item in dimensions)
            or len(dimensions) != len(set(dimensions)) or set(dimensions) - set(DIMENSIONS[row.scope])):
        raise TradeError('INVALID_SCORE_COPY', '请选择当前范围内不重复的评分维度')
    entries = json.loads(row.scores_json)
    before = _data(row, ids)
    for dimension in dimensions:
        entry = entries.get(dimension, {})
        value = entry.get('ai')
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
            raise TradeError('AI_SCORE_MISSING', '所选维度没有有效 AI 建议')
        if entry.get('ai_account_revision') != account.input_revision:
            raise TradeError('AI_SCORE_SOURCE_CHANGED', '来源账户事实已修改，请重新评分', 409)
    for dimension in dimensions:
        entries[dimension].update(final=entries[dimension]['ai'], final_source='ai_accepted',
                                  comment=entries[dimension].get('ai_comment', ''))
    row.scores_json = json.dumps(entries, ensure_ascii=False)
    row.revision, row.updated_at = row.revision + 1, utc_now()
    after = _data(row, ids)
    _audit(session, account_id, row, 'ai_copy_to_final', before, after)
    session.flush()
    return after
