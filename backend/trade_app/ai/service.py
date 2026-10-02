"""Explicit AI calls with frozen prompts and short durable streaming transactions."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import threading
from typing import Callable

from sqlalchemy import func, select, text, update

from trade_app.ai.config import encode, freeze_config
from trade_app.ai.contexts import BASE_SYSTEM_PROMPT, freeze_context, system_templates, text_field
from trade_app.ai.models import AICall, AIPromptRevision, AIPromptTemplate, AISession
from trade_app.ai.provider import (MAX_OUTPUT_CHARACTERS, MAX_REQUEST_BYTES, UNKNOWN_USAGE,
                                   ProviderFailure, normalize_usage, stream_chat)
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.trading.service import account_or_error


ACTIVE = ('queued', 'running')
_NETWORK_SLOTS = threading.BoundedSemaphore(2)


def _revision(actual: int, expected: object) -> None:
    if isinstance(expected, bool) or not isinstance(expected, int) or actual != expected:
        raise TradeError('REVISION_CONFLICT', '记录已变更，请刷新后重试', 409)


def _scope(session, account_id: str | None) -> None:
    if account_id is not None:
        account_or_error(session, account_id)


def _session(session, session_id: str, account_id: str | None) -> AISession:
    row = session.get(AISession, session_id)
    if row is None or row.deleted or row.account_id != account_id:
        raise TradeError('AI_SESSION_NOT_FOUND', 'AI 会话不存在于当前账户范围', 404)
    _scope(session, account_id)
    return row


def _call(session, run_id: str, account_id: str | None) -> AICall:
    row = session.get(AICall, run_id)
    if row is None or row.account_id != account_id:
        raise TradeError('AI_RUN_NOT_FOUND', 'AI 调用不存在于当前账户范围', 404)
    if row.session_id:
        _session(session, row.session_id, account_id)
    else:
        _scope(session, account_id)
    return row


def _session_view(session, row: AISession, detail=False) -> dict:
    calls = list(session.scalars(select(AICall).where(AICall.session_id == row.id).order_by(
        AICall.created_at.desc(), AICall.id.desc()).limit(50)))
    total = session.scalar(select(func.count()).select_from(AICall).where(AICall.session_id == row.id))
    result = {'id': row.id, 'account_id': row.account_id, 'title': row.title, 'revision': row.revision,
              'created_at': row.created_at, 'updated_at': row.updated_at, 'message_count': total * 2,
              'active_run_id': next((item.id for item in calls if item.status in ACTIVE), None)}
    if detail:
        messages, characters, retained = [], 0, 0
        for call in calls:
            request = json.loads(call.request_json)
            user_message = request.get('user_message', '')
            size = len(user_message) + len(call.output)
            if characters + size > 1000000:
                break
            characters += size
            retained += 1
            messages.extend([{'id': call.id + ':assistant', 'run_id': call.id, 'role': 'assistant',
                              'content': call.output, 'status': call.status, 'created_at': call.created_at},
                             {'id': call.id + ':user', 'run_id': call.id, 'role': 'user',
                              'content': user_message, 'status': call.status, 'created_at': call.created_at}])
        result.update(messages=list(reversed(messages)), omitted_message_count=(total - retained) * 2)
    return result


def create_session(session, body: dict) -> dict:
    if set(body) - {'title', 'account_id'}:
        raise TradeError('INVALID_AI_SESSION', '会话字段无效')
    account_id = body.get('account_id')
    _scope(session, account_id)
    now = utc_now()
    row = AISession(id=new_id(), account_id=account_id,
                    title=text_field(body.get('title', '新会话'), '会话标题', 128, True),
                    revision=1, deleted=0, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return _session_view(session, row, True)


def list_sessions(session, account_id: str | None = None) -> list[dict]:
    _scope(session, account_id)
    return [_session_view(session, row) for row in session.scalars(select(AISession).where(
        AISession.account_id == account_id, AISession.deleted == 0).order_by(
            AISession.updated_at.desc(), AISession.id).limit(100))]


def get_session(session, session_id: str, account_id: str | None = None) -> dict:
    return _session_view(session, _session(session, session_id, account_id), True)


def delete_session(session, session_id: str, body: dict, account_id: str | None = None) -> dict:
    row = _session(session, session_id, account_id)
    _revision(row.revision, body.get('expected_revision'))
    if session.scalar(select(AICall.id).where(AICall.session_id == row.id, AICall.status.in_(ACTIVE)).limit(1)):
        raise TradeError('AI_SESSION_BUSY', '请先停止当前调用再删除会话', 409)
    row.deleted, row.updated_at = 1, utc_now()
    row.revision += 1
    session.flush()
    return {'deleted': True, 'id': row.id, 'revision': row.revision}


def _template_view(row: AIPromptTemplate) -> dict:
    return {'id': row.id, 'name': row.name, 'content': row.content, 'revision': row.revision,
            'readonly': False, 'scope': 'custom', 'created_at': row.created_at, 'updated_at': row.updated_at}


def list_templates(session) -> list[dict]:
    return system_templates() + [_template_view(row) for row in session.scalars(select(AIPromptTemplate).where(
        AIPromptTemplate.deleted == 0).order_by(AIPromptTemplate.name, AIPromptTemplate.id))]


def _template(session, template_id: str) -> AIPromptTemplate:
    if template_id.startswith('strategy:'):
        raise TradeError('AI_TEMPLATE_READ_ONLY', '原策略说明只读，可复制为自定义提示词', 409)
    row = session.get(AIPromptTemplate, template_id)
    if row is None or row.deleted:
        raise TradeError('AI_TEMPLATE_NOT_FOUND', '提示词不存在', 404)
    return row


def _template_audit(session, row: AIPromptTemplate) -> None:
    session.add(AIPromptRevision(id=new_id(), template_id=row.id, revision=row.revision,
                                 snapshot_json=encode({**_template_view(row), 'deleted': bool(row.deleted)}),
                                 created_at=utc_now()))


def create_template(session, body: dict) -> dict:
    if set(body) != {'name', 'content'}:
        raise TradeError('INVALID_AI_TEMPLATE', '提示词需要 name 和 content')
    now = utc_now()
    row = AIPromptTemplate(id=new_id(), name=text_field(body['name'], '提示词名称', 128, True),
                           content=text_field(body['content'], '提示词', 16000, True), revision=1,
                           deleted=0, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    _template_audit(session, row)
    return _template_view(row)


def update_template(session, template_id: str, body: dict) -> dict:
    row = _template(session, template_id)
    _revision(row.revision, body.get('expected_revision'))
    if set(body) != {'name', 'content', 'expected_revision'}:
        raise TradeError('INVALID_AI_TEMPLATE', '编辑提示词需要 name、content 和 expected_revision')
    row.name = text_field(body['name'], '提示词名称', 128, True)
    row.content = text_field(body['content'], '提示词', 16000, True)
    row.revision, row.updated_at = row.revision + 1, utc_now()
    _template_audit(session, row)
    session.flush()
    return _template_view(row)


def delete_template(session, template_id: str, body: dict) -> dict:
    row = _template(session, template_id)
    _revision(row.revision, body.get('expected_revision'))
    row.deleted, row.revision, row.updated_at = 1, row.revision + 1, utc_now()
    _template_audit(session, row)
    session.flush()
    return {'deleted': True, 'id': row.id, 'revision': row.revision}


def _selected_template(session, body: dict) -> dict | None:
    template_id = body.get('template_id')
    if template_id is None:
        return None
    template = next((item for item in list_templates(session) if item['id'] == template_id), None)
    if template is None:
        raise TradeError('AI_TEMPLATE_NOT_FOUND', '提示词不存在', 404)
    _revision(template['revision'], body.get('template_revision'))
    return template


def _frozen_config(session, body: dict) -> dict:
    expected = body.get('config_revision')
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise TradeError('AI_CONFIG_CONFLICT', '须提供模型配置版本', 409)
    return freeze_config(session, expected)


def _channel(body: dict) -> str:
    channel = body.get('channel', 'text')
    if channel not in ('text', 'vision'):
        raise TradeError('INVALID_AI_CHANNEL', '模型类别须为 text 或 vision')
    return channel


def preview_prompt(session, data_dir: Path, session_id: str, body: dict,
                   account_id: str | None = None) -> dict:
    row = _session(session, session_id, account_id)
    if set(body) - {'message', 'channel', 'config_revision', 'context', 'template_id',
                    'template_revision', 'expected_input_sha256'}:
        raise TradeError('INVALID_AI_REQUEST', '对话请求包含不支持的字段')
    config = _frozen_config(session, body)
    channel = _channel(body)
    message = text_field(body.get('message'), '消息', 12000, True)
    context = freeze_context(session, data_dir, body.get('context'), account_id=row.account_id)
    template = _selected_template(session, body)
    complete = list(session.scalars(select(AICall).where(AICall.session_id == row.id,
        AICall.status == 'completed').order_by(AICall.created_at.desc(), AICall.id.desc()).limit(20)))
    history, history_chars = [], 0
    for call in complete:
        user_text = json.loads(call.request_json).get('user_message', '')
        if history_chars + len(user_text) + len(call.output) > 60000:
            break
        history_chars += len(user_text) + len(call.output)
        history[0:0] = [{'role': 'user', 'content': user_text}, {'role': 'assistant', 'content': call.output}]
    system = BASE_SYSTEM_PROMPT + ('\n用户选择的提示词：\n' + template['content'] if template else '')
    messages = [{'role': 'system', 'content': system}, *history,
                {'role': 'user', 'content': '本次明确选择的冻结资料（JSON）：\n' + encode(context) + '\n\n问题：\n' + message}]
    if len(encode(messages).encode('utf-8')) > MAX_REQUEST_BYTES - 4096:
        raise TradeError('AI_INPUT_TOO_LARGE', '本次提示词过大，请减少资料或新建会话')
    payload = {'messages': messages, 'context': context, 'config': config, 'channel': channel, 'template': template}
    return {'messages': messages, 'context': context, 'config_revision': config['revision'],
            'channel': channel, 'model': config[channel]['model'], 'provider': config[channel]['base_url'],
            'template': template, 'history_run_count': len(history) // 2,
            'input_sha256': hashlib.sha256(encode(payload).encode('utf-8')).hexdigest()}


def _run_view(row: AICall, detail=True) -> dict:
    config = json.loads(row.config_json)
    result = {'id': row.id, 'session_id': row.session_id, 'account_id': row.account_id, 'kind': row.kind,
              'channel': row.channel, 'status': row.status, 'config_revision': row.config_revision,
              'model': config[row.channel]['model'], 'provider': config[row.channel]['base_url'],
              'input_sha256': row.input_sha256, 'output': row.output, 'usage': json.loads(row.usage_json),
              'error_code': row.error_code, 'cancel_requested': bool(row.cancel_requested),
              'created_at': row.created_at, 'updated_at': row.updated_at,
              'started_at': row.started_at, 'finished_at': row.finished_at}
    if detail:
        result.update(context=json.loads(row.context_json), request=json.loads(row.request_json))
    return result


def _new_call(session, *, config: dict, account_id: str | None, channel: str, request: dict,
              context: dict, input_sha256: str, session_id: str | None, kind: str) -> dict:
    now = utc_now()
    row = AICall(id=new_id(), session_id=session_id, account_id=account_id, kind=kind, channel=channel,
                 status='queued', config_revision=config['revision'], config_json=encode(config),
                 request_json=encode(request), context_json=encode(context), input_sha256=input_sha256,
                 output='', usage_json=encode(UNKNOWN_USAGE), error_code=None, cancel_requested=0,
                 created_at=now, updated_at=now, started_at=None, finished_at=None)
    session.add(row)
    session.flush()
    return _run_view(row)


def prepare_run(session, data_dir: Path, session_id: str, body: dict,
                account_id: str | None = None) -> dict:
    row = _session(session, session_id, account_id)
    if session.scalar(select(AICall.id).where(AICall.session_id == row.id, AICall.status.in_(ACTIVE)).limit(1)):
        raise TradeError('AI_SESSION_BUSY', '每个会话同时只能运行一个调用', 409)
    if session.scalar(select(func.count()).select_from(AICall).where(AICall.session_id == row.id)) >= 1000:
        raise TradeError('AI_SESSION_LIMIT', '此会话已达 1000 次调用，请创建新会话', 409)
    preview = preview_prompt(session, data_dir, session_id, body, account_id)
    if body.get('expected_input_sha256') is not None and body['expected_input_sha256'] != preview['input_sha256']:
        raise TradeError('AI_CONTEXT_CHANGED', '预览后上下文或历史已变化，请重新预览', 409)
    config = _frozen_config(session, body)
    result = _new_call(session, config=config, account_id=account_id, channel=preview['channel'],
                       request={'messages': preview['messages'], 'template': preview['template'],
                                'user_message': body['message'].strip()}, context=preview['context'],
                       input_sha256=preview['input_sha256'], session_id=row.id, kind='chat')
    row.revision, row.updated_at = row.revision + 1, utc_now()
    session.flush()
    return result


def prepare_connection_test(session, body: dict, account_id: str | None = None) -> dict:
    _scope(session, account_id)
    if set(body) - {'channel', 'config_revision'}:
        raise TradeError('INVALID_AI_REQUEST', '连接测试只接受模型类别和配置版本')
    config = _frozen_config(session, body)
    config['max_tokens'] = 8
    channel = _channel(body)
    request = {'messages': [{'role': 'user', 'content': 'Reply OK.'}], 'template': None, 'user_message': 'Reply OK.'}
    identity = {'config': config, 'channel': channel, 'request': request}
    return _new_call(session, config=config, account_id=account_id, channel=channel, request=request,
                     context={'version': 'connection-test-v1', 'sources': []},
                     input_sha256=hashlib.sha256(encode(identity).encode()).hexdigest(), session_id=None, kind='connection_test')


def get_run(session, run_id: str, account_id: str | None = None) -> dict:
    return _run_view(_call(session, run_id, account_id))


def list_runs(session, account_id: str | None = None) -> list[dict]:
    _scope(session, account_id)
    statement = select(AICall).outerjoin(AISession, AICall.session_id == AISession.id).where(
        AICall.account_id == account_id, (AICall.session_id.is_(None) | (AISession.deleted == 0))).order_by(
        AICall.created_at.desc(), AICall.id).limit(100)
    return [_run_view(row, False) for row in session.scalars(statement)]


def cancel_run(session, run_id: str, account_id: str | None = None) -> dict:
    row = _call(session, run_id, account_id)
    if row.status in ACTIVE:
        row.cancel_requested = 1
        row.updated_at = utc_now()
        if row.status == 'queued':
            row.status, row.error_code, row.finished_at = 'cancelled', 'AI_CANCELLED', row.updated_at
        session.flush()
    return _run_view(row)


def assert_run_startable(session, run_id: str, account_id: str | None = None) -> dict:
    row = _call(session, run_id, account_id)
    if row.status != 'queued':
        raise TradeError('AI_RUN_ALREADY_STARTED', '调用已经启动或结束；重试须创建新调用', 409)
    return _run_view(row)


def stream_run(factory, run_id: str, account_id: str | None = None, *,
               provider_factory: Callable | None = None, should_stop: Callable[[], bool] = lambda: False,
               data_dir: Path | None = None):
    """Run once, yielding dictionaries for SSE. Generator close persists interruption."""
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        row = _call(session, run_id, account_id)
        if row.status != 'queued':
            raise TradeError('AI_RUN_ALREADY_STARTED', '调用已经启动或结束；重试须创建新调用', 409)
        row.status, row.started_at, row.updated_at = 'running', utc_now(), utc_now()
        session.flush()
        config, request = json.loads(row.config_json), json.loads(row.request_json)
        channel = row.channel
    acquired = False
    iterator = None
    finalized = False

    def cancelled() -> bool:
        if should_stop():
            return True
        with factory() as session:
            current = session.get(AICall, run_id)
            return current is None or bool(current.cancel_requested) or current.status != 'running'

    def finish(status: str, code: str | None = None) -> dict:
        nonlocal finalized
        with factory.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            current = session.get(AICall, run_id)
            if current.status == 'running':
                if current.cancel_requested:
                    status, code = 'cancelled', 'AI_CANCELLED'
                current.status, current.error_code = status, code
                current.finished_at, current.updated_at = utc_now(), utc_now()
                session.flush()
            result = _run_view(current)
        finalized = True
        return result

    try:
        yield {'type': 'started', 'run_id': run_id}
        acquired = _NETWORK_SLOTS.acquire(blocking=False)
        if not acquired:
            raise ProviderFailure('AI_CONCURRENCY_LIMIT')
        if cancelled():
            raise ProviderFailure('AI_CANCELLED')
        messages = request['messages']
        if request.get('images'):
            from trade_app.ai.generation_service import hydrate_images
            try:
                with factory() as session:
                    messages = hydrate_images(session, data_dir, account_id, request)
            except TradeError as exc:
                raise ProviderFailure(exc.code) from None
        producer = provider_factory or stream_chat
        iterator = iter(producer(config, channel, messages, should_cancel=cancelled))
        ended = False
        finish_reason = None
        for event in iterator:
            if cancelled():
                raise ProviderFailure('AI_CANCELLED')
            kind = event.get('type')
            if kind == 'end':
                ended, finish_reason = True, event.get('finish_reason', 'stop')
                continue
            if kind not in ('delta', 'usage'):
                continue
            with factory.begin() as session:
                session.execute(text('BEGIN IMMEDIATE'))
                current = session.get(AICall, run_id)
                if current.status != 'running' or current.cancel_requested:
                    raise ProviderFailure('AI_CANCELLED')
                if kind == 'delta':
                    content = event.get('content')
                    if not isinstance(content, str):
                        raise ProviderFailure('AI_INVALID_RESPONSE')
                    if len(current.output) + len(content) > MAX_OUTPUT_CHARACTERS:
                        raise ProviderFailure('AI_OUTPUT_TOO_LARGE')
                    current.output += content
                else:
                    event = {'type': 'usage', 'usage': normalize_usage(event.get('usage'))}
                    current.usage_json = encode(event['usage'])
                current.updated_at = utc_now()
            yield event
        if not ended:
            raise ProviderFailure('AI_STREAM_INTERRUPTED')
        if finish_reason == 'length':
            raise ProviderFailure('AI_OUTPUT_TRUNCATED')
        if finish_reason not in (None, 'stop'):
            raise ProviderFailure('AI_RESPONSE_INCOMPLETE')
        yield {'type': 'done', 'run': finish('completed')}
    except GeneratorExit:
        finish('interrupted', 'AI_CLIENT_DISCONNECTED')
        raise
    except ProviderFailure as exc:
        status = ('cancelled' if exc.code == 'AI_CANCELLED' else 'interrupted' if exc.code in (
            'AI_STREAM_INTERRUPTED', 'AI_OUTPUT_TRUNCATED', 'AI_RESPONSE_INCOMPLETE') else 'failed')
        yield {'type': 'done', 'run': finish(status, exc.code)}
    except Exception:
        # Never persist or log provider exception text, URLs, headers or raw body.
        yield {'type': 'done', 'run': finish('failed', 'AI_INTERNAL_ERROR')}
    finally:
        try:
            if iterator is not None and hasattr(iterator, 'close'):
                iterator.close()
        finally:
            if acquired:
                _NETWORK_SLOTS.release()
            if not finalized:
                finish('interrupted', 'AI_CLIENT_DISCONNECTED')


def recover_interrupted_runs(factory) -> int:
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        now = utc_now()
        result = session.execute(update(AICall).where(AICall.status.in_(ACTIVE)).values(
            status='interrupted', error_code='AI_PROCESS_INTERRUPTED', updated_at=now, finished_at=now))
        return result.rowcount


def usage_summary(session, account_id: str | None = None) -> dict:
    _scope(session, account_id)
    rows = list(session.scalars(select(AICall).where(AICall.account_id == account_id)))

    def totals(calls: list) -> dict:
        usages = [json.loads(row.usage_json) for row in calls]
        result = {'call_count': len(calls), 'completed_count': sum(row.status == 'completed' for row in calls),
                  'failed_count': sum(row.status in ('failed', 'interrupted') for row in calls),
                  'unknown_usage_count': sum(item['total_tokens'] is None for item in usages)}
        for key in UNKNOWN_USAGE:
            known = [item[key] for item in usages if item[key] is not None]
            result[key] = sum(known) if known else None
        result['tokens_are_partial'] = any(item['total_tokens'] is None for item in usages)
        return result

    groups = defaultdict(list)
    for row in rows:
        config = json.loads(row.config_json)[row.channel]
        groups[(config['model'], config['base_url'])].append(row)
    return {**totals(rows), 'by_model': [{'model': key[0], 'provider': key[1], **totals(calls)}
                                       for key, calls in sorted(groups.items())]}
