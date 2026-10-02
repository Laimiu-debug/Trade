from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable, Generator

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from trade_app.analytics.service import projection_status, retry_failed
from trade_app.analytics.performance import performance
from trade_app.api.schemas import AccountCreate, AkShareDailySync, OnlineDailySync, BacktestCreate, CashFlowCreate, DailyInspirationRequest, DailyReviewSave, InspirationCardCreate, MarketDatasetImport, MarketCacheImport, StockAnnotationSave, TdxDayImport, PendingTradeBatch, PendingTradeConfirm, PeriodReviewSave, RealFeeConfigUpdate, ResearchRunCreate, ReviewScoresSave, RoundNoteSave, SimAccountCreate, SimConfigUpdate, SimDraftBatchSubmit, SimDraftCreate, SimDraftSizing, SimDraftUpdate, SimFillCreate, SimFillTagsSave, SimMarketAdvance, SimOpenMatch, SimOrderCreate, SimRecoveryActivate, SimReset, SimReviewTagCreate, SimSettle, SnapshotSave, TargetConfigUpdate, TradeCreate, TradeUpdate
from trade_app.platform.models import IdempotencyKey
from trade_app.platform.backup import create_backup
from trade_app.market.service import close_on_date, get_dataset, import_dataset, list_datasets
from trade_app.market.symbols import market_symbol_key
from trade_app.market.calendar_service import plan_dates
from trade_app.market.tdx import import_tdx_day
from trade_app.market.intraday import read_tdx_intraday
from trade_app.market.diagnostics import market_diagnostics
from trade_app.market.annotations import delete_annotation, get_annotation, list_annotations, save_annotation
from trade_app.market.cache_csv import import_cache_csv
from trade_app.market.akshare_online import prepare_akshare_sync, save_akshare_sync
from trade_app.market.baostock_online import prepare_baostock_sync, save_baostock_sync
from trade_app.market.sync_jobs import create_sync_job, list_sync_jobs, get_sync_job, cancel_sync_job, retry_failed_symbols
from trade_app.market.tdx_float_shares import read_tdx_float_shares
from trade_app.api.schemas import OnlineBatchSync
from trade_app.api.schemas import ScreenerRunCreate, TdxUniverseRunCreate, B1RunCreate, B1UniverseRunCreate, TrendLeaderRunCreate, TrendUniverseRunCreate, LadderRunCreate, LadderUniverseRunCreate, SectorFlowRunCreate, AbnormalUniverseRunCreate, ValuationRunCreate
from trade_app.research.b1_service import prepare_b1_run, save_user_b1_run, list_b1_runs, get_b1_run, promote_b1_hit
from trade_app.research.trend_service import prepare_trend_run, save_trend_run, list_trend_runs, get_trend_run
from trade_app.research.ladder_service import prepare_ladder_run, save_ladder_run, list_ladder_runs, get_ladder_run
from trade_app.research.sector_service import save_sector_run, list_sector_runs, get_sector_run
from trade_app.research.abnormal_service import list_abnormal_runs, get_abnormal_run
from trade_app.research.valuation_service import prepare_valuation_run, save_valuation_run, list_valuation_runs, get_valuation_run, fetch_valuation_quote
from trade_app.research.screener_service import (
    prepare_screener_run, save_screener_run, list_screener_runs, get_screener_run,
    promote_screener_finalist,
)
from trade_app.research.tdx_universe import (
    prepare_universe_job, prepare_b1_universe_job, prepare_trend_universe_job, prepare_ladder_universe_job, prepare_sector_universe_job, prepare_abnormal_universe_job, create_universe_job, list_universe_jobs,
    get_universe_job, cancel_universe_job,
)
from trade_app.research.service import create_run, get_run, list_runs, strategy_catalog
from trade_app.api.schemas import MatrixPoolRunCreate
from trade_app.research.pool_service import create_matrix_run, get_matrix_run, list_matrix_runs, promote_matrix_signal
from trade_app.api.schemas import EventProfileAction, EventProfileSave, StrategyScanCreate
from trade_app.research.event_profile_service import list_profiles, save_profile, delete_profile, apply_profile, profile_history
from trade_app.research.scan_service import get_scan, list_scans, delete_scan
from trade_app.api.schemas import BasketCreate, BasketUpdate, BasketDeleteBatch, BasketEvaluationCreate
from trade_app.research.basket_service import create_basket, get_basket, update_basket, list_baskets, delete_baskets, evaluate_basket, list_evaluations, get_evaluation, list_audit as basket_audit
from trade_app.api.research_export import basket_csv, basket_xlsx, basket_evaluation_xlsx
from trade_app.research.simulation import advance_market_day, match_order_at_open
from trade_app.research.valuation import value_sim_portfolio
from trade_app.research.backtest_service import cancel_backtest, create_backtest, get_backtest, list_backtests, retry_backtest
from trade_app.research.drafts import cancel_draft, create_draft, list_drafts, preview_draft, preview_draft_batch, size_draft, submit_draft, submit_draft_batch, update_draft
from trade_app.platform.types import TradeError, utc_now
from trade_app.reviews.service import get_review, list_reviews, review_gaps, save_review
from trade_app.reviews.periods import delete_period, get_period, list_periods, save_period
from trade_app.trading.service import (
    create_account, create_flow, create_trade, list_accounts, list_flows,
    list_snapshots, list_trades, revise_trade, save_snapshot, void_flow, void_trade,
)
from trade_app.trading.pending import (confirm_pending_batch, confirm_pending_trade,
    create_pending_trade, discard_pending_trade, list_pending_trades, update_pending_trade)
from trade_app.trading.real_fees import fee_preview, fee_settings, update_fee_settings
from trade_app.trading.targets import target_config, update_target_config
from trade_app.research.real_valuation import estimate_real_assets
from trade_app.insights.service import create_card, daily_old_card, delete_card, list_cards
from trade_app.reviews.rounds import get_round_note, list_round_notes, save_round_note
from trade_app.reviews.tags import create_tag, delete_tag, list_assignments, list_tags, save_assignment, tag_stats
from trade_app.reviews.sim_performance import sim_performance
from trade_app.reviews.attachments import MAX_IMAGE_BYTES, add_attachment, delete_attachment, get_attachment, list_attachments
from trade_app.reviews.scores import list_scores, save_scores
from trade_app.reviews.plans import plan_comparison
from trade_app.reviews.markdown_export import export_markdown
from trade_app.reviews.pdf_export import render_review_pdf
from trade_app.api.csv_export import account_csv
from trade_app.api.excel_export import account_xlsx, backtest_xlsx
from trade_app.trading.simulation import (
    activate_sim_recovery, cancel_order, create_order, create_sim_account, fill_order, list_fills as list_sim_fills,
    list_orders as list_sim_orders, portfolio as sim_portfolio, settle as settle_sim,
    reset_sim_account, update_config as update_sim_config,
)


router = APIRouter(prefix="/api/v1")


def read_session(request: Request) -> Generator[Session, None, None]:
    with request.app.state.db_factory() as session:
        yield session


@router.get('/insights/cards')
def get_inspiration_cards(session: Session = Depends(read_session)) -> dict:
    return {'data': list_cards(session)}


@router.post('/insights/cards')
def post_inspiration_card(request: Request, payload: InspirationCardCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'insights/cards', body, lambda session: create_card(session, body))


