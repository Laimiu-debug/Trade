"""Small, read-only evidence rows for share cards; never recompute historical runs."""
import json

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from trade_app.market.service import list_datasets
from trade_app.market.symbols import market_symbol_key
from trade_app.platform.types import TradeError
from trade_app.research.models import ResearchRun


def stock_research_history(session: Session, symbol: str, limit: int = 50) -> dict:
    identity = market_symbol_key(symbol)
    if not identity:
        raise TradeError('INVALID_SYMBOL', '请选择有效证券代码')
    datasets = [row for row in list_datasets(session) if market_symbol_key(row['symbol']) == identity]
    ids = [row['id'] for row in datasets]
    result = {'symbol': identity, 'items': [], 'limit_per_kind': limit,
              'scope': 'saved_research_on_all_frozen_versions',
              'note': '历史记录是已保存的事后研究证据，记录创建时间与策略观察日期分别列出，不证明当时已实际生成。'}
    if not ids:
        return result
    items = []
    for row in session.execute(select(ResearchRun.id, ResearchRun.dataset_id, ResearchRun.strategy_id,
            ResearchRun.decision_at, ResearchRun.created_at,
            func.json_extract(ResearchRun.result_json, '$.status').label('status'),
            func.json_extract(ResearchRun.result_json, '$.signal').label('signal'))
            .where(ResearchRun.dataset_id.in_(ids)).order_by(ResearchRun.decision_at.desc(), ResearchRun.id.desc()).limit(limit)).mappings():
        items.append({'kind': 'strategy', 'run_id': row['id'], 'dataset_id': row['dataset_id'],
                      'observed_at': row['decision_at'], 'created_at': row['created_at'],
                      'strategy_id': row['strategy_id'], 'outcome': 'signal' if row['signal'] else 'no_signal' if row['status'] == 'computed' else 'insufficient',
                      'detail_path': '/research/runs/' + row['id']})
    # SQL selects bounded scalar evidence, not full universe result bodies.
    # Table names and JSON paths below are fixed program constants, never input.
    dataset_expr = "CASE WHEN source.type='object' THEN json_extract(source.value,'$.dataset_id') ELSE source.value END"
    for kind, table in (('screener', 'screener_runs'), ('b1', 'b1_runs')):
        stages = ('step4', 'step3', 'step2', 'step1', 'input') if kind == 'screener' else ('hits',)
        cases = []
        if kind == 'b1':
            cases.append(f"WHEN EXISTS (SELECT 1 FROM json_each(r.result_json,'$.excluded') hit WHERE json_extract(hit.value,'$.dataset_id')={dataset_expr}) THEN 'insufficient'")
        for stage in stages:
            path = '$.pools.' + stage if kind == 'screener' else '$.hits'
            cases.append(f"WHEN EXISTS (SELECT 1 FROM json_each(r.result_json, '{path}') hit WHERE json_extract(hit.value,'$.dataset_id')={dataset_expr}) THEN '{stage}'")
        outcome = 'CASE ' + ' '.join(cases) + (" ELSE 'excluded' END" if kind == 'screener' else " ELSE 'no_hit' END")
        request_path = "'$.datasets'" if kind == 'screener' else "CASE WHEN json_type(r.request_json,'$.dataset_ids')='array' THEN '$.dataset_ids' ELSE '$.datasets' END"
        rows = session.execute(text(f"""SELECT r.id, r.created_at,
            json_extract(r.result_json,'$.as_of_date') AS observed_at,
            {dataset_expr} AS dataset_id, {outcome} AS outcome
            FROM {table} r, json_each(r.request_json, {request_path}) source
            WHERE {dataset_expr} IN (SELECT value FROM json_each(:datasets))
            ORDER BY observed_at DESC, r.created_at DESC, r.id DESC LIMIT :limit
            """), {'datasets': json.dumps(ids), 'limit': limit}).mappings()
        for row in rows:
            items.append({'kind': kind, 'run_id': row['id'], 'dataset_id': row['dataset_id'],
                          'observed_at': row['observed_at'], 'created_at': row['created_at'],
                          'outcome': row['outcome'], 'detail_path': f'/research/{"screener" if kind == "screener" else "b1"}-runs/{row["id"]}'})
    result['items'] = sorted(items, key=lambda row: (row['observed_at'] or '', row['created_at'], row['run_id']), reverse=True)
    return result
