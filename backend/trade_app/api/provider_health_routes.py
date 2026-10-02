from datetime import date
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from pathlib import Path
import json
from sqlalchemy import select

from trade_app.market.provider_health import capabilities, probe
from trade_app.market.tdx_location import inspect_location, scan_locations, source_fingerprint
from trade_app.platform.types import TradeError
from trade_app.research.tdx_universe_models import TdxUniverseJob

router = APIRouter(prefix='/api/v1/market/providers', tags=['market-providers'])


class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbol: str = Field(min_length=1, max_length=16)
    start_date: date
    end_date: date


class ScanLocations(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SelectLocation(ScanLocations):
    path: str = Field(min_length=1, max_length=2000)


def _location_state(request):
    state = request.app.state
    return {**state.tdx_discovery, 'current_path': str(state.tdx_root) if state.tdx_root else None,
            'selection': state.tdx_selection, 'persisted': False}


@router.get('/tdx/locations')
def tdx_locations(request: Request):
    return {'data': _location_state(request)}


@router.post('/tdx/scan')
def scan_tdx_locations(request: Request, body: ScanLocations):
    request.app.state.tdx_discovery = scan_locations()
    return {'data': _location_state(request)}


@router.post('/tdx/select')
def select_tdx_location(request: Request, body: SelectLocation):
    location = inspect_location(body.path)
    def apply():
        with request.app.state.db_factory() as session:
            pending = list(session.scalars(select(TdxUniverseJob.request_json).where(
                TdxUniverseJob.state.in_(('queued', 'running', 'finalizing')))))
            if pending and Path(location['path']) != request.app.state.tdx_root:
                chosen = source_fingerprint(Path(location['path']))
                if any(json.loads(value).get('tdx_source_fingerprint') != chosen for value in pending):
                    raise TradeError('TDX_SOURCE_BUSY', '有全市场任务依赖其他目录；请选择任务使用的原通达信目录，或先取消任务', 409)
        request.app.state.tdx_root = Path(location['path'])
        request.app.state.tdx_selection = 'manual'
        return {'data': _location_state(request)}
    return request.app.state.lifecycle.reconfigure_local_source(apply)


@router.get('')
def listing(request: Request):
    return {'data': capabilities(request.app.state.tdx_root, request.app.state.cache_roots)}


@router.post('/{provider}/probe')
def test_provider(request: Request, provider: Literal['tdx', 'akshare_cache', 'baostock_cache', 'akshare', 'baostock'], body: ProbeRequest):
    return {'data': probe(provider, body.model_dump(mode='json'), tdx_root=request.app.state.tdx_root,
                          cache_roots=request.app.state.cache_roots)}
