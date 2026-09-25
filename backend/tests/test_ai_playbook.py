from __future__ import annotations

from app.core.ai_playbook import build_playbook
from app.core.ai_chat_service import normalize_ai_conclusion
from app.core.strategy_registry import StrategyRegistry


def test_build_playbook_includes_enabled_strategies() -> None:
    registry = StrategyRegistry()
    playbook = build_playbook(registry, scope="all")
    strategies = playbook.get("strategies")
    assert isinstance(strategies, dict)
    assert "wyckoff_trend_v1" in strategies
    assert strategies["wyckoff_trend_v1"]["description"]
    assert "screener" in playbook
    assert "wyckoff" in playbook
    assert "backtest" in playbook


def test_build_playbook_scope_chart() -> None:
    registry = StrategyRegistry()
    playbook = build_playbook(registry, scope="chart")
    assert "screener" in playbook
    assert "strategies" not in playbook or isinstance(playbook.get("strategies"), dict)


def test_normalize_ai_conclusion_maps_free_text() -> None:
    assert normalize_ai_conclusion("看多") == "发酵中"
    assert normalize_ai_conclusion("当前处于退潮阶段") == "退潮"
    assert normalize_ai_conclusion("发酵中") == "发酵中"
    assert normalize_ai_conclusion("") == "Unknown"
