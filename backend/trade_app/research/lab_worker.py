"""One bounded laboratory quantum; no database or network access."""
import json
import sys

from trade_app.platform.compute_process import apply_worker_memory_limit


def compute(payload):
    from trade_app.market.domain import eligible_bars
    from trade_app.platform.types import TradeError
    from trade_app.research.lab_domain import signal_rows
    from trade_app.research.portfolio_domain import run_chunk
    context = payload['context']
    from trade_app.research.lab_position import step1_signals
    provider = lambda prefixes, dates: (step1_signals(prefixes, context) if context.get('step1') else
        signal_rows(prefixes, context['strategy_id'], context['params'], context['filters']))
    if payload['operation'] == 'target-fit':
        from trade_app.research.lab_target_fit import fit_targets
        return fit_targets(context, payload['request'])
    if payload['operation'] == 'verify-rhythm':
        from trade_app.research.lab_rule_verify import verify_dates
        return verify_dates(context, payload['request'])
    if payload['operation'] == 'chunk':
        return run_chunk(context, payload.get('checkpoint'), days=5, signal_provider=provider)
    if payload['operation'] == 'scan':
        day = payload['as_of_date']
        # Shanghai end of day; late publications cannot enter the requested date.
        at = day + 'T15:59:59.999999+00:00'
        prefixes, omitted = {}, []
        quality = set()
        for item in context['datasets']:
            prior = [bar for bar in item['bars'] if bar['event_date'] <= day]
            bars, flags = eligible_bars(prior, at, context['config']['execution_strict'])
            if not context['config']['execution_strict'] and any(bar.get('available_at') is None for bar in bars):
                # Shared availability rules already apply the local day-end
                # assumption. Preserve the laboratory's explicit quality flag.
                flags.append('historical_availability_assumed_at_local_close')
            quality.update(flags)
            if not bars or bars[-1]['event_date'] != day or context['config']['execution_strict'] and len(bars) != len(prior):
                omitted.append({'symbol': item['symbol'], 'reason': 'no_complete_fresh_history', 'quality_flags': flags})
            else:
                prefixes[item['symbol']] = bars
        rows = provider(prefixes, [])
        selected = sorted((symbol for symbol, row in rows.items() if row['buy'] and row['in_pool']), key=lambda symbol: (-rows[symbol]['score'], symbol))
        if context.get('step1'):
            # Raw buy predicates remain inspectable for static-pool diagnostics.
            # The plain current-pool selection applies the cap after membership.
            selected = selected[:context['filters']['daily_top']]
            for symbol, row in rows.items():
                row['selected'] = symbol in selected
        return {'as_of_date': day, 'decision_at': at, 'rows': rows, 'omitted': omitted, 'quality_flags': sorted(quality),
            'selected_symbols': selected}
    raise TradeError('UNKNOWN_LAB_OPERATION', '未知实验操作')


def main():
    apply_worker_memory_limit()
    try:
        raw = sys.stdin.buffer.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError('LAB_INPUT_LIMIT')
        payload = json.loads(raw)
        result = compute(payload)
        response = {'ok': True, 'attempt_id': payload['attempt_id'], 'result': result}
    except MemoryError:
        response = {'ok': False, 'error': {'code': 'COMPUTE_MEMORY_LIMIT'}}
    except Exception as exc:
        response = {'ok': False, 'error': {'code': getattr(exc, 'code', 'LAB_COMPUTE_FAILED'), 'message': str(exc)[:300]}}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    sys.stdout.flush()


if __name__ == '__main__':
    main()
