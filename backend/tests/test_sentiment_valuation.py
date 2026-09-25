from app.core.sentiment_valuation import (
    calc_implied_sentiment,
    calc_theoretical_cap,
    growth_rate_to_coef,
    index_points_to_coef,
    parse_eastmoney_quote,
    ValuationInputs,
)


def test_changjiang_power_example():
    inputs = ValuationInputs(
        earnings_yi=358.0,
        growth_coef=1.05,
        base_pe=12.5,
        index_coef=1.36,
        sentiment_coef=1.0,
    )
    result = calc_theoretical_cap(inputs)
    assert abs(result.theoretical_cap_yi - 6390.3) < 5.0


def test_maotai_example():
    inputs = ValuationInputs(
        earnings_yi=863.0,
        growth_coef=0.85,
        base_pe=15.0,
        index_coef=1.36,
        sentiment_coef=1.0,
    )
    result = calc_theoretical_cap(inputs)
    assert abs(result.theoretical_cap_yi - 14964.42) < 5.0


def test_yuanjie_example():
    inputs = ValuationInputs(
        earnings_yi=12.0,
        growth_coef=2.0,
        base_pe=30.0,
        index_coef=1.36,
        sentiment_coef=1.0,
    )
    result = calc_theoretical_cap(inputs)
    assert abs(result.theoretical_cap_yi - 979.2) < 1.0
    implied = calc_implied_sentiment(
        2083.0,
        earnings_yi=12.0,
        growth_coef=2.0,
        base_pe=30.0,
        index_coef=1.36,
    )
    assert implied is not None
    assert abs(implied - 2.12) < 0.05


def test_honghe_example():
    inputs = ValuationInputs(
        earnings_yi=10.0,
        growth_coef=2.5,
        base_pe=30.0,
        index_coef=1.36,
        sentiment_coef=1.0,
    )
    result = calc_theoretical_cap(inputs)
    assert abs(result.theoretical_cap_yi - 1020.0) < 1.0
    implied = calc_implied_sentiment(
        2332.0,
        earnings_yi=10.0,
        growth_coef=2.5,
        base_pe=30.0,
        index_coef=1.36,
    )
    assert implied is not None
    assert abs(implied - 2.28) < 0.05


def test_index_and_growth_helpers():
    assert abs(index_points_to_coef(3300.0) - 1.1) < 0.001
    assert abs(growth_rate_to_coef(30.0) - 1.3) < 0.001
    assert abs(growth_rate_to_coef(-15.0) - 0.85) < 0.001


def test_parse_eastmoney_quote_derives_earnings_from_pe_ttm():
    parsed = parse_eastmoney_quote(
        {
            "f58": "长江电力",
            "f127": "电力",
            "f43": 2850,
            "f116": 7000_0000_0000,
            "f164": 2200,
            "f162": 2300,
        }
    )
    assert parsed["market_cap_yi"] == 7000.0
    assert parsed["pe_ttm"] == 22.0
    assert parsed["implied_earnings_yi"] is not None
    assert abs(parsed["implied_earnings_yi"] - 318.18) < 0.1


def test_parse_eastmoney_quote_ignores_invalid_pe():
    parsed = parse_eastmoney_quote(
        {
            "f58": "亏损股",
            "f116": 500_0000_0000,
            "f164": -100,
            "f162": 0,
        }
    )
    assert parsed["market_cap_yi"] == 500.0
    assert parsed["pe_ttm"] is None
    assert parsed["implied_earnings_yi"] is None
