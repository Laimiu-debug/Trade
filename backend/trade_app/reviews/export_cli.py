"""Read-only local PDF export and optional opening in the system PDF viewer."""
import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from trade_app.platform.local_api_client import LocalAPI
from trade_app.platform.types import TradeError
from trade_app.reviews.periods import bounds


def export_pdf(client, account_id, mode, *, kind='daily', key=None, date_basis='sell', limit=24, date_from=None, date_to=None, output=None):
    if not re.fullmatch(r'[a-f0-9]{32}', account_id):
        raise TradeError('INVALID_EXPORT_ACCOUNT', '账户ID须为新应用中的32位编号')
    if kind not in ('daily', 'weekly', 'monthly') or mode not in ('review', 'statistics'):
        raise TradeError('INVALID_EXPORT_KIND', '导出类型或周期无效')
    if date_basis not in ('buy', 'sell') or type(limit) is not int or not 1 <= limit <= 366:
        raise TradeError('INVALID_EXPORT_RANGE', '统计归属或范围无效')
    if mode == 'review':
        if not isinstance(key, str):
            raise TradeError('INVALID_EXPORT_DATE', '请指定复盘日期或周期')
        if kind == 'daily':
            try:
                if date.fromisoformat(key).isoformat() != key:
                    raise ValueError(key)
            except ValueError as exc:
                raise TradeError('INVALID_EXPORT_DATE', '日复盘使用YYYY-MM-DD') from exc
        else:
            bounds(kind, key)
        path = f'/api/v1/accounts/{account_id}/exports/review/{kind}/{key}.pdf'
        filename = f'trade-review-{account_id}-{kind}-{key}.pdf'
    else:
        path = f'/api/v1/accounts/{account_id}/exports/performance.pdf?' + urlencode({'kind': kind, 'limit': limit, 'date_basis': date_basis,
            **({'date_from': date_from} if date_from else {}), **({'date_to': date_to} if date_to else {})})
        filename = f'trade-statistics-{account_id}-{kind}-{date_basis}.pdf'
    client.connect()
    if output is None:
        folder = client.request('/api/v1/settings/groups/print')['value']['export_directory']
        if not folder:
            raise TradeError('EXPORT_DIRECTORY_REQUIRED', '请指定 --output，或先在打印设置中保存本机导出目录')
        output = Path(folder) / filename
    output = Path(output).expanduser().resolve()
    if output.suffix.lower() != '.pdf' or output.exists():
        raise TradeError('EXPORT_FILE_EXISTS', '输出须为尚不存在的PDF文件；已有导出不会覆盖')
    try:
        with client.opener.open(Request(client.base + path, headers={'Accept': 'application/pdf', 'Origin': client.base}), timeout=60) as response:
            raw = response.read(32 * 1024 * 1024 + 1)
            if len(raw) > 32 * 1024 * 1024 or response.headers.get_content_type() != 'application/pdf' or not raw.startswith(b'%PDF-'):
                raise TradeError('EXPORT_PDF_INVALID', '导出不是有效PDF或超过32MiB')
    except HTTPError as exc:
        try:
            error = json.loads(exc.read(4096)).get('error', {})
            message = error.get('message', '账户或已保存复盘无法读取')
        except (ValueError, AttributeError):
            message = '账户或已保存复盘无法读取'
        raise TradeError('EXPORT_API_ERROR', str(message)[:500], exc.code) from exc
    except (URLError, TimeoutError, ConnectionError) as exc:
        raise TradeError('EXPORT_CONNECTION_FAILED', '本机导出连接中断；没有生成输出文件') from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return {'state': 'exported', 'path': str(output), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def open_pdf(path):
    if sys.platform == 'win32':
        os.startfile(str(path))
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(argv=None):
    parser = argparse.ArgumentParser(description='从正在运行的新Trade本机服务导出已保存内容；不访问互联网、不改账本')
    parser.add_argument('--url', default='http://127.0.0.1:8011')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('accounts', help='列出新应用账户编号')
    for mode in ('review', 'statistics'):
        command = commands.add_parser(mode)
        command.add_argument('--account', required=True)
        command.add_argument('--kind', choices=['daily', 'weekly', 'monthly'], default='daily' if mode == 'review' else 'monthly')
        if mode == 'review': command.add_argument('--key', required=True)
        else:
            command.add_argument('--date-basis', choices=['buy', 'sell'], default='sell')
            command.add_argument('--limit', type=int, default=24)
            command.add_argument('--date-from')
            command.add_argument('--date-to')
        command.add_argument('--output', type=Path)
        command.add_argument('--open', action='store_true', help='保存成功后打开系统PDF查看器')
    args = parser.parse_args(argv)
    try:
        client = LocalAPI(args.url)
        if args.command == 'accounts':
            client.connect(); result = client.request('/api/v1/accounts')
        else:
            result = export_pdf(client, args.account, args.command, kind=args.kind, key=getattr(args, 'key', None),
                date_basis=getattr(args, 'date_basis', 'sell'), limit=getattr(args, 'limit', 24),
                date_from=getattr(args, 'date_from', None), date_to=getattr(args, 'date_to', None), output=args.output)
            if args.open:
                try: open_pdf(result['path'])
                except OSError:
                    result['preview'] = '无法启动系统查看器，PDF已保存，可手动打开。'
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (TradeError, OSError, ValueError) as exc:
        print(json.dumps({'state': 'failed', 'error': getattr(exc, 'code', type(exc).__name__), 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
