"""TDX industry index amount/price momentum, with the former flow proxy semantics."""
from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.types import TradeError, utc_now
from trade_app.research.sector_codes import SECTOR_CODES
from trade_app.research.sector_models import SectorFlowRun


def _merge(datasets: list[dict]) -> list[dict]:
    by_day: dict[str, dict[str, list[float]]] = {}
    for dataset in datasets:
        for bar in dataset['bars']:
            close = float(bar['close'])
            if close <= 0:
                continue
            entry = by_day.setdefault(bar['event_date'], {'close': [], 'amount': []})
            entry['close'].append(close)
            if bar.get('amount') is not None:
                entry['amount'].append(float(bar['amount']))
    return [{'date': day, 'close': statistics.mean(values['close']),
             'amount': sum(values['amount']) if len(values['amount']) == len(values['close']) else None}
            for day, values in sorted(by_day.items())]


def compute_sector_flow(datasets_by_code: dict[str, dict], body: dict) -> dict:
    series_by_name = {name: _merge([datasets_by_code[code] for code in codes
                                    if code in datasets_by_code])
                      for name, codes in SECTOR_CODES.items()}
    series_by_name = {name: bars for name, bars in series_by_name.items() if bars}
    dates = sorted({bar['date'] for bars in series_by_name.values() for bar in bars
                    if body['date_from'] <= bar['date'] <= body['date_to']})
    rows: list[dict] = []
    leading_days: dict[str, list[str]] = defaultdict(list)
    scores_by_sector: dict[str, list[float]] = defaultdict(list)
    missing_amount = False
    for day in dates:
        day_rows = []
        for sector, bars in series_by_name.items():
            by_date = {bar['date']: index for index, bar in enumerate(bars)}
            index = by_date.get(day)
            if index is None:
                continue
            current = bars[index]
            earlier = bars[index - 1] if index else current
            amount = current['amount']
            prior_amount = earlier['amount']
            recent = bars[max(0, index - body['flow_window'] + 1):index + 1]
            if amount is None or prior_amount is None or any(bar['amount'] is None for bar in recent):
                missing_amount = True
                continue
            mean_amount = statistics.mean(bar['amount'] for bar in recent)
            previous = earlier['close']
            return_pct = (current['close'] - previous) / previous * 100 if previous > 0 else 0
            day_rows.append({'date': day, 'sector': sector,
                             'return_pct': round(return_pct, 2),
                             'flow_score': round((amount / mean_amount - 1) * 100, 2)
                             if mean_amount > 0 else 0,
                             'amount': round(amount, 2),
                             'amount_delta': round(amount - prior_amount, 2),
                             'rank_return': 0, 'rank_flow': 0})
        by_return = sorted(day_rows, key=lambda row: -row['return_pct'])
        by_flow = sorted(day_rows, key=lambda row: -row['flow_score'])
        for rank, row in enumerate(by_return, 1):
            row['rank_return'] = rank
        for rank, row in enumerate(by_flow, 1):
            row['rank_flow'] = rank
        top = {row['sector'] for row in by_flow[:body['daily_top_n']]}
        for row in day_rows:
            scores_by_sector[row['sector']].append(row['flow_score'])
            if row['sector'] in top:
                leading_days[row['sector']].append(day)
            rows.append(row)
    series = []
    leaders = []
    score_lookup = {(row['sector'], row['date']): row['flow_score'] for row in rows}
    for sector, leading in leading_days.items():
        bars = series_by_name[sector]
        first = next((bar for bar in bars if bar['date'] >= body['date_from']), None)
        if first is None or first['close'] <= 0:
            continue
        points = [{'date': bar['date'],
                   'return_pct': round((bar['close'] - first['close']) / first['close'] * 100, 2),
                   'flow_score': score_lookup[(sector, bar['date'])]}
                  for bar in bars if (sector, bar['date']) in score_lookup]
        series.append({'sector': sector, 'points': points})
        leaders.append({'sector': sector, 'leader_days': len(leading),
                        'max_return_pct': max((point['return_pct'] for point in points), default=0),
                        'avg_flow_score': round(statistics.mean(scores_by_sector[sector]), 2),
                        'first_leader_date': leading[0], 'last_leader_date': leading[-1]})
    leaders.sort(key=lambda item: (-item['leader_days'], -item['max_return_pct'], item['sector']))
    rank = {item['sector']: index for index, item in enumerate(leaders)}
    series.sort(key=lambda item: rank[item['sector']])
    quality = ['HISTORICAL_AVAILABLE_AT_UNKNOWN', 'TDX_INPUTS_FROZEN_SEQUENTIALLY']
    if missing_amount:
        quality.append('AMOUNT_NOT_FOUND')
    return {'date_from': body['date_from'], 'date_to': body['date_to'],
            'daily_top_n': body['daily_top_n'], 'flow_window': body['flow_window'],
            'dates': dates, 'flow_table': rows, 'series': series, 'leaders': leaders,
            'loaded_index_count': len(datasets_by_code), 'loaded_sector_count': len(series_by_name),
            'quality_flags': quality,
            'metric_label': 'tdx_index_amount_relative_to_rolling_mean_not_net_capital_flow'}


def sector_code_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes() +
                          (Path(__file__).parent / 'sector_codes.py').read_bytes()).hexdigest()


def _view(row: SectorFlowRun, detail: bool = False) -> dict:
    result = json.loads(row.result_json)
    return {'id': row.id, 'created_at': row.created_at,
            'date_from': result['date_from'], 'date_to': result['date_to'],
            'loaded_index_count': result['loaded_index_count'],
            'leader_count': len(result['leaders']),
            'quality_flags': result['quality_flags'],
            **({'request': json.loads(row.request_json), 'result': result} if detail else {})}


def save_sector_run(session: Session, prepared: dict) -> dict:
    row = session.get(SectorFlowRun, prepared['id'])
    if row is None:
        row = SectorFlowRun(id=prepared['id'],
                            request_json=json.dumps(prepared['request'], ensure_ascii=False),
                            result_json=json.dumps(prepared['result'], ensure_ascii=False),
                            code_sha256=prepared['code_sha256'], created_at=utc_now())
        session.add(row)
        session.flush()
    return _view(row, True)


def list_sector_runs(session: Session) -> list[dict]:
    return [_view(row) for row in session.scalars(select(SectorFlowRun).order_by(
        SectorFlowRun.created_at.desc(), SectorFlowRun.id.desc()).limit(100))]


def get_sector_run(session: Session, run_id: str) -> dict:
    row = session.get(SectorFlowRun, run_id)
    if row is None:
        raise TradeError('SECTOR_RUN_NOT_FOUND', '板块资金记录不存在', 404)
    return _view(row, True)
