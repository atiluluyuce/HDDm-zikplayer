"""Doğrudan Linux framebuffer'a çizim.

Waveshare 3.5" ekran SPI üzerinden sürüldüğü için X sunucusu + tarayıcı yükü
kaldırmaz. Bunun yerine Pillow ile RAM'de bir kare üretip /dev/fb1'e yazıyoruz.

İki optimizasyon SPI hattını rahat bırakıyor:
  * kare RGB565'e numpy ile çevriliyor (saf Python'da ~100 ms, numpy ile ~2 ms)
  * yalnızca bir önceki kareye göre değişen satır aralığı yazılıyor
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None
    log.warning("numpy yok; kare dönüşümü yavaş yolu kullanacak")

from PIL import Image


def _read_sysfs(device: str, name: str) -> str | None:
    path = Path(f"/sys/class/graphics/{device}/{name}")
    try:
        return path.read_text().strip()
    except OSError:
        return None


class FrameBuffer:
    """Tek bir framebuffer aygıtına tampon yazan basit sarmalayıcı."""

    def __init__(self, device_path: str, width: int, height: int) -> None:
        self.path = Path(device_path)
        self.device = self.path.name

        detected = self._detect()
        self.width = detected[0] or width
        self.height = detected[1] or height
        self.bpp = detected[2]
        self.stride = detected[3] or self.width * (self.bpp // 8)

        self._file = None
        self._previous: "np.ndarray | None" = None

        log.info(
            "framebuffer %s: %dx%d @ %d bpp (stride %d)",
            self.path, self.width, self.height, self.bpp, self.stride,
        )

    def _detect(self) -> tuple[int | None, int | None, int, int | None]:
        size = _read_sysfs(self.device, "virtual_size")
        width = height = None
        if size and "," in size:
            try:
                width, height = (int(part) for part in size.split(",")[:2])
            except ValueError:
                width = height = None

        bpp_text = _read_sysfs(self.device, "bits_per_pixel")
        try:
            bpp = int(bpp_text) if bpp_text else 16
        except ValueError:
            bpp = 16
        if bpp not in (16, 32):
            log.warning("beklenmeyen renk derinliği %s; 16 bit varsayılıyor", bpp)
            bpp = 16

        stride_text = _read_sysfs(self.device, "stride")
        try:
            stride = int(stride_text) if stride_text else None
        except ValueError:
            stride = None

        return width, height, bpp, stride

    def open(self) -> None:
        self._file = open(self.path, "r+b", buffering=0)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> "FrameBuffer":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # --- Dönüşüm -------------------------------------------------------------

    def _to_bytes(self, image: Image.Image) -> "np.ndarray":
        """PIL görüntüsünü framebuffer'ın piksel biçimine çevirir."""
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height))
        if image.mode != "RGB":
            image = image.convert("RGB")

        if np is None:
            return self._to_bytes_slow(image)

        pixels = np.asarray(image, dtype=np.uint16)
        if self.bpp == 16:
            red = (pixels[:, :, 0] >> 3).astype(np.uint16) << 11
            green = (pixels[:, :, 1] >> 2).astype(np.uint16) << 5
            blue = (pixels[:, :, 2] >> 3).astype(np.uint16)
            return (red | green | blue).astype("<u2")

        # 32 bpp: BGRA sırası (Raspberry Pi'nin fb0 düzeni)
        rgb = np.asarray(image, dtype=np.uint8)
        frame = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        frame[:, :, 0] = rgb[:, :, 2]
        frame[:, :, 1] = rgb[:, :, 1]
        frame[:, :, 2] = rgb[:, :, 0]
        frame[:, :, 3] = 255
        return frame

    def _to_bytes_slow(self, image: Image.Image):
        """numpy yoksa kullanılan yedek yol. Yavaş ama çalışır."""
        data = image.tobytes()
        out = bytearray(self.width * self.height * (self.bpp // 8))
        if self.bpp == 16:
            for i in range(self.width * self.height):
                r, g, b = data[i * 3], data[i * 3 + 1], data[i * 3 + 2]
                value = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                out[i * 2] = value & 0xFF
                out[i * 2 + 1] = value >> 8
        else:
            for i in range(self.width * self.height):
                r, g, b = data[i * 3], data[i * 3 + 1], data[i * 3 + 2]
                out[i * 4:i * 4 + 4] = bytes((b, g, r, 255))
        return out

    # --- Yazma ---------------------------------------------------------------

    def show(self, image: Image.Image, force: bool = False) -> None:
        """Kareyi ekrana basar; sadece değişen satır aralığını yazar."""
        if self._file is None:
            self.open()

        frame = self._to_bytes(image)
        bytes_per_line = self.width * (self.bpp // 8)

        first_row, last_row = 0, self.height - 1
        if not force and np is not None and self._previous is not None \
                and getattr(self._previous, "shape", None) == getattr(frame, "shape", None):
            # Satır bazında karşılaştır: değişen ilk ve son satırı bul.
            axes = tuple(range(1, frame.ndim))
            changed = np.any(frame != self._previous, axis=axes)
            indices = np.nonzero(changed)[0]
            if indices.size == 0:
                return
            first_row, last_row = int(indices[0]), int(indices[-1])

        if np is not None and hasattr(frame, "tobytes"):
            payload = frame[first_row:last_row + 1].tobytes()
        else:
            payload = bytes(frame[first_row * bytes_per_line:(last_row + 1) * bytes_per_line])

        try:
            if self.stride == bytes_per_line:
                self._file.seek(first_row * self.stride)
                self._file.write(payload)
            else:
                # Satır uzunluğu ile stride farklıysa satır satır yaz.
                for offset, row in enumerate(range(first_row, last_row + 1)):
                    self._file.seek(row * self.stride)
                    start = offset * bytes_per_line
                    self._file.write(payload[start:start + bytes_per_line])
        except OSError as exc:
            log.error("framebuffer yazılamadı: %s", exc)
            return

        if np is not None:
            self._previous = frame

    def clear(self) -> None:
        self.show(Image.new("RGB", (self.width, self.height), (0, 0, 0)), force=True)
