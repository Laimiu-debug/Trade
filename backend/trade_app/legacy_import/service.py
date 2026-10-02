"""Transactional cross-domain import coordinator; only new account identities."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from trade_app.legacy_import.models import LegacyImport
from trade_app.legacy_import.supplement_models import LegacySupplementBatch
from trade_app.legacy_import.sim_models import LegacySimPromotion
from trade_app.legacy_import.reader import canonical, digest, read_upload
from trade_app.legacy_import.normalizer import prepare
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.reviews.models import DailyReview, PeriodReview
from trade_app.reviews.score_models import ReviewScoreSheet
from trade_app.trading.models import Account, Trade, PendingTrade, CashFlow, AssetSnapshot, SnapshotPosition
from trade_app.trading.service import create_account, mark_changed


def preview(body: dict) -> tuple[dict, dict]:
    upload = read_upload(body)
    result = prepare(upload, mode=body['mode'], account_name=body.get('account_name', ''))
    return upload, result


def _view(row: LegacyImport, *, detail=False):
    original = json.loads(row.preview_json)
    promotion = json.loads(row.promotion_json) if row.promotion_json else None
    saved = promotion['preview'] if promotion else original
    result = {key: getattr(row, key) for key in ('id', 'source_sha256', 'source_kind', 'filename', 'source_bytes', 'logical_sha256', 'account_id', 'created_at')}
    result.update(revision=row.revision, promoted=bool(promotion), mode=saved['mode'], account_name=saved['account_name'], archive_format=saved['archive_format'],
                  redaction_count=len(saved['redacted_paths']), mapped_record_count=saved['mapped_record_count'])
    session = object_session(row)
    sim = session.get(LegacySimPromotion, row.id) if session else None
    result['sim_promotion'] = {'account_id': sim.account_id, 'created_at': sim.created_at, 'preview_sha256': sim.preview_sha256} if sim else None
    if detail:
        result.update(preview=saved, original_preview=original, promotion=promotion, mappings=json.loads(row.mappings_json), archive=json.loads(row.archive_json))
    return result


def list_imports(session: Session) -> list[dict]:
    return [_view(row) for row in session.scalars(select(LegacyImport).order_by(LegacyImport.created_at.desc()).limit(200))]


def _row(session: Session, import_id: str) -> LegacyImport:
    row = session.get(LegacyImport, import_id)
    if row is None:
        raise TradeError('LEGACY_IMPORT_NOT_FOUND', '旧资料档案不存在', 404)
    return row


def get_import(session: Session, import_id: str) -> dict:
    return _view(_row(session, import_id), detail=True)


def export_archive(session: Session, import_id: str) -> dict:
    row = _row(session, import_id)
    return {'format': 'trade-sanitized-legacy-logical-archive-v1', 'original_bytes_included': False,
            'source': _view(row), 'mapping': json.loads(row.mappings_json),
            'redacted_paths': json.loads(row.preview_json)['redacted_paths'],
            'logical_data': json.loads(row.archive_json),
            'simulation_mapping': _sim_export(session, import_id),
            'supplement_mappings': [{'preview': json.loads(item.preview_json), 'result': json.loads(item.result_json)}
                                   for item in session.scalars(select(LegacySupplementBatch).where(LegacySupplementBatch.import_id == import_id)
                                                               .order_by(LegacySupplementBatch.created_at))]}


def _sim_export(session: Session, import_id: str):
    value = session.get(LegacySimPromotion, import_id)
    return {'preview': json.loads(value.preview_json), 'mappings': json.loads(value.mappings_json), 'account_id': value.account_id} if value else None


def save_import(session: Session, upload: dict, frozen: dict, expected_sha256: str, *, acknowledged: bool, _existing: LegacyImport | None = None) -> dict:
    if not acknowledged:
        raise TradeError('LEGACY_ACK_REQUIRED', '请核对映射、未承接字段和脱敏说明后确认')
    if expected_sha256 != frozen['preview_sha256'] or digest({key: value for key, value in frozen.items() if key != 'preview_sha256'}) != expected_sha256:
        raise TradeError('LEGACY_PREVIEW_CHANGED', '文件、导入方式或账户名称已变化，请重新预览', 409)
    if not frozen['can_import'] or frozen['errors']:
        raise TradeError('LEGACY_VALIDATION_FAILED', '存在未解决的金额、日期或引用问题；请修正源文件副本或选择只读档案')
    if digest(upload['payload']) != frozen['logical_sha256']:
        raise TradeError('LEGACY_PREVIEW_CHANGED', '逻辑档案校验失败', 409)
    if _existing is None and session.scalar(select(LegacyImport).where(LegacyImport.source_sha256 == upload['source_sha256'])):
        raise TradeError('LEGACY_ALREADY_IMPORTED', '该原文件 SHA256 已导入，请在历史档案查看；不会重复建立账户', 409)
    import_id, now, account_id, mappings = _existing.id if _existing else new_id(), utc_now(), None, []
    if frozen['mode'] == 'new_real_account':
        account_id = create_account(session, name=frozen['account_name'])['id']
        account = session.get(Account, account_id)
        items = frozen['records']
        trade_ids, score_sources, days = {}, [], []
        sequence = defaultdict(int)
        # Old code orders ties by integer PK; source JSON order is not authoritative.
        items = sorted(items, key=lambda item: (item['section'], int(item['source_id'])))
        for index, item in enumerate(items):
            section, old_id = item['section'], item['source_id']
            values = dict(item['data'])
            values.pop('source_id')
            new = new_id()
            common = {'id': new, 'account_id': account_id, 'revision': 1, 'created_at': now, 'updated_at': now}
            if section == 'trades':
                day = values['trade_date']
                sequence[day] += 1
                row = Trade(**common, **values, sequence=sequence[day], calculated_fee_minor=values['fee_minor'], fee_source='manual', fee_rule_version=None)
                trade_ids[old_id] = new
                days.append(day)
            elif section == 'pending_trades':
                row = PendingTrade(**common, **values, calculated_fee_minor=0, fee_source='manual',
                                   source='legacy_import', status='pending', confirmed_trade_id=None)
            elif section == 'capital_flows':
                # NAV orders flows by created_at then id. Preserve the old PK order
                # explicitly rather than replacing ties with random new UUID order.
                common['created_at'] = (datetime.fromisoformat(now) + timedelta(microseconds=index)).isoformat()
                row = CashFlow(**common, **values)
                days.append(values['flow_date'])
            elif section == 'snapshots':
                positions = values.pop('positions')
                row = AssetSnapshot(**common, **values)
                row.positions = [SnapshotPosition(id=new_id(), snapshot_id=new, sequence=index + 1, **position) for index, position in enumerate(positions)]
                days.append(values['snap_date'])
            elif section == 'daily_reviews':
                scores, trade_scores = values.pop('scores'), values.pop('trade_scores')
                score_sources.append((values['review_date'], scores, trade_scores))
                row = DailyReview(**common, **values, title='', tomorrow_plan='', overall_summary='', reflection='', tags_json='[]', next_target_date=None)
            else:
                row = PeriodReview(**common, **values)
            session.add(row)
            mappings.append({'section': section, 'source_id': old_id, 'target_id': new, 'target_table': row.__tablename__})
        session.flush()
        for day, scores, trade_scores in score_sources:
            entries = [('daily', day, [], scores)] if scores else []
            entries.extend(('trade', trade_ids[old_id], [trade_ids[old_id]], value) for old_id, value in trade_scores.items())
            for scope, subject, ids, values in entries:
                new = new_id()
                session.add(ReviewScoreSheet(id=new, account_id=account_id, review_date=day, scope=scope,
                                            subject_id=subject, trade_ids_json=canonical(ids), scores_json=canonical(values),
                                            comment='从旧账本导入；AI 建议未由当前模型验证', revision=1, created_at=now, updated_at=now))
                mappings.append({'section': 'review_scores', 'source_id': f'{day}/{scope}/{subject}', 'target_id': new, 'target_table': 'review_score_sheets'})
        if days:
            mark_changed(session, account, min(days), 'legacy_import', 'legacy_import', import_id, None,
                         {'source_sha256': upload['source_sha256'], 'mapped_record_count': len(items)})
    if _existing:
        row = _existing
        row.account_id = account_id
        row.mappings_json = canonical(mappings)
        row.revision += 1
        row.promotion_json = canonical({'preview': frozen, 'created_at': now, 'account_id': account_id})
    else:
        row = LegacyImport(id=import_id, source_sha256=upload['source_sha256'], source_kind=upload['source'],
                           filename=upload['filename'], source_bytes=upload['source_bytes'],
                           logical_sha256=upload['logical_sha256'], archive_json=canonical(upload['payload']),
                           preview_json=canonical(frozen), mappings_json=canonical(mappings), account_id=account_id,
                           created_at=now, revision=1, promotion_json=None)
    session.add(row)
    session.add(AuditEvent(id=new_id(), account_id=account_id, entity_type='legacy_import', entity_id=import_id,
                           operation='promote' if _existing else 'import', before_json=canonical({'revision': row.revision - 1, 'account_id': None}) if _existing else None,
                           after_json=canonical({'source_sha256': upload['source_sha256'], 'logical_sha256': upload['logical_sha256'],
                                                'preview_sha256': expected_sha256, 'account_id': account_id, 'redacted_paths': upload['redacted_paths']}), created_at=now))
    session.flush()
    return _view(row)


def prepare_promotion(session: Session, import_id: str, expected_revision: int, account_name: str, confirmation_hash: str | None = None) -> tuple[dict, dict]:
    row = _row(session, import_id)
    original = json.loads(row.preview_json)
    upload = {'source': row.source_kind, 'filename': row.filename, 'source_sha256': row.source_sha256,
              'source_bytes': row.source_bytes, 'logical_sha256': row.logical_sha256,
              'redacted_paths': original['redacted_paths'], 'payload': json.loads(row.archive_json)}
    if row.account_id:
        previous = json.loads(row.promotion_json)['preview'] if row.promotion_json else None
        if previous and confirmation_hash == previous['preview_sha256'] and account_name.strip() == previous['account_name'] and expected_revision == previous['expected_revision']:
            return upload, previous
        raise TradeError('LEGACY_ALREADY_PROMOTED', '该档案已经关联新账户，不能再次转换或更换账户', 409)
    if row.revision != expected_revision:
        raise TradeError('LEGACY_REVISION_CONFLICT', '档案版本已变化，请刷新后重新预览', 409)
    result = prepare(upload, mode='new_real_account', account_name=account_name)
    result.pop('preview_sha256')
    result.update(promotion_of=import_id, expected_revision=expected_revision)
    result['preview_sha256'] = digest(result)
    return upload, result


def save_promotion(session: Session, import_id: str, upload: dict, frozen: dict, expected_sha256: str, *, acknowledged: bool) -> dict:
    row = _row(session, import_id)
    if not acknowledged:
        raise TradeError('LEGACY_ACK_REQUIRED', '请明确确认转换预览')
    if row.account_id:
        previous = json.loads(row.promotion_json)['preview'] if row.promotion_json else None
        if previous and previous == frozen and previous['preview_sha256'] == expected_sha256:
            return _view(row)
        raise TradeError('LEGACY_ALREADY_PROMOTED', '该档案已经转换，不会重复建立账户', 409)
    if row.revision != frozen.get('expected_revision') or import_id != frozen.get('promotion_of'):
        raise TradeError('LEGACY_REVISION_CONFLICT', '档案版本已变化，请重新预览', 409)
    if row.source_sha256 != upload['source_sha256'] or row.logical_sha256 != upload['logical_sha256']:
        raise TradeError('LEGACY_PREVIEW_CHANGED', '档案来源摘要不匹配', 409)
    return save_import(session, upload, frozen, expected_sha256, acknowledged=True, _existing=row)
