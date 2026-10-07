import ast
from copy import deepcopy
from decimal import Decimal
import math
from pathlib import Path

import pytest

from trade_app.platform.types import TradeError
from trade_app.research import lab_cli
from trade_app.research.lab_score_validation import build_score_validation, build_trade_summary, summarize, monotonic_check
from test_strategy_lab import fixture,bars
from legacy_oracle import oracle


def evidence(scores):
    trades=[]
    for index,score in enumerate(scores):
        trades.append({'symbol':'sh600000','side':'buy','date':'2025-01-01','signal_date':'2024-12-31','price':'10','quantity':100,'fees':'0',
                       'reason_metrics':{'score':score,'components':{'evaluation':{'signal_score':score,'event_grade':'A' if score>=82 else 'B' if score>=70 else 'C'},'indicator':{'confirm_type':'hold_above'}}}})
        for day,quantity,pnl in [('2025-01-02',60,'60'),('2025-01-03',40,'-20')]:
            trades.append({'symbol':'sh600000','side':'sell','date':day,'quantity':quantity,'realized_pnl':pnl,'reason':'DAILY_TAKE_PROFIT' if quantity==60 else 'FIXED_HOLD'})
    context={'strategy_id':'chart_volume_swing_v1','datasets':[{'symbol':'sh600000','bars':bars(3)}]}
    return context,[{'result':{'trades':trades}}]


def test_score_edges_partial_legs_and_open_cycles_do_not_fake_samples():
    context,records=evidence([0,61.99,62,69.99,70,74.99,75,81.99,82,100])
    records[0]['result']['trades'].append(deepcopy(records[0]['result']['trades'][0]))
    actual=build_score_validation(context,records)
    assert [row['n'] for row in actual['score_buckets']]==[2]*5
    assert actual['summary']['n']==10 and len(actual['open_cycles'])==1
    assert Decimal(actual['summary']['avg_pnl'])==Decimal('.04')
    assert actual['completed_cycles'][0]['exit_legs']==2
    assert all(row['nondecreasing'] is None for row in actual['validation'].values())
    assert actual['summary']['pf'] is None and actual['summary']['pf_status']=='no_losses'
    assert actual['top30_by_score'][0]['signal_score']==100
    records[0]['result']['trades'][0]['reason_metrics']['components']['evaluation']['signal_score']=None
    assert len(build_score_validation(context,records)['missing_score_cycles'])==1
    context['strategy_id']='hybrid_band_v1'
    with pytest.raises(TradeError):build_score_validation(context,records)


def test_bucket_metrics_match_old_ratio_formula_with_explicit_undefined_pf_and_sample_gate():
    rows=[{'pnl_net':'.30','hit_tp':True,'holding_days':2},{'pnl_net':'-.1','hit_tp':False,'holding_days':3},{'pnl_net':'.05','hit_tp':False,'holding_days':4}]
    def legacy():
        # summarize() of the retired backend/scripts/backtest_chart_volume_swing.py.
        source=Path(__file__).resolve().parents[1]/'scripts/backtest_chart_volume_swing.py'
        node=next(row for row in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(row,ast.FunctionDef) and row.name=='summarize')
        scope={};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope)
        return scope['summarize'](rows)
    old=oracle('summarize_buckets',legacy);new=summarize(rows)
    for key in ('win_rate','avg_pnl','pf','tp_rate'):assert round(float(new[key]),4)==old[key]
    assert new['hit_30pct']==old['hit_30pct'] and new['avg_hold']==old['avg_hold']
    sufficient=[{'score_range':'low','n':20,'pf':'1.5'},{'score_range':'high','n':20,'pf':'1.4'}]
    assert monotonic_check(sufficient,'pf')['nondecreasing'] is False
    sufficient[-1]['pf']='1.6';assert monotonic_check(sufficient,'pf')['nondecreasing'] is True
    sufficient[-1]['pf']=None;assert monotonic_check(sufficient,'pf')['nondecreasing'] is None
    assert summarize([])['win_rate'] is None


def test_fast_target_counts_complete_cycles_only_with_exact_net_cash_cost():
    context, records = evidence([85, 85])
    trades = records[0]['result']['trades']
    for offset in (0,3):
        trades[offset+1]['realized_pnl']='200'; trades[offset+2]['realized_pnl']='80'
    # Entry gross rounds to cents in the actual ledger, preventing threshold drift.
    trades[0]['price']='10.00004'
    trades.pop()  # Second cycle remains partially open and cannot count as failure/success.
    result=build_trade_summary(context,records,window=2,target=.28)
    assert result['target_reached_count']==result['fast_target_reached_count']==1
    assert result['completed_cycles'][0]['entry_cost']=='1000.00'
    assert len(result['open_cycles'])==1 and result['summary']['n']==1
    assert build_trade_summary(context,records,window=1,target=.28)['fast_target_reached_count']==0


def test_real_chart_validation_reuses_paused_evidence_and_saved_entry_scores(tmp_path):
    raw=fixture();raw['strategy_id']='chart_volume_swing_v1';rows=bars(110)
    for i,row in enumerate(rows):
        close=20+math.sin(i/4)*1.3 if i<90 else 23+min(i-90,4)*.1
        volume=(1200000 if math.sin(i/4)>math.sin((i-1)/4) else 250000) if i<90 else 1600000 if i==90 else 900000 if i<93 else 200000
        row.update(open=f'{close-.05:.4f}',high=f'{close+.02:.4f}',low=f'{close-.18:.4f}',close=f'{close:.4f}',volume=volume,amount='500000000')
    raw['datasets'][0]['bars']=rows
    raw['config']={'max_holding_bars':3,'stop_loss_pct':'0','take_profit_pct':'0','daily_weak_clear':False}
    output=tmp_path/'validation'
    assert lab_cli.run_experiment(raw,output,'validate-chart',checkpoint_limit=1)['state']=='paused'
    first=(output/'variant-000/chunk-0000.json').read_bytes()
    assert lab_cli.run_experiment(raw,output,'validate-chart',resume=True)['state']=='succeeded'
    assert (output/'variant-000/chunk-0000.json').read_bytes()==first
    result=lab_cli.read_json(output/'result.json')['score_validation']
    assert result['summary']['n']>=1 and result['completed_cycles'][0]['signal_score']>75
    assert sum(row['n'] for row in result['score_buckets'])==result['summary']['n']
    assert result['validation']['win_rate_monotonic']['nondecreasing'] is None
    assert lab_cli.read_json(output/'result.json')['results'][0]['trade_statistics']['summary']['n']==result['summary']['n']
