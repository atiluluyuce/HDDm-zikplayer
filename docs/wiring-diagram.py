"""wiring.svg üretir — çalıştır:  python3 docs/wiring-diagram.py

Bu dosya wiring.svg'nin kaynağıdır — Pi 3 40-pin header, PCM5102A DAC ve EC11 encoder.

Koordinatlar burada hesaplanıyor: 40 pin elle yazılınca kaçınılmaz olarak kayıyor.
Yerleşim üstten alta akar; her bloğun yüksekliği içeriğinden türetilir, böylece
metin uzadığında bir sonraki blok kendiliğinden aşağı iner.
"""
from pathlib import Path
import xml.etree.ElementTree as ET

OUT = Path("/home/user/HDDm-zikplayer/docs/wiring.svg")

W = 1200
M = 48                       # kenar boşluğu

BG, INK, MUTED, RULE, CARD = "#fbfbf9", "#111827", "#6b7280", "#d4d4d8", "#ffffff"
DAC, DAC_BG = "#b45309", "#fef3c7"
ENC, ENC_BG = "#0f766e", "#ccfbf1"
LCD, LCD_BG = "#6d28d9", "#f1ecfe"
V5, V33, GND = "#dc2626", "#ea580c", "#374151"
WARN, WARN_BG = "#b91c1c", "#fef2f2"
FREE, FREE_BG = "#15803d", "#dcfce7"

PINS = {
    1: "3V3", 2: "5V", 3: "GPIO2 / SDA1", 4: "5V", 5: "GPIO3 / SCL1", 6: "GND",
    7: "GPIO4", 8: "GPIO14 / TXD", 9: "GND", 10: "GPIO15 / RXD",
    11: "GPIO17", 12: "GPIO18 / PCM_CLK", 13: "GPIO27", 14: "GND",
    15: "GPIO22", 16: "GPIO23", 17: "3V3", 18: "GPIO24",
    19: "GPIO10 / MOSI", 20: "GND", 21: "GPIO9 / MISO", 22: "GPIO25",
    23: "GPIO11 / SCLK", 24: "GPIO8 / CE0", 25: "GND", 26: "GPIO7 / CE1",
    27: "GPIO0 / ID_SD", 28: "GPIO1 / ID_SC", 29: "GPIO5", 30: "GND",
    31: "GPIO6", 32: "GPIO12", 33: "GPIO13", 34: "GND",
    35: "GPIO19 / PCM_FS", 36: "GPIO16", 37: "GPIO26", 38: "GPIO20 / PCM_DIN",
    39: "GND", 40: "GPIO21 / PCM_DOUT",
}
DAC_PINS = {4: "VIN", 12: "BCK", 34: "GND", 35: "LCK", 40: "DIN"}
ENC_PINS = {29: "CLK", 31: "DT", 33: "SW", 39: "GND"}
LCD_PINS = {11, 18, 19, 21, 22, 23, 24, 26}
BLOCKED = {4, 12}

# --- Pin haritası yerleşimi --------------------------------------------------
ROW_H, ROW0_Y = 32, 196
SPINE_X0, SPINE_X1 = 548, 652
PAD_ODD_X, PAD_EVEN_X = 574, 626
CHIP_W, CHIP_H = 38, 22
LEFT_CHIP_X, LEFT_NAME_END = 500, 488
RIGHT_CHIP_X, RIGHT_NAME_X = 662, 708
LEFT_ROLE_END = LEFT_NAME_END - 132          # 356
RIGHT_ROLE_X = RIGHT_NAME_X + 136            # 844
TINT_X0 = 268
BRACKET_X = 236                              # rol etiketleriyle çakışmaz

parts: list[str] = []
add = parts.append
FONTS = {
    "sans": "ui-sans-serif,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif",
    "mono": "ui-monospace,'SF Mono',Menlo,Consolas,monospace",
}


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=13, fill=INK, anchor="start", weight="400", family="sans"):
    add(f'<text x="{x}" y="{y}" font-family="{FONTS[family]}" font-size="{size}" '
        f'fill="{fill}" text-anchor="{anchor}" font-weight="{weight}">{esc(s)}</text>')


def rect(x, y, w, h, fill=CARD, stroke=None, rx=6, sw=1, opacity=None):
    a = f'x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}"'
    if stroke:
        a += f' stroke="{stroke}" stroke-width="{sw}"'
    if opacity is not None:
        a += f' opacity="{opacity}"'
    add(f'<rect {a}/>')


