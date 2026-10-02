"""Migrate only explicitly uploaded images bound to retained legacy references.

Source paths are display-only strings. No function opens a user-supplied path.
"""
import base64
import hashlib
import io
import json

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from trade_app.legacy_import.reader import canonical, digest, parse_json
from trade_app.legacy_import.service import _row
from trade_app.legacy_import.supplement_models import LegacySupplementBatch, LegacySupplementItem
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.reviews.attachments import add_attachment, get_attachment, FORMAT_MIME, MAX_IMAGE_BYTES
from trade_app.reviews.attachment_models import ReviewAttachment
from trade_app.reviews.models import DailyReview
from trade_app.trading.service import account_or_error

MAX_TOTAL_BYTES = 24 * 1024 * 1024


def catalog(session, import_id):
    row = _row(session, import_id)
    items = []
    if not row.source_kind.startswith('laimiu_') or not row.account_id:
        return {'import_id': import_id, 'expected_revision': row.revision, 'account_id': row.account_id, 'items': [],
                'notes': ['附件仅映射到同一来源已建立的新实盘账户；请先完成实盘核心转换。']}
    account_or_error(session, row.account_id, real=True)
    logical = json.loads(row.archive_json)
    if digest(logical) != row.logical_sha256:
        raise TradeError('LEGACY_ARCHIVE_HASH_MISMATCH', '逻辑档案摘要校验失败', 409)
    mappings = {str(item['source_id']): item for item in json.loads(row.mappings_json) if item['section'] == 'daily_reviews'}
    prior = {item.source_key: json.loads(item.target_json) for item in session.scalars(select(LegacySupplementItem).where(LegacySupplementItem.import_id == import_id))}
    for index, review in enumerate(logical.get('daily_reviews', [])):
        if not isinstance(review, dict):
            continue
        source_id = str(review.get('id', index))
        try:
            paths = parse_json(review.get('images') or '[]') if isinstance(review.get('images'), str) else review.get('images') or []
        except (ValueError, TypeError):
            paths = None
        if not isinstance(paths, list):
            items.append({'key': 'attachment:' + source_id + ':invalid', 'source_path': '(无法解析 images)', 'review_date': review.get('review_date'), 'status': 'blocked', 'reason': '原 images 不是路径数组'})
            continue
        mapping = mappings.get(source_id)
        target = session.get(DailyReview, mapping['target_id']) if mapping else None
        valid = target is not None and target.account_id == row.account_id and target.review_date == review.get('review_date')
        for number, path in enumerate(paths):
            key = f'attachment:{source_id}:{number}'
            reason = '' if valid and isinstance(path, str) and path.strip() else '缺少同来源日复盘映射或原路径无效'
            items.append({'key': key, 'source_path': path if isinstance(path, str) else '(非文本路径)',
                          'review_date': review.get('review_date'), 'target_review_id': target.id if valid else None,
                          'target_revision': target.revision if valid else None,
                          'status': 'mapped' if key in prior else 'blocked' if reason else 'needs_file',
                          'reason': reason, 'mapping': prior.get(key)})
    return {'import_id': import_id, 'expected_revision': row.revision, 'account_id': row.account_id,
            'source_sha256': row.source_sha256, 'logical_sha256': row.logical_sha256, 'items': items,
            'notes': ['旧路径只供核对，从不由服务打开或扫描。请选择文件并逐项确认路径对应。',
                      '每批最多 20 张、合计 24 MiB，每张最多 8 MiB。图片内容 SHA256、日期和目标版本共同冻结。',
                      '只新增附件或复用同账户同日期相同内容；不覆盖人工正文，不删除原件。图片中的文字和元数据不会自动脱敏。']}


def decode(files):
    if not isinstance(files, list) or not 1 <= len(files) <= 20:
        raise TradeError('LEGACY_ATTACHMENT_SELECTION', '每批须明确选择 1–20 个来源附件')
    result, seen, total = [], set(), 0
    for item in files:
        key = item.get('source_key')
        if not isinstance(key, str) or key in seen or len(key) > 128:
            raise TradeError('LEGACY_ATTACHMENT_SELECTION', '附件来源键重复或无效')
        seen.add(key)
        name = item.get('filename')
        if not isinstance(name, str) or not name or len(name) > 180 or any(char in name for char in '/\\\r\n\x00'):
            raise TradeError('LEGACY_ATTACHMENT_FILENAME', '仅接受所选文件的名称，不接受服务器路径')
        try:
            encoded = item['content_base64']
            if not isinstance(encoded, str) or len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 4:
                raise ValueError()
            content = base64.b64decode(encoded, validate=True)
        except (KeyError, ValueError, TypeError) as exc:
            raise TradeError('LEGACY_ATTACHMENT_CONTENT', '附件编码无效或超过单张 8 MiB') from exc
        total += len(content)
        if not content or len(content) > MAX_IMAGE_BYTES or total > MAX_TOTAL_BYTES:
            raise TradeError('LEGACY_ATTACHMENT_SIZE', '单张须为 1 B–8 MiB，每批合计不超过 24 MiB')
        try:
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
                mime = FORMAT_MIME.get(image.format or '')
                if not mime or min(width, height) < 1 or max(width, height) > 12000 or width * height > 40_000_000:
                    raise ValueError()
                image.verify()
        except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise TradeError('LEGACY_ATTACHMENT_CONTENT', '图片内容无效、损坏或尺寸超限') from exc
        result.append({'source_key': key, 'filename': name, 'content': content, 'sha256': hashlib.sha256(content).hexdigest(),
                       'byte_size': len(content), 'mime_type': mime, 'width': width, 'height': height})
    return sorted(result, key=lambda item: item['source_key'])


