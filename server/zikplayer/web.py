"""HTTP API ve web arayüzü sunucusu (Starlette).

FastAPI yerine düz Starlette kullanılıyor: pydantic'in derlenmiş bağımlılıkları
32-bit Raspberry Pi OS'ta hazır wheel bulamayıp kaynaktan derlenmeye çalışıyor ve
Pi 3'te bu işlem çok uzun sürüyor. Starlette + uvicorn tamamen saf Python.

Canlı durum güncellemeleri WebSocket yerine SSE ile taşınıyor — tek yönlü akış
için yeterli ve ek bağımlılık gerektirmiyor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import config, db, library, recommender, scanner
from .player import Player

log = logging.getLogger(__name__)

player = Player()


# --- Yardımcılar --------------------------------------------------------------


def _int_param(request: Request, name: str, default: int, minimum: int = 0,
               maximum: int = 5000) -> int:
    try:
        value = int(request.query_params.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ok(**extra: Any) -> JSONResponse:
    return JSONResponse({"ok": True, **extra})


# --- Durum ve olaylar ---------------------------------------------------------


async def get_state(request: Request) -> JSONResponse:
    return JSONResponse(player.state)


async def event_stream(request: Request) -> StreamingResponse:
    """Sunucu tarafı olay akışı: durum her değiştiğinde istemciye itilir."""

    async def generator():
        queue = player.subscribe()
        try:
            while True:
                try:
                    state = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(state, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # Vekil sunucuların ve tarayıcının bağlantıyı kapatmaması için
                    # düzenli aralıkla yorum satırı gönderilir.
                    yield ": ping\n\n"
                if await request.is_disconnected():
                    break
        finally:
            player.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --- Transport ----------------------------------------------------------------


async def player_action(request: Request) -> JSONResponse:
    action = request.path_params["action"]
    data = await _body(request)

    if action == "toggle":
        await player.toggle()
    elif action == "play":
        await player.play()
    elif action == "pause":
        await player.pause()
    elif action == "stop":
        await player.stop_playback()
    elif action == "next":
        await player.next()
    elif action == "previous":
        await player.previous()
    elif action == "seek":
        await player.seek(float(data.get("seconds", 0)))
    elif action == "volume":
        if "delta" in data:
            await player.adjust_volume(int(data["delta"]))
        else:
            await player.set_volume(int(data.get("value", 0)))
    elif action == "option":
        await player.set_option(str(data.get("name", "")), bool(data.get("value")))
    elif action == "autoqueue":
        player.set_autoqueue(bool(data.get("value", True)))
        await player.refresh()
    else:
        return JSONResponse({"error": f"bilinmeyen eylem: {action}"}, status_code=404)

    return _ok()


# --- Kuyruk -------------------------------------------------------------------


async def get_queue(request: Request) -> JSONResponse:
    return JSONResponse({"items": await player.queue()})


async def queue_action(request: Request) -> JSONResponse:
    action = request.path_params["action"]
    data = await _body(request)

    if action == "clear":
        await player.clear_queue()
    elif action == "remove":
        await player.remove_from_queue(int(data["songId"]))
    elif action == "jump":
        await player.play_queue_position(int(data["position"]))
    elif action == "add":
        ids = [int(i) for i in data.get("trackIds", [])]
        added = await player.enqueue_ids(ids, replace=bool(data.get("replace")),
                                        play=bool(data.get("play")))
        return _ok(added=added)
    else:
        return JSONResponse({"error": f"bilinmeyen eylem: {action}"}, status_code=404)

    return _ok()


# --- Çalma eylemleri ----------------------------------------------------------


async def play_album(request: Request) -> JSONResponse:
    data = await _body(request)
    album_id = int(request.path_params["album_id"])
    if data.get("fromTrackId"):
        count = await player.play_album_from(album_id, int(data["fromTrackId"]))
    else:
        count = await player.play_album(album_id, replace=not data.get("append"))
    return _ok(queued=count)


async def play_artist(request: Request) -> JSONResponse:
    data = await _body(request)
    count = await player.play_artist(
        int(request.path_params["artist_id"]),
        replace=not data.get("append"),
        shuffle=bool(data.get("shuffle")),
    )
    return _ok(queued=count)


async def play_track(request: Request) -> JSONResponse:
    data = await _body(request)
    count = await player.play_track(int(request.path_params["track_id"]),
                                    replace=bool(data.get("replace")))
    return _ok(queued=count)


async def play_mix(request: Request) -> JSONResponse:
    mix_id = request.path_params["mix_id"]
    count = await player.play_mix(mix_id, _int_param(request, "count", 50, 1, 500))
    return _ok(queued=count)


async def play_radio(request: Request) -> JSONResponse:
    count = await player.play_radio(int(request.path_params["track_id"]),
                                    _int_param(request, "count", 25, 1, 200))
    return _ok(queued=count)


async def smart_shuffle(request: Request) -> JSONResponse:
    count = await player.smart_shuffle_all(_int_param(request, "count", 50, 1, 500))
    return _ok(queued=count)


# --- Kütüphane ----------------------------------------------------------------


async def get_albums(request: Request) -> JSONResponse:
    return JSONResponse({
        "total": await asyncio.to_thread(library.album_count),
        "items": await asyncio.to_thread(
            library.albums,
            _int_param(request, "offset", 0),
            _int_param(request, "limit", 60, 1, 500),
            request.query_params.get("sort", "artist"),
            None,
            request.query_params.get("genre"),
        ),
    })


async def get_album(request: Request) -> Response:
    data = await asyncio.to_thread(library.album, int(request.path_params["album_id"]))
    if data is None:
        return JSONResponse({"error": "albüm bulunamadı"}, status_code=404)
    return JSONResponse(data)


async def get_artists(request: Request) -> JSONResponse:
    return JSONResponse({
        "total": await asyncio.to_thread(library.artist_count),
        "items": await asyncio.to_thread(
            library.artists,
            _int_param(request, "offset", 0),
            _int_param(request, "limit", 200, 1, 2000),
        ),
    })


async def get_artist(request: Request) -> Response:
    data = await asyncio.to_thread(library.artist, int(request.path_params["artist_id"]))
    if data is None:
        return JSONResponse({"error": "sanatçı bulunamadı"}, status_code=404)
    return JSONResponse(data)


async def get_track(request: Request) -> Response:
    data = await asyncio.to_thread(library.track, int(request.path_params["track_id"]))
    if data is None:
        return JSONResponse({"error": "parça bulunamadı"}, status_code=404)
    return JSONResponse(data)


async def search(request: Request) -> JSONResponse:
    query = request.query_params.get("q", "").strip()
    if not query:
        return JSONResponse({"artists": [], "albums": [], "tracks": []})
    return JSONResponse(await asyncio.to_thread(
        library.search, query, _int_param(request, "limit", 30, 1, 200)
    ))


async def get_home(request: Request) -> JSONResponse:
    """Ana ekran: öneri mixleri + yeni eklenenler + son çalınanlar."""
    mixes, recent, played, stats = await asyncio.gather(
        asyncio.to_thread(recommender.mixes),
        asyncio.to_thread(library.recently_added, 20),
        asyncio.to_thread(library.recently_played, 20),
        asyncio.to_thread(library.stats),
    )
    return JSONResponse({
        "mixes": mixes,
        "recentlyAdded": recent,
        "recentlyPlayed": played,
        "stats": stats,
    })


async def get_mix(request: Request) -> JSONResponse:
    tracks = await asyncio.to_thread(
        recommender.mix_track_dicts,
        request.path_params["mix_id"],
        _int_param(request, "count", 50, 1, 500),
    )
    return JSONResponse({"items": tracks})


async def get_genres(request: Request) -> JSONResponse:
    return JSONResponse({"items": await asyncio.to_thread(library.genres)})


async def get_stats(request: Request) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(library.stats))


async def set_rating(request: Request) -> JSONResponse:
    data = await _body(request)
    track_id = int(request.path_params["track_id"])
    await asyncio.to_thread(player.set_rating, track_id, int(data.get("rating", 0)))
    return _ok()


async def toggle_favourite(request: Request) -> JSONResponse:
    rating = await asyncio.to_thread(player.toggle_favourite)
    return _ok(rating=rating)


# --- Kapak görselleri ---------------------------------------------------------


async def get_art(request: Request) -> Response:
    key = request.path_params["key"]
    if not key.isalnum() or len(key) != 40:
        return PlainTextResponse("geçersiz anahtar", status_code=400)

    full_path, thumb_path = scanner.art_paths(key)
    path = thumb_path if request.query_params.get("s") == "t" else full_path
    if not path.exists():
        path = full_path if path is thumb_path else thumb_path
    if not path.exists():
        return PlainTextResponse("kapak yok", status_code=404)

    # İçerik adresli dosyalar; anahtar değişmeden içerik de değişmez.
    return FileResponse(path, media_type="image/jpeg", headers={
        "Cache-Control": "public, max-age=31536000, immutable",
    })


# --- Bakım --------------------------------------------------------------------


async def scan_endpoint(request: Request) -> JSONResponse:
    """GET durumu döndürür, POST yeni tarama başlatır."""
    if request.method == "GET":
        return JSONResponse(scanner.STATE.snapshot())

    data = await _body(request)
    scanner.scan_in_background(full=bool(data.get("full")))
    # MPD'nin kendi indeksi de tazelensin, yoksa yeni dosyaları çalamaz.
    try:
        await player.mpd.update()
    except Exception as exc:
        log.warning("MPD güncellemesi başlatılamadı: %s", exc)
    return _ok(scan=scanner.STATE.snapshot())


async def system_action(request: Request) -> JSONResponse:
    action = request.path_params["action"]
    commands = {
        "poweroff": ["sudo", "-n", "systemctl", "poweroff"],
        "reboot": ["sudo", "-n", "systemctl", "reboot"],
    }
    command = commands.get(action)
    if command is None:
        return JSONResponse({"error": "bilinmeyen işlem"}, status_code=404)
    try:
        subprocess.Popen(command)
    except OSError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return _ok()


async def healthz(request: Request) -> PlainTextResponse:
    alive = await player.mpd.ping()
    return PlainTextResponse("ok" if alive else "mpd yok", status_code=200 if alive else 503)


# --- Uygulama -----------------------------------------------------------------


async def on_startup() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    config.ensure_dirs()
    db.init_db()
    await player.start()
    if config.SCAN_ON_START:
        scanner.scan_in_background()


async def on_shutdown() -> None:
    await player.stop()


routes = [
    Route("/api/state", get_state),
    Route("/api/events", event_stream),
    Route("/api/player/{action}", player_action, methods=["POST"]),

    Route("/api/queue", get_queue),
    Route("/api/queue/{action}", queue_action, methods=["POST"]),

    Route("/api/play/album/{album_id:int}", play_album, methods=["POST"]),
    Route("/api/play/artist/{artist_id:int}", play_artist, methods=["POST"]),
    Route("/api/play/track/{track_id:int}", play_track, methods=["POST"]),
    Route("/api/play/mix/{mix_id:path}", play_mix, methods=["POST"]),
    Route("/api/play/radio/{track_id:int}", play_radio, methods=["POST"]),
    Route("/api/play/shuffle", smart_shuffle, methods=["POST"]),

    Route("/api/home", get_home),
    Route("/api/albums", get_albums),
    Route("/api/albums/{album_id:int}", get_album),
    Route("/api/artists", get_artists),
    Route("/api/artists/{artist_id:int}", get_artist),
    Route("/api/tracks/{track_id:int}", get_track),
    Route("/api/tracks/{track_id:int}/rating", set_rating, methods=["POST"]),
    Route("/api/favourite", toggle_favourite, methods=["POST"]),
    Route("/api/search", search),
    Route("/api/mixes/{mix_id:path}", get_mix),
    Route("/api/genres", get_genres),
    Route("/api/stats", get_stats),

    Route("/api/art/{key}", get_art),

    Route("/api/scan", scan_endpoint, methods=["GET", "POST"]),
    Route("/api/system/{action}", system_action, methods=["POST"]),
    Route("/healthz", healthz),
]

if config.UI_DIR.is_dir():
    routes.append(Mount("/", app=StaticFiles(directory=str(config.UI_DIR), html=True), name="ui"))

app = Starlette(
    routes=routes,
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
    middleware=[
        # Cihaz ev ağında; telefondan/laptoptan doğrudan erişilebilsin.
        Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]),
    ],
)
