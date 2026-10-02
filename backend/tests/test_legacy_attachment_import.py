import base64
import io
import json
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select, func

from trade_app.legacy_import import service, attachment_service as images
from trade_app.platform.types import TradeError
from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.db import open_database
from trade_app.reviews.models import DailyReview
from trade_app.reviews.attachment_models import ReviewAttachment
from trade_app.reviews.attachments import get_attachment, add_attachment
from test_legacy_import import db, body, sample, save
from test_watch_pool import client_at


def image_bytes(color='red'):
    output=io.BytesIO();Image.new('RGB',(4,3),color).save(output,format='PNG');return output.getvalue()


def request(revision=1,content=None):
    return {'expected_revision':revision,'files':[{'source_key':'attachment:1:0','filename':'selected.png',
             'content_base64':base64.b64encode(content or image_bytes()).decode()}]}


def setup(factory):
    with factory.begin() as session:return save(session,body())


def test_explicit_upload_preview_apply_restore_without_opening_legacy_path(db,tmp_path):
    path,factory=db;imported=setup(factory);body_=request();decoded=images.decode(body_['files'])
    with factory.begin() as session:
        catalog=images.catalog(session,imported['id'])
        assert catalog['items'][0]['source_path']=='D:/private/chart.png'
        assert catalog['items'][0]['status']=='needs_file'
        frozen=images.preview(session,imported['id'],body_,decoded)
        assert not (path/'attachments').exists()
        before=session.scalar(select(DailyReview)).market_observation
        confirm={**body_,'expected_preview_sha256':frozen['preview_sha256'],'acknowledge_limitations':True}
        result=images.apply(session,path,imported['id'],confirm,decoded)
        assert images.apply(session,path,imported['id'],confirm,decoded)==result
        target=result['targets'][0]['target']
        assert get_attachment(session,path,imported['account_id'],target['id'])[1]==image_bytes()
        assert session.scalar(select(DailyReview)).market_observation==before
        assert images.catalog(session,imported['id'])['items'][0]['status']=='mapped'
        assert service.export_archive(session,imported['id'])['supplement_mappings'][0]['result']==result
    restore_to_new_directory(create_backup(path),tmp_path/'restored')
    engine,other=open_database(tmp_path/'restored')
    try:
        with other() as session:
            assert get_attachment(session,tmp_path/'restored',imported['account_id'],target['id'])[1]==image_bytes()
    finally:engine.dispose()


@pytest.mark.parametrize('change',['review','image','content','source','revision'])
def test_frozen_source_target_and_content_changes_reject(db,change):
    path,factory=db;imported=setup(factory);body_=request();decoded=images.decode(body_['files'])
    with factory.begin() as session:
        frozen=images.preview(session,imported['id'],body_,decoded)
        if change=='review':session.scalar(select(DailyReview)).revision+=1
        if change=='image':add_attachment(session,path,imported['account_id'],'2025-01-02',image_bytes('blue'),'manual.png')
        if change=='content':decoded=images.decode(request(content=image_bytes('green'))['files'])
        if change=='source':decoded[0]['source_key']='attachment:other:0'
        if change=='revision':body_['expected_revision']=2
        session.flush()
        with pytest.raises(TradeError):images.apply(session,path,imported['id'],{**body_,'expected_preview_sha256':frozen['preview_sha256'],'acknowledge_limitations':True},decoded)
        assert session.scalar(select(func.count()).select_from(ReviewAttachment))==(1 if change=='image' else 0)


def test_malformed_images_unknown_paths_and_missing_account_never_scanned(db):
    _,factory=db
    with factory.begin() as session:
        imported=save(session,body(mode='archive_only',account_name=''))
        assert not images.catalog(session,imported['id'])['items']
    for changed in ({'filename':'C:\\private\\x.png'},{'content_base64':base64.b64encode(b'notimage').decode()},{'content_base64':'bad'}):
        with pytest.raises(TradeError):images.decode([{**request()['files'][0],**changed}])
    with pytest.raises(TradeError):images.decode(request()['files']*2)


def test_same_day_equal_content_reused_not_duplicate(db):
    path,factory=db;imported=setup(factory);body_=request();decoded=images.decode(body_['files'])
    with factory.begin() as session:
        existing=add_attachment(session,path,imported['account_id'],'2025-01-02',image_bytes(),'existing.png');session.flush()
        frozen=images.preview(session,imported['id'],body_,decoded)
        result=images.apply(session,path,imported['id'],{**body_,'expected_preview_sha256':frozen['preview_sha256'],'acknowledge_limitations':True},decoded)
        assert result['targets'][0]['target']['id']==existing['id'] and result['targets'][0]['target']['reused']
        assert session.scalar(select(func.count()).select_from(ReviewAttachment))==1


def test_routes_require_csrf_and_do_not_store_uploaded_binary(db):
    path,factory=db;imported=setup(factory)
    with client_at(path) as (_app, client):
        csrf=client.get('/api/v1/session').json()['data']['csrf_token']
        def post(route,data):return client.post('/api/v1/legacy-imports/'+imported['id']+route,json=data,headers={'X-CSRF-Token':csrf,'Idempotency-Key':uuid4().hex})
        value=post('/attachments/preview',request());assert value.status_code==200,value.text
        frozen=value.json()['data'];body_={**request(),'expected_preview_sha256':frozen['preview_sha256'],'acknowledge_limitations':True}
        assert client.post('/api/v1/legacy-imports/'+imported['id']+'/attachments/apply',json=body_).status_code==403
        result=post('/attachments/apply',body_);assert result.status_code==200,result.text
        assert 'content_base64' not in json.dumps(client.get('/api/v1/legacy-imports/'+imported['id']+'/export.json').json())
