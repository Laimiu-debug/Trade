"""Offline account statistics PDFs, with source versions and complete table rows."""
import io
from html import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, Spacer, LongTable, TableStyle, KeepTogether

from trade_app.platform.types import TradeError, utc_now
from trade_app.reviews.pdf_export import FONT_NAME, ReviewDocument, _register_font, _styles
from trade_app.reviews.statistics import statistics_source
from trade_app.reviews.pdf_charts import chart


class StatisticsDocument(ReviewDocument):
    def _page(self, canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#c9d9d2'))
        canvas.line(54, 42, A4[0] - 54, 42)
        canvas.setFont(FONT_NAME, 8)
        canvas.setFillColor(colors.HexColor('#61746d'))
        canvas.drawString(54, 28, 'Trade / 账户统计')
        canvas.drawRightString(A4[0] - 54, 28, str(doc.page))
        canvas.restoreState()


def render_statistics(value, *, name, account_id, account_kind, input_revision, generated_at=None, author=''):
    _register_font()
    styles = _styles()
    styles['cell'] = ParagraphStyle('statistics-cell', fontName=FONT_NAME, fontSize=8.5, leading=13,
        textColor=colors.HexColor('#17352e'), wordWrap='CJK')
    styles['head'] = ParagraphStyle('statistics-head', parent=styles['cell'], textColor=colors.HexColor('#0a6b54'))
    title = '模拟账户成交统计' if account_kind == 'sim' else '实盘账户周期统计'
    buffer = io.BytesIO()
    document = StatisticsDocument(buffer, title)
    document.subject = '账户统计与来源追溯'
    document.author = author or 'Trade'
    p = lambda value, style='body': Paragraph(escape(str(value)).replace('\n', '<br/>'), styles[style])
    text = lambda value, suffix='': '未知 / 不适用' if value is None else str(value) + suffix
    story = [p(title, 'title'), p(name, 'subtitle'), p(f'账户 {account_id} / 输入修订 {input_revision}', 'subtitle'),
             p('生成时间 ' + (generated_at or utc_now()), 'subtitle'), p(value['method']), Spacer(1, 10)]
    if author:
        story.insert(2, p('署名：' + author, 'subtitle'))

    def table(headers, rows, proportions):
        if not rows:
            story.append(p('当前没有符合条件的记录。'))
            return
        if len(rows) > 5000:
            raise TradeError('STATISTICS_PDF_LIMIT', '统计表超过5000行，请改用完整CSV/Excel导出')
        contents = [[p(item, 'head') for item in headers]] + [[p(item, 'cell') for item in row] for row in rows]
        widths = [document.width * size / sum(proportions) for size in proportions]
        grid = LongTable(contents, colWidths=widths, repeatRows=1, hAlign='LEFT', splitByRow=1)
        grid.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e7f1ed')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, 0), .7, colors.HexColor('#b8cbc2')),
            ('LINEBELOW', (0, 1), (-1, -1), .3, colors.HexColor('#dce5e0')),
            ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]))
        story.extend([grid, Spacer(1, 12)])

    if account_kind == 'sim':
        basis = '买入日（事后归属）' if value['date_basis'] == 'buy' else '卖出成交日'
        story.extend([p('成交摘要', 'section'), p(f'模拟时钟 {value["as_of_date"]} / 月份归属 {basis}'),
            p(f'模拟钱包修订 {value.get("wallet_revision", "未提供")}'),
            p(f'买入 {value["buy_fill_count"]} 笔 / 卖出 {value["sell_fill_count"]} 笔 / 已实现盈亏 ¥ {value["realized_pnl"]}'),
            p(f'卖出胜率 {text(value["win_rate_pct"], "%")} / Profit Factor {text(value["profit_factor"])} / 最长连胜 {value["max_consecutive_wins"]} / 最长连亏 {value["max_consecutive_losses"]}')])
        story.append(p(f'成交筛选 {value.get("date_from") or "不限起点"} 至 {value.get("date_to") or "当前模拟时钟"} / {basis}'))
        for title, data, date_key, value_key, bars in [
            ('累计已实现盈亏 / 元 · 卖出日期轴', value.get('realized_curve', []), 'date', 'cumulative_realized_pnl', False),
            ('月度已实现盈亏 / 元 · 所选归属', sorted(value['monthly'], key=lambda row: row['month']), 'month', 'realized_pnl', True)]:
            picture = chart(data, date_key=date_key, value_key=value_key, width=document.width, bars=bars)
            if picture: story.append(KeepTogether([p(title, 'section'), picture]))
        missing = value['buy_attribution_unavailable_fill_ids']
        if missing and value['date_basis'] == 'buy':
            scope = '筛选范围也不猜测其盈亏。' if value.get('date_from') or value.get('date_to') else '总盈亏保留全部卖出。'
            story.append(p(f'有 {len(missing)} 笔旧卖出缺少精确买入批次分摊，未纳入买入月汇总；' + scope))
        story.append(p('月份汇总', 'section'))
        table(['月份', '卖出笔数', '已实现盈亏 / 元'], [[row['month'], row['sell_count'], row['realized_pnl']] for row in value['monthly']], [1, 1, 2])
        story.append(p('卖出成交明细', 'section'))
        table(['成交日 / 代码', '数量', '卖出金额 / 元', '费用 / 元', '已实现盈亏 / 元'],
            [[f'{row["sell_date"]}\n{row["symbol"]}', row['quantity'], row['sell_gross'], row['fees'], row['realized_pnl']] for row in value['closed_fills']], [1.5, .65, 1.2, 1, 1.2])
        story.append(p('FIFO 来源与核对编号', 'section'))
        for row in value['closed_fills']:
            source_rows = [p(f'{row["sell_date"]} / {row["symbol"]} / 卖出成交 {row["fill_id"]}')]
            for source in row['buy_allocations']:
                source_rows.append(p(f'买入 {source["buy_date"]} / {source["quantity"]} 股 / 成交 {source.get("buy_fill_id") or "旧记录未关联"} / 成本 {text(source.get("cost_basis"))} / 卖出费用 {text(source.get("sell_fees"))} / 已实现 {text(source.get("realized_pnl"))}'))
            if row['quality'] != 'complete' or row['allocation_quality'] != 'persisted':
                source_rows.append(p('该笔旧成交存在批次归属缺口，请在应用中核对。'))
            story.append(KeepTogether(source_rows))
    else:
        kind = {'daily': '每日', 'weekly': '每周', 'monthly': '每月'}[value['kind']]
        story.extend([p(f'{kind}汇总', 'section'),
            p(f'投影状态 {value["projection_status"]} / 版本 {value["projection_version"] or "尚未生成"} / 算法 {value["calculation_version"] or "尚未生成"}')])
        if value['projection_status'] != 'fresh':
            story.append(p('统计尚未与最新输入完全同步，以下内容仅代表列出的投影版本。请等待账户重算后重新导出。'))
        rows = value['items']
        picture = chart(sorted(rows, key=lambda row: row['key']), date_key='key', value_key='return_pct', width=document.width, bars=True)
        if picture: story.append(KeepTogether([p(f'{kind}收益率 / % · 缺失不补零', 'section'), picture]))
        table(['周期', '收益率', '最大回撤', '已结束回合', '回合盈亏 / 元', '快照范围 / 质量'],
            [[row['key'], text(row['return_pct'], '%'), text(row['min_drawdown_pct'], '%'), row['rounds']['closed_rounds'],
              row['rounds']['closed_pnl'], f'{row["baseline_date"] or "缺基线"} → {row["last_confirmed_date"] or "缺快照"}\n确认 {row["confirmed_points"]} 个 / {row["return_quality"]}'] for row in rows],
            [1, 1, 1, .8, 1.15, 2.1])
        story.append(p('周期口径与回合来源', 'section'))
        for row in rows:
            source_rows = [p(f'{row["key"]} / {row["start_date"]} 至 {row["end_date"]} / 胜率 {text(row["rounds"]["win_rate_pct"], "%")} / 盈亏比 {text(row["rounds"]["payoff_ratio"])} / Profit Factor {text(row["rounds"]["profit_factor"])}')]
            if row['rounds']['round_ids']:
                source_rows.append(p('回合编号：' + ' / '.join(row['rounds']['round_ids'])))
            if row['node_achievements']:
                source_rows.append(p('首次节点：' + ' / '.join(f'第{item["level"]}级 {item["first_lit_date"]}' for item in row['node_achievements'])))
            story.append(KeepTogether(source_rows))
    document.build(story)
    return buffer.getvalue()


def export_performance_pdf(session, account_id, **options):
    result, meta = statistics_source(session, account_id, **options)
    return render_statistics(result, **meta)
