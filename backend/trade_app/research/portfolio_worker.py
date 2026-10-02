"""One bounded portfolio quantum. Frozen JSON only; no database or credentials."""
import json
import sys
from trade_app.platform.compute_process import apply_worker_memory_limit


def main():
    apply_worker_memory_limit()
    try:
        raw = sys.stdin.buffer.read(24 * 1024 * 1024 + 1)
        if len(raw) > 24 * 1024 * 1024:
            raise ValueError('COMPUTE_INPUT_LIMIT')
        payload = json.loads(raw)
        context = payload['context']
        if not 1 <= len(context['datasets']) <= 64 or sum(len(item['bars']) for item in context['datasets']) > 60000:
            raise ValueError('PORTFOLIO_SIZE_LIMIT')
        from trade_app.research.portfolio_domain import run_chunk
        result = run_chunk(context, payload.get('checkpoint'))
        response = {'ok': True, 'attempt_id': payload['attempt_id'], 'result': result}
    except Exception as exc:
        response = {'ok': False, 'error': {'code': getattr(exc, 'code', 'COMPUTE_FAILED'), 'message': str(exc)[:500]}}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    sys.stdout.flush()


if __name__ == '__main__':
    main()
