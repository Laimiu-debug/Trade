from __future__ import annotations

import json

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from trade_app.analytics.domain import project_account
from trade_app.analytics.models import ProjectionVersion
from trade_app.platform.models import RebuildRequest
from trade_app.platform.types import new_id, utc_now
from trade_app.trading.models import Account
from trade_app.trading.service import account_or_error, list_trades, nav_inputs
from trade_app.trading.targets import target_config


CALCULATION_VERSION = "nav-rounds-v5"


def projection_status(session: Session, account_id: str) -> dict:
    account = account_or_error(session, account_id, real=True)
    projection = session.scalar(select(ProjectionVersion).where(
        ProjectionVersion.account_id == account_id, ProjectionVersion.state == "current"
    ).order_by(ProjectionVersion.created_at.desc()).limit(1))
    pending = session.scalar(select(RebuildRequest).where(
        RebuildRequest.account_id == account_id,
        RebuildRequest.state.in_(("queued", "running"))
    ).limit(1))
    latest_error = session.scalar(select(RebuildRequest).where(
        RebuildRequest.account_id == account_id, RebuildRequest.state == "failed"
    ).order_by(RebuildRequest.updated_at.desc()).limit(1))
    if projection and projection.input_revision == account.input_revision and projection.calculation_version == CALCULATION_VERSION:
        status = "fresh"
    elif pending:
        status = "recalculating"
    elif latest_error and latest_error.target_revision == account.input_revision:
        status = "failed"
    else:
        status = "stale"
    return {"status": status, "account_input_revision": account.input_revision,
            "projection_version": projection.id if projection else None,
            "projection_input_revision": projection.input_revision if projection else None,
            "calculation_version": projection.calculation_version if projection else None,
            "generated_at": projection.created_at if projection else None,
            "error": latest_error.error if status == "failed" and latest_error else None,
            "result": json.loads(projection.payload_json) if projection else None}


def process_one(factory: sessionmaker) -> bool:
    """The API owns DB writes. This lightweight projection runs in its thread pool."""
    with factory.begin() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        request = session.scalar(select(RebuildRequest).where(
            RebuildRequest.state == "queued"
        ).order_by(RebuildRequest.created_at).limit(1))
        if request is None:
            return False
        request.state = "running"
        request.updated_at = utc_now()
        request_id = request.id
        account_id = request.account_id
    try:
        with factory() as session:
            session.execute(text("BEGIN"))
            account = account_or_error(session, account_id, real=True)
            source_revision = account.input_revision
            flows, snapshots = nav_inputs(session, account_id)
            trades = list_trades(session, account_id)
            targets = target_config(session, account_id)
        payload = project_account(flows, snapshots, trades, targets)
        with factory.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            account = session.get(Account, account_id)
            request = session.get(RebuildRequest, request_id)
            if account is None or request is None:
                raise RuntimeError("Projection input disappeared")
            if account.input_revision != source_revision:
                request.state = "failed"
                request.error = "SUPERSEDED_BY_NEW_INPUT"
                request.updated_at = utc_now()
                if not session.scalar(select(RebuildRequest).where(
                    RebuildRequest.account_id == account_id, RebuildRequest.state == "queued"
                ).limit(1)):
                    session.add(RebuildRequest(id=new_id(), account_id=account_id,
                                               earliest_date=request.earliest_date,
                                               target_revision=account.input_revision,
                                               change_kind="superseded", state="queued",
                                               created_at=utc_now(), updated_at=utc_now()))
                return True
            existing = session.scalar(select(ProjectionVersion).where(
                ProjectionVersion.account_id == account_id,
                ProjectionVersion.input_revision == source_revision,
                ProjectionVersion.calculation_version == CALCULATION_VERSION))
            for old in session.scalars(select(ProjectionVersion).where(
                ProjectionVersion.account_id == account_id, ProjectionVersion.state == "current")):
                if existing is None or old.id != existing.id:
                    old.state = "historical"
            if existing is None:
                session.add(ProjectionVersion(id=new_id(), account_id=account_id,
                                          input_revision=source_revision,
                                          calculation_version=CALCULATION_VERSION,
                                          payload_json=json.dumps(payload, ensure_ascii=False),
                                          state="current", created_at=utc_now()))
            else:
                # Multiple recovered requests can legitimately capture the same
                # latest revision. Reuse that projection, never insert it twice.
                existing.state = "current"
            request.state = "succeeded"
            request.error = None
            request.updated_at = utc_now()
        return True
    except Exception as exc:
        with factory.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            request = session.get(RebuildRequest, request_id)
            if request:
                request.state = "failed"
                request.error = f"{type(exc).__name__}: {exc}"[:500]
                request.updated_at = utc_now()
        return True


def recover_outdated_projections(factory: sessionmaker) -> None:
    """Resume interrupted projections and queue calculation upgrades on startup.

    The application holds the data-directory lock before calling this function,
    so a running request belongs to a previous process, never a live worker.
    """
    with factory.begin() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        for request in session.scalars(select(RebuildRequest).where(RebuildRequest.state == 'running')):
            request.state, request.error, request.updated_at = 'queued', None, utc_now()
        session.flush()
        outdated = session.scalars(select(ProjectionVersion).where(
            ProjectionVersion.state == 'current',
            ProjectionVersion.calculation_version != CALCULATION_VERSION)).all()
        for old in outdated:
            account = session.get(Account, old.account_id)
            if account is None or account.kind != 'real':
                continue
            existing = session.scalar(select(RebuildRequest).where(
                RebuildRequest.account_id == account.id,
                RebuildRequest.state.in_(('queued', 'running'))).limit(1))
            if existing is None:
                session.add(RebuildRequest(id=new_id(), account_id=account.id,
                                           earliest_date='0001-01-01',
                                           target_revision=account.input_revision,
                                           change_kind='calculation_upgrade', state='queued',
                                           created_at=utc_now(), updated_at=utc_now()))
def retry_failed(session: Session, account_id: str) -> dict:
    account = account_or_error(session, account_id, real=True)
    current = session.scalar(select(ProjectionVersion).where(
        ProjectionVersion.account_id == account_id,
        ProjectionVersion.input_revision == account.input_revision,
        ProjectionVersion.calculation_version == CALCULATION_VERSION,
        ProjectionVersion.state == "current"
    ).limit(1))
    if current is not None:
        return {"queued": False, "account_id": account_id, "reason": "already_fresh"}
    queued = session.scalar(select(RebuildRequest).where(
        RebuildRequest.account_id == account_id, RebuildRequest.state.in_(("queued", "running"))
    ).limit(1))
    if queued is None:
        session.add(RebuildRequest(id=new_id(), account_id=account_id,
                                   earliest_date="0001-01-01", target_revision=account.input_revision,
                                   change_kind="retry", state="queued",
                                   created_at=utc_now(), updated_at=utc_now()))
    return {"queued": True, "account_id": account_id}
