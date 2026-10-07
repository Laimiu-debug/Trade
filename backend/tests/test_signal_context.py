"""Full scanner semantics: original plugin ranking, frozen candidates and PIT."""
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from legacy_oracle import oracle, plain
from trade_app.platform.types import TradeError
from trade_app.platform.db import open_database
from trade_app.research.signal_context import (candidate, context_catalog, normalize_context,
    normalize_context_params, evaluate_context)
from trade_app.research.signal_workspace_domain import rank_score
from trade_app.research.event_profile_service import freeze_profile
from trade_app.research.scan_job_service import prepare_scan_job
from trade_app.research.scan_job_worker import compute_chunk
from trade_app.research.scan_service import get_scan
from trade_app.research.signal_workspace_service import prepare_report
from test_strategy_scan_jobs import enqueue, finish, inline_child
from test_strategy_scans import _bars, _import
from test_wulong_universe import _legacy_row

STORE = {'candidate_path':'legacy_store','window_days':60,'universe_mode':'strategy_filtered'}
TDX = {'candidate_path':'legacy_tdx','window_days':60,'universe_mode':'strategy_filtered'}


@pytest.fixture
def context_scenario(tmp_path):
    engine, factory = open_database(tmp_path)
    bars = _bars(wulong=True)
    first = _import(factory,tmp_path,'600000.SH',bars)
    second = _import(factory,tmp_path,'600001',bars)
    yield factory,tmp_path,first,second,bars
    engine.dispose()


def test_all_context_catalog_parameters_are_used_and_strict():
    assert len(context_catalog()) == 16
    for row in context_catalog():
        params = normalize_context_params(row['id'],{})
        assert set(params)==set(row['signal_params'])
        with pytest.raises(TradeError): normalize_context_params(row['id'],{'unused':1})
        with pytest.raises(TradeError): normalize_context_params(row['id'],{'min_score':float('nan')})
        with pytest.raises(TradeError): normalize_context_params(row['id'],{'require_sequence':'false-ish'})
    with pytest.raises(TradeError): normalize_context({'candidate_path':'made_up'})
    with pytest.raises(TradeError): normalize_context({'window_days':True})


def _legacy_rank(key, strategy, signal, metrics, params, fallback_score):
    def compute():
        from app.core.strategy_plugins import RelativeStrengthBreakoutPlugin, MatrixSignalPlugin, B1MultiTimeframePlugin
        plugin = {'relative_strength_breakout_v1': RelativeStrengthBreakoutPlugin, 'matrix_signal_v1': MatrixSignalPlugin,
                  'b1_mtf_v1': B1MultiTimeframePlugin}[strategy]()
        return plugin.rank_signals(signal=signal, row=SimpleNamespace(**metrics), params=params, fallback_score=fallback_score)
    return oracle(key, compute)


@pytest.mark.parametrize('strategy',['relative_strength_breakout_v1','matrix_signal_v1','b1_mtf_v1'])
@pytest.mark.parametrize('weights', [{},{'rank_weight_health':0,'rank_weight_event':1,'rank_weight_strength':0,'rank_weight_volume':0,'rank_weight_structure':0}])
def test_added_rank_formulas_match_original_plugins(strategy,weights):
    metrics = {'ret40':.13,'retrace20':.08,'up_down_volume_ratio':1.36,'vol_slope20':.05,
        'pullback_volume_ratio':.7,'price_vs_ma20':.03,'pullback_days':2,'ma10_above_ma20_days':12}
    params = normalize_context_params(strategy,weights if strategy=='relative_strength_breakout_v1' else {})
    signal = SimpleNamespace(entry_quality_score=72,health_score=61,event_score=83)
    actual = rank_score(strategy,{'indicator':vars(signal)},metrics,params=params)
    expected = _legacy_rank(f'rank:{strategy}:{bool(weights)}',strategy,signal,metrics,params,50)
    assert actual['rank_score']==pytest.approx(expected)


def test_all_16_paths_execute_real_indicators(context_scenario):
    factory,path,first,_second,bars=context_scenario
    with factory() as session:
        profile=freeze_profile(session)
    metrics=candidate(bars,{'id':first,'symbol':'sh600000'},STORE,bars[-1]['event_date'])
    for row in context_catalog():
        params=normalize_context_params(row['id'],{})
        pool=None
        if row['id']=='matrix_signal_v1':
            from trade_app.research.matrix_domain import evaluate_matrix_pool,MATRIX_PARAM_KEYS
            pool=evaluate_matrix_pool([metrics],{key:params[key] for key in MATRIX_PARAM_KEYS})['rows'][0]
        result=evaluate_context(row['id'],symbol='sh600000',bars=bars,params=params,profile=profile,context=STORE,frozen_candidate=metrics,pool_evaluation=pool)
        assert result['status']=='computed',row['id']
        assert result['ranking']['rank_score'] is not None,row['id']
        assert result['evaluation']['params']['window_days']=='60'
        if row['id']=='wulong_cluster_v1': assert result['signal'],result
        if row['id']=='b1_mtf_v1': assert not result['signal'] and not result['draft_eligible']


