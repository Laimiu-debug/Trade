"""Data-only portable portfolio reports, retaining the original checkpoint chain."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, DecimalException
import html
import json
import re

from trade_app.market.domain import normalize_bars
from trade_app.platform.types import TradeError
from trade_app.research.report_domain import (MAX_PACKAGE_BYTES, _keys, _text,
    digest, encode, package_report, strict_json, unpack_report)

FORMAT = 'trade.fixed-sample-portfolio'
SCOPE = 'fixed_sample_portfolio'
VERSIONS = {'fixed-sample-causal-portfolio-v1', 'fixed-sample-causal-portfolio-shanghai-marks-v2', 'fixed-sample-causal-portfolio-known-band-exits-v3'}
MODES = {'matrix_raw_s1_s9', 'traditional_runtime14', 'aligned_wyckoff_events'}
HEX = re.compile(r'^[a-f0-9]{64}$')


def _number(value):
    # Portfolio ratios retain Decimal's 28-digit division results. Keep those
    # exact frozen values; truncating them would invalidate the evidence chain.
    if isinstance(value, bool) or len(str(value)) > 64:
        raise ValueError('Invalid numeric field')
    number = Decimal(str(value))
    if not number.is_finite() or abs(number) > Decimal('1e18') or number.as_tuple().exponent < -40:
        raise ValueError('Unbounded numeric field')
    return number


def _sha(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError('Invalid digest')


def _date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Invalid date')


def _timestamp(value, *, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
        raise ValueError('Invalid timestamp')


def _quantity(value, *, zero=True):
    if type(value) is not int or not (0 if zero else 1) <= value <= 10**18:
        raise ValueError('Invalid quantity')


def _positions(positions):
    if not isinstance(positions, dict) or len(positions) > 64:
        raise ValueError('Invalid positions')
    for symbol, item in positions.items():
        _text(symbol, 24)
        _quantity(item['quantity'])
        if 'cost' in item:
            _number(item['cost'])
        if 'mark' in item:
            _number(item['mark'])


def validate_portfolio_report(value):
    """Validate bounded format and integrity; never recompute or trust performance."""
    try:
        _keys(value, {'format', 'version', 'scope', 'title', 'created_at', 'source', 'input', 'initial_checkpoint', 'summary', 'chunks'}
              | ({'analysis'} if value.get('version') == 2 else set()))
        if value['format'] != FORMAT or type(value['version']) is not int or value['version'] not in (1, 2) or value['scope'] != SCOPE:
            raise ValueError('Unknown portfolio report format')
        _text(value['title'], 120)
        _timestamp(value['created_at'])
        source, context, initial, result = value['source'], value['input'], value['initial_checkpoint'], value['summary']
        _keys(source, {'run_id', 'state', 'created_at', 'updated_at', 'input_sha256', 'result_sha256'})
        if source['state'] != 'succeeded':
            raise ValueError('Completed source required')
        _text(source['run_id'])
        _timestamp(source['created_at'])
        _timestamp(source['updated_at'])
        _sha(source['input_sha256'])
        _sha(source['result_sha256'])
        _keys(context, {'version', 'mode', 'strategy_id', 'strategy_version', 'universe_scope', 'params', 'event_profile',
                        'datasets', 'all_calendar', 'calendar', 'config', 'code_sha256', 'budget'})
        if context['version'] not in VERSIONS or context['mode'] not in MODES or context['universe_scope'] != 'fixed_research_sample':
            raise ValueError('Unsupported portfolio protocol')
        _text(context['strategy_id'])
        _text(context['strategy_version'])
        _sha(context['code_sha256'])
        if not isinstance(context['params'], dict) or not isinstance(context['config'], dict) or not isinstance(context['budget'], dict):
            raise ValueError('Invalid frozen configuration')
        if digest(encode(context)) != source['input_sha256']:
            raise ValueError('Input digest mismatch')
        if not isinstance(context['datasets'], list) or not 1 <= len(context['datasets']) <= 64:
            raise ValueError('Invalid dataset count')
        dates, symbols, count = set(), set(), 0
        for item in context['datasets']:
            _keys(item, {'dataset_id', 'symbol', 'bars', 'bars_sha256'})
            _text(item['dataset_id'])
            _text(item['symbol'], 24)
            _sha(item['bars_sha256'])
            if item['symbol'] in symbols or not isinstance(item['bars'], list) or not 32 <= len(item['bars']) <= 2000:
                raise ValueError('Duplicate symbol or invalid bars')
            symbols.add(item['symbol'])
            normalized = normalize_bars(item['bars'])
            if normalized != item['bars'] or digest(encode(normalized)) != item['bars_sha256']:
                raise ValueError('Frozen bar digest mismatch')
            count += len(normalized)
            dates.update(bar['event_date'] for bar in normalized)
        if count > 60000 or len(dates) > 2000 or context['all_calendar'] != sorted(dates):
            raise ValueError('Calendar or resource mismatch')
        calendar = context['calendar']
        if not isinstance(calendar, list) or not 2 <= len(calendar) <= 2000 or calendar != sorted(set(calendar)) or set(calendar) - dates:
            raise ValueError('Invalid evaluation dates')
        for day in calendar:
            _date(day)
        if not isinstance(initial, dict) or initial.get('version') != context['version'] or initial.get('cursor') != 0 or initial.get('positions') != {} or initial.get('pending') != {}:
            raise ValueError('Invalid initial checkpoint')
        if _number(initial['cash']) != _number(context['config']['initial_capital']):
            raise ValueError('Initial funding mismatch')
        if not isinstance(result, dict) or result.get('version') != context['version'] or result.get('mode') != context['mode'] or result.get('strategy_id') != context['strategy_id']:
            raise ValueError('Result identity mismatch')
        if result.get('universe_scope') != 'fixed_research_sample':
            raise ValueError('Missing fixed-sample scope')
        for key in ('initial_capital', 'ending_assets', 'total_return', 'max_drawdown', 'realized_pnl', 'total_fees'):
            _number(result[key])
        for key in ('buy_count', 'sell_count', 'completed_days', 'total_days'):
            _quantity(result[key])
        for key in ('quality_flags', 'limitations'):
            if not isinstance(result.get(key), list) or len(result[key]) > 128:
                raise ValueError('Missing quality metadata')
            for text in result[key]:
                _text(text, 200 if key == 'quality_flags' else 2000)
        if 'selection_membership_unverified' not in result['quality_flags']:
            raise ValueError('Fixed sample caveat cannot be removed')
        _positions(result['open_positions'])
        chunks = value['chunks']
        if not isinstance(chunks, list) or not 1 <= len(chunks) <= 400:
            raise ValueError('Invalid checkpoint count')
        previous, cursor, equity_dates = digest(encode(initial)), 0, []
        buys = sells = 0
        for ordinal, chunk in enumerate(chunks):
            _keys(chunk, {'ordinal', 'prior_sha256', 'result_sha256', 'result'})
            if type(chunk['ordinal']) is not int or chunk['ordinal'] != ordinal or chunk['prior_sha256'] != previous:
                raise ValueError('Checkpoint chain mismatch')
            _sha(chunk['result_sha256'])
            part = chunk['result']
            _keys(part, {'checkpoint', 'trades', 'equity', 'pool_history', 'decisions', 'done'})
            if digest(encode(part)) != chunk['result_sha256']:
                raise ValueError('Checkpoint content mismatch')
            checkpoint = part['checkpoint']
            expected = min(len(calendar), cursor + 5)
            if (not isinstance(checkpoint, dict) or checkpoint.get('version') != context['version']
                    or type(checkpoint.get('cursor')) is not int or checkpoint['cursor'] != expected
                    or type(part['done']) is not bool or part['done'] != (expected == len(calendar))):
                raise ValueError('Checkpoint cursor mismatch')
            for key in ('cash', 'ending_assets', 'peak_equity', 'max_drawdown', 'realized_pnl', 'total_fees'):
                _number(checkpoint[key])
            _positions(checkpoint['positions'])
            for key, limit in (('trades', 1920), ('equity', 5), ('pool_history', 5), ('decisions', 1280)):
                if not isinstance(part[key], list) or len(part[key]) > limit or any(not isinstance(row, dict) for row in part[key]):
                    raise ValueError('Invalid checkpoint rows')
            if [row.get('date') for row in part['equity']] != calendar[cursor:expected]:
                raise ValueError('Equity dates mismatch')
            for row in part['equity']:
                for key in ('total_assets', 'cash'):
                    _number(row[key])
                _quantity(row['position_count'])
                _positions(row['positions'])
                equity_dates.append(row['date'])
            for row in part['trades']:
                if row.get('date') not in calendar[cursor:expected] or row.get('symbol') not in symbols or row.get('side') not in ('buy', 'sell'):
                    raise ValueError('Trade identity mismatch')
                _quantity(row['quantity'], zero=False)
                if row['quantity'] % 100 or row.get('phase') not in ('open', 'intraday_ohlc'):
                    raise ValueError('Trade execution mismatch')
                for key in ('price', 'reference_price', 'slippage_rate', 'fees', 'commission', 'stamp', 'transfer'):
                    _number(row[key])
                _text(row['reason'])
                for key in ('execution_at', 'known_at', 'decision_at'):
                    _timestamp(row.get(key), nullable=True)
                if row['side'] == 'buy':
                    buys += 1
                    _date(row['signal_date'])
                    if row['signal_date'] >= row['date']:
                        raise ValueError('Same-day close entry is unsupported')
                else:
                    sells += 1
                    _number(row['realized_pnl'])
            for row in part['pool_history']:
                if row.get('date') not in calendar[cursor:expected] or row.get('scope') != 'fixed_research_sample':
                    raise ValueError('Pool scope mismatch')
                _timestamp(row['decision_at'])
                if not isinstance(row.get('members'), list) or set(row['members']) - symbols or not isinstance(row.get('rows'), list) or len(row['rows']) != len(symbols):
                    raise ValueError('Invalid pool membership')
            previous, cursor = digest(encode(checkpoint)), expected
        if (cursor != len(calendar) or equity_dates != calendar or result['completed_days'] != cursor or result['total_days'] != cursor
                or result['buy_count'] != buys or result['sell_count'] != sells):
            raise ValueError('Incomplete result')
        if digest(encode({'summary': result, 'chunks': [chunk['result_sha256'] for chunk in chunks], 'input_sha256': source['input_sha256']})) != source['result_sha256']:
            raise ValueError('Final result digest mismatch')
        if value['version'] == 2:
            analysis = value['analysis']
            _keys(analysis, {'id', 'version', 'code_sha256', 'options', 'input_sha256', 'source_result_sha256', 'result_sha256', 'result'})
            _text(analysis['id'])
            if analysis['version'] not in ('portfolio-complete-cycles-daily-block-bootstrap-v1',
                                           'portfolio-complete-cycles-daily-block-bootstrap-v2'):
                raise ValueError('Unknown analysis version')
            for key in ('code_sha256', 'input_sha256', 'source_result_sha256', 'result_sha256'): _sha(analysis[key])
            if analysis['source_result_sha256'] != source['result_sha256'] or digest(encode(analysis['result'])) != analysis['result_sha256']:
                raise ValueError('Analysis source or result digest mismatch')
            _keys(analysis['options'], {'seed', 'iterations', 'block_size', 'plan_date'})
            for key, lo, hi in (('seed', 0, 2**32 - 1), ('iterations', 100, 2000), ('block_size', 1, 60)):
                if type(analysis['options'][key]) is not int or not lo <= analysis['options'][key] <= hi:
                    raise ValueError('Invalid analysis options')
            if analysis['options']['plan_date'] not in calendar: raise ValueError('Invalid plan date')
            report = analysis['result']
            _keys(report, {'version', 'status', 'risk', 'stability', 'regimes', 'monte_carlo', 'completed_trades', 'open_cycles',
                'quality_flags', 'walk_forward', 'methodology', 'daily_plan', 'daily_detail'})
            if report['version'] != analysis['version'] or report['status'] != 'generated': raise ValueError('Invalid analysis result')
            for key in ('risk', 'stability', 'regimes', 'monte_carlo', 'walk_forward', 'methodology', 'daily_plan', 'daily_detail'):
                if not isinstance(report[key], dict): raise ValueError('Invalid analysis section')
            for key in ('completed_trades', 'open_cycles', 'quality_flags'):
                if not isinstance(report[key], list): raise ValueError('Invalid analysis rows')
            if (report['daily_detail']['date'] != analysis['options']['plan_date']
                    or report['daily_plan']['as_of_date'] != analysis['options']['plan_date']):
                raise ValueError('Analysis plan date mismatch')
            for key in ('seed', 'block_size'):
                if report['monte_carlo'][key] != analysis['options'][key]: raise ValueError('Bootstrap options mismatch')
            if report['monte_carlo']['requested_iterations'] != analysis['options']['iterations']: raise ValueError('Bootstrap iterations mismatch')
        return value
    except (KeyError, TypeError, ValueError, DecimalException, TradeError) as exc:
        raise TradeError('INVALID_PORTFOLIO_REPORT', '组合报告的格式、行情、成交或检查点摘要校验失败') from exc


def flatten(payload):
    return {**payload['summary'], **{key: [row for chunk in payload['chunks'] for row in chunk['result'][key]]
            for key in ('trades', 'equity', 'pool_history', 'decisions')}}


def render_portfolio_html(payload, *, origin='local'):
    validate_portfolio_report(payload)
    result, context = flatten(payload), payload['input']
    def esc(value):
        return html.escape(json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else '未知' if value is None else str(value), quote=True)
    def table(headers, rows):
        return '<div class="table"><table><thead><tr>' + ''.join(f'<th>{esc(title)}</th>' for _, title in headers) + '</tr></thead><tbody>' + ''.join(
            '<tr>' + ''.join(f'<td>{esc(row.get(key))}</td>' for key, _ in headers) + '</tr>' for row in rows) + '</tbody></table></div>'
    values = [Decimal(row['total_assets']) for row in result['equity']]
    low, high = min(values), max(values)
    points = ' '.join(f'{20 + index * 920 / max(1, len(values)-1):.2f},{180 - float((value-low)/(high-low or 1))*150:.2f}' for index, value in enumerate(values))
    curve = f'<svg viewBox="0 0 960 210" role="img" aria-label="组合权益曲线"><polyline points="{points}" fill="none" stroke="#0a6b54" stroke-width="3"/></svg>'
    trades = table([('date', '日期'), ('symbol', '证券'), ('side', '方向'), ('quantity', '数量'), ('phase', '阶段'),
        ('reference_price', '参考价'), ('price', '成交价'), ('slippage_rate', '滑点'), ('fees', '费用'),
        ('realized_pnl', '已实现盈亏'), ('known_at', '可知时间'), ('reason', '原因'), ('reason_metrics', '执行依据')], result['trades'])
    pools = ''.join(f'<details><summary>{esc(pool["date"])} · {len(pool["members"])} 个成员</summary><p>{esc(pool["decision_at"])}</p>' +
        table([('symbol', '证券'), ('in_pool', '入池'), ('score', '分数'), ('known_at', '可知时间'), ('reason', '原因'), ('components', '完整信号证据')], pool['rows']) + '</details>' for pool in result['pool_history'])
    datasets = [{'symbol': item['symbol'], 'rows': len(item['bars']), 'first': item['bars'][0]['event_date'], 'last': item['bars'][-1]['event_date'], 'sha256': item['bars_sha256']} for item in context['datasets']]
    analysis_html = '<section><h2>高级分析与条件计划</h2><p>未附加已完成分析；未生成指标不记为0。</p></section>'
    if payload.get('analysis'):
        analysis = payload['analysis']
        sections = [('risk', '风险指标'), ('stability', '稳定性与月度收益'), ('regimes', '入场前已知行情分组'),
            ('monte_carlo', '组合日收益区块 Bootstrap'), ('daily_detail', '指定日冻结成交与估值'),
            ('daily_plan', '日终条件计划，不是委托'), ('completed_trades', '完整清仓周期'), ('open_cycles', '未完整清仓周期'),
            ('methodology', '方法与空值原因')]
        analysis_html = '<section><h2>已冻结高级分析与条件计划</h2><p>样本内描述统计及有条件抽样，不预测未来；独立Walk-forward结果不在此处伪造。</p>' + ''.join(
            f'<details><summary>{esc(title)}</summary><pre>{esc(analysis["result"][key])}</pre></details>' for key, title in sections) + f'<pre>{esc({key: value for key, value in analysis.items() if key != "result"})}</pre></section>'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"><title>{esc(payload['title'])}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#eef3f0;color:#18342c;font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1280px;margin:auto;padding:32px 24px}}header{{border-top:5px solid #0a6b54;padding:20px 0}}section{{background:#fff;border:1px solid #dce5df;border-radius:12px;padding:20px;margin:16px 0}}h1{{font-size:30px}}h2{{font-size:20px}}table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{padding:8px;border-bottom:1px solid #dce5df;vertical-align:top;overflow-wrap:anywhere}}th{{text-align:left;background:#f3f7f5}}.table{{overflow-x:auto}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}svg{{width:100%;max-height:260px}}details{{margin:10px 0}}.muted{{color:#62736c}}@media print{{body{{background:white}}main{{padding:0}}section{{border-color:#aaa}}}}
</style></head><body><main><header><p>TRADE · 固定研究样本组合报告</p><h1>{esc(payload['title'])}</h1><p>{esc(context['mode'])} · {len(datasets)} 个证券 · {esc(context['calendar'][0])} — {esc(context['calendar'][-1])}</p><p>{'导入快照，未重新计算' if origin == 'import' else '本地已完成组合快照'} · {esc(payload['created_at'])}</p></header>
<section><h2>组合结果</h2>{table([('label','指标'),('value','值')],[{'label': label, 'value': result[key]} for key,label in [('initial_capital','初始资金'),('ending_assets','期末资产'),('total_return','收益比例'),('max_drawdown','最大回撤比例'),('buy_count','买入笔数'),('sell_count','卖出腿数'),('realized_pnl','已实现盈亏'),('total_fees','手续费')]])}{curve}</section>
<section><h2>范围和数据质量</h2><p>固定研究样本不等同于历史全市场。日线触达不能证明分钟顺序；包内SHA只验证内容完整，不是对第三方绩效的认证。</p><p>{esc(result['quality_flags'])}</p><ul>{''.join(f'<li>{esc(item)}</li>' for item in result['limitations'])}</ul></section>
<section><h2>冻结参数与事件模板</h2><pre>{esc({'params': context['params'], 'config': context['config'], 'event_profile': context['event_profile']})}</pre></section>
<section><h2>成交与资金阶段 · {len(result['trades'])} 笔</h2>{trades}</section><section><h2>股票池与信号门槛</h2>{pools}</section>
<section><h2>每日现金和持仓</h2>{table([('date','日期'),('total_assets','总资产'),('cash','现金'),('position_count','证券数'),('positions','数量与估值')],result['equity'])}<h3>期末保留持仓</h3><pre>{esc(result['open_positions'])}</pre></section>
<section><h2>未成交原因</h2>{table([('date','日期'),('symbol','证券'),('status','状态'),('reason','原因')],result['decisions'])}</section>
{analysis_html}<section><h2>输入日线与摘要链</h2>{table([('symbol','证券'),('rows','日线数'),('first','首日'),('last','末日'),('sha256','行情摘要')],datasets)}<pre>{esc({'source': payload['source'], 'protocol': context['version'], 'strategy_version': context['strategy_version'], 'code_sha256': context['code_sha256'], 'checkpoint_hashes': [chunk['result_sha256'] for chunk in payload['chunks']]})}</pre><p class="muted">全部输入日线和原始分块在包内report.json，Excel也提供独立行情和检查点工作表。导入不会执行任何策略。</p></section></main></body></html>'''


def package_portfolio_report(payload, workbook, *, origin='local'):
    return package_report(payload, workbook, origin=origin, scope=SCOPE,
                          validator=validate_portfolio_report, renderer=render_portfolio_html)


def unpack_portfolio_report(contents):
    return unpack_report(contents, scope=SCOPE, validator=validate_portfolio_report)
