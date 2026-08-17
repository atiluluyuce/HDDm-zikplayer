"""Panel girdileri: rezistif dokunmatik ekran ve EC11 döner encoder.

İki kaynak da aynı olay kuyruğuna yazar, böylece arayüz hangi girdiyle
sürüldüğünü bilmek zorunda kalmaz. Biri çalışmasa (encoder takılı değilse veya
dokunmatik kalibre edilmemişse) diğeriyle cihaz tam olarak kullanılabilir.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import config

log = logging.getLogger(__name__)

TAP = "tap"
ROTATE = "rotate"
PRESS = "press"
LONG_PRESS = "long_press"


@dataclass
class Event:
    kind: str
    x: int = 0
    y: int = 0
    delta: int = 0


class InputHub:
    """Tüm girdi kaynaklarını tek kuyrukta toplar."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.queue: queue.Queue[Event] = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._sources: list[threading.Thread] = []
        self._encoder = None
        self._button = None

    def emit(self, event: Event) -> None:
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            log.debug("girdi kuyruğu doldu, olay atlandı")

    def get(self, timeout: float | None = None) -> Event | None:
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def start(self) -> None:
        self._start_touch()
        if config.ENCODER_ENABLED:
            self._start_encoder()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._sources:
            thread.join(timeout=1.0)
        for device in (self._encoder, self._button):
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass

    # --- Dokunmatik ----------------------------------------------------------

    def _start_touch(self) -> None:
        try:
            import evdev  # noqa: F401
        except ImportError:
            log.warning("python3-evdev yok; dokunmatik devre dışı (encoder kullanılabilir)")
            return

        thread = threading.Thread(target=self._touch_loop, name="panel-touch", daemon=True)
        thread.start()
        self._sources.append(thread)

    def _find_touch_device(self):
        import evdev

        if config.PANEL_TOUCH_DEVICE:
            return evdev.InputDevice(config.PANEL_TOUCH_DEVICE)

        for path in evdev.list_devices():
            try:
                device = evdev.InputDevice(path)
            except OSError:
                continue
            capabilities = device.capabilities()
            abs_codes = {code for code, _ in capabilities.get(evdev.ecodes.EV_ABS, [])}
            key_codes = set(capabilities.get(evdev.ecodes.EV_KEY, []))
            if {evdev.ecodes.ABS_X, evdev.ecodes.ABS_Y} <= abs_codes and \
                    evdev.ecodes.BTN_TOUCH in key_codes:
                log.info("dokunmatik aygıt: %s (%s)", device.path, device.name)
                return device
            device.close()
        return None

    def _touch_loop(self) -> None:
        import evdev

        while not self._stop.is_set():
            device = None
            try:
                device = self._find_touch_device()
                if device is None:
                    log.warning("dokunmatik aygıt bulunamadı; 10 sn sonra tekrar denenecek")
                    self._stop.wait(10.0)
                    continue

                calibration = _Calibration.load(device, self.width, self.height)
                raw_x = raw_y = None
                touching = False
                pressed_at = 0.0

                for event in device.read_loop():
                    if self._stop.is_set():
                        break

                    if event.type == evdev.ecodes.EV_ABS:
                        if event.code == evdev.ecodes.ABS_X:
                            raw_x = event.value
                        elif event.code == evdev.ecodes.ABS_Y:
                            raw_y = event.value

                    elif event.type == evdev.ecodes.EV_KEY and event.code == evdev.ecodes.BTN_TOUCH:
                        if event.value == 1:
                            touching = True
                            pressed_at = time.monotonic()
                        elif event.value == 0 and touching:
                            touching = False
                            # Dokunma bırakılınca tetikle: rezistif ekranda basma
                            # anındaki değer oturmamış oluyor, bırakma anı daha isabetli.
                            held = time.monotonic() - pressed_at
                            if raw_x is not None and raw_y is not None and 0.02 < held < 2.0:
                                x, y = calibration.to_screen(raw_x, raw_y)
                                self.emit(Event(TAP, x=x, y=y))
                            raw_x = raw_y = None

            except OSError as exc:
                log.warning("dokunmatik okuma hatası: %s", exc)
                self._stop.wait(3.0)
            except Exception:
                log.exception("dokunmatik döngüsünde beklenmeyen hata")
                self._stop.wait(3.0)
            finally:
                if device is not None:
                    try:
                        device.close()
                    except Exception:
                        pass

    # --- EC11 encoder --------------------------------------------------------

    def _start_encoder(self) -> None:
        try:
            from gpiozero import Button, RotaryEncoder
        except ImportError:
            log.warning("gpiozero yok; EC11 encoder devre dışı")
            return

        try:
            # max_steps=0 -> sayaç sınırsız; biz sadece yön olaylarını kullanıyoruz.
            self._encoder = RotaryEncoder(
                config.ENCODER_PIN_A, config.ENCODER_PIN_B, max_steps=0, bounce_time=0.002,
            )
            self._button = Button(
                config.ENCODER_PIN_SW, pull_up=True, bounce_time=0.05,
                hold_time=config.ENCODER_LONG_PRESS,
            )
        except Exception as exc:
            # Pin meşgulse veya lgpio/pigpio arka ucu yoksa panel yine de çalışsın.
            log.warning("EC11 encoder başlatılamadı (%s); dokunmatikle devam", exc)
            self._encoder = self._button = None
            return

        step = 1 if not config.ENCODER_REVERSED else -1
        self._encoder.when_rotated_clockwise = lambda: self.emit(Event(ROTATE, delta=step))
        self._encoder.when_rotated_counter_clockwise = lambda: self.emit(Event(ROTATE, delta=-step))

        held = {"fired": False}

        def on_held() -> None:
            held["fired"] = True
            self.emit(Event(LONG_PRESS))

        def on_released() -> None:
            if held["fired"]:
                held["fired"] = False
            else:
                self.emit(Event(PRESS))

        self._button.when_held = on_held
        self._button.when_released = on_released

        log.info(
            "EC11 encoder hazır (CLK=GPIO%d, DT=GPIO%d, SW=GPIO%d)",
            config.ENCODER_PIN_A, config.ENCODER_PIN_B, config.ENCODER_PIN_SW,
        )