def identity(body, decoded):
    return {'expected_revision': body['expected_revision'],
            'files': [{key: value for key, value in item.items() if key != 'content'} for item in decoded]}


def preview(session, import_id, body, decoded):
    value = catalog(session, import_id)
    if value['expected_revision'] != body['expected_revision']:
        raise TradeError('LEGACY_REVISION_CONFLICT', '旧档案版本已变化，请重新预览', 409)
    available = {item['key']: item for item in value['items']}
    selected = []
    for file in decoded:
        source = available.get(file['source_key'])
        if source is None or source['status'] != 'needs_file':
            raise TradeError('LEGACY_ATTACHMENT_SELECTION', '所选旧路径不存在、映射不合法或已迁入')
        # Freeze all attachments for this date, so a concurrent deletion/addition
        # cannot silently alter the reviewed merge into the new account.
        existing = [{'id': item.id, 'revision': item.revision, 'sha256': item.sha256, 'deleted_at': item.deleted_at}
                    for item in session.scalars(select(ReviewAttachment).where(
                        ReviewAttachment.account_id == value['account_id'], ReviewAttachment.review_date == source['review_date']).order_by(ReviewAttachment.id))]
        selected.append({**source, 'file': {key: val for key, val in file.items() if key != 'content'}, 'existing': existing})
    value['selected'] = selected
    value['can_apply'] = bool(selected)
    value['preview_sha256'] = digest(value)
    return value


def apply(session, data_dir, import_id, body, decoded):
    if not body.get('acknowledge_limitations'):
        raise TradeError('LEGACY_ACK_REQUIRED', '请核对所选图片与旧路径映射')
    request = identity(body, decoded)
    previous = session.scalar(select(LegacySupplementBatch).where(LegacySupplementBatch.import_id == import_id,
                                                                 LegacySupplementBatch.preview_sha256 == body['expected_preview_sha256']))
    if previous:
        if json.loads(previous.request_json) != request:
            raise TradeError('LEGACY_PREVIEW_CHANGED', '附件确认参数不匹配', 409)
        return json.loads(previous.result_json)
    frozen = preview(session, import_id, body, decoded)
    if frozen['preview_sha256'] != body['expected_preview_sha256']:
        raise TradeError('LEGACY_PREVIEW_CHANGED', '文件、旧来源、日复盘或附件集合已变化，请重新预览', 409)
    row = _row(session, import_id)
    batch_id, now = new_id(), utc_now()
    batch = LegacySupplementBatch(id=batch_id, import_id=import_id, preview_sha256=frozen['preview_sha256'], request_json=canonical(request),
                                  preview_json=canonical(frozen), result_json='{}', created_at=now)
    session.add(batch); session.flush()
    targets = []
    for item, file in zip(frozen['selected'], decoded):
        existing = session.scalar(select(ReviewAttachment).where(ReviewAttachment.account_id == row.account_id,
            ReviewAttachment.review_date == item['review_date'], ReviewAttachment.sha256 == file['sha256'], ReviewAttachment.deleted_at.is_(None)))
        if existing:
            saved, _ = get_attachment(session, data_dir, row.account_id, existing.id)
        else:
            saved = add_attachment(session, data_dir, row.account_id, item['review_date'], file['content'], file['filename'])
            session.flush()
        target = {'type': 'review_attachment', 'id': saved['id'], 'revision': saved['revision'], 'account_id': row.account_id,
                  'review_date': item['review_date'], 'sha256': file['sha256'], 'source_path': item['source_path'], 'reused': existing is not None}
        session.add(LegacySupplementItem(import_id=import_id, source_key=item['key'], batch_id=batch_id, target_json=canonical(target)))
        targets.append({'source_key': item['key'], 'target': target})
    row.revision += 1
    result = {'id': batch_id, 'import_id': import_id, 'revision': row.revision, 'targets': targets, 'created_at': now, 'preview_sha256': frozen['preview_sha256']}
    batch.result_json = canonical(result)
    session.add(AuditEvent(id=new_id(), account_id=row.account_id, entity_type='legacy_attachment_import', entity_id=batch_id,
                           operation='apply', before_json=canonical(frozen), after_json=canonical(result), created_at=now))
    session.flush()
    return result
