"""3.5" dokunmatik panel arayüzü.

Ekranlar bir yığın (stack) üzerinde tutulur: "Çalıyor" ekranı tabanda durur,
menü ve listeler üstüne binerek açılır. Geri dönüş hem ekrandaki geri tuşuyla
hem de encoder'a uzun basarak yapılabilir.

Girdi eşlemesi:
  dokunma        -> ekrandaki bölgeler
  encoder çevir  -> "Çalıyor" ekranında ses, listelerde seçim
  encoder bas    -> "Çalıyor" ekranında çal/duraklat, listelerde seçileni aç
  encoder uzun   -> geri / ana ekran
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Sequence

from PIL import Image, ImageDraw

from .. import config
from . import ui
from .client import ApiClient
from .framebuffer import FrameBuffer
from .input import LONG_PRESS, PRESS, ROTATE, TAP, Event, InputHub

log = logging.getLogger(__name__)

Region = tuple[tuple[int, int, int, int], Callable[[], None]]


# --- Ekran temeli -------------------------------------------------------------


class Screen:
    title = ""

    def __init__(self, app: "PanelApp") -> None:
        self.app = app
        self.regions: list[Region] = []

    @property
    def width(self) -> int:
        return self.app.width

    @property
    def height(self) -> int:
        return self.app.height

    def enter(self) -> None:
        """Ekran yığına konduğunda çağrılır (veri yüklemek için)."""

    def render(self, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
        raise NotImplementedError

    def add_region(self, box: tuple[int, int, int, int], action: Callable[[], None]) -> None:
        self.regions.append((box, action))

    def on_tap(self, x: int, y: int) -> None:
        for box, action in reversed(self.regions):
            if ui.inside(box, x, y):
                action()
                return

    def on_rotate(self, delta: int) -> None:
        pass

    def on_press(self) -> None:
        pass

    def on_long_press(self) -> None:
        self.app.pop()


# --- Çalıyor ekranı -----------------------------------------------------------


class NowPlayingScreen(Screen):
    title = "Çalıyor"

    def render(self, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
        state = self.app.state
        track = state.get("track") or {}
        playing = state.get("state") == "play"

        self._top_bar(draw, state)

        # Kapak
        art_size = 176
        art_box = (12, 34, 12 + art_size, 34 + art_size)
        art = self.app.api.art(track.get("art"), art_size)
        ui.album_art(image, art, art_box, draw)
        self.add_region(art_box, self.app.show_menu)

        # Metin sütunu
        left = 200
        max_width = self.width - left - 12
        if track:
            ui.draw_text(draw, (left, 38), track.get("title") or "—", ui.font(21, bold=True),
                         max_width=max_width)
            ui.draw_text(draw, (left, 70), track.get("artist") or "Bilinmeyen sanatçı",
                         ui.font(17), fill=ui.TEXT, max_width=max_width)
            ui.draw_text(draw, (left, 96), track.get("album") or "", ui.font(14),
                         fill=ui.TEXT_MUTED, max_width=max_width)
            meta = " · ".join(str(part) for part in (
                track.get("year") or "", track.get("genre") or "",
                f"{track['bitrate']} kbps" if track.get("bitrate") else "",
            ) if part)
            ui.draw_text(draw, (left, 118), meta, ui.font(12), fill=ui.TEXT_MUTED,
                         max_width=max_width)
        else:
            ui.draw_text(draw, (left, 70), "Çalan parça yok", ui.font(18), fill=ui.TEXT_MUTED,
                         max_width=max_width)
            ui.draw_text(draw, (left, 96), "Menüden bir liste seç", ui.font(13),
                         fill=ui.TEXT_MUTED, max_width=max_width)

        self._action_buttons(draw, track, left)
        self._progress(draw, state)
        self._transport(draw, playing)

        if self.app.volume_overlay_until > time.monotonic():
            self._volume_overlay(draw, state)

    def _top_bar(self, draw: ImageDraw.ImageDraw, state: dict) -> None:
        ui.draw_text(draw, (12, 8), time.strftime("%H:%M"), ui.font(14, bold=True),
                     fill=ui.TEXT_MUTED)

        queue_length = state.get("queueLength") or 0
        position = state.get("queuePosition")
        if queue_length:
            label = f"{(position or 0) + 1}/{queue_length}"
            ui.draw_text(draw, (self.width // 2, 8), label, ui.font(13), fill=ui.TEXT_MUTED,
                         anchor="ma")

        if state.get("volumeMode") == "fixed":
            ui.draw_text(draw, (self.width - 12, 8), "SABİT ÇIKIŞ", ui.font(12),
                         fill=ui.ACCENT_DIM, anchor="ra")
        else:
            ui.icon_speaker(draw, (self.width - 62, 15), 8, fill=ui.TEXT_MUTED)
            ui.draw_text(draw, (self.width - 12, 8), f"{state.get('volume', 0)}",
                         ui.font(14, bold=True), fill=ui.TEXT_MUTED, anchor="ra")

        scan = state.get("scan") or {}
        if scan.get("running"):
            percent = int((scan.get("progress") or 0) * 100)
            ui.draw_text(draw, (self.width // 2, 8), f"taranıyor %{percent}", ui.font(12),
                         fill=ui.ACCENT, anchor="ma")

    def _action_buttons(self, draw: ImageDraw.ImageDraw, track: dict, left: int) -> None:
        if not track:
            return
        top, height, width, gap = 144, 56, 76, 12

        favourite_box = (left, top, left + width, top + height)
        loved = (track.get("rating") or 0) > 0
        ui.panel(draw, favourite_box, fill=ui.SURFACE_HI if loved else ui.SURFACE, radius=10)
        ui.icon_heart(draw, (left + width // 2, top + height // 2), 13,
                      fill=ui.LOVE if loved else ui.TEXT_MUTED, filled=loved)
        self.add_region(favourite_box, self.app.toggle_favourite)

        radio_x = left + width + gap
        radio_box = (radio_x, top, radio_x + width, top + height)
        ui.panel(draw, radio_box, fill=ui.SURFACE, radius=10)
        ui.icon_radio(draw, (radio_x + width // 2, top + height // 2), 15, fill=ui.ACCENT)
        self.add_region(radio_box, self.app.play_radio)

        ui.draw_text(draw, (radio_x + width + gap, top + 12), "benzer", ui.font(12),
                     fill=ui.TEXT_MUTED)
        ui.draw_text(draw, (radio_x + width + gap, top + 30), "parçalar", ui.font(12),
                     fill=ui.TEXT_MUTED)

    def _progress(self, draw: ImageDraw.ImageDraw, state: dict) -> None:
        elapsed = self.app.interpolated_elapsed()
        duration = state.get("duration") or 0.0
        ratio = (elapsed / duration) if duration > 0 else 0.0

        bar = (12, 222, self.width - 12, 231)
        ui.progress_bar(draw, bar, ratio)
        ui.draw_text(draw, (12, 236), ui.format_time(elapsed), ui.font(12), fill=ui.TEXT_MUTED)
        ui.draw_text(draw, (self.width - 12, 236), ui.format_time(duration), ui.font(12),
                     fill=ui.TEXT_MUTED, anchor="ra")

        # Çubuğa dokunarak sarma. Dokunma bandı çubuktan kalın tutuldu ki
        # rezistif ekranda isabet ettirmek kolay olsun.
        if duration > 0:
            def seek_to(bar_box=bar, total=duration) -> None:
                x = self.app.last_tap[0]
                ratio_hit = (x - bar_box[0]) / max(1, bar_box[2] - bar_box[0])
                self.app.api.seek(max(0.0, min(1.0, ratio_hit)) * total)

            self.add_region((12, 212, self.width - 12, 248), seek_to)

    def _transport(self, draw: ImageDraw.ImageDraw, playing: bool) -> None:
        top, height, gap = 256, 60, 10
        count = 4
        width = (self.width - gap * (count + 1)) // count

        buttons: list[tuple[Callable, Callable[[], None], bool]] = [
            (ui.icon_previous, self.app.api.previous, False),
            (ui.icon_pause if playing else ui.icon_play, self.app.toggle, True),
            (ui.icon_next, self.app.api.next, False),
            (ui.icon_menu, self.app.show_menu, False),
        ]

        for index, (icon, action, primary) in enumerate(buttons):
            x0 = gap + index * (width + gap)
            box = (x0, top, x0 + width, top + height)
            ui.panel(draw, box, fill=ui.ACCENT if primary else ui.SURFACE, radius=12)
            icon(draw, (x0 + width // 2, top + height // 2), 16,
                 fill=ui.BG if primary else ui.TEXT)
            self.add_region(box, action)

    def _volume_overlay(self, draw: ImageDraw.ImageDraw, state: dict) -> None:
        box = (60, 118, self.width - 60, 202)
        ui.panel(draw, box, fill=ui.SURFACE_HI, radius=14, outline=ui.ACCENT, width=2)
        volume = state.get("volume", 0)
        ui.draw_text(draw, (self.width // 2, 132), f"Ses  %{volume}", ui.font(20, bold=True),
                     anchor="ma")
        ui.progress_bar(draw, (84, 170, self.width - 84, 182), volume / 100.0)

    # --- Girdi ---------------------------------------------------------------

    def on_rotate(self, delta: int) -> None:
        self.app.change_volume(delta * config.ENCODER_VOLUME_STEP)

    def on_press(self) -> None:
        self.app.toggle()

    def on_long_press(self) -> None:
        self.app.show_menu()


# --- Genel liste ekranı -------------------------------------------------------


class ListScreen(Screen):
    """Sayfalanabilir liste. Encoder seçimi gezdirir, dokunma doğrudan açar."""

    ROWS = 4
    ROW_HEIGHT = 58
    ROW_GAP = 3
    TOP = 38

    def __init__(self, app: "PanelApp", title: str,
                 loader: Callable[[int, int], tuple[list[dict], int]],
                 on_select: Callable[[dict], None],
                 subtitle_key: str | None = None) -> None:
        super().__init__(app)
        self.title = title
        self._loader = loader
        self._on_select = on_select
        self._subtitle_key = subtitle_key
        self._items: list[dict] = []
        self._total = 0
        self._page = 0
        self._selected = 0
        self._loading = True

    @property
    def page_count(self) -> int:
        return max(1, -(-self._total // self.ROWS))

    def enter(self) -> None:
        self._load()

    def _load(self) -> None:
        self._loading = True
        self.app.request_redraw()

        def work() -> None:
            try:
                items, total = self._loader(self._page * self.ROWS, self.ROWS)
            except Exception:
                log.exception("liste yüklenemedi")
                items, total = [], 0
            self._items = items
            self._total = total
            self._selected = min(self._selected, max(0, len(items) - 1))
            self._loading = False
            self.app.request_redraw()

        threading.Thread(target=work, name="panel-list-load", daemon=True).start()

    def render(self, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
        self._header(draw)

        if self._loading:
            ui.draw_text(draw, (self.width // 2, 150), "yükleniyor…", ui.font(16),
                         fill=ui.TEXT_MUTED, anchor="ma")
            return

        if not self._items:
            ui.draw_text(draw, (self.width // 2, 150), "burada bir şey yok", ui.font(16),
                         fill=ui.TEXT_MUTED, anchor="ma")
        else:
            for index, item in enumerate(self._items):
                self._row(image, draw, index, item)

        self._footer(draw)

    def _header(self, draw: ImageDraw.ImageDraw) -> None:
        back_box = (0, 0, 56, 34)
        ui.icon_back(draw, (26, 17), 10, fill=ui.TEXT)
        self.add_region((0, 0, 64, 36), self.app.pop)

        ui.draw_text(draw, (68, 8), self.title, ui.font(17, bold=True), max_width=self.width - 150)
        if self.page_count > 1:
            ui.draw_text(draw, (self.width - 12, 10), f"{self._page + 1}/{self.page_count}",
                         ui.font(13), fill=ui.TEXT_MUTED, anchor="ra")
        draw.line((0, 35, self.width, 35), fill=ui.SURFACE_HI, width=1)

    def _row(self, image: Image.Image, draw: ImageDraw.ImageDraw, index: int, item: dict) -> None:
        y0 = self.TOP + index * (self.ROW_HEIGHT + self.ROW_GAP)
        box = (8, y0, self.width - 8, y0 + self.ROW_HEIGHT)
        selected = index == self._selected

        ui.panel(draw, box, fill=ui.SURFACE_HI if selected else ui.SURFACE, radius=10)
        if selected:
            draw.rounded_rectangle(box, radius=10, outline=ui.ACCENT, width=2)

        text_x = 18
        art_key = item.get("art")
        if art_key:
            art = self.app.api.art(art_key, self.ROW_HEIGHT - 12)
            if art is not None:
                image.paste(art, (16, y0 + 6))
                text_x = 16 + art.width + 12

        max_width = self.width - text_x - 20
        subtitle = self._subtitle(item)
        if subtitle:
            ui.draw_text(draw, (text_x, y0 + 11), item.get("title") or item.get("name") or "—",
                         ui.font(16, bold=True), max_width=max_width)
            ui.draw_text(draw, (text_x, y0 + 33), subtitle, ui.font(13), fill=ui.TEXT_MUTED,
                         max_width=max_width)
        else:
            ui.draw_text(draw, (text_x, y0 + 20), item.get("title") or item.get("name") or "—",
                         ui.font(16, bold=True), max_width=max_width)

        self.add_region(box, lambda captured=item: self._activate(captured))

    def _subtitle(self, item: dict) -> str:
        if self._subtitle_key:
            return str(item.get(self._subtitle_key) or "")
        for key in ("subtitle", "artist", "album"):
            value = item.get(key)
            if value:
                return str(value)
        return ""

    def _footer(self, draw: ImageDraw.ImageDraw) -> None:
        if self.page_count <= 1:
            return
        top = 282
        ui.panel(draw, (8, top, 148, top + 36), fill=ui.SURFACE, radius=10)
        ui.icon_chevron(draw, (78, top + 18), 10, direction="up",
                        fill=ui.TEXT if self._page > 0 else ui.SURFACE_HI)
        self.add_region((0, top - 4, 156, self.height), self._previous_page)

        ui.panel(draw, (self.width - 148, top, self.width - 8, top + 36), fill=ui.SURFACE, radius=10)
        ui.icon_chevron(draw, (self.width - 78, top + 18), 10, direction="down",
                        fill=ui.TEXT if self._page + 1 < self.page_count else ui.SURFACE_HI)
        self.add_region((self.width - 156, top - 4, self.width, self.height), self._next_page)

    def _activate(self, item: dict) -> None:
        self._on_select(item)

    def _previous_page(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._selected = 0
            self._load()

    def _next_page(self) -> None:
        if self._page + 1 < self.page_count:
            self._page += 1
            self._selected = 0
            self._load()

    # --- Girdi ---------------------------------------------------------------

    def on_rotate(self, delta: int) -> None:
        if not self._items:
            return
        target = self._selected + delta
        if target < 0:
            if self._page > 0:
                self._page -= 1
                self._selected = self.ROWS - 1
                self._load()
            return
        if target >= len(self._items):
            if self._page + 1 < self.page_count:
                self._page += 1
                self._selected = 0
                self._load()
            return
        self._selected = target
        self.app.request_redraw()

    def on_press(self) -> None:
        if self._items and 0 <= self._selected < len(self._items):
            self._activate(self._items[self._selected])


class ActionListScreen(ListScreen):
    """Sabit eylem listesi (menü, ayarlar). Veriyi her açılışta yeniden üretir."""

    def __init__(self, app: "PanelApp", title: str,
                 build: Callable[[], list[dict]]) -> None:
        self._build = build
        super().__init__(app, title, self._load_page, self._run, subtitle_key="subtitle")

    def _load_page(self, offset: int, limit: int) -> tuple[list[dict], int]:
        items = self._build()
        return items[offset:offset + limit], len(items)

    @staticmethod
    def _run(item: dict) -> None:
        action = item.get("action")
        if callable(action):
            action()


# --- Uygulama -----------------------------------------------------------------


class PanelApp:
    def __init__(self, api_url: str = "http://127.0.0.1:8080") -> None:
        self.api = ApiClient(api_url)
        self.framebuffer = FrameBuffer(config.PANEL_FB, config.PANEL_WIDTH, config.PANEL_HEIGHT)
        self.width = self.framebuffer.width
        self.height = self.framebuffer.height
        self.input = InputHub(self.width, self.height)

        self.state: dict[str, Any] = {}
        self._state_received_at = 0.0
        self._stack: list[Screen] = []
        self._dirty = threading.Event()
        self._running = True
        self.last_tap = (0, 0)
        self.volume_overlay_until = 0.0
        self._toast_text = ""
        self._toast_until = 0.0

    # --- Yaşam döngüsü -------------------------------------------------------

    def run(self) -> None:
        log.info("panel başlıyor; sunucu bekleniyor…")
        if not self.api.wait_for_server():
            log.error("API sunucusuna ulaşılamadı, panel kapanıyor")
            return

        self.api.watch_state(self._on_state)
        self.input.start()
        self.push(NowPlayingScreen(self))

        with self.framebuffer:
            self.framebuffer.clear()
            frame_interval = 1.0 / max(1, config.PANEL_FPS)
            last_render = 0.0
            try:
                while self._running:
                    event = self.input.get(timeout=frame_interval)
                    if event is not None:
                        self._handle(event)

                    now = time.monotonic()
                    playing = self.state.get("state") == "play"
                    # Çalarken ilerleme çubuğu için sürekli, dururken sadece
                    # değişiklik olduğunda çiz. Boştayken SPI hattı serbest kalır.
                    due = self._dirty.is_set() or (playing and now - last_render >= frame_interval)
                    if due:
                        self._dirty.clear()
                        self._render()
                        last_render = now
            except KeyboardInterrupt:
                pass
            finally:
                self.stop()

    def stop(self) -> None:
        self._running = False
        self.input.stop()
        self.api.stop()

    def request_redraw(self) -> None:
        self._dirty.set()

    def _on_state(self, state: dict) -> None:
        self.state = state
        self._state_received_at = time.monotonic()
        self.request_redraw()

    def interpolated_elapsed(self) -> float:
        """İki durum güncellemesi arasında ilerlemeyi yumuşatır."""
        elapsed = self.state.get("elapsed") or 0.0
        if self.state.get("state") != "play":
            return elapsed
        return elapsed + max(0.0, time.monotonic() - self._state_received_at)

    # --- Ekran yığını --------------------------------------------------------

    @property
    def screen(self) -> Screen:
        return self._stack[-1]

    def push(self, screen: Screen) -> None:
        self._stack.append(screen)
        screen.enter()
        self.request_redraw()

    def pop(self) -> None:
        if len(self._stack) > 1:
            self._stack.pop()
            self.request_redraw()

    def go_home(self) -> None:
        del self._stack[1:]
        self.request_redraw()

    # --- Girdi dağıtımı ------------------------------------------------------

    def _handle(self, event: Event) -> None:
        screen = self.screen
        if event.kind == TAP:
            self.last_tap = (event.x, event.y)
            screen.on_tap(event.x, event.y)
        elif event.kind == ROTATE:
            screen.on_rotate(event.delta)
        elif event.kind == PRESS:
            screen.on_press()
        elif event.kind == LONG_PRESS:
            screen.on_long_press()
        self.request_redraw()

    def _render(self) -> None:
        image, draw = ui.new_frame(self.width, self.height)
        screen = self.screen
        screen.regions = []
        try:
            screen.render(image, draw)
        except Exception:
            log.exception("ekran çizilemedi")
            ui.draw_text(draw, (self.width // 2, self.height // 2), "çizim hatası",
                         ui.font(16), fill=ui.DANGER, anchor="mm")

        if self._toast_until > time.monotonic():
            self._draw_toast(draw)

        self.framebuffer.show(image)

    def _draw_toast(self, draw: ImageDraw.ImageDraw) -> None:
        box = (24, self.height - 92, self.width - 24, self.height - 46)
        ui.panel(draw, box, fill=ui.SURFACE_HI, radius=12, outline=ui.ACCENT, width=1)
        ui.draw_text(draw, (self.width // 2, self.height - 78), self._toast_text,
                     ui.font(15, bold=True), anchor="ma", max_width=self.width - 72)

    def toast(self, text: str, seconds: float = 2.0) -> None:
        self._toast_text = text
        self._toast_until = time.monotonic() + seconds
        self.request_redraw()

    # --- Eylemler ------------------------------------------------------------

    def toggle(self) -> None:
        self.api.toggle()

    def change_volume(self, delta: int) -> None:
        if self.state.get("volumeMode") == "fixed":
            self.toast("Çıkış sabit — sesi amfiden ayarla")
            return
        # Sunucu yanıtı gelene kadar arayüz donuk görünmesin diye yerel tahmin.
        current = self.state.get("volume", 0)
        self.state["volume"] = max(0, min(100, current + delta))
        self.volume_overlay_until = time.monotonic() + 1.6
        self.api.volume_delta(delta)
        self.request_redraw()

    def toggle_favourite(self) -> None:
        track = self.state.get("track") or {}
        loved = (track.get("rating") or 0) > 0
        self.api.toggle_favourite()
        self.toast("Favorilerden çıkarıldı" if loved else "Favorilere eklendi")

    def play_radio(self) -> None:
        track = self.state.get("track") or {}
        if not track.get("id"):
            self.toast("Önce bir parça çal")
            return
        self.api.play_radio(track["id"])
        self.toast("Benzer parçalar sıraya alındı")

    # --- Menü ekranları ------------------------------------------------------

    def show_menu(self) -> None:
        if isinstance(self.screen, ActionListScreen) and self.screen.title == "Menü":
            return
        self.push(ActionListScreen(self, "Menü", self._menu_items))

    def _menu_items(self) -> list[dict]:
        return [
            {"name": "Akıllı Karıştır", "subtitle": "Zevkine göre seçilmiş liste",
             "action": self._smart_shuffle},
            {"name": "Mixler", "subtitle": "Hazır öneri listeleri", "action": self._show_mixes},
            {"name": "Albümler", "subtitle": "Tüm albümler", "action": self._show_albums},
            {"name": "Sanatçılar", "subtitle": "Tüm sanatçılar", "action": self._show_artists},
            {"name": "Kuyruk", "subtitle": "Sıradaki parçalar", "action": self._show_queue},
            {"name": "Ayarlar", "subtitle": "Tarama, sistem, bilgi", "action": self._show_settings},
        ]

    def _smart_shuffle(self) -> None:
        self.api.smart_shuffle()
        self.go_home()
        self.toast("Akıllı karıştırma başladı")

    def _show_mixes(self) -> None:
        def loader(offset: int, limit: int) -> tuple[list[dict], int]:
            data = self.api.get("/api/home") or {}
            mixes = data.get("mixes", [])
            return mixes[offset:offset + limit], len(mixes)

        def select(item: dict) -> None:
            self.api.play_mix(item["id"])
            self.go_home()
            self.toast(f"{item.get('title', 'Mix')} çalınıyor")

        screen = ListScreen(self, "Mixler", loader, select, subtitle_key="subtitle")
        # Mixlerde başlık alanı "title", listelerin geneli "name" kullanıyor.
        self.push(screen)

    def _show_albums(self) -> None:
        def loader(offset: int, limit: int) -> tuple[list[dict], int]:
            data = self.api.get("/api/albums", offset=offset, limit=limit, sort="artist") or {}
            return data.get("items", []), data.get("total", 0)

        def select(item: dict) -> None:
            self.api.play_album(item["id"])
            self.go_home()
            self.toast(f"{item.get('name', 'Albüm')} çalınıyor")

        self.push(ListScreen(self, "Albümler", loader, select, subtitle_key="artist"))

    def _show_artists(self) -> None:
        def loader(offset: int, limit: int) -> tuple[list[dict], int]:
            data = self.api.get("/api/artists", offset=offset, limit=limit) or {}
            return data.get("items", []), data.get("total", 0)

        def select(item: dict) -> None:
            self._show_artist_albums(item)

        self.push(ListScreen(self, "Sanatçılar", loader, select))

    def _show_artist_albums(self, artist: dict) -> None:
        def loader(offset: int, limit: int) -> tuple[list[dict], int]:
            data = self.api.get(f"/api/artists/{artist['id']}") or {}
            albums = data.get("albums", [])
            return albums[offset:offset + limit], len(albums)

        def select(item: dict) -> None:
            self.api.play_album(item["id"])
            self.go_home()
            self.toast(f"{item.get('name', 'Albüm')} çalınıyor")

        self.push(ListScreen(self, artist.get("name", "Sanatçı"), loader, select,
                             subtitle_key="year"))

    def _show_queue(self) -> None:
        def loader(offset: int, limit: int) -> tuple[list[dict], int]:
            data = self.api.get("/api/queue") or {}
            items = data.get("items", [])
            return items[offset:offset + limit], len(items)

        def select(item: dict) -> None:
            self.api.queue_jump(item["position"])
            self.go_home()

        self.push(ListScreen(self, "Kuyruk", loader, select, subtitle_key="artist"))

    def _show_settings(self) -> None:
        self.push(ActionListScreen(self, "Ayarlar", self._settings_items))

    def _settings_items(self) -> list[dict]:
        state = self.state
        options = state.get("options", {})
        stats = self.api.get("/api/stats") or {}
        scan = state.get("scan") or {}

        def toggle_option(name: str) -> Callable[[], None]:
            def action() -> None:
                self.api.post("/api/player/option",
                              {"name": name, "value": not options.get(name, False)})
                self.toast("Güncellendi")
            return action

        def toggle_autoqueue() -> None:
            self.api.post("/api/player/autoqueue", {"value": not state.get("autoQueue", True)})
            self.toast("Güncellendi")

        def rescan() -> None:
            self.api.post("/api/scan", {})
            self.toast("Tarama başlatıldı")

        def power(action_name: str, label: str) -> Callable[[], None]:
            def action() -> None:
                self.toast(label)
                self.api.post(f"/api/system/{action_name}")
            return action

        scan_subtitle = "çalışıyor…" if scan.get("running") else (
            f"{stats.get('tracks', 0)} parça · {stats.get('albums', 0)} albüm"
        )

        return [
            {"name": "Otomatik kuyruk",
             "subtitle": "açık — kuyruk bitince öneri eklenir" if state.get("autoQueue")
                         else "kapalı",
             "action": toggle_autoqueue},
            {"name": "Tekrarla",
             "subtitle": "açık" if options.get("repeat") else "kapalı",
             "action": toggle_option("repeat")},
            {"name": "Karıştır",
             "subtitle": "açık" if options.get("random") else "kapalı",
             "action": toggle_option("random")},
            {"name": "Kütüphaneyi tara", "subtitle": scan_subtitle, "action": rescan},
            {"name": "Yeniden başlat", "subtitle": "sistemi yeniden başlatır",
             "action": power("reboot", "Yeniden başlatılıyor…")},
            {"name": "Kapat", "subtitle": "güvenli kapatma", "action": power("poweroff", "Kapatılıyor…")},
        ]


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    import os

    PanelApp(os.environ.get("HDDMUSICPLAYER_API_URL", "http://127.0.0.1:8080")).run()


if __name__ == "__main__":
    main()
