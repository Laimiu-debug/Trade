import copy
import json
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from trade_app.legacy_import import supplements, service
from trade_app.legacy_import.models import LegacyImport
from trade_app.legacy_import.supplement_models import LegacySupplementItem, LegacySupplementBatch
from trade_app.analytics.service import process_one
from trade_app.api.settings_service import get_group, save_group
from trade_app.insights.models import InspirationCard
from trade_app.insights.service import create_card
from trade_app.reviews.round_models import RoundNote
from trade_app.trading.models import Trade
from trade_app.platform.types import TradeError
from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.db import open_database
from test_legacy_import import db, sample, body, save
from test_watch_pool import client_at


def fixture():
    value=sample()
    for trade in value['trades']:trade['code']='600000'
    value['flash_cards']=[{'id':1,'content':'原始交易纪律','tags':'纪律,复盘,纪律'}, {'id':2,'content':'原始交易纪律','tags':'纪律,复盘'}]
    value['settings'] += [{'key':key,'value':val} for key,val in {
        'commission_rate':'0.0004','commission_min':'6','stamp_tax_rate':'0.0005','transfer_fee_rate':'0.00002',
        'wave_pct':'40','node_count':'10','market_priority':'tdx,akshare,web',
        'ai_score_base_url':'https://example.invalid/v1','ai_score_text_model':'old-model'}.items()]
    return value


def selection(revision=1,keys=None):
    return {'expected_revision':revision,'selected_keys':keys if keys is not None else ['card:1','card:2','round:9','setting:fees','setting:targets','setting:ai'], 'secret_refs':{'text':'TRADE_AI_TEXT_KEY','vision':'TRADE_AI_VISION_KEY'}}


def confirm(session,identity,request):
    value=supplements.preview(session,identity,request)
    return supplements.apply(session,identity,{**request,'expected_preview_sha256':value['preview_sha256'],'acknowledge_limitations':True})


def setup(factory,payload=None,mode='new_real_account'):
    with factory.begin() as session: imported=save(session,body(payload or fixture(),mode=mode))
    while process_one(factory):pass
    return imported


def test_explicit_card_settings_round_promotion_no_raw_secret_and_backup(db,tmp_path):
    path,factory=db
    imported=setup(factory)
    with factory.begin() as session:
        before=service.export_archive(session,imported['id'])
        preview=supplements.preview(session,imported['id'],selection())
        assert preview['can_apply']
        assert session.scalar(select(func.count()).select_from(InspirationCard)) == 0
        assert next(item for item in preview['items'] if item['key']=='setting:market_sources')['status']=='blocked'
        result=confirm(session,imported['id'],selection())
        assert result['revision']==2 and len(result['targets'])==6
        assert session.scalar(select(func.count()).select_from(InspirationCard))==1
        assert session.scalar(select(RoundNote)).summary=='旧回合'
        assert get_group(session,'fees',imported['account_id'])['value']['minimum_commission']=='6.00'
        assert get_group(session,'targets',imported['account_id'])['value']['multiplier']=='1.4'
        assert get_group(session,'ai')['value']['text']['secret_ref']=='TRADE_AI_TEXT_KEY'
        assert service.export_archive(session,imported['id'])['logical_data']==before['logical_data']
        again=supplements.apply(session,imported['id'],{**selection(),'expected_preview_sha256':preview['preview_sha256'],'acknowledge_limitations':True})
        assert again==result
        assert session.scalar(select(func.count()).select_from(LegacySupplementBatch))==1
        assert len(supplements.history(session,imported['id']))==1
        assert 'never-persist-this-secret' not in json.dumps(service.export_archive(session,imported['id']))
    restored=tmp_path/'restored'
    restore_to_new_directory(create_backup(path),restored)
    engine,other=open_database(restored)
    try:
        with other() as session:
            assert len(supplements.history(session,imported['id']))==1
            assert session.scalar(select(func.count()).select_from(LegacySupplementItem))==6
            assert session.scalar(select(RoundNote)).summary=='旧回合'
    finally:engine.dispose()


def test_selected_target_changes_reject_entire_batch_before_any_card_write(db):
    _path,factory=db
    imported=setup(factory)
    request=selection(keys=['card:1','setting:fees'])
    with factory() as session:preview=supplements.preview(session,imported['id'],request)
    with factory.begin() as session:
        current=get_group(session,'fees',imported['account_id'])
        save_group(session,'fees',imported['account_id'],current['revision'],{**current['value'],'minimum_commission':'7'})
    with factory.begin() as session,pytest.raises(TradeError) as stale:
        supplements.apply(session,imported['id'],{**request,'expected_preview_sha256':preview['preview_sha256'],'acknowledge_limitations':True})
    assert stale.value.code=='LEGACY_PREVIEW_CHANGED'
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(InspirationCard))==0
        assert session.get(LegacyImport,imported['id']).revision==1


