"""Strict, portable laboratory JSON contracts; no legacy cache deserialization."""
from datetime import date
from pathlib import Path

from trade_app.market.domain import normalize_bars
from trade_app.market.symbols import normalize_a_share_symbol
from trade_app.platform.types import TradeError
from trade_app.research import portfolio_domain
from trade_app.research.lab_domain import VERSION, STRATEGIES, RUNTIME_STRATEGIES, normalize_filters, normalize_params
from trade_app.research.portfolio_domain import canonical, digest
from trade_app.research.portfolio_service import normalize_config, code_sha256 as portfolio_code_sha256
from trade_app.research.service import normalize_strategy_params

MAX_INPUT_BYTES = 24 * 1024 * 1024


def strategy_params(strategy, raw):
    if strategy in RUNTIME_STRATEGIES:
        return normalize_strategy_params(strategy, raw)
    return normalize_params(strategy, raw)


def strategy_filters(strategy, raw):
    result = normalize_filters(raw)
    if strategy in RUNTIME_STRATEGIES and (result['rank_bonus'] or result['confirm_type'] != 'any' or
            result['grade'] != 'any' or float(result['min_volume_price_ratio']) != 0 or result['require_ths'] or result['require_rhythm'] or result['sources'] != 'any'):
        raise TradeError('LAB_UNUSED_FILTER', '传统策略不使用混合排名加分、图形确认、图形等级和量价比过滤')
    if strategy != 'hybrid_band_v1' and (result['require_ths'] or result['require_rhythm'] or result['sources'] != 'any'):
        raise TradeError('LAB_UNUSED_FILTER', '仅混合波段使用THS/节奏和来源组合过滤')
    return result


def code_sha256():
    names = ('lab_domain.py', 'lab_context.py', 'lab_worker.py', 'lab_cli.py',
             'lab_presets.py', 'lab_target_fit.py', 'lab_rule_verify.py', 'lab_score_validation.py', 'portfolio_analysis_domain.py',
             'lab_position.py', 'screener_domain.py', 'screener_metrics.py',
             'chart_volume_swing_domain.py', 'hybrid_band_domain.py')
    return digest({'portfolio': portfolio_code_sha256(), 'laboratory': {
        name: digest((Path(__file__).parent / name).read_text(encoding='utf-8')) for name in names}})


