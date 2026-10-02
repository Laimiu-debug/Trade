"""Small offline vector charts; missing observations create actual gaps."""
from datetime import date
from decimal import Decimal
import math

from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String, Circle
from reportlab.lib import colors

from trade_app.reviews.pdf_export import FONT_NAME, _register_font

INK = colors.HexColor('#17352e')
ACCENT = colors.HexColor('#0a6b54')
NEGATIVE = colors.HexColor('#a43138')
GRID = colors.HexColor('#c9d9d2')


def chart(rows, *, date_key, value_key, width=470, height=185, bars=False, date_axis=True):
    """Return None when every observation is missing; do not interpolate gaps."""
    _register_font()
    values = [None if row.get(value_key) is None else float(Decimal(str(row[value_key]))) for row in rows]
    known = [value for value in values if value is not None and math.isfinite(value)]
    if not known: return None
    values = [value if value is not None and math.isfinite(value) else None for value in values]
    left, right, bottom, top = 66, width - 8, 32, height - 12
    low, high = min(known + ([0] if bars else [])), max(known + ([0] if bars else []))
    pad = (high - low) * .05 or max(abs(high) * .01, 1)
    low, high = low - pad, high + pad
    y = lambda value: bottom + (value - low) / (high - low) * (top - bottom)
    if date_axis and not bars:
        days = [date.fromisoformat(row[date_key]).toordinal() for row in rows]
        first, span = min(days), max(days) - min(days)
        xs = [left + (day - first) / (span or 1) * (right - left) for day in days]
    else:
        xs = [left + (index + .5) / len(rows) * (right - left) for index in range(len(rows))]
    drawing = Drawing(width, height)
    for value in (low, (low + high) / 2, high):
        position = y(value)
        drawing.add(Line(left, position, right, position, strokeColor=GRID, strokeWidth=.35))
        label = f'{value:,.2f}' if abs(value) < 1e7 else f'{value:.3g}'
        drawing.add(String(left - 5, position - 3, label, textAnchor='end', fontName=FONT_NAME, fontSize=8, fillColor=INK))
    if bars:
        drawing.add(Line(left, y(0), right, y(0), strokeColor=INK, strokeWidth=.6))
        bar_width = min(26, (right - left) / len(rows) * .7)
        for x, value in zip(xs, values):
            if value is not None:
                drawing.add(Rect(x - bar_width / 2, min(y(0), y(value)), bar_width, abs(y(value) - y(0)),
                    fillColor=ACCENT if value >= 0 else NEGATIVE, strokeColor=None))
    else:
        segment = []
        def flush():
            if len(segment) >= 4: drawing.add(PolyLine(segment[:], strokeColor=ACCENT, strokeWidth=1.5))
            elif segment: drawing.add(Circle(segment[0], segment[1], 2, fillColor=ACCENT, strokeColor=None))
            segment.clear()
        for x, value in zip(xs, values):
            if value is None: flush()
            else: segment.extend([x, y(value)])
        flush()
    indices = sorted({0, len(rows) // 2, len(rows) - 1})
    for index in indices:
        anchor = 'start' if index == 0 else 'end' if index == len(rows) - 1 else 'middle'
        drawing.add(String(xs[index], 12, str(rows[index][date_key]), textAnchor=anchor, fontName=FONT_NAME, fontSize=8, fillColor=INK))
    return drawing
