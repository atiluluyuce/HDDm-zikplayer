"""Çalar denetleyicisi: MPD ile kütüphane/öneri motoru arasındaki köprü.

Sorumlulukları:
  * MPD durumunu izlemek ve abonelere (web arayüzü, panel) yayınlamak
  * parça değişimlerini yakalayıp çalma geçmişine işlemek
  * kuyruk azaldığında öneri motorundan otomatik doldurma yapmak
  * "albümü çal", "benzerlerini çal" gibi üst seviye eylemleri sunmak
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, AsyncIterator, Iterable, Sequence

from . import config, db, library, mpd, recommender, scanner

log = logging.getLogger(__name__)

#: Çalarken durumun MPD'den tazelenme aralığı. `idle` ilerleme çubuğu için
#: olay üretmediğinden konum bilgisi bu döngüyle güncelleniyor.
TICK_SECONDS = 1.0


class Player:
    def __init__(self) -> None:
        self.mpd = mpd.MPD(config.MPD_HOST, config.MPD_PORT)
        self.watcher = mpd.MPDWatcher(config.MPD_HOST, config.MPD_PORT, self._on_mpd_change)

        self._state: dict[str, Any] = _empty_state()
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._ticker: asyncio.Task | None = None

        # Parça değişimi tespiti için tutulan bağlam
        self._current_song_id: int | None = None
        self._current_track_id: int | None = None
        self._previous_track_id: int | None = None
        self._last_elapsed: float = 0.0
        self._last_duration: float = 0.0
        self._play_source: str = "queue"

        self._autoqueue = (db.get_pref("autoqueue", "1") == "1")
        self._autoqueue_busy = False

    # --- Yaşam döngüsü -------------------------------------------------------

    async def start(self) -> None:
        self.watcher.start()
        self._ticker = asyncio.create_task(self._tick_loop(), name="player-tick")
        with contextlib.suppress(mpd.MPDError):
            await self.refresh()

    async def stop(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
            self._ticker = None
        await self.watcher.stop()
        await self.mpd.close()

    async def _tick_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(TICK_SECONDS)
                if self._state.get("state") == "play":
                    await self.refresh()
            except asyncio.CancelledError:
                raise
            except mpd.MPDError as exc:
                log.debug("tik sırasında MPD hatası: %s", exc)
            except Exception:
                log.exception("tik döngüsünde beklenmeyen hata")

    # --- Abonelik ------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._subscribers.add(queue)
        queue.put_nowait(self.state)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        queue = self.subscribe()
        try:
            while True:
                yield await queue.get()
        finally:
            self.unsubscribe(queue)

    def _broadcast(self) -> None:
        state = self.state
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(state)
            except asyncio.QueueFull:
                # Yavaş istemci: en eskiyi düşür, en güncel durumu koy.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(state)

    @property
    def state(self) -> dict[str, Any]:
        data = dict(self._state)
        data["scan"] = scanner.STATE.snapshot()
        return data

    # --- MPD durumu ----------------------------------------------------------

    async def _on_mpd_change(self, subsystems: set[str]) -> None:
        try:
            await self.refresh()
            if "playlist" in subsystems or "player" in subsystems:
                await self._maybe_autoqueue()
        except mpd.MPDError as exc:
            log.debug("durum tazelenemedi: %s", exc)

    async def refresh(self) -> None:
        status = await self.mpd.status()
        song = await self.mpd.current_song()

        song_id = _as_int(status.get("songid"))
        uri = song.get("file")
        track = library.track_by_path(uri) if uri else None
        track_id = track["id"] if track else None

        elapsed = _as_float(status.get("elapsed"), 0.0)
        duration = _as_float(status.get("duration"), 0.0) or (track or {}).get("duration") or 0.0

        if song_id != self._current_song_id:
            await self._on_track_change(track_id)
        self._current_song_id = song_id
        self._current_track_id = track_id

        # Sonraki parçaya geçildiğinde "ne kadarı dinlendi" bilgisi kaybolmasın:
        # geçişten önceki son ölçümü saklıyoruz.
        if elapsed > 0:
            self._last_elapsed = elapsed
        if duration > 0:
            self._last_duration = duration

        self._state = {
            "state": status.get("state", "stop"),
            "volume": _as_int(status.get("volume"), 0) or 0,
            "volumeMode": config.VOLUME_MODE,
            "elapsed": elapsed,
            "duration": duration,
            "track": track,
            "songId": song_id,
            "queueLength": _as_int(status.get("playlistlength"), 0) or 0,
            "queuePosition": _as_int(status.get("song")),
            "options": {
                "random": status.get("random") == "1",
                "repeat": status.get("repeat") == "1",
                "single": status.get("single") == "1",
                "consume": status.get("consume") == "1",
            },
            "autoQueue": self._autoqueue,
            "audio": status.get("audio", ""),
            "bitrate": _as_int(status.get("bitrate")),
            "error": status.get("error", ""),
            "updatedAt": time.time(),
        }
        self._broadcast()

    async def _on_track_change(self, new_track_id: int | None) -> None:
        """Önceki parçayı geçmişe işler."""
        finished_id = self._current_track_id
        if finished_id is None:
            self._previous_track_id = None
            self._last_elapsed = 0.0
            return

        duration = self._last_duration or 0.0
        ratio = (self._last_elapsed / duration) if duration > 0 else 0.0
        completed = ratio >= config.PLAY_COMPLETION_RATIO

        try:
            recommender.record_play(
                finished_id,
                completed=completed,
                source=self._play_source,
                previous_id=self._previous_track_id,
            )
        except Exception:
            log.exception("çalma geçmişi kaydedilemedi")

        self._previous_track_id = finished_id
        self._last_elapsed = 0.0
        self._last_duration = 0.0

    # --- Otomatik kuyruk -----------------------------------------------------

    async def _maybe_autoqueue(self) -> None:
        if not self._autoqueue or self._autoqueue_busy:
            return
        length = self._state.get("queueLength", 0)
        position = self._state.get("queuePosition")
        if position is None:
            return
        if length - position - 1 >= config.AUTOQUEUE_MIN:
            return

        self._autoqueue_busy = True
        try:
            playlist = await self.mpd.playlist()
            existing_paths = [item.get("file", "") for item in playlist]
            existing_ids = [
                t["id"] for t in (library.track_by_path(p) for p in existing_paths) if t
            ]
            picks = await asyncio.to_thread(
                recommender.next_up,
                None,
                existing_ids,
                self._current_track_id,
            )
            paths = library.paths_for_ids(picks)
            if paths:
                await self.mpd.add(paths)
                log.info("otomatik kuyruk: %d parça eklendi", len(paths))
        except mpd.MPDError as exc:
            log.warning("otomatik kuyruk başarısız: %s", exc)
        except Exception:
            log.exception("otomatik kuyrukta beklenmeyen hata")
        finally:
            self._autoqueue_busy = False

    def set_autoqueue(self, enabled: bool) -> None:
        self._autoqueue = bool(enabled)
        db.set_pref("autoqueue", "1" if enabled else "0")

    # --- Transport -----------------------------------------------------------

    async def toggle(self) -> None:
        if self._state.get("state") == "play":
            await self.mpd.pause(True)
        elif self._state.get("state") == "pause":
            await self.mpd.pause(False)
        else:
            await self.mpd.play()

    async def play(self) -> None:
        if self._state.get("state") == "pause":
            await self.mpd.pause(False)
        else:
            await self.mpd.play()

    async def pause(self) -> None:
        await self.mpd.pause(True)

    async def stop_playback(self) -> None:
        await self.mpd.stop()

    async def next(self) -> None:
        await self.mpd.next()

    async def previous(self) -> None:
        """Parçanın başındaysa öncekine geçer, değilse başa sarar.

        Fiziksel bir çalarda beklenen davranış bu; MPD'nin ham `previous`
        komutu her zaman önceki parçaya atlar.
        """
        if self._state.get("elapsed", 0) > 3.0:
            await self.mpd.seek(0)
        else:
            await self.mpd.previous()

    async def seek(self, seconds: float) -> None:
        await self.mpd.seek(seconds)

    async def set_volume(self, volume: int) -> None:
        if config.VOLUME_MODE == "fixed":
            return  # çıkış sabit; seviye amfiden ayarlanıyor
        await self.mpd.set_volume(max(0, min(config.VOLUME_MAX, int(volume))))

    async def adjust_volume(self, delta: int) -> None:
        await self.set_volume(self._state.get("volume", 0) + delta)

    async def set_option(self, name: str, value: bool) -> None:
        if name not in ("random", "repeat", "single", "consume"):
            raise ValueError(f"bilinmeyen seçenek: {name}")
        await self.mpd.set_option(name, value)

    # --- Kuyruk --------------------------------------------------------------

    async def queue(self) -> list[dict[str, Any]]:
        items = await self.mpd.playlist()
        result = []
        for index, item in enumerate(items):
            uri = item.get("file", "")
            track = library.track_by_path(uri)
            if track is None:
                track = {
                    "id": None,
                    "title": item.get("Title") or uri.rsplit("/", 1)[-1],
                    "artist": item.get("Artist"),
                    "album": item.get("Album"),
                    "duration": _as_float(item.get("duration"), 0.0),
                    "art": None,
                }
            entry = dict(track)
            entry["position"] = index
            entry["songId"] = _as_int(item.get("Id"))
            result.append(entry)
        return result

    async def clear_queue(self) -> None:
        await self.mpd.clear()

    async def remove_from_queue(self, song_id: int) -> None:
        await self.mpd.delete_id(song_id)

    async def play_queue_position(self, position: int) -> None:
        await self.mpd.play(position)

    async def enqueue_ids(self, track_ids: Sequence[int], replace: bool = False,
                          play: bool = False, source: str = "queue") -> int:
        """Parçaları kuyruğa ekler. replace=True mevcut kuyruğu temizler."""
        paths = library.paths_for_ids(track_ids)
        if not paths:
            return 0

        self._play_source = source
        if replace:
            await self.mpd.clear()

        start_position = 0 if replace else self._state.get("queueLength", 0)
        await self.mpd.add(paths)
        if play:
            await self.mpd.play(start_position)
        return len(paths)

    # --- Üst seviye eylemler -------------------------------------------------

    async def play_album(self, album_id: int, replace: bool = True) -> int:
        tracks = library.album_tracks(album_id)
        return await self.enqueue_ids(
            [t["id"] for t in tracks], replace=replace, play=replace, source="album"
        )

    async def play_artist(self, artist_id: int, replace: bool = True, shuffle: bool = False) -> int:
        tracks = library.artist_tracks(artist_id)
        ids = [t["id"] for t in tracks]
        if shuffle:
            recommender.shuffle_ids(ids)
        return await self.enqueue_ids(ids, replace=replace, play=replace, source="artist")

    async def play_track(self, track_id: int, replace: bool = False) -> int:
        """Tek parça çalar. replace=False ise sıradaki parça olarak araya girer."""
        if replace:
            return await self.enqueue_ids([track_id], replace=True, play=True, source="track")

        paths = library.paths_for_ids([track_id])
        if not paths:
            return 0
        self._play_source = "track"
        ids = await self.mpd.add_ids(paths)
        position = self._state.get("queuePosition")
        if ids and position is not None:
            await self.mpd.move_id(ids[0], position + 1)
            await self.mpd.play(position + 1)
        elif ids:
            await self.mpd.play_id(ids[0])
        return 1

    async def play_album_from(self, album_id: int, track_id: int) -> int:
        """Albümü çalar ama seçilen parçadan başlar (albüm sırasını korur)."""
        tracks = library.album_tracks(album_id)
        ids = [t["id"] for t in tracks]
        if track_id in ids:
            index = ids.index(track_id)
        else:
            index = 0
        await self.enqueue_ids(ids, replace=True, play=False, source="album")
        await self.mpd.play(index)
        return len(ids)

    async def play_mix(self, mix_id: str, count: int = 50) -> int:
        ids = await asyncio.to_thread(recommender.mix_tracks, mix_id, count)
        return await self.enqueue_ids(ids, replace=True, play=True, source="smart")

    async def play_radio(self, track_id: int, count: int = 25) -> int:
        """Tohum parçayı çalar, ardına benzerlerini dizer."""
        ids = await asyncio.to_thread(recommender.radio, track_id, count)
        return await self.enqueue_ids([track_id, *ids], replace=True, play=True, source="radio")

    async def smart_shuffle_all(self, count: int = 50) -> int:
        ids = await asyncio.to_thread(recommender.smart_shuffle, count, None, ())
        return await self.enqueue_ids(ids, replace=True, play=True, source="smart")

    # --- Beğeni --------------------------------------------------------------

    def set_rating(self, track_id: int, rating: int) -> None:
        library.set_rating(track_id, rating)
        track = self._state.get("track")
        if track and track.get("id") == track_id:
            track["rating"] = rating
            self._broadcast()

    def toggle_favourite(self) -> int | None:
        track = self._state.get("track")
        if not track or track.get("id") is None:
            return None
        new_rating = 0 if track.get("rating", 0) > 0 else 1
        self.set_rating(track["id"], new_rating)
        return new_rating


def _empty_state() -> dict[str, Any]:
    return {
        "state": "stop",
        "volume": 0,
        "volumeMode": config.VOLUME_MODE,
        "elapsed": 0.0,
        "duration": 0.0,
        "track": None,
        "songId": None,
        "queueLength": 0,
        "queuePosition": None,
        "options": {"random": False, "repeat": False, "single": False, "consume": False},
        "autoQueue": True,
        "audio": "",
        "bitrate": None,
        "error": "",
        "updatedAt": 0.0,
    }


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
