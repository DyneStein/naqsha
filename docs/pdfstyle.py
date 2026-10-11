"""Shared look for Naqsha documents (reportlab)."""
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, Paragraph, Spacer, Table, TableStyle)

F = "/System/Library/Fonts/Supplemental/"
pdfmetrics.registerFont(TTFont("Arial", F + "Arial.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Bold", F + "Arial Bold.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Italic", F + "Arial Italic.ttf"))
pdfmetrics.registerFont(TTFont("Arial-BoldItalic", F + "Arial Bold Italic.ttf"))
pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic",
                              boldItalic="Arial-BoldItalic")
pdfmetrics.registerFont(TTFont("Mono", "/System/Library/Fonts/Supplemental/Courier New.ttf"))

INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#475569")
ACCENT = colors.HexColor("#0369a1")
LIGHT = colors.HexColor("#e2e8f0")
PALE = colors.HexColor("#f1f5f9")
GOOD = colors.HexColor("#047857")

PAGE = A4
MARGIN = 16 * mm
WIDTH = PAGE[0] - 2 * MARGIN


def styles(base=9.6):
    s = {}
    s["body"] = ParagraphStyle("body", fontName="Arial", fontSize=base, leading=base * 1.38, textColor=INK,
                               spaceAfter=base * 0.45, alignment=TA_LEFT)
    s["small"] = ParagraphStyle("small", parent=s["body"], fontSize=base - 1.4, leading=(base - 1.4) * 1.35,
                                textColor=MUTED)
    s["cell"] = ParagraphStyle("cell", parent=s["body"], fontSize=base - 1.2, leading=(base - 1.2) * 1.3,
                               spaceAfter=0)
    s["cellb"] = ParagraphStyle("cellb", parent=s["cell"], fontName="Arial-Bold")
    s["title"] = ParagraphStyle("title", fontName="Arial-Bold", fontSize=24, leading=28, textColor=INK,
                                spaceAfter=4)
    s["subtitle"] = ParagraphStyle("subtitle", fontName="Arial", fontSize=11.5, leading=15, textColor=MUTED,
                                   spaceAfter=10)
    s["h1"] = ParagraphStyle("h1", fontName="Arial-Bold", fontSize=13, leading=16, textColor=ACCENT,
                             spaceBefore=9, spaceAfter=4, keepWithNext=1)
    s["h2"] = ParagraphStyle("h2", fontName="Arial-Bold", fontSize=10.4, leading=13, textColor=INK,
                             spaceBefore=6, spaceAfter=2, keepWithNext=1)
    s["bullet"] = ParagraphStyle("bullet", parent=s["body"], leftIndent=11, bulletIndent=2, spaceAfter=2)
    s["code"] = ParagraphStyle("code", fontName="Mono", fontSize=8.2, leading=10.5, textColor=INK,
                               backColor=PALE, borderPadding=(4, 5, 4, 5), spaceBefore=3, spaceAfter=7)
    s["caption"] = ParagraphStyle("caption", parent=s["small"], spaceBefore=2, spaceAfter=8)
    return s


def table(rows, widths, st, header=True, zebra=True, cell="cell"):
    data = []
    for r_i, row in enumerate(rows):
        data.append([c if not isinstance(c, str) else
                     Paragraph(c, st["cellb"] if (header and r_i == 0) else st[cell]) for c in row])
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    cmds = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5), ("LINEBELOW", (0, 0), (-1, -1), 0.25, LIGHT)]
    if header:
        cmds += [("BACKGROUND", (0, 0), (-1, 0), PALE), ("LINEBELOW", (0, 0), (-1, 0), 0.8, ACCENT)]
    if zebra:
        for i in range(2 if header else 1, len(data), 2):
            cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafbfc")))
    t.setStyle(TableStyle(cmds))
    return t


def image(path, width, max_height=None, border=True):
    from PIL import Image as PImage
    with PImage.open(path) as im:
        w, h = im.size
    height = width * h / w
    if max_height and height > max_height:
        height = max_height
        width = height * w / h
    img = Image(path, width=width, height=height)
    if not border:
        return img
    t = Table([[img]], colWidths=[width + 2])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LIGHT), ("LEFTPADDING", (0, 0), (-1, -1), 1),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return t


def footer(title):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(LIGHT)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, 11 * mm, PAGE[0] - MARGIN, 11 * mm)
        canvas.setFont("Arial", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 7 * mm, title)
        canvas.drawRightString(PAGE[0] - MARGIN, 7 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def bullets(items, st):
    return [Paragraph(i, st["bullet"], bulletText="•") for i in items]


__all__ = ["styles", "table", "image", "footer", "bullets", "WIDTH", "MARGIN", "PAGE", "ACCENT", "INK",
           "MUTED", "LIGHT", "PALE", "GOOD", "KeepTogether", "Paragraph", "Spacer", "Table", "TableStyle", "mm"]