def line(x1, y1, x2, y2, stroke=RULE, sw=1):
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{sw}"/>')


def row_y(i):
    return ROW0_Y + i * ROW_H


def pin_style(pin):
    if pin in DAC_PINS:
        return DAC_BG, DAC, DAC, f"DAC · {DAC_PINS[pin]}"
    if pin in ENC_PINS:
        return ENC_BG, ENC, ENC, f"EC11 · {ENC_PINS[pin]}"
    if pin in LCD_PINS:
        return LCD_BG, LCD, LCD, "LCD"
    name = PINS[pin]
    if name == "5V":
        return "#fee2e2", V5, V5, ""
    if name == "3V3":
        return "#ffedd5", V33, V33, ""
    if name == "GND":
        return "#e5e7eb", GND, GND, ""
    return "#f4f4f5", "#a1a1aa", MUTED, ""


# =============================================================================
body: list[str] = []          # yükseklik sonra hesaplanacağı için gövde ayrı tutulur

# --- Başlık ------------------------------------------------------------------
text(M, 62, "hddmusicplayer — kablolama şeması", 30, INK, weight="700")
text(M, 92, "Raspberry Pi 3 Model B  ·  PCM5102A I2S DAC  ·  EC11 döner encoder  ·  "
            "Waveshare 3.5\" RPi LCD (A)", 15, MUTED)
line(M, 112, W - M, 112, RULE, 2)

# --- Header ------------------------------------------------------------------
spine_top, spine_bottom = row_y(0) - 22, row_y(19) + 22
text((SPINE_X0 + SPINE_X1) // 2, spine_top - 34, "J8  ·  40 pin GPIO header",
     13, INK, anchor="middle", weight="700")
text((SPINE_X0 + SPINE_X1) // 2, spine_top - 16,
     "şemada 90° döndürülmüş: yukarıdan aşağı pin 1 → 40", 11.5, MUTED, anchor="middle")

# Satır zeminleri
for i in range(20):
    y, odd = row_y(i), 2 * i + 1
    tint = LCD_BG if odd <= 26 else ("#f4f4f5" if i % 2 == 0 else BG)
    rect(TINT_X0, y - 15, SPINE_X0 - TINT_X0 - 4, 30, tint, None, rx=4, opacity=0.75)
    rect(SPINE_X1 + 4, y - 15, (W - M) - (SPINE_X1 + 4), 30, tint, None, rx=4, opacity=0.75)

rect(SPINE_X0, spine_top, SPINE_X1 - SPINE_X0, spine_bottom - spine_top, "#1f2937", None, rx=8)

# Ekran ayak izi parantezleri
br_bottom = row_y(12) + 16
rect(BRACKET_X, spine_top, 14, br_bottom - spine_top, LCD_BG, LCD, rx=6)
cy = (spine_top + br_bottom) // 2
add(f'<text x="{BRACKET_X - 12}" y="{cy}" font-family="{FONTS["sans"]}" font-size="13" '
    f'fill="{LCD}" font-weight="700" text-anchor="middle" '
    f'transform="rotate(-90 {BRACKET_X - 12} {cy})">EKRAN KAPLIYOR  ·  pin 1–26</text>')

free_top = row_y(13) - 16
rect(BRACKET_X, free_top, 14, spine_bottom - free_top, FREE_BG, FREE, rx=6)
cy = (free_top + spine_bottom) // 2
add(f'<text x="{BRACKET_X - 12}" y="{cy}" font-family="{FONTS["sans"]}" font-size="12.5" '
    f'fill="{FREE}" font-weight="700" text-anchor="middle" '
    f'transform="rotate(-90 {BRACKET_X - 12} {cy})">27–40 AÇIKTA</text>')

