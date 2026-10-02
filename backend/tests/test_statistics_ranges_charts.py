from copy import deepcopy
from decimal import Decimal
import csv
import io

from reportlab.graphics.shapes import PolyLine, Circle
from sqlalchemy import select

from trade_app.reviews.pdf_charts import chart
from trade_app.reviews.equity_pdf import render_equity
from trade_app.research import sim_equity_service
from trade_app.trading.sim_models import SimFill
from test_sim_equity import scenario, prepare, save
from test_wyckoff_research_api import client_for, data, write


def test_buy_range_uses_exact_allocations_and_preserves_full_fifo_before_filter(tmp_path):
    with client_for(tmp_path) as client:
        account = data(write(client, '/sim-accounts', {'name': '按买入批次筛选', 'initial_capital': '100000', 'start_date': '2025-01-31'}))
        base = '/sim-accounts/' + account['id']
        def fill(day, side, qty, price):
            data(write(client, base + '/settle', {'to_date': day}))
            order = data(write(client, base + '/orders', {'symbol': '600000', 'side': side, 'quantity': qty,
                'limit_price': price, 'signal_date': day, 'submit_date': day}))
            return data(write(client, base + '/orders/' + order['id'] + '/fill', {'expected_revision': 1, 'fill_date': day, 'fill_price': price}))['fill']
        first = fill('2025-01-31', 'buy', 100, '10')
        second = fill('2025-02-03', 'buy', 100, '20')
        sale = fill('2025-02-04', 'sell', 200, '30')
        expected = sale['buy_allocations'][0]
        query = '?date_basis=buy&date_from=2025-01-01&date_to=2025-01-31'
        value = data(client.get('/api/v1' + base + '/performance' + query))
        assert value['buy_fill_count'] == 1 and value['sell_fill_count'] == 1
        row = value['closed_fills'][0]
        assert row['quantity'] == 100 and row['original_quantity'] == 200 and row['allocation_subset']
        assert row['realized_pnl'] == expected['realized_pnl']
        assert row['fees'] == expected['sell_fees'] and row['sell_gross'] == expected['sell_gross']
        assert row['buy_allocations'][0]['buy_fill_id'] == first['id']
        assert value['realized_curve'][0]['date'] == '2025-02-04' and value['monthly'][0]['month'] == '2025-01'
        root = '/api/v1/accounts/' + account['id'] + '/exports/performance'
        rows = list(csv.DictReader(io.StringIO(client.get(root + '/allocations.csv' + query).content.decode('utf-8-sig'))))
        assert len(rows) == 1 and rows[0]['buy_fill_id'] == first['id'] and rows[0]['realized_pnl'] == expected['realized_pnl']
        assert second['id'] not in str(rows)
        pdf = client.get(root + '.pdf' + query)
        assert pdf.status_code == 200 and b'/FontFile2' in pdf.content
        sell_view = data(client.get('/api/v1' + base + '/performance?date_basis=sell&date_from=2025-02-04&date_to=2025-02-04'))
        assert sell_view['realized_pnl'] == sale['realized_pnl'] and sell_view['closed_fills'][0]['quantity'] == 200
        empty = data(client.get('/api/v1' + base + '/performance?date_to=2025-02-03'))
        assert empty['closed_fills'] == [] and empty['realized_pnl'] == '0.00'
        for query in ('date_from=2025-02-30', 'date_from=2025-02-04&date_to=2025-01-01'):
            assert client.get('/api/v1' + base + '/performance?' + query).status_code == 400
        with client.app.state.db_factory.begin() as session:
            session.get(SimFill, sale['id']).allocations_json = None
        unknown = data(client.get('/api/v1' + base + '/performance?date_basis=buy&date_to=2025-01-31'))
        assert unknown['closed_fills'] == [] and unknown['buy_attribution_unavailable_fill_ids'] == [sale['id']]


def test_vector_chart_does_not_connect_or_fill_missing_prices():
    rows = [{'date': f'2025-01-0{i}', 'value': value} for i, value in enumerate(['100', '110', None, '130'], 1)]
    drawing = chart(rows, date_key='date', value_key='value')
    segments = [shape for shape in drawing.contents if isinstance(shape, PolyLine)]
    dots = [shape for shape in drawing.contents if isinstance(shape, Circle)]
    assert len(segments) == 1 and len(segments[0].points) == 4 and len(dots) == 1
    assert chart([{'date': '2025-01-01', 'value': None}], date_key='date', value_key='value') is None


def test_equity_pdf_uses_frozen_report_and_keeps_missingness(scenario, tmp_path):
    prepared = prepare(scenario)
    saved = save(scenario, prepared)
    before = deepcopy(saved)
    raw = render_equity(saved, author='中文权益署名')
    assert raw.startswith(b'%PDF-') and b'/FontFile2' in raw
    assert saved == before
    (tmp_path / 'equity.pdf').write_bytes(raw)
    # A report with no valuation prices is valid; no fake flat zero-equity curve.
    missing = save(scenario, prepare(scenario, dataset_ids=[]))
    assert any(row['total_assets'] is None for row in missing['result']['points'])
    assert render_equity(missing).startswith(b'%PDF-')