class _Calibration:
    """Ham dokunmatik ADC değerlerini ekran koordinatına çevirir."""

    def __init__(self, width: int, height: int, data: dict[str, Any]) -> None:
        self.width = width
        self.height = height
        self.x_min = int(data.get("x_min", 0))
        self.x_max = int(data.get("x_max", 4095))
        self.y_min = int(data.get("y_min", 0))
        self.y_max = int(data.get("y_max", 4095))
        self.swap_xy = bool(data.get("swap_xy", True))
        self.invert_x = bool(data.get("invert_x", False))
        self.invert_y = bool(data.get("invert_y", True))

    @classmethod
    def path(cls) -> Path:
        return config.DATA_DIR / "touch-calibration.json"

    @classmethod
    def load(cls, device, width: int, height: int) -> "_Calibration":
        data: dict[str, Any] = {}

        # Sürücünün bildirdiği ADC aralığı makul bir başlangıç noktası.
        try:
            import evdev

            absinfo = dict(device.capabilities().get(evdev.ecodes.EV_ABS, []))
            x_info = absinfo.get(evdev.ecodes.ABS_X)
            y_info = absinfo.get(evdev.ecodes.ABS_Y)
            if x_info:
                data["x_min"], data["x_max"] = x_info.min, x_info.max
            if y_info:
                data["y_min"], data["y_max"] = y_info.min, y_info.max
        except Exception:
            pass

        stored = cls.path()
        try:
            data.update(json.loads(stored.read_text()))
            log.info("dokunmatik kalibrasyonu yüklendi: %s", stored)
        except (OSError, json.JSONDecodeError):
            log.info("kalibrasyon dosyası yok, sürücü değerleri kullanılıyor "
                     "(install/calibrate-touch.sh ile ayarlanabilir)")

        return cls(width, height, data)

    def to_screen(self, raw_x: int, raw_y: int) -> tuple[int, int]:
        def normalise(value: int, low: int, high: int) -> float:
            if high == low:
                return 0.0
            return max(0.0, min(1.0, (value - low) / (high - low)))

        nx = normalise(raw_x, self.x_min, self.x_max)
        ny = normalise(raw_y, self.y_min, self.y_max)

        if self.swap_xy:
            nx, ny = ny, nx
        if self.invert_x:
            nx = 1.0 - nx
        if self.invert_y:
            ny = 1.0 - ny

        return int(nx * (self.width - 1)), int(ny * (self.height - 1))
