from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from trade_app.analytics.service import process_one, recover_outdated_projections
from trade_app.research.backtest_service import process_one_backtest, recover_interrupted_backtests
from trade_app.research.scan_job_service import process_one_scan_chunk, recover_interrupted_scan_jobs
from trade_app.research.plateau_service import process_one_plateau, recover_interrupted_plateaus
from trade_app.research.walk_forward_service import process_one_walk_forward, recover_interrupted_walk_forwards
from trade_app.research.portfolio_service import process_one_portfolio, recover_interrupted_portfolios
from trade_app.research.portfolio_experiment_service import process_one_experiment, recover_interrupted_experiments
from trade_app.research.portfolio_walk_forward_service import process_one_walk_forward as process_one_portfolio_walk_forward, recover_interrupted_walk_forwards as recover_portfolio_walk_forwards
from trade_app.research.portfolio_analysis_service import process_one_analysis as process_one_portfolio_analysis, recover_interrupted_analyses as recover_portfolio_analyses
from trade_app.research.event_store_service import process_one_event_store, recover_interrupted_event_store_jobs
from trade_app.platform.compute_scheduler import ComputeScheduler
from trade_app.platform.lifecycle import LifecycleController, LifecycleWriteGate
from trade_app.market.sync_jobs import process_one_sync_symbol, recover_sync_jobs
from trade_app.market.tdx_location import scan_locations
from trade_app.research.tdx_universe import process_one_universe_symbol, recover_universe_jobs
from trade_app.api.routes import router
from trade_app.api.ai_routes import router as ai_router
from trade_app.api.preset_routes import router as preset_router
from trade_app.api.task_routes import router as task_router
from trade_app.api.report_routes import router as report_router
from trade_app.api.generation_routes import router as generation_router
from trade_app.api.score_routes import router as score_router
from trade_app.api.scan_job_routes import router as scan_job_router
from trade_app.api.lifecycle_routes import router as lifecycle_router
from trade_app.api.plateau_routes import router as plateau_router
from trade_app.api.walk_forward_routes import router as walk_forward_router
from trade_app.api.system_routes import router as system_router
from trade_app.api.news_routes import router as news_router
from trade_app.api.planning_routes import router as planning_router
from trade_app.api.watch_pool_routes import router as watch_pool_router
from trade_app.api.stock_history_routes import router as stock_history_router
from trade_app.api.portfolio_routes import router as portfolio_router
from trade_app.api.event_store_routes import router as event_store_router
from trade_app.api.settings_routes import router as settings_router
from trade_app.api.reminder_routes import router as reminder_router
from trade_app.api.signal_workspace_routes import router as signal_workspace_router
from trade_app.api.legacy_import_routes import router as legacy_import_router
from trade_app.api.portfolio_report_routes import router as portfolio_report_router
from trade_app.api.sim_equity_routes import router as sim_equity_router
from trade_app.api.portfolio_experiment_routes import router as portfolio_experiment_router
from trade_app.api.ai_context_routes import router as ai_context_router
from trade_app.api.provider_health_routes import router as provider_health_router
from trade_app.api.registry_routes import router as registry_router
from trade_app.api.portfolio_walk_forward_routes import router as portfolio_walk_forward_router
from trade_app.api.performance_export_routes import router as performance_export_router
from trade_app.api.legacy_report_routes import router as legacy_report_router
from trade_app.api.screener_export_routes import router as screener_export_router
from trade_app.api.research_table_export_routes import router as research_table_export_router
from trade_app.api.schemas import BrowserSessionResponse
from trade_app.api.intraday_routes import router as intraday_router
from trade_app.api.portfolio_analysis_routes import router as portfolio_analysis_router
from trade_app.ai.service import recover_interrupted_runs
from trade_app.platform.db import open_database
from trade_app.platform.instance_lock import InstanceLock
from trade_app.platform.types import TradeError
from trade_app.platform.runtime import resource_root
from trade_app.platform.remote_access import RemoteAccess


LOGGER = logging.getLogger(__name__)
BACKGROUND_SHUTDOWN_SECONDS = 3.0


