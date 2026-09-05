#!/usr/bin/env python3
"""
make_flashcards.py

Generate a double-sided, print-ready PDF of six square-image flashcards.

Input directory must contain:
    img_1.png .. img_6.png     (square PNGs, e.g. 1024x1024)
    text_1.txt .. text_6.txt   (the text for the back of each card)

Each text file is read as a list of non-empty lines. The script is tuned
for the 6-line format:

    headword
    pinyin
    meaning
    example sentence
    example sentence pinyin
    example sentence translation

but will also lay out files with a different number of lines reasonably.

Output:
    cards.pdf written into the same input directory (page 1 = images,
    page 2 = text, positioned so that duplex-printing on the LONG edge
    (i.e. "flip on the vertical edge", like turning a book page) puts
    each card's text on the back of its picture).

Usage:
    python make_flashcards.py /path/to/input_dir
    python make_flashcards.py /path/to/input_dir --page-size letter
    python make_flashcards.py /path/to/input_dir --binding short-edge
"""

import argparse
import os
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# --------------------------------------------------------------------------
# CJK-capable TrueType font.
#
# ReportLab's TTFont loader can't handle CFF/PostScript-outline fonts (which
# is what the Noto CJK .ttc files are - they'll raise "postscript outlines
# are not supported"), so we need a TrueType ("glyf" outline) font. We also
# can't just trust *any* installed system font: some CJK system fonts are
# missing plain Latin/accented-Latin glyphs (or vice versa), and ReportLab
# silently skips characters a font doesn't have rather than erroring - so a
# bad font produces a PDF that looks fine until you notice whole lines of
# pinyin/English are blank.
#
# To sidestep all of that, this script ships with its own font
# (NotoSansSC-Flashcards.ttf, a subsetted build of Google's Noto Sans SC
# covering Latin, pinyin diacritics, and common Chinese characters) placed
# next to this .py file. That bundled font is tried first. System fonts are
# only used as a fallback, and - critically - every candidate (bundled or
# system) is checked for actual glyph coverage of representative test
# characters before being accepted, so a partially-broken font is rejected
# instead of silently producing blank text.
# --------------------------------------------------------------------------
FONT_NAME = "CJKFont"

_BUNDLED_FONT = Path(__file__).resolve().parent / "NotoSansSC-Flashcards.ttf"

_CJK_FONT_CANDIDATES = [
    (str(_BUNDLED_FONT), 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("/usr/share/fonts/truetype/arphic/uming.ttc", 0),
    ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 0),
    (str(Path.home() / "Library/Fonts/PingFang.ttc"), 0),
    ("C:/Windows/Fonts/msyh.ttc", 0),
]

# A representative sample of characters the font MUST contain: plain ASCII
# letters/digits, the full set of pinyin tone-mark vowels (all four tones,
# including umlaut-u), and a handful of common Hanzi. This is a coverage
# smoke test, not an exhaustive check, but it reliably catches fonts that
# are missing whole categories of glyphs (which is exactly the failure mode
# that causes silently-blank lines).
_REQUIRED_TEST_CHARS = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü"
    "下巴他用手托着中国你好谢"
)


def _font_has_full_coverage(ttfont_obj, chars=_REQUIRED_TEST_CHARS):
    char_to_glyph = ttfont_obj.face.charToGlyph
    missing = [ch for ch in chars if ord(ch) not in char_to_glyph]
    return (not missing), missing


def register_cjk_font():
    attempted = []
    for path, idx in _CJK_FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            tt = TTFont(FONT_NAME, path, subfontIndex=idx)
        except Exception as e:
            attempted.append(f"  {path}: failed to load ({e})")
            continue
        ok, missing = _font_has_full_coverage(tt)
        if not ok:
            preview = "".join(missing[:10])
            attempted.append(f"  {path}: missing glyphs, e.g. {preview!r}")
            continue
        pdfmetrics.registerFont(tt)
        return
    detail = "\n".join(attempted) if attempted else "  (no candidate font files found on this system)"
    raise RuntimeError(
        "Could not find a CJK TrueType font with full Latin+pinyin+Hanzi glyph "
        "coverage on this system.\n"
        f"Candidates tried:\n{detail}\n\n"
        "Make sure NotoSansSC-Flashcards.ttf (shipped alongside this script) is "
        "in the same folder as make_flashcards.py, or install a complete CJK "
        "font, e.g. on Debian/Ubuntu:\n"
        "    sudo apt install fonts-wqy-zenhei\n"
        "or edit _CJK_FONT_CANDIDATES in this script to point at one you have "
        "(must be a .ttf/.ttc with TrueType, not CFF/PostScript, outlines)."
    )


