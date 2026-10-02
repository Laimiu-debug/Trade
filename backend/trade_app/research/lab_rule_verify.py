"""Fixed legacy verification profile, deliberately separate from target fitting."""
from datetime import date

from trade_app.platform.types import TradeError
from trade_app.research.force_rhythm_domain import detect_rhythm_wave_pattern, evaluate_force_rhythm_signal
from trade_app.research.lab_target_fit import collect_snapshots, normalize_fit

VERSION = 'legacy-weike-rule-verification-v1'


def normalize_verification(raw, datasets):
    if raw is None: return None
    if not isinstance(raw, dict) or set(raw) - {'profile', 'symbol', 'date_from', 'date_to', 'want_dates', 'reject_dates'}:
        raise TradeError('LAB_VERIFY_INPUT', '规则验证仅接受证券、日期范围与WANT/REJECT日期')
    if raw.get('profile', VERSION) != VERSION: raise TradeError('LAB_VERIFY_INPUT', '不支持的规则验证口径')
    base = normalize_fit({'symbol': raw.get('symbol'), 'date_from': raw.get('date_from'), 'date_to': raw.get('date_to'),
                          'target_dates': raw.get('want_dates')}, datasets)
    rejected = raw.get('reject_dates', [])
    try:
        if not isinstance(rejected, list) or len(rejected) > 100 or len(set(rejected)) != len(rejected): raise ValueError()
        if any(type(day) is not str or date.fromisoformat(day).isoformat() != day for day in rejected): raise ValueError()
    except (ValueError, TypeError): raise TradeError('LAB_VERIFY_INPUT', 'REJECT日期须为至多100个不重复日期') from None
    dataset = next(row for row in datasets if row['symbol'] == base['symbol'])
    dates = {row['event_date'] for row in dataset['bars'] if base['date_from'] <= row['event_date'] <= base['date_to']}
    if set(rejected) - dates or set(rejected) & set(base['target_dates']):
        raise TradeError('LAB_VERIFY_INPUT', 'REJECT须在冻结观察区间内且不能与WANT重叠')
    return {'profile': VERSION, 'symbol': base['symbol'], 'date_from': base['date_from'], 'date_to': base['date_to'],
            'want_dates': base['target_dates'], 'reject_dates': sorted(rejected)}


def verify_rule(ths, rhythm, *, pattern=None):
    pattern = detect_rhythm_wave_pattern(rhythm) if pattern is None else pattern
    trough = float(rhythm.get('trough_percentile', .5))
    previous, state = str(ths.get('prev_main_force_state') or 'flat'), str(rhythm.get('main_force_state') or 'flat')
    checks = {'pattern': bool(pattern.get('rhythm_pattern_formed')), 'below_peak': trough < .72,
              'retail_allowed': not (bool(rhythm.get('retail_sell_signal')) and trough > .22),
              'flip_band': previous == 'falling' and state == 'rising' and .35 <= trough <= .52,
              'trough_turn_band': bool(rhythm.get('trough_turn')) and .17 <= trough <= .30,
              'deep_trough': trough <= .05 and state == 'falling'}
    reason = ('no_pattern' if not checks['pattern'] else 'peak_zone' if not checks['below_peak'] else
              'retail_block' if not checks['retail_allowed'] else 'flip_band' if checks['flip_band'] else
              'trough_turn_band' if checks['trough_turn_band'] else 'deep_trough' if checks['deep_trough'] else 'miss')
    return {'signal': reason in ('flip_band', 'trough_turn_band', 'deep_trough'), 'reason': reason,
            'checks': checks, 'trough_percentile': trough, 'pattern': pattern}


def verify_dates(context, request):
    want, reject = set(request['want_dates']), set(request['reject_dates'])
    snapshots, excluded = collect_snapshots(context, request['symbol'], request['date_from'], request['date_to'], want | reject)
    rows = [{**snapshot, **verify_rule(snapshot['ths'], snapshot['indicator']),
             'production_evaluation': evaluate_force_rhythm_signal(snapshot['indicator']),
             'want': snapshot['date'] in want, 'reject': snapshot['date'] in reject} for snapshot in snapshots]
    observable = {row['date'] for row in rows}; hits = {row['date'] for row in rows if row['signal']}
    production = {row['date'] for row in rows if row['production_evaluation']['signal']}
    purple = {row['date'] for row in rows if row['purple_flip']}
    return {'version': VERSION, 'scope': 'fixed_research_rule_verification_only', 'symbol': request['symbol'],
        'date_from': request['date_from'], 'date_to': request['date_to'], 'want_dates': sorted(want), 'reject_dates': sorted(reject),
        'hit_dates': sorted(hits), 'want_coverage': {day: day in hits if day in observable else None for day in sorted(want)},
        'missed_want_dates': sorted((want & observable) - hits), 'false_positive_dates': sorted(hits & reject),
        'extra_dates': sorted(hits - want - reject), 'unobservable_want_dates': sorted(want - observable),
        'unobservable_reject_dates': sorted(reject - observable), 'excluded': excluded, 'daily_evaluations': rows,
        'production_signal_dates': sorted(production), 'purple_flip_dates': sorted(purple),
        'target_and_purple_details': [row for row in rows if row['want'] or row['purple_flip']],
        'production_target_coverage': {day: {'purple': day in purple if day in observable else None,
                                           'rhythm': day in production if day in observable else None} for day in sorted(want)},
        'limitations': ['固定旧verify_weike_rules研究谓词，与tune_custom及生产节奏波分开；不创建可执行历史信号',
            '人工WANT/REJECT只作事后标签，不进入指标或谓词；原始每日快照、生产规则、紫转黄同时保留便于比较',
            '未知/迟到的目标证据标为null与独立缺失列表，不作为命中、漏报或通过REJECT的依据']}
