# Backend API (FastAPI)

## Setup

```bash
cd backend
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
```

### Optional: AkShare Sync

```bash
pip install -r requirements-akshare.txt
python scripts/sync_akshare_daily.py --symbols 600519,300750,601899
```

Incremental update (default): script only fetches dates after each symbol's latest local row.

```bash
python scripts/sync_akshare_daily.py --all-market --limit 300 --end-date 2026-02-13
```

Force full refresh:

```bash
python scripts/sync_akshare_daily.py --symbols 600519 --full-history --start-date 2024-01-01
```

Downloaded files are saved to:

`~/.tdx-trend/akshare/daily/*.csv`

Runtime candle loader will fallback to this cache when TDX `.day` files are unavailable.

### Optional: Baostock Sync (Recommended when AkShare is unstable)

```bash
pip install -r requirements-baostock.txt
python scripts/sync_baostock_daily.py --all-market --limit 300 --mode incremental
```

You can also trigger the same sync from API (used by the home-page button):

`POST /api/system/sync-market-data`

## Local persistence files

- App config / AI records / annotations: `~/.tdx-trend/app_state.json`
- Sim trading account state: `~/.tdx-trend/sim_state.json`

## Run

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Health

```bash
curl http://127.0.0.1:8000/health
```