def test_tdx_and_store_candidates_retain_distinct_ma_and_volume_paths():
    bars=[]
    for index in range(270):
        day=(date(2024,1,1)+timedelta(days=index)).isoformat()
        bars.append({'event_date':day,'open':'10','high':'10.1','low':'9.9','close':'10','volume':1000+index,'available_at':day+'T08:00:00+00:00'})
    dataset={'id':'a'*64,'symbol':'sh600000'}
    store=candidate(bars,dataset,STORE,bars[-1]['event_date'])
    tdx=candidate(bars,dataset,TDX,bars[-1]['event_date'])
    assert store['ma10_above_ma20_days']==19 and tdx['ma10_above_ma20_days']==0
    assert store['vol_slope20'] != tdx['vol_slope20']
    assert store['up_down_volume_ratio'] != tdx['up_down_volume_ratio']
    assert candidate(bars[:250],dataset,TDX,bars[249]['event_date']) is None


@pytest.mark.parametrize('count',[30,40,90,270])
def test_full_store_candidate_matches_original_candidate(count):
    from test_wulong_universe import _bars as original_bars
    bars=original_bars(count)
    actual=candidate(bars,{'id':'a'*64,'symbol':'sh600000'},STORE,bars[-1]['event_date'])
    old=_legacy_row(bars)['row']
    for key,value in actual.items():
        if key in old: assert plain(value)==old[key],key


def test_context_formatters_exact_original_pure_method_results(context_scenario):
    from trade_app.research.signal_context_formatters import SignalContextBuilder
    factory,path,first,_second,bars=context_scenario
    with factory() as session: profile=freeze_profile(session)
    metrics=candidate(bars,{'id':first,'symbol':'sh600000'},STORE,bars[-1]['event_date'])
    for row in context_catalog():
        if row['id']=='matrix_signal_v1': continue
        params=normalize_context_params(row['id'],{})
        result=evaluate_context(row['id'],symbol='sh600000',bars=bars,params=params,profile=profile,context=STORE,frozen_candidate=metrics)
        snapshot=result['indicator']
        def legacy():
            from app.store import InMemoryStore
            # __new__ does not initialize the old data store or touch user files.
            return InMemoryStore.__new__(InMemoryStore)._build_strategy_signal_context(row['id'],snapshot,params)
        assert plain(SignalContextBuilder()._build_strategy_signal_context(row['id'],snapshot,params))==oracle(f"formatter:{row['id']}",legacy)


def test_insufficient_tdx_history_never_falls_back_to_store_ranking(context_scenario,monkeypatch):
    factory,path,first,_second,bars=context_scenario
    inline_child(monkeypatch)
    body={'dataset_ids':[first],'strategies':[{'strategy_id':sid,'params':{}} for sid in
        ('matrix_signal_v1','b1_mtf_v1','relative_strength_breakout_v1')],
        'strict':True,'as_of_date':bars[-1]['event_date'],'signal_context':TDX}
    job=enqueue(factory,path,body);done=finish(factory,path,job['id'])
    assert done['state']=='succeeded',done
    with factory() as session: report=prepare_report(session,path,{'scan_id':done['scan_id'],'as_of_date':body['as_of_date']})
    for row in report['result']['rows']:
        assert not row['signal'] and not row['draft_eligible']
        assert row['rank_score'] is None and row['rank_metrics'] is None
        assert row['rank_metric_source']=='legacy_tdx'


def test_full_context_real_worker_report_and_matrix_pool_identity(context_scenario):
    factory,path,first,second,bars=context_scenario
    body={'dataset_ids':[first,second],'strategies':[{'strategy_id':'relative_strength_breakout_v1','params':{}},
          {'strategy_id':'matrix_signal_v1','params':{}}],
          'strict':True,'as_of_date':bars[-1]['event_date'],'signal_context':STORE}
    with factory() as session:
        prepared=prepare_scan_job(session,path,body)
        alone=prepare_scan_job(session,path,{**body,'dataset_ids':[first]})
    matrix=[item for item in prepared['plan'] if item['strategy_id']=='matrix_signal_v1']
    assert [item['signal_pool']['evaluation']['ret40_rank'] for item in matrix]==[1,2]
    assert matrix[0]['signal_pool']['sha256'] != next(item['signal_pool']['sha256'] for item in alone['plan'] if item['strategy_id']=='matrix_signal_v1')
    job=enqueue(factory,path,body); done=finish(factory,path,job['id'])
    assert done['state']=='succeeded',done
    with factory() as session:
        scan=get_scan(session,done['scan_id'])
        report=prepare_report(session,path,{'scan_id':scan['id'],'as_of_date':body['as_of_date']})
    for row in report['result']['rows']:
        assert row['rank_score'] is not None
        assert row['health_score'] is not None and row['event_score'] is not None
        assert row['rank_metric_source']=='legacy_store'
    assert scan['request']['signal_context']==STORE


