"""Panelin API istemcisi.

Panel ayrı bir süreç olarak çalışır ve sunucuyla yalnızca HTTP üzerinden
konuşur. Böylece arayüz çökse bile müzik kesilmez, panel servisi tek başına
yeniden başlatılabilir.

Yalnızca standart kütüphane kullanılır; SSE akışı satır satır okunur.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from typing import Any, Callable

from PIL import Image

log = logging.getLogger(__name__)


class ApiClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8080", timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._art_cache: OrderedDict[tuple[str, int], Image.Image] = OrderedDict()
        self._art_cache_limit = 40
        self._stop = threading.Event()

    # --- İstekler ------------------------------------------------------------

    def _request(self, method: str, path: str, payload: dict | None = None,
                 params: dict | None = None) -> Any:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})

        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
            return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            log.warning("%s %s -> HTTP %s", method, path, exc.code)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            log.debug("%s %s başarısız: %s", method, path, exc)
        return None

    def get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, payload: dict | None = None, **params: Any) -> Any:
        return self._request("POST", path, payload=payload or {}, params=params)

    # --- Kapak görselleri ----------------------------------------------------

    def art(self, key: str | None, size: int) -> Image.Image | None:
        """Kapağı indirir, istenen kutuya sığdırır ve önbelleğe alır."""
        if not key:
            return None

        cache_key = (key, size)
        cached = self._art_cache.get(cache_key)
        if cached is not None:
            self._art_cache.move_to_end(cache_key)
            return cached

        url = f"{self.base_url}/api/art/{key}?s=t"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                raw = response.read()
            with Image.open(io.BytesIO(raw)) as image:
                image = image.convert("RGB")
                image.thumbnail((size, size), Image.LANCZOS)
                result = image.copy()
        except Exception as exc:
            log.debug("kapak alınamadı (%s): %s", key, exc)
            return None

        self._art_cache[cache_key] = result
        self._art_cache.move_to_end(cache_key)
        while len(self._art_cache) > self._art_cache_limit:
            self._art_cache.popitem(last=False)
        return result

    # --- Durum akışı ---------------------------------------------------------

    def watch_state(self, on_state: Callable[[dict], None]) -> threading.Thread:
        """SSE akışını arka planda dinler, her durum güncellemesinde çağırır."""

        def loop() -> None:
            backoff = 1.0
            while not self._stop.is_set():
                try:
                    request = urllib.request.Request(
                        self.base_url + "/api/events",
                        headers={"Accept": "text/event-stream"},
                    )
                    # Akış sürekli açık kalacağı için burada zaman aşımı yok;
                    # sunucu 15 saniyede bir ping gönderiyor.
                    with urllib.request.urlopen(request) as stream:
                        backoff = 1.0
                        for raw_line in stream:
                            if self._stop.is_set():
                                return
                            line = raw_line.decode("utf-8", "replace").strip()
                            if not line.startswith("data:"):
                                continue
                            try:
                                on_state(json.loads(line[5:].strip()))
                            except json.JSONDecodeError:
                                continue
                except Exception as exc:
                    if self._stop.is_set():
                        return
                    log.info("durum akışı koptu (%s), %.0f sn sonra tekrar", exc, backoff)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 15.0)

        thread = threading.Thread(target=loop, name="panel-sse", daemon=True)
        thread.start()
        return thread

    def wait_for_server(self, attempts: int = 60) -> bool:
        """Sunucu ayağa kalkana kadar bekler (systemd sıralaması garanti değil)."""
        for _ in range(attempts):
            if self._stop.is_set():
                return False
            if self.get("/api/state") is not None:
                return True
            time.sleep(1.0)
        return False

    def stop(self) -> None:
        self._stop.set()

    # --- Kısayollar ----------------------------------------------------------

    def toggle(self) -> None:
        self.post("/api/player/toggle")

    def next(self) -> None:
        self.post("/api/player/next")

    def previous(self) -> None:
        self.post("/api/player/previous")

    def volume_delta(self, delta: int) -> None:
        self.post("/api/player/volume", {"delta": delta})

    def seek(self, seconds: float) -> None:
        self.post("/api/player/seek", {"seconds": seconds})

    def toggle_favourite(self) -> None:
        self.post("/api/favourite")

    def play_radio(self, track_id: int) -> None:
        self.post(f"/api/play/radio/{track_id}")

    def play_mix(self, mix_id: str) -> None:
        self.post(f"/api/play/mix/{urllib.parse.quote(mix_id)}")

    def smart_shuffle(self) -> None:
        self.post("/api/play/shuffle")

    def play_album(self, album_id: int) -> None:
        self.post(f"/api/play/album/{album_id}")

    def play_artist(self, artist_id: int) -> None:
        self.post(f"/api/play/artist/{artist_id}", {"shuffle": True})

    def queue_jump(self, position: int) -> None:
        self.post("/api/queue/jump", {"position": position})