# --------------------------------------------------------------------------
# Text-side layout styles
# --------------------------------------------------------------------------
# Tuned for the standard 6-line format:
#   0 headword | 1 word pinyin | 2 word meaning |
#   3 sentence | 4 sentence pinyin | 5 sentence translation
SIX_LINE_STYLES = [
    dict(size=30, color=(0.05, 0.05, 0.05), bold=True, gap_after=8),
    dict(size=15, color=(0.35, 0.35, 0.35), bold=False, gap_after=4),
    dict(size=14, color=(0.05, 0.05, 0.05), bold=False, gap_after=16),
    dict(size=13.5, color=(0.05, 0.05, 0.05), bold=False, gap_after=4),
    dict(size=10.5, color=(0.42, 0.42, 0.42), bold=False, gap_after=3),
    dict(size=11, color=(0.25, 0.25, 0.25), bold=False, gap_after=0),
]


def generic_styles(n):
    styles = []
    for i in range(n):
        if i == 0:
            styles.append(dict(size=24, color=(0.05, 0.05, 0.05), bold=True, gap_after=8))
        else:
            styles.append(dict(size=13, color=(0.1, 0.1, 0.1), bold=False, gap_after=4))
    return styles


def styles_for(lines):
    if len(lines) == 6:
        return SIX_LINE_STYLES
    return generic_styles(len(lines))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def load_lines(path):
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def fit_font_size(text, font, start_size, max_width, min_size=6.0):
    size = start_size
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_width:
        size -= 0.5
    return size


def draw_centered_text(c, text, cx, baseline_y, font, size, color, bold=False):
    c.setFont(font, size)
    c.setFillColorRGB(*color)
    w = pdfmetrics.stringWidth(text, font, size)
    x = cx - w / 2
    c.drawString(x, baseline_y, text)
    if bold:
        # ReportLab has no bold weight for arbitrary TTFs, so fake it with
        # a second, slightly offset pass ("faux bold").
        c.drawString(x + 0.4, baseline_y, text)


def cell_origin(row, col, margin, cell_w, cell_h, page_h):
    """Bottom-left corner of a grid cell. row 0 = top row."""
    x0 = margin + col * cell_w
    top_y = page_h - margin - row * cell_h
    y0 = top_y - cell_h
    return x0, y0


def draw_cut_guides(c, margin, page_w, page_h, cols, rows, cell_w, cell_h):
    c.saveState()
    c.setStrokeColorRGB(0.75, 0.75, 0.75)
    c.setLineWidth(0.4)
    c.setDash(3, 3)
    # vertical lines
    for col in range(cols + 1):
        x = margin + col * cell_w
        c.line(x, margin, x, page_h - margin)
    # horizontal lines
    for row in range(rows + 1):
        y = margin + row * cell_h
        c.line(margin, y, page_w - margin, y)
    c.restoreState()


def draw_image_card(c, img_path, x0, y0, cell_w, cell_h, border):
    size = min(cell_w, cell_h) - 2 * border
    cx = x0 + cell_w / 2
    cy = y0 + cell_h / 2
    c.drawImage(
        str(img_path),
        cx - size / 2,
        cy - size / 2,
        width=size,
        height=size,
        preserveAspectRatio=True,
        anchor="c",
        mask="auto",
    )


