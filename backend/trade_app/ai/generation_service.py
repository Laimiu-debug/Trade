"""Structured AI drafts require separate validation and explicit business acceptance."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from trade_app.ai.config import encode
from trade_app.ai.contexts import BASE_SYSTEM_PROMPT, freeze_context, text_field
from trade_app.ai.generation_models import AIGeneration, AIGenerationAudit
from trade_app.ai.generation_schemas import DAILY_FIELDS, REHEARSAL_FIELDS, day, normalize_output, output_prompt, strict_json
from trade_app.ai.models import AICall
from trade_app.ai.provider import MAX_REQUEST_BYTES
from trade_app.ai.service import _frozen_config, _new_call, _revision
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.reviews.attachments import get_attachment
from trade_app.reviews.ai_scores import apply_score_suggestions, freeze_score_targets, retract_score_suggestions
from trade_app.reviews.periods import FIELDS as PERIOD_FIELDS, bounds, get_period, save_period
from trade_app.reviews.rounds import get_round_note, save_round_note
from trade_app.reviews.service import get_review, save_review
from trade_app.trading.models import PendingTrade
from trade_app.trading.pending import create_pending_trade, discard_pending_trade
from trade_app.trading.service import account_or_error, list_snapshots, save_snapshot


KINDS = ('stock_analysis', 'review_draft', 'ocr_trades', 'ocr_assets', 'review_scores')


def _record(session, generation_id: str, account_id: str | None, include_deleted=False) -> AIGeneration:
    row = session.get(AIGeneration, generation_id)
    if row is None or row.account_id != account_id or (row.deleted and not include_deleted):
        raise TradeError('AI_GENERATION_NOT_FOUND', 'AI 生成记录不存在于当前账户范围', 404)
    if account_id is not None:
        account_or_error(session, account_id)
    return row


def _view(row: AIGeneration, detail=True) -> dict:
    source = json.loads(row.source_json)
    return {'id': row.id, 'run_id': row.run_id, 'account_id': row.account_id, 'kind': row.kind,
            'status': row.status, 'revision': row.revision, 'target': source['target'],
            'source': source if detail else {'target': source['target']}, 'input_sha256': row.input_sha256,
            'output': json.loads(row.output_json) if row.output_json and detail else None,
            'validation_errors': json.loads(row.errors_json),
            'acceptance': json.loads(row.acceptance_json) if row.acceptance_json and detail else None,
            'deleted': bool(row.deleted), 'created_at': row.created_at, 'updated_at': row.updated_at}


def _audit(session, row: AIGeneration, action: str) -> None:
    session.add(AIGenerationAudit(id=new_id(), generation_id=row.id, revision=row.revision,
                                  action=action, snapshot_json=encode(_view(row)), created_at=utc_now()))


def _change(session, row: AIGeneration, action: str) -> dict:
    row.revision, row.updated_at = row.revision + 1, utc_now()
    session.flush()
    _audit(session, row, action)
    return _view(row)


def _only(body: dict, fields: set) -> None:
    if not isinstance(body, dict) or set(body) - fields:
        raise TradeError('INVALID_AI_GENERATION_REQUEST', '生成请求含不支持的字段')


def _review_current(session, account_id: str, target: dict) -> dict:
    kind, key = target['type'], target['key']
    if kind in ('daily', 'rehearsal'):
        return get_review(session, account_id, key) or {'revision': 0}
    if kind in ('weekly', 'monthly'):
        return get_period(session, account_id, kind, key)
    return get_round_note(session, account_id, key)


def _review_target(session, account_id: str, raw: dict) -> tuple[dict, dict, str | None]:
    _only(raw, {'type', 'key', 'fields'})
    kind, key = raw.get('type'), raw.get('key')
    if kind in ('daily', 'rehearsal'):
        day(key)
        allowed = set(REHEARSAL_FIELDS if kind == 'rehearsal' else DAILY_FIELDS)
        defaults = list(REHEARSAL_FIELDS) if kind == 'rehearsal' else ['overall_summary', 'reflection']
        as_of = key
    elif kind in ('weekly', 'monthly'):
        _, as_of = bounds(kind, key)
        allowed = PERIOD_FIELDS[kind]
        defaults = ['key_insight', 'next_week_strategy'] if kind == 'weekly' else ['summary', 'next_goal']
    elif kind == 'round':
        if not isinstance(key, str) or not key or len(key) > 128:
            raise TradeError('INVALID_AI_GENERATION_TARGET', '回合 ID 无效')
        allowed, defaults, as_of = {'summary'}, ['summary'], None
    else:
        raise TradeError('INVALID_AI_GENERATION_TARGET', '复盘目标类型无效')
    fields = raw.get('fields', defaults)
    if (not isinstance(fields, list) or not fields or any(not isinstance(item, str) for item in fields)
            or len(fields) != len(set(fields)) or set(fields) - allowed):
        raise TradeError('INVALID_AI_GENERATION_FIELDS', '须明确选择目标支持的复盘栏目')
    target = {'type': kind, 'key': key, 'fields': sorted(fields)}
    before = _review_current(session, account_id, target)
    if kind == 'round':
        if before['projection_status'] != 'fresh' or not before['round_exists']:
            raise TradeError('PROJECTION_NOT_FRESH', '回合统计须为最新才能生成摘要', 409)
        as_of = before['round'].get('end_date') or max(
            [item.get('date', '') for item in before['related']['daily']] + [before['round']['start_date']])
    return target, before, as_of


def preview_generation(session, data_dir: Path, body: dict, account_id: str | None = None) -> dict:
    _only(body, {'kind', 'target', 'config_revision', 'notes', 'expected_input_sha256'})
    kind = body.get('kind')
    if kind not in KINDS or not isinstance(body.get('target'), dict):
        raise TradeError('INVALID_AI_GENERATION_TARGET', '须选择生成类型与目标')
    config = _frozen_config(session, body)
    notes = text_field(body.get('notes', ''), '补充说明', 4000)
    if kind != 'stock_analysis' and account_id is None:
        raise TradeError('AI_ACCOUNT_SCOPE_REQUIRED', 'OCR 和复盘草稿须绑定实盘账户')
    account = account_or_error(session, account_id, real=kind != 'stock_analysis') if account_id else None
    source = {'target': {}, 'images': [], 'account_revision': account.input_revision if account else None}
    raw = body['target']
    if kind == 'stock_analysis':
        _only(raw, {'dataset_id', 'decision_at', 'strict'})
        context = freeze_context(session, data_dir, {'dataset_ids': [raw.get('dataset_id')],
            'decision_at': raw.get('decision_at'), 'strict': raw.get('strict', True)}, account_id=account_id)
        selected = context['datasets'][0]
        if not selected['bars']:
            raise TradeError('AI_NO_ELIGIBLE_BARS', '没有可用于分析的已可得行情')
        source.update(target={'dataset_id': selected['id'], 'decision_at': context['decision_at'],
                              'strict': context['strict']}, symbol=selected['symbol'],
                      breakout_candidates=[bar['event_date'] for bar in selected['bars']])
    elif kind == 'review_scores':
        scoring = freeze_score_targets(session, account_id, raw)
        source.update(target=scoring['target'], score_targets=scoring['subjects'])
        context = freeze_context(session, data_dir, {'account_date': scoring['target']['key']}, account_id=account_id)
        context.update(day_trades=scoring['day_trades'], selected_trade_ids=scoring['selected_trade_ids'],
                       daily_review=get_review(session, account_id, scoring['target']['key']))
    elif kind == 'review_draft':
        target, before, as_of = _review_target(session, account_id, raw)
        source.update(target=target, target_before=before)
        context = freeze_context(session, data_dir, {'account_date': as_of} if as_of else {}, account_id=account_id)
        context['review_target'] = before
    else:
        _only(raw, {'attachment_ids'})
        ids = raw.get('attachment_ids')
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 3 or any(not isinstance(item, str) for item in ids)
                or len(set(ids)) != len(ids)):
            raise TradeError('AI_INVALID_IMAGES', '需要 1 至 3 张不同的账户复盘图片')
        total = 0
        for attachment_id in sorted(ids):
            metadata, content = get_attachment(session, data_dir, account_id, attachment_id)
            total += len(content)
            if total > 8 * 1024 * 1024:
                raise TradeError('AI_IMAGES_TOO_LARGE', '本次图片总大小不能超过 8 MB')
            source['images'].append({key: metadata[key] for key in (
                'id', 'revision', 'mime_type', 'byte_size', 'width', 'height', 'review_date')})
            source['images'][-1]['sha256'] = hashlib.sha256(content).hexdigest()
        source['target'] = {'attachment_ids': sorted(ids)}
        # A screenshot recognition request sends only the selected screenshots.
        context = {'version': 'selected-image-context-v1', 'account_id': account_id,
                   'images': source['images'], 'sources': [{'type': 'attachment', 'id': image['id'],
                       'date': image['review_date'], 'sha256': image['sha256']} for image in source['images']]}
    source['context'] = context
    instruction = output_prompt(kind, source) + ('\n用户补充：' + notes if notes else '')
    messages = [{'role': 'system', 'content': BASE_SYSTEM_PROMPT},
                {'role': 'user', 'content': instruction + '\n已冻结资料：\n' + encode(source)}]
    if len(encode(messages).encode('utf-8')) > MAX_REQUEST_BYTES - 4096:
        raise TradeError('AI_INPUT_TOO_LARGE', '所选结构化生成上下文过大')
    channel = 'vision' if kind.startswith('ocr_') else 'text'
    fingerprint = {'kind': kind, 'source': source, 'config': config, 'messages': messages}
    return {'kind': kind, 'target': source['target'], 'source': source, 'context': context,
            'config_revision': config['revision'], 'channel': channel, 'model': config[channel]['model'],
            'provider': config[channel]['base_url'], 'messages': messages,
            'input_sha256': hashlib.sha256(encode(fingerprint).encode()).hexdigest()}


def prepare_generation(session, data_dir: Path, body: dict, account_id: str | None = None) -> dict:
    preview = preview_generation(session, data_dir, body, account_id)
    if body.get('expected_input_sha256') != preview['input_sha256']:
        raise TradeError('AI_CONTEXT_CHANGED', '请先预览并确认本次冻结上下文', 409)
    request = {'messages': preview['messages'], 'template': None, 'user_message': preview['kind'],
               'images': preview['source']['images']}
    run = _new_call(session, config=_frozen_config(session, body), account_id=account_id,
                    channel=preview['channel'], request=request, context=preview['source'],
                    input_sha256=preview['input_sha256'], session_id=None, kind=preview['kind'])
    now = utc_now()
    row = AIGeneration(id=new_id(), run_id=run['id'], account_id=account_id, kind=preview['kind'],
                       status='queued', revision=1, source_json=encode(preview['source']), output_json=None,
                       errors_json='[]', acceptance_json=None, input_sha256=preview['input_sha256'],
                       deleted=0, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    _audit(session, row, 'prepare')
    return _view(row)


def hydrate_images(session, data_dir: Path | None, account_id: str | None, request: dict) -> list[dict]:
    if data_dir is None or account_id is None:
        raise TradeError('AI_IMAGE_STORAGE_UNAVAILABLE', '图片调用缺少本地存储或账户范围')
    parts = [{'type': 'text', 'text': request['messages'][-1]['content']}]
    total = 0
    for frozen in request['images']:
        current, content = get_attachment(session, data_dir, account_id, frozen['id'])
        if current['revision'] != frozen['revision'] or hashlib.sha256(content).hexdigest() != frozen['sha256']:
            raise TradeError('AI_IMAGE_CHANGED', '所选图片已变化，请重新预览', 409)
        total += len(content)
        if total > 8 * 1024 * 1024:
            raise TradeError('AI_IMAGES_TOO_LARGE', '图片总大小超出上限')
        parts.append({'type': 'image_url', 'image_url': {'url': 'data:' + current['mime_type'] + ';base64,' +
                                                       base64.b64encode(content).decode('ascii')}})
    messages = deepcopy(request['messages'])
    messages[-1]['content'] = parts
    return messages


def list_generations(session, account_id: str | None = None) -> list[dict]:
    if account_id:
        account_or_error(session, account_id)
    return [_view(row, False) for row in session.scalars(select(AIGeneration).where(
        AIGeneration.account_id == account_id, AIGeneration.deleted == 0).order_by(
            AIGeneration.created_at.desc(), AIGeneration.id).limit(100))]


def get_generation(session, generation_id: str, account_id: str | None = None) -> dict:
    return _view(_record(session, generation_id, account_id))


def list_generation_audit(session, generation_id: str, account_id: str | None = None) -> list[dict]:
    _record(session, generation_id, account_id, True)
    return [{'id': item.id, 'revision': item.revision, 'action': item.action,
             'snapshot': json.loads(item.snapshot_json), 'created_at': item.created_at}
            for item in session.scalars(select(AIGenerationAudit).where(
                AIGenerationAudit.generation_id == generation_id).order_by(AIGenerationAudit.revision))]


def _bind_asset_destination(session, row: AIGeneration, output: dict) -> None:
    if row.kind == 'ocr_assets':
        source = json.loads(row.source_json)
        source['destination_snapshot'] = next((item for item in list_snapshots(session, row.account_id)
            if item['snap_date'] == output['snap_date']), None) if output['snap_date'] else None
        row.source_json = encode(source)


def finalize_generation(session, generation_id: str, body: dict, account_id: str | None = None) -> dict:
    _only(body, {'expected_revision'})
    row = _record(session, generation_id, account_id)
    _revision(row.revision, body.get('expected_revision'))
    if row.status != 'queued':
        raise TradeError('AI_GENERATION_ALREADY_FINALIZED', '此记录已完成结构化校验', 409)
    call = session.get(AICall, row.run_id)
    if call.status != 'completed':
        raise TradeError('AI_RUN_NOT_COMPLETED', '仅完整成功的模型回复可以形成结构化草稿', 409)
    try:
        normalized = normalize_output(row.kind, strict_json(call.output), json.loads(row.source_json))
        row.output_json, row.status, row.errors_json = encode(normalized), 'draft', '[]'
        _bind_asset_destination(session, row, normalized)
    except TradeError as exc:
        row.status, row.errors_json = 'invalid_output', encode([{'code': exc.code, 'message': exc.message}])
    return _change(session, row, 'finalize')


def edit_generation(session, generation_id: str, body: dict, account_id: str | None = None) -> dict:
    _only(body, {'expected_revision', 'output'})
    row = _record(session, generation_id, account_id)
    _revision(row.revision, body.get('expected_revision'))
    if row.status not in ('draft', 'invalid_output'):
        raise TradeError('AI_GENERATION_CLOSED', '只可修订未接受的草稿或格式错误结果', 409)
    normalized = normalize_output(row.kind, body.get('output'), json.loads(row.source_json))
    row.output_json, row.status, row.errors_json = encode(normalized), 'draft', '[]'
    _bind_asset_destination(session, row, normalized)
    return _change(session, row, 'edit')


def _save_review_sections(session, account_id: str, target: dict, current: dict, sections: dict) -> dict:
    kind, key = target['type'], target['key']
    if kind in ('daily', 'rehearsal'):
        return save_review(session, account_id, key, {**current, **sections, 'expected_revision': current['revision']})
    if kind in ('weekly', 'monthly'):
        return save_period(session, account_id, kind, key, {'expected_revision': current['revision'],
            'sections': {**current.get('sections', {}), **sections}})
    return save_round_note(session, account_id, key, {'expected_revision': current['revision'], 'summary': sections['summary']})


def accept_generation(session, generation_id: str, body: dict, account_id: str | None = None) -> dict:
    _only(body, {'expected_revision', 'expected_target_revision'})
    row = _record(session, generation_id, account_id)
    _revision(row.revision, body.get('expected_revision'))
    if row.status != 'draft':
        raise TradeError('AI_GENERATION_CLOSED', '只能接受已校验草稿', 409)
    source, output = json.loads(row.source_json), json.loads(row.output_json)
    acceptance = {'kind': row.kind, 'accepted_at': utc_now()}
    if row.kind == 'review_scores':
        account = account_or_error(session, account_id, real=True)
        if account.input_revision != source['account_revision']:
            raise TradeError('AI_SOURCE_CHANGED', '来源交易或资产已修订，请重新评分', 409)
        acceptance.update(apply_score_suggestions(session, account_id, source['target']['key'],
            source['score_targets'], output['subjects'], generation_id=row.id, run_id=row.run_id))
    elif row.kind == 'review_draft':
        account = account_or_error(session, account_id, real=True)
        if account.input_revision != source['account_revision']:
            raise TradeError('AI_SOURCE_CHANGED', '来源交易或资产已修订，请重新生成复盘草稿', 409)
        current = _review_current(session, account_id, source['target'])
        _revision(current['revision'], body.get('expected_target_revision'))
        _revision(current['revision'], source['target_before']['revision'])
        after = _save_review_sections(session, account_id, source['target'], current, output['sections'])
        acceptance.update(before=current, after=after, target_revision=after['revision'])
    elif row.kind == 'ocr_trades':
        rows = output['trades']
        if not rows or any(item['side'] not in ('buy', 'sell') or not item['quantity'] or any(
                item[key] is None for key in ('trade_date', 'symbol', 'price', 'fee')) for item in rows):
            raise TradeError('AI_OCR_NEEDS_REVIEW', '请补齐成交日期、代码、方向、正数量、价格和费用；持仓行不能当作成交')
        pending = []
        for item in rows:
            created = create_pending_trade(session, account_id, {**item, 'note': '来自 AI 识别草稿 ' + row.id})
            record = session.get(PendingTrade, created['id'])
            before = created
            record.source, record.source_text = 'ai_ocr', encode({'generation_id': row.id, 'run_id': row.run_id})
            created = {**created, 'source': record.source, 'source_text': record.source_text}
            session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type='pending_trade',
                entity_id=record.id, operation='ai_provenance', before_json=encode(before),
                after_json=encode(created), created_at=utc_now()))
            pending.append(created)
        acceptance['pending_trades'] = pending
    elif row.kind == 'ocr_assets':
        if any(output[key] is None for key in ('snap_date', 'total_assets', 'available_cash')) or any(
                item[key] is None for item in output['positions'] for key in ('symbol', 'quantity', 'market_value')):
            raise TradeError('AI_OCR_NEEDS_REVIEW', '请人工补齐资产日期、总资产、现金及持仓字段后确认')
        before = source.get('destination_snapshot')
        current = next((item for item in list_snapshots(session, account_id) if item['snap_date'] == output['snap_date']), None)
        revision = current['revision'] if current else 0
        _revision(revision, body.get('expected_target_revision'))
        _revision(revision, before['revision'] if before else 0)
        after = save_snapshot(session, account_id, {key: output[key] for key in (
            'snap_date', 'total_assets', 'available_cash', 'positions')} | {
                'expected_revision': revision, 'note': '来自人工确认的 AI 识别草稿 ' + row.id})
        acceptance.update(before=before, after=after, target_revision=after['revision'])
    # Stock acceptance only marks the saved research result retained; annotations stay manual.
    row.status, row.acceptance_json = 'accepted', encode(acceptance)
    return _change(session, row, 'accept')


def reject_generation(session, generation_id: str, body: dict, account_id: str | None = None) -> dict:
    _only(body, {'expected_revision'})
    row = _record(session, generation_id, account_id)
    _revision(row.revision, body.get('expected_revision'))
    if row.status not in ('draft', 'invalid_output'):
        raise TradeError('AI_GENERATION_CLOSED', '只能拒绝未接受的草稿', 409)
    row.status = 'rejected'
    return _change(session, row, 'reject')


def retract_generation(session, generation_id: str, body: dict, account_id: str | None = None) -> dict:
    _only(body, {'expected_revision'})
    row = _record(session, generation_id, account_id)
    _revision(row.revision, body.get('expected_revision'))
    if row.status != 'accepted':
        raise TradeError('AI_GENERATION_CLOSED', '只能撤回已接受结果', 409)
    source, acceptance = json.loads(row.source_json), json.loads(row.acceptance_json)
    if row.kind == 'review_scores':
        acceptance['retraction'] = retract_score_suggestions(session, account_id, acceptance['sheets'])
    elif row.kind == 'review_draft':
        target = source['target']
        current = _review_current(session, account_id, target)
        _revision(current['revision'], acceptance['target_revision'])
        prior = acceptance['before'].get('sections', {}) if target['type'] in ('weekly', 'monthly') else acceptance['before']
        restored = {key: prior.get(key, '') for key in target['fields']}
        acceptance['retraction'] = _save_review_sections(session, account_id, target, current, restored)
    elif row.kind == 'ocr_trades':
        # Validate every pending row before mutating any, even if caller catches errors.
        for saved in acceptance['pending_trades']:
            pending = session.get(PendingTrade, saved['id'])
            if pending is None or pending.status != 'pending' or pending.revision != saved['revision']:
                raise TradeError('AI_ACCEPTED_DATA_CHANGED', '识别记录已编辑或确认入账，不能从 AI 草稿撤回', 409)
        acceptance['retraction'] = [discard_pending_trade(session, account_id, item['id'], item['revision'])
                                    for item in acceptance['pending_trades']]
    elif row.kind == 'ocr_assets':
        before = acceptance['before']
        if before is None:
            raise TradeError('AI_RETRACT_UNSUPPORTED', '已新建的确认资产快照需通过资产页面人工修订；不能从 AI 草稿删除事实', 409)
        current = next((item for item in list_snapshots(session, account_id)
                        if item['snap_date'] == acceptance['after']['snap_date']), None)
        if current is None:
            raise TradeError('AI_ACCEPTED_DATA_CHANGED', '确认资产快照已变化', 409)
        _revision(current['revision'], acceptance['target_revision'])
        acceptance['retraction'] = save_snapshot(session, account_id, {**before, 'expected_revision': current['revision']})
    row.status, row.acceptance_json = 'retracted', encode(acceptance)
    return _change(session, row, 'retract')


def delete_generation(session, generation_id: str, body: dict, account_id: str | None = None) -> dict:
    _only(body, {'expected_revision'})
    row = _record(session, generation_id, account_id)
    _revision(row.revision, body.get('expected_revision'))
    call = session.get(AICall, row.run_id)
    if call.status in ('queued', 'running') or row.status == 'accepted':
        raise TradeError('AI_GENERATION_BUSY', '请先停止调用或撤回已接受结果，再删除记录', 409)
    row.deleted = 1
    return _change(session, row, 'delete')
