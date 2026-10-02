import ast
from copy import deepcopy
from pathlib import Path
import json
import math
import subprocess
import sys

import pytest

from trade_app.platform.types import TradeError
from trade_app.research import lab_cli, lab_worker, lab_position
from trade_app.research.lab_context import normalize_input, portfolio_context
from trade_app.research.lab_presets import preset_catalog, adopt_preset
from trade_app.research.lab_target_fit import evaluate_custom, normalize_params, fit_targets, normalize_fit
from trade_app.research.portfolio_domain import canonical
from test_strategy_lab import fixture, bars


def test_four_presets_match_original_constants_and_require_explicit_adaptation():
    old_path = Path(__file__).resolve().parents[3] / 'final-trade/backend/scripts/morning_band_report.py'
    module = ast.parse(old_path.read_text(encoding='utf-8'))
    declaration = next(row for row in module.body if isinstance(row, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'CANDIDATES' for target in row.targets))
    scope = {'dict': dict}
    exec(compile(ast.Module(body=[declaration], type_ignores=[]), str(old_path), 'exec'), scope)
    catalog = preset_catalog()['presets']
    assert [row['original'] for row in catalog] == scope['CANDIDATES']
    raw = fixture(); before = deepcopy(raw)
    for preset in catalog:
        with pytest.raises(TradeError) as error: adopt_preset(raw, preset['id'])
        assert error.value.code == 'LAB_PRESET_NOT_EQUIVALENT'
        adopted = adopt_preset(raw, preset['id'], accept_adapted=True)
        assert adopted['preset_adoption']['exact_legacy_equivalence'] is False
        assert adopted['config']['max_positions'] == preset['original']['max_slots']
        assert adopted['filters']['sources'] == preset['original']['filter']['sources']
        assert adopted['filters']['rank_bonus'] is True
        assert float(adopted['config']['trail_activate']) == preset['original']['exit']['trail_activate']
        assert adopted['config']['time_stop_days'] == preset['original']['exit']['time_stop_days']
        assert adopted['config']['structural_box_stop'] and adopted['config']['ma20_box_break']
        assert preset['unsupported_legacy_fields'] == {}
    assert raw == before


def old_custom():
    path = Path(__file__).resolve().parents[3] / 'final-trade/backend/scripts/tune_weike_rhythm.py'
    module = ast.parse(path.read_text(encoding='utf-8'))
    node = next(row for row in module.body if isinstance(row, ast.FunctionDef) and row.name == '_eval_custom')
    from app.core.force_rhythm_strategy import resolve_force_rhythm_params
    scope = {'resolve_force_rhythm_params': resolve_force_rhythm_params}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    return scope['_eval_custom']


def indicator(**changes):
    return {'has_data': True, 'cycle_count': 4, 'cycle_cv': .1, 'amplitude_cv': .1, 'autocorr': .9,
        'wave_swing_ratio': .5, 'trough_percentile': .04, 'main_force_state': 'flat', 'trough_turn': False,
        'main_force': 20, 'retail_force': 10, 'retail_percentile': .2, 'retail_force_state': 'flat', **changes}


def test_legacy_fit_predicate_parity_including_boundaries_and_nonproduction_trigger():
    original = old_custom()
    for mode in ('combined', 'flip_only', 'turn_only'):
        params = normalize_params({'buy_trigger_mode': mode})
        for trough in (.04, .08, .25, .30, .40, .55, .72, .80):
            for state in ('falling', 'flat', 'rising'):
                for retail in (10, 30):
                    row = indicator(trough_percentile=trough, main_force_state=state, retail_force=retail,
                        retail_force_state='rising', trough_turn=trough <= .4)
                    ths = {'prev_main_force_state': 'falling'}
                    assert evaluate_custom(row, ths, params)['signal'] == original(row, ths, params)
    # The legacy custom deep trough admits flat state without production streak.
    assert evaluate_custom(indicator(), {}, normalize_params({}))['signal']
    with pytest.raises(TradeError): normalize_params({'deep_trough_streak_target': 2})