@router.delete('/insights/cards/{card_id}')
def delete_inspiration_card(request: Request, card_id: str, expected_revision: int) -> JSONResponse:
    body = {'expected_revision': expected_revision}
    return _write(request, f'insights/cards/{card_id}/delete', body,
                  lambda session: delete_card(session, card_id, expected_revision))


@router.post('/insights/daily')
def post_daily_inspiration(request: Request, payload: DailyInspirationRequest) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'insights/daily', body,
                  lambda session: daily_old_card(session, body['day']))


@router.get('/backups/export')
def export_backup(request: Request) -> StreamingResponse:
    try:
        contents = create_backup(request.app.state.data_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise TradeError('BACKUP_INCOMPLETE', '备份前校验失败，请检查数据库与行情文件', 409) from exc
    return StreamingResponse(io.BytesIO(contents), media_type='application/zip',
                             headers={'Content-Disposition': 'attachment; filename="trade-backup.zip"',
                                     'Cache-Control': 'no-store'})


@router.get('/market/datasets')
def get_market_datasets(session: Session = Depends(read_session)) -> dict:
    return {'data': list_datasets(session)}


@router.get('/market/tdx-float-shares')
def get_market_tdx_float_shares(request: Request,
                                symbol: list[str] = Query(min_length=1, max_length=100)) -> dict:
    return {'data': read_tdx_float_shares(request.app.state.tdx_root, symbol)}


@router.get('/market/diagnostics')
def get_market_diagnostics(request: Request, verify: bool = False,
                           session: Session = Depends(read_session)) -> dict:
    return {'data': market_diagnostics(session, request.app.state.data_dir, verify=verify)}


@router.get('/market/annotations')
def get_stock_annotations(session: Session = Depends(read_session)) -> dict:
    return {'data': list_annotations(session)}


@router.get('/market/annotations/{symbol}')
def get_stock_annotation(symbol: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_annotation(session, symbol)}


@router.put('/market/annotations/{symbol}')
def put_stock_annotation(request: Request, symbol: str, payload: StockAnnotationSave) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'market/annotations/{symbol}', body,
                  lambda session: save_annotation(session, symbol, body))


@router.delete('/market/annotations/{symbol}')
def remove_stock_annotation(request: Request, symbol: str, expected_revision: int) -> JSONResponse:
    body = {'expected_revision': expected_revision}
    return _write(request, f'market/annotations/{symbol}/delete', body,
                  lambda session: delete_annotation(session, symbol, expected_revision))


@router.get('/market/close')
def get_market_close(request: Request, dataset_id: str, day: str,
                     session: Session = Depends(read_session)) -> dict:
    return {'data': close_on_date(session, request.app.state.data_dir, dataset_id, day)}


@router.get('/market/datasets/{dataset_id}')
def get_market_dataset(request: Request, dataset_id: str,
                       session: Session = Depends(read_session)) -> dict:
    return {'data': get_dataset(session, request.app.state.data_dir, dataset_id)}


@router.post('/market/datasets')
def post_market_dataset(request: Request, payload: MarketDatasetImport) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'market/datasets', body,
                  lambda session: import_dataset(session, request.app.state.data_dir, body))


@router.post('/market/tdx-import')
def post_tdx_day_import(request: Request, payload: TdxDayImport) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'market/tdx-import', body,
                  lambda session: import_tdx_day(session, request.app.state.data_dir,
                                                 body['symbol'], request.app.state.tdx_root))


@router.get('/market/tdx-intraday')
def get_tdx_intraday(request: Request, symbol: str, day: str) -> dict:
    return {'data': read_tdx_intraday(request.app.state.tdx_root, symbol, day)}


@router.post('/market/cache-import')
def post_market_cache_import(request: Request, payload: MarketCacheImport) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'market/cache-import', body,
                  lambda session: import_cache_csv(session, request.app.state.data_dir,
                                                   body['provider'], body['symbol'],
                                                   request.app.state.cache_roots))


@router.post('/market/akshare-sync')
def post_market_akshare_sync(request: Request, payload: AkShareDailySync) -> JSONResponse:
    return _online_sync(request, payload.model_dump(mode='json'), 'market/akshare-sync',
                        [('akshare', prepare_akshare_sync, save_akshare_sync)])


@router.get('/market/sync-jobs')
def get_market_sync_jobs(session: Session = Depends(read_session)) -> dict:
    return {'data': list_sync_jobs(session)}


@router.get('/market/sync-jobs/{job_id}')
def get_market_sync_job(job_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_sync_job(session, job_id)}


@router.post('/market/sync-jobs')
def post_market_sync_job(request: Request, payload: OnlineBatchSync) -> JSONResponse:
    body = payload.model_dump(mode='json')
    from trade_app.api.settings_service import resolve_market_sources
    # Caller intent, including omitted fields, is the stable request identity.
    missing = sorted({'provider', 'provider_order'} - payload.model_fields_set)
    identity = {**body, **({'_source_defaults': missing} if missing else {})}
    return _write(request, 'market/sync-jobs', identity,
                  lambda session: create_sync_job(session, resolve_market_sources(session, body, payload.model_fields_set)))


@router.post('/market/sync-jobs/{job_id}/cancel')
def post_market_sync_cancel(request: Request, job_id: str) -> JSONResponse:
    return _write(request, f'market/sync-jobs/{job_id}/cancel', {},
                  lambda session: cancel_sync_job(session, job_id))


@router.post('/market/sync-jobs/{job_id}/retry-failed')
def post_market_sync_retry_failed(request: Request, job_id: str) -> JSONResponse:
    return _write(request, f'market/sync-jobs/{job_id}/retry-failed', {},
                  lambda session: retry_failed_symbols(session, job_id))


@router.post('/market/online-sync')
def post_market_online_sync(request: Request, payload: OnlineDailySync) -> JSONResponse:
    from trade_app.api.settings_service import resolve_market_sources
    original = payload.model_dump(mode='json')
    # Scope is based on caller intent, never a mutable global default.
    scope_provider = original['provider']
    missing = sorted({'provider', 'provider_order'} - payload.model_fields_set)
    identity = {key: value for key, value in original.items() if key != 'provider'}
    if missing:
        identity['_source_defaults'] = missing
    with request.app.state.db_factory() as session:
        body = resolve_market_sources(session, original, payload.model_fields_set)
    provider = body.pop('provider')
    choices = {'akshare': (prepare_akshare_sync, save_akshare_sync),
               'baostock': (prepare_baostock_sync, save_baostock_sync)}
    priority = body['provider_order'] if provider == 'auto' else [provider]
    return _online_sync(request, body, f'market/online-sync/{scope_provider}',
                        [(name, *choices[name]) for name in priority], identity_body=identity)


