"""Checked, content-addressed local review images with account-scoped access."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.reviews.attachment_models import ReviewAttachment
from trade_app.reviews.service import validate_day
from trade_app.trading.service import account_or_error


MAX_IMAGE_BYTES = 8 * 1024 * 1024
FORMAT_MIME = {'PNG': 'image/png', 'JPEG': 'image/jpeg',
               'WEBP': 'image/webp', 'GIF': 'image/gif'}


def attachment_data(row: ReviewAttachment) -> dict:
    return {'id': row.id, 'review_date': row.review_date, 'mime_type': row.mime_type,
            'width': row.width, 'height': row.height,
            'original_name': row.original_name, 'byte_size': row.byte_size,
            'revision': row.revision, 'created_at': row.created_at,
            'deleted': row.deleted_at is not None,
            'url': f'/api/v1/accounts/{row.account_id}/review-attachments/{row.id}/content'}


def list_attachments(session: Session, account_id: str, day: str) -> list[dict]:
    validate_day(day)
    account_or_error(session, account_id, real=True)
    rows = session.scalars(select(ReviewAttachment).where(
        ReviewAttachment.account_id == account_id,
        ReviewAttachment.review_date == day,
        ReviewAttachment.deleted_at.is_(None)).order_by(
        ReviewAttachment.created_at, ReviewAttachment.id))
    return [attachment_data(row) for row in rows]


def get_attachment(session: Session, data_dir: Path, account_id: str,
                   attachment_id: str) -> tuple[dict, bytes]:
    account_or_error(session, account_id, real=True)
    row = session.get(ReviewAttachment, attachment_id)
    if row is None or row.account_id != account_id or row.deleted_at is not None:
        raise TradeError('ATTACHMENT_NOT_FOUND', '复盘图片不存在', 404)
    path = data_dir / 'attachments' / f'{row.sha256}.bin'
    if not path.is_file():
        raise TradeError('ATTACHMENT_MISSING', '复盘图片文件缺失', 409)
    content = path.read_bytes()
    if len(content) != row.byte_size or hashlib.sha256(content).hexdigest() != row.sha256:
        raise TradeError('ATTACHMENT_CORRUPT', '复盘图片校验失败', 409)
    return attachment_data(row), content


def add_attachment(session: Session, data_dir: Path, account_id: str, day: str,
                   content: bytes, original_name: str) -> dict:
    validate_day(day)
    account_or_error(session, account_id, real=True)
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise TradeError('INVALID_IMAGE_SIZE', '图片大小须在 1 B 至 8 MB 之间')
    try:
        with Image.open(io.BytesIO(content)) as image:
            mime = FORMAT_MIME.get(image.format or '')
            width, height = image.size
            if not mime or width < 1 or height < 1 or width > 12000 or height > 12000 or width * height > 40_000_000:
                raise TradeError('INVALID_IMAGE', '仅支持合理尺寸的 PNG、JPEG、WebP 或 GIF 图片')
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise TradeError('INVALID_IMAGE', '图片内容无法识别或已损坏') from exc
    digest = hashlib.sha256(content).hexdigest()
    folder = data_dir / 'attachments'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{digest}.bin'
    if target.exists():
        if target.read_bytes() != content:
            raise TradeError('ATTACHMENT_CORRUPT', '已有图片哈希不匹配', 409)
    else:
        fd, temporary = tempfile.mkstemp(prefix='.attachment-', dir=folder)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    now = utc_now()
    row = ReviewAttachment(id=new_id(), account_id=account_id,
                           review_date=day, sha256=digest, mime_type=mime,
                           width=width, height=height,
                           original_name=Path(original_name.replace('\\', '/')).name[:120] or 'image',
                           byte_size=len(content), revision=1, deleted_at=None, created_at=now)
    session.add(row)
    result = attachment_data(row)
    session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type='review_attachment',
                           entity_id=row.id, operation='create', before_json=None,
                           after_json=json.dumps(result, ensure_ascii=False), created_at=now))
    return result


def delete_attachment(session: Session, account_id: str, attachment_id: str,
                      expected_revision: int) -> dict:
    account_or_error(session, account_id, real=True)
    row = session.get(ReviewAttachment, attachment_id)
    if row is None or row.account_id != account_id:
        raise TradeError('ATTACHMENT_NOT_FOUND', '复盘图片不存在', 404)
    if row.deleted_at is not None:
        raise TradeError('ATTACHMENT_DELETED', '复盘图片已移除', 409)
    if row.revision != expected_revision:
        raise TradeError('REVISION_CONFLICT', '图片版本已变化', 409)
    before = attachment_data(row)
    row.deleted_at = utc_now()
    row.revision += 1
    after = attachment_data(row)
    session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type='review_attachment',
                           entity_id=row.id, operation='delete',
                           before_json=json.dumps(before, ensure_ascii=False),
                           after_json=json.dumps(after, ensure_ascii=False), created_at=row.deleted_at))
    return after
