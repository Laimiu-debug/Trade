"""Persisted abnormal-movement scans and provenance."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.types import TradeError, utc_now
from trade_app.research.abnormal_models import AbnormalRun


def _view(row: AbnormalRun, detail: bool = False) -> dict:
    result = json.loads(row.result_json)
    return {'id': row.id, 'created_at': row.created_at,
            'date_from': result['date_from'], 'date_to': result['date_to'],
            'scan_mode': result['scan_mode'], 'total_scanned': result['total_scanned'],
            'event_count': len(result['events']),
            'quality_flags': result['quality_flags'],
            **({'request': json.loads(row.request_json), 'result': result} if detail else {})}


def save_abnormal_run(session: Session, prepared: dict) -> dict:
    row = session.get(AbnormalRun, prepared['id'])
    if row is None:
        row = AbnormalRun(id=prepared['id'],
                          request_json=json.dumps(prepared['request'], ensure_ascii=False),
                          result_json=json.dumps(prepared['result'], ensure_ascii=False),
                          code_sha256=prepared['code_sha256'], created_at=utc_now())
        session.add(row)
        session.flush()
    return _view(row, True)


def list_abnormal_runs(session: Session) -> list[dict]:
    return [_view(row) for row in session.scalars(select(AbnormalRun).order_by(
        AbnormalRun.created_at.desc(), AbnormalRun.id.desc()).limit(100))]


def get_abnormal_run(session: Session, run_id: str) -> dict:
    row = session.get(AbnormalRun, run_id)
    if row is None:
        raise TradeError('ABNORMAL_RUN_NOT_FOUND', '异动扫描记录不存在', 404)
    return _view(row, True)
