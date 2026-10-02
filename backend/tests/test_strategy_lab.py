from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from trade_app.platform.types import TradeError
from trade_app.research.chart_volume_swing_domain import calculate_chart_volume_swing_signal, evaluate_chart_volume_swing_signal
from trade_app.research.hybrid_band_domain import calculate_hybrid_band_signal
from trade_app.research.lab_context import normalize_input, portfolio_context
from trade_app.research.lab_domain import normalize_params, normalize_filters, signal_rows, band_statistics
from trade_app.research import lab_cli, lab_worker
from trade_app.research.portfolio_domain import canonical, digest
from trade_app.research.trend_king_domain import CandlePoint


def bars(count=96):
    rows = []
    for index in range(count):
        day = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        close = 10 + index * .03
        rows.append({'event_date': day, 'open': f'{close-.01:.4f}', 'high': f'{close+.1:.4f}',
            'low': f'{close-.1:.4f}', 'close': f'{close:.4f}', 'volume': 1000 + index % 7 * 23,
            'amount': '10000000', 'available_at': day + 'T08:00:00+00:00'})
    return rows


def fixture():
    return {'format': 'trade-lab-input-v1', 'strategy_id': 'hybrid_band_v1',
        'datasets': [{'symbol': '600000.SH', 'bars': bars()}]}


def test_chart_hybrid_port_matches_original_formulas_across_prefixes():
    from app.models import CandlePoint as OldCandle
    from app.core.chart_volume_swing_strategy import calculate_chart_volume_swing_signal as old_chart
    from app.core.chart_volume_swing_strategy import evaluate_chart_volume_swing_signal as old_evaluation
    from app.core.hybrid_band_strategy import calculate_hybrid_band_signal as old_hybrid

    values = bars(130)
    # Flat-to-breakout-to-shrinking-volume shape also exercises setup scoring.
    for index, row in enumerate(values):
        close = 10 + (index % 6) * .04 if index < 100 else 10.7 + (index-100) * .06
        row.update(open=f'{close-.01:.4f}', high=f'{close+.1:.4f}', low=f'{close-.15:.4f}', close=f'{close:.4f}',
                   volume=1500 if index == 100 else 900 if index < 100 else 400)
    new = [CandlePoint(row['event_date'], float(row['open']), float(row['high']), float(row['low']),
        float(row['close']), row['volume'], float(row['amount'])) for row in values]
    old = [OldCandle(**asdict(row)) for row in new]
    for size in (0, 60, 80, 99, 101, 104, 110, 130):
        actual, expected = calculate_chart_volume_swing_signal(new[:size]), old_chart(old[:size])
        assert actual == expected
        assert evaluate_chart_volume_swing_signal(actual) == old_evaluation(expected)
        for rank in (None, 100, 101, 201, 501):
            assert calculate_hybrid_band_signal(new[:size], ret40_rank=rank) == old_hybrid(old[:size], ret40_rank=rank)


@pytest.mark.parametrize('change', [
    {'unknown': 1}, {'box_windows': [9]}, {'box_windows': [20, 20]}, {'min_score': float('nan')},
    {'confirm_hold_days': 1.5}, {'hold_ratio': 0}, {'box_range_min': .5, 'box_range_max': .3}])
def test_unimplemented_and_invalid_parameters_rejected(change):
    with pytest.raises(TradeError):
        normalize_params('hybrid_band_v1', change)


def test_input_aliases_amount_unknown_and_rank_ties_are_explicit():
    raw = fixture()
    duplicate = deepcopy(raw['datasets'][0])
    duplicate['symbol'] = 'SH600000'
    raw['datasets'].append(duplicate)
    with pytest.raises(TradeError, match='同一证券'):
        normalize_input(raw)
    prefixes = {'sh600001': bars(), 'sh600000': bars()}
    for row in prefixes['sh600000']:
        row.pop('amount')
    result = signal_rows(prefixes, 'hybrid_band_v1', normalize_params('hybrid_band_v1', {}),
                         normalize_filters({'min_amount20': '100'}))
    assert result['sh600000']['components']['ret40_rank'] == 1
    assert result['sh600001']['components']['ret40_rank'] == 2
    assert result['sh600000']['components']['amount20'] is None
    assert 'amount20_missing' in result['sh600000']['reasons']
    assert not result['sh600000']['buy']


