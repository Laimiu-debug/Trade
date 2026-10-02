"""Isolated BaoStock socket client; parent process enforces a wall-clock timeout."""
from __future__ import annotations

import json
import sys
from pathlib import Path


FIELDS = 'date,code,open,high,low,close,volume,amount,tradestatus'


def main() -> int:
    if len(sys.argv) != 5:
        return 2
    symbol, start, end, output = sys.argv[1:]
    if not (symbol.startswith(('sh.', 'sz.')) and len(symbol) == 9):
        return 2
    import baostock as bs
    logged_in = False
    try:
        login = bs.login()
        if login.error_code != '0':
            return 3
        logged_in = True
        response = bs.query_history_k_data_plus(
            symbol, FIELDS, start_date=start, end_date=end,
            frequency='d', adjustflag='3')
        if response.error_code != '0':
            return 4
        rows = []
        while response.next():
            values = response.get_row_data()
            if len(values) != len(response.fields):
                return 5
            rows.append(dict(zip(response.fields, values)))
            if len(rows) > 5000:
                return 6
        Path(output).write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
        return 0
    finally:
        if logged_in:
            bs.logout()


if __name__ == '__main__':
    raise SystemExit(main())