def test_target_labels_change_only_score_and_missing_target_evidence_is_explicit(monkeypatch):
    from trade_app.research import lab_target_fit as fit
    raw = fixture(); value = normalize_input(raw); ctx = portfolio_context(value)
    monkeypatch.setattr(fit, 'calculate_force_rhythm_signal', lambda candles: indicator())
    monkeypatch.setattr(fit, 'calculate_ths_main_retail_signal', lambda candles: {'purple_to_yellow': int(candles[-1].time[-2:]) % 2 == 0})
    dates = [row['event_date'] for row in bars()[90:94]]
    request = normalize_fit({'symbol': 'sh600000', 'date_from': dates[0], 'date_to': dates[-1], 'target_dates': [dates[0]],
        'variants': [{}, {'deep_trough_percentile_max': .01}]}, value['datasets'])
    actual = fit_targets(ctx, request)
    best = actual['results'][0]
    assert best['complete_target_match'] and len(best['signal_dates']) == 4
    assert best['legacy_exact_fit_score'] == sum(best['score_terms'].values())
    changed = fit_targets(ctx, {**request, 'target_dates': [dates[1]]})
    assert actual['snapshots'] == changed['snapshots']
    assert actual['results'][0]['daily_evaluations'] == changed['results'][0]['daily_evaluations']
    future = deepcopy(ctx)
    for row in future['datasets'][0]['bars'][94:]: row['close'] = '99999'
    assert fit_targets(future, request) == actual
    ctx['datasets'][0]['bars'][90]['available_at'] = dates[0] + 'T20:00:00+00:00'
    missing = fit_targets(ctx, request)
    assert missing['selected_variant'] is None and dates[0] in missing['unobservable_target_dates']


def position_fixture():
    raw = fixture()
    raw['strategy_id'] = 'ths_main_force_golden_cross_v1'
    rows = bars(267)
    for index, row in enumerate(rows):
        row.update(volume=10000000, amount='100000000' if index < 252 else '0')
    raw['datasets'][0]['bars'] = rows
    raw.update(start_date=rows[251]['event_date'], end_date=rows[-1]['event_date'], as_of_date=rows[-1]['event_date'],
        config={'max_holding_bars': 1, 'stop_loss_pct': '0', 'take_profit_pct': '0', 'daily_weak_clear': False},
        step1={'parameters': {'amount_threshold': 100000000, 'amplitude_threshold': .01},
            'float_shares': [{'symbol': 'sh600000', 'value': 100000000, 'as_of_date': '2024-01-01'}], 'focus_symbols': ['sh600000']})
    return raw


def test_position_step1_structured_replay_differs_from_static_and_resumes(tmp_path, monkeypatch):
    def always(prefixes, strategy, params, filters):
        return {symbol: {'in_pool': True, 'buy': True, 'sell': False, 'score': 1, 'source_date': rows[-1]['event_date'],
            'reasons': [], 'components': {}, 'quality_flags': [], 'ma10': None} for symbol, rows in prefixes.items()}
    monkeypatch.setattr(lab_position, 'signal_rows', always)
    monkeypatch.setattr(lab_cli, 'compute', lab_worker.compute)
    raw = position_fixture(); output = tmp_path / 'diagnostic'
    assert lab_cli.run_experiment(raw, output, 'diagnose-position', checkpoint_limit=1)['state'] == 'paused'
    before = (output / 'static/chunk-0000.json').read_bytes()
    assert lab_cli.run_experiment(raw, output, 'diagnose-position', resume=True)['state'] == 'succeeded'
    assert (output / 'static/chunk-0000.json').read_bytes() == before
    result = lab_cli.read_json(output / 'result.json')
    assert result['comparison'][0]['static_pool_member']
    assert not result['comparison'][0]['position_pool_member']
    assert len(result['paths']['position']['pool_refreshes']) >= 2
    assert result['paths']['position']['focused_trades']['sh600000']
    assert result['paths']['static']['equity'][-1]['date'] == raw['as_of_date']
    for mode in ('static', 'position'):
        assert all(row['price'] is None and row['quantity'] is None for row in result['paths'][mode]['plan_signals'])


def test_step1_future_unknown_float_shares_and_short_history_are_not_assumed():
    raw = position_fixture(); value = normalize_input(raw); ctx = portfolio_context(value)
    prefixes = {item['symbol']: item['bars'][:252] for item in ctx['datasets']}
    assert lab_position.step1_signals(prefixes, ctx)['sh600000']['in_pool']
    ctx['step1']['float_shares'][0]['as_of_date'] = '2030-01-01'
    row = lab_position.step1_signals(prefixes, ctx)['sh600000']
    assert not row['in_pool'] and 'FLOAT_SHARES_FROM_FUTURE' in row['quality_flags']
    ctx['step1']['float_shares'][0]['as_of_date'] = None
    assert not lab_position.step1_signals(prefixes, ctx)['sh600000']['in_pool']
    prefixes['sh600000'] = prefixes['sh600000'][:250]
    assert lab_position.step1_signals(prefixes, ctx)['sh600000']['components']['step1']['reasons'] == ['STEP1_REQUIRES_251_KNOWN_BARS']


