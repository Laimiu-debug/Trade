"""Bounded frozen portfolio diagnostics. No database or runtime configuration."""
import json
import sys
from trade_app.platform.compute_process import apply_worker_memory_limit


def main():
    apply_worker_memory_limit()
    try:
        raw = sys.stdin.buffer.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError('COMPUTE_INPUT_LIMIT')
        payload = json.loads(raw)
        frozen = payload['input']
        context, result = frozen['context'], frozen['result']
        if (not 1 <= len(context['datasets']) <= 64
                or sum(len(item['bars']) for item in context['datasets']) > 60000
                or not 1 <= len(result['equity']) <= 2000):
            raise ValueError('PORTFOLIO_SIZE_LIMIT')
        from trade_app.research.portfolio_analysis_domain import build_analysis
        from trade_app.research.portfolio_plan_domain import build_frozen_plan
        computed = build_analysis(context, result, **{key: value for key, value in frozen['options'].items() if key != 'plan_date'})
        computed['daily_plan'] = build_frozen_plan(context, frozen['plan'])
        plan_date = frozen['plan']['as_of_date']
        computed['daily_detail'] = {'date': plan_date, 'trades': [row for row in result['trades'] if row['date'] == plan_date],
            'equity': next(row for row in result['equity'] if row['date'] == plan_date),
            'decisions': [row for row in result['decisions'] if row['date'] == plan_date]}
        response = {'ok': True, 'attempt_id': payload['attempt_id'], 'result': computed}
    except Exception as exc:
        response = {'ok': False, 'error': {'code': getattr(exc, 'code', 'COMPUTE_FAILED'), 'message': str(exc)[:500]}}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    sys.stdout.flush()


if __name__ == '__main__':
    main()