def test_scan_excludes_late_publication_and_future_prices():
    raw = fixture()
    raw['as_of_date'] = raw['datasets'][0]['bars'][88]['event_date']
    value = normalize_input(raw)
    payload = {'operation': 'scan', 'context': portfolio_context(value), 'as_of_date': value['as_of_date']}
    first = lab_worker.compute(payload)
    future = payload['context']['datasets'][0]['bars'][89]
    future.update(open='9999', high='9999', close='9999', low='9999')
    assert lab_worker.compute(payload) == first
    payload['context']['datasets'][0]['bars'][88]['available_at'] = value['as_of_date'] + 'T16:01:00+00:00'
    excluded = lab_worker.compute(payload)
    assert excluded['rows'] == {} and excluded['omitted'][0]['reason'] == 'no_complete_fresh_history'


def test_band_statistics_does_not_count_incomplete_final_window():
    equity = [{'date': f'2025-01-0{index}', 'total_assets': str(100+index*10)} for index in range(1, 6)]
    result = band_statistics(equity, '100', window=2, target=.1)
    assert result['complete_bands'] == 2 and result['target_reached_count'] == 2
    assert result['bands'][1]['starting_assets'] == '120.00'
    assert result['bands'][-1]['complete'] is False


def test_pause_resume_reconstructs_committed_chunks_and_rejects_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(lab_cli, 'compute', lab_worker.compute)
    raw = fixture()
    output = tmp_path / 'experiment'
    original = deepcopy(raw)
    assert lab_cli.run_experiment(raw, output, 'backtest', checkpoint_limit=1)['state'] == 'paused'
    chunk = output / 'variant-000/chunk-0000.json'
    first = chunk.read_bytes()
    assert lab_cli.run_experiment(raw, output, 'backtest', resume=True)['state'] == 'succeeded'
    assert chunk.read_bytes() == first and raw == original
    result = json.loads((output / 'result.json').read_text(encoding='utf-8'))
    assert result['results'][0]['summary']['completed_days'] == 16
    record = json.loads(first)
    record['result']['checkpoint']['cash'] = '9999999'
    chunk.write_text(canonical(record), encoding='utf-8')
    with pytest.raises(TradeError, match='校验失败'):
        lab_cli.run_experiment(raw, output, 'backtest', resume=True)


def test_unknown_historical_availability_requires_explicit_retrospective_mode():
    raw = fixture()
    for row in raw['datasets'][0]['bars']:
        row['available_at'] = None
    value = normalize_input(raw)
    payload = {'operation': 'scan', 'context': portfolio_context(value), 'as_of_date': value['as_of_date']}
    assert lab_worker.compute(payload)['rows'] == {}
    payload['context']['config']['execution_strict'] = False
    retrospective = lab_worker.compute(payload)
    assert retrospective['rows']['sh600000']['source_date'] == value['as_of_date']
    assert 'historical_availability_assumed_at_local_close' in retrospective['quality_flags']
    payload['context']['datasets'][0]['bars'][-1]['available_at'] = '2026-01-01T00:00:00Z'
    assert lab_worker.compute(payload)['rows'] == {}


def test_resume_cannot_mix_inputs_or_code_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(lab_cli, 'compute', lab_worker.compute)
    output, raw = tmp_path / 'experiment', fixture()
    lab_cli.run_experiment(raw, output, 'backtest', checkpoint_limit=1)
    changed = {**raw, 'filters': {'daily_top': 2}}
    with pytest.raises(TradeError, match='已变化'):
        lab_cli.run_experiment(changed, output, 'backtest', resume=True)
    monkeypatch.setattr(lab_cli, 'code_sha256', lambda: 'changed')
    with pytest.raises(TradeError, match='已变化'):
        lab_cli.run_experiment(raw, output, 'backtest', resume=True)


def test_optimization_records_each_input_and_does_not_claim_out_of_sample(tmp_path, monkeypatch):
    monkeypatch.setattr(lab_cli, 'compute', lab_worker.compute)
    raw = fixture()
    raw['variants'] = [{'params': {'min_hybrid_score': 82}}, {'params': {'min_hybrid_score': 92}}]
    lab_cli.run_experiment(raw, tmp_path / 'opt', 'optimize')
    report = json.loads((tmp_path / 'opt/result.json').read_text(encoding='utf-8'))
    assert report['ranking_scope'] == 'in_sample_only' and report['ranking'] == [0, 1]
    assert report['results'][0]['parameters']['params']['min_hybrid_score'] == 82
    assert report['results'][1]['parameters']['params']['min_hybrid_score'] == 92
    assert report['results'][0]['input_sha256'] != report['results'][1]['input_sha256']