def test_real_target_fit_cli_is_frozen_separate_profile_and_does_not_modify_source(tmp_path):
    raw = fixture(); dates = [row['event_date'] for row in raw['datasets'][0]['bars'][90:]]
    raw['target_fit'] = {'symbol': 'sh600000', 'date_from': dates[0], 'date_to': dates[-1], 'target_dates': [dates[1]], 'variants': [{}, {'deep_trough_percentile_max': .04}]}
    path = tmp_path / '目标.json'; path.write_text(canonical(raw), encoding='utf-8'); before = path.read_bytes()
    output = tmp_path / 'fit'
    repo = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, str(repo / 'scripts/trade_rebuild_lab.py'), 'target-fit', '--input', str(path), '--output', str(output)], cwd=repo, capture_output=True, timeout=45)
    assert done.returncode == 0, done.stderr.decode('utf-8', errors='replace')
    result = lab_cli.read_json(output / 'result.json')
    assert result['scope'] == 'supervised_in_sample_target_fit_only' and len(result['results']) == 2
    assert path.read_bytes() == before and result['input_sha256']


def test_real_adopted_morning_pipeline_executes_versioned_time_exit(tmp_path):
    raw = fixture()
    rows = bars(110)
    for index, row in enumerate(rows):
        close = 20 + math.sin(index / 4) * 1.3 if index < 90 else 23 + min(index - 90, 4) * .1
        volume = (1200000 if math.sin(index / 4) > math.sin((index - 1) / 4) else 250000) if index < 90 else 1600000 if index == 90 else 900000 if index < 93 else 200000
        row.update(open=f'{close-.05:.4f}', high=f'{close+.02:.4f}', low=f'{close-.18:.4f}', close=f'{close:.4f}',
            volume=volume, amount='500000000')
    raw['datasets'][0]['bars'] = rows
    adopted = adopt_preset(raw, 'hybrid_compound', accept_adapted=True)
    output = tmp_path / 'pipeline'
    result = lab_cli.run_experiment(adopted, output, 'backtest')
    assert result['state'] == 'succeeded'
    records = [lab_cli.read_json(path) for path in sorted((output / 'variant-000').glob('chunk-*.json'))]
    trades = [row for record in records for row in record['result']['trades']]
    assert any(row['side'] == 'buy' and row['reason_metrics']['entry_box_high'] for row in trades)
    assert any(row['reason'] == 'BAND_TIME_STOP_NEXT_OPEN' for row in trades)
    assert all(row['signal_date'] < row['date'] for row in trades if row['side'] == 'buy')
    report = lab_cli.read_json(output / 'result.json')
    assert report['preset_reference']['unsupported_legacy_fields'] == {}
    assert report['preset_adoption']['exact_legacy_equivalence'] is False


@pytest.mark.parametrize('volume', [100.5, 100.0, True, '100'])
def test_lab_volume_is_strict_before_market_normalizer_can_coerce(volume):
    raw = fixture(); raw['datasets'][0]['bars'][-1]['volume'] = volume
    with pytest.raises(TradeError) as error: normalize_input(raw)
    assert error.value.code == 'INVALID_LAB_VOLUME'


def test_step1_daily_cap_applies_after_membership_and_preserves_stricter_execution_cap(monkeypatch):
    raw = position_fixture()
    raw['filters'] = {'daily_top': 2}
    raw['config']['entry_top_k'] = 1
    for symbol in ('sh600001', 'sh600002', 'sh600003'):
        raw['datasets'].append({**deepcopy(raw['datasets'][0]), 'symbol': symbol})
    value = normalize_input(raw); context = portfolio_context(value)
    assert context['config']['entry_top_k'] == 1
    assert lab_position.diagnostic_context(context, 'position')['config']['entry_top_k'] == 1
    assert value['config']['entry_top_k'] == 1
    def rows(prefixes, ctx):
        return {symbol: {'buy': True, 'in_pool': symbol != 'sh600000', 'score': 100 - index}
                for index, symbol in enumerate(sorted(prefixes))}
    monkeypatch.setattr(lab_position, 'step1_signals', rows)
    result = lab_worker.compute({'operation': 'scan', 'context': context, 'as_of_date': value['as_of_date']})
    assert result['selected_symbols'] == ['sh600001', 'sh600002']
    assert result['rows']['sh600000']['buy'] and not result['rows']['sh600000']['selected']
    assert result['rows']['sh600003']['buy'] and not result['rows']['sh600003']['selected']
