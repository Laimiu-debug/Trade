import io
import json
from uuid import uuid4

from PIL import Image

from trade_app.ai import service
from trade_app.platform.backup import restore_to_new_directory
from trade_app.reviews.scores import DIMENSIONS
from test_ai_preset_api import consume
from test_wyckoff_research_api import client_for, data, write


def prepared(client, body, scope):
    preview = data(write(client, '/ai/generations/preview' + scope, body))
    body = {**body, 'expected_input_sha256': preview['input_sha256']}
    generation = data(write(client, '/ai/generations' + scope, body))
    consume(client, generation['run_id'], scope)
    return data(write(client, '/ai/generations/' + generation['id'] + '/finalize' + scope,
                      {'expected_revision': generation['revision']}))


def test_review_draft_http_accept_and_retract_preserve_other_human_fields(tmp_path, monkeypatch):
    def provider(*_args, **_kwargs):
        yield {'type': 'delta', 'content': json.dumps({'sections': {'reflection': 'AI 草稿中的反思'}})}
        yield {'type': 'end'}

    monkeypatch.setattr(service, 'stream_chat', provider)
    with client_for(tmp_path) as client:
        account = data(write(client, '/accounts', {'name': '复盘草稿验收'}))
        scope = '?account_id=' + account['id']
        review_path = '/accounts/' + account['id'] + '/daily-reviews/2026-01-05'
        data(write(client, review_path, {'expected_revision': 0, 'title': '人工标题',
                                        'reflection': '原始人工反思', 'market_observation': '保留的市场观察'}, 'PUT'))
        draft = prepared(client, {'kind': 'review_draft', 'config_revision': 0,
                        'target': {'type': 'daily', 'key': '2026-01-05', 'fields': ['reflection']}}, scope)
        assert draft['status'] == 'draft'
        assert data(client.get('/api/v1' + review_path))['reflection'] == '原始人工反思'
        path = '/ai/generations/' + draft['id']
        assert client.get('/api/v1' + path).status_code == 404
        assert write(client, path + '/accept' + scope,
                     {'expected_revision': draft['revision'], 'expected_target_revision': 0}).status_code == 409
        accepted = data(write(client, path + '/accept' + scope,
                              {'expected_revision': draft['revision'], 'expected_target_revision': 1}))
        current = data(client.get('/api/v1' + review_path))
        assert current['reflection'] == 'AI 草稿中的反思'
        assert current['title'] == '人工标题' and current['market_observation'] == '保留的市场观察'
        retracted = data(write(client, path + '/retract' + scope, {'expected_revision': accepted['revision']}))
        assert retracted['status'] == 'retracted'
        assert data(client.get('/api/v1' + review_path))['reflection'] == '原始人工反思'
        audit = data(client.get('/api/v1' + path + '/audit' + scope))
        assert {item['action'] for item in audit} >= {'prepare', 'finalize', 'accept', 'retract'}


