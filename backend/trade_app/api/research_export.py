"""Version-labelled basket exports. Observed returns are fractions, not percentages."""
import csv
import io

from openpyxl import Workbook
from sqlalchemy.orm import Session

from trade_app.api.excel_export import _cell, _sheet
from trade_app.research.basket_service import get_basket, get_evaluation, list_evaluations


MEMBER_COLUMNS = ['symbol', 'run_ids', 'source_date', 'decision_date', 'anchor_date',
                  'dataset_id', 'current_date', 'current_price', 'quality_flags']
RETURN_COLUMNS = ['symbol', 'entry_mode', 'status', 'entry_date', 'entry_price', 'target_date',
                  'exit_price', 'quantity', 'raw_return', 'after_cost_return',
                  'mark_to_market_return', 'buy_fees', 'sell_fees']


def _save(workbook):
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _returns(evaluation):
    return [{'symbol': row['symbol'], 'entry_mode': mode, **row[mode]}
            for row in evaluation['result']['constituents'] for mode in ('t1', 't2')]


def _current_report(session, basket):
    row = next((item for item in list_evaluations(session, basket['id'])
                if item['basket_revision'] == basket['revision']), None)
    return get_evaluation(session, basket['id'], row['id']) if row else None


def basket_csv(session: Session, basket_id: str) -> bytes:
    basket = get_basket(session, basket_id, include_deleted=True)
    report = _current_report(session, basket)
    rows = _returns(report) if report else [{'symbol': item['symbol'], 'entry_mode': mode,
                                           'status': 'not_evaluated'}
                                          for item in basket['constituents'] for mode in ('t1', 't2')]
    columns = ['basket_id', 'basket_name', 'basket_revision', 'evaluation_id', 'as_of_date',
               'return_unit', *RETURN_COLUMNS]
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\r\n')
    writer.writerow(columns)
    for row in rows:
        row.update(basket_id=basket['id'], basket_name=basket['name'], basket_revision=basket['revision'],
                   evaluation_id=report['id'] if report else None, as_of_date=report['as_of_date'] if report else None,
                   return_unit='fraction: 0.1 = 10%')
        writer.writerow([_cell(row.get(key)) for key in columns])
    return ('\ufeff' + output.getvalue()).encode('utf-8')


def basket_xlsx(session: Session, basket_id: str) -> bytes:
    basket = get_basket(session, basket_id, include_deleted=True)
    report = _current_report(session, basket)
    workbook = Workbook()
    workbook.remove(workbook.active)
    meta = {key: value for key, value in basket.items() if key != 'constituents'}
    meta.update(return_unit='fraction: 0.1 = 10%', evaluation_id=report['id'] if report else None,
                evaluation_status='saved_observation' if report else 'not_evaluated',
                as_of_date=report['as_of_date'] if report else None)
    _sheet(workbook, 'Basket', ['field', 'value'], [{'field': key, 'value': value} for key, value in meta.items()])
    _sheet(workbook, 'Constituents', MEMBER_COLUMNS, basket['constituents'])
    if report:
        _report_sheets(workbook, report)
    return _save(workbook)


def _report_sheets(workbook, report):
    summary = report['result']['summary']
    columns = ['entry_mode', *summary['t1']]
    _sheet(workbook, 'Summary', columns, [{'entry_mode': mode, **summary[mode]} for mode in ('t1', 't2')])
    _sheet(workbook, 'Returns', RETURN_COLUMNS, _returns(report))
    _sheet(workbook, 'ObservationCurve', ['date', 't1', 't2'], report['result']['curve'])
    _sheet(workbook, 'Notes', ['note'], [{'note': note} for note in report['result']['notes']])


def basket_evaluation_xlsx(session: Session, basket_id: str, evaluation_id: str) -> bytes:
    report = get_evaluation(session, basket_id, evaluation_id)
    workbook = Workbook()
    workbook.remove(workbook.active)
    meta = {key: report[key] for key in ('id', 'basket_id', 'basket_revision', 'as_of_date', 'created_at', 'code_sha256')}
    meta.update(return_unit='fraction: 0.1 = 10%', strict=report['request']['strict'],
                config=report['request']['basket_snapshot']['config'])
    _sheet(workbook, 'Meta', ['field', 'value'], [{'field': key, 'value': value} for key, value in meta.items()])
    _sheet(workbook, 'Sources', ['symbol', 'dataset_id', 'content_sha256', 'original_dataset_id', 'provider', 'adjustment'], report['request']['datasets'])
    _sheet(workbook, 'Constituents', MEMBER_COLUMNS, report['result']['constituents'])
    _report_sheets(workbook, report)
    return _save(workbook)
