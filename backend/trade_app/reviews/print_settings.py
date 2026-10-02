"""Versioned print identity and a local export destination preference."""
import json
from pathlib import Path

from trade_app.platform.settings_models import ApplicationSetting
from trade_app.platform.types import TradeError

DEFAULTS = {'author': '', 'export_directory': ''}


def normalize(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise TradeError('INVALID_PRINT_SETTINGS', '打印设置需要署名与导出目录')
    result = {}
    for key, maximum in (('author', 80), ('export_directory', 500)):
        text = value[key]
        if not isinstance(text, str) or len(text) > maximum or any(ord(char) < 32 for char in text):
            raise TradeError('INVALID_PRINT_SETTINGS', '署名或导出目录包含无效字符或过长')
        result[key] = text.strip()
    if result['export_directory'] and not Path(result['export_directory']).expanduser().is_absolute():
        raise TradeError('INVALID_PRINT_SETTINGS', '导出目录须为当前运行机器的绝对路径')
    return result


def get_settings(session):
    row = session.get(ApplicationSetting, 'print')
    return {'revision': row.revision if row else 0,
            **(json.loads(row.value_json) if row else DEFAULTS)}
