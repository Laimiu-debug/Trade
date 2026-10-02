from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal, ROUND_HALF_UP

from trade_app.platform.types import decimal_text
from trade_app.platform.symbols import market_symbol_key
from trade_app.trading.nav import FlowFact, SnapshotFact, calculate_nav


def project_account(flows: list[FlowFact], snapshots: list[SnapshotFact], trades: list[dict],
                    target_config: dict | None = None) -> dict:
    """Deterministic read model from one captured account input revision."""
    nav = calculate_nav(flows, snapshots, **({
        'multiplier': target_config['multiplier'],
        'node_count': target_config['node_count'],
        'target_version': target_config['version']} if target_config else {}))
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    display_symbols: dict[str, str] = {}
    for trade in sorted(trades, key=lambda item: (item["trade_date"], item["sequence"], item["id"])):
        key = market_symbol_key(trade["symbol"])
        display_symbols.setdefault(key, trade["symbol"])
        by_symbol[key].append(trade)

    positions: list[dict] = []
    rounds: list[dict] = []
    anomalies: list[dict] = []
    for key, items in sorted(by_symbol.items(), key=lambda item: display_symbols[item[0]]):
        symbol = display_symbols[key]
        lots: deque[tuple[int, Decimal]] = deque()
        available = 0
        active: list[str] = []
        buy_gross = Decimal(0)
        sell_gross = Decimal(0)
        paid_fees = Decimal(0)
        round_start = ""
        name = ""
        for trade in items:
            qty = int(trade["quantity"])
            price = Decimal(trade["price"])
            fee = Decimal(trade["fee"])
            name = trade["name"] or name
            if trade["side"] == "buy":
                if not lots:
                    round_start = trade["trade_date"]
                    active = []
                    buy_gross = sell_gross = paid_fees = Decimal(0)
                lots.append((qty, (price * qty + fee) / qty))
                available += qty
                buy_gross += price * qty
                paid_fees += fee
                active.append(trade["id"])
                continue
            if qty > available:
                anomaly = {"trade_id": trade["id"], "symbol": symbol,
                           "date": trade["trade_date"], "reason": "sell_exceeds_recorded_position",
                           'attempted_quantity': qty, 'available_quantity': available}
                anomalies.append(anomaly)
                rounds.append({'id': f"anomaly:{trade['id']}", 'symbol': symbol, 'name': name,
                               'start_date': trade['trade_date'], 'end_date': trade['trade_date'],
                               'status': 'anomaly', 'trade_ids': [trade['id']], 'pnl': None,
                               'reason': anomaly['reason']})
                continue
            available -= qty
            remaining = qty
            while remaining:
                lot_qty, unit_cost = lots[0]
                take = min(remaining, lot_qty)
                remaining -= take
                if take == lot_qty:
                    lots.popleft()
                else:
                    lots[0] = (lot_qty - take, unit_cost)
            sell_gross += price * qty
            paid_fees += fee
            active.append(trade["id"])
            if not lots:
                pnl = sell_gross - buy_gross - paid_fees
                rounds.append({"id": f"round:{active[0]}", "symbol": symbol, "name": name, "start_date": round_start,
                               "end_date": trade["trade_date"], "status": "closed",
                               "trade_ids": active, "pnl": decimal_text(pnl, 2),
                               'close_sequence': trade['sequence']})
                active = []
        if lots:
            positions.append({"symbol": symbol, "name": name,
                              "quantity": sum(lot[0] for lot in lots),
                              "cost_basis": decimal_text(sum(Decimal(q) * cost for q, cost in lots), 2)})
            rounds.append({"id": f"round:{active[0]}", "symbol": symbol, "name": name, "start_date": round_start,
                           "end_date": None, "status": "open", "trade_ids": active, "pnl": None})

    closed = [row for row in rounds if row["status"] == "closed"]
    winners = [row for row in closed if Decimal(row["pnl"]) > 0]
    losers = [row for row in closed if Decimal(row["pnl"]) < 0]
    win_rate = (Decimal(len(winners)) / len(closed) * 100) if closed else None
    avg_win = sum((Decimal(row["pnl"]) for row in winners), Decimal(0)) / len(winners) if winners else None
    avg_loss = -sum((Decimal(row["pnl"]) for row in losers), Decimal(0)) / len(losers) if losers else None
    payoff = avg_win / avg_loss if avg_win is not None and avg_loss else None
    gross_profit = sum((Decimal(row['pnl']) for row in winners), Decimal(0))
    gross_loss = -sum((Decimal(row['pnl']) for row in losers), Decimal(0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    streak_wins = streak_losses = max_wins = max_losses = 0
    for row in sorted(closed, key=lambda item: (item['end_date'], item['close_sequence'], item['id'])):
        pnl = Decimal(row['pnl'])
        streak_wins = streak_wins + 1 if pnl > 0 else 0
        streak_losses = streak_losses + 1 if pnl < 0 else 0
        max_wins = max(max_wins, streak_wins)
        max_losses = max(max_losses, streak_losses)
    return {
        "nav": nav,
        "positions": positions,
        "rounds": rounds,
        "anomalies": anomalies,
        "trade_stats": {"trade_count": len(trades), "closed_rounds": len(closed),
                        "winning_rounds": len(winners), "losing_rounds": len(losers),
                        "flat_rounds": len(closed) - len(winners) - len(losers),
                        "win_rate_pct": decimal_text(win_rate, 2) if win_rate is not None else None,
                        "payoff_ratio": decimal_text(payoff, 4) if payoff is not None else None,
                        'profit_factor': decimal_text(profit_factor, 4) if profit_factor is not None else None,
                        'max_consecutive_wins': max_wins, 'max_consecutive_losses': max_losses,
                        'current_consecutive_wins': streak_wins,
                        'current_consecutive_losses': streak_losses},
    }
