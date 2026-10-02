"""Deterministic single-symbol research execution with the shared fill and fee rules."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from trade_app.market.domain import eligible_bars
from trade_app.platform.types import TradeError, money_text, price_units
from trade_app.research.domain import STRATEGY_ID
from trade_app.research.runtime import evaluate_strategy
from trade_app.research.classic_domain import CLASSIC_STRATEGY_IDS
from trade_app.trading.domain import FeeRule, FillState, adverse_execution_price, apply_fill, calculate_fees


EXECUTION_VERSION = 'next-open-known-prefix-slippage-v3'
CALCULATION_VERSION = 'single-symbol-equity-v4'


def _cents(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _price(bar: dict, key: str) -> Decimal:
    return Decimal(price_units(bar[key])) / Decimal(10_000)


def run_single_symbol_backtest(*, symbol: str, bars: list[dict], params: dict[str, str],
                               initial_minor: int, holding_bars: int,
                               max_position_pct: Decimal, fees: FeeRule,
                               strict: bool, cash_buffer_minor: int = 0,
                               strategy_id: str = STRATEGY_ID, event_profile: dict | None = None,
                               stop_loss_pct: Decimal = Decimal(0),
                               take_profit_pct: Decimal = Decimal(0),
                               trailing_stop_pct: Decimal = Decimal(0),
                               slippage_rate: Decimal = Decimal(0),
                               trade_start_date: str | None = None) -> dict:
    if initial_minor <= 0 or cash_buffer_minor < 0 or not 1 <= holding_bars <= 60 or not Decimal(0) < max_position_pct <= Decimal(1):
        raise TradeError('INVALID_BACKTEST_CONFIG', '回测资金、持有期或仓位无效')
    if len(bars) < 32:
        raise TradeError('INSUFFICIENT_MARKET_DATA', '至少需要 32 根 K 线运行回测')
    if trade_start_date is not None and trade_start_date not in [bar['event_date'] for bar in bars[:-1]]:
        raise TradeError('INVALID_TRADING_WINDOW', '交易开始日须存在于冻结样本且至少保留两根交易日线')
    adverse_execution_price(Decimal(1), 'buy', slippage_rate)
    for key, value, maximum in (('stop_loss_pct', stop_loss_pct, Decimal('0.5')),
                                 ('take_profit_pct', take_profit_pct, Decimal('1.5')),
                                 ('trailing_stop_pct', trailing_stop_pct, Decimal('0.5'))):
        if not value.is_finite() or not Decimal(0) <= value <= maximum:
            raise TradeError('INVALID_BACKTEST_CONFIG', f'{key} 超出支持范围')
    state = FillState(cash=Decimal(initial_minor) / 100, quantity=0,
                      sellable_quantity=0, cost_basis=Decimal(0))
    entry_index: int | None = None
    entry_price = Decimal(0)
    peak_close = Decimal(0)
    trades: list[dict] = []
    equity: list[dict] = []
    decisions: list[dict] = []
    quality: set[str] = {'single_symbol_selection_unverified',
                         'trading_calendar_unverified', 'corporate_actions_unverified'}
    signal_count = 0
    skipped_cash = 0
    skipped_unavailable = 0
    blocked_observations = 0
    skipped_untradable = 0
    realized = Decimal(0)
    wins = 0
    peaks = Decimal(initial_minor) / 100
    max_drawdown = Decimal(0)
    for index, bar in enumerate(bars):
        day = bar['event_date']
        if trade_start_date is not None and day < trade_start_date:
            # Prefix bars are available only to the later indicator evaluation.
            # No trades, equity marks or performance are attributed to warmup.
            continue
        open_at = datetime.combine(datetime.fromisoformat(day).date(), time(9, 30),
                                   tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc)
        decision_at = open_at - timedelta(microseconds=1)
        visible, flags = eligible_bars(bars[:index], decision_at.isoformat(), strict)
        quality.update(flags)
        fresh = bool(index and visible and visible[-1]['event_date'] == bars[index - 1]['event_date'])
        complete = not strict or len(visible) == index
        if index and not fresh:
            quality.add('latest_prior_bar_unavailable')
        if strict and len(visible) < index:
            quality.add('incomplete_strict_history')
        elif not strict and len(visible) < index:
            quality.add('known_future_bars_excluded')
        known_at = None
        if visible and all(item.get('available_at') for item in visible):
            known_at = max(datetime.fromisoformat(item['available_at']) for item in visible).isoformat()
        opening = _price(bar, 'open')
        closing = _price(bar, 'close')
        # Daily volume is a realized fill guard, never an input to the prior-close
        # decision. Preserve the archived strategies' execution behavior.
        execution_has_volume = strategy_id not in CLASSIC_STRATEGY_IDS or int(bar['volume']) > 0
        exited = False
        exit_reason = None
        exit_metrics = {}
        if entry_index is not None:
            if fresh and complete:
                prior_close = _price(visible[-1], 'close')
                peak_close = max(peak_close, prior_close)
                exit_metrics = {'observed_close': str(prior_close), 'observed_date': visible[-1]['event_date'],
                                'entry_price': str(entry_price), 'peak_close': str(peak_close)}
                if stop_loss_pct and prior_close <= entry_price * (1 - stop_loss_pct):
                    exit_reason = 'CLOSE_STOP_LOSS'
                elif take_profit_pct and prior_close >= entry_price * (1 + take_profit_pct):
                    exit_reason = 'CLOSE_TAKE_PROFIT'
                elif trailing_stop_pct and prior_close <= peak_close * (1 - trailing_stop_pct):
                    exit_reason = 'CLOSE_TRAILING_STOP'
                if exit_reason is None and strategy_id in CLASSIC_STRATEGY_IDS:
                    exit_result = evaluate_strategy(strategy_id, symbol=symbol, bars=visible, params=params,
                                                    event_profile=event_profile, quality=flags)
                    quality.update(exit_result.get('quality_flags', []))
                    if exit_result.get('exit_signal') is True:
                        exit_reason = 'CLASSIC_SIGNAL_NEXT_OPEN'
                        exit_metrics.update(strategy_id=strategy_id, source_date=exit_result['source_date'],
                                            evaluation=exit_result['evaluation'], indicator=exit_result['indicator'])
            if exit_reason is None and index - entry_index >= holding_bars:
                exit_reason = 'MAX_HOLDING_BARS'
            if exit_reason is None and index == len(bars) - 1:
                exit_reason = 'SAMPLE_END'
        if entry_index is not None and exit_reason is not None and not execution_has_volume:
            skipped_untradable += 1
            quality.add('zero_volume_execution_skipped')
        if entry_index is not None and exit_reason is not None and execution_has_volume:
            qty = state.quantity
            execution_price = adverse_execution_price(opening, 'sell', slippage_rate)
            sale_fees = calculate_fees(execution_price, qty, 'sell', fees)
            cost = state.cost_basis
            state = apply_fill(replace(state, sellable_quantity=qty), side='sell',
                               price=execution_price, quantity=qty, fees=sale_fees)
            pnl = execution_price * qty - sale_fees.total - cost
            realized += pnl
            wins += int(pnl > 0)
            trades.append({'date': day, 'side': 'sell', 'quantity': qty,
                           'price': str(execution_price), 'reference_price': str(opening),
                           'slippage_rate': str(slippage_rate), 'fees': money_text(_cents(sale_fees.total)),
                           'realized_pnl': money_text(_cents(pnl)), 'reason': exit_reason,
                           'reason_metrics': exit_metrics, 'decision_at': decision_at.isoformat(),
                           'execution_at': open_at.isoformat(), 'known_at': known_at,
                           'holding_bars': index - entry_index})
            entry_index = None
            exited = True
        if not exited and state.quantity == 0 and 0 < index < len(bars) - 1:
            if not fresh or not complete:
                skipped_unavailable += 1
                decisions.append({'date': day, 'decision_at': decision_at.isoformat(),
                                  'status': 'skipped_unavailable', 'source_date': visible[-1]['event_date'] if visible else None,
                                  'signal': None, 'draft_eligible': False,
                                  'reasons': ['LATEST_PRIOR_BAR_UNAVAILABLE' if not fresh else 'INCOMPLETE_STRICT_HISTORY']})
                result = None
            else:
                result = evaluate_strategy(strategy_id, symbol=symbol, bars=visible, params=params,
                                           event_profile=event_profile, quality=flags)
                quality.update(result.get('quality_flags', []))
                evaluation = result.get('evaluation') or {}
                decisions.append({'date': day, 'decision_at': decision_at.isoformat(), 'known_at': known_at,
                                  'status': result['status'], 'source_date': result['source_date'],
                                  'signal': result['signal'], 'draft_eligible': result.get('draft_eligible', False),
                                  'primary_event': evaluation.get('primary_event'),
                                  'trigger_date': evaluation.get('trigger_date'),
                                  'reasons': evaluation.get('reasons') or (result.get('universe') or {}).get('reasons', []),
                                  'draft_block_reason': result.get('draft_block_reason'),
                                  'quality_flags': result.get('quality_flags', [])})
                if result['signal'] is True and result.get('draft_eligible') is not True:
                    blocked_observations += 1
            if result and result['signal'] is True and result.get('draft_eligible') is True:
                signal_count += 1
                if not execution_has_volume:
                    skipped_untradable += 1
                    quality.add('zero_volume_execution_skipped')
                    decisions[-1].update(execution_status='skipped_zero_volume')
                    result = None
            if result and result['signal'] is True and result.get('draft_eligible') is True:
                budget = max(Decimal(0), state.cash - Decimal(cash_buffer_minor) / 100) * max_position_pct
                execution_price = adverse_execution_price(opening, 'buy', slippage_rate)
                qty = int(budget / (execution_price * 100)) * 100
                while qty > 0:
                    buy_fees = calculate_fees(execution_price, qty, 'buy', fees)
                    if execution_price * qty + buy_fees.total <= budget:
                        break
                    qty -= 100
                if qty:
                    state = apply_fill(state, side='buy', price=execution_price, quantity=qty,
                                       fees=buy_fees)
                    entry_index = index
                    entry_price = execution_price
                    peak_close = execution_price
                    evaluation = result.get('evaluation') or {}
                    signal_hash = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                                            allow_nan=False).encode()).hexdigest()
                    trades.append({'date': day, 'side': 'buy', 'quantity': qty,
                                   'price': str(execution_price), 'reference_price': str(opening),
                                   'slippage_rate': str(slippage_rate), 'fees': money_text(_cents(buy_fees.total)),
                                   'realized_pnl': None, 'signal_date': result['source_date'],
                                   'decision_at': decision_at.isoformat(), 'execution_at': open_at.isoformat(),
                                   'known_at': known_at, 'reason': 'STRATEGY_LONG_ELIGIBLE',
                                   'reason_metrics': {'candidate': result.get('candidate'), 'evaluation': evaluation,
                                                      'quality_flags': result.get('quality_flags', [])},
                                   'signal_sha256': signal_hash})
                else:
                    skipped_cash += 1
        mark = state.cash + closing * state.quantity
        peaks = max(peaks, mark)
        if peaks > 0:
            max_drawdown = max(max_drawdown, (peaks - mark) / peaks)
        equity.append({'date': day, 'total_assets': money_text(_cents(mark)),
                       'cash': money_text(_cents(state.cash)), 'quantity': state.quantity})
    start = Decimal(initial_minor) / 100
    sells = [trade for trade in trades if trade['side'] == 'sell']
    ending_assets = state.cash + _price(bars[-1], 'close') * state.quantity
    if state.quantity:
        quality.add('open_position_at_sample_end')
    return {'symbol': symbol, 'strategy_id': strategy_id, 'execution_profile_version': EXECUTION_VERSION,
            'calculation_version': CALCULATION_VERSION,
            'initial_capital': money_text(initial_minor),
            'ending_assets': money_text(_cents(ending_assets)),
            'total_return': str(((ending_assets / start) - 1).quantize(Decimal('0.000001'))),
            'max_drawdown': str(max_drawdown.quantize(Decimal('0.000001'))),
            'trade_count': len(sells),
            'win_rate': str((Decimal(wins) / len(sells)).quantize(Decimal('0.000001'))) if sells else None,
            'signal_count': signal_count, 'skipped_insufficient_cash': skipped_cash,
            'skipped_unavailable': skipped_unavailable, 'blocked_observations': blocked_observations,
            **({'skipped_untradable': skipped_untradable, 'ending_quantity': state.quantity,
                'ending_cash': money_text(_cents(state.cash))} if strategy_id in CLASSIC_STRATEGY_IDS else {}),
            'realized_pnl': money_text(_cents(realized)),
            'quality_flags': sorted(quality), 'trades': trades, 'equity': equity, 'decisions': decisions,
            'event_profile': event_profile,
            **({'evaluation_window': {'trade_start_date': trade_start_date, 'trade_end_date': bars[-1]['event_date'],
                                      'warmup_bars': sum(bar['event_date'] < trade_start_date for bar in bars)}}
               if trade_start_date is not None else {}),
            'slippage_rate': str(slippage_rate),
            'exit_rules': {'holding_bars': holding_bars, 'stop_loss_pct': str(stop_loss_pct),
                           'take_profit_pct': str(take_profit_pct), 'trailing_stop_pct': str(trailing_stop_pct)},
            'limitations': ['仅单股；尚未对照旧矩阵 / 传统回测执行路径',
                            '以执行日 09:30 中国时间之前可得的历史日线判断，缺少前一根行情则跳过',
                            '止损、止盈、移动止损均按已知收盘价判断，在下一次开盘执行；未模拟盘中顺序',
                            ('最大持有期按样本交易日计数，最后一日有成交量才可开盘平仓；未平仓持仓按末日收盘估值'
                             if strategy_id in CLASSIC_STRATEGY_IDS else '最大持有期按样本交易日计数，样本最后一日开盘强制平仓'),
                            '按固定比例施加买入向上、卖出向下的滑点；不代表真实盘口冲击',
                            '无停牌、涨跌停及盘中触发模型']}