def default_data_dir() -> Path:
    configured = os.environ.get("TRADE_REBUILD_DATA_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else (Path.home() / "TradeRebuild").resolve()


def create_app(data_dir: Path | None = None, *, auto_rebuild: bool = True,
               lifecycle: LifecycleController | None = None, auto_detect_tdx: bool = False) -> FastAPI:
    """No disk access until ASGI lifespan starts."""
    target = (data_dir or default_data_dir()).resolve()
    controller = lifecycle or LifecycleController(target)
    if controller.data_dir != target:
        raise ValueError('Lifecycle controller data directory does not match application')
    port_setting = os.environ.get('TRADE_REBUILD_PORT', '8011')
    if not port_setting.isdecimal() or not 1 <= int(port_setting) <= 65535:
        raise ValueError('TRADE_REBUILD_PORT must be a valid TCP port')
    service_port = int(port_setting)
    remote_access = RemoteAccess.from_environment()
    tdx_setting = os.environ.get('TRADE_TDX_ROOT', '').strip()
    tdx_root = Path(tdx_setting).expanduser().resolve() if tdx_setting else None
    cache_roots = {}
    for provider, variable in (('akshare', 'TRADE_AKSHARE_CACHE_DIR'),
                               ('baostock', 'TRADE_BAOSTOCK_CACHE_DIR')):
        configured = os.environ.get(variable, '').strip()
        cache_roots[provider] = Path(configured).expanduser().resolve() if configured else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lock = InstanceLock(target)
        lock.acquire()
        release_deferred = False
        try:
            controller.validate_startup()
            engine, factory = open_database(target)
            app.state.db_engine = engine
            app.state.db_factory = factory
            app.state.data_dir = target
            app.state.tdx_root = tdx_root
            app.state.tdx_selection = 'environment' if tdx_root else 'unset'
            app.state.tdx_discovery = {'candidates': [], 'limited': False, 'checked_folders': 0,
                                       'scope': '点击扫描本机，或手动选择通达信目录。'}
            if auto_detect_tdx and tdx_root is None:
                app.state.tdx_discovery = scan_locations()
                candidates = app.state.tdx_discovery['candidates']
                if len(candidates) == 1:
                    app.state.tdx_root = Path(candidates[0]['path'])
                    app.state.tdx_selection = 'auto'
            app.state.cache_roots = cache_roots
            recover_outdated_projections(factory)
            recover_interrupted_backtests(factory)
            recover_interrupted_scan_jobs(factory)
            recover_interrupted_plateaus(factory)
            recover_interrupted_walk_forwards(factory)
            recover_interrupted_portfolios(factory)
            recover_interrupted_experiments(factory)
            recover_portfolio_walk_forwards(factory)
            recover_portfolio_analyses(factory)
            recover_interrupted_event_store_jobs(factory)
            recover_sync_jobs(factory)
            recover_universe_jobs(factory)
            recover_interrupted_runs(factory)
            app.state.sessions = {}
            app.state.stop_rebuild = threading.Event()
            app.state.compute_should_stop = lambda: app.state.stop_rebuild.is_set() or controller.quiescing
            app.state.background_errors = {}

            def domain_loop(name, operation) -> None:
                while not app.state.stop_rebuild.is_set():
                    try:
                        did_work = controller.run_background(name, operation)
                        app.state.background_errors.pop(name, None)
                    except Exception as exc:
                        app.state.background_errors[name] = f'{type(exc).__name__}: {exc}'[:500]
                        LOGGER.exception('Background domain %s failed; next iteration will retry', name)
                        did_work = False
                    if not did_work:
                        app.state.stop_rebuild.wait(0.2)

            scheduler = ComputeScheduler([
                lambda: process_one_backtest(factory, target, should_stop=app.state.compute_should_stop),
                lambda: process_one_scan_chunk(factory, target, should_stop=app.state.compute_should_stop),
                lambda: process_one_plateau(factory, target, should_stop=app.state.compute_should_stop),
                lambda: process_one_walk_forward(factory, target, should_stop=app.state.compute_should_stop),
                lambda: process_one_portfolio(factory, target, should_stop=app.state.compute_should_stop),
                lambda: process_one_experiment(factory, target, should_stop=app.state.compute_should_stop),
                lambda: process_one_portfolio_walk_forward(factory, target, should_stop=app.state.compute_should_stop),
                lambda: process_one_portfolio_analysis(factory, target, should_stop=app.state.compute_should_stop),
                lambda: process_one_event_store(factory, target, should_stop=app.state.compute_should_stop),
            ])
            operations = {
                'analytics': lambda: process_one(factory),
                'compute': scheduler.process_one,
                'market-sync': lambda: process_one_sync_symbol(factory, target),
                'tdx-universe': lambda: process_one_universe_symbol(factory, target, app.state.tdx_root),
            }
            background = [threading.Thread(target=domain_loop, args=(name, operation), daemon=True,
                                            name=f'trade-{name}') for name, operation in operations.items()] if auto_rebuild else []
            app.state.background_threads = background
            for worker in background:
                worker.start()
            try:
                if controller.managed:
                    lock.publish(instance_id=controller.instance_id, port=service_port,
                                 launcher_pid=controller.managed.get('launcher_pid'))
                yield
            finally:
                app.state.stop_rebuild.set()
                deadline = time.monotonic() + BACKGROUND_SHUTDOWN_SECONDS
                while any(worker.is_alive() for worker in background) and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                if any(worker.is_alive() for worker in background):
                    # A provider may still be unwinding its own network timeout. Keep the
                    # directory lock until all writes finish, without blocking ASGI shutdown.
                    release_deferred = True

                    def finish_cleanup():
                        for worker in background:
                            worker.join()
                        try:
                            engine.dispose()
                        finally:
                            lock.release()

                    threading.Thread(target=finish_cleanup, daemon=True,
                                     name='trade-background-cleanup').start()
                    LOGGER.warning('Shutdown deadline reached; directory lock retained until background I/O exits')
                else:
                    engine.dispose()
        finally:
            if not release_deferred:
                lock.release()

    app = FastAPI(title="Trade Rebuild API", version="0.1.0", lifespan=lifespan)
    app.state.lifecycle = controller
    app.add_middleware(LifecycleWriteGate)

    @app.middleware("http")
    async def local_session_boundary(request: Request, call_next):
        try:
            request.state.authenticated_proxy = remote_access.check(request)
        except TradeError as exc:
            return JSONResponse({'error': {'code': exc.code, 'message': exc.message}}, status_code=exc.status)
        origin = request.headers.get("origin")
        approved = {"http://127.0.0.1:4174", "http://localhost:4174",
                    f"http://127.0.0.1:{service_port}", f"http://localhost:{service_port}",
                    "http://testserver"}
        if request.state.authenticated_proxy:
            approved = {remote_access.origin}
        if origin and origin not in approved:
            return JSONResponse({"error": {"code": "ORIGIN_REJECTED", "message": "不支持的请求来源"}}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": {"code": "CROSS_SITE_REJECTED", "message": "跨站请求已拒绝"}}, status_code=403)
        path = request.url.path
        if path.startswith("/api/v1") and path != "/api/v1/session":
            sid = request.cookies.get("trade_rebuild_sid", "")
            entry = getattr(request.app.state, "sessions", {}).get(sid)
            if not entry or entry[1] < time.time():
                return JSONResponse({"error": {"code": "SESSION_REQUIRED", "message": "请重新连接本地服务"}}, status_code=401)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), entry[0]):
                    return JSONResponse({"error": {"code": "CSRF_REJECTED", "message": "会话校验失败"}}, status_code=403)
        return await call_next(request)

    @app.exception_handler(TradeError)
    def handle_trade_error(_request: Request, exc: TradeError) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"error": {"code": "VALIDATION_ERROR", "message": "请求字段无效",
                                       "fields": [{"path": ".".join(map(str, item.get("loc", ()))),
                                                   "type": item.get("type", "invalid"),
                                                   "message": item.get("msg", "无效字段")}
                                                  for item in exc.errors()]}}, status_code=422)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "product": "trade-rebuild", "version": "0.1.0", "instance_id": controller.instance_id}

    @app.get("/api/v1/session", response_model=BrowserSessionResponse)
    def session(request: Request) -> JSONResponse:
        existing = request.app.state.sessions.get(request.cookies.get('trade_rebuild_sid', ''))
        remaining = int(existing[1] - time.time()) if existing else 0
        if existing and remaining > 0:
            # Browser tabs share the HttpOnly cookie. Replacing it on each mount
            # invalidated the CSRF token kept by every other open editor.
            return JSONResponse({'data': {'csrf_token': existing[0], 'expires_in_seconds': remaining}},
                                headers={'Cache-Control': 'no-store'})
        sid = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        request.app.state.sessions[sid] = (csrf, time.time() + 8 * 3600)
        response = JSONResponse({"data": {"csrf_token": csrf, "expires_in_seconds": 8 * 3600}},
                                headers={'Cache-Control': 'no-store'})
        response.set_cookie("trade_rebuild_sid", sid, httponly=True, samesite="strict", max_age=8 * 3600,
                            secure=bool(request.state.authenticated_proxy))
        return response

    app.include_router(router)
    app.include_router(ai_router)
    app.include_router(preset_router)
    app.include_router(task_router)
    app.include_router(report_router)
    app.include_router(generation_router)
    app.include_router(score_router)
    app.include_router(scan_job_router)
    app.include_router(lifecycle_router)
    app.include_router(plateau_router)
    app.include_router(walk_forward_router)
    app.include_router(system_router)
    app.include_router(news_router)
    app.include_router(planning_router)
    app.include_router(watch_pool_router)
    app.include_router(stock_history_router)
    app.include_router(portfolio_router)
    app.include_router(event_store_router)
    app.include_router(settings_router)
    app.include_router(reminder_router)
    app.include_router(signal_workspace_router)
    app.include_router(legacy_import_router)
    app.include_router(portfolio_report_router)
    app.include_router(sim_equity_router)
    app.include_router(portfolio_experiment_router)
    app.include_router(ai_context_router)
    app.include_router(provider_health_router)
    app.include_router(registry_router)
    app.include_router(portfolio_walk_forward_router)
    app.include_router(performance_export_router)
    app.include_router(legacy_report_router)
    app.include_router(screener_export_router)
    app.include_router(research_table_export_router)
    app.include_router(intraday_router)
    app.include_router(portfolio_analysis_router)
    built_frontend = resource_root() / 'frontend' / 'dist-rebuild'
    if (built_frontend / 'rebuild.html').is_file():
        @app.get('/')
        def home() -> RedirectResponse:
            return RedirectResponse('/rebuild.html')

        app.mount('/', StaticFiles(directory=built_frontend), name='rebuild-ui')
    return app


app = create_app()
