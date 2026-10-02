import hashlib
from uuid import uuid4
import pytest
from trade_app.ai import context_sources, quick_prompts
from trade_app.ai.config import encode
from trade_app.ai.contexts import freeze_context
from trade_app.market.service import import_dataset
from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError
from trade_app.research.backtest_models import BacktestRun
from trade_app.research.valuation_models import ValuationRun
from test_watch_pool import client_at
from test_ai_workspace import scenario, _request
from trade_app.ai.service import preview_prompt, prepare_run


@pytest.fixture
def db(tmp_path):
    engine, factory = open_database(tmp_path)
    try:
        yield tmp_path, factory
    finally:
        engine.dispose()


def prompt(**changes):
    return {'id': 'a'*32, 'page':'market', 'label':'检查日期', 'prompt':'只检查证据', 'pinned':False, **changes}


def test_prompt_order_versions_history_and_backup(db, tmp_path):
    path, factory = db
    with factory.begin() as session:
        first = quick_prompts.save_prompts(session, 0, [prompt(), prompt(id='b'*32, pinned=True)])
        ordered = quick_prompts.save_prompts(session, 1, list(reversed(first['items'])))
        assert ordered['items'][0]['id'] == 'b'*32
        quick_prompts.save_prompts(session, 2, [])
    with factory.begin() as session, pytest.raises(TradeError):
        quick_prompts.save_prompts(session, 0, [prompt()])
    restored_path = tmp_path.parent / (tmp_path.name + '-restored')
    restore_to_new_directory(create_backup(path), restored_path)
    engine, restored = open_database(restored_path)
    try:
        with restored() as session:
            assert quick_prompts.get_prompts(session)['revision'] == 3
            assert quick_prompts.get_prompts(session)['items'] == []
            assert len(quick_prompts.history(session)) == 3
    finally:
        engine.dispose()


@pytest.mark.parametrize('items', [[prompt(id='system:evidence')], [prompt(),prompt()], [prompt(pinned=1)], [prompt(page='unknown')], [prompt(prompt=' ')], [prompt(extra='no')]])
def test_quick_validation(items):
    with pytest.raises(TradeError):
        quick_prompts.normalize(items)


def seed(session, path):
    dataset = import_dataset(session, path, {'symbol':'sh600000','adjustment':'none','bars':[{'event_date':f'2025-01-0{day}', 'open':'10', 'high':'11','low':'9','close':'10','volume':1000,'available_at':f'2025-01-0{day}T08:00:00+00:00'} for day in (1,2)]})
    payload = encode({'total_return':'.1','equity':[{'date':'2025-01-02','total_assets':str(10000+i)} for i in range(15)],'trades':[{'index':i} for i in range(25)],'decisions':[{}]})
    session.add(BacktestRun(id='backtest',dataset_id=dataset['id'],strategy_id='fixture',strategy_version='v1',execution_version='v1',calculation_version='v1',code_sha256='f'*64,params_json='{}',config_json='{}',state='succeeded',cancel_requested=0,result_json=payload,result_sha256=hashlib.sha256(payload.encode()).hexdigest(),error=None,created_at='2026-01-01T00:00:00+00:00',updated_at='2026-01-01T00:00:00+00:00',attempt_number=1))
    session.add(ValuationRun(id='valuation',request_json=encode({'symbol':'sh600000','growth':10}),result_json=encode({'symbol':'sh600000','assumption':'manual'}),code_sha256='b'*64,created_at='2025-01-03T00:00:00+00:00'))
    session.flush()


def test_backtest_freeze_cutoff_corruption_and_bounded_evidence(db):
    path, factory = db
    with factory.begin() as session:
        seed(session,path)
        raw={'artifact_sources':[{'type':'backtest','id':'backtest'}],'decision_at':'2025-01-03T00:00:00+08:00'}
        frozen=freeze_context(session,path,raw,account_id=None)
        value=frozen['research_artifacts'][0]
        assert value['trades']['omitted_count'] == 5 and len(value['trades']['items']) == 20
        assert value['equity']['omitted_count'] == 5 and value['omitted_decision_count'] == 1
        assert value['historical_selection_knowledge_verified'] is False
        assert frozen['sources'][0]['frozen_source_sha256'] == value['frozen_source_sha256']
        assert value == freeze_context(session,path,raw,account_id=None)['research_artifacts'][0]
        with pytest.raises(TradeError) as early:
            freeze_context(session,path,{**raw,'decision_at':'2025-01-02T16:00:00+08:00'},account_id=None)
        assert early.value.code == 'AI_SOURCE_AFTER_CUTOFF'
        session.get(BacktestRun,'backtest').result_json='{}'
        with pytest.raises(TradeError) as corrupt:
            freeze_context(session,path,raw,account_id=None)
        assert corrupt.value.code == 'AI_SOURCE_CORRUPT'