for i in range(20):
    odd, even, y = 2 * i + 1, 2 * i + 2, row_y(i)
    for pin, side in ((odd, "left"), (even, "right")):
        fill, stroke, ink, role = pin_style(pin)
        pad_x = PAD_ODD_X if side == "left" else PAD_EVEN_X
        # Pin 1 gerçek konnektörlerdeki gibi kare pedle işaretlenir.
        if pin == 1:
            rect(pad_x - 7, y - 7, 14, 14, fill, stroke, rx=2, sw=2)
        else:
            add(f'<circle cx="{pad_x}" cy="{y}" r="7.5" fill="{fill}" stroke="{stroke}" '
                f'stroke-width="2"/>')

        chip_x = LEFT_CHIP_X if side == "left" else RIGHT_CHIP_X
        if side == "left":
            line(pad_x - 8, y, chip_x + CHIP_W, y, stroke, 2)
        else:
            line(pad_x + 8, y, chip_x, y, stroke, 2)

        rect(chip_x, y - CHIP_H // 2, CHIP_W, CHIP_H, fill, stroke, rx=5, sw=1.5)
        text(chip_x + CHIP_W / 2, y + 5, str(pin), 13, ink, anchor="middle",
             weight="700", family="mono")

        bold = "600" if role else "400"
        if side == "left":
            text(LEFT_NAME_END, y + 5, PINS[pin], 13, INK if role else MUTED, "end", bold)
            if role:
                text(LEFT_ROLE_END, y + 5, role, 11.5, ink, "end", "700")
            if pin in BLOCKED:
                text(LEFT_ROLE_END - 104, y + 5, "⚠ ekranın altında", 11, WARN, "end", "700")
        else:
            text(RIGHT_NAME_X, y + 5, PINS[pin], 13, INK if role else MUTED, "start", bold)
            if role:
                text(RIGHT_ROLE_X, y + 5, role, 11.5, ink, "start", "700")
            if pin in BLOCKED:
                text(RIGHT_ROLE_X + 100, y + 5, "⚠ ekranın altında", 11, WARN, "start", "700")

# --- Renk kodu + kart yönü (yan yana) ---------------------------------------
band_y = spine_bottom + 40
band_h = 200
legend_w = 560
rect(M, band_y, legend_w, band_h, CARD, RULE, rx=10)
text(M + 22, band_y + 34, "Renk kodu", 15, INK, weight="700")
legend = [
    (DAC, DAC_BG, "PCM5102A DAC"), (V5, "#fee2e2", "5V"),
    (ENC, ENC_BG, "EC11 encoder"), (V33, "#ffedd5", "3.3V"),
    (LCD, LCD_BG, "ekranın kullandığı sinyal"), (GND, "#e5e7eb", "GND"),
]
for idx, (stroke, fill, label) in enumerate(legend):
    col, row = idx % 2, idx // 2
    lx = M + 24 + col * 268
    ly = band_y + 68 + row * 34
    add(f'<circle cx="{lx + 8}" cy="{ly}" r="7.5" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="2"/>')
    text(lx + 26, ly + 5, label, 13, INK)
text(M + 24, band_y + band_h - 22,
     "⚠ = pin fiziksel olarak ekranın altında kalıyor", 11.5, WARN, weight="700")

ox = M + legend_w + 24
ow = (W - M) - ox
rect(ox, band_y, ow, band_h, CARD, RULE, rx=10)
text(ox + 22, band_y + 34, "Kartı nasıl tutuyoruz", 15, INK, weight="700")
bx, by, bw, bh = ox + 24, band_y + 50, ow - 48, 96
rect(bx, by, bw, bh, "#eef2f4", "#9ca3af", rx=8)
rect(bx + 44, by + 10, 190, 15, "#1f2937", None, rx=3)
for i in range(20):
    cx = bx + 50 + i * 9.5
    add(f'<circle cx="{cx}" cy="{by + 14}" r="2" fill="#facc15"/>')
    add(f'<circle cx="{cx}" cy="{by + 21}" r="2" fill="#facc15"/>')
add(f'<path d="M {bx+46} {by+40} L {bx+50} {by+27}" stroke="{DAC}" stroke-width="1.6" fill="none"/>')
text(bx + 40, by + 52, "pin 1/2 bu uçta", 10.5, DAC, weight="700")
rect(bx + bw - 30, by + 30, 22, 24, "#9ca3af", None, rx=2)
rect(bx + bw - 30, by + 60, 22, 24, "#9ca3af", None, rx=2)
text(bx + bw - 38, by + 46, "USB", 9.5, "#374151", anchor="end")
text(bx + bw - 38, by + 76, "LAN", 9.5, "#374151", anchor="end")
rect(bx + 10, by + bh - 26, 26, 11, "#9ca3af", None, rx=2)
text(bx + 10, by + bh - 30, "microSD", 9.5, "#374151")
text(ox + 24, band_y + 172, "GPIO header üst kenarda, USB/Ethernet sağda.", 12, MUTED)
text(ox + 24, band_y + 190, "Çift pinler (2,4,…) kart kenarındaki sıra.", 12, MUTED)

# --- Modül panelleri ---------------------------------------------------------
DAC_ROWS = [
    ("VIN", "4", "5V", True), ("GND", "34", "GND (serbest)", False),
    ("BCK", "12", "GPIO18", True), ("LCK", "35", "GPIO19 (serbest)", False),
    ("DIN", "40", "GPIO21 (serbest)", False),
    ("SCK", None, "modülün GND padine bağla", False),
]
ENC_ROWS = [
    ("CLK (A)", "29", "GPIO5", False), ("DT  (B)", "31", "GPIO6", False),
    ("SW", "33", "GPIO13", False), ("GND (C)", "39", "GND", False),
]

panel_y = band_y + band_h + 34
panel_w = (W - 2 * M - 24) // 2
panel_h = 78 + max(len(DAC_ROWS), len(ENC_ROWS)) * 30 + 16 + 58


def module_panel(x, title, subtitle, accent, accent_bg, rows, footer):
    rect(x, panel_y, panel_w, panel_h, CARD, RULE, rx=10)
    rect(x, panel_y, panel_w, 6, accent, None, rx=3)
    text(x + 22, panel_y + 38, title, 18, INK, weight="700")
    text(x + 22, panel_y + 60, subtitle, 12.5, MUTED)

    mod_x, mod_y, mod_w = x + 22, panel_y + 78, 118
    mod_h = len(rows) * 30 + 16
    rect(mod_x, mod_y, mod_w, mod_h, accent_bg, accent, rx=8, sw=1.5)

    for i, (module_pin, pi_pin, note, blocked) in enumerate(rows):
        ry = mod_y + 24 + i * 30
        text(mod_x + 14, ry + 5, module_pin, 13, INK, weight="700", family="mono")
        add(f'<circle cx="{mod_x + mod_w}" cy="{ry}" r="5" fill="{CARD}" stroke="{accent}" '
            f'stroke-width="2"/>')
        target_x = mod_x + mod_w + 100
        add(f'<path d="M {mod_x + mod_w + 5} {ry} L {target_x - 6} {ry}" stroke="{accent}" '
            f'stroke-width="2.5" fill="none" stroke-linecap="round"/>')
        if pi_pin:
            rect(target_x, ry - 12, 44, 24, accent_bg, accent, rx=5, sw=1.5)
            text(target_x + 22, ry + 5, pi_pin, 13, accent, "middle", "700", "mono")
            text(target_x + 58, ry + 5, note, 12, MUTED)
            if blocked:
                text(target_x + 58 + len(note) * 6.6 + 10, ry + 5, "⚠ ekran altı", 11,
                     WARN, "start", "700")
        else:
            text(target_x, ry + 5, note, 12, accent, "start", "700")

    text(x + 22, panel_y + panel_h - 24, footer, 11.5, MUTED)


module_panel(M, "PCM5102A", "I2S DAC — bağlanacağı Pi pinleri", DAC, DAC_BG, DAC_ROWS,
             "Modül jumper'ları:   FLT = L    DEMP = L    XSMT = H    FMT = L")
module_panel(M + panel_w + 24, "EC11 encoder", "Döner kontrol — hepsi serbest pin",
             ENC, ENC_BG, ENC_ROWS,
             "KY-040 kartıysa + ucunu bağlama; Pi'nin dahili pull-up'ları yeterli.")

# --- Uyarılar ----------------------------------------------------------------
warn_rows = [
    ("SCK mutlaka GND'ye",
     "Pi master clock (MCLK) üretmez. SCK şaseye çekilmezse PCM5102A dahili PLL'ini "
     "devreye almaz: ya hiç ses gelmez ya cızırtı olur."),
    ("XSMT jumper'ı H konumunda",
     "L konumundaysa yonga kalıcı olarak sessizde kalır."),
    ("USB diski ayrı besle",
     "Pi 3'ün tüm USB portları toplam ~1.2 A verir; 2.5\" disk kalkışta 0.9 A çeker. "
     "Beslemeli hub veya Y-kablo kullan, 5V/3A kaynak şart."),
]
warn_y = panel_y + panel_h + 32
warn_h = 52 + len(warn_rows) * 28 + 10
rect(M, warn_y, W - 2 * M, warn_h, WARN_BG, WARN, rx=10, sw=1.5)
text(M + 22, warn_y + 34, "⚠  Atlanırsa çalışmayan üç şey", 16, WARN, weight="700")
for i, (head, bodytext) in enumerate(warn_rows):
    ry = warn_y + 62 + i * 28
    text(M + 22, ry, head + " —", 13, WARN, weight="700")
    text(M + 22 + len(head) * 7.5 + 18, ry, bodytext, 12.5, "#7f1d1d")

# --- Ekran çakışması ---------------------------------------------------------
sol_y = warn_y + warn_h + 30
sol_h = 122
rect(M, sol_y, W - 2 * M, sol_h, CARD, LCD, rx=10, sw=1.5)
text(M + 22, sol_y + 32, "Ekran 26 pinlik: pin 1–26 kapanır, 27–40 açıkta kalır. "
                         "DAC'ın yalnızca VIN (4) ve BCK (12) uçları altta kalıyor.",
     13.5, INK, weight="600")
text(M + 22, sol_y + 66, "Çözüm A  ·  2×20 stacking header:", 13, LCD, weight="700")
text(M + 262, sol_y + 66, "ekranı ~11 mm yükseltir, 40 pinin tamamı altta erişilebilir "
                          "kalır. Lehim gerekmez.", 12.5, MUTED)
text(M + 22, sol_y + 94, "Çözüm B  ·  lehimle:", 13, LCD, weight="700")
text(M + 262, sol_y + 94, "pin 12'ye kartın altından kablo lehimle; VIN'i header'a hiç "
                          "dokunmadan mevcut 5V besleme kablosundan ayır.", 12.5, MUTED)

H = sol_y + sol_h + 40

# =============================================================================
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
       f'height="{H}">\n<rect width="{W}" height="{H}" fill="{BG}"/>\n'
       + "\n".join(parts) + "\n</svg>\n")