def test_future_tail_and_late_available_bar_never_enter_frozen_context(context_scenario):
    factory,path,first,_second,bars=context_scenario
    future=deepcopy(bars)
    future.append({**bars[-1],'event_date':'2025-03-30','available_at':'2025-03-30T08:00:00+00:00','close':'1','low':'1','open':'1','high':'1'})
    second=_import(factory,path,'600000',future)
    body={'dataset_ids':[first],'strategies':[{'strategy_id':'relative_strength_breakout_v1','params':{}}],
        'strict':True,'as_of_date':'2025-03-29','signal_context':STORE}
    results=[]
    for dataset_id in (first,second):
        with factory() as session: prepared=prepare_scan_job(session,path,{**body,'dataset_ids':[dataset_id]})
        payload={'attempt_id':'one','input_sha256':'test','start_index':0,'market_dir':str(path/'market'),
            'items':prepared['plan'],'strategies':prepared['strategies'],'strict':True,'event_profile':prepared['request']['event_profile'],'signal_context':STORE}
        results.append(compute_chunk(payload)['runs'][0]['result'])
    for result in results:
        result['candidate'].pop('dataset_id');result['ranking']['rank_metrics'].pop('dataset_id',None)
    assert results[0]==results[1]
    late=deepcopy(bars);late[-1]['available_at']='2025-03-30T08:00:00+00:00'
    late_id=_import(factory,path,'600000',late)
    with factory() as session: frozen=prepare_scan_job(session,path,{**body,'dataset_ids':[late_id]})
    assert frozen['plan'][0]['signal_candidate']['as_of_date']=='2025-03-28'


def test_full_market_skips_only_candidate_filter_not_generate_predicates(context_scenario):
    factory,path,first,_second,bars=context_scenario
    with factory() as session: profile=freeze_profile(session)
    metrics=candidate(bars,{'id':first,'symbol':'sh600000'},STORE,bars[-1]['event_date'])
    params=normalize_context_params('relative_strength_breakout_v1',{'min_vol_slope20':'.5','min_score':0,'health_score_min':0,'event_score_min':0,'min_event_count':0})
    def calculate(context,values):
        return evaluate_context('relative_strength_breakout_v1',symbol='sh600000',bars=bars,
            params=params,profile=profile,context=context,frozen_candidate=values)
    filtered=calculate(STORE,metrics)
    unfiltered=calculate({**STORE,'universe_mode':'full_market'},metrics)
    assert not filtered['signal'] and filtered['shape_signal']
    assert unfiltered['signal'] and unfiltered['draft_eligible']
    assert not unfiltered['universe']['passed'] and not unfiltered['universe']['applied']
    # A failed actual generate-signals predicate cannot be relabelled as a hit.
    failed=calculate({**STORE,'universe_mode':'full_market'},{**metrics,'ret40':0})
    assert not failed['signal'] and not failed['draft_eligible'] and not failed['shape_signal']


def test_tdx_original_candidate_values_and_wulong_filter_difference():
    from test_wulong_universe import _bars as original_bars
    bars=original_bars(270,flat=True)
    for bar in bars: bar['available_at']=bar['event_date']+'T08:00:00+00:00';bar['amount']='1000000'
    actual=candidate(bars,{'id':'a'*64,'symbol':'sh600000'},TDX,bars[-1]['event_date'])
    series={'symbol':'sh600000','total_bars':len(bars),'dates':[bar['event_date'] for bar in bars],
        **{key:[float(bar[key]) for bar in bars] for key in ('open','high','low','close','volume','amount')}}
    keys=('ret40','amplitude20','retrace20','pullback_days','ma10_above_ma20_days','ma5_above_ma10_days','price_vs_ma20','vol_slope20','up_down_volume_ratio','pullback_volume_ratio','has_blowoff_top','has_upper_shadow_risk','ai_confidence')
    store=candidate(bars,{'id':'a'*64,'symbol':'sh600000'},STORE,bars[-1]['event_date'])
    params=normalize_context_params('wulong_cluster_v1',{'min_ret40':0})
    def legacy():
        from app.tdx_loader import _build_row
        from app.core.strategy_plugins import WulongClusterPlugin
        old=_build_row(series,return_window_days=40,float_shares=None,as_of_date=bars[-1]['event_date'])
        plugin=WulongClusterPlugin()
        return {'row':{key:getattr(old,key) for key in keys},
                'store_admitted':bool(plugin.build_universe(candidates=[SimpleNamespace(**store)],params=params,mode='signals')),
                'tdx_admitted':bool(plugin.build_universe(candidates=[SimpleNamespace(**actual)],params=params,mode='signals'))}
    old=oracle('tdx_candidate',legacy)
    for key in keys:
        assert plain(actual[key])==old['row'][key],key
    assert old['store_admitted']
    assert not old['tdx_admitted']


