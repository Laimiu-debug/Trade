"""Offline Chinese PDF export for saved review records."""
from __future__ import annotations

import io
import threading
from html import escape
from pathlib import Path

from PIL import Image as PillowImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, PageBreak,
                               PageTemplate, Paragraph, Spacer)
from reportlab.platypus.tableofcontents import TableOfContents
from sqlalchemy.orm import Session

from trade_app.platform.types import TradeError
from trade_app.platform.runtime import resource_root
from trade_app.reviews.attachments import get_attachment, list_attachments
from trade_app.reviews.markdown_export import export_markdown
from trade_app.reviews.print_settings import get_settings


FONT_NAME = 'TradeLXGW'
_font_lock = threading.Lock()


def _register_font() -> None:
    with _font_lock:
        if FONT_NAME in pdfmetrics.getRegisteredFontNames():
            return
        root = resource_root()
        font = root / 'frontend' / 'public' / 'fonts' / 'LXGWWenKai-Regular.ttf'
        if not font.is_file():
            font = root / 'frontend' / 'dist-rebuild' / 'fonts' / 'LXGWWenKai-Regular.ttf'
        if not font.is_file():
            raise TradeError('PDF_FONT_MISSING', '中文 PDF 字体文件缺失', 503)
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(font)))


class ReviewDocument(BaseDocTemplate):
    def __init__(self, buffer: io.BytesIO, title: str):
        super().__init__(buffer, pagesize=A4, leftMargin=54, rightMargin=54,
                         topMargin=60, bottomMargin=58, title=title,
                         author='Trade', subject='复盘记录')
        frame = Frame(self.leftMargin, self.bottomMargin,
                      self.width, self.height, leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0)
        self.addPageTemplates(PageTemplate(id='Review', frames=[frame], onPage=self._page))
        self._section_count = 0

    def beforeDocument(self) -> None:
        self._section_count = 0

    def _page(self, canvas, doc) -> None:
        canvas.saveState()
        width, _height = A4
        canvas.setStrokeColor(colors.HexColor('#c9d9d2'))
        canvas.line(54, 42, width - 54, 42)
        canvas.setFont(FONT_NAME, 8)
        canvas.setFillColor(colors.HexColor('#61746d'))
        canvas.drawString(54, 28, 'Trade · 复盘导出')
        canvas.drawRightString(width - 54, 28, f'{doc.page}')
        canvas.restoreState()

    def afterFlowable(self, flowable) -> None:
        if isinstance(flowable, Paragraph) and flowable.style.name == 'review-section':
            self._section_count += 1
            key = f'section-{self._section_count}'
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.getPlainText(), key, level=0)
            self.notify('TOCEntry', (0, flowable.getPlainText(), self.page, key))


def _styles() -> dict[str, ParagraphStyle]:
    color = colors.HexColor('#17352e')
    return {
        'title': ParagraphStyle('review-title', fontName=FONT_NAME, fontSize=24,
                                leading=36, textColor=color, alignment=TA_CENTER,
                                spaceAfter=20, wordWrap='CJK'),
        'subtitle': ParagraphStyle('review-subtitle', fontName=FONT_NAME, fontSize=11,
                                   leading=19, textColor=colors.HexColor('#61746d'),
                                   alignment=TA_CENTER, spaceAfter=8, wordWrap='CJK'),
        'section': ParagraphStyle('review-section', fontName=FONT_NAME, fontSize=15,
                                  leading=24, textColor=colors.HexColor('#0a6b54'),
                                  spaceBefore=19, spaceAfter=9, keepWithNext=True,
                                  wordWrap='CJK'),
        'body': ParagraphStyle('review-body', fontName=FONT_NAME, fontSize=10.5,
                               leading=18, textColor=color, spaceAfter=8,
                               allowWidows=0, allowOrphans=0, wordWrap='CJK'),
        'bullet': ParagraphStyle('review-bullet', fontName=FONT_NAME, fontSize=10,
                                 leading=17, textColor=color, leftIndent=18,
                                 firstLineIndent=-12, spaceAfter=5, wordWrap='CJK'),
        'toc': ParagraphStyle('review-toc', fontName=FONT_NAME, fontSize=11,
                              leading=22, textColor=color, leftIndent=8,
                              rightIndent=12, wordWrap='CJK'),
    }


def render_review_pdf(session: Session, data_dir: Path, account_id: str, kind: str, key: str) -> bytes:
    markdown = export_markdown(session, account_id, kind, key)
    attachments = list_attachments(session, account_id, key) if kind == 'daily' else []
    if attachments:
        markdown = markdown.replace('图片文件未包含在此 Markdown，请在 Trade 应用中打开原复盘查看。',
                                    '原始截图已附于本 PDF 末尾。')
    _register_font()
    lines = markdown.splitlines()
    title = lines[0].removeprefix('# ').strip()
    styles = _styles()
    buffer = io.BytesIO()
    document = ReviewDocument(buffer, title)
    document.author = get_settings(session)['author'] or 'Trade'
    story = [Spacer(1, 85), Paragraph(escape(title), styles['title'])]
    meta = []
    index = 1
    while index < len(lines) and not lines[index].startswith('## '):
        value = lines[index].strip()
        if value.startswith('- '):
            meta.append(value[2:])
        index += 1
    for value in meta:
        story.append(Paragraph(escape(value), styles['subtitle']))
    story.extend([PageBreak(), Paragraph('目录', styles['title'])])
    toc = TableOfContents()
    toc.levelStyles = [styles['toc']]
    story.extend([toc, PageBreak()])
    block: list[str] = []

    def flush() -> None:
        if block:
            value = '<br/>'.join(escape(item) for item in block)
            story.append(Paragraph(value, styles['body']))
            block.clear()

    for line in lines[index:]:
        value = line.strip()
        if value.startswith('## '):
            flush()
            story.append(Paragraph(escape(value[3:]), styles['section']))
        elif value.startswith('- '):
            flush()
            story.append(Paragraph('• ' + escape(value[2:]), styles['bullet']))
        elif not value:
            flush()
        else:
            block.append(value)
    flush()
    for item in attachments:
        metadata, content = get_attachment(session, data_dir, account_id, item['id'])
        with PillowImage.open(io.BytesIO(content)) as original:
            original.seek(0)
            picture = original.convert('RGB')
            picture.thumbnail((1600, 1600))
            image_bytes = io.BytesIO()
            picture.save(image_bytes, format='JPEG', quality=88, optimize=True)
        image_bytes.seek(0)
        width, height = picture.size
        scale = min(document.width / width, (document.height - 110) / height, 1)
        story.extend([PageBreak(), Paragraph('复盘截图', styles['section']),
                      Paragraph(escape(metadata['original_name']), styles['body']),
                      Image(image_bytes, width=width * scale, height=height * scale,
                            hAlign='CENTER')])
    try:
        document.multiBuild(story)
    except Exception as exc:
        raise TradeError('PDF_BUILD_FAILED', '复盘 PDF 生成失败，请检查内容或字体', 500) from exc
    return buffer.getvalue()