@pytest.mark.parametrize('problem',['aliases','two_rounds','edited'])
def test_round_mapping_requires_exact_original_and_target_membership(db,problem):
    _path,factory=db
    payload=fixture()
    if problem=='aliases':payload['trades'][0]['code']='600000.SH'
    if problem=='two_rounds':
        payload['trades'] += [{**payload['trades'][1],'id':3},{**payload['trades'][0],'id':4}]
    imported=setup(factory,payload)
    if problem=='edited':
        with factory.begin() as session:session.scalar(select(Trade).where(Trade.account_id==imported['account_id'])).revision=2
    with factory() as session:
        value=supplements.preview(session,imported['id'],selection(keys=['round:9']))
        assert not value['can_apply'] and value['selected'][0]['status']=='blocked'
        assert session.scalar(select(func.count()).select_from(RoundNote))==0


def test_archive_only_cards_and_identical_existing_reuse(db):
    _path,factory=db
    imported=setup(factory,mode='archive_only')
    with factory.begin() as session:
        existing=create_card(session,{'content':'原始交易纪律','tags':['纪律','复盘']})
        session.flush()
        result=confirm(session,imported['id'],selection(keys=['card:1']))
        assert result['targets'][0]['target']['id']==existing['id']
        assert result['targets'][0]['target']['reused']
        value=supplements.inspect(session,imported['id'])
        assert next(item for item in value['items'] if item['key']=='setting:fees')['status']=='blocked'
        assert session.get(LegacyImport,imported['id']).account_id is None


def test_missing_or_pasted_ai_reference_never_saved(db):
    _path,factory=db
    imported=setup(factory)
    with factory() as session:
        value=supplements.inspect(session,imported['id'])
        assert next(item for item in value['items'] if item['key']=='setting:ai')['status']=='blocked'
        with pytest.raises(TradeError):supplements.inspect(session,imported['id'],secret_refs={'text':'sk-actual-value'})
        assert get_group(session,'ai')['revision']==0


def test_bad_archive_auxiliary_rows_are_blocked_not_crash(db):
    _path,factory=db
    payload=fixture();payload['flash_cards']=[None,{'id':1,'content':None,'tags':3}];payload['round_reviews']=[{'id':1,'code':[],'start_date':{}}];payload['settings']=[{'key':'wave_pct','value':'garbage'}]
    imported=setup(factory,payload,mode='archive_only')
    with factory() as session:
        value=supplements.inspect(session,imported['id'])
        assert all(item['status']=='blocked' for item in value['items'])


def test_api_preview_no_write_csrf_replay_source_revision(tmp_path):
    with client_at(tmp_path) as (app,client):
        token=client.get('/api/v1/session').json()['data']['csrf_token']
        with app.state.db_factory.begin() as session:imported=save(session,body(fixture(),mode='archive_only'))
        prefix=f'/api/v1/legacy-imports/{imported["id"]}/supplements'
        request=selection(keys=['card:1'])
        value=client.post(prefix+'/preview',json=request,headers={'X-CSRF-Token':token}).json()['data']
        assert value['can_apply']
        confirm_body={**request,'expected_preview_sha256':value['preview_sha256'],'acknowledge_limitations':True}
        assert client.post(prefix+'/apply',json=confirm_body).status_code==403
        headers={'X-CSRF-Token':token,'Idempotency-Key':str(uuid4())}
        first=client.post(prefix+'/apply',json=confirm_body,headers=headers)
        assert first.status_code==200,first.text
        headers['Idempotency-Key']=str(uuid4())
        assert client.post(prefix+'/apply',json=confirm_body,headers=headers).json()==first.json()
        assert client.post(prefix+'/preview',json=request,headers={'X-CSRF-Token':token}).status_code==409
        assert len(client.get(prefix+'/history').json()['data'])==1


def test_print_preferences_are_explicit_and_do_not_create_directory(db,tmp_path):
    _,factory=db
    destination=tmp_path/'not-created-by-preference'
    payload=fixture()
    payload['settings'] += [{'key':'pdf_username','value':'原作者'}, {'key':'pdf_export_dir','value':str(destination)}]
    imported=setup(factory,payload)
    with factory.begin() as session:
        request=selection(keys=['setting:print'])
        preview=supplements.preview(session,imported['id'],request)
        assert preview['can_apply'] and get_group(session,'print')['revision']==0
        confirm(session,imported['id'],request)
        assert get_group(session,'print')['value']=={'author':'原作者','export_directory':str(destination)}
    assert not destination.exists()