def _online_sync(request: Request, body: dict, scope: str,
                 candidates: list[tuple[str, Callable, Callable]], *, identity_body: dict | None = None) -> JSONResponse:
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key or len(key) > 128:
        raise TradeError('IDEMPOTENCY_KEY_REQUIRED', '请提供有效的 Idempotency-Key', 400)
    request_identity = body if identity_body is None else identity_body
    normalized = json.dumps(request_identity, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    request_hash = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    with request.app.state.db_factory() as session:
        existing = session.get(IdempotencyKey, (scope, key))
        if existing:
            if existing.request_hash != request_hash:
                raise TradeError('IDEMPOTENCY_CONFLICT', '相同请求键对应不同内容', 409)
            return JSONResponse(json.loads(existing.response_json), status_code=existing.status_code)
    failures = []
    for index, (name, prepare, save) in enumerate(candidates):
        try:
            with request.app.state.db_factory() as session:
                prepared = prepare(session, request.app.state.data_dir, body)
            break
        except TradeError as exc:
            if index == len(candidates) - 1 or exc.code not in {
                'MARKET_PROVIDER_TIMEOUT', 'MARKET_PROVIDER_FAILED',
                'MARKET_PROVIDER_UNAVAILABLE', 'MARKET_PROVIDER_UNSUPPORTED',
                'MARKET_PROVIDER_EMPTY', 'MARKET_PROVIDER_TOO_LARGE',
                'INVALID_PROVIDER_DATA', 'INVALID_PROVIDER_VOLUME',
            }:
                raise
            failures.append({'provider': name, 'code': exc.code})
    return _write(request, scope, request_identity,
                  lambda session: {**save(session, request.app.state.data_dir, prepared),
                                   'actual_provider': name,
                                   'attempted_providers': [item[0] for item in candidates[:index + 1]],
                                   'fallback_errors': failures,
                                   **({'source_defaults': body['source_defaults']} if 'source_defaults' in body else {})})


@router.get('/research/strategies')
def get_research_strategies(session: Session = Depends(read_session)) -> dict:
    from trade_app.research.registry_service import catalog_with_registry
    return {'data': catalog_with_registry(session)}


@router.get('/research/screener-runs')
def get_research_screener_runs(session: Session = Depends(read_session)) -> dict:
    return {'data': list_screener_runs(session)}


@router.get('/research/b1-runs')
def get_research_b1_runs(session: Session = Depends(read_session)) -> dict:
    return {'data': list_b1_runs(session)}


@router.get('/research/trend-leader-runs')
def get_research_trend_leader_runs(session: Session = Depends(read_session)) -> dict:
    return {'data': list_trend_runs(session)}


@router.get('/research/trend-leader-runs/{run_id}')
def get_research_trend_leader_run(run_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_trend_run(session, run_id)}


@router.post('/research/trend-leader-runs')
def post_research_trend_leader_run(request: Request, payload: TrendLeaderRunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    with request.app.state.db_factory() as session:
        prepared = prepare_trend_run(session, request.app.state.data_dir,
                                     request.app.state.tdx_root, body)
    return _write(request, 'research/trend-leader-runs', body,
                  lambda session: save_trend_run(session, prepared))


@router.post('/research/tdx-trend-jobs')
def post_research_tdx_trend_job(request: Request, payload: TrendUniverseRunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    scope = 'research/tdx-trend-jobs'
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key or len(key) > 128:
        raise TradeError('IDEMPOTENCY_KEY_REQUIRED', '请提供有效的 Idempotency-Key', 400)
    request_hash = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':')).encode()).hexdigest()
    with request.app.state.db_factory() as session:
        existing = session.get(IdempotencyKey, (scope, key))
        if existing:
            if existing.request_hash != request_hash:
                raise TradeError('IDEMPOTENCY_CONFLICT', '相同请求键对应不同内容', 409)
            return JSONResponse(json.loads(existing.response_json), status_code=existing.status_code)
    prepared = prepare_trend_universe_job(request.app.state.tdx_root, body)
    return _write(request, scope, body, lambda session: create_universe_job(session, prepared))


@router.get('/research/limit-up-ladder-runs')
def get_research_ladder_runs(session: Session = Depends(read_session)) -> dict:
    return {'data': list_ladder_runs(session)}


@router.get('/research/limit-up-ladder-runs/{run_id}')
def get_research_ladder_run(run_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_ladder_run(session, run_id)}


@router.post('/research/limit-up-ladder-runs')
def post_research_ladder_run(request: Request, payload: LadderRunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    with request.app.state.db_factory() as session:
        prepared = prepare_ladder_run(session, request.app.state.data_dir,
                                      request.app.state.tdx_root, body)
    return _write(request, 'research/limit-up-ladder-runs', body,
                  lambda session: save_ladder_run(session, prepared))


@router.post('/research/tdx-ladder-jobs')
def post_research_tdx_ladder_job(request: Request, payload: LadderUniverseRunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    scope = 'research/tdx-ladder-jobs'
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key or len(key) > 128:
        raise TradeError('IDEMPOTENCY_KEY_REQUIRED', '请提供有效的 Idempotency-Key', 400)
    request_hash = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':')).encode()).hexdigest()
    with request.app.state.db_factory() as session:
        existing = session.get(IdempotencyKey, (scope, key))
        if existing:
            if existing.request_hash != request_hash:
                raise TradeError('IDEMPOTENCY_CONFLICT', '相同请求键对应不同内容', 409)
            return JSONResponse(json.loads(existing.response_json), status_code=existing.status_code)
    prepared = prepare_ladder_universe_job(request.app.state.tdx_root, body)
    return _write(request, scope, body, lambda session: create_universe_job(session, prepared))


@router.get('/research/sector-flow-runs')
def get_research_sector_flow_runs(session: Session = Depends(read_session)) -> dict:
    return {'data': list_sector_runs(session)}


@router.get('/research/sector-flow-runs/{run_id}')
def get_research_sector_flow_run(run_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_sector_run(session, run_id)}


@router.post('/research/tdx-sector-flow-jobs')
def post_research_tdx_sector_flow_job(request: Request, payload: SectorFlowRunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    scope = 'research/tdx-sector-flow-jobs'
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key or len(key) > 128:
        raise TradeError('IDEMPOTENCY_KEY_REQUIRED', '请提供有效的 Idempotency-Key', 400)
    request_hash = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':')).encode()).hexdigest()
    with request.app.state.db_factory() as session:
        existing = session.get(IdempotencyKey, (scope, key))
        if existing:
            if existing.request_hash != request_hash:
                raise TradeError('IDEMPOTENCY_CONFLICT', '相同请求键对应不同内容', 409)
            return JSONResponse(json.loads(existing.response_json), status_code=existing.status_code)
    prepared = prepare_sector_universe_job(request.app.state.tdx_root, body)
    return _write(request, scope, body, lambda session: create_universe_job(session, prepared))


@router.get('/research/abnormal-runs')
def get_research_abnormal_runs(session: Session = Depends(read_session)) -> dict:
    return {'data': list_abnormal_runs(session)}


@router.get('/research/abnormal-runs/{run_id}')
def get_research_abnormal_run(run_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_abnormal_run(session, run_id)}


@router.post('/research/tdx-abnormal-jobs')
def post_research_tdx_abnormal_job(request: Request, payload: AbnormalUniverseRunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    scope = 'research/tdx-abnormal-jobs'
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key or len(key) > 128:
        raise TradeError('IDEMPOTENCY_KEY_REQUIRED', '请提供有效的 Idempotency-Key', 400)
    request_hash = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':')).encode()).hexdigest()
    with request.app.state.db_factory() as session:
        existing = session.get(IdempotencyKey, (scope, key))
        if existing:
            if existing.request_hash != request_hash:
                raise TradeError('IDEMPOTENCY_CONFLICT', '相同请求键对应不同内容', 409)
            return JSONResponse(json.loads(existing.response_json), status_code=existing.status_code)
    return _write(request, scope, body,
                  lambda session: create_universe_job(session, prepare_abnormal_universe_job(
                      session, request.app.state.data_dir, request.app.state.tdx_root, body)))


