"""Read-only exports of one persisted screener stage; never rerun a strategy."""
import csv
import hashlib
import io
import json
from html import escape

from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, LongTable, TableStyle, PageBreak

from trade_app.api.excel_export import _cell, _sheet
from trade_app.platform.types import TradeError
from trade_app.research.screener_models import ScreenerRun
from trade_app.research.b1_models import B1Run
from trade_app.reviews.pdf_export import FONT_NAME, _register_font

VERSION = 'frozen-screening-stage-export-v1'
STAGES = {'input': '输入池', 'step1': '流动性', 'step2': '图形', 'step3': '量能与风险', 'step4': '最终观察池', 'hits': 'B1命中'}
FUNNEL_COLUMNS = {
    'symbol': '证券代码', 'name': '证券名称', 'dataset_id': '冻结行情SHA256', 'as_of_date': '指标日期',
    'score': '候选分数', 'ret40': '窗口涨幅（小数比例）', 'turnover20': '20日平均换手（小数比例）',
    'amount20': '20日平均成交额（元）', 'amplitude20': '20日平均振幅（小数比例）', 'retrace20': '20日回撤（小数比例）',
    'pullback_days': '回撤天数', 'ma10_above_ma20_days': 'MA10高于MA20天数', 'ma5_above_ma10_days': 'MA5高于MA10天数',
    'price_vs_ma20': '价格偏离MA20（小数比例）', 'vol_slope20': '20日量能斜率', 'up_down_volume_ratio': '涨跌量比（倍）',
    'pullback_volume_ratio': '回撤量比（倍）', 'has_blowoff_top': '放量滞涨', 'has_divergence_5d': '五日量价背离',
    'has_upper_shadow_risk': '长上影风险', 'ai_confidence': '候选置信度（公式代理，0至1）',
    'theme_stage': '题材阶段', 'trend_class': '趋势分类', 'degraded': '质量降级', 'quality_flags': '质量标记',
    'rejection_stage': '未通过阶段', 'rejection_reasons': '未通过原因',
}
B1_COLUMNS = {'symbol': '证券代码', 'name': '证券名称', 'dataset_id': '冻结行情SHA256',
    'close': '收盘价（元）', 'change_pct': '日涨跌幅（百分数）', 'amplitude_pct': '日振幅（百分数）',
    'volume_ratio': '当日量/20日均量（倍）', 'kdj_j': '当日KDJ J', 'weekly_macd': '周MACD柱', 'monthly_macd': '月MACD柱'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _text(value):
    if value is None: return ''
    if isinstance(value, bool): return 'true' if value else 'false'
    return _json(value) if isinstance(value, (dict, list)) else str(value)


def build_export(session, kind, run_id, body):
    model = ScreenerRun if kind == 'funnel' else B1Run
    run = session.get(model, run_id)
    if run is None: raise TradeError('SCREEN_EXPORT_RUN_NOT_FOUND', '冻结筛选记录不存在', 404)
    request, result = json.loads(run.request_json), json.loads(run.result_json)
    stage = body.get('stage') or ('step4' if kind == 'funnel' else 'hits')
    allowed_stages = set(STAGES) - {'hits'} if kind == 'funnel' else {'hits'}
    if stage not in allowed_stages: raise TradeError('SCREEN_EXPORT_STAGE', '该筛选类型不支持所选阶段')
    columns = FUNNEL_COLUMNS if kind == 'funnel' else B1_COLUMNS
    keys = body.get('columns')
    if keys is None: keys = list(columns)
    if not isinstance(keys, list) or not keys or any(type(key) is not str or key not in columns for key in keys) or len(keys) != len(set(keys)):
        raise TradeError('SCREEN_EXPORT_COLUMNS', '导出列无效、重复或为空')
    keys = ['symbol', 'name', *[key for key in keys if key not in ('symbol', 'name')]]
    pool = result['pools'][stage] if kind == 'funnel' else result['hits']
    ids = body.get('dataset_ids')
    if ids is not None:
        if not isinstance(ids, list) or not ids or any(type(item) is not str for item in ids) or len(ids) != len(set(ids)):
            raise TradeError('SCREEN_EXPORT_SELECTION', '证券选择不能为空或重复')
        if set(ids) - {row['dataset_id'] for row in pool}: raise TradeError('SCREEN_EXPORT_SELECTION', '所选证券不属于此冻结阶段', 409)
    selected_ids = set(ids) if ids is not None else None
    selected = [(index + 1, row) for index, row in enumerate(pool) if selected_ids is None or row['dataset_id'] in selected_ids]
    if len(selected) > 12000: raise TradeError('SCREEN_EXPORT_LIMIT', '单次导出最多12000只证券，不会截断')
    rows, sources = [], []
    source_map = {item['dataset_id']: item for item in request.get('datasets', [])}
    for ordinal, item in selected:
        rejection = result.get('rejections', {}).get(item['dataset_id'], {})
        row = {'stage_ordinal': ordinal, **item, 'rejection_stage': rejection.get('stage'), 'rejection_reasons': rejection.get('reasons', [])}
        rows.append(row)
        sources.append({'stage_ordinal': ordinal, 'symbol': item['symbol'], 'dataset_id': item['dataset_id'], **source_map.get(item['dataset_id'], {})})
    parameters = {key: value for key, value in request.items() if key in ('as_of_date', 'return_window_days', 'config', 'b1_params', 'source', 'markets', 'max_bars')}
    parameters.setdefault('config' if kind == 'funnel' else 'b1_params', result.get('config' if kind == 'funnel' else 'b1_params', {}))
    if kind == 'funnel': parameters.setdefault('return_window_days', result.get('return_window_days'))
    metadata = {'export_version': VERSION, 'kind': kind, 'run_id': run.id, 'created_at': run.created_at,
        'as_of_date': result['as_of_date'], 'stage': stage, 'stage_label': STAGES[stage], 'stage_count': len(pool),
        'selected_count': len(rows), 'selection_scope': 'entire_stage' if ids is None else 'explicit_subset',
        'ordering': 'frozen_stage_order', 'code_sha256': run.code_sha256,
        'request_sha256': _sha(run.request_json), 'result_sha256': _sha(run.result_json),
        'selection_sha256': _sha(_json({'stage': stage, 'dataset_ids': [row['dataset_id'] for row in rows], 'columns': keys})),
        'quality_flags': result.get('quality_flags', []), 'parameters': parameters,
        'units': '金额/价格=元；股本=股；漏斗比例0.1=10%；B1涨跌/振幅10=10%；原保存精度，不增加精度',
        'scope_note': '冻结记录的所选阶段；不是当前表单重算、全部输入行情或信号篮子。空值保持未知；候选置信度是公式代理。'}
    value = {'metadata': metadata, 'columns': {key: columns[key] for key in keys}, 'rows': rows, 'sources': sources}
    if len(_json(value).encode('utf-8')) > 24 * 1024 * 1024: raise TradeError('SCREEN_EXPORT_LIMIT', '所选导出内容超过24MiB，不会截断')
    return value


def csv_export(value):
    output = io.StringIO(newline=''); writer = csv.writer(output, lineterminator='\r\n')
    meta = value['metadata']; keys = list(value['columns'])
    # Metadata repeats with each row so a standalone CSV retains its frozen scope.
    metadata_keys = ['run_id', 'stage', 'as_of_date', 'code_sha256', 'request_sha256', 'result_sha256', 'selection_sha256', 'units', 'parameters', 'export_version', 'stage_count', 'selected_count', 'quality_flags']
    sources = {row['dataset_id']: row for row in value['sources']}
    writer.writerow([*metadata_keys, 'source_json', 'stage_ordinal', *[f'{key} | {value["columns"][key]}' for key in keys]])
    for row in value['rows'] or [None]:
        writer.writerow([*[_cell(_text(meta[key])) for key in metadata_keys],
                         '' if row is None else _cell(_text(sources[row['dataset_id']])),
                         *([''] * (1 + len(keys)) if row is None else [_text(row['stage_ordinal']), *[_cell(_text(row.get(key))) for key in keys]])])
    return ('\ufeff' + output.getvalue()).encode('utf-8')


def xlsx_export(value):
    workbook = Workbook(); workbook.remove(workbook.active)
    keys = list(value['columns'])
    # Text cells preserve JSON-decoded decimal spelling beyond Excel's 15 digits.
    _sheet(workbook, 'SelectedRows', ['stage_ordinal', *keys],
           [{key: _text(row.get(key)) for key in ['stage_ordinal', *keys]} for row in value['rows']])
    _sheet(workbook, 'ColumnDefinitions', ['field', 'label'], [{'field': key, 'label': label} for key, label in value['columns'].items()])
    meta = []
    for key, raw in value['metadata'].items():
        text = _text(raw)
        meta.extend({'field': key, 'part': part // 16000 + 1, 'value': text[part:part+16000]} for part in range(0, max(1, len(text)), 16000))
    _sheet(workbook, 'Metadata', ['field', 'part', 'value'], meta)
    _sheet(workbook, 'SelectedSources', ['stage_ordinal', 'symbol', 'dataset_id', 'float_shares', 'float_shares_as_of_date', 'float_shares_source_sha256'],
           [{key: _text(raw) for key, raw in row.items()} for row in value['sources']])
    output = io.BytesIO(); workbook.save(output); return output.getvalue()


def pdf_export(value):
    _register_font(); meta = value['metadata']; width, height = landscape(A4)
    body = ParagraphStyle('screen-body', fontName=FONT_NAME, fontSize=9, leading=13, wordWrap='CJK', textColor=colors.HexColor('#17352e'))
    small = ParagraphStyle('screen-small', parent=body, fontSize=7.5, leading=10)
    title = ParagraphStyle('screen-title', parent=body, fontSize=20, leading=28, spaceAfter=12)
    section = ParagraphStyle('screen-section', parent=body, fontSize=12, leading=18, spaceBefore=10, spaceAfter=8)
    p = lambda text, style=body: Paragraph(escape(_text(text)).replace('\n', '<br/>'), style)
    story = [p(f'{"四步漏斗" if meta["kind"] == "funnel" else "B1多周期"} - {meta["stage_label"]}', title),
        p(f'截至 {meta["as_of_date"]} · 所选 {meta["selected_count"]} / 阶段 {meta["stage_count"]} · 按冻结阶段顺序'),
        p(meta['units']), p(meta['scope_note']), Spacer(1, 8)]
    for key in ('run_id', 'created_at', 'code_sha256', 'request_sha256', 'result_sha256', 'selection_sha256', 'quality_flags'):
        story.append(p(f'{key}: {_text(meta[key])}', small))
    keys = list(value['columns']); metric_keys = [key for key in keys if key not in ('symbol', 'name')]
    groups = [metric_keys[offset:offset+5] for offset in range(0, len(metric_keys), 5)] or [[]]
    for index, group in enumerate(groups):
        if index: story.append(PageBreak())
        story.append(p(f'所选证券明细 {index+1}/{len(groups)}', section))
        columns = ['stage_ordinal', 'symbol', 'name', *group]
        labels = {'stage_ordinal': '阶段序号', **value['columns']}
        table_rows = [[p(labels[key], small) for key in columns]]
        table_rows.extend([p(_text(row.get(key)) or '—', small) for key in columns] for row in value['rows'])
        if not value['rows']: story.append(p('此冻结阶段为空，没有证券明细。'))
        else:
            fixed = [42, 70, 90]; rest = (width-72-sum(fixed))/max(1,len(group))
            widths = [*fixed, *([rest]*len(group))]
            table = LongTable(table_rows, colWidths=widths, repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e2eee8')),
                ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f4f7f5')]),
                ('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#cbd9d3')),
                ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
            story.append(table)
    story.extend([PageBreak(), p('冻结参数与证券来源', section)])
    for key, raw in meta['parameters'].items():
        story.append(p(f'{key}: {_text(raw)}', small))
    story.append(Spacer(1, 12))
    for row in value['sources']:
        story.append(p(_json(row), small))
    output = io.BytesIO()
    def footer(canvas, doc):
        canvas.saveState(); canvas.setFont(FONT_NAME,8); canvas.setFillColor(colors.HexColor('#61746d'))
        canvas.drawString(36,22,f'Trade · 冻结筛选导出 · {meta["stage_label"]} · {meta["as_of_date"]}')
        canvas.drawRightString(width-36,22,str(doc.page)); canvas.restoreState()
    SimpleDocTemplate(output,pagesize=(width,height),leftMargin=36,rightMargin=36,topMargin=32,bottomMargin=38,
        title=f'Trade {meta["stage_label"]} {meta["as_of_date"]}',author='Trade').build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()


def render_export(value, format):
    data = {'csv': csv_export, 'xlsx': xlsx_export, 'pdf': pdf_export}[format](value)
    if len(data) > 32 * 1024 * 1024: raise TradeError('SCREEN_EXPORT_LIMIT', '导出文件超过32MiB，请缩小显式选择范围')
    return data
