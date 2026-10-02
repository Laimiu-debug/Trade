"""Persist complete frozen report snapshots; importing never starts computation."""
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from trade_app.market.service import get_dataset
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research.backtest_service import get_backtest
from trade_app.research.report_domain import digest, encode, freeze_report, strict_json, unpack_report, validate_report
from trade_app.research.report_models import ResearchReport


def _payload(row):
    raw = row.payload_json.encode('utf-8')
    if digest(raw) != row.content_sha256:
        raise TradeError('REPORT_CONTENT_CORRUPT', '已保存报告内容摘要不一致', 409)
    return validate_report(strict_json(raw))


def _metadata(payload):
    run, result, dataset = payload['run'], payload['run']['result'], payload['dataset']
    return {'strategy_id': run['strategy_id'], 'strategy_version': run['strategy_version'], 'symbol': dataset['symbol'],
            'first_date': dataset['first_date'], 'last_date': dataset['last_date'], 'scope': 'single_symbol_backtest',
            'summary': {key: result.get(key) for key in ('initial_capital', 'ending_assets', 'total_return',
                'max_drawdown', 'trade_count', 'win_rate', 'quality_flags')}}


def _data(row, *, detail=False):
    payload = _payload(row) if detail else None
    metadata = _metadata(payload) if payload is not None else strict_json(row.metadata_json.encode(), maximum=65536) if row.metadata_json else {
        'strategy_id': 'metadata_unavailable', 'strategy_version': '—', 'symbol': '—', 'first_date': '—', 'last_date': '—',
        'scope': 'single_symbol_backtest', 'metadata_missing': True,
        'summary': {'initial_capital': None, 'ending_assets': None, 'total_return': None, 'max_drawdown': None,
                    'trade_count': None, 'win_rate': None, 'quality_flags': ['REPORT_METADATA_UNAVAILABLE']}}
    return {'id': row.id, 'title': row.title, 'source_run_id': row.source_run_id,
            'origin': row.origin, 'content_sha256': row.content_sha256, 'created_at': row.created_at,
            **metadata, **({'payload': payload} if detail else {})}


def _row(session, report_id):
    row = session.get(ResearchReport, report_id)
    if row is None or row.deleted:
        raise TradeError('RESEARCH_REPORT_NOT_FOUND', '研究报告不存在或已删除', 404)
    return row


def _store(session, payload, origin):
    raw = encode(payload)
    sha = digest(raw)
    existing = session.scalar(select(ResearchReport).where(ResearchReport.content_sha256 == sha,
                                                         ResearchReport.deleted == 0).limit(1))
    if existing:
        return _data(existing, detail=True)
    row = ResearchReport(id=new_id(), title=payload['title'], source_run_id=payload['run']['id'],
                         origin=origin, content_sha256=sha, payload_json=raw.decode('utf-8'),
                         metadata_json=encode(_metadata(payload)).decode('utf-8'),
                         deleted=0, created_at=utc_now(), deleted_at=None)
    session.add(row)
    session.flush()
    return _data(row, detail=True)


def create_report(session: Session, data_dir: Path, body: dict) -> dict:
    if set(body) != {'backtest_run_id', 'title'}:
        raise TradeError('INVALID_REPORT_FIELDS', '报告只接受来源回测与标题')
    if not isinstance(body['title'], str) or not 1 <= len(body['title'].strip()) <= 120:
        raise TradeError('INVALID_REPORT_TITLE', '报告标题须为 1 至 120 个字符')
    run = get_backtest(session, body['backtest_run_id'])
    if run['state'] != 'succeeded':
        raise TradeError('BACKTEST_NOT_READY', '回测尚未成功完成，不能保存报告', 409)
    dataset = get_dataset(session, data_dir, run['dataset_id'])
    payload = freeze_report(run, dataset, title=body['title'], created_at=utc_now())
    return _store(session, payload, 'local')


def import_report(session: Session, contents: bytes) -> dict:
    return _store(session, unpack_report(contents), 'import')


def list_reports(session: Session) -> list[dict]:
    query = select(ResearchReport).options(load_only(
        ResearchReport.id, ResearchReport.title, ResearchReport.source_run_id, ResearchReport.origin,
        ResearchReport.content_sha256, ResearchReport.created_at, ResearchReport.metadata_json)).where(
            ResearchReport.deleted == 0).order_by(ResearchReport.created_at.desc(), ResearchReport.id).limit(100)
    return [_data(row) for row in session.scalars(query)]


def get_report(session: Session, report_id: str) -> dict:
    return _data(_row(session, report_id), detail=True)


def delete_report(session: Session, report_id: str) -> dict:
    row = _row(session, report_id)
    row.deleted, row.deleted_at = 1, utc_now()
    session.flush()
    return {'id': row.id, 'deleted': True}
