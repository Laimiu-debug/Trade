"""Frozen portfolio reports remain readable independently of source tasks."""
from sqlalchemy import select
from sqlalchemy.orm import load_only

from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.research.portfolio_domain import initial_checkpoint
from trade_app.research.portfolio_models import PortfolioRun, PortfolioChunk
from trade_app.research.portfolio_report_models import PortfolioReport
from trade_app.research.portfolio_report_domain import (FORMAT, SCOPE, validate_portfolio_report,
    unpack_portfolio_report, encode, digest, strict_json, MAX_PACKAGE_BYTES)
from trade_app.research.portfolio_service import get_portfolio


def _payload(row):
    raw = row.payload_json.encode('utf-8')
    if digest(raw) != row.content_sha256:
        raise TradeError('PORTFOLIO_REPORT_CORRUPT', '组合报告内容摘要不一致', 409)
    return validate_portfolio_report(strict_json(raw))


def _metadata(payload):
    context = payload['input']
    return {'mode': context['mode'], 'scope': SCOPE, 'strategy_id': context['strategy_id'],
            'strategy_version': context['strategy_version'], 'symbol_count': len(context['datasets']),
            'symbols': [item['symbol'] for item in context['datasets']], 'first_date': context['calendar'][0],
            'last_date': context['calendar'][-1], 'checkpoint_count': len(payload['chunks']),
            'summary': {key: payload['summary'][key] for key in ('initial_capital', 'ending_assets', 'total_return',
                'max_drawdown', 'buy_count', 'sell_count', 'realized_pnl', 'total_fees', 'quality_flags')}}


def _data(row, *, detail=False):
    payload = _payload(row) if detail else None
    return {'id': row.id, 'title': row.title, 'source_run_id': row.source_run_id, 'origin': row.origin,
            'created_at': row.created_at, 'content_sha256': row.content_sha256,
            **(_metadata(payload) if detail else strict_json(row.metadata_json.encode('utf-8'), maximum=128 * 1024)),
            **({'payload': payload} if detail else {})}


def _row(session, report_id):
    row = session.get(PortfolioReport, report_id)
    if row is None or row.deleted:
        raise TradeError('PORTFOLIO_REPORT_NOT_FOUND', '组合报告不存在或已删除', 404)
    return row


def _store(session, payload, origin):
    raw = encode(payload)
    if len(raw) > MAX_PACKAGE_BYTES:
        raise TradeError('REPORT_PACKAGE_TOO_LARGE', '完整组合报告超过16MiB，不能截断保存；请缩小组合研究区间')
    validate_portfolio_report(strict_json(raw))
    sha = digest(raw)
    existing = session.scalar(select(PortfolioReport).where(PortfolioReport.content_sha256 == sha, PortfolioReport.deleted == 0).limit(1))
    if existing:
        return _data(existing, detail=True)
    row = PortfolioReport(id=new_id(), title=payload['title'], source_run_id=payload['source']['run_id'], origin=origin,
        content_sha256=sha, payload_json=raw.decode('utf-8'), metadata_json=encode(_metadata(payload)).decode('utf-8'),
        deleted=0, created_at=utc_now(), deleted_at=None)
    session.add(row)
    session.flush()
    return _data(row, detail=True)


def create_report(session, body):
    if (set(body) - {'portfolio_run_id', 'title', 'analysis_run_id'} or not {'portfolio_run_id', 'title'} <= set(body)
            or not isinstance(body['title'], str) or not 1 <= len(body['title'].strip()) <= 120):
        raise TradeError('INVALID_PORTFOLIO_REPORT_REQUEST', '请提供组合任务和1至120字报告标题')
    run = get_portfolio(session, body['portfolio_run_id'], full=True)
    if run['state'] != 'succeeded':
        raise TradeError('PORTFOLIO_NOT_READY', '组合尚未成功完成，不能冻结完整报告', 409)
    source = session.get(PortfolioRun, run['id'])
    context = strict_json(source.input_json.encode('utf-8'), maximum=24 * 1024 * 1024)
    chunks = session.scalars(select(PortfolioChunk).where(PortfolioChunk.run_id == source.id).order_by(PortfolioChunk.ordinal)).all()
    payload = {'format': FORMAT, 'version': 1, 'scope': SCOPE, 'title': body['title'].strip(), 'created_at': utc_now(),
        'source': {'run_id': source.id, 'state': 'succeeded', 'created_at': source.created_at, 'updated_at': source.updated_at,
                   'input_sha256': source.input_sha256, 'result_sha256': source.result_sha256},
        'input': context, 'initial_checkpoint': initial_checkpoint(context), 'summary': run['summary'],
        'chunks': [{'ordinal': row.ordinal, 'prior_sha256': row.prior_sha256, 'result_sha256': row.result_sha256,
                    'result': strict_json(row.result_json.encode('utf-8'))} for row in chunks]}
    if body.get('analysis_run_id'):
        from trade_app.research.portfolio_analysis_service import get_analysis
        analysis = get_analysis(session, body['analysis_run_id'])
        if (analysis['state'] != 'succeeded' or analysis['source_run_id'] != source.id
                or analysis['source_result_sha256'] != source.result_sha256):
            raise TradeError('PORTFOLIO_REPORT_ANALYSIS_MISMATCH', '请选择此组合证据对应的已完成分析', 409)
        payload['version'] = 2
        payload['analysis'] = {key: analysis[key] for key in ('id', 'version', 'code_sha256', 'options', 'input_sha256',
            'source_result_sha256', 'result_sha256', 'result')}
    return _store(session, payload, 'local')


def import_report(session, contents):
    return _store(session, unpack_portfolio_report(contents), 'import')


def list_reports(session):
    return [_data(row) for row in session.scalars(select(PortfolioReport).options(load_only(*[
        getattr(PortfolioReport, key) for key in PortfolioReport.__table__.columns.keys() if key != 'payload_json']))
        .where(PortfolioReport.deleted == 0).order_by(PortfolioReport.created_at.desc(), PortfolioReport.id).limit(100))]


def get_report(session, report_id):
    return _data(_row(session, report_id), detail=True)


def delete_report(session, report_id):
    row = _row(session, report_id)
    row.deleted, row.deleted_at = 1, utc_now()
    session.flush()
    return {'id': row.id, 'deleted': True}
