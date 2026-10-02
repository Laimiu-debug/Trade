"""Complete statistics tables, with precise decimal strings and separate source rows."""
import csv
import io

from openpyxl import Workbook

from trade_app.api.csv_export import _cell
from trade_app.api.excel_export import _sheet
from trade_app.platform.types import TradeError
from trade_app.reviews.statistics import statistics_source


def statistics_tables(session, account_id, **options):
    value, meta = statistics_source(session, account_id, **options)
    meta.update({key: value[key] for key in ('method', 'kind', 'projection_status', 'projection_version',
        'calculation_version', 'wallet_revision', 'as_of_date', 'date_basis', 'date_from', 'date_to') if key in value})
    meta.update({'currency': 'CNY', 'amount_unit': 'yuan', 'return_unit': 'percent',
                 'date_timezone': 'Asia/Shanghai', 'null_means': 'unknown_or_not_applicable'})
    tables = {'metadata': (['field', 'value'], [{'field': key, 'value': item} for key, item in meta.items()])}
    if meta['account_kind'] == 'sim':
        tables['summary'] = (['month', 'sell_count', 'realized_pnl'], value['monthly'])
        tables['fills'] = (['fill_id', 'order_id', 'symbol', 'sell_date', 'quantity', 'sell_gross', 'fees',
                           'realized_pnl', 'allocation_quality', 'quality', 'allocation_subset', 'original_quantity'], value['closed_fills'])
        tables['allocations'] = (['sell_fill_id', 'buy_fill_id', 'buy_date', 'quantity', 'cost_basis',
                                 'sell_gross', 'sell_fees', 'realized_pnl'], [dict(source, sell_fill_id=fill['fill_id'])
            for fill in value['closed_fills'] for source in fill['buy_allocations']])
        tables['curve'] = (['date', 'fill_id', 'cumulative_realized_pnl'], value['realized_curve'])
        tables['unallocated'] = (['fill_id'], [{'fill_id': item} for item in value['buy_attribution_unavailable_fill_ids']])
        tables['metadata'][1].extend({'field': key, 'value': value[key]} for key in ('buy_fill_count', 'sell_fill_count',
            'realized_pnl', 'win_rate_pct', 'profit_factor', 'max_consecutive_wins', 'max_consecutive_losses', 'best_fill_id', 'worst_fill_id'))
    else:
        tables['summary'] = (['key', 'start_date', 'end_date', 'return_pct', 'return_quality', 'baseline_date',
            'last_confirmed_date', 'confirmed_points', 'coverage_days', 'last_nav', 'min_drawdown_pct',
            'closed_rounds', 'winning_rounds', 'losing_rounds', 'closed_pnl', 'win_rate_pct', 'payoff_ratio', 'profit_factor'],
            [{**row, **row['rounds']} for row in value['items']])
        tables['rounds'] = (['period_key', 'round_id'], [{'period_key': row['key'], 'round_id': item}
            for row in value['items'] for item in row['rounds']['round_ids']])
        tables['nodes'] = (['period_key', 'level', 'first_lit_date', 'days_from_start'],
                          [dict(item, period_key=row['key']) for row in value['items'] for item in row['node_achievements']])
    if sum(len(rows) for _columns, rows in tables.values()) > 100000:
        raise TradeError('STATISTICS_EXPORT_LIMIT', '统计导出超过100000行，请缩小范围')
    return tables


def statistics_xlsx(session, account_id, **options):
    tables = statistics_tables(session, account_id, **options)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, (columns, rows) in tables.items():
        _sheet(workbook, name, columns, rows)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def statistics_csv(session, account_id, table='summary', **options):
    tables = statistics_tables(session, account_id, **options)
    if table not in tables:
        raise TradeError('INVALID_STATISTICS_TABLE', '此账户类型没有该统计明细表')
    columns, rows = tables[table]
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\r\n')
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(key)) for key in columns])
    return ('\ufeff' + output.getvalue()).encode('utf-8')
