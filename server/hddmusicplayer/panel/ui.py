"""Panel için tema, yazı tipleri ve çizim yardımcıları.

480×320 gibi küçük bir ekranda okunabilirlik her şeyden önemli: yüksek kontrast,
kalın yazı, geniş dokunma hedefleri. Simgeler yazı tipi glifleriyle değil
poligonla çizilir — hangi fontun hangi sembolü içerdiğine bağlı kalmamak için.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --- Renkler ------------------------------------------------------------------

BG = (13, 15, 18)
SURFACE = (26, 30, 36)
SURFACE_HI = (38, 44, 53)
ACCENT = (232, 180, 74)        # sıcak amber — hi-fi cihaz hissi
ACCENT_DIM = (120, 92, 38)
TEXT = (242, 244, 247)
TEXT_MUTED = (139, 147, 161)
DANGER = (222, 92, 84)
LOVE = (233, 86, 105)

#: Dokunma hedefi alt sınırı. Rezistif ekranda parmakla isabet için gerekli.
MIN_TOUCH = 64


# --- Yazı tipleri -------------------------------------------------------------

_FONT_CANDIDATES = {
    "regular": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ),
    "bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
}

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = ("bold" if bold else "regular", size)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached

    for candidate in _FONT_CANDIDATES[key[0]]:
        if Path(candidate).is_file():
            loaded = ImageFont.truetype(candidate, size)
            break
    else:
        loaded = ImageFont.load_default()

    _font_cache[key] = loaded
    return loaded


# --- Metin --------------------------------------------------------------------

def text_width(draw: ImageDraw.ImageDraw, text: str, fnt) -> int:
    return int(draw.textlength(text, font=fnt))


def ellipsize(draw: ImageDraw.ImageDraw, text: str, fnt, max_width: int) -> str:
    """Metni verilen genişliğe sığdırır, taşarsa sonuna … ekler."""
    if not text:
        return ""
    if text_width(draw, text, fnt) <= max_width:
        return text
    ellipsis = "…"
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if text_width(draw, text[:middle] + ellipsis, fnt) <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low] + ellipsis if low else ellipsis


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fnt,
              fill=TEXT, max_width: int | None = None, anchor: str = "la") -> None:
    if max_width is not None:
        text = ellipsize(draw, text, fnt, max_width)
    draw.text(xy, text, font=fnt, fill=fill, anchor=anchor)


def format_time(seconds: float | None) -> str:
    if not seconds or seconds < 0:
        return "0:00"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# --- Kutular ------------------------------------------------------------------

def panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill=SURFACE,
          radius: int = 8, outline=None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def progress_bar(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
                 ratio: float, fill=ACCENT, track=SURFACE_HI) -> None:
    x0, y0, x1, y1 = box
    radius = (y1 - y0) // 2
    draw.rounded_rectangle(box, radius=radius, fill=track)
    ratio = max(0.0, min(1.0, ratio))
    if ratio <= 0:
        return
    end = x0 + int((x1 - x0) * ratio)
    if end - x0 >= radius * 2:
        draw.rounded_rectangle((x0, y0, end, y1), radius=radius, fill=fill)
    else:
        draw.ellipse((x0, y0, x0 + radius * 2, y1), fill=fill)


def album_art(image: Image.Image, art: Image.Image | None, box: tuple[int, int, int, int],
              draw: ImageDraw.ImageDraw) -> None:
    """Kapağı kutuya yerleştirir; kapak yoksa nota simgeli yer tutucu çizer."""
    x0, y0, x1, y1 = box
    size = min(x1 - x0, y1 - y0)

    if art is None:
        panel(draw, (x0, y0, x0 + size, y0 + size), fill=SURFACE, radius=6)
        icon_note(draw, (x0 + size // 2, y0 + size // 2), size // 4, TEXT_MUTED)
        return

    fitted = art
    if fitted.size != (size, size):
        fitted = fitted.copy()
        fitted.thumbnail((size, size), Image.LANCZOS)

    offset_x = x0 + (size - fitted.width) // 2
    offset_y = y0 + (size - fitted.height) // 2
    image.paste(fitted, (offset_x, offset_y))


# --- Simgeler -----------------------------------------------------------------
# Hepsi (cx, cy) merkezli, `size` yarı-genişlikli çizilir.

def icon_play(draw, center, size, fill=TEXT) -> None:
    cx, cy = center
    draw.polygon(
        [(cx - size * 0.55, cy - size), (cx - size * 0.55, cy + size), (cx + size * 0.85, cy)],
        fill=fill,
    )


def icon_pause(draw, center, size, fill=TEXT) -> None:
    cx, cy = center
    bar = size * 0.34
    draw.rectangle((cx - size * 0.7, cy - size, cx - size * 0.7 + bar, cy + size), fill=fill)
    draw.rectangle((cx + size * 0.36, cy - size, cx + size * 0.36 + bar, cy + size), fill=fill)


def icon_next(draw, center, size, fill=TEXT) -> None:
    cx, cy = center
    draw.polygon([(cx - size, cy - size * 0.8), (cx - size, cy + size * 0.8), (cx + size * 0.3, cy)],
                 fill=fill)
    draw.rectangle((cx + size * 0.45, cy - size * 0.8, cx + size * 0.75, cy + size * 0.8), fill=fill)


def icon_previous(draw, center, size, fill=TEXT) -> None:
    cx, cy = center
    draw.polygon([(cx + size, cy - size * 0.8), (cx + size, cy + size * 0.8), (cx - size * 0.3, cy)],
                 fill=fill)
    draw.rectangle((cx - size * 0.75, cy - size * 0.8, cx - size * 0.45, cy + size * 0.8), fill=fill)


def icon_heart(draw, center, size, fill=LOVE, filled=True) -> None:
    cx, cy = center
    radius = size * 0.52
    top = cy - size * 0.30
    outline = None if filled else fill
    body = fill if filled else None
    draw.ellipse((cx - size * 0.95, top - radius, cx - size * 0.95 + radius * 2, top + radius),
                 fill=body, outline=outline, width=2)
    draw.ellipse((cx + size * 0.95 - radius * 2, top - radius, cx + size * 0.95, top + radius),
                 fill=body, outline=outline, width=2)
    draw.polygon(
        [(cx - size * 0.95, top), (cx + size * 0.95, top), (cx, cy + size * 0.92)],
        fill=body, outline=outline,
    )


def icon_menu(draw, center, size, fill=TEXT) -> None:
    cx, cy = center
    thickness = max(2, int(size * 0.22))
    for offset in (-size * 0.62, 0, size * 0.62):
        y = cy + offset
        draw.rounded_rectangle(
            (cx - size, y - thickness / 2, cx + size, y + thickness / 2),
            radius=thickness / 2, fill=fill,
        )


def icon_radio(draw, center, size, fill=TEXT) -> None:
    """Yayılan dalgalar — 'benzerlerini çal' için."""
    cx, cy = center
    draw.ellipse((cx - size * 0.24, cy - size * 0.24, cx + size * 0.24, cy + size * 0.24), fill=fill)
    for index in (1, 2):
        radius = size * (0.28 + 0.34 * index)
        draw.arc((cx - radius, cy - radius, cx + radius, cy + radius), start=-55, end=55,
                 fill=fill, width=max(2, int(size * 0.16)))
        draw.arc((cx - radius, cy - radius, cx + radius, cy + radius), start=125, end=235,
                 fill=fill, width=max(2, int(size * 0.16)))


def icon_back(draw, center, size, fill=TEXT) -> None:
    cx, cy = center
    thickness = max(2, int(size * 0.24))
    draw.line([(cx + size * 0.5, cy - size * 0.8), (cx - size * 0.4, cy),
               (cx + size * 0.5, cy + size * 0.8)], fill=fill, width=thickness, joint="curve")


def icon_chevron(draw, center, size, fill=TEXT, direction: str = "down") -> None:
    cx, cy = center
    thickness = max(2, int(size * 0.24))
    if direction == "down":
        points = [(cx - size * 0.8, cy - size * 0.4), (cx, cy + size * 0.45),
                  (cx + size * 0.8, cy - size * 0.4)]
    elif direction == "up":
        points = [(cx - size * 0.8, cy + size * 0.4), (cx, cy - size * 0.45),
                  (cx + size * 0.8, cy + size * 0.4)]
    else:  # right
        points = [(cx - size * 0.4, cy - size * 0.8), (cx + size * 0.45, cy),
                  (cx - size * 0.4, cy + size * 0.8)]
    draw.line(points, fill=fill, width=thickness, joint="curve")


def icon_note(draw, center, size, fill=TEXT_MUTED) -> None:
    cx, cy = center
    stem_x = cx + size * 0.55
    draw.rectangle((stem_x - size * 0.14, cy - size, stem_x + size * 0.14, cy + size * 0.55),
                   fill=fill)
    draw.ellipse((cx - size * 0.85, cy + size * 0.15, stem_x + size * 0.14, cy + size * 1.05),
                 fill=fill)
    draw.polygon([(stem_x - size * 0.14, cy - size), (stem_x + size * 0.9, cy - size * 0.75),
                  (stem_x + size * 0.9, cy - size * 0.35), (stem_x - size * 0.14, cy - size * 0.6)],
                 fill=fill)


def icon_speaker(draw, center, size, fill=TEXT) -> None:
    cx, cy = center
    draw.polygon([(cx - size * 0.75, cy - size * 0.3), (cx - size * 0.35, cy - size * 0.3),
                  (cx + size * 0.05, cy - size * 0.8), (cx + size * 0.05, cy + size * 0.8),
                  (cx - size * 0.35, cy + size * 0.3), (cx - size * 0.75, cy + size * 0.3)],
                 fill=fill)
    for index in (1, 2):
        radius = size * (0.25 + 0.3 * index)
        draw.arc((cx + size * 0.05 - radius, cy - radius, cx + size * 0.05 + radius, cy + radius),
                 start=-60, end=60, fill=fill, width=max(2, int(size * 0.14)))


def new_frame(width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (width, height), BG)
    return image, ImageDraw.Draw(image)


def inside(box: tuple[int, int, int, int], x: int, y: int) -> bool:
    x0, y0, x1, y1 = box
    return x0 <= x <= x1 and y0 <= y <= y1
