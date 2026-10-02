from pathlib import Path

import pytest

from trade_app.market.symbols import normalize_a_share_symbol, standard_limit_up_ratio
from trade_app.platform.types import TradeError
from trade_app.research import ladder_service
from trade_app.research.limit_up_arb_domain import calculate_limit_up_arb_signal, evaluate_limit_up_arb_signal
from trade_app.research.trend_king_domain import CandlePoint


@pytest.mark.parametrize('code,exchange,ratio', [
    ('600000', 'sh', 0.10), ('000001', 'sz', 0.10),
    ('300001', 'sz', 0.20), ('301001', 'sz', 0.20),
    ('688001', 'sh', 0.20), ('689001', 'sh', 0.20),
    ('430001', 'bj', 0.30), ('830001', 'bj', 0.30), ('920001', 'bj', 0.30),
])
def test_stock_aliases_have_identical_board_rules(code, exchange, ratio):
    for alias in (code, exchange + code, code + '.' + exchange.upper(), ' ' + exchange.upper() + code + ' '):
        assert normalize_a_share_symbol(alias) == (exchange, code)
        assert standard_limit_up_ratio(alias) == ratio


@pytest.mark.parametrize('symbol', ['sh300001', '600000.SZ', '920001.SH', 'bj600000',
                                    'sz300001.SH', '300001.SZ.exe', 'SH6000000', 'AAPL', '999999'])
def test_invalid_or_conflicting_symbols_are_rejected(symbol):
    with pytest.raises(TradeError):
        normalize_a_share_symbol(symbol)


def _candles(previous_close):
    return [CandlePoint(time=f'2026-01-0{index + 1}', open=close, high=close,
                        low=close, close=close, volume=volume, amount=0)
            for index, (close, volume) in enumerate(((10, 1000), (previous_close, 1000),
                                                      (previous_close * 1.04, 1500)))]


@pytest.mark.parametrize('aliases', [('300001', 'sz300001', '300001.SZ'),
                                     ('688001', 'sh688001', '688001.SH'),
                                     ('920001', 'bj920001', '920001.BJ')])
def test_ten_percent_move_does_not_trigger_higher_limit_board_arbitrage(aliases):
    results = [calculate_limit_up_arb_signal(_candles(11), symbol=alias) for alias in aliases]
    assert all(result == results[0] for result in results)
    assert results[0]['prev_limit_up'] is False
    assert evaluate_limit_up_arb_signal(results[0])['signal'] is False


@pytest.mark.parametrize('aliases,previous_close', [
    (('600000', 'sh600000', '600000.SH'), 11),
    (('300001', 'sz300001', '300001.SZ'), 12),
    (('920001', 'bj920001', '920001.BJ'), 13),
])
def test_valid_limit_move_preserves_arbitrage_trigger_for_every_alias(aliases, previous_close):
    results = [calculate_limit_up_arb_signal(_candles(previous_close), symbol=alias) for alias in aliases]
    assert all(result == results[0] for result in results)
    assert results[0]['prev_limit_up'] is True
    assert evaluate_limit_up_arb_signal(results[0])['signal'] is True


def _dataset(symbol, previous_close=11):
    return {'id': symbol, 'symbol': symbol, 'adjustment': 'none',
            'availability_quality': 'historical_availability_unknown',
            'bars': [{'event_date': point.time, 'close': str(point.close), 'available_at': None}
                     for point in _candles(previous_close)]}


def _request(symbols, filters=None):
    return {'dataset_ids': symbols, 'board_filters': filters or [],
            'date_from': '2026-01-01', 'date_to': '2026-01-03',
            'recent_days': 3, 'historical_min_boards': 2}


def test_ladder_suffix_board_filter_and_limit_count_are_consistent():
    body = _request([], ['gem'])
    assert ladder_service.ladder_contribution(_dataset('300001.SZ'), '股票', body)['heights'] == {}
    expected = ladder_service.ladder_contribution(_dataset('300001', 12), '股票', body)
    suffixed = ladder_service.ladder_contribution(_dataset('300001.SZ', 12), '股票', body)
    assert expected['dates'] == suffixed['dates']
    assert expected['heights'] == suffixed['heights'] == {'2026-01-02': 1}
    beijing = ladder_service.ladder_contribution(_dataset('920001.BJ', 13), '股票', _request([], ['beijing']))
    assert beijing['heights'] == {'2026-01-02': 1}


def test_ladder_rejects_duplicate_stock_aliases_before_counting(monkeypatch):
    monkeypatch.setattr(ladder_service, 'get_dataset', lambda session, data_dir, value: _dataset(value))
    with pytest.raises(TradeError) as error:
        ladder_service.prepare_ladder_run(None, Path('.'), None, _request(['300001', '300001.SZ']))
    assert error.value.code == 'DUPLICATE_SYMBOL'


def test_ladder_name_lookup_uses_canonical_exchange_and_preserves_dataset_identity(monkeypatch):
    captured = []
    monkeypatch.setattr(ladder_service, 'get_dataset', lambda session, data_dir, value: _dataset(value, 13))

    def names(root, symbols):
        captured.extend(symbols)
        return {'names': {'bj920001': '北交股票'}, 'sources': []}

    monkeypatch.setattr(ladder_service, 'read_tdx_names', names)
    result = ladder_service.prepare_ladder_run(None, Path('.'), None, _request(['920001.BJ']))
    assert captured == ['bj920001']
    assert result['result']['summaries'][0]['symbol'] == '920001.BJ'
    assert result['result']['summaries'][0]['dataset_id'] == '920001.BJ'
    assert result['result']['summaries'][0]['name'] == '北交股票'
