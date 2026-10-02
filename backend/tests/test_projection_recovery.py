"""Restarting after a committed claim must not leave analytics permanently busy."""
from sqlalchemy import select

from trade_app.analytics.models import ProjectionVersion
from trade_app.analytics.service import (
    CALCULATION_VERSION, process_one, projection_status, recover_outdated_projections,
)
from trade_app.platform.db import open_database
from trade_app.platform.models import RebuildRequest
from trade_app.trading.service import create_account, create_flow, create_trade


def test_restart_resumes_claimed_projection_from_current_inputs(tmp_path):
    engine, factory = open_database(tmp_path)
    try:
        with factory.begin() as session:
            account = create_account(session, name='中断恢复')
            create_flow(session, account['id'], {
                'flow_date': '2025-01-01', 'kind': 'initial', 'amount': '10000'})
            session.flush()
            request = session.scalar(select(RebuildRequest))
            request_id = request.id
            request.state = 'running'  # Crash after the claim transaction commits.
        assert process_one(factory) is False
    finally:
        engine.dispose()

    engine, factory = open_database(tmp_path)
    try:
        recover_outdated_projections(factory)
        recover_outdated_projections(factory)  # Repeated startup does not duplicate work.
        with factory.begin() as session:
            request = session.get(RebuildRequest, request_id)
            assert request.state == 'queued'
            assert list(session.scalars(select(RebuildRequest.id))) == [request_id]
            # A save after recovery must be included in the resumed projection.
            create_trade(session, account['id'], {
                'trade_date': '2025-01-02', 'symbol': '600000', 'side': 'buy',
                'quantity': 100, 'price': '10', 'fee': '0', 'fee_mode': 'manual'})
        assert process_one(factory) is True
        assert process_one(factory) is False
        with factory() as session:
            status = projection_status(session, account['id'])
            assert status['status'] == 'fresh'
            assert status['projection_input_revision'] == status['account_input_revision']
            assert status['result']['positions'][0]['quantity'] == 100
            assert session.get(RebuildRequest, request_id).state == 'succeeded'
    finally:
        engine.dispose()


def test_interrupted_old_algorithm_is_requeued_once_and_preserves_history(tmp_path):
    engine, factory = open_database(tmp_path)
    try:
        with factory.begin() as session:
            account = create_account(session, name='旧算法恢复')
            create_flow(session, account['id'], {
                'flow_date': '2025-01-01', 'kind': 'initial', 'amount': '10000'})
            session.flush()
            request = session.scalar(select(RebuildRequest))
            request.state = 'running'
            session.add(ProjectionVersion(
                id='previous-version', account_id=account['id'], input_revision=1,
                calculation_version='nav-rounds-v1', payload_json='{}', state='current',
                created_at='2025-01-01T00:00:00Z'))
        recover_outdated_projections(factory)
        with factory() as session:
            assert len(list(session.scalars(select(RebuildRequest)))) == 1
        assert process_one(factory) is True
        with factory() as session:
            assert session.get(ProjectionVersion, 'previous-version').state == 'historical'
            assert projection_status(session, account['id'])['calculation_version'] == CALCULATION_VERSION
    finally:
        engine.dispose()


def test_recovered_and_queued_requests_share_latest_projection(tmp_path):
    engine, factory = open_database(tmp_path)
    try:
        with factory.begin() as session:
            account = create_account(session, name='恢复后合并结果')
            create_flow(session, account['id'], {
                'flow_date': '2025-01-01', 'kind': 'initial', 'amount': '10000'})
            session.flush()
            session.scalar(select(RebuildRequest)).state = 'running'
            session.flush()
            create_trade(session, account['id'], {
                'trade_date': '2025-01-02', 'symbol': '600000', 'side': 'buy',
                'quantity': 100, 'price': '10', 'fee': '0', 'fee_mode': 'manual'})
            session.flush()
            requests = list(session.scalars(select(RebuildRequest)))
            assert {row.state for row in requests} == {'running', 'queued'}
            assert {row.target_revision for row in requests} == {1, 2}
        recover_outdated_projections(factory)
        recover_outdated_projections(factory)
        assert process_one(factory) is True
        assert process_one(factory) is True
        assert process_one(factory) is False
        with factory() as session:
            projections = list(session.scalars(select(ProjectionVersion)))
            assert len(projections) == 1
            assert projections[0].input_revision == 2
            assert projections[0].state == 'current'
            requests = list(session.scalars(select(RebuildRequest)))
            assert len(requests) == 2
            assert all(row.state == 'succeeded' and row.error is None for row in requests)
            assert projection_status(session, account['id'])['status'] == 'fresh'
        recover_outdated_projections(factory)
        assert process_one(factory) is False
    finally:
        engine.dispose()
