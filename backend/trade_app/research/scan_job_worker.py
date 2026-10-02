"""A bounded scan quantum: immutable file references in, private evidence out.

No database connection is opened here. The parent owns checkpointing/publication.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

from trade_app.platform.compute_process import apply_worker_memory_limit

MAX_CHUNK_EVALUATIONS = 25
MAX_CHUNK_BYTES = 2 * 1024 * 1024
MAX_DATASET_BYTES = 2 * 1024 * 1024


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def run_identity(item: dict, descriptor: dict, strict: bool, profile: dict | None, context: dict | None = None) -> dict:
    identity = {'dataset_id': item['dataset_id'], 'strategy_id': descriptor['strategy_id'],
                'strategy_version': descriptor['strategy_version'],
                'calculation_version': descriptor['calculation_version'],
                'decision_at': item['decision_at'], 'strict': strict, 'params': descriptor['params'],
                'code_sha256': descriptor['code_sha256']}
    if profile:
        identity['event_profile'] = profile
    if context:
        identity['signal_context'] = context
        identity['signal_candidate'] = item['signal_candidate']
        identity['signal_pool'] = item.get('signal_pool')
    return identity


def read_dataset(market_dir: Path, dataset_id: str) -> dict:
    from trade_app.platform.types import TradeError
    if not re.fullmatch('[0-9a-f]{64}', dataset_id):
        raise TradeError('INVALID_SCAN_DATASET', '冻结样本标识无效')
    path = market_dir / f'{dataset_id}.json'
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_DATASET_BYTES + 1)
    except OSError as exc:
        raise TradeError('MARKET_DATA_MISSING', '冻结行情文件缺失', 409) from exc
    if len(raw) > MAX_DATASET_BYTES or hashlib.sha256(raw).hexdigest() != dataset_id:
        raise TradeError('MARKET_DATA_CORRUPT', '冻结行情文件内容校验失败', 409)
    dataset = json.loads(raw)
    if not isinstance(dataset.get('bars'), list) or not 1 <= len(dataset['bars']) <= 2000:
        raise TradeError('INVALID_COMPUTE_BAR_COUNT', '冻结行情样本大小无效')
    return dataset


def compute_chunk(payload: dict) -> dict:
    from trade_app.market.domain import eligible_bars
    from trade_app.research.runtime import evaluate_strategy
    from trade_app.research.wyckoff_strategy import WYCKOFF_STRATEGY_IDS
    from trade_app.platform.types import TradeError

    items = payload['items']
    allowed = {'attempt_id','input_sha256','start_index','market_dir','items','strategies','strict','event_profile','signal_context'}
    if set(payload) - allowed:
        raise TradeError('SCAN_CHUNK_INVALID', '计算批次包含未知字段')
    context = payload.get('signal_context')
    if context is not None:
        from trade_app.research.signal_context import normalize_context, evaluate_context, candidate
        context = normalize_context(context)
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_CHUNK_EVALUATIONS:
        raise TradeError('SCAN_CHUNK_LIMIT', '计算批次大小无效')
    descriptors = {row['strategy_id']: row for row in payload['strategies']}
    # One quantum uses at most one dataset; no 100-dataset memory accumulation.
    if len({item['dataset_id'] for item in items}) != 1:
        raise TradeError('SCAN_CHUNK_INVALID', '一个计算批次只能使用一个冻结样本')
    dataset = read_dataset(Path(payload['market_dir']), items[0]['dataset_id'])
    runs = []
    byte_count = 0
    for item in items:
        descriptor = descriptors[item['strategy_id']]
        item_keys = {'dataset_id','symbol','decision_date','decision_at','strategy_id'} | ({'signal_candidate','signal_pool'} if context else set())
        if set(item) - item_keys:
            raise TradeError('SCAN_CHUNK_INVALID', '计算计划包含未知字段')
        profile = payload['event_profile'] if context or item['strategy_id'] in WYCKOFF_STRATEGY_IDS else None
        identity = run_identity(item, descriptor, payload['strict'], profile, context)
        bars, quality = eligible_bars(dataset['bars'], item['decision_at'], payload['strict'])
        if context:
            bars = [bar for bar in bars if bar['event_date'] <= item['decision_date']]
            actual_candidate = candidate(bars, {**dataset, 'id':item['dataset_id'], 'symbol':item['symbol']}, context, item['decision_date'])
            if actual_candidate != item['signal_candidate']:
                raise TradeError('SCAN_INPUT_CHANGED', '候选指标与冻结时点不一致', 409)
            result = evaluate_context(item['strategy_id'],symbol=item['symbol'],bars=bars,params=descriptor['params'],
                profile=profile,context=context,frozen_candidate=actual_candidate,
                pool_evaluation=(item.get('signal_pool') or {}).get('evaluation'),quality=quality)
            result['signal_pool'] = item.get('signal_pool')
        else:
            result = evaluate_strategy(item['strategy_id'], symbol=dataset['symbol'], bars=bars,
                                       params=descriptor['params'], event_profile=profile, quality=quality)
        result.update(code_sha256=descriptor['code_sha256'], calculation_version=descriptor['calculation_version'])
        run = {'id': digest(identity), 'dataset_id': item['dataset_id'],
               'strategy_id': item['strategy_id'], 'strategy_version': descriptor['strategy_version'],
               'decision_at': item['decision_at'], 'strict': payload['strict'],
               'params': descriptor['params'], 'result': result}
        byte_count += len(encode(run).encode('utf-8')) + 1
        if byte_count > MAX_CHUNK_BYTES - 1024:
            raise TradeError('SCAN_CHECKPOINT_LIMIT', '单批研究证据超过大小预算，请缩小扫描')
        runs.append(run)
    return {'ok': True, 'attempt_id': payload['attempt_id'], 'input_sha256': payload['input_sha256'],
            'start_index': payload['start_index'], 'runs': runs}


def main() -> None:
    apply_worker_memory_limit()
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('COMPUTE_INPUT_LIMIT')
        response = compute_chunk(json.loads(raw))
    except MemoryError:
        response = {'ok': False, 'error': {'code': 'COMPUTE_MEMORY_LIMIT'}}
    except Exception as exc:
        response = {'ok': False, 'error': {'code': getattr(exc, 'code', 'SCAN_COMPUTE_FAILED')}}
    sys.stdout.write(encode(response))
    sys.stdout.flush()


if __name__ == '__main__':
    main()