@router.get('/research/valuation-quote')
def get_research_valuation_quote(request: Request, symbol: str = Query(min_length=6, max_length=8),
                                 dataset_id: str | None = Query(default=None),
                                 session: Session = Depends(read_session)) -> dict:
    return {'data': fetch_valuation_quote(session, request.app.state.data_dir, symbol, dataset_id)}


@router.get('/research/valuation-runs')
def get_research_valuation_runs(session: Session = Depends(read_session)) -> dict:
    return {'data': list_valuation_runs(session)}


@router.get('/research/valuation-runs/{run_id}')
def get_research_valuation_run(run_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_valuation_run(session, run_id)}


@router.post('/research/valuation-runs')
def post_research_valuation_run(request: Request, payload: ValuationRunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    prepared = prepare_valuation_run(body)
    return _write(request, 'research/valuation-runs', body,
                  lambda session: save_valuation_run(session, prepared))


@router.get('/research/b1-runs/{run_id}')
def get_research_b1_run(run_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_b1_run(session, run_id)}


@router.post('/research/b1-runs/{run_id}/signals/{symbol}')
def post_research_b1_signal(request: Request, run_id: str, symbol: str) -> JSONResponse:
    return _write(request, f'research/b1-runs/{run_id}/signals/{symbol}', {},
                  lambda session: promote_b1_hit(session, request.app.state.data_dir,
                                                 run_id, symbol))


@router.post('/research/b1-runs')
def post_research_b1_run(request: Request, payload: B1RunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    with request.app.state.db_factory() as session:
        prepared = prepare_b1_run(session, request.app.state.data_dir, body)
    return _write(request, 'research/b1-runs', body,
                  lambda session: save_user_b1_run(session, prepared))


@router.get('/research/tdx-universe-jobs')
def get_research_tdx_universe_jobs(session: Session = Depends(read_session)) -> dict:
    return {'data': list_universe_jobs(session)}


@router.post('/research/tdx-b1-jobs')
def post_research_tdx_b1_job(request: Request, payload: B1UniverseRunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    scope = 'research/tdx-b1-jobs'
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key or len(key) > 128:
        raise TradeError('IDEMPOTENCY_KEY_REQUIRED', '请提供有效的 Idempotency-Key', 400)
    request_hash = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':')).encode()).hexdigest()
    with request.app.state.db_factory() as session:
        existing = session.get(IdempotencyKey, (scope, key))
        if existing:
            if existing.request_hash != request_hash:
                raise TradeError('IDEMPOTENCY_CONFLICT', '相同请求键对应不同内容', 409)
            return JSONResponse(json.loads(existing.response_json), status_code=existing.status_code)
    prepared = prepare_b1_universe_job(request.app.state.tdx_root, body)
    return _write(request, scope, body, lambda session: create_universe_job(session, prepared))


@router.get('/research/tdx-universe-jobs/{job_id}')
def get_research_tdx_universe_job(job_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_universe_job(session, job_id)}


@router.post('/research/tdx-universe-jobs')
def post_research_tdx_universe_job(request: Request, payload: TdxUniverseRunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    scope = 'research/tdx-universe-jobs'
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key or len(key) > 128:
        raise TradeError('IDEMPOTENCY_KEY_REQUIRED', '请提供有效的 Idempotency-Key', 400)
    request_hash = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':')).encode()).hexdigest()
    with request.app.state.db_factory() as session:
        existing = session.get(IdempotencyKey, (scope, key))
        if existing:
            if existing.request_hash != request_hash:
                raise TradeError('IDEMPOTENCY_CONFLICT', '相同请求键对应不同内容', 409)
            return JSONResponse(json.loads(existing.response_json), status_code=existing.status_code)
    prepared = prepare_universe_job(request.app.state.tdx_root, body)
    return _write(request, scope, body, lambda session: create_universe_job(session, prepared))


@router.post('/research/tdx-universe-jobs/{job_id}/cancel')
def post_research_tdx_universe_cancel(request: Request, job_id: str) -> JSONResponse:
    return _write(request, f'research/tdx-universe-jobs/{job_id}/cancel', {},
                  lambda session: cancel_universe_job(session, job_id))


@router.get('/research/screener-runs/{run_id}')
def get_research_screener_run(run_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_screener_run(session, run_id)}


@router.post('/research/screener-runs/{run_id}/signals/{dataset_id}')
def post_research_screener_signal(request: Request, run_id: str,
                                  dataset_id: str) -> JSONResponse:
    return _write(request, f'research/screener-runs/{run_id}/signals/{dataset_id}', {},
                  lambda session: promote_screener_finalist(
                      session, request.app.state.data_dir, run_id, dataset_id))


@router.post('/research/screener-runs')
def post_research_screener_run(request: Request, payload: ScreenerRunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    scope = 'research/screener-runs'
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key or len(key) > 128:
        raise TradeError('IDEMPOTENCY_KEY_REQUIRED', '请提供有效的 Idempotency-Key', 400)
    request_hash = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':')).encode()).hexdigest()
    with request.app.state.db_factory() as session:
        existing = session.get(IdempotencyKey, (scope, key))
        if existing:
            if existing.request_hash != request_hash:
                raise TradeError('IDEMPOTENCY_CONFLICT', '相同请求键对应不同内容', 409)
            return JSONResponse(json.loads(existing.response_json), status_code=existing.status_code)
        prepared = prepare_screener_run(session, request.app.state.data_dir, body,
                                        request.app.state.tdx_root)
    return _write(request, scope, body, lambda session: save_screener_run(session, prepared))


@router.get('/research/runs')
def get_research_runs(session: Session = Depends(read_session)) -> dict:
    return {'data': list_runs(session)}


@router.get('/research/scans')
def get_strategy_scans(session: Session = Depends(read_session)) -> dict:
    return {'data': list_scans(session)}


@router.get('/research/baskets')
def get_signal_baskets(include_deleted: bool = False, strategy_id: str | None = None,
                       name: str | None = None, session: Session = Depends(read_session)) -> dict:
    return {'data': list_baskets(session, include_deleted=include_deleted, strategy_id=strategy_id, name=name)}


@router.post('/research/baskets')
def post_signal_basket(request: Request, payload: BasketCreate) -> JSONResponse:
    body = payload.model_dump(mode='json', exclude_none=True)
    return _write(request, 'research/baskets', body,
                  lambda session: create_basket(session, request.app.state.data_dir, body))


@router.post('/research/baskets/delete-batch')
def remove_signal_baskets(request: Request, payload: BasketDeleteBatch) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'research/baskets/delete-batch', body, lambda session: delete_baskets(session, body))


@router.get('/research/baskets/{basket_id}')
def get_signal_basket(basket_id: str, include_deleted: bool = False,
                       session: Session = Depends(read_session)) -> dict:
    return {'data': get_basket(session, basket_id, include_deleted=include_deleted)}


@router.put('/research/baskets/{basket_id}')
def put_signal_basket(request: Request, basket_id: str, payload: BasketUpdate) -> JSONResponse:
    body = payload.model_dump(mode='json', exclude_unset=True)
    return _write(request, f'research/baskets/{basket_id}/update', body,
                  lambda session: update_basket(session, request.app.state.data_dir, basket_id, body))


