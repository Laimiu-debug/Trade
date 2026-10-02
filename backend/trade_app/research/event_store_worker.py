"""Pure bounded historical event snapshots; no database, network or lazy fill."""
from datetime import date
import json
import math
from pathlib import Path
import sys

from trade_app.market.domain import eligible_bars
from trade_app.platform.compute_process import apply_worker_memory_limit
from trade_app.platform.types import TradeError
from trade_app.research.scan_job_worker import digest, encode, read_dataset
from trade_app.research.wyckoff_domain import calculate_snapshot

CHUNK_SIZE = 10
MAX_CHUNK_BYTES = 2 * 1024 * 1024


def record_identity(item: dict) -> dict:
    return {key: item[key] for key in ('version_id', 'dataset_id', 'symbol', 'decision_date', 'decision_at')}


def validate_result(result: dict, decision_date: str) -> None:
    if not isinstance(result, dict) or not isinstance(result.get('snapshot'), dict):
        raise TradeError('EVENT_STORE_RESULT_INVALID', '事件快照格式异常', 409)
    snapshot = result['snapshot']
    source_date = result.get('source_date')
    if not isinstance(snapshot.get('event_dates'), dict) or any(
            not isinstance(snapshot.get(field), list) for field in ('events', 'risk_events', 'event_chain')):
        raise TradeError('EVENT_STORE_RESULT_INVALID', '事件列表格式异常', 409)
    dates = list(snapshot.get('event_dates', {}).values()) + [snapshot.get('trigger_date')]
    dates += [item.get('date') for item in snapshot.get('event_chain', [])]
    if source_date and source_date > decision_date:
        raise TradeError('EVENT_STORE_FUTURE_DATE', '来源日期晚于决策日期', 409)
    for value in dates:
        if not value:
            continue
        try:
            if date.fromisoformat(value).isoformat() != value or not source_date or value > source_date:
                raise ValueError(value)
        except (TypeError, ValueError) as exc:
            raise TradeError('EVENT_STORE_EVENT_DATE_INVALID', '事件日期超出可见来源日期', 409) from exc
    for field, value in snapshot.items():
        if field.endswith('_score'):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
                raise TradeError('EVENT_STORE_SCORE_INVALID', '事件评分超出有效范围', 409)
    if type(result.get('has_data')) is not bool or type(result.get('observed_bars')) is not int:
        raise TradeError('EVENT_STORE_RESULT_INVALID', '历史样本状态异常', 409)


def compute_chunk(payload: dict) -> dict:
    items = payload['items']
    if not isinstance(items, list) or not 1 <= len(items) <= CHUNK_SIZE or len({item['dataset_id'] for item in items}) != 1:
        raise TradeError('EVENT_STORE_CHUNK_INVALID', '事件仓计算批次无效')
    dataset = read_dataset(Path(payload['market_dir']), items[0]['dataset_id'])
    records, size = [], 0
    for item in items:
        config = payload['versions'][item['version_id']]
        bars, quality = eligible_bars(dataset['bars'], item['decision_at'], config['strict'])
        # Guard the local event date explicitly, independently of timezone conversion.
        bars = [bar for bar in bars if bar['event_date'] <= item['decision_date']]
        result = calculate_snapshot(bars, config['window_days'], config['event_profile']['snapshot'])
        result['quality_flags'] = sorted(set(quality + (['stale_source_date'] if result['source_date']
            and result['source_date'] < item['decision_date'] else [])))
        validate_result(result, item['decision_date'])
        encoded = encode(result)
        size += len(encoded.encode('utf-8'))
        if size > MAX_CHUNK_BYTES - 8192:
            raise TradeError('EVENT_STORE_OUTPUT_LIMIT', '事件证据超过单批预算')
        identity = record_identity(item)
        snapshot = result['snapshot']
        record = {**identity, 'id': digest(identity), 'result_json': encoded,
            'content_sha256': digest({'identity': identity, 'result': result}),
            'byte_count': len(encoded.encode('utf-8')), 'source_date': result['source_date'],
            'status': 'complete' if result['has_data'] else 'insufficient_history',
            'observed_bars': result['observed_bars'], 'event_count': len(snapshot['events']),
            'risk_count': len(snapshot['risk_events']), 'primary_event': snapshot.get('signal', '')}
        records.append(record)
    return {'ok': True, 'attempt_id': payload['attempt_id'], 'input_sha256': payload['input_sha256'], 'records': records}


def main() -> None:
    apply_worker_memory_limit()
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise TradeError('EVENT_STORE_INPUT_LIMIT', '计算输入过大')
        response = compute_chunk(json.loads(raw))
    except MemoryError:
        response = {'ok': False, 'error': {'code': 'COMPUTE_MEMORY_LIMIT'}}
    except Exception as exc:
        response = {'ok': False, 'error': {'code': getattr(exc, 'code', 'EVENT_STORE_COMPUTE_FAILED')}}
    sys.stdout.write(encode(response))
    sys.stdout.flush()


if __name__ == '__main__':
    main()