def normalize_input(raw):
    allowed = {'format', 'strategy_id', 'params', 'filters', 'config', 'datasets', 'start_date', 'end_date',
               'as_of_date', 'target', 'window_sample_days', 'variants', 'preset_adoption', 'target_fit', 'step1', 'rhythm_verification'}
    if not isinstance(raw, dict) or set(raw) - allowed or raw.get('format') != 'trade-lab-input-v1':
        raise TradeError('INVALID_LAB_INPUT', '需要 trade-lab-input-v1 JSON，且不能含未知字段')
    datasets = raw.get('datasets')
    if not isinstance(datasets, list) or not 1 <= len(datasets) <= 64:
        raise TradeError('LAB_SAMPLE_LIMIT', '实验样本需要1至64个证券')
    frozen, seen = [], set()
    for item in datasets:
        if not isinstance(item, dict) or set(item) - {'symbol', 'bars', 'source_id', 'bars_sha256'}:
            raise TradeError('INVALID_LAB_DATASET', '行情仅接收symbol/bars/source_id/bars_sha256字段')
        symbol = ''.join(normalize_a_share_symbol(item.get('symbol', '')))
        if symbol in seen:
            raise TradeError('LAB_DUPLICATE_SYMBOL', '同一证券只能选择一个冻结数据版本')
        seen.add(symbol)
        bars = item.get('bars')
        if not isinstance(bars, list) or not 80 <= len(bars) <= 2000:
            raise TradeError('LAB_HISTORY_LIMIT', '每个证券需要80至2000根日线')
        if any(not isinstance(bar, dict) or type(bar.get('volume')) is not int or bar['volume'] > 10**15 for bar in bars):
            raise TradeError('INVALID_LAB_VOLUME', '成交量须为非负整数且不超过上限')
        normalized = normalize_bars(bars)
        if item.get('bars_sha256') and item['bars_sha256'] != digest(normalized):
            raise TradeError('LAB_DATASET_CHANGED', '行情规范化摘要与冻结摘要不同')
        source = item.get('source_id')
        if source is not None and (not isinstance(source, str) or len(source) > 128):
            raise TradeError('INVALID_LAB_SOURCE', '来源编号无效')
        frozen.append({'symbol': symbol, 'bars': normalized, 'source_id': source, 'bars_sha256': digest(normalized)})
    if sum(len(item['bars']) for item in frozen) > 60000:
        raise TradeError('LAB_TOTAL_BAR_LIMIT', '实验样本合计最多60000根日线')
    frozen.sort(key=lambda item: item['symbol'])
    calendar = sorted({bar['event_date'] for item in frozen for bar in item['bars']})
    if len(calendar) > 2000:
        raise TradeError('LAB_CALENDAR_LIMIT', '样本日期并集最多2000天')
    strategy = raw.get('strategy_id', 'hybrid_band_v1')
    params = strategy_params(strategy, raw.get('params', {}))
    filters = strategy_filters(strategy, raw.get('filters', {}))
    config = normalize_config(raw.get('config', {}))
    if (config['structural_box_stop'] or config['ma20_box_break']) and strategy not in STRATEGIES:
        raise TradeError('LAB_BOX_SIGNAL_REQUIRED', '结构箱体退出仅支持冻结入场箱顶的图形/混合策略')
    dates = {key: raw.get(key, default) for key, default in (
        ('start_date', calendar[min(80, len(calendar)-2)]), ('end_date', calendar[-1]), ('as_of_date', calendar[-1]))}
    try:
        if any(not isinstance(value, str) or date.fromisoformat(value).isoformat() != value for value in dates.values()):
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise TradeError('INVALID_LAB_DATE', '实验日期须为 YYYY-MM-DD') from exc
    selected = [day for day in calendar if dates['start_date'] <= day <= dates['end_date']]
    if len(selected) < 2 or len(selected) > 366 or dates['as_of_date'] < calendar[0]:
        raise TradeError('LAB_WINDOW_LIMIT', '回测区间须包含2至366个样本日期；观察日期不能早于样本')
    window, target = raw.get('window_sample_days', 20), raw.get('target', .28)
    if type(window) is not int or not 2 <= window <= 240 or isinstance(target, bool) or not isinstance(target, (int, float)) or not 0 <= target <= 10:
        raise TradeError('INVALID_LAB_TARGET', '波段窗口或目标比例无效')
    variants = raw.get('variants', [{}])
    if not isinstance(variants, list) or not 1 <= len(variants) <= 32:
        raise TradeError('LAB_VARIANT_LIMIT', '一次实验需要1至32个明确参数组')
    normalized_variants = []
    for variant in variants:
        if not isinstance(variant, dict) or set(variant) - {'strategy_id', 'params', 'filters', 'config'}:
            raise TradeError('INVALID_LAB_VARIANT', '参数组只接收strategy_id/params/filters/config')
        if any(not isinstance(variant.get(key, {}), dict) for key in ('params', 'filters', 'config')):
            raise TradeError('INVALID_LAB_VARIANT', '参数覆盖必须为对象')
        variant_strategy = variant.get('strategy_id', strategy)
        normalized_variants.append({'strategy_id': variant_strategy,
            'params': strategy_params(variant_strategy, {**(params if strategy == variant_strategy else {}), **variant.get('params', {})}),
            'filters': strategy_filters(variant_strategy, {**filters, **variant.get('filters', {})}),
            'config': normalize_config({**config, **variant.get('config', {})})})
        if (normalized_variants[-1]['config']['structural_box_stop'] or normalized_variants[-1]['config']['ma20_box_break']) and variant_strategy not in STRATEGIES:
            raise TradeError('LAB_BOX_SIGNAL_REQUIRED', '对照策略缺少入场箱顶，不能应用结构箱体退出')
    if len(normalized_variants) * len(selected) * len(frozen) > 50000:
        raise TradeError('LAB_EVALUATION_LIMIT', '参数组×交易日期×证券数量最多50000')
    value = {'format': raw['format'], 'strategy_id': strategy, 'params': params, 'filters': filters,
        'config': config, 'datasets': frozen, **dates, 'window_sample_days': window,
        'target': target, 'variants': normalized_variants}
    from trade_app.research.lab_presets import validate_adoption
    from trade_app.research.lab_target_fit import normalize_fit
    from trade_app.research.lab_position import normalize_step1
    from trade_app.research.lab_rule_verify import normalize_verification
    for key, normalized in (('preset_adoption', validate_adoption(raw.get('preset_adoption'))),
                            ('target_fit', normalize_fit(raw.get('target_fit'), frozen)),
                            ('step1', normalize_step1(raw.get('step1'), frozen)),
                            ('rhythm_verification', normalize_verification(raw.get('rhythm_verification'), frozen))):
        if normalized is not None: value[key] = normalized
    if len(canonical(value).encode('utf-8')) > MAX_INPUT_BYTES:
        raise TradeError('LAB_INPUT_BYTE_LIMIT', '规范化输入超过24MiB')
    return value


def portfolio_context(value, variant=None):
    variant = variant or {key: value[key] for key in ('params', 'config', 'filters')}
    calendar = sorted({bar['event_date'] for item in value['datasets'] for bar in item['bars']})
    config = dict(variant['config'])
    if value.get('step1'):
        # Step1 membership precedes ranking. Preserve raw hits for frozen pools,
        # then apply both explicit caps to eligible execution candidates.
        config['entry_top_k'] = min(config['entry_top_k'] or 2000, variant['filters']['daily_top'])
    return {'version': portfolio_domain.VERSION, 'mode': 'independent_laboratory',
        'strategy_id': variant.get('strategy_id', value['strategy_id']), 'strategy_version': VERSION,
        'universe_scope': 'fixed_research_sample', 'params': variant['params'], 'config': config,
        'filters': variant['filters'], 'event_profile': None, 'step1': value.get('step1'),
        'datasets': [{'dataset_id': item['bars_sha256'], **item} for item in value['datasets']],
        'all_calendar': calendar, 'calendar': [day for day in calendar if value['start_date'] <= day <= value['end_date']]}