def draw_text_card(c, lines, x0, y0, cell_w, cell_h, border):
    if not lines:
        return
    content_w = cell_w - 2 * border
    cx = x0 + cell_w / 2
    cy = y0 + cell_h / 2

    styles = styles_for(lines)
    slot_heights = [st["size"] * 1.2 + st["gap_after"] for st in styles]
    total_h = sum(slot_heights)

    y_cursor = cy + total_h / 2
    for line, st, slot_h in zip(lines, styles, slot_heights):
        baseline_y = y_cursor - st["size"] * 0.85
        fsize = fit_font_size(line, FONT_NAME, st["size"], content_w)
        draw_centered_text(c, line, cx, baseline_y, FONT_NAME, fsize, st["color"], bold=st["bold"])
        y_cursor -= slot_h


# --------------------------------------------------------------------------
# Main build
# --------------------------------------------------------------------------
def build_pdf(input_dir, output_path, page_size, cols, rows, border_cm, mirror):
    n_cards = cols * rows
    register_cjk_font()

    image_paths = [input_dir / f"img_{i}.png" for i in range(1, n_cards + 1)]
    text_paths = [input_dir / f"text_{i}.txt" for i in range(1, n_cards + 1)]

    missing = [p.name for p in image_paths + text_paths if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing expected file(s) in {input_dir}: {', '.join(missing)}"
        )

    texts = [load_lines(p) for p in text_paths]

    page_w, page_h = page_size
    margin = border_cm * cm
    usable_w = page_w - 2 * margin
    usable_h = page_h - 2 * margin
    cell_w = usable_w / cols
    cell_h = usable_h / rows
    border = border_cm * cm

    c = canvas.Canvas(str(output_path), pagesize=page_size)

    # ---- PAGE 1: images ----
    draw_cut_guides(c, margin, page_w, page_h, cols, rows, cell_w, cell_h)
    for idx in range(n_cards):
        row, col = divmod(idx, cols)
        x0, y0 = cell_origin(row, col, margin, cell_w, cell_h, page_h)
        draw_image_card(c, image_paths[idx], x0, y0, cell_w, cell_h, border)
    c.showPage()

    # ---- PAGE 2: text, mirrored for duplex printing ----
    draw_cut_guides(c, margin, page_w, page_h, cols, rows, cell_w, cell_h)
    for idx in range(n_cards):
        row, col = divmod(idx, cols)
        back_row = (rows - 1 - row) if mirror in ("rows", "both") else row
        back_col = (cols - 1 - col) if mirror in ("columns", "both") else col
        x0, y0 = cell_origin(back_row, back_col, margin, cell_w, cell_h, page_h)
        draw_text_card(c, texts[idx], x0, y0, cell_w, cell_h, border)
    c.showPage()

    c.save()


def main():
    parser = argparse.ArgumentParser(description="Generate a double-sided flashcard PDF.")
    parser.add_argument("input_dir", type=Path, help="Directory containing img_1..6.png and text_1..6.txt")
    parser.add_argument("--output", default="cards.pdf", help="Output filename (written into input_dir)")
    parser.add_argument("--page-size", choices=["a4", "letter"], default="letter")
    parser.add_argument("--cols", type=int, default=2, help="Grid columns (cols x rows must be 6)")
    parser.add_argument("--rows", type=int, default=3, help="Grid rows (cols x rows must be 6)")
    parser.add_argument("--border-cm", type=float, default=0.5, help="Border/margin around each card, in cm")
    parser.add_argument(
        "--binding",
        choices=["long-edge", "short-edge"],
        default="long-edge",
        help=(
            "Which edge the duplex printer flips on. 'long-edge' (default) matches "
            "flipping the page over its vertical edge, like a book, which mirrors "
            "column order between front and back. 'short-edge' mirrors row order instead."
        ),
    )
    args = parser.parse_args()

    if args.cols * args.rows != 6:
        parser.error(f"--cols x --rows must equal 6 (got {args.cols} x {args.rows} = {args.cols * args.rows})")

    mirror = "columns" if args.binding == "long-edge" else "rows"
    page_size = A4 if args.page_size == "a4" else LETTER

    input_dir = args.input_dir.expanduser().resolve()
    output_path = input_dir / args.output

    build_pdf(input_dir, output_path, page_size, args.cols, args.rows, args.border_cm, mirror)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
