"""
Render a pyte.Screen to a PIL Image / PNG bytes.
Ported from tui/screenshot.py — no dependency on tui/.
"""

from io import BytesIO

import pyte
from PIL import Image, ImageDraw, ImageFont

# ── font paths ─────────────────────────────────────────────────────────────────
MONO_FONT_PATH = "/System/Library/Fonts/SFNSMono.ttf"
CJK_FONT_PATH  = "/System/Library/Fonts/Hiragino Sans GB.ttc"

# ── terminal dimensions ────────────────────────────────────────────────────────
COLS, ROWS = 140, 36

# ── rendering constants ────────────────────────────────────────────────────────
FONT_SIZE = 15
CELL_W    = 9
CELL_H    = 20
PAD_X     = 4
PAD_Y     = 4

IMG_W = COLS * CELL_W + PAD_X * 2
IMG_H = ROWS * CELL_H + PAD_Y * 2

ANSI_COLORS = {
    "black":         (  0,   0,   0),
    "red":           (197,  15,  31),
    "green":         ( 19, 161,  14),
    "brown":         (193, 156,   0),
    "blue":          ( 58, 150, 221),
    "magenta":       (136,  23, 152),
    "cyan":          ( 58, 150, 221),
    "white":         (204, 204, 204),
    "brightblack":   (118, 118, 118),
    "brightred":     (231,  72,  86),
    "brightgreen":   ( 22, 198,  12),
    "brightyellow":  (249, 241, 165),
    "brightblue":    ( 59, 120, 255),
    "brightmagenta": (180,   0, 158),
    "brightcyan":    ( 97, 214, 214),
    "brightwhite":   (242, 242, 242),
    "default":       (204, 204, 204),
}

DEFAULT_BG = (18, 18, 18)
DEFAULT_FG = (204, 204, 204)


def resolve_color(c, is_bg: bool):
    if c == "default":
        return DEFAULT_BG if is_bg else DEFAULT_FG
    if isinstance(c, str) and len(c) == 6:
        try:
            return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
        except ValueError:
            pass
    if isinstance(c, str) and c in ANSI_COLORS:
        return ANSI_COLORS[c]
    if isinstance(c, int):
        if c < 16:
            names = list(ANSI_COLORS.keys())[:16]
            return ANSI_COLORS[names[c]]
        if c < 232:
            c -= 16
            b = c % 6; c //= 6
            g = c % 6; r = c // 6
            def v(x): return 0 if x == 0 else 55 + x * 40
            return (v(r), v(g), v(b))
        gray = 8 + (c - 232) * 10
        return (gray, gray, gray)
    return DEFAULT_BG if is_bg else DEFAULT_FG


def is_cjk(ch: str) -> bool:
    if not ch or ch == " ":
        return False
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x20000 <= cp <= 0x2A6DF
        or 0x3000 <= cp <= 0x303F
        or 0xFF00 <= cp <= 0xFFEF
        or 0x3040 <= cp <= 0x30FF
        or 0xAC00 <= cp <= 0xD7AF
    )


def render_screen(screen: pyte.Screen) -> Image.Image:
    cols = screen.columns
    rows = screen.lines
    img_w = cols * CELL_W + PAD_X * 2
    img_h = rows * CELL_H + PAD_Y * 2

    mono_font = ImageFont.truetype(MONO_FONT_PATH, FONT_SIZE)
    cjk_font  = ImageFont.truetype(CJK_FONT_PATH,  FONT_SIZE)

    img  = Image.new("RGB", (img_w, img_h), DEFAULT_BG)
    draw = ImageDraw.Draw(img)

    for row_idx in range(rows):
        line = screen.buffer[row_idx]
        col  = 0
        while col < cols:
            char = line[col]
            ch   = char.data if char.data else " "

            fg = resolve_color(char.fg, is_bg=False)
            bg = resolve_color(char.bg, is_bg=True)

            wide = is_cjk(ch)
            cell_span = CELL_W * 2 if wide else CELL_W

            x = PAD_X + col * CELL_W
            y = PAD_Y + row_idx * CELL_H

            draw.rectangle([x, y, x + cell_span - 1, y + CELL_H - 1], fill=bg)

            if ch.strip():
                font = cjk_font if wide else mono_font
                if char.bold:
                    draw.text((x + 1, y + 2), ch, font=font, fill=fg)
                draw.text((x, y + 2), ch, font=font, fill=fg)

            col += 2 if wide else 1

    return img


def render_to_png_bytes(screen: pyte.Screen) -> bytes:
    img = render_screen(screen)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
