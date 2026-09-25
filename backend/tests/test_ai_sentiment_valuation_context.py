from app.core.ai_playbook import SENTIMENT_VALUATION_MODEL, build_playbook
from app.core.sentiment_valuation import summarize_valuation_payload
from app.core.strategy_registry import StrategyRegistry


def test_playbook_includes_sentiment_valuation_scope():
    registry = StrategyRegistry()
    payload = build_playbook(registry, scope="sentiment_valuation")
    assert "sentiment_valuation" in payload
    assert payload["sentiment_valuation"]["formula"] == SENTIMENT_VALUATION_MODEL["formula"]


def test_summarize_valuation_payload():
    summary = summarize_valuation_payload(
        {
            "earnings_yi": 12,
            "growth_rate_pct": 100,
            "base_pe": 30,
            "index_points": 4090,
            "actual_cap_yi": 2083,
        }
    )
    outputs = summary["valuation_outputs"]
    assert outputs["theoretical_cap_yi"] is not None
    assert outputs["implied_sentiment_coef"] is not None
    assert outputs["implied_sentiment_coef"] > 2.0
