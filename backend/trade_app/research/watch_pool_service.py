"""A shared revisioned research pool; members retain immutable screener evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.service import get_dataset
from trade_app.market.symbols import normalize_market_symbol
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research.screener_models import ScreenerRun
from trade_app.research.watch_pool_models import WatchPool, WatchPoolAudit


class PoolConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    enabled: bool = True
    source_mode: Literal['filter', 'strategy'] = 'filter'
    ret40_min: float = Field(default=.2, ge=-1, le=1000)
    ret40_max: float = Field(default=2, ge=-1, le=1000)
    turnover20_min: float = Field(default=.05, ge=0, le=1000)
    amount20_min: float = Field(default=5e8, ge=0, le=1e16)
    amplitude20_min: float = Field(default=.03, ge=0, le=1000)
    trend_classes: list[Literal['A', 'A_B', 'B', 'Unknown']] = Field(default_factory=lambda: ['A', 'A_B'], max_length=4)
    retrace20_min: float = Field(default=0, ge=0, le=1)
    retrace20_max: float = Field(default=.3, ge=0, le=1)
    pullback_days_max: int = Field(default=5, ge=0, le=10000)
    vol_slope20_min: float = Field(default=0, ge=-1000, le=1000)
    up_down_volume_ratio_min: float = Field(default=0, ge=0, le=1e12)
    top_n: int = Field(default=50, ge=1, le=500)

    @model_validator(mode='after')
    def ranges(self):
        if self.ret40_min > self.ret40_max or self.retrace20_min > self.retrace20_max:
            raise ValueError('下限不能大于上限')
        if len(set(self.trend_classes)) != len(self.trend_classes):
            raise ValueError('趋势类别不能重复')
        return self


class PoolState(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    config: PoolConfig = Field(default_factory=PoolConfig)
    manual: list[dict] = Field(default_factory=list, max_length=500)
    automatic: list[dict] = Field(default_factory=list, max_length=500)
    order: list[str] = Field(default_factory=list, max_length=1000)


METRICS = ('ret40', 'turnover20', 'amount20', 'amplitude20', 'trend_class',
           'retrace20', 'pullback_days', 'vol_slope20', 'up_down_volume_ratio')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def symbol_key(value):
    return ''.join(normalize_market_symbol(value))


def state_sha(state):
    return hashlib.sha256(encode(state).encode()).hexdigest()


def get_pool(session: Session) -> dict:
    row = session.get(WatchPool, 1)
    state = json.loads(row.state_json) if row else PoolState().model_dump()
    return {'revision': row.revision if row else 0, 'state': state,
            'sha256': state_sha(state), 'updated_at': row.updated_at if row else None,
            'scope': 'shared_research'}


def prepare_state(session: Session, data_dir: Path, raw: dict) -> dict:
    """Read/validate before entering the write transaction. Never trust client metrics.

    Old local rows lack a source run id. Resolve them against persisted candidate
    evidence, keeping original metrics/formulas instead of recalculating today.
    """
    try:
        parsed = PoolState.model_validate(raw).model_dump()
        encode(parsed)
    except (ValidationError, ValueError, TypeError) as exc:
        raise TradeError('INVALID_WATCH_POOL', '观察池参数、范围或成员格式无效') from exc
    members = parsed['manual'] + parsed['automatic']
    if len(members) > 500:
        raise TradeError('WATCH_POOL_LIMIT', '人工与自动观察成员合计最多 500 只')
    if any(not isinstance(row.get('dataset_id'), str) or not isinstance(row.get('as_of_date'), str)
           or not isinstance(row.get('symbol'), str)
           or ('source_run_id' in row and not isinstance(row['source_run_id'], str)) for row in members):
        raise TradeError('INVALID_WATCH_MEMBER', '成员缺少冻结行情标识、证券或日期')
    # Build only the needed lookup; legacy provenance recovery is a read-only scan.
    required = {(row.get('dataset_id'), row.get('as_of_date')) for row in members}
    evidence: dict[tuple, list] = {}
    if required:
        query = select(ScreenerRun).order_by(ScreenerRun.created_at.desc(), ScreenerRun.id)
        if all(row.get('source_run_id') for row in members):
            query = query.where(ScreenerRun.id.in_({row['source_run_id'] for row in members}))
        resolved = set()
        for run in session.scalars(query):
            for candidate in json.loads(run.result_json).get('pools', {}).get('input', []):
                pair = (candidate['dataset_id'], candidate['as_of_date'])
                if pair in required:
                    evidence.setdefault(pair, []).append((run, candidate))
                    for index, supplied in enumerate(members):
                        if (supplied['dataset_id'], supplied['as_of_date']) == pair and (
                            not supplied.get('source_run_id') or supplied['source_run_id'] == run.id
                        ) and all(field in supplied and supplied[field] == candidate[field] for field in METRICS):
                            resolved.add(index)
            if len(resolved) == len(members):
                break
    datasets, seen = {}, set()
    for group in ('manual', 'automatic'):
        normalized = []
        for supplied in parsed[group]:
            dataset_id, day = supplied.get('dataset_id'), supplied.get('as_of_date')
            if not isinstance(dataset_id, str) or not isinstance(day, str):
                raise TradeError('INVALID_WATCH_MEMBER', '成员缺少冻结行情标识或日期')
            if dataset_id not in datasets:
                datasets[dataset_id] = get_dataset(session, data_dir, dataset_id)
            dataset = datasets[dataset_id]
            key = symbol_key(supplied.get('symbol', ''))
            if key != symbol_key(dataset['symbol']):
                raise TradeError('WATCH_SYMBOL_MISMATCH', '观察成员证券与冻结行情身份不一致')
            if key in seen:
                raise TradeError('DUPLICATE_WATCH_SYMBOL', '同一证券（包括代码别名）只能保存一份观察样本')
            seen.add(key)
            if not any(bar['event_date'] == day for bar in dataset['bars']):
                raise TradeError('WATCH_DATE_MISMATCH', '冻结行情中不存在该观察日期')
            matches = [(run, candidate) for run, candidate in evidence.get((dataset_id, day), [])
                       if (not supplied.get('source_run_id') or supplied['source_run_id'] == run.id)
                       and symbol_key(candidate['symbol']) == key
                       and all(field in supplied and supplied[field] == candidate[field] for field in METRICS)]
            if not matches:
                raise TradeError('WATCH_EVIDENCE_NOT_FOUND', '未找到与成员指标一致的已保存漏斗来源，请重新运行筛选后加入')
            run, candidate = matches[0]
            normalized.append({**candidate, 'symbol': key, 'imported_symbol': dataset['symbol'],
                               'source_run_id': run.id, 'source_code_sha256': run.code_sha256,
                               'source_as_of_date': json.loads(run.result_json)['as_of_date']})
        parsed[group] = normalized
    order = [symbol_key(value) for value in parsed['order']]
    # Removed members may leave legacy ordering entries; discard those explicitly in preview.
    parsed['order'] = list(dict.fromkeys(value for value in order if value in seen))
    parsed['order'] += [row['symbol'] for row in parsed['manual'] + parsed['automatic'] if row['symbol'] not in parsed['order']]
    return {'state': parsed, 'sha256': state_sha(parsed), 'member_count': len(members),
            'notes': ['成员证券身份已规范化；指标来自已保存漏斗记录。',
                      '自动成员是保存时的快照，需明确更新；本机旧记录不会自动删除。']}


def save_pool(session: Session, prepared: dict, expected_revision: int, *, action='save') -> dict:
    current = get_pool(session)
    if type(expected_revision) is not int or expected_revision != current['revision']:
        raise TradeError('WATCH_POOL_VERSION_CONFLICT', '观察池已在另一处修改；草稿已保留，请读取最新版本后比较', 409)
    row = session.get(WatchPool, 1)
    if row is None:
        row = WatchPool(id=1, revision=0, state_json='{}', updated_at=utc_now())
        session.add(row)
    row.revision += 1
    row.state_json, row.updated_at = encode(prepared['state']), utc_now()
    session.flush()
    result = get_pool(session)
    session.add(WatchPoolAudit(id=new_id(), revision=row.revision, action=action,
                              snapshot_json=encode(result), created_at=row.updated_at))
    session.flush()
    return result


def list_audit(session: Session) -> list[dict]:
    return [{'id': row.id, 'revision': row.revision, 'action': row.action,
             'snapshot': json.loads(row.snapshot_json), 'created_at': row.created_at}
            for row in session.scalars(select(WatchPoolAudit).order_by(WatchPoolAudit.revision.desc()).limit(100))]
