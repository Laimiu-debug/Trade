from copy import deepcopy
from types import MethodType, SimpleNamespace

import pytest

from app.store import InMemoryStore
from trade_app.platform.types import TradeError
from trade_app.research.event_profiles import (
    DEFAULT_PROFILE_ID, event_profile_catalog, get_system_profile, normalize_profile, profile_sha256,
)


STAMP = '2026-09-26T00:00:00+00:00'


def _legacy():
    # Bind only pure methods to a data-only harness. Never initialize InMemoryStore.
    options = InMemoryStore._build_event_judgment_rule_options()
    harness = SimpleNamespace(_event_judgment_rule_options=options,
                              _event_judgment_rule_option_map={item['rule_key']: item for item in options},
                              _event_judgment_metric_options=InMemoryStore._build_event_judgment_metric_options(),
                              _now_datetime=lambda: STAMP)
    for name in ('_default_event_judgment_rule_values', '_normalize_event_judgment_rule_value',
                 '_normalize_event_judgment_rule_values', '_normalize_event_judgment_dimension'):
        setattr(harness, name, MethodType(getattr(InMemoryStore, name), harness))
    return harness


def test_frozen_system_profiles_and_schemas_match_original_pure_definitions():
    catalog = event_profile_catalog()
    old = _legacy()
    assert catalog['default_profile_id'] == InMemoryStore._default_event_judgment_profile_id() == DEFAULT_PROFILE_ID
    assert catalog['metric_options'] == old._event_judgment_metric_options
    assert catalog['rule_options'] == old._event_judgment_rule_options
    assert catalog['profiles'] == list(InMemoryStore._build_event_judgment_system_profiles(old).values())
    for profile in catalog['profiles']:
        normalized = normalize_profile(profile, profile_id=profile['profile_id'], updated_at=STAMP, is_system=True)
        assert normalized == profile


def test_custom_profile_normalization_matches_original_for_valid_inputs():
    raw = {'name': ' 自定义事件 ', 'description': ' 固定样本 ', 'score_mode': 'dimension_weighted',
           'dimensions': [{'metric_key': 'event_background_score', 'weight': 1.23456789},
                          {'dimension_id': 'risk', 'label': '风险', 'metric_key': 'risk_score',
                           'weight': 2, 'invert': True, 'enabled': True}],
           'rule_values': [{'rule_key': 'sos_vol_ratio_min', 'value': 1.25},
                           {'rule_key': 'enable_spring', 'value': False},
                           {'rule_key': 'lookback_core_days', 'value': 60}]}
    fallback = [{'rule_key': 'sc_scan_lookback_days', 'value': 40},
                {'rule_key': 'lookback_core_days', 'value': 50}]
    actual = normalize_profile(raw, profile_id='custom_1', updated_at=STAMP,
                               fallback_rule_values=fallback)
    expected = InMemoryStore._normalize_event_judgment_profile(
        _legacy(), raw, is_system=False, fallback_profile_id='custom_1',
        fallback_name='事件判别模板', fallback_updated_at=STAMP, fallback_rule_values=fallback)
    assert actual == expected
    assert len(actual['rule_values']) == 82
    assert actual['dimensions'][0]['dimension_id'] == 'dim_1'


@pytest.mark.parametrize('updates', [
    {'unknown': True}, {'score_mode': 'typo'}, {'name': 'a' * 65},
    {'dimensions': [{'metric_key': 'does_not_exist'}]},
    {'dimensions': [{'metric_key': 'risk_score', 'weight': 10.1}]},
    {'dimensions': [{'metric_key': 'risk_score', 'weight': float('nan')}]},
    {'dimensions': [{'metric_key': 'risk_score', 'invert': 'false'}]},
    {'dimensions': [{'metric_key': 'risk_score', 'enabled': False}]},
    {'dimensions': [{'dimension_id': 'x', 'metric_key': 'risk_score'},
                    {'dimension_id': 'x', 'metric_key': 'risk_score'}]},
    {'rule_values': [{'rule_key': 'missing', 'value': 1}]},
    {'rule_values': [{'rule_key': 'enable_spring', 'value': 'false'}]},
    {'rule_values': [{'rule_key': 'lookback_core_days', 'value': 40.5}]},
    {'rule_values': [{'rule_key': 'lookback_core_days', 'value': 121}]},
    {'rule_values': [{'rule_key': 'sos_vol_ratio_min', 'value': float('inf')}]},
    {'rule_values': [{'rule_key': 'sos_vol_ratio_min', 'value': True}]},
    {'rule_values': [{'rule_key': 'sos_vol_ratio_min', 'value': 1.25, 'typo': 1}]},
    {'rule_values': [{'rule_key': 'sos_vol_ratio_min', 'value': 1.25},
                     {'rule_key': 'sos_vol_ratio_min', 'value': 1.5}]},
    {'profile_id': 'different'}, {'is_system': True}, {'updated_at': '2020-01-01T00:00:00Z'},
])
def test_invalid_overrides_fail_instead_of_silent_fallback(updates):
    raw = {'name': '测试模板', 'dimensions': [{'metric_key': 'risk_score'}], **updates}
    with pytest.raises(TradeError) as error:
        normalize_profile(raw, profile_id='custom_1', updated_at=STAMP)
    assert error.value.code == 'INVALID_EVENT_PROFILE'


def test_catalog_and_system_profile_results_do_not_mutate_cached_defaults():
    catalog = event_profile_catalog()
    catalog['profiles'][0]['rule_values'][0]['value'] = -1
    profile = get_system_profile()
    profile['rule_values'][0]['value'] = -2
    assert get_system_profile()['rule_values'][0]['value'] == 40
    with pytest.raises(TradeError) as error:
        get_system_profile('missing')
    assert error.value.code == 'EVENT_PROFILE_NOT_FOUND'


def test_digest_is_stable_across_input_order_and_ignores_display_metadata():
    raw = {'name': '模板', 'dimensions': [{'metric_key': 'risk_score'}],
           'rule_values': [{'rule_key': 'enable_spring', 'value': False},
                           {'rule_key': 'lookback_core_days', 'value': 60}]}
    first = normalize_profile(raw, profile_id='custom_1', updated_at=STAMP)
    raw['rule_values'].reverse()
    second = normalize_profile(raw, profile_id='custom_1', updated_at=STAMP)
    assert profile_sha256(first) == profile_sha256(second)
    changed = deepcopy(first)
    changed.update(name='新名称', description='备注', updated_at='2026-09-27T00:00:00Z')
    assert profile_sha256(changed) == profile_sha256(first)
    changed['rule_values'][0]['value'] = 80
    assert profile_sha256(changed) != profile_sha256(first)
