"""Bounded legacy news adapters and persisted, explicitly dated cache snapshots.

Publication time is not historical availability. No news is fed into strategy inputs.
The original sources are Eastmoney fast news, followed by Google News RSS.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from trade_app.platform.types import TradeError

EASTMONEY_URL = 'https://np-weblist.eastmoney.com/comm/web/getFastNewsList'
GOOGLE_URL = 'https://news.google.com/rss/search'
DEFAULT_QUERY = 'A股 热点'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 150
CACHE_SECONDS = 300
SOURCE_VERSION = 'legacy-news-bounded-v1'
SHANGHAI = ZoneInfo('Asia/Shanghai')
_network_slots = threading.BoundedSemaphore(2)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean(value: object, maximum: int) -> str:
    parser = _Text()
    parser.feed(html.unescape(str(value or ''))[:20000])
    return re.sub(r'\s+', ' ', ' '.join(parser.parts)).strip()[:maximum]


def safe_url(value: object) -> str | None:
    raw = str(value or '').strip()
    if raw.startswith('//'):
        raw = 'https:' + raw
    try:
        parsed = urlsplit(raw)
        if (len(raw) > 2048 or parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username or parsed.password or any(ord(c) < 33 for c in raw)):
            return None
        return raw
    except ValueError:
        return None


def publication_time(value: object) -> str | None:
    raw = str(value or '').strip()
    if not raw or len(raw) > 128:
        return None
    try:
        if re.fullmatch(r'\d{10}|\d{13}', raw):
            parsed = datetime.fromtimestamp(int(raw) / (1000 if len(raw) == 13 else 1), timezone.utc)
        else:
            # A date without a time cannot establish membership of a rolling hourly window.
            if re.fullmatch(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', raw):
                return None
            try:
                parsed = datetime.fromisoformat(raw.replace('/', '-').replace('Z', '+00:00'))
            except ValueError:
                parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=SHANGHAI)
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _item(title, snippet, url, published, source, provider) -> dict | None:
    title, snippet = clean(title, 240), clean(snippet, 700)
    if not title and not snippet:
        return None
    row = {'title': title or '（来源未提供标题）', 'snippet': snippet,
           'url': safe_url(url), 'published_at': publication_time(published),
           'publication_time_raw': clean(published, 128),
           'source_name': clean(source, 80), 'provider': provider}
    row['id'] = digest(row)
    return row


def parse_eastmoney(contents: bytes) -> list[dict]:
    try:
        payload = json.loads(contents)
        source = payload.get('data') if isinstance(payload, dict) else None
        if isinstance(source, dict):
            source = source.get('fastNewsList', source.get('newsList', source.get('list')))
        if not isinstance(source, list):
            raise ValueError('Unexpected response shape')
        rows = []
        for raw in source[:MAX_ITEMS]:
            if not isinstance(raw, dict):
                continue
            url = raw.get('url')
            identifier = str(raw.get('code') or raw.get('newsid') or raw.get('id') or '')
            if not url and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identifier):
                url = 'https://kuaixun.eastmoney.com/news/' + identifier
            row = _item(raw.get('title'), raw.get('summary') or raw.get('digest') or raw.get('content') or raw.get('ltext'),
                        url, raw.get('showTime') or raw.get('displayTime') or raw.get('publishTime') or raw.get('time'),
                        raw.get('source') or raw.get('mediaName') or raw.get('media') or raw.get('infoSource') or '东方财富快讯', 'eastmoney')
            if row:
                rows.append(row)
        return rows
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise TradeError('NEWS_SOURCE_FORMAT', '资讯来源的响应格式暂不兼容', 502) from exc


def parse_google(contents: bytes) -> list[dict]:
    if b'<!DOCTYPE' in contents.upper() or b'<!ENTITY' in contents.upper():
        raise TradeError('NEWS_SOURCE_FORMAT', '资讯来源的 XML 格式不受支持', 502)
    try:
        root = ET.fromstring(contents)
        channel = root.find('channel')
        if channel is None:
            raise ValueError('Missing RSS channel')
        rows = []
        for raw in channel.findall('item')[:MAX_ITEMS]:
            row = _item(raw.findtext('title'), raw.findtext('description'), raw.findtext('link'),
                        raw.findtext('pubDate'), raw.findtext('source') or 'Google News RSS', 'google_rss')
            if row:
                rows.append(row)
        return rows
    except (ET.ParseError, ValueError) as exc:
        raise TradeError('NEWS_SOURCE_FORMAT', '资讯来源的响应格式暂不兼容', 502) from exc


def fetch_provider(provider: str, query: str) -> list[dict]:
    url = EASTMONEY_URL if provider == 'eastmoney' else GOOGLE_URL
    params = ({'client': 'web', 'biz': 'web_724', 'fastColumn': '102', 'sortEnd': '', 'pageSize': MAX_ITEMS,
               'req_trace': str(int(time.time() * 1000)), '_': str(int(time.time() * 1000))}
              if provider == 'eastmoney' else {'q': query, 'hl': 'zh-CN', 'gl': 'CN', 'ceid': 'CN:zh-Hans'})
    deadline = time.monotonic() + 8
    chunks, size = [], 0
    try:
        with httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=False,
                          headers={'User-Agent': 'TradeRebuild/1.0', 'Accept': 'application/json, application/rss+xml'}) as client:
            with client.stream('GET', url, params=params) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES or time.monotonic() > deadline:
                        raise TradeError('NEWS_SOURCE_LIMIT', '资讯响应超过大小或读取时限', 502)
                    chunks.append(chunk)
        return (parse_eastmoney if provider == 'eastmoney' else parse_google)(b''.join(chunks))
    except httpx.TimeoutException as exc:
        raise TradeError('NEWS_SOURCE_TIMEOUT', '资讯来源请求超时', 502) from exc
    except httpx.HTTPError as exc:
        raise TradeError('NEWS_SOURCE_UNAVAILABLE', '资讯来源暂不可用', 502) from exc


def normalize_request(body: dict) -> dict:
    now = now_utc()
    try:
        as_of = datetime.fromisoformat(body.get('as_of_at') or now.isoformat())
        if as_of.tzinfo is None or as_of > now + timedelta(seconds=60):
            raise ValueError('Invalid as of')
        as_of = as_of.astimezone(timezone.utc)
        age = body.get('age_hours', 72)
        if type(age) is not int or age not in (24, 48, 72):
            raise ValueError('Invalid age')
        provider = body.get('provider', 'auto')
        query = re.sub(r'\s+', ' ', body.get('query', DEFAULT_QUERY)).strip()
        if provider not in {'auto', 'eastmoney', 'google_rss'} or not query or len(query) > 120:
            raise ValueError('Invalid source/query')
        start = as_of - timedelta(hours=age)
        end = as_of
        date_from, date_to = body.get('date_from'), body.get('date_to')
        if bool(date_from) != bool(date_to):
            raise ValueError('Both dates required')
        if date_from:
            first = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=SHANGHAI)
            last = datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=SHANGHAI)
            if first > last or first.strftime('%Y-%m-%d') != date_from or last.strftime('%Y-%m-%d') != date_to:
                raise ValueError('Invalid range')
            start = max(start, first.astimezone(timezone.utc))
            end = min(end, (last + timedelta(days=1)).astimezone(timezone.utc) - timedelta(microseconds=1))
        return {'query': query, 'provider': provider, 'age_hours': age, 'as_of_at': as_of.isoformat(),
                'date_from': date_from, 'date_to': date_to,
                'window_start': start.isoformat(), 'window_end': end.isoformat(),
                'cache_key': digest({'query': query, 'provider': provider, 'version': SOURCE_VERSION})}
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        raise TradeError('INVALID_NEWS_REQUEST', '请选择 24/48/72 小时、有效来源、含时区的截至时间；复盘起止日期须同时填写且顺序正确') from exc


def cached_snapshot(session: Session, request: dict) -> dict | None:
    row = session.execute(text('SELECT payload_json FROM market_news_snapshots WHERE cache_key=:key ORDER BY created_at DESC, id DESC LIMIT 1'),
                          {'key': request['cache_key']}).scalar_one_or_none()
    return json.loads(row) if row else None


def present(snapshot: dict | None, request: dict, *, cache_hit: bool = True,
            errors: list[dict] | None = None, attempted: list[str] | None = None) -> dict:
    errors = errors or []
    source_rows = snapshot['items'] if snapshot else []
    start, end = datetime.fromisoformat(request['window_start']), datetime.fromisoformat(request['window_end'])
    as_of = datetime.fromisoformat(request['as_of_at'])
    rows, excluded = [], {'unknown_publication_time': 0, 'after_as_of': 0, 'outside_window': 0}
    tokens = request['query'].casefold().split() if request['query'] != DEFAULT_QUERY else []
    for row in source_rows:
        if tokens and not any(token in (row['title'] + ' ' + row['snippet'] + ' ' + row['source_name']).casefold() for token in tokens):
            continue
        published = row['published_at']
        if published is None:
            excluded['unknown_publication_time'] += 1
            continue
        at = datetime.fromisoformat(published)
        if at > as_of:
            excluded['after_as_of'] += 1
        elif not start <= at <= end:
            excluded['outside_window'] += 1
        else:
            rows.append(row)
    rows.sort(key=lambda row: (row['published_at'], row['id']), reverse=True)
    rows = list({row['id']: row for row in rows}.values())
    age = max(0, (now_utc() - datetime.fromisoformat(snapshot['fetched_at'])).total_seconds()) if snapshot else None
    stale = age is not None and age > CACHE_SECONDS
    effective_errors = errors or (snapshot.get('errors', []) if snapshot else [])
    return {'request': {key: value for key, value in request.items() if key != 'cache_key'},
            'snapshot_id': snapshot['id'] if snapshot else None, 'fetched_at': snapshot['fetched_at'] if snapshot else None,
            'source_version': SOURCE_VERSION, 'actual_provider': snapshot['actual_provider'] if snapshot else None,
            'source_url': snapshot['source_url'] if snapshot else None,
            'items': rows, 'count': len(rows), 'source_item_count': len(source_rows), 'excluded': excluded,
            'cache_hit': cache_hit and snapshot is not None, 'cache_age_seconds': int(age) if age is not None else None,
            'cache_stale': stale, 'cache_ttl_seconds': CACHE_SECONDS,
            'attempted_providers': attempted if attempted is not None else snapshot.get('attempted_providers', []) if snapshot else [],
            'fallback_used': bool(errors and snapshot) or bool(snapshot and snapshot['actual_provider'] == 'google_rss' and request['provider'] == 'auto'),
            'degraded': bool(effective_errors or stale), 'errors': effective_errors,
            'status': 'unavailable' if errors and snapshot is None else 'not_loaded' if snapshot is None else 'ready' if rows else 'empty',
            'availability_quality': 'retrieved_snapshot_not_historical_availability',
            'notes': ['窗口以截至时间固定，复盘日期按上海时区取交集；未自动扩大窗口。',
                      '来源仅提供有限近期条目，不保证覆盖完整 72 小时；历史窗口不代表当时可获得的资讯。',
                      '发布时间未知、晚于截至时间或窗口之外的条目不计入列表；不向策略输入新闻。']}


def prepare_refresh(request: dict, cached: dict | None) -> dict:
    if not _network_slots.acquire(blocking=False):
        raise TradeError('NEWS_BUSY', '已有资讯请求正在执行，请稍后重试', 409)
    attempts, errors, empty_snapshot = [], [], None
    try:
        for provider in (['eastmoney', 'google_rss'] if request['provider'] == 'auto' else [request['provider']]):
            attempts.append(provider)
            try:
                rows = fetch_provider(provider, request['query'])
                snapshot = {'items': rows, 'fetched_at': now_utc().isoformat(), 'actual_provider': provider,
                            'cache_key': request['cache_key'],
                            'source_url': EASTMONEY_URL if provider == 'eastmoney' else GOOGLE_URL,
                            'source_version': SOURCE_VERSION, 'attempted_providers': list(attempts), 'errors': list(errors)}
                snapshot['id'] = digest(snapshot)
                if present(snapshot, request, cache_hit=False)['items'] or request['provider'] != 'auto':
                    return {'snapshot': snapshot, 'new': True, 'errors': errors, 'attempted': attempts}
                empty_snapshot = snapshot
            except TradeError as exc:
                errors.append({'provider': provider, 'code': exc.code})
        # A valid empty source is a successful result, not a fabricated cache hit.
        if empty_snapshot is not None:
            empty_snapshot.update(errors=errors, attempted_providers=attempts)
            empty_snapshot.pop('id')
            empty_snapshot['id'] = digest(empty_snapshot)
            return {'snapshot': empty_snapshot, 'new': True, 'errors': errors, 'attempted': attempts}
        return {'snapshot': cached, 'new': False, 'errors': errors, 'attempted': attempts}
    finally:
        _network_slots.release()


def save_refresh(session: Session, request: dict, prepared: dict) -> dict:
    snapshot = prepared['snapshot']
    if prepared['new']:
        session.execute(text('INSERT OR IGNORE INTO market_news_snapshots(id,cache_key,payload_json,created_at) VALUES (:id,:key,:payload,:created)'),
                        {'id': snapshot['id'], 'key': request['cache_key'], 'payload': encode(snapshot), 'created': snapshot['fetched_at']})
        # Cached public news is bounded; persistent research/ledger records are not involved.
        session.execute(text('DELETE FROM market_news_snapshots WHERE id NOT IN (SELECT id FROM market_news_snapshots ORDER BY created_at DESC,id DESC LIMIT 100)'))
    return present(snapshot, request, cache_hit=not prepared['new'], errors=prepared['errors'], attempted=prepared['attempted'])
