from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import StockAnnotation
from app.store import InMemoryStore


def test_store_persists_config_annotations_and_ai_records(tmp_path: Path) -> None:
    app_state_path = tmp_path / "app_state.json"
    sim_state_path = tmp_path / "sim_state.json"

    store_a = InMemoryStore(app_state_path=str(app_state_path), sim_state_path=str(sim_state_path))
    cfg = store_a.get_config()
    updated_cfg = cfg.model_copy(
        update={
            "ai_provider": "deepseek",
            "market_data_source": "akshare_only",
            "akshare_cache_dir": str(tmp_path / "ak-cache"),
            "backtest_default_execution_path_preference": "legacy",
        }
    )
    store_a.set_config(updated_cfg)
    store_a.save_annotation(
        StockAnnotation(
            symbol="sz300750",
            start_date="2026-02-01",
            stage="Mid",
            trend_class="A_B",
            decision="保留",
            notes="persist-test",
            updated_by="manual",
        )
    )
    first = store_a.get_ai_records()[0]
    deleted = store_a.delete_ai_record(first.symbol, first.fetched_at, first.provider)
    assert deleted is True
    after_delete_count = len(store_a.get_ai_records())
    assert app_state_path.exists()

    store_b = InMemoryStore(app_state_path=str(app_state_path), sim_state_path=str(sim_state_path))
    reloaded_cfg = store_b.get_config()
    assert reloaded_cfg.ai_provider == "deepseek"
    assert reloaded_cfg.market_data_source == "akshare_only"
    assert reloaded_cfg.akshare_cache_dir == str(tmp_path / "ak-cache")
    assert reloaded_cfg.backtest_default_execution_path_preference == "legacy"
    assert len(store_b.get_ai_records()) == after_delete_count
    analysis = store_b.get_analysis("sz300750")
    assert analysis.annotation is not None
    assert analysis.annotation.notes == "persist-test"
