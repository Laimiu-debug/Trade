import ast
from copy import deepcopy
from itertools import product
from pathlib import Path
import subprocess
import sys

import pytest

from trade_app.platform.types import TradeError
from trade_app.research import lab_cli
from trade_app.research.lab_context import normalize_input, portfolio_context
from trade_app.research.lab_rule_verify import verify_rule, verify_dates, normalize_verification
from trade_app.research.portfolio_domain import canonical
from trade_app.research.trend_king_domain import CandlePoint
from test_strategy_lab import fixture
from legacy_oracle import oracle, plain


def original_rule():
    """``should_buy`` from the retired backend/scripts/verify_weike_rules.py (recording only)."""
    source = Path(__file__).resolve().parents[1] / 'scripts/verify_weike_rules.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    function = next(row for row in tree.body if isinstance(row, ast.FunctionDef) and row.name == 'should_buy')
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]),str(source),'exec'),scope)
    return scope['should_buy']


def request_for(value):
    days = [row['event_date'] for row in value['datasets'][0]['bars']]
    return {'symbol': value['datasets'][0]['symbol'], 'date_from': days[80], 'date_to': days[94],
            'want_dates': [days[81], days[90]], 'reject_dates': [days[82], days[92]]}


def test_verify_profile_matches_original_branches_and_all_threshold_boundaries():
    cases = []
    for trough, state, previous, retail, turn, formed in product(
            [.0499,.05,.0501,.1699,.17,.22,.2201,.30,.3001,.3499,.35,.52,.5201,.72],
            ['falling','flat','rising'], ['falling','flat'], [False,True], [False,True], [False,True]):
        indicator = {'trough_percentile':trough,'main_force_state':state,'retail_sell_signal':retail,'trough_turn':turn}
        cases.append(({'prev_main_force_state':previous}, indicator, {'rhythm_pattern_formed':formed}))

    def legacy():
        old = original_rule()
        return [list(old(ths, indicator, pattern=pattern)) for ths, indicator, pattern in cases]
    expected_rows = oracle('should_buy_grid', legacy)
    assert len(expected_rows) == len(cases)
    for (ths, indicator, pattern), expected in zip(cases, expected_rows):
        actual = verify_rule(ths,indicator,pattern=pattern)
        assert [actual['signal'],actual['reason']] == expected
    # This fixed diagnostic differs from tune_custom's flat-state deep trough.
    assert not verify_rule({}, {'trough_percentile':.04,'main_force_state':'flat'}, pattern={'rhythm_pattern_formed':True})['signal']


def test_actual_each_prefix_matches_original_formulas_and_labels_cannot_change_signals():
    value = normalize_input(fixture()); context = portfolio_context(value)
    request = normalize_verification(request_for(value), value['datasets'])
    result = verify_dates(context,request)
    assert result['scope'] == 'fixed_research_rule_verification_only'

    def legacy():
        from app.core.force_rhythm_strategy import calculate_force_rhythm_signal as old_rhythm, detect_rhythm_wave_pattern as old_pattern
        from app.core.ths_volume_signal import calculate_ths_main_retail_signal as old_ths
        old = original_rule(); rows = {}
        for row in result['daily_evaluations']:
            candles = [CandlePoint(time=bar['event_date'], **{key: float(bar[key]) for key in ('open','high','low','close')},volume=bar['volume'],amount=float(bar.get('amount') or 0))
                       for bar in context['datasets'][0]['bars'] if bar['event_date'] <= row['date']]
            indicator, ths = old_rhythm(candles), old_ths(candles)
            rows[row['date']] = {'indicator': indicator, 'ths': ths, 'rule': list(old(ths,indicator,pattern=old_pattern(indicator)))}
        return rows
    expected = oracle('daily_prefixes', legacy)
    assert set(expected) == {row['date'] for row in result['daily_evaluations']}
    for row in result['daily_evaluations']:
        assert plain(row['indicator']) == expected[row['date']]['indicator']
        assert plain(row['ths']) == expected[row['date']]['ths']
        assert [row['signal'],row['reason']] == expected[row['date']]['rule']
    changed = verify_dates(context,{**request,'want_dates':request['reject_dates'],'reject_dates':request['want_dates']})
    assert [(r['date'],r['signal'],r['reason'],r['indicator']) for r in changed['daily_evaluations']] == [(r['date'],r['signal'],r['reason'],r['indicator']) for r in result['daily_evaluations']]
    future = deepcopy(context)
    for bar in future['datasets'][0]['bars'][95:]: bar['close']='999999'
    assert verify_dates(future,request) == result


def test_want_reject_extra_and_unknown_are_distinct(monkeypatch):
    import trade_app.research.lab_rule_verify as module
    value = normalize_input(fixture()); ctx=portfolio_context(value); request=request_for(value)
    want0,want1=request['want_dates'];reject0,reject1=request['reject_dates']
    dates=[want0,want1,reject0,reject1,'2025-04-05']
    snapshots=[{'date':day,'indicator':{'trough_percentile':.04 if day!=want1 else .1,'main_force_state':'falling'},
                'ths':{},'purple_flip':False} for day in dates if day != reject1]
    monkeypatch.setattr(module,'collect_snapshots',lambda *args:(snapshots,[{'date':reject1,'reason':'late'}]))
    original=module.verify_rule
    monkeypatch.setattr(module,'verify_rule',lambda a,b:original(a,b,pattern={'rhythm_pattern_formed':True}))
    result=verify_dates(ctx,request)
    assert result['want_coverage']=={want0:True,want1:False}
    assert result['false_positive_dates']==[reject0]
    assert result['unobservable_reject_dates']==[reject1]
    assert result['extra_dates']==['2025-04-05']
    ctx['datasets'][0]['bars'][81]['available_at']=want0+'T20:00:00+00:00'
    monkeypatch.undo()
    result=verify_dates(ctx,request)
    assert result['want_coverage'][want0] is None and want0 in result['unobservable_want_dates']


def test_profile_input_rejects_overlap_unknown_parameters_and_real_worker_isolated(tmp_path):
    raw=fixture(); request=request_for(normalize_input(raw)); raw['rhythm_verification']=request
    with pytest.raises(TradeError): normalize_verification({**request,'reject_dates':request['want_dates']},normalize_input(fixture())['datasets'])
    with pytest.raises(TradeError): normalize_verification({**request,'flip_trough_percentile_min':.3},normalize_input(fixture())['datasets'])
    path=tmp_path/'verify-input.json';path.write_text(canonical(raw),encoding='utf-8');before=path.read_bytes()
    output=tmp_path/'verify';repo=Path(__file__).resolve().parents[2]
    process=subprocess.run([sys.executable,str(repo/'scripts/trade_rebuild_lab.py'),'verify-rhythm','--input',str(path),'--output',str(output)],cwd=repo,capture_output=True,timeout=45)
    assert process.returncode==0,process.stderr.decode('utf-8',errors='replace')
    result=lab_cli.read_json(output/'result.json')
    assert result['version']=='legacy-weike-rule-verification-v1'
    assert result['input_sha256'] and result['want_coverage'] and result['production_target_coverage']
    assert path.read_bytes()==before
