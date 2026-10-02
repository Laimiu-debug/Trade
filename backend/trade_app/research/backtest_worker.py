"""Isolated backtest entry point: frozen JSON in, result JSON out, no database."""
from __future__ import annotations

import json
import sys

from trade_app.platform.compute_process import apply_worker_memory_limit


def main() -> None:
    apply_worker_memory_limit()
    try:
        raw = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('COMPUTE_INPUT_LIMIT')
        payload = json.loads(raw)
        bars = payload['bars']
        if not isinstance(bars, list) or not 32 <= len(bars) <= 2000:
            raise ValueError('INVALID_COMPUTE_BAR_COUNT')
        from decimal import Decimal
        from trade_app.platform.types import money_minor
        from trade_app.research.backtest_domain import run_single_symbol_backtest
        from trade_app.trading.simulation import fee_rule
        config = payload['config']
        result = run_single_symbol_backtest(
            symbol=payload['symbol'], bars=bars, params=payload['params'],
            initial_minor=money_minor(config['initial_capital'], '回测初始资金'),
            holding_bars=config['holding_bars'], max_position_pct=Decimal(config['max_position_pct']),
            fees=fee_rule(config['fee_config']), strict=config['strict'],
            cash_buffer_minor=money_minor(config['fee_config']['cash_buffer'], '现金缓冲', allow_zero=True),
            strategy_id=payload['strategy_id'], event_profile=config.get('event_profile'),
            stop_loss_pct=Decimal(config.get('stop_loss_pct', '0')),
            take_profit_pct=Decimal(config.get('take_profit_pct', '0')),
            trailing_stop_pct=Decimal(config.get('trailing_stop_pct', '0')),
            slippage_rate=Decimal(config['fee_config'].get('slippage_rate', '0')),
            trade_start_date=config.get('trade_start_date'))
        if config.get('advanced_analysis', False):
            from trade_app.research.backtest_analytics import build_backtest_analysis
            result['advanced_analysis'] = build_backtest_analysis(
                result, bars, seed=config.get('analysis_seed', 20260926),
                iterations=config.get('analysis_iterations', 400), strict=config['strict'])
        else:
            result['advanced_analysis'] = {'status': 'not_generated', 'reason': '本次回测未启用高级分析'}
        response = {'ok': True, 'attempt_id': payload['attempt_id'], 'result': result}
    except MemoryError:
        response = {'ok': False, 'error': {'code': 'COMPUTE_MEMORY_LIMIT', 'message': '计算内存超过资源预算'}}
    except Exception as exc:
        response = {'ok': False, 'error': {'code': getattr(exc, 'code', 'COMPUTE_FAILED'),
                                          'message': str(exc)[:500]}}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    sys.stdout.flush()


if __name__ == '__main__':
    main()
