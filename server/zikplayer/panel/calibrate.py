"""Rezistif dokunmatik ekran kalibrasyonu.

Ekrana sırayla dört hedef çizer, her birine dokunulduğunda ham ADC değerini
kaydeder ve doğrusal eşlemeyi çözer. Sonuç DATA_DIR/touch-calibration.json
dosyasına yazılır; panel açılışta bunu okur.

    sudo ./install/calibrate-touch.sh
"""

from __future__ import annotations

import json
import logging
import sys
import time

from PIL import Image, ImageDraw

from .. import config
from . import ui
from .framebuffer import FrameBuffer

log = logging.getLogger(__name__)

#: Hedeflerin ekran üzerindeki normalleştirilmiş konumları.
#: Sıra: sol-üst, sağ-üst, sol-alt, sağ-alt.
TARGETS = ((0.10, 0.10), (0.90, 0.10), (0.10, 0.90), (0.90, 0.90))
LABELS = ("sol üst", "sağ üst", "sol alt", "sağ alt")


def _draw_target(framebuffer: FrameBuffer, index: int, message: str) -> None:
    image = Image.new("RGB", (framebuffer.width, framebuffer.height), ui.BG)
    draw = ImageDraw.Draw(image)

    ui.draw_text(draw, (framebuffer.width // 2, framebuffer.height // 2 - 34),
                 "Dokunmatik kalibrasyon", ui.font(18, bold=True), anchor="ma")
    ui.draw_text(draw, (framebuffer.width // 2, framebuffer.height // 2 - 6),
                 message, ui.font(14), fill=ui.TEXT_MUTED, anchor="ma")
    ui.draw_text(draw, (framebuffer.width // 2, framebuffer.height // 2 + 18),
                 f"{index + 1} / {len(TARGETS)}", ui.font(13), fill=ui.ACCENT, anchor="ma")

    nx, ny = TARGETS[index]
    cx = int(nx * (framebuffer.width - 1))
    cy = int(ny * (framebuffer.height - 1))
    draw.line((cx - 18, cy, cx + 18, cy), fill=ui.ACCENT, width=2)
    draw.line((cx, cy - 18, cx, cy + 18), fill=ui.ACCENT, width=2)
    draw.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), outline=ui.ACCENT, width=2)

    framebuffer.show(image, force=True)


def _read_touch(device, timeout: float = 60.0) -> tuple[int, int] | None:
    """Bir dokunma bekler ve bırakma anındaki ham değeri döndürür."""
    import evdev

    raw_x = raw_y = None
    touching = False
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if not _wait_readable(device, min(1.0, max(0.05, remaining))):
            continue
        for event in device.read():
            if event.type == evdev.ecodes.EV_ABS:
                if event.code == evdev.ecodes.ABS_X:
                    raw_x = event.value
                elif event.code == evdev.ecodes.ABS_Y:
                    raw_y = event.value
            elif event.type == evdev.ecodes.EV_KEY and event.code == evdev.ecodes.BTN_TOUCH:
                if event.value == 1:
                    touching = True
                elif event.value == 0 and touching:
                    if raw_x is not None and raw_y is not None:
                        return raw_x, raw_y
                    touching = False
    return None


def _wait_readable(device, timeout: float) -> bool:
    import select

    readable, _, _ = select.select([device.fd], [], [], timeout)
    return bool(readable)


def _solve(samples: list[tuple[int, int]]) -> dict:
    """Dört örnekten doğrusal eşlemeyi çıkarır.

    Ekran 90 derece döndürülmüş olabileceği için hangi ham eksenin hangi ekran
    eksenini sürdüğü de burada tespit edilir.
    """
    raw_x = [sample[0] for sample in samples]
    raw_y = [sample[1] for sample in samples]

    def mean(values: list[int], indices: tuple[int, ...]) -> float:
        return sum(values[i] for i in indices) / len(indices)

    # Hedef indeksleri: 0 sol-üst, 1 sağ-üst, 2 sol-alt, 3 sağ-alt
    left, right = (0, 2), (1, 3)      # ekran x ekseni boyunca ayrışan çiftler
    top, bottom = (0, 1), (2, 3)      # ekran y ekseni boyunca ayrışan çiftler

    spread_x_along_screen_x = abs(mean(raw_x, right) - mean(raw_x, left))
    spread_x_along_screen_y = abs(mean(raw_x, bottom) - mean(raw_x, top))
    swap_xy = spread_x_along_screen_y > spread_x_along_screen_x

    def extrapolate(values: list[int], low: tuple[int, ...],
                    high: tuple[int, ...]) -> tuple[int, int, bool]:
        """0.1 ve 0.9 konumlarındaki ölçümlerden 0.0-1.0 aralığını türetir."""
        at_low = mean(values, low)
        at_high = mean(values, high)
        slope = (at_high - at_low) / 0.8
        start = at_low - 0.1 * slope
        end = at_low + 0.9 * slope
        return int(round(min(start, end))), int(round(max(start, end))), slope < 0

    if swap_xy:
        # raw_x ekran y'sini, raw_y ekran x'ini sürüyor.
        x_min, x_max, invert_y = extrapolate(raw_x, top, bottom)
        y_min, y_max, invert_x = extrapolate(raw_y, left, right)
    else:
        x_min, x_max, invert_x = extrapolate(raw_x, left, right)
        y_min, y_max, invert_y = extrapolate(raw_y, top, bottom)

    return {
        "x_min": x_min, "x_max": x_max,
        "y_min": y_min, "y_max": y_max,
        "swap_xy": swap_xy, "invert_x": invert_x, "invert_y": invert_y,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        import evdev
    except ImportError:
        print("python3-evdev kurulu değil.", file=sys.stderr)
        return 1

    from .input import InputHub, _Calibration

    framebuffer = FrameBuffer(config.PANEL_FB, config.PANEL_WIDTH, config.PANEL_HEIGHT)
    hub = InputHub(framebuffer.width, framebuffer.height)

    try:
        device = hub._find_touch_device()
    except Exception as exc:
        print(f"Dokunmatik aygıt aranırken hata: {exc}", file=sys.stderr)
        return 1

    if device is None:
        print("Dokunmatik aygıt bulunamadı. Ekran overlay'i yüklü mü?", file=sys.stderr)
        print("Kontrol: cat /proc/bus/input/devices", file=sys.stderr)
        return 1

    print(f"Aygıt: {device.name} ({device.path})")
    print("Ekrandaki hedeflere sırayla bas. Mümkünse ince uçlu bir kalem kullan.\n")

    samples: list[tuple[int, int]] = []
    with framebuffer:
        try:
            for index in range(len(TARGETS)):
                _draw_target(framebuffer, index, f"{LABELS[index]} hedefe bas")
                sample = _read_touch(device)
                if sample is None:
                    print("Zaman aşımı — kalibrasyon iptal edildi.", file=sys.stderr)
                    return 1
                samples.append(sample)
                print(f"  {LABELS[index]:9s} -> ham {sample}")
                time.sleep(0.45)   # aynı dokunuşun sonraki hedefe sayılmaması için
        finally:
            device.close()

        calibration = _solve(samples)
        path = _Calibration.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(calibration, indent=2) + "\n")

        image = Image.new("RGB", (framebuffer.width, framebuffer.height), ui.BG)
        draw = ImageDraw.Draw(image)
        ui.draw_text(draw, (framebuffer.width // 2, framebuffer.height // 2 - 14),
                     "Kalibrasyon kaydedildi", ui.font(18, bold=True), anchor="ma")
        ui.draw_text(draw, (framebuffer.width // 2, framebuffer.height // 2 + 14),
                     "panel yeniden başlatılıyor…", ui.font(13), fill=ui.TEXT_MUTED, anchor="ma")
        framebuffer.show(image, force=True)
        time.sleep(1.5)

    print(f"\nKaydedildi: {path}")
    print(json.dumps(calibration, indent=2))
    print("\nPaneli yeniden başlat:  sudo systemctl restart zikplayer-panel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