def test_b1_complete_context_positive_and_explicit_params_are_not_ignored(tmp_path):
    days=[];day=date(2020,1,1)
    while len(days)<940:
        if day.weekday()<5: days.append(day.isoformat())
        day+=timedelta(days=1)
    tail=[16.17,15.51,15.94,15.64,16.28,15.74,15.53,15.52,15.91]
    bars=[]
    for index,day in enumerate(days):
        close=tail[index-931] if index>=931 else round(10+index*.005+max(0,index-700)**2*.00002,2)
        bars.append({'event_date':day,'open':str(close),'high':str(close+.12),'low':str(close-.12),'close':str(close),
            'volume':300 if index==939 else 1000,'available_at':day+'T07:00:00+00:00'})
    engine,factory=open_database(tmp_path)
    try:
        with factory() as session: profile=freeze_profile(session)
        metrics=candidate(bars,{'id':'a'*64,'symbol':'sh600000'},TDX,days[-1])
        def calculate(params):
            return evaluate_context('b1_mtf_v1',symbol='sh600000',bars=bars,profile=profile,context=TDX,
                frozen_candidate=metrics,params=normalize_context_params('b1_mtf_v1',params))
        result=calculate({})
        assert result['signal'] and result['draft_eligible']
        assert result['indicator']['events']==['B1多周期']
        expected=_legacy_rank('b1_complete_context','b1_mtf_v1',SimpleNamespace(entry_quality_score=result['indicator']['entry_quality_score']),metrics,{},0)
        assert result['ranking']['rank_score']==pytest.approx(expected)
        rejected=calculate({'vol_ratio':'.1'})
        assert not rejected['signal'] and not rejected['draft_eligible']
    finally: engine.dispose()


def test_context_api_preview_parameter_profile_conflicts_and_worker_whitelist(tmp_path):
    from trade_app.main import create_app
    from test_matrix_pool_api import started_client,data,writer
    from trade_app.research.scan_job_service import process_one_scan_chunk
    app=create_app(tmp_path,auto_rebuild=False)
    root='/api/v1/research/signal-workspace'
    with started_client(app) as client:
        post=writer(client)
        dataset=data(post('/api/v1/market/datasets',{'symbol':'600000.SH','bars':_bars(wulong=True),'adjustment':'none'}))
        assert len(data(client.get(root+'/sources'))['signal_context_strategies'])==16
        body={'source':{'kind':'fixed','dataset_ids':[dataset['id']]},'scan':{'as_of_date':'2025-03-29','strict':True,
            'strategies':[{'strategy_id':'relative_strength_breakout_v1','params':{'min_score':0,'health_score_min':0,'event_score_min':0}}],'signal_context':STORE}}
        preview=data(post(root+'/scan-preview',body))
        assert preview['request']['event_profile']['sha256']
        changed=deepcopy(body);changed['scan']['signal_context']['window_days']=30
        assert post(root+'/scan-jobs',{**changed,'expected_preview_sha256':preview['input_sha256']}).status_code==409
        job=data(post(root+'/scan-jobs',{**body,'expected_preview_sha256':preview['input_sha256']}))
        assert process_one_scan_chunk(app.state.db_factory,tmp_path)
        done=data(client.get('/api/v1/research/scan-jobs/'+job['id']))
        assert done['state']=='succeeded',done
        report=data(post(root+'/preview',{'scan_id':done['scan_id'],'as_of_date':'2025-03-29'}))
        row=report['result']['rows'][0]
        assert row['signal'] and row['rank_score'] is not None
        assert row['rank_components']['weights']['health']==.25
    with pytest.raises(TradeError,match='未知字段'):
        compute_chunk({'items':[],'extra':'no'})
