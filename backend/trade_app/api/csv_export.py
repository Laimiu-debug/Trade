"""Stable, account-scoped CSV exports for the rebuilt ledger."""
from __future__ import annotations

import csv
import io
import json
import re

from sqlalchemy.orm import Session

from trade_app.analytics.service import projection_status
from trade_app.platform.types import TradeError
from trade_app.trading.service import account_or_error, list_flows, list_snapshots, list_trades
from trade_app.trading.simulation import list_fills, list_orders, sim_account


HEADERS = {
    'trades': ['id', 'trade_date', 'sequence', 'symbol', 'name', 'side', 'quantity',
               'price', 'fee', 'calculated_fee', 'fee_source', 'fee_rule_version',
               'note', 'revision'],
    'flows': ['id', 'flow_date', 'kind', 'amount', 'note', 'revision'],
    'snapshots': ['id', 'snap_date', 'total_assets', 'available_cash',
                  'position_value', 'positions_json', 'revision'],
    'rounds': ['id', 'symbol', 'name', 'start_date', 'end_date', 'status',
               'pnl', 'trade_ids', 'projection_status', 'projection_version'],
    'sim-fills': ['id', 'order_id', 'fill_date', 'symbol', 'side', 'quantity',
                  'fill_price', 'gross', 'commission', 'stamp', 'transfer',
                  'realized_pnl', 'price_source'],
}


def _cell(value: object) -> object:
    if value is None:
        return ''
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        if not re.fullmatch(r'-?\d+(?:\.\d+)?', value):
            return "'" + value
    return value


def account_csv(session: Session, account_id: str, kind: str) -> bytes:
    if kind not in HEADERS:
        raise TradeError('INVALID_EXPORT_KIND', '不支持的 CSV 导出类型')
    if kind == 'sim-fills':
        sim_account(session, account_id)
        orders = {row['id']: row for row in list_orders(session, account_id)}
        rows = [{**row, 'symbol': orders[row['order_id']]['symbol'],
                 'side': orders[row['order_id']]['side'],
                 'quantity': orders[row['order_id']]['quantity']}
                for row in list_fills(session, account_id)]
    else:
        account_or_error(session, account_id, real=True)
        if kind == 'trades':
            rows = list_trades(session, account_id)
        elif kind == 'flows':
            rows = list_flows(session, account_id)
        elif kind == 'snapshots':
            rows = [{**row, 'positions_json': json.dumps(row['positions'], ensure_ascii=False)}
                    for row in list_snapshots(session, account_id)]
        else:
            projection = projection_status(session, account_id)
            rows = [{**row, 'trade_ids': '|'.join(row['trade_ids']),
                     'projection_status': projection['status'],
                     'projection_version': projection['projection_version']}
                    for row in (projection['result'] or {}).get('rounds', [])]
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\r\n')
    columns = HEADERS[kind]
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(column)) for column in columns])
    return ('\ufeff' + output.getvalue()).encode('utf-8')
