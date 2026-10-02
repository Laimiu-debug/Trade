"""Separate legacy archive storage: no creation of backtest/plateau jobs or facts."""
import json
from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research.legacy_report_domain import digest, encode, attachment
from trade_app.research.legacy_report_models import LegacyResearchReport


def _row(session, identifier):
    row = session.get(LegacyResearchReport, identifier)
    if row is None or row.deleted:
        raise TradeError('LEGACY_REPORT_NOT_FOUND', '旧报告不存在或已删除', 404)
    return row


def get_report(session: Session, identifier: str):
    row = _row(session, identifier)
    if digest(row.payload_json.encode()) != row.content_sha256 or digest(row.original_bytes) != row.source_sha256:
        raise TradeError('LEGACY_REPORT_CORRUPTED', '旧报告原件或转换快照摘要不符', 409)
    preview, payload = json.loads(row.preview_json), json.loads(row.payload_json)
    if (digest(encode({key: value for key, value in preview.items() if key != 'preview_sha256'})) != preview['preview_sha256']
            or preview['source_sha256'] != row.source_sha256 or payload['source_sha256'] != row.source_sha256
            or payload['summary'] != preview['summary'] or payload['mode'] != row.mode):
        raise TradeError('LEGACY_REPORT_CORRUPTED', '旧报告预览与转换快照不一致', 409)
    return {**json.loads(row.metadata_json), 'summary': payload['summary'], 'title': preview['title'],
            'id': row.id, 'mode': row.mode, 'created_at': row.created_at,
            'content_sha256': row.content_sha256, 'preview': preview, 'payload': payload}


def list_reports(session: Session):
    rows = session.execute(select(LegacyResearchReport.id, LegacyResearchReport.mode,
        LegacyResearchReport.created_at, LegacyResearchReport.content_sha256, LegacyResearchReport.metadata_json)
        .where(LegacyResearchReport.deleted == 0).order_by(LegacyResearchReport.created_at.desc(), LegacyResearchReport.id).limit(100))
    return [{**json.loads(row.metadata_json), 'id': row.id, 'mode': row.mode,
             'created_at': row.created_at, 'content_sha256': row.content_sha256} for row in rows]


def save_report(session: Session, prepared: dict, expected_preview_sha256: str, mode: str, acknowledged: bool):
    preview = prepared['preview']
    if expected_preview_sha256 != preview['preview_sha256']:
        raise TradeError('LEGACY_REPORT_PREVIEW_CHANGED', '上传内容或预览已改变，请重新预览', 409)
    if mode not in ('legacy_readonly', 'archive_only') or acknowledged is not True:
        raise TradeError('LEGACY_REPORT_CONFIRMATION_REQUIRED', '请选择保存方式并确认旧报告限制')
    if mode == 'legacy_readonly' and not preview['can_convert']:
        raise TradeError('LEGACY_REPORT_FIELDS_MISSING', '无法结构转换，缺失字段：' + '、'.join(preview['missing_fields'][:15]))
    existing = session.scalar(select(LegacyResearchReport).where(LegacyResearchReport.source_sha256 == preview['source_sha256']))
    if existing:
        if existing.deleted:
            # Explicit re-import restores the same immutable archive identity.
            existing.deleted = 0; existing.deleted_at = None
            session.flush()
        return get_report(session, existing.id)
    metadata = {key: preview[key] for key in ('title', 'source_format', 'source_sha256', 'source_bytes', 'filename', 'can_convert', 'summary', 'quality_flags')}
    metadata.update({'missing_field_count': len(preview['missing_fields']), 'attachment_count': len(preview['attachments'])})
    payload = {**prepared['payload'], 'mode': mode}
    encoded = encode(payload)
    row = LegacyResearchReport(id=new_id(), source_sha256=preview['source_sha256'], mode=mode,
        content_sha256=digest(encoded), original_bytes=prepared['contents'], metadata_json=encode(metadata).decode(),
        preview_json=encode(preview).decode(), payload_json=encoded.decode(), created_at=utc_now(), deleted=0, deleted_at=None)
    session.add(row); session.flush()
    return get_report(session, row.id)


def delete_report(session: Session, identifier: str):
    row = _row(session, identifier)
    row.deleted = 1; row.deleted_at = utc_now()
    return {'id': identifier, 'deleted': True}


def original(session: Session, identifier: str, name: str | None = None):
    report = get_report(session, identifier)
    row = _row(session, identifier)
    if name is None:
        return row.original_bytes
    return attachment(row.original_bytes, report['filename'], name)
