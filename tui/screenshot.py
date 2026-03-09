"""
Run tui/main.py in a PTY, capture the terminal state with pyte,
and render it to a PNG using Pillow.
"""

import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time

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


def run_app_and_capture(base_url: str, wait: float = 5.0) -> pyte.Screen:
    screen = pyte.Screen(COLS, ROWS)
    stream = pyte.ByteStream(screen)

    master_fd, slave_fd = pty.openpty()

    pid = os.fork()
    if pid == 0:
        # child process
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", ROWS, COLS, 0, 0))
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        os.close(master_fd)
        os.close(slave_fd)

        venv_python = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".venv", "bin", "python",
        )
        script = os.path.abspath(__file__).replace("screenshot.py", "main.py")
        os.execv(venv_python, [venv_python, script, "--base-url", base_url])
        sys.exit(1)

    # parent
    os.close(slave_fd)
    deadline = time.time() + wait

    try:
        while time.time() < deadline:
            r, _, _ = select.select([master_fd], [], [], 0.1)
            if r:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                stream.feed(data)
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
        except Exception:
            pass
        os.close(master_fd)

    return screen


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
    mono_font = ImageFont.truetype(MONO_FONT_PATH, FONT_SIZE)
    cjk_font  = ImageFont.truetype(CJK_FONT_PATH,  FONT_SIZE)

    img  = Image.new("RGB", (IMG_W, IMG_H), DEFAULT_BG)
    draw = ImageDraw.Draw(img)

    for row_idx in range(ROWS):
        line = screen.buffer[row_idx]
        col  = 0
        while col < COLS:
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


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--out", default="/tmp/tui_preview.png")
    parser.add_argument("--wait", type=float, default=5.0,
                        help="seconds to wait for app to render")
    args = parser.parse_args()

    print(f"Running TUI in PTY (wait {args.wait}s) …", flush=True)
    screen = run_app_and_capture(args.base_url, wait=args.wait)
    print("Rendering to PNG …", flush=True)
    img = render_screen(screen)
    img.save(args.out)
    print(f"Saved → {args.out}")


if __name__ == "__main__":
    main()