def test_valuation_assumptions_creation_time_and_missing(db):
    path, factory = db
    with factory.begin() as session:
        seed(session,path)
        refs=[{'type':'valuation','id':'valuation'}]
        with pytest.raises(TradeError) as early:
            context_sources.freeze_sources(session,refs,'2025-01-02T16:00:00+00:00')
        assert early.value.code == 'AI_SOURCE_AFTER_CUTOFF'
        value=context_sources.freeze_sources(session,refs,'2025-01-03T00:00:00+00:00')[0]
        assert value['availability'].startswith('record_creation_only') and value['source_date'] is None
        assert len(context_sources.catalog(session)) == 2


def test_portfolio_adapter_keeps_versions_and_end_of_day(db,monkeypatch):
    _path,factory=db
    item={'id':'p','name':'组合','mode':'raw','strategy_id':'S1','state':'succeeded','input_sha256':'a','code_sha256':'b','result_sha256':'c','summary':{'total_return':'.2'},'limitations':['fixed sample'],'created_at':'2026-01-01','updated_at':'2026-01-02','frozen_context':{'end_date':'2025-01-02','config':{'execution_version':'v2'}},'result':{'trades':[],'equity':[],'decisions':[],'pool_history':[{}]}}
    monkeypatch.setattr(context_sources,'get_portfolio',lambda session,run_id,full:item)
    with factory() as session:
        refs=[{'type':'portfolio','id':'p'}]
        value=context_sources.freeze_sources(session,refs,'2025-01-03T00:00:00+08:00')[0]
        assert value['frozen_context']['config']['execution_version'] == 'v2' and value['omitted_pool_day_count'] == 1
        with pytest.raises(TradeError):
            context_sources.freeze_sources(session,refs,'2025-01-02T23:59:00+08:00')
        item['state']='running'
        with pytest.raises(TradeError) as pending:
            context_sources.freeze_sources(session,refs,'2025-01-03T00:00:00+08:00')
        assert pending.value.code == 'AI_SOURCE_NOT_COMPLETE'


@pytest.mark.parametrize('refs', [[{'type':[],'id':'x'}], [{'type':'valuation','id':''}], [{'type':'valuation','id':'a','extra':1}], [{'type':'valuation','id':'a'}]*4])
def test_source_validation(db,refs):
    _path,factory=db
    with factory() as session,pytest.raises(TradeError):
        context_sources.freeze_sources(session,refs,'2025-01-03T00:00:00+00:00')


def test_api_csrf_idempotency_stale_revision(tmp_path):
    with client_at(tmp_path) as (_app,client):
        token=client.get('/api/v1/session').json()['data']['csrf_token']
        body={'expected_revision':0,'items':[prompt()]}
        assert client.put('/api/v1/ai/quick-prompts',json=body).status_code == 403
        headers={'X-CSRF-Token':token,'Idempotency-Key':str(uuid4())}
        first=client.put('/api/v1/ai/quick-prompts',json=body,headers=headers)
        assert first.status_code == 200, first.text
        assert client.put('/api/v1/ai/quick-prompts',json=body,headers=headers).json() == first.json()
        headers['Idempotency-Key']=str(uuid4())
        assert client.put('/api/v1/ai/quick-prompts',json=body,headers=headers).status_code == 409
        assert len(client.get('/api/v1/ai/quick-prompts/history').json()['data']) == 1
        assert client.get('/api/v1/ai/context-sources').json()['data'] == []


def test_preview_hash_catches_changed_selected_artifact_without_provider_call(scenario):
    factory, path, chat = scenario
    with factory.begin() as session:
        seed(session, path)
        body = _request(context={'artifact_sources':[{'type':'valuation','id':'valuation'}], 'decision_at':'2025-01-04T00:00:00+00:00'})
        preview = preview_prompt(session, path, chat['id'], body)
        session.get(ValuationRun, 'valuation').result_json = encode({'symbol':'sh600000','assumption':'changed'})
        with pytest.raises(TradeError) as stale:
            prepare_run(session, path, chat['id'], {**body, 'expected_input_sha256':preview['input_sha256']})
        assert stale.value.code == 'AI_CONTEXT_CHANGED'