def test_json_duplicates_pickle_and_nonfinite_are_rejected(tmp_path):
    path = tmp_path / 'data.json'
    for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'\x80\x04pickle'):
        path.write_bytes(raw)
        with pytest.raises(TradeError):
            lab_cli.read_json(path)


def test_real_cli_child_scan_outputs_utf8_safe_report_without_mutating_input(tmp_path):
    raw = fixture()
    source = tmp_path / '实验输入.json'
    source.write_text(canonical(raw), encoding='utf-8')
    before = source.read_bytes()
    destination = tmp_path / '晨报'
    repo = Path(__file__).resolve().parents[2]
    completed = subprocess.run([sys.executable, str(repo / 'scripts/trade_rebuild_lab.py'), 'morning',
        '--input', str(source), '--output', str(destination)], cwd=repo, capture_output=True, timeout=45)
    assert completed.returncode == 0, completed.stderr.decode('utf-8', errors='replace')
    report = json.loads((destination / 'result.json').read_text(encoding='utf-8'))
    assert report['as_of_date'] == raw['datasets'][0]['bars'][-1]['event_date']
    assert report['rows']['sh600000']['components']['rank_scope'] == 'fixed_research_sample'
    assert '独立策略实验报告' in (destination / 'report.html').read_text(encoding='utf-8')
    assert source.read_bytes() == before


def test_real_worker_backtest_executes_after_signal_and_resumes_identically(tmp_path):
    raw = fixture()
    raw['params'] = {'min_score': 0, 'min_hybrid_score': 0}
    values = bars(106)
    for index, row in enumerate(values):
        close = 20 + math.sin(index / 4) * 1.3 if index < 90 else 23 + (index-90) * .1
        volume = 800000 if index < 90 else 6000000 if index == 90 else 1500000 if index < 93 else 300000
        row.update(open=f'{close-.05:.4f}', high=f'{close+.15:.4f}', low=f'{close-.15:.4f}', close=f'{close:.4f}', volume=volume)
    raw['datasets'][0]['bars'] = values
    raw['config'] = {'max_holding_bars': 4, 'stop_loss_pct': '0', 'take_profit_pct': '0', 'daily_weak_clear': False}
    output = tmp_path / 'real-process'
    assert lab_cli.run_experiment(raw, output, 'backtest', checkpoint_limit=3)['state'] == 'paused'
    assert lab_cli.run_experiment(raw, output, 'backtest', resume=True)['state'] == 'succeeded'
    chunks = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((output / 'variant-000').glob('chunk-*.json'))]
    trades = [row for chunk in chunks for row in chunk['result']['trades']]
    buys, sells = [row for row in trades if row['side'] == 'buy'], [row for row in trades if row['side'] == 'sell']
    assert buys and sells and buys[0]['date'] == values[94]['event_date']
    assert buys[0]['quantity'] % 100 == 0 and float(buys[0]['fees']) > 0
    assert sells[0]['date'] > buys[0]['date']
    context = portfolio_context(normalize_input(raw))
    from trade_app.research.portfolio_domain import run_chunk
    direct = run_chunk(context, days=len(context['calendar']), signal_provider=lambda prefixes, dates:
        signal_rows(prefixes, context['strategy_id'], context['params'], context['filters']))
    assert chunks[-1]['result']['checkpoint'] == direct['checkpoint']
    assert trades == direct['trades']


def test_traditional_strategy_comparison_uses_registered_normalizers(tmp_path, monkeypatch):
    monkeypatch.setattr(lab_cli, 'compute', lab_worker.compute)
    raw = fixture()
    raw['strategy_id'] = 'ths_main_force_flip_v1'
    raw['variants'] = [{'strategy_id': 'ths_main_force_flip_v1'}, {'strategy_id': 'ths_main_force_golden_cross_v1'}]
    lab_cli.run_experiment(raw, tmp_path / 'compare', 'optimize')
    result = lab_cli.read_json(tmp_path / 'compare/result.json')
    assert [row['parameters']['strategy_id'] for row in result['results']] == [item['strategy_id'] for item in raw['variants']]
    raw['filters'] = {'rank_bonus': True}
    with pytest.raises(TradeError, match='传统策略不使用'):
        normalize_input(raw)