def test_ocr_http_sends_selected_image_only_requires_pending_confirmation_and_backup(tmp_path, monkeypatch):
    images = []

    def provider(_config, channel, messages, **_):
        assert channel == 'vision'
        images.extend(part for part in messages[-1]['content'] if part['type'] == 'image_url')
        yield {'type': 'delta', 'content': json.dumps({'trades': [{'trade_date': '2026-01-05',
            'symbol': '000001', 'name': '验收证券', 'side': 'buy', 'quantity': 100, 'price': '10', 'fee': '5'}], 'warnings': []})}
        yield {'type': 'end'}

    monkeypatch.setattr(service, 'stream_chat', provider)
    root = tmp_path / 'original'
    with client_for(root) as client:
        account = data(write(client, '/accounts', {'name': 'OCR 待确认'}))
        scope = '?account_id=' + account['id']
        account_path = '/accounts/' + account['id']
        image = io.BytesIO()
        Image.new('RGB', (10, 10), 'white').save(image, format='PNG')
        attachment = data(client.post('/api/v1' + account_path + '/daily-reviews/2026-01-05/attachments',
                          files={'file': ('screenshot.png', image.getvalue(), 'image/png')},
                          headers={'Idempotency-Key': str(uuid4())}))
        draft = prepared(client, {'kind': 'ocr_trades', 'config_revision': 0,
                                  'target': {'attachment_ids': [attachment['id']]}}, scope)
        assert len(images) == 1 and images[0]['image_url']['url'].startswith('data:image/png;base64,')
        assert 'base64' not in json.dumps(draft)
        assert data(client.get('/api/v1' + account_path + '/pending-trades')) == []
        accepted = data(write(client, '/ai/generations/' + draft['id'] + '/accept' + scope,
                              {'expected_revision': draft['revision']}))
        pending = data(client.get('/api/v1' + account_path + '/pending-trades'))
        assert len(pending) == 1 and pending[0]['symbol'] == '000001'
        assert data(client.get('/api/v1' + account_path + '/trades')) == []
        data(write(client, account_path + '/pending-trades/' + pending[0]['id'] + '/confirm',
                   {'expected_revision': pending[0]['revision']}))
        assert len(data(client.get('/api/v1' + account_path + '/trades'))) == 1

        assert write(client, '/ai/generations/' + draft['id'] + '/retract' + scope,
                     {'expected_revision': accepted['revision']}).status_code == 409
        backup = client.get('/api/v1/backups/export').content
    restored = tmp_path / 'restored'
    restore_to_new_directory(backup, restored)
    with client_for(restored) as client:
        assert data(client.get('/api/v1/ai/generations/' + draft['id'] + scope))['status'] == 'accepted'
        assert client.get('/api/v1' + account_path + '/review-attachments/' + attachment['id'] + '/content').content == image.getvalue()
        assert len(data(client.get('/api/v1' + account_path + '/trades'))) == 1


def test_score_api_ai_acceptance_preserves_final_and_copy_selected_zero_is_explicit(tmp_path, monkeypatch):
    def provider(*_args, **_kwargs):
        yield {'type': 'delta', 'content': json.dumps({'summary': '低证据示例', 'subjects': [{
            'subject_id': '2026-01-05', 'scope': 'daily', 'trade_ids': [], 'comment': '建议说明',
            'scores': {key: {'score': 0, 'comment': '待人工核对'} for key in DIMENSIONS['daily']}}]})}
        yield {'type': 'end'}

    monkeypatch.setattr(service, 'stream_chat', provider)
    with client_for(tmp_path) as client:
        account = data(write(client, '/accounts', {'name': '建议评分隔离'}))
        scope = '?account_id=' + account['id']
        path = '/accounts/' + account['id'] + '/daily-reviews/2026-01-05/scores'
        sheet = data(write(client, path, {'scope': 'daily', 'trade_ids': [], 'expected_revision': 0,
                'scores': {key: {'final': 9, 'comment': '人工判断'} for key in DIMENSIONS['daily']}, 'comment': '原始总评'}, 'PUT'))
        draft = prepared(client, {'kind': 'review_scores', 'config_revision': 0,
                                  'target': {'scope': 'daily', 'key': '2026-01-05', 'trade_ids': []}}, scope)
        data(write(client, '/ai/generations/' + draft['id'] + '/accept' + scope, {'expected_revision': draft['revision']}))
        sheet = data(client.get('/api/v1' + path))[0]
        assert all(entry['final'] == 9 and entry['ai'] == 0 for entry in sheet['scores'].values())
        copy_path = '/accounts/' + account['id'] + '/review-scores/' + sheet['id'] + '/copy-ai-to-final'
        copied = data(write(client, copy_path, {'expected_revision': sheet['revision'], 'dimensions': ['position']}))
        assert copied['scores']['position']['final'] == 0
        assert copied['scores']['position']['final_source'] == 'ai_accepted'
        assert copied['scores']['emotion']['final'] == 9 and copied['comment'] == '原始总评'
        # A manual zero score is also a real value; AI provenance survives that later edit.
        edited = data(write(client, path, {'scope': 'daily', 'trade_ids': [], 'expected_revision': copied['revision'],
                  'scores': {'position': {'final': 0, 'comment': '手工零分'}}, 'comment': '手工总评'}, 'PUT'))
        assert edited['scores']['position']['final'] == 0 and edited['scores']['position']['ai_generation_id'] == draft['id']
