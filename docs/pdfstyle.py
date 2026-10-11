"""Shared look for Naqsha documents (reportlab)."""
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, Paragraph, Spacer, Table, TableStyle)

import os

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "fonts")
ASSETS = os.path.join(HERE, "assets")
# Archivo (SIL Open Font License), the face the app uses; static cuts made from the variable font
for name, file in (("Body", "Archivo-Regular.ttf"), ("Body-Bold", "Archivo-Bold.ttf"),
                   ("Body-Medium", "Archivo-Medium.ttf"), ("Display", "Archivo-Display.ttf")):
    pdfmetrics.registerFont(TTFont(name, os.path.join(FONTS, file)))
pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Medium", boldItalic="Body-Bold")
pdfmetrics.registerFont(TTFont("Mono", "/System/Library/Fonts/Supplemental/Courier New.ttf"))

# Naqsha palette: survey-ink navy, teal, saffron
INK = colors.HexColor("#0a1a2e")
MUTED = colors.HexColor("#4d6178")
ACCENT = colors.HexColor("#0e7c77")
SAFFRON = colors.HexColor("#d88a12")
LIGHT = colors.HexColor("#dfe6ed")
PALE = colors.HexColor("#f2f5f8")
GOOD = colors.HexColor("#0b6b3c")

PAGE = A4
MARGIN = 16 * mm
WIDTH = PAGE[0] - 2 * MARGIN


def styles(base=9.6):
    s = {}
    s["body"] = ParagraphStyle("body", fontName="Body", fontSize=base, leading=base * 1.38, textColor=INK,
                               spaceAfter=base * 0.45, alignment=TA_LEFT)
    s["small"] = ParagraphStyle("small", parent=s["body"], fontSize=base - 1.4, leading=(base - 1.4) * 1.35,
                                textColor=MUTED)
    s["cell"] = ParagraphStyle("cell", parent=s["body"], fontSize=base - 1.2, leading=(base - 1.2) * 1.3,
                               spaceAfter=0)
    s["cellb"] = ParagraphStyle("cellb", parent=s["cell"], fontName="Body-Bold")
    s["title"] = ParagraphStyle("title", fontName="Display", fontSize=24, leading=28, textColor=INK,
                                spaceAfter=4)
    s["subtitle"] = ParagraphStyle("subtitle", fontName="Body", fontSize=11.5, leading=15, textColor=MUTED,
                                   spaceAfter=10)
    s["h1"] = ParagraphStyle("h1", fontName="Display", fontSize=13.5, leading=17, textColor=ACCENT,
                             spaceBefore=9, spaceAfter=4, keepWithNext=1)
    s["h2"] = ParagraphStyle("h2", fontName="Body-Bold", fontSize=10.4, leading=13, textColor=INK,
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


COVER_H = {"guide": 210 * 430 / 794 * mm, "tech": 210 * 330 / 794 * mm}


def pages(doc_title, cover):
    """(first page, later pages) decorators: the brand cover band on page one, then the
    Naqsha mark, document title and a saffron rule at the top of every page."""
    def foot(canvas, doc):
        canvas.setStrokeColor(LIGHT)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, 11 * mm, PAGE[0] - MARGIN, 11 * mm)
        canvas.setFont("Body", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 7 * mm, f"Naqsha  ·  {doc_title}  ·  DenseFusion")
        canvas.drawRightString(PAGE[0] - MARGIN, 7 * mm, f"Page {doc.page}")

    def first(canvas, doc):
        canvas.saveState()
        h = COVER_H[cover]
        canvas.drawImage(os.path.join(ASSETS, f"cover-{cover}.png"), 0, PAGE[1] - h, width=PAGE[0], height=h)
        foot(canvas, doc)
        canvas.restoreState()

    def later(canvas, doc):
        canvas.saveState()
        mh = 5.2 * mm
        canvas.drawImage(os.path.join(ASSETS, "mark.png"), MARGIN, PAGE[1] - 11.5 * mm, height=mh,
                         width=mh * 381 / 78, mask="auto", preserveAspectRatio=True, anchor="sw")
        canvas.setFont("Body", 7.8)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(PAGE[0] - MARGIN, PAGE[1] - 9.8 * mm, doc_title)
        canvas.setStrokeColor(SAFFRON)
        canvas.setLineWidth(0.9)
        canvas.line(MARGIN, PAGE[1] - 13 * mm, MARGIN + 18 * mm, PAGE[1] - 13 * mm)
        canvas.setStrokeColor(LIGHT)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN + 18 * mm, PAGE[1] - 13 * mm, PAGE[0] - MARGIN, PAGE[1] - 13 * mm)
        foot(canvas, doc)
        canvas.restoreState()
    return first, later


def cover_space(cover, top_margin):
    """Spacer that starts page one's text just below the cover band."""
    return Spacer(1, COVER_H[cover] - top_margin + 7 * mm)


def footer(title):
    """Kept for older scripts: plain footer only."""
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Body", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 7 * mm, title)
        canvas.drawRightString(PAGE[0] - MARGIN, 7 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def bullets(items, st):
    return [Paragraph(i, st["bullet"], bulletText="•") for i in items]


__all__ = ["styles", "table", "image", "footer", "bullets", "pages", "cover_space", "SAFFRON", "WIDTH", "MARGIN", "PAGE", "ACCENT", "INK",
           "MUTED", "LIGHT", "PALE", "GOOD", "KeepTogether", "Paragraph", "Spacer", "Table", "TableStyle", "mm"]
