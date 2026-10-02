"""Read-only health and freshness summary for frozen market datasets."""
from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.market.models import MarketDataset


def market_diagnostics(session: Session, data_dir: Path, *, verify: bool = False) -> dict:
    rows = list(session.scalars(select(MarketDataset).order_by(MarketDataset.last_date.desc(), MarketDataset.id)))
    providers = Counter(row.provider for row in rows)
    statuses: list[dict] = []
    total_bytes = 0
    checked = 0
    today = datetime.now(timezone.utc).date()
    for row in rows:
        path = data_dir / 'market' / f'{row.id}.json'
        age = (today - date.fromisoformat(row.last_date)).days
        status = 'available'
        size = None
        try:
            size = path.stat().st_size
            total_bytes += size
            if verify:
                with path.open('rb') as contents:
                    if hashlib.file_digest(contents, 'sha256').hexdigest() != row.id:
                        status = 'hash_mismatch'
                checked += 1
        except FileNotFoundError:
            status = 'missing'
        except OSError:
            status = 'unreadable'
        statuses.append({'dataset_id': row.id, 'symbol': row.symbol, 'provider': row.provider,
                         'last_date': row.last_date, 'age_calendar_days': age,
                         'stale_calendar_days': age > 7, 'availability_quality': row.availability_quality,
                         'content_status': status, 'bytes': size})
    return {'dataset_count': len(rows), 'provider_counts': dict(sorted(providers.items())),
            'total_bytes': total_bytes, 'verified_contents': checked if verify else 0,
            'verification_requested': verify,
            'missing_or_bad_count': sum(item['content_status'] != 'available' for item in statuses),
            'stale_calendar_days_count': sum(item['stale_calendar_days'] for item in statuses),
            'items': statuses}
