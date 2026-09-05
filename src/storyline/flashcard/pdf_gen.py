import os
from pathlib import Path

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from storyline.logging import get_logger

_log = get_logger("flashcard.pdf")

FONT_NAME = "CJKFont"

_BUNDLED_FONT = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "scripts" / "NotoSansSC-Flashcards.ttf"
)

_CJK_FONT_CANDIDATES = [
    (str(_BUNDLED_FONT), 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("/usr/share/fonts/truetype/arphic/uming.ttc", 0),
    ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 0),
    (str(Path.home() / "Library/Fonts/PingFang.ttc"), 0),
    ("C:/Windows/Fonts/msyh.ttc", 0),
]

_REQUIRED_TEST_CHARS = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "\u0101\u00e1\u01ce\u00e0\u0113\u00e9\u011b\u00e8\u012b\u00ed\u01d0\u00ec"
    "\u014d\u00f3\u01d2\u00f2\u016b\u00fa\u01d4\u00f9\u01d6\u01d8\u01da\u01dc\u00fc"
    "\u4e0b\u5df4\u4ed6\u7528\u624b\u6258\u7740\u4e2d\u56fd\u4f60\u597d\u8c22"
)

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
        "Make sure NotoSansSC-Flashcards.ttf is in the scripts/ folder, "
        "or install a complete CJK font, e.g.:\n"
        "    sudo apt install fonts-wqy-zenhei"
    )


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
        c.drawString(x + 0.4, baseline_y, text)


def cell_origin(row, col, margin, cell_w, cell_h, page_h):
    x0 = margin + col * cell_w
    top_y = page_h - margin - row * cell_h
    y0 = top_y - cell_h
    return x0, y0


def draw_cut_guides(c, margin, page_w, page_h, cols, rows, cell_w, cell_h):
    c.saveState()
    c.setStrokeColorRGB(0.75, 0.75, 0.75)
    c.setLineWidth(0.4)
    c.setDash(3, 3)
    for col in range(cols + 1):
        x = margin + col * cell_w
        c.line(x, margin, x, page_h - margin)
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

    draw_cut_guides(c, margin, page_w, page_h, cols, rows, cell_w, cell_h)
    for idx in range(n_cards):
        row, col = divmod(idx, cols)
        x0, y0 = cell_origin(row, col, margin, cell_w, cell_h, page_h)
        draw_image_card(c, image_paths[idx], x0, y0, cell_w, cell_h, border)
    c.showPage()

    draw_cut_guides(c, margin, page_w, page_h, cols, rows, cell_w, cell_h)
    for idx in range(n_cards):
        row, col = divmod(idx, cols)
        back_row = (rows - 1 - row) if mirror in ("rows", "both") else row
        back_col = (cols - 1 - col) if mirror in ("columns", "both") else col
        x0, y0 = cell_origin(back_row, back_col, margin, cell_w, cell_h, page_h)
        draw_text_card(c, texts[idx], x0, y0, cell_w, cell_h, border)
    c.showPage()

    c.save()


def generate_pdf(
    input_dir: Path,
    output_path: Path | None = None,
    page_size=A4,
    cols: int = 2,
    rows: int = 3,
    border_cm: float = 0.5,
    binding: str = "long-edge",
) -> Path:
    if output_path is None:
        output_path = input_dir / "cards.pdf"

    if cols * rows != 6:
        raise ValueError(f"cols x rows must equal 6, got {cols} x {rows} = {cols * rows}")

    mirror = "columns" if binding == "long-edge" else "rows"

    build_pdf(input_dir, output_path, page_size, cols, rows, border_cm, mirror)
    _log.info("event=pdf_written path=%s", output_path)
    return output_path