"""Render an explicitly selected frozen simulation valuation, without recomputation."""
import io
from html import escape

from reportlab.lib import colors
from reportlab.platypus import KeepTogether, LongTable, Paragraph, Spacer, TableStyle

from trade_app.reviews.pdf_export import _register_font, _styles
from trade_app.reviews.performance_pdf import StatisticsDocument
from trade_app.reviews.pdf_charts import chart


def render_equity(report, *, author=''):
    _register_font()
    value, request = report['result'], report['request']
    styles = _styles()
    p = lambda item, style='body': Paragraph(escape(str(item)).replace('\n', '<br/>'), styles[style])
    text = lambda item: '缺失' if item is None else str(item)
    output = io.BytesIO()
    doc = StatisticsDocument(output, '模拟账户冻结权益报告')
    doc.author = author or 'Trade'
    story = [p('模拟账户冻结权益报告', 'title'), p(f'账户 {report["account_id"]}', 'subtitle')]
    if author: story.append(p('署名：' + author, 'subtitle'))
    story.extend([p(f'{value["date_from"]} 至 {value["date_to"]} / 钱包修订 {report["wallet_revision"]}', 'subtitle'),
        p('报告 SHA-256：' + report['id']), p('输入 SHA-256：' + report['input_sha256']), p(value['method']),
        p('此文件读取所选历史报告；账户后续成交不会更新本报告。缺失点断线，旧价格与可得时间未知仍按明细标注。'), Spacer(1, 8)])
    summary = value['summary']
    story.extend([p('范围摘要', 'section'), p(f'期末资产 {text(summary["ending_assets"])} 元 / 期末现金 {summary["ending_cash"]} 元'),
        p(f'范围收益 {text(summary["range_return_pct"])} % / 完整最大回撤 {text(summary["max_drawdown_pct"])} % / 已观察点最大回撤 {summary["observed_max_drawdown_pct"]} %'),
        p(f'收益分母：{text(summary["range_denominator_assets"])} 元，日期 {summary["range_denominator_date"]}；缺失估值 {summary["missing_observation_count"]} 个，旧价格估值 {summary["stale_observation_count"]} 个。')])
    for title, rows, date_key, field, bars in [
        ('权益曲线 / 元', value['points'], 'date', 'total_assets', False),
        ('从已观察峰值回撤 / %', value['points'], 'date', 'drawdown_pct', False),
        ('月份收益 / % · 区间端点可能为非完整月份', value['monthly'], 'month', 'return_pct', True)]:
        picture = chart(rows, date_key=date_key, value_key=field, width=doc.width, bars=bars)
        story.append(KeepTogether([p(title, 'section'), picture or p('缺少可绘制数据，保留缺失。')]))

    def table(headers, rows, widths):
        contents = [[p(item) for item in headers]] + [[p(item) for item in row] for row in rows]
        grid = LongTable(contents, colWidths=[doc.width * item / sum(widths) for item in widths], repeatRows=1)
        grid.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e7f1ed')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5), ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6), ('LINEBELOW', (0, 0), (-1, -1), .3, colors.HexColor('#c9d9d2'))]))
        story.extend([grid, Spacer(1, 10)])
    story.append(p('月份明细', 'section'))
    table(['月份 / 范围', '资产分母 / 元', '期末资产 / 元', '收益率 / %', '质量'],
        [[f'{row["month"]}\n{row["start_date"]}→{row["end_date"]}', text(row['denominator_assets']), text(row['ending_assets']), text(row['return_pct']),
          row['quality'] + (' / 非完整月' if row['is_partial_month'] else '')] for row in value['monthly']], [1.5, 1, 1, .8, 1.1])
    story.append(p('逐日估值明细', 'section'))
    table(['日期', '现金 / 元', '总资产 / 元', '回撤 / %', '质量'],
        [[row['date'], row['cash'], text(row['total_assets']), text(row['drawdown_pct']), row['quality'] + ' / ' + row['drawdown_quality']] for row in value['points']], [1, 1.1, 1.1, .8, 1.5])
    story.append(p('冻结行情来源', 'section'))
    for source in request['datasets']:
        story.append(p(f'{source["symbol"]} / {source["provider"]} / {source["adjustment"]} / {source["first_date"]}至{source["last_date"]} / {source["availability_quality"]}\n样本：{source["id"]}'))
    doc.build(story)
    return output.getvalue()