OUT.write_text(svg, encoding="utf-8")

# --- Doğrulama ---------------------------------------------------------------
root = ET.fromstring(svg)
print(f"yazıldı: {OUT}  ({len(svg)/1024:.1f} KB, {W}×{H}, {sum(1 for _ in root.iter())} öğe)")

assert set(PINS) == set(range(1, 41))
assert not (set(DAC_PINS) & set(ENC_PINS)), "DAC ve encoder aynı pini istiyor"
assert all(p > 26 for p in ENC_PINS), "encoder pini ekranın altında"
assert BLOCKED == {p for p in DAC_PINS if p <= 26}
assert PINS[4] == "5V" and PINS[34] == "GND" and PINS[39] == "GND"
assert PINS[12].startswith("GPIO18") and PINS[35].startswith("GPIO19") \
    and PINS[40].startswith("GPIO21"), "I2S pinleri yanlış"
# Şemadaki pin numaralarıyla modül panellerindeki numaralar tutmalı
panel_dac = {int(p) for _, p, _, _ in DAC_ROWS if p}
panel_enc = {int(p) for _, p, _, _ in ENC_ROWS if p}
assert panel_dac == set(DAC_PINS), f"DAC paneli tutarsız: {panel_dac} vs {set(DAC_PINS)}"
assert panel_enc == set(ENC_PINS), f"EC11 paneli tutarsız: {panel_enc} vs {set(ENC_PINS)}"
# Blokların dikey olarak çakışmadığını doğrula
blocks = [("harita", spine_top, spine_bottom), ("bant", band_y, band_y + band_h),
          ("paneller", panel_y, panel_y + panel_h), ("uyarı", warn_y, warn_y + warn_h),
          ("çözüm", sol_y, sol_y + sol_h)]
for (n1, _, e1), (n2, s2, _) in zip(blocks, blocks[1:]):
    assert e1 < s2, f"{n1} ve {n2} çakışıyor ({e1} >= {s2})"
assert blocks[-1][2] < H, "son blok tuvalden taşıyor"
print("pin ataması, panel tutarlılığı ve blok yerleşimi doğrulandı")
