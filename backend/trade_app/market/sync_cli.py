"""Command-line access to the same durable sync jobs used by the desktop UI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid

from trade_app.platform.local_api_client import LocalAPI, data_directory_identity
from trade_app.platform.types import TradeError


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def load(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise TradeError('CLI_FILE_INVALID', '输入或任务文件不存在、为链接或超过1MiB')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise TradeError('CLI_FILE_INVALID', '输入或任务文件必须为JSON对象')
    return value


def save(path, value, *, exclusive=False):
    encoded = encode(value)
    if exclusive:
        with path.open('x', encoding='utf-8') as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        return
    fd, temporary = tempfile.mkstemp(prefix='.sync-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_journal(value, client):
    if value.get('format') != 'trade-sync-job-v1' or value.get('base_url') != client.base or value.get('intent_sha256') != digest(value.get('intent')):
        raise TradeError('CLI_JOURNAL_CHANGED', '任务文件、输入摘要或本机地址不匹配')
    if value.get('data_dir') != data_directory_identity(client):
        raise TradeError('CLI_DATA_DIRECTORY_CHANGED', '服务当前数据目录已改变，不能在另一目录继续原同步任务')
    if not re.fullmatch('[a-f0-9]{32}', str(value.get('request_id', ''))):
        raise TradeError('CLI_JOURNAL_CHANGED', '任务请求编号无效')


def submit(client, intent, path, *, resume=False):
    if resume:
        journal = load(path)
        validate_journal(journal, client)
        if intent != journal['intent']:
            raise TradeError('CLI_INTENT_CHANGED', '原同步请求已变化，请使用新的任务文件')
    else:
        allowed = {'symbols', 'start_date', 'end_date', 'mode', 'provider', 'provider_order'}
        if not isinstance(intent, dict) or set(intent) - allowed:
            raise TradeError('CLI_SYNC_FIELDS', '同步请求包含未知字段')
        journal = {'format': 'trade-sync-job-v1', 'base_url': client.base,
            'data_dir': data_directory_identity(client), 'request_id': uuid.uuid4().hex,
            'intent': intent, 'intent_sha256': digest(intent), 'job_id': None}
        save(path, journal, exclusive=True)
    if journal.get('job_id'):
        return status(client, journal)
    # Persist request ID before sending. A lost response replays the same
    # idempotent request after obtaining a new session, even after app restart.
    result = client.request('/api/v1/market/sync-jobs', 'POST', intent, request_id=journal['request_id'])
    journal['job_id'] = result['id']
    save(path, journal)
    return result


def status(client, journal):
    identifier = str(journal.get('job_id', ''))
    if not re.fullmatch('[a-f0-9]{32}', identifier):
        raise TradeError('CLI_JOB_UNCONFIRMED', '任务提交尚未收到确认，请使用sync --resume重放相同请求')
    return client.request('/api/v1/market/sync-jobs/' + identifier)


def retry(client, journal, source, output):
    if source['state'] != 'partial_failed':
        raise TradeError('CLI_JOB_NOT_RETRYABLE', '只有部分失败任务可生成失败证券重试')
    if output.exists():
        pending = load(output)
        if pending.get('format') == 'trade-sync-job-v1':
            validate_journal(pending, client)
            if pending.get('retry_parent_id') != journal['job_id']:
                raise TradeError('CLI_RETRY_CHANGED', '重试文件属于其他来源任务')
            return status(client, pending)
        if pending.get('format') != 'trade-sync-retry-v1' or pending.get('parent') != journal:
            raise TradeError('CLI_RETRY_CHANGED', '重试文件属于其他来源任务')
    else:
        pending = {'format': 'trade-sync-retry-v1', 'parent': journal, 'request_id': uuid.uuid4().hex}
        save(output, pending, exclusive=True)
    operation_id = pending['request_id']
    if not re.fullmatch('[a-f0-9]{32}', str(operation_id)):
        raise TradeError('CLI_RETRY_CHANGED', '重试请求编号无效')
    result = client.request('/api/v1/market/sync-jobs/' + source['id'] + '/retry-failed', 'POST', {}, request_id=operation_id)
    intent = {**journal['intent'], 'symbols': result['symbols']}
    save(output, {**journal, 'request_id': operation_id, 'job_id': result['id'],
        'intent': intent, 'intent_sha256': digest(intent), 'retry_parent_id': journal['job_id']})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='新Trade本机行情同步CLI；任务与桌面任务中心共用')
    parser.add_argument('--url', default='http://127.0.0.1:8011')
    commands = parser.add_subparsers(dest='command', required=True)
    sync = commands.add_parser('sync')
    sync.add_argument('--request', required=True, type=Path)
    sync.add_argument('--job-file', required=True, type=Path)
    sync.add_argument('--resume', action='store_true')
    for name in ('status', 'cancel', 'retry-failed', 'wait'):
        command = commands.add_parser(name)
        command.add_argument('--job-file', required=True, type=Path)
        if name == 'retry-failed':
            command.add_argument('--new-job-file', required=True, type=Path)
        if name == 'wait':
            command.add_argument('--timeout', default=60, type=int)
    args = parser.parse_args(argv)
    try:
        client = LocalAPI(args.url)
        if args.command == 'sync':
            result = submit(client, load(args.request), args.job_file, resume=args.resume)
        else:
            journal = load(args.job_file)
            validate_journal(journal, client)
            result = status(client, journal)
            if args.command == 'cancel':
                result = client.request('/api/v1/market/sync-jobs/' + result['id'] + '/cancel', 'POST', {}, request_id=uuid.uuid4().hex)
            elif args.command == 'retry-failed':
                result = retry(client, journal, result, args.new_job_file)
            elif args.command == 'wait':
                if not 1 <= args.timeout <= 3600:
                    raise TradeError('CLI_WAIT_LIMIT', '等待上限须为1至3600秒')
                deadline = time.monotonic() + args.timeout
                while result['state'] in ('queued', 'running', 'cancelling') and time.monotonic() < deadline:
                    time.sleep(min(2, max(0, deadline-time.monotonic())))
                    result = status(client, journal)
        print(encode(result))
        return 0 if result['state'] not in ('failed', 'partial_failed', 'cancelled') else 2
    except (TradeError, ValueError, OSError) as exc:
        print(encode({'error': getattr(exc, 'code', type(exc).__name__), 'message': str(exc)[:500]}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
