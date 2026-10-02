"""Versioned profile editing; immutable research runs keep their own full copy."""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research.event_profile_models import EventProfile, EventProfileAudit, EventProfileSelection
from trade_app.research.event_profiles import (
    DEFAULT_PROFILE_ID, event_profile_catalog, normalize_profile, profile_sha256,
)


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _entry(snapshot, revision):
    return {**snapshot, 'revision': revision, 'sha256': profile_sha256(snapshot)}


def _system_profiles():
    return {row['profile_id']: row for row in event_profile_catalog()['profiles']}


def get_profile(session: Session, profile_id: str) -> dict:
    system = _system_profiles().get(profile_id)
    if system is not None:
        return _entry(system, 0)
    row = session.get(EventProfile, profile_id)
    if row is None or row.deleted:
        raise TradeError('EVENT_PROFILE_NOT_FOUND', '事件模板不存在或已删除', 404)
    return _entry(json.loads(row.snapshot_json), row.revision)


def active_selection(session: Session) -> dict:
    row = session.get(EventProfileSelection, 1)
    return {'active_profile_id': row.profile_id if row else DEFAULT_PROFILE_ID,
            'active_revision': row.revision if row else 0}


def list_profiles(session: Session) -> dict:
    catalog = event_profile_catalog()
    catalog['profiles'] = [_entry(row, 0) for row in catalog['profiles']]
    catalog['profiles'] += [_entry(json.loads(row.snapshot_json), row.revision)
                            for row in session.scalars(select(EventProfile).where(
                                EventProfile.deleted == 0).order_by(EventProfile.updated_at.desc(), EventProfile.id))]
    return {**catalog, **active_selection(session)}


def freeze_profile(session: Session, profile_id: str | None = None,
                   expected_revision: int | None = None) -> dict:
    entry = get_profile(session, profile_id or active_selection(session)['active_profile_id'])
    if expected_revision is not None and entry['revision'] != expected_revision:
        raise TradeError('EVENT_PROFILE_VERSION_CONFLICT', '事件模板已修改，请刷新后重试', 409)
    revision, digest = entry.pop('revision'), entry.pop('sha256')
    return {'profile_id': entry['profile_id'], 'revision': revision,
            'sha256': digest, 'snapshot': entry}


def _audit(session, profile_id, action, revision, snapshot):
    session.add(EventProfileAudit(id=new_id(), profile_id=profile_id, action=action,
                                 revision=revision, snapshot_json=_encode(snapshot), created_at=utc_now()))


def save_profile(session: Session, body: dict, profile_id: str | None = None) -> dict:
    now = utc_now()
    if profile_id is None:
        profile_id = 'custom_' + new_id()
        row = None
        previous = None
    else:
        if profile_id in _system_profiles():
            raise TradeError('EVENT_PROFILE_READONLY', '系统模板只读，请复制为自定义模板', 409)
        previous = get_profile(session, profile_id)
        row = session.get(EventProfile, profile_id)
        if body.get('expected_revision') != row.revision:
            raise TradeError('EVENT_PROFILE_VERSION_CONFLICT', '事件模板已修改，请刷新后重试', 409)
    snapshot = normalize_profile(body['profile'], profile_id=profile_id, updated_at=now,
                                 fallback_rule_values=previous['rule_values'] if previous else None)
    if row is None:
        row = EventProfile(id=profile_id, revision=1, snapshot_json=_encode(snapshot),
                           deleted=0, created_at=now, updated_at=now)
        session.add(row)
        action = 'create'
    else:
        row.revision += 1
        row.snapshot_json = _encode(snapshot)
        row.updated_at = now
        action = 'update'
    _audit(session, profile_id, action, row.revision, snapshot)
    session.flush()
    return get_profile(session, profile_id)


def apply_profile(session: Session, profile_id: str, body: dict) -> dict:
    profile = get_profile(session, profile_id)
    if body.get('expected_revision') != profile['revision']:
        raise TradeError('EVENT_PROFILE_VERSION_CONFLICT', '事件模板已修改，请刷新后重试', 409)
    current = active_selection(session)
    if body.get('expected_active_revision') != current['active_revision']:
        raise TradeError('EVENT_PROFILE_SELECTION_CONFLICT', '当前模板已切换，请刷新后重试', 409)
    row = session.get(EventProfileSelection, 1)
    if row is None:
        row = EventProfileSelection(id=1, profile_id=profile_id, revision=1, updated_at=utc_now())
        session.add(row)
    else:
        row.profile_id, row.revision, row.updated_at = profile_id, row.revision + 1, utc_now()
    _audit(session, profile_id, 'apply', profile['revision'], {**profile, 'selection_revision': row.revision})
    session.flush()
    return active_selection(session)


def delete_profile(session: Session, profile_id: str, body: dict) -> dict:
    if profile_id in _system_profiles():
        raise TradeError('EVENT_PROFILE_READONLY', '系统模板不能删除', 409)
    profile = get_profile(session, profile_id)
    row = session.get(EventProfile, profile_id)
    if body.get('expected_revision') != row.revision:
        raise TradeError('EVENT_PROFILE_VERSION_CONFLICT', '事件模板已修改，请刷新后重试', 409)
    row.deleted, row.revision, row.updated_at = 1, row.revision + 1, utc_now()
    selection = session.get(EventProfileSelection, 1)
    if selection is not None and selection.profile_id == profile_id:
        if body.get('expected_active_revision') != selection.revision:
            raise TradeError('EVENT_PROFILE_SELECTION_CONFLICT', '当前模板已切换，请刷新后重试', 409)
        selection.profile_id, selection.revision = DEFAULT_PROFILE_ID, selection.revision + 1
        selection.updated_at = utc_now()
    _audit(session, profile_id, 'delete', row.revision, profile)
    session.flush()
    return {'deleted': True, 'profile_id': profile_id, **active_selection(session)}


def profile_history(session: Session, profile_id: str) -> list[dict]:
    if profile_id not in _system_profiles() and session.get(EventProfile, profile_id) is None:
        raise TradeError('EVENT_PROFILE_NOT_FOUND', '事件模板不存在', 404)
    return [{'id': row.id, 'action': row.action, 'revision': row.revision,
             'snapshot': json.loads(row.snapshot_json), 'created_at': row.created_at}
            for row in session.scalars(select(EventProfileAudit).where(
                EventProfileAudit.profile_id == profile_id).order_by(
                    EventProfileAudit.created_at.desc(), EventProfileAudit.id))]
