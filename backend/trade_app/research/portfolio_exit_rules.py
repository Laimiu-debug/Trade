"""Optional causal band exits. All activation inputs predate the open."""
from decimal import Decimal

VERSION = 'known-band-peak-time-ma10-breakeven-box-v1'


def enabled(config):
    return (Decimal(config.get('trail_activate', '0')) > 0 or config.get('time_stop_days', 0) > 0
        or Decimal(config.get('breakeven_trigger', '0')) > 0 or config.get('structural_box_stop', False)
        or config.get('ma20_box_break', False))


def known_band_exit(position, prior, ma10, ma20, config):
    if not enabled(config): return None, {}
    entry, close = Decimal(position['entry_price']), Decimal(prior['close'])
    peak = max(Decimal(position.get('band_peak', position['entry_price'])), Decimal(prior['high']))
    position['band_peak'] = str(peak)
    position['band_observed_date'] = prior['event_date']
    gain, held = peak / entry - 1, max(0, position['held_bars'] - 1)
    trigger = Decimal(config.get('breakeven_trigger', '0'))
    if trigger > 0 and gain >= trigger:
        position['breakeven_active'] = True
    details = {'known_band_peak': str(peak), 'peak_gain': str(gain), 'completed_post_entry_bars': held,
        'observed_date': prior['event_date'], 'observed_close': str(close),
        'breakeven_active': bool(position.get('breakeven_active')), 'activation_scope': 'known_prior_bar_only'}
    if config.get('time_stop_days', 0) > 0 and held >= config['time_stop_days'] and gain < Decimal(config['time_stop_min_gain']):
        return 'BAND_TIME_STOP_NEXT_OPEN', details
    box = position.get('entry_box_high')
    if config.get('ma20_box_break', False) and box and ma20 and close < Decimal(str(ma20)) * Decimal('.97') and close < Decimal(box) * Decimal('.95'):
        return 'BAND_MA20_BOX_BREAK_NEXT_OPEN', {**details, 'ma20': str(ma20), 'entry_box_high': box}
    activate = Decimal(config.get('trail_activate', '0'))
    if activate > 0 and gain >= activate and ma10 and close < Decimal(str(ma10)):
        return 'BAND_MA10_TRAIL_NEXT_OPEN', {**details, 'ma10': str(ma10)}
    return None, details


def effective_stop(position, config):
    entry = Decimal(position['entry_price'])
    values = []
    if Decimal(config['stop_loss_pct']) > 0: values.append((entry * (1 - Decimal(config['stop_loss_pct'])), 'STOP_LOSS'))
    if config.get('structural_box_stop', False) and position.get('entry_box_high'):
        values.append((Decimal(position['entry_box_high']) * Decimal('.95'), 'BOX_STRUCTURE_STOP'))
    if position.get('breakeven_active'):
        values.append((entry * Decimal('1.002'), 'BREAKEVEN_STOP'))
    # Later rules win exact-price ties: keep the active protection explanation.
    return max(enumerate(values), key=lambda row: (row[1][0], row[0]))[1] if values else (None, None)