@router.get('/research/baskets/{basket_id}/audit')
def get_signal_basket_audit(basket_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': basket_audit(session, basket_id)}


@router.get('/research/baskets/{basket_id}/evaluations')
def get_basket_evaluations(basket_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': list_evaluations(session, basket_id)}


@router.get('/research/baskets/{basket_id}/evaluations/{evaluation_id}')
def get_basket_evaluation(basket_id: str, evaluation_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_evaluation(session, basket_id, evaluation_id)}


@router.post('/research/baskets/{basket_id}/evaluations')
def post_basket_evaluation(request: Request, basket_id: str, payload: BasketEvaluationCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'research/baskets/{basket_id}/evaluations', body,
                  lambda session: evaluate_basket(session, request.app.state.data_dir, basket_id, body))


def _research_download(content: bytes, filename: str, media_type: str) -> Response:
    return Response(content, media_type=media_type,
                    headers={'Content-Disposition': f'attachment; filename="{filename}"',
                             'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})


@router.get('/research/baskets/{basket_id}/export.csv')
def download_basket_csv(basket_id: str, session: Session = Depends(read_session)) -> Response:
    return _research_download(basket_csv(session, basket_id), f'trade-basket-{basket_id}.csv', 'text/csv; charset=utf-8')


@router.get('/research/baskets/{basket_id}/export.xlsx')
def download_basket_xlsx(basket_id: str, session: Session = Depends(read_session)) -> Response:
    return _research_download(basket_xlsx(session, basket_id), f'trade-basket-{basket_id}.xlsx',
                              'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@router.get('/research/baskets/{basket_id}/evaluations/{evaluation_id}/export.xlsx')
def download_basket_evaluation_xlsx(basket_id: str, evaluation_id: str,
                                    session: Session = Depends(read_session)) -> Response:
    return _research_download(basket_evaluation_xlsx(session, basket_id, evaluation_id),
                              f'trade-basket-report-{evaluation_id[:16]}.xlsx',
                              'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@router.get('/research/scans/{scan_id}')
def get_strategy_scan(scan_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_scan(session, scan_id)}


@router.post('/research/scans')
def post_strategy_scan(request: Request, payload: StrategyScanCreate) -> JSONResponse:
    raise TradeError('ASYNC_SCAN_REQUIRED', '同步扫描入口已停用，请通过 /api/v1/research/scan-jobs 创建后台扫描任务', 409)


@router.delete('/research/scans/{scan_id}')
def remove_strategy_scan(request: Request, scan_id: str) -> JSONResponse:
    return _write(request, f'research/scans/{scan_id}/delete', {}, lambda session: delete_scan(session, scan_id))


@router.get('/research/event-profiles')
def get_event_profiles(session: Session = Depends(read_session)) -> dict:
    return {'data': list_profiles(session)}


@router.get('/research/event-profiles/{profile_id}/history')
def get_event_profile_history(profile_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': profile_history(session, profile_id)}


@router.post('/research/event-profiles')
def post_event_profile(request: Request, payload: EventProfileSave) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'research/event-profiles', body, lambda session: save_profile(session, body))


@router.put('/research/event-profiles/{profile_id}')
def put_event_profile(request: Request, profile_id: str, payload: EventProfileSave) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'research/event-profiles/{profile_id}/update', body,
                  lambda session: save_profile(session, body, profile_id))


@router.delete('/research/event-profiles/{profile_id}')
def remove_event_profile(request: Request, profile_id: str, payload: EventProfileAction) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'research/event-profiles/{profile_id}/delete', body,
                  lambda session: delete_profile(session, profile_id, body))


@router.post('/research/event-profiles/{profile_id}/apply')
def post_event_profile_apply(request: Request, profile_id: str, payload: EventProfileAction) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'research/event-profiles/{profile_id}/apply', body,
                  lambda session: apply_profile(session, profile_id, body))


@router.get('/research/matrix-runs')
def get_research_matrix_runs(session: Session = Depends(read_session)) -> dict:
    return {'data': list_matrix_runs(session)}


@router.get('/research/matrix-runs/{run_id}')
def get_research_matrix_run(run_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_matrix_run(session, run_id)}


@router.post('/research/matrix-runs')
def post_research_matrix_run(request: Request, payload: MatrixPoolRunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'research/matrix-runs', body, lambda session: create_matrix_run(session, body))


@router.post('/research/matrix-runs/{run_id}/signals/{dataset_id}')
def post_research_matrix_signal(request: Request, run_id: str, dataset_id: str) -> JSONResponse:
    return _write(request, f'research/matrix-runs/{run_id}/signals/{dataset_id}', {},
                  lambda session: promote_matrix_signal(session, request.app.state.data_dir, run_id, dataset_id))


@router.get('/research/runs/{run_id}')
def get_research_run(run_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_run(session, run_id)}


@router.post('/research/runs')
def post_research_run(request: Request, payload: ResearchRunCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'research/runs', body,
                  lambda session: create_run(session, request.app.state.data_dir, body))


@router.get('/backtests')
def get_backtests(session: Session = Depends(read_session)) -> dict:
    return {'data': list_backtests(session)}


@router.get('/backtests/{run_id}')
def get_backtest_run(run_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': get_backtest(session, run_id)}


@router.get('/backtests/{run_id}/export.xlsx')
def download_backtest_xlsx(run_id: str,
                           session: Session = Depends(read_session)) -> Response:
    content = backtest_xlsx(session, run_id)
    return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="trade-backtest-{run_id[:16]}.xlsx"',
                             'X-Content-Type-Options': 'nosniff',
                             'Cache-Control': 'private, no-store'})


@router.post('/backtests')
def post_backtest(request: Request, payload: BacktestCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'backtests', body,
                  lambda session: create_backtest(session, body))


@router.post('/backtests/{run_id}/cancel')
def post_backtest_cancel(request: Request, run_id: str) -> JSONResponse:
    return _write(request, f'backtests/{run_id}/cancel', {},
                  lambda session: cancel_backtest(session, run_id))


@router.post('/backtests/{run_id}/retry')
def post_backtest_retry(request: Request, run_id: str) -> JSONResponse:
    return _write(request, f'backtests/{run_id}/retry', {},
                  lambda session: retry_backtest(session, run_id))


def _write(request: Request, scope: str, body: dict, operation: Callable[[Session], dict]) -> JSONResponse:
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key or len(key) > 128:
        raise TradeError("IDEMPOTENCY_KEY_REQUIRED", "请提供有效的 Idempotency-Key", 400)
    normalized = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    with request.app.state.db_factory.begin() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        existing = session.get(IdempotencyKey, (scope, key))
        if existing:
            if existing.request_hash != request_hash:
                raise TradeError("IDEMPOTENCY_CONFLICT", "相同请求键对应不同内容", 409)
            return JSONResponse(json.loads(existing.response_json), status_code=existing.status_code)
        result = operation(session)
        response = {"data": result}
        status = 200
        session.add(IdempotencyKey(scope=scope, key=key, request_hash=request_hash,
                                   response_json=json.dumps(response, ensure_ascii=False),
                                   status_code=status, created_at=utc_now()))
    return JSONResponse(response, status_code=status)


@router.get("/accounts")
def get_accounts(session: Session = Depends(read_session)) -> dict:
    return {"data": list_accounts(session)}


@router.post("/accounts")
def post_account(request: Request, payload: AccountCreate) -> JSONResponse:
    body = payload.model_dump(mode="json")
    return _write(request, "accounts", body, lambda session: create_account(session, **body))


@router.post('/sim-accounts')
def post_sim_account(request: Request, payload: SimAccountCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, 'sim-accounts', body,
                  lambda session: create_sim_account(session, body))


@router.post('/sim-accounts/{account_id}/reset')
def post_sim_reset(request: Request, account_id: str, payload: SimReset) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/reset', body,
                  lambda session: reset_sim_account(session, account_id, body))


@router.post('/sim-accounts/{account_id}/activate-recovery')
def post_sim_recovery_activate(request: Request, account_id: str,
                               payload: SimRecoveryActivate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/activate-recovery', body,
                  lambda session: activate_sim_recovery(
                      session, account_id, body['expected_wallet_revision']))


@router.get('/sim-accounts/{account_id}/portfolio')
def get_sim_portfolio(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': sim_portfolio(session, account_id)}


@router.get('/sim-accounts/{account_id}/valuation')
def get_sim_valuation(request: Request, account_id: str, decision_at: str,
                      dataset_id: list[str] = Query(default=[]), strict: bool = True,
                      session: Session = Depends(read_session)) -> dict:
    return {'data': value_sim_portfolio(session, request.app.state.data_dir,
                                        account_id, dataset_id, decision_at, strict)}


@router.get('/sim-accounts/{account_id}/orders')
def get_sim_orders(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': list_sim_orders(session, account_id)}


@router.get('/sim-accounts/{account_id}/drafts')
def get_sim_drafts(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': list_drafts(session, account_id)}


@router.post('/sim-accounts/{account_id}/drafts/size')
def post_sim_draft_size(account_id: str, payload: SimDraftSizing,
                        session: Session = Depends(read_session)) -> dict:
    return {'data': size_draft(session, account_id, payload.model_dump())}


@router.post('/sim-accounts/{account_id}/drafts')
def post_sim_draft(request: Request, account_id: str, payload: SimDraftCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/drafts', body,
                  lambda session: create_draft(session, account_id, body))


@router.get('/sim-accounts/{account_id}/drafts/preview-batch')
def get_sim_draft_batch_preview(account_id: str, draft_id: list[str] = Query(default=[]),
                                session: Session = Depends(read_session)) -> dict:
    return {'data': preview_draft_batch(session, account_id, draft_id)}


@router.post('/sim-accounts/{account_id}/drafts/submit-batch')
def post_sim_draft_batch_submit(request: Request, account_id: str,
                                payload: SimDraftBatchSubmit) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/drafts/submit-batch', body,
                  lambda session: submit_draft_batch(session, account_id, body))


@router.put('/sim-accounts/{account_id}/drafts/{draft_id}')
def put_sim_draft(request: Request, account_id: str, draft_id: str,
                  payload: SimDraftUpdate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/drafts/{draft_id}', body,
                  lambda session: update_draft(session, account_id, draft_id, body))


@router.get('/sim-accounts/{account_id}/drafts/{draft_id}/preview')
def get_sim_draft_preview(account_id: str, draft_id: str,
                          session: Session = Depends(read_session)) -> dict:
    return {'data': preview_draft(session, account_id, draft_id)}


@router.post('/sim-accounts/{account_id}/drafts/{draft_id}/submit')
def post_sim_draft_submit(request: Request, account_id: str, draft_id: str,
                          expected_revision: int, expected_wallet_revision: int,
                          expected_config_version: int) -> JSONResponse:
    body = {'expected_revision': expected_revision,
            'expected_wallet_revision': expected_wallet_revision,
            'expected_config_version': expected_config_version}
    return _write(request, f'sim-accounts/{account_id}/drafts/{draft_id}/submit', body,
                  lambda session: submit_draft(session, account_id, draft_id, expected_revision,
                                               expected_wallet_revision, expected_config_version))


@router.post('/sim-accounts/{account_id}/drafts/{draft_id}/cancel')
def post_sim_draft_cancel(request: Request, account_id: str, draft_id: str,
                          expected_revision: int) -> JSONResponse:
    body = {'expected_revision': expected_revision}
    return _write(request, f'sim-accounts/{account_id}/drafts/{draft_id}/cancel', body,
                  lambda session: cancel_draft(session, account_id, draft_id, expected_revision))


@router.get('/sim-accounts/{account_id}/fills')
def get_sim_fills(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': list_sim_fills(session, account_id)}


@router.get('/sim-accounts/{account_id}/review-tags')
def get_sim_review_tags(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': list_tags(session, account_id)}


@router.post('/sim-accounts/{account_id}/review-tags')
def post_sim_review_tag(request: Request, account_id: str,
                        payload: SimReviewTagCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/review-tags', body,
                  lambda session: create_tag(session, account_id, body))


@router.delete('/sim-accounts/{account_id}/review-tags/{tag_id}')
def delete_sim_review_tag(request: Request, account_id: str, tag_id: str,
                          expected_revision: int) -> JSONResponse:
    body = {'expected_revision': expected_revision}
    return _write(request, f'sim-accounts/{account_id}/review-tags/{tag_id}/delete', body,
                  lambda session: delete_tag(session, account_id, tag_id, expected_revision))


@router.get('/sim-accounts/{account_id}/fill-tags')
def get_sim_fill_tags(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': list_assignments(session, account_id)}


@router.get('/sim-accounts/{account_id}/performance')
def get_sim_performance(account_id: str, date_basis: str = 'sell', date_from: str | None = None,
                        date_to: str | None = None, session: Session = Depends(read_session)) -> dict:
    return {'data': sim_performance(session, account_id, date_basis, date_from, date_to)}


@router.put('/sim-accounts/{account_id}/fills/{fill_id}/tags')
def put_sim_fill_tags(request: Request, account_id: str, fill_id: str,
                      payload: SimFillTagsSave) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/fills/{fill_id}/tags', body,
                  lambda session: save_assignment(session, account_id, fill_id, body))


@router.get('/sim-accounts/{account_id}/tag-stats')
def get_sim_tag_stats(account_id: str, date_from: str | None = None,
                      date_to: str | None = None,
                      session: Session = Depends(read_session)) -> dict:
    return {'data': tag_stats(session, account_id, date_from, date_to)}


@router.post('/sim-accounts/{account_id}/orders')
def post_sim_order(request: Request, account_id: str, payload: SimOrderCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/orders', body,
                  lambda session: create_order(session, account_id, body))


@router.post('/sim-accounts/{account_id}/orders/{order_id}/cancel')
def post_sim_cancel(request: Request, account_id: str, order_id: str,
                    expected_revision: int) -> JSONResponse:
    body = {'expected_revision': expected_revision}
    return _write(request, f'sim-accounts/{account_id}/orders/{order_id}/cancel', body,
                  lambda session: cancel_order(session, account_id, order_id, expected_revision))


@router.post('/sim-accounts/{account_id}/orders/{order_id}/fill')
def post_sim_fill(request: Request, account_id: str, order_id: str,
                  payload: SimFillCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/orders/{order_id}/fill', body,
                  lambda session: fill_order(session, account_id, order_id, body))


@router.post('/sim-accounts/{account_id}/orders/{order_id}/match-open')
def post_sim_open_match(request: Request, account_id: str, order_id: str,
                        payload: SimOpenMatch) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/orders/{order_id}/match-open', body,
                  lambda session: match_order_at_open(
                      session, request.app.state.data_dir, account_id, order_id, body))


@router.post('/sim-accounts/{account_id}/settle')
def post_sim_settle(request: Request, account_id: str, payload: SimSettle) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/settle', body,
                  lambda session: settle_sim(session, account_id, body['to_date']))


@router.post('/sim-accounts/{account_id}/advance-market-day')
def post_sim_market_advance(request: Request, account_id: str,
                            payload: SimMarketAdvance) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/advance-market-day', body,
                  lambda session: advance_market_day(
                      session, request.app.state.data_dir, account_id, body))


@router.put('/sim-accounts/{account_id}/config')
def put_sim_config(request: Request, account_id: str, payload: SimConfigUpdate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'sim-accounts/{account_id}/config', body,
                  lambda session: update_sim_config(session, account_id, body))


@router.get("/accounts/{account_id}/trades")
def get_trades(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {"data": list_trades(session, account_id)}


@router.get('/accounts/{account_id}/performance')
def get_account_performance(account_id: str, kind: str = 'daily', limit: int = 24,
                            session: Session = Depends(read_session)) -> dict:
    return {'data': performance(session, account_id, kind, limit)}


@router.get('/accounts/{account_id}/asset-estimate/{day}')
def get_asset_estimate(request: Request, account_id: str, day: str,
                       decision_at: str, dataset_id: list[str] = Query(default=[]),
                       strict: bool = True, session: Session = Depends(read_session)) -> dict:
    return {'data': estimate_real_assets(session, request.app.state.data_dir, account_id,
                    day, decision_at, dataset_id, strict)}


@router.get('/accounts/{account_id}/fee-settings')
def get_real_fee_settings(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': fee_settings(session, account_id)}


@router.get('/accounts/{account_id}/target-config')
def get_target_config(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': target_config(session, account_id)}


@router.put('/accounts/{account_id}/target-config')
def put_target_config(request: Request, account_id: str,
                      payload: TargetConfigUpdate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'accounts/{account_id}/target-config', body,
                  lambda session: update_target_config(session, account_id, body))


@router.put('/accounts/{account_id}/fee-settings')
def put_real_fee_settings(request: Request, account_id: str,
                          payload: RealFeeConfigUpdate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'accounts/{account_id}/fee-settings', body,
                  lambda session: update_fee_settings(session, account_id, body))


@router.post('/accounts/{account_id}/fee-preview')
def post_real_fee_preview(request: Request, account_id: str, payload: TradeCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'accounts/{account_id}/fee-preview', body,
                  lambda session: fee_preview(session, account_id, body))


@router.get('/accounts/{account_id}/fee-preview')
def get_real_fee_preview(account_id: str, price: str, quantity: int = Query(gt=0),
                         side: str = Query(pattern='^(buy|sell)$'), fee_mode: str = Query(pattern='^(auto|manual)$'),
                         fee: str = '0', session: Session = Depends(read_session)) -> dict:
    return {'data': fee_preview(session, account_id, dict(price=price, quantity=quantity,
                    side=side, fee_mode=fee_mode, fee=fee))}


@router.get('/accounts/{account_id}/pending-trades')
def get_pending_trades(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': list_pending_trades(session, account_id)}


@router.post('/accounts/{account_id}/pending-trades')
def post_pending_trade(request: Request, account_id: str, payload: TradeCreate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'accounts/{account_id}/pending-trades', body,
                  lambda session: create_pending_trade(session, account_id, body))


@router.put('/accounts/{account_id}/pending-trades/{pending_id}')
def put_pending_trade(request: Request, account_id: str, pending_id: str,
                      payload: TradeUpdate) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'accounts/{account_id}/pending-trades/{pending_id}', body,
                  lambda session: update_pending_trade(session, account_id, pending_id, body))


@router.delete('/accounts/{account_id}/pending-trades/{pending_id}')
def delete_pending_trade(request: Request, account_id: str, pending_id: str,
                         expected_revision: int) -> JSONResponse:
    body = {'expected_revision': expected_revision}
    return _write(request, f'accounts/{account_id}/pending-trades/{pending_id}/discard', body,
                  lambda session: discard_pending_trade(session, account_id, pending_id, expected_revision))


@router.post('/accounts/{account_id}/pending-trades/{pending_id}/confirm')
def post_pending_confirm(request: Request, account_id: str, pending_id: str,
                         payload: PendingTradeConfirm) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'accounts/{account_id}/pending-trades/{pending_id}/confirm', body,
                  lambda session: confirm_pending_trade(session, account_id, pending_id,
                      body['expected_revision'], body['acknowledge_duplicate']))


@router.post('/accounts/{account_id}/pending-trades/confirm-batch')
def post_pending_batch(request: Request, account_id: str,
                       payload: PendingTradeBatch) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'accounts/{account_id}/pending-trades/confirm-batch', body,
                  lambda session: confirm_pending_batch(session, account_id, body['items']))


@router.post("/accounts/{account_id}/trades")
def post_trade(request: Request, account_id: str, payload: TradeCreate) -> JSONResponse:
    body = payload.model_dump(mode="json")
    return _write(request, f"accounts/{account_id}/trades", body,
                  lambda session: create_trade(session, account_id, body))


@router.put("/accounts/{account_id}/trades/{trade_id}")
def put_trade(request: Request, account_id: str, trade_id: str, payload: TradeUpdate) -> JSONResponse:
    body = payload.model_dump(mode="json")
    return _write(request, f"accounts/{account_id}/trades/{trade_id}", body,
                  lambda session: revise_trade(session, account_id, trade_id, body))


@router.delete("/accounts/{account_id}/trades/{trade_id}")
def delete_trade(request: Request, account_id: str, trade_id: str,
                 expected_revision: int) -> JSONResponse:
    body = {"expected_revision": expected_revision}
    return _write(request, f"accounts/{account_id}/trades/{trade_id}/void", body,
                  lambda session: void_trade(session, account_id, trade_id, expected_revision))


@router.get("/accounts/{account_id}/cash-flows")
def get_cash_flows(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {"data": list_flows(session, account_id)}


@router.post("/accounts/{account_id}/cash-flows")
def post_cash_flow(request: Request, account_id: str, payload: CashFlowCreate) -> JSONResponse:
    body = payload.model_dump(mode="json")
    return _write(request, f"accounts/{account_id}/cash-flows", body,
                  lambda session: create_flow(session, account_id, body))


@router.delete("/accounts/{account_id}/cash-flows/{flow_id}")
def delete_cash_flow(request: Request, account_id: str, flow_id: str,
                     expected_revision: int) -> JSONResponse:
    body = {"expected_revision": expected_revision}
    return _write(request, f"accounts/{account_id}/cash-flows/{flow_id}/void", body,
                  lambda session: void_flow(session, account_id, flow_id, expected_revision))


@router.get("/accounts/{account_id}/snapshots")
def get_snapshots(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {"data": list_snapshots(session, account_id)}


@router.put("/accounts/{account_id}/snapshots/{day}")
def put_snapshot(request: Request, account_id: str, day: str, payload: SnapshotSave) -> JSONResponse:
    body = payload.model_dump(mode="json")
    if day != body["snap_date"]:
        raise TradeError("DATE_MISMATCH", "路径日期与快照日期不一致")
    return _write(request, f"accounts/{account_id}/snapshots/{day}", body,
                  lambda session: save_snapshot(session, account_id, body))


@router.get('/accounts/{account_id}/review-gaps')
def get_review_gaps(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': review_gaps(session, account_id)}


@router.get('/accounts/{account_id}/round-notes')
def get_round_notes(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {'data': list_round_notes(session, account_id)}


@router.get('/accounts/{account_id}/round-notes/{round_id}')
def get_round_note_detail(account_id: str, round_id: str,
                          session: Session = Depends(read_session)) -> dict:
    return {'data': get_round_note(session, account_id, round_id)}


@router.put('/accounts/{account_id}/round-notes/{round_id}')
def put_round_note(request: Request, account_id: str, round_id: str,
                   payload: RoundNoteSave) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'accounts/{account_id}/round-notes/{round_id}', body,
                  lambda session: save_round_note(session, account_id, round_id, body))


@router.get('/accounts/{account_id}/daily-reviews')
def get_daily_reviews(account_id: str, limit: int = 100,
                      session: Session = Depends(read_session)) -> dict:
    return {'data': list_reviews(session, account_id, limit)}


@router.get('/accounts/{account_id}/plan-comparison/{day}')
def get_plan_comparison(account_id: str, day: str,
                        session: Session = Depends(read_session)) -> dict:
    result = plan_comparison(session, account_id, day, symbol_key=market_symbol_key)
    for plan in result['plans']:
        plan['calendar'] = plan_dates(session, plan['written_on'], day)
    return {'data': result}


@router.get("/accounts/{account_id}/daily-reviews/{day}")
def get_daily_review(account_id: str, day: str, session: Session = Depends(read_session)) -> dict:
    return {"data": get_review(session, account_id, day)}


@router.put("/accounts/{account_id}/daily-reviews/{day}")
def put_daily_review(request: Request, account_id: str, day: str, payload: DailyReviewSave) -> JSONResponse:
    body = payload.model_dump(mode="json")
    if 'next_target_date' not in payload.model_fields_set:
        body.pop('next_target_date', None)
    return _write(request, f"accounts/{account_id}/daily-reviews/{day}", body,
                  lambda session: save_review(session, account_id, day, body))


@router.get('/accounts/{account_id}/daily-reviews/{day}/attachments')
def get_daily_attachments(account_id: str, day: str,
                          session: Session = Depends(read_session)) -> dict:
    return {'data': list_attachments(session, account_id, day)}


@router.get('/accounts/{account_id}/daily-reviews/{day}/scores')
def get_daily_scores(account_id: str, day: str,
                     session: Session = Depends(read_session)) -> dict:
    return {'data': list_scores(session, account_id, day)}


@router.put('/accounts/{account_id}/daily-reviews/{day}/scores')
def put_daily_scores(request: Request, account_id: str, day: str,
                     payload: ReviewScoresSave) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'accounts/{account_id}/daily-reviews/{day}/scores', body,
                  lambda session: save_scores(session, account_id, day, body))


@router.post('/accounts/{account_id}/daily-reviews/{day}/attachments')
async def post_daily_attachment(request: Request, account_id: str, day: str,
                                file: UploadFile = File(...)) -> JSONResponse:
    content = await file.read(MAX_IMAGE_BYTES + 1)
    body = {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content),
            'name': file.filename or 'image'}
    return _write(request, f'accounts/{account_id}/daily-reviews/{day}/attachments', body,
                  lambda session: add_attachment(session, request.app.state.data_dir,
                                                 account_id, day, content, file.filename or 'image'))


@router.get('/accounts/{account_id}/review-attachments/{attachment_id}/content')
def get_daily_attachment_content(request: Request, account_id: str,
                                 attachment_id: str,
                                 session: Session = Depends(read_session)) -> StreamingResponse:
    meta, content = get_attachment(session, request.app.state.data_dir, account_id, attachment_id)
    return StreamingResponse(io.BytesIO(content), media_type=meta['mime_type'],
                             headers={'Cache-Control': 'private, no-store',
                                      'X-Content-Type-Options': 'nosniff',
                                      'Content-Disposition': 'inline'})


@router.delete('/accounts/{account_id}/review-attachments/{attachment_id}')
def delete_daily_attachment(request: Request, account_id: str, attachment_id: str,
                            expected_revision: int) -> JSONResponse:
    body = {'expected_revision': expected_revision}
    return _write(request, f'accounts/{account_id}/review-attachments/{attachment_id}/delete', body,
                  lambda session: delete_attachment(session, account_id, attachment_id,
                                                    expected_revision))


@router.get('/accounts/{account_id}/period-reviews/{kind}')
def get_period_reviews(account_id: str, kind: str,
                       session: Session = Depends(read_session)) -> dict:
    return {'data': list_periods(session, account_id, kind)}


@router.get('/accounts/{account_id}/exports/review/{kind}/{key}.md')
def download_review_markdown(account_id: str, kind: str, key: str,
                             session: Session = Depends(read_session)) -> Response:
    content = export_markdown(session, account_id, kind, key)
    return Response(content, media_type='text/markdown; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="trade-review-{kind}-{key}.md"',
        'X-Content-Type-Options': 'nosniff',
    })


@router.get('/accounts/{account_id}/exports/review/{kind}/{key}.pdf')
def download_review_pdf(request: Request, account_id: str, kind: str, key: str,
                        session: Session = Depends(read_session)) -> Response:
    content = render_review_pdf(session, request.app.state.data_dir, account_id, kind, key)
    return Response(content, media_type='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="trade-review-{kind}-{key}.pdf"',
        'X-Content-Type-Options': 'nosniff',
    })


@router.get('/accounts/{account_id}/exports/{kind}.csv')
def download_account_csv(account_id: str, kind: str,
                         session: Session = Depends(read_session)) -> Response:
    content = account_csv(session, account_id, kind)
    return Response(content, media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="trade-{kind}-{account_id}.csv"',
        'X-Content-Type-Options': 'nosniff',
    })


@router.get('/accounts/{account_id}/exports/account.xlsx')
def download_account_xlsx(account_id: str,
                          session: Session = Depends(read_session)) -> Response:
    content = account_xlsx(session, account_id)
    return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="trade-account-{account_id}.xlsx"',
                             'X-Content-Type-Options': 'nosniff',
                             'Cache-Control': 'private, no-store'})


