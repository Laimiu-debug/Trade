"""Bounded OpenAI-compatible SSE transport; errors never include response bodies."""
from __future__ import annotations

import json
import codecs
import time
from collections.abc import Callable, Iterator

import httpx

from trade_app.ai.config import encode, resolve_credentials
from trade_app.platform.types import TradeError


MAX_REQUEST_BYTES = 196608
MAX_OUTPUT_CHARACTERS = 131072
MAX_RESPONSE_BYTES = 2097152
UNKNOWN_USAGE = {'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None}


class ProviderFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def normalize_usage(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {key: raw.get(key) if isinstance(raw.get(key), int) and not isinstance(raw.get(key), bool)
            and 0 <= raw[key] <= 1000000000 else None for key in UNKNOWN_USAGE}


def stream_chat(config: dict, channel: str, messages: list[dict], *,
                should_cancel: Callable[[], bool] = lambda: False,
                client_factory=None) -> Iterator[dict]:
    """Network starts on iteration only; read idle timeout bounds cancellation lag."""
    try:
        setting, key = resolve_credentials(config, channel)
    except TradeError as exc:
        raise ProviderFailure(exc.code) from None
    body = {'model': setting['model'], 'messages': messages, 'stream': True,
            'stream_options': {'include_usage': True}, 'temperature': config['temperature'],
            'max_tokens': config['max_tokens']}
    images = [part for message in messages if isinstance(message.get('content'), list)
              for part in message['content'] if isinstance(part, dict) and part.get('type') == 'image_url']
    if images and (channel != 'vision' or len(images) > 3 or any(
            not part.get('image_url', {}).get('url', '').startswith(('data:image/png;base64,', 'data:image/jpeg;base64,',
                                                                    'data:image/webp;base64,', 'data:image/gif;base64,'))
            for part in images)):
        raise ProviderFailure('AI_INVALID_IMAGE_INPUT')
    request_limit = 12 * 1024 * 1024 if images else MAX_REQUEST_BYTES
    if len(encode(body).encode('utf-8')) > request_limit:
        raise ProviderFailure('AI_INPUT_TOO_LARGE')
    url = setting['base_url']
    if not url.endswith('/chat/completions'):
        url += '/chat/completions'
    headers = {'Accept': 'text/event-stream', 'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    deadline = time.monotonic() + config['timeout_seconds']
    received, produced = 0, 0
    finished = False
    finish_reason = None

    def check() -> None:
        if should_cancel():
            raise ProviderFailure('AI_CANCELLED')
        if time.monotonic() >= deadline:
            raise ProviderFailure('AI_TIMEOUT')

    def parse_event(lines: list[str]) -> list[dict]:
        nonlocal produced, finished, finish_reason
        if not lines:
            return []
        payload = '\n'.join(lines)
        if payload.strip() == '[DONE]':
            finished = True
            return [{'type': 'end', 'finish_reason': finish_reason or 'stop'}]
        try:
            value = json.loads(payload)
            if not isinstance(value, dict) or 'error' in value:
                raise ValueError()
            events = []
            if 'usage' in value and value['usage'] is not None:
                events.append({'type': 'usage', 'usage': normalize_usage(value['usage'])})
            choices = value.get('choices', [])
            if not isinstance(choices, list):
                raise ValueError()
            if choices:
                choice = choices[0]
                content = choice.get('delta', {}).get('content')
                if content is not None:
                    if not isinstance(content, str):
                        raise ValueError()
                    produced += len(content)
                    if produced > MAX_OUTPUT_CHARACTERS:
                        raise ProviderFailure('AI_OUTPUT_TOO_LARGE')
                    if content:
                        events.append({'type': 'delta', 'content': content})
                reason = choice.get('finish_reason')
                if reason is not None:
                    finish_reason = reason
            return events
        except (ValueError, TypeError, AttributeError, IndexError):
            raise ProviderFailure('AI_INVALID_RESPONSE') from None

    factory = client_factory or httpx.Client
    try:
        check()
        # Proxy environment is deliberately ignored: the explicit provider URL
        # defines the destination, and redirects cannot forward credentials.
        with factory(timeout=httpx.Timeout(connect=min(5, config['timeout_seconds']), read=5,
                                           write=5, pool=5), follow_redirects=False, trust_env=False) as client:
            with client.stream('POST', url, headers=headers, json=body) as response:
                if response.status_code != 200:
                    code = ('AI_AUTH_ERROR' if response.status_code in (401, 403) else
                            'AI_RATE_LIMIT' if response.status_code == 429 else
                            'AI_PROVIDER_UNAVAILABLE' if response.status_code >= 500 else 'AI_HTTP_ERROR')
                    raise ProviderFailure(code)
                pending = []
                pending_size = 0
                def bounded_lines():
                    decoder = codecs.getincrementaldecoder('utf-8')()
                    buffer = ''
                    byte_count = 0
                    for chunk in response.iter_bytes():
                        byte_count += len(chunk)
                        if byte_count > MAX_RESPONSE_BYTES:
                            raise ProviderFailure('AI_RESPONSE_TOO_LARGE')
                        try:
                            buffer += decoder.decode(chunk)
                        except UnicodeDecodeError:
                            raise ProviderFailure('AI_INVALID_RESPONSE') from None
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            yield line.rstrip('\r')
                        if len(buffer) > 262144:
                            raise ProviderFailure('AI_RESPONSE_TOO_LARGE')
                    try:
                        buffer += decoder.decode(b'', final=True)
                    except UnicodeDecodeError:
                        raise ProviderFailure('AI_INVALID_RESPONSE') from None
                    if buffer:
                        yield buffer.rstrip('\r')

                for line in bounded_lines():
                    check()
                    size = len(line.encode('utf-8'))
                    received += size
                    if size > 262144 or received > MAX_RESPONSE_BYTES:
                        raise ProviderFailure('AI_RESPONSE_TOO_LARGE')
                    if line == '':
                        events = parse_event(pending)
                        pending, pending_size = [], 0
                        yield from events
                        if finished:
                            return
                    elif line.startswith('data:'):
                        pending.append(line[5:].lstrip(' '))
                        pending_size += size
                        if pending_size > 262144:
                            raise ProviderFailure('AI_RESPONSE_TOO_LARGE')
                    # Comments, event names, ids and retry hints carry no model text.
                yield from parse_event(pending)
                if finished:
                    return
                if finish_reason is None:
                    raise ProviderFailure('AI_STREAM_INTERRUPTED')
                yield {'type': 'end', 'finish_reason': finish_reason}
    except httpx.TimeoutException:
        raise ProviderFailure('AI_CANCELLED' if should_cancel() else 'AI_TIMEOUT') from None
    except httpx.HTTPError:
        raise ProviderFailure('AI_CANCELLED' if should_cancel() else 'AI_NETWORK_ERROR') from None
