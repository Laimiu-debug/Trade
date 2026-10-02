"""Small loopback-only CLI client; session cookies/CSRF stay in memory."""
from http.cookiejar import CookieJar
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler

from trade_app.platform.types import TradeError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TradeError('CLI_REDIRECT_BLOCKED', '本机API不能重定向到其他地址')


class LocalAPI:
    def __init__(self, base_url):
        parts = urlsplit(base_url)
        if (parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost', '::1') or
                parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/')):
            raise TradeError('CLI_LOCAL_URL_REQUIRED', 'CLI只连接明确的 http://127.0.0.1:端口 本机服务')
        if not parts.port or not 1 <= parts.port <= 65535:
            raise TradeError('CLI_LOCAL_URL_REQUIRED', '本机服务URL必须包含有效端口')
        self.base = base_url.rstrip('/')
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()), NoRedirect())
        self.csrf = None

    def request(self, path, method='GET', payload=None, *, request_id=None, bootstrap=False):
        if not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path:
            raise TradeError('CLI_INVALID_API_PATH', 'API路径无效')
        headers = {'Accept': 'application/json', 'Origin': self.base}
        if method != 'GET':
            if not self.csrf:
                self.connect()
            headers.update({'Content-Type': 'application/json', 'X-CSRF-Token': self.csrf})
            if request_id:
                headers['Idempotency-Key'] = request_id
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8') if payload is not None else None
        try:
            with self.opener.open(Request(self.base + path, data=body, method=method, headers=headers), timeout=20) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise TradeError('CLI_RESPONSE_LIMIT', '本机API响应超过4MiB')
                decoded = json.loads(raw)
                return decoded if bootstrap else decoded['data']
        except HTTPError as exc:
            try:
                data = json.loads(exc.read(4096)).get('error', {})
                message = data.get('message', f'API请求失败（HTTP {exc.code}）')
                code = data.get('code', 'CLI_API_ERROR')
            except (ValueError, AttributeError):
                code, message = 'CLI_API_ERROR', f'API请求失败（HTTP {exc.code}）'
            raise TradeError(code, str(message)[:500], exc.code) from exc
        except (URLError, TimeoutError, ConnectionError) as exc:
            raise TradeError('CLI_API_UNREACHABLE', '本机服务未连接或请求中断；已提交的任务可用同一任务文件重试') from exc
        except (ValueError, KeyError) as exc:
            raise TradeError('CLI_API_INVALID_RESPONSE', '本机API响应不是预期JSON') from exc

    def connect(self):
        health = self.request('/health', bootstrap=True)
        if health.get('product') != 'trade-rebuild':
            raise TradeError('CLI_WRONG_PRODUCT', '目标端口不是新Trade应用')
        session = self.request('/api/v1/session')
        self.csrf = session['csrf_token']
        return health


def data_directory_identity(client):
    """Bind retry journals to the selected data directory across server restarts."""
    client.connect()
    return client.request('/api/v1/system/storage')['data_dir']