@router.get('/accounts/{account_id}/period-reviews/{kind}/{period_key}')
def get_period_review(account_id: str, kind: str, period_key: str,
                      session: Session = Depends(read_session)) -> dict:
    return {'data': get_period(session, account_id, kind, period_key)}


@router.put('/accounts/{account_id}/period-reviews/{kind}/{period_key}')
def put_period_review(request: Request, account_id: str, kind: str, period_key: str,
                      payload: PeriodReviewSave) -> JSONResponse:
    body = payload.model_dump(mode='json')
    return _write(request, f'accounts/{account_id}/period-reviews/{kind}/{period_key}', body,
                  lambda session: save_period(session, account_id, kind, period_key, body))


@router.delete('/accounts/{account_id}/period-reviews/{kind}/{period_key}')
def delete_period_review(request: Request, account_id: str, kind: str, period_key: str,
                         expected_revision: int) -> JSONResponse:
    body = {'expected_revision': expected_revision}
    return _write(request, f'accounts/{account_id}/period-reviews/{kind}/{period_key}/delete', body,
                  lambda session: delete_period(session, account_id, kind, period_key,
                                                expected_revision))


@router.get("/accounts/{account_id}/analytics")
def get_analytics(account_id: str, session: Session = Depends(read_session)) -> dict:
    return {"data": projection_status(session, account_id)}


@router.post("/accounts/{account_id}/analytics/retry")
def post_retry_analytics(request: Request, account_id: str) -> JSONResponse:
    return _write(request, f"accounts/{account_id}/analytics/retry", {},
                  lambda session: retry_failed(session, account_id))
