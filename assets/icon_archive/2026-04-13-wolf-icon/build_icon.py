from collections import deque
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


ARCHIVE_DIR = Path(__file__).resolve().parent
SRC = ARCHIVE_DIR / "Gemini_Generated_Image_ctm3lsctm3lsctm3.png"
OUT_PNG = ARCHIVE_DIR / "icon.png"
OUT_ICNS = ARCHIVE_DIR / "icon.icns"

FINAL_SIZE = 1024
SUPERSAMPLE_SCALE = 3


def _find_inner_light_regions(gray: Image.Image, threshold: int) -> Image.Image:
    width, height = gray.size
    is_light = [[gray.getpixel((x, y)) >= threshold for x in range(width)] for y in range(height)]
    visited = [[False] * width for _ in range(height)]
    queue: deque[tuple[int, int]] = deque()

    for x in range(width):
        for y in (0, height - 1):
            if is_light[y][x] and not visited[y][x]:
                visited[y][x] = True
                queue.append((x, y))

    for y in range(height):
        for x in (0, width - 1):
            if is_light[y][x] and not visited[y][x]:
                visited[y][x] = True
                queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height and is_light[ny][nx] and not visited[ny][nx]:
                visited[ny][nx] = True
                queue.append((nx, ny))

    inner_light = Image.new("L", (width, height), 0)
    pixels = inner_light.load()
    for y in range(height):
        for x in range(width):
            if is_light[y][x] and not visited[y][x]:
                pixels[x, y] = 255
    return inner_light


def main() -> None:
    source = Image.open(SRC).convert("RGBA")
    source_gray = source.convert("L")
    rough_mask = source_gray.point(lambda p: 255 if p < 236 else 0)
    bbox = rough_mask.getbbox()
    if bbox is None:
        raise RuntimeError("No subject detected from source image.")

    left, top, right, bottom = bbox
    crop_box = (
        max(0, left - 56),
        max(0, top - 48),
        min(source.width, right + 56),
        min(source.height, bottom + 48),
    )
    subject = source.crop(crop_box).convert("RGBA")

    canvas_size = FINAL_SIZE * SUPERSAMPLE_SCALE
    target_height = 836 * SUPERSAMPLE_SCALE
    resize_scale = target_height / subject.height
    target_width = round(subject.width * resize_scale)

    subject_gray = subject.convert("L").resize((target_width, target_height), Image.Resampling.LANCZOS)

    dark_alpha = ImageOps.invert(subject_gray).point(lambda p: max(0, min(255, int(p * 1.18))))
    dark_alpha = dark_alpha.filter(ImageFilter.GaussianBlur(0.8 * SUPERSAMPLE_SCALE))

    inner_light = _find_inner_light_regions(subject_gray, threshold=242)
    inner_light = inner_light.filter(ImageFilter.GaussianBlur(1.2 * SUPERSAMPLE_SCALE))

    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))

    gradient = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
    gradient_pixels = gradient.load()
    for y in range(target_height):
        ty = y / max(1, target_height - 1)
        for x in range(target_width):
            tx = x / max(1, target_width - 1)
            mix = min(1.0, max(0.0, tx * 0.5 + ty * 0.34))
            r = round(8 * (1 - mix) + 28 * mix)
            g = round(28 * (1 - mix) + 92 * mix)
            b = round(82 * (1 - mix) + 164 * mix)
            gradient_pixels[x, y] = (r, g, b, 255)
    colored_wolf = Image.composite(
        gradient,
        Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0)),
        dark_alpha,
    )

    face_gradient = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
    face_pixels = face_gradient.load()
    for y in range(target_height):
        t = y / max(1, target_height - 1)
        rr = round(242 * (1 - t) + 224 * t)
        gg = round(247 * (1 - t) + 234 * t)
        bb = round(255 * (1 - t) + 246 * t)
        for x in range(target_width):
            face_pixels[x, y] = (rr, gg, bb, 255)
    face_fill = Image.composite(
        face_gradient,
        Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0)),
        inner_light,
    )

    wolf = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
    wolf = Image.alpha_composite(wolf, face_fill)
    wolf = Image.alpha_composite(wolf, colored_wolf)

    shadow_mask = dark_alpha.filter(ImageFilter.GaussianBlur(12 * SUPERSAMPLE_SCALE))
    shadow = Image.new("RGBA", (target_width, target_height), (4, 16, 40, 78))
    shadow.putalpha(shadow_mask)

    gloss = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse(
        (-80 * SUPERSAMPLE_SCALE, -150 * SUPERSAMPLE_SCALE, int(target_width * 0.78), int(target_height * 0.42)),
        fill=(150, 220, 255, 56),
    )
    gloss = gloss.filter(ImageFilter.GaussianBlur(24 * SUPERSAMPLE_SCALE))
    gloss = Image.composite(gloss, Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0)), dark_alpha)
    wolf = Image.alpha_composite(wolf, gloss)

    outer_edge = ImageChops.subtract(
        dark_alpha.filter(ImageFilter.GaussianBlur(2.4 * SUPERSAMPLE_SCALE)),
        dark_alpha.filter(ImageFilter.GaussianBlur(6.4 * SUPERSAMPLE_SCALE)),
    )
    outer_edge = outer_edge.filter(ImageFilter.GaussianBlur(0.8 * SUPERSAMPLE_SCALE))
    edge_rgba = Image.new("RGBA", (target_width, target_height), (164, 214, 255, 0))
    edge_rgba.putalpha(outer_edge.point(lambda p: min(82, p)))
    wolf = Image.alpha_composite(wolf, edge_rgba)

    inner_edge = ImageChops.subtract(
        inner_light.filter(ImageFilter.GaussianBlur(1.8 * SUPERSAMPLE_SCALE)),
        inner_light.filter(ImageFilter.GaussianBlur(4.8 * SUPERSAMPLE_SCALE)),
    )
    inner_edge = inner_edge.filter(ImageFilter.GaussianBlur(0.8 * SUPERSAMPLE_SCALE))
    crease = Image.new("RGBA", (target_width, target_height), (16, 56, 118, 0))
    crease.putalpha(inner_edge.point(lambda p: min(58, p)))
    wolf = Image.alpha_composite(wolf, crease)

    paste_x = 156 * SUPERSAMPLE_SCALE
    paste_y = 98 * SUPERSAMPLE_SCALE
    canvas.alpha_composite(shadow, (paste_x + 12 * SUPERSAMPLE_SCALE, paste_y + 20 * SUPERSAMPLE_SCALE))
    canvas.alpha_composite(wolf, (paste_x, paste_y))

    final = canvas.resize((FINAL_SIZE, FINAL_SIZE), Image.Resampling.LANCZOS)
    final.save(OUT_PNG)
    final.save(OUT_ICNS)

    print(f"Generated: {OUT_PNG}")
    print(f"Generated: {OUT_ICNS}")


if __name__ == "__main__":
    main()
