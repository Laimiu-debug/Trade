"""Optional authenticated HTTPS proxy boundary; the API remains loopback only."""
from dataclasses import dataclass, field
import ipaddress
import os
import re
import secrets
from urllib.parse import urlsplit

from trade_app.platform.types import TradeError


@dataclass(frozen=True)
class RemoteAccess:
    origin: str | None = None
    authority: str | None = None
    token: str = field(default='', repr=False)

    @classmethod
    def from_environment(cls):
        raw = os.environ.get('TRADE_PUBLIC_ORIGIN', '').strip()
        if not raw:
            return cls()
        try:
            parts = urlsplit(raw)
            hostname = parts.hostname or ''
            port = parts.port
            if (parts.scheme != 'https' or parts.username or parts.password or parts.query or parts.fragment or
                    parts.path not in ('', '/') or len(hostname) > 253 or hostname == 'localhost' or
                    not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', hostname) or '.' not in hostname):
                raise ValueError()
            try:
                ipaddress.ip_address(hostname)
            except ValueError:
                pass
            else:
                raise ValueError()
            if port is not None and not 1 <= port <= 65535:
                raise ValueError()
        except ValueError as exc:
            raise ValueError('TRADE_PUBLIC_ORIGIN must be one HTTPS DNS origin without a path') from exc
        token = os.environ.get('TRADE_PROXY_TOKEN', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', token):
            raise ValueError('TRADE_PROXY_TOKEN must be a separate 32-byte-or-longer URL-safe random token')
        authority = hostname + (f':{port}' if port and port != 443 else '')
        return cls('https://' + authority, authority, token)

    def check(self, request):
        authority = request.headers.get('host', '').lower()
        local_host = authority.split(':', 1)[0] in {'127.0.0.1', 'localhost', 'testserver'}
        peer = request.client.host if request.client else ''
        try:
            local_peer = ipaddress.ip_address(peer).is_loopback
        except ValueError:
            local_peer = peer == 'testclient'  # In-process ASGI test transport only.
        if not local_peer:
            raise TradeError('NON_LOOPBACK_PEER', 'API仅接受本机连接，请通过已认证的HTTPS代理访问', 403)
        remote_authorities = (self.authority, self.authority + ':443') if self.authority and ':' not in self.authority else (self.authority,)
        remote = self.authority is not None and authority in remote_authorities
        if remote:
            supplied = request.headers.get('x-trade-proxy-token', '')
            if (not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', supplied) or
                    not secrets.compare_digest(supplied, self.token) or
                    request.headers.get('x-forwarded-proto') != 'https'):
                raise TradeError('AUTHENTICATED_PROXY_REQUIRED', '远程请求须经过已认证的HTTPS入口', 403)
        elif not local_host:
            raise TradeError('UNTRUSTED_HOST', '不支持的访问地址', 403)
        elif request.headers.get('origin') == self.origin and self.origin is not None:
            raise TradeError('REMOTE_HOST_MISMATCH', '远程来源与访问地址不匹配', 403)
        return remote
