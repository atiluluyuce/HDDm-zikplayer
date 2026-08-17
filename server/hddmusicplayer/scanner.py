"""Kütüphane tarayıcısı: dosya sistemi -> SQLite indeksi.

Artımlı çalışır: dosyanın mtime + boyutu değişmediyse etiketleri yeniden okunmaz.
İlk tarama 500 GB'lik bir arşiv için USB HDD üzerinden uzun sürer (parça başına
~3-5 ms), sonraki taramalar saniyeler mertebesindedir.

Kapak görselleri içerik özetine (sha1) göre saklanır; aynı kapağı paylaşan bütün
parçalar tek bir dosyayı işaret eder.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import config, db

log = logging.getLogger(__name__)

try:
    import mutagen
except ImportError:  # pragma: no cover - kurulum eksikse tarama kapanır
    mutagen = None
    log.error("mutagen bulunamadı; kütüphane taraması devre dışı")

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None
    log.warning("Pillow bulunamadı; kapak görselleri işlenmeyecek")


_NUM_RE = re.compile(r"(\d+)")
_YEAR_RE = re.compile(r"(\d{4})")


# --- Tarama durumu (arayüze SSE ile aktarılır) --------------------------------


@dataclass
class ScanState:
    running: bool = False
    phase: str = "bosta"          # bosta | dosyalar | etiketler | ozet | bitti | hata
    total: int = 0
    processed: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    current: str = ""
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = {
                k: v for k, v in self.__dict__.items()
                if not k.startswith("_")
            }
        if data["total"]:
            data["progress"] = round(data["processed"] / data["total"], 4)
        else:
            data["progress"] = 0.0
        return data

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, value)


STATE = ScanState()
_scan_lock = threading.Lock()


# --- Etiket okuma -------------------------------------------------------------


def _first(value: Any) -> str | None:
    """Mutagen değerlerini tek bir string'e indirger."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    text = str(value).strip()
    return text or None


def _parse_int(value: Any) -> int | None:
    """'3', '3/12', (3, 12) gibi biçimlerden ilk sayıyı çeker."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if isinstance(value, (list, tuple)) and value:  # MP4 trkn -> ((3, 12),)
        value = value[0]
    if isinstance(value, int):
        return value or None
    match = _NUM_RE.search(str(value))
    return int(match.group(1)) if match else None


def _parse_year(value: Any) -> int | None:
    text = _first(value)
    if not text:
        return None
    match = _YEAR_RE.search(text)
    if not match:
        return None
    year = int(match.group(1))
    return year if 1900 <= year <= 2100 else None


#: Vorbis/MP4/ID3 alan adlarının ortak isimlere eşlenmesi.
_TAG_ALIASES = {
    "title": ("TIT2", "©nam", "title", "Title"),
    "artist": ("TPE1", "©ART", "artist", "Author"),
    "album_artist": ("TPE2", "aART", "albumartist", "album artist", "WM/AlbumArtist"),
    "album": ("TALB", "©alb", "album", "WM/AlbumTitle"),
    "genre": ("TCON", "©gen", "genre", "WM/Genre"),
    "date": ("TDRC", "TYER", "TDAT", "©day", "date", "year", "WM/Year"),
    "track_no": ("TRCK", "trkn", "tracknumber", "WM/TrackNumber"),
    "disc_no": ("TPOS", "disk", "discnumber", "WM/PartOfSet"),
}


def _lookup(tags: Any, names: tuple[str, ...]) -> Any:
    if tags is None:
        return None
    for name in names:
        try:
            value = tags[name]
        except (KeyError, TypeError, ValueError):
            continue
        if value not in (None, "", []):
            return value
    return None


def _read_tags(mf: Any) -> dict[str, Any]:
    tags = getattr(mf, "tags", None)
    info = getattr(mf, "info", None)

    def pick(field_name: str) -> Any:
        return _lookup(tags, _TAG_ALIASES[field_name])

    return {
        "title": _first(pick("title")),
        "artist": _first(pick("artist")),
        "album_artist": _first(pick("album_artist")),
        "album": _first(pick("album")),
        "genre": _first(pick("genre")),
        "year": _parse_year(pick("date")),
        "track_no": _parse_int(pick("track_no")),
        "disc_no": _parse_int(pick("disc_no")),
        "duration": float(getattr(info, "length", 0.0) or 0.0),
        "bitrate": int(getattr(info, "bitrate", 0) or 0) // 1000,
    }


def _embedded_art(mf: Any) -> bytes | None:
    """Dosyaya gömülü kapak görselini çıkarır (ID3 APIC / MP4 covr / FLAC)."""
    tags = getattr(mf, "tags", None)

    # FLAC ve bazı OGG dosyaları
    pictures = getattr(mf, "pictures", None)
    if pictures:
        return bytes(pictures[0].data)

    if tags is None:
        return None

    # ID3 (MP3, AIFF)
    getall = getattr(tags, "getall", None)
    if callable(getall):
        frames = getall("APIC")
        if frames:
            # Varsa "front cover" (tip 3) tercih edilir.
            frames = sorted(frames, key=lambda f: 0 if getattr(f, "type", 0) == 3 else 1)
            return bytes(frames[0].data)

    # MP4 / M4A
    try:
        covr = tags["covr"]
    except (KeyError, TypeError, ValueError):
        covr = None
    if covr:
        return bytes(covr[0])

    return None


def _folder_art(directory: Path) -> bytes | None:
    """Klasördeki cover.jpg / folder.jpg gibi dosyaları arar."""
    for name in config.COVER_FILENAMES:
        candidate = directory / name
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate.read_bytes()
        except OSError:
            continue
    return None


# --- Kapak görseli önbelleği --------------------------------------------------


def art_paths(key: str) -> tuple[Path, Path]:
    """(tam boy, küçük) yollarını döndürür."""
    bucket = config.ART_DIR / key[:2]
    return bucket / f"{key}.jpg", bucket / f"{key}_t.jpg"


def store_art(data: bytes | None) -> str | None:
    """Görseli önbelleğe alır ve anahtarını döndürür. Zaten varsa yeniden üretmez."""
    if not data or Image is None:
        return None

    key = hashlib.sha1(data).hexdigest()
    full_path, thumb_path = art_paths(key)
    if full_path.exists() and thumb_path.exists():
        return key

    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            full_path.parent.mkdir(parents=True, exist_ok=True)

            full = img.copy()
            full.thumbnail((config.ART_FULL_SIZE, config.ART_FULL_SIZE), Image.LANCZOS)
            full.save(full_path, "JPEG", quality=config.ART_QUALITY, optimize=True)

            thumb = img.copy()
            thumb.thumbnail((config.ART_THUMB_SIZE, config.ART_THUMB_SIZE), Image.LANCZOS)
            thumb.save(thumb_path, "JPEG", quality=config.ART_QUALITY, optimize=True)
    except Exception as exc:  # bozuk/desteklenmeyen görseller taramayı durdurmasın
        log.debug("kapak işlenemedi: %s", exc)
        return None

    return key


# --- Sanatçı / albüm kimlikleri ----------------------------------------------


class _IdCache:
    """Tarama boyunca sanatçı ve albüm kimliklerini bellekte tutar.

    Parça başına iki SELECT yapmak yerine sözlükten okur; 50 bin parçalık bir
    kütüphanede tarama süresini belirgin şekilde kısaltır.
    """

    def __init__(self, conn) -> None:
        self._conn = conn
        self._artists: dict[str, int] = {}
        self._albums: dict[tuple[str, int], int] = {}

    def artist(self, name: str | None) -> int | None:
        if not name:
            return None
        key = db.sort_key(name)
        if not key:
            return None
        cached = self._artists.get(key)
        if cached is not None:
            return cached

        self._conn.execute(
            "INSERT INTO artists(name, sort_key) VALUES(?, ?) ON CONFLICT(sort_key) DO NOTHING",
            (name, key),
        )
        row = self._conn.execute("SELECT id FROM artists WHERE sort_key = ?", (key,)).fetchone()
        artist_id = row["id"] if row else None
        if artist_id is not None:
            self._artists[key] = artist_id
        return artist_id

    def album(
        self,
        name: str | None,
        artist_id: int | None,
        album_artist: str | None,
        year: int | None,
        genre: str | None,
        now: float,
    ) -> int | None:
        if not name:
            return None
        key = db.sort_key(name)
        if not key:
            return None
        cache_key = (key, artist_id or 0)
        cached = self._albums.get(cache_key)
        if cached is not None:
            return cached

        self._conn.execute(
            "INSERT INTO albums(name, sort_key, artist_id, album_artist, year, genre, added_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(sort_key, IFNULL(artist_id, 0)) DO NOTHING",
            (name, key, artist_id, album_artist, year, genre, now),
        )
        row = self._conn.execute(
            "SELECT id FROM albums WHERE sort_key = ? AND IFNULL(artist_id, 0) = ?",
            (key, artist_id or 0),
        ).fetchone()
        album_id = row["id"] if row else None
        if album_id is not None:
            self._albums[cache_key] = album_id
        return album_id


# --- Dosya gezintisi ----------------------------------------------------------


def _walk_audio(root: Path) -> Iterator[tuple[Path, os.stat_result]]:
    """Ses dosyalarını dolaşır; okunamayan dizinleri atlar."""
    skip_dirs = {".git", ".Trash", "$RECYCLE.BIN", "System Volume Information", "@eaDir"}
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: log.debug("atlandı: %s", e)):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith("._")]
        for filename in filenames:
            if filename.startswith("._"):
                continue  # macOS resource fork artıkları
            if Path(filename).suffix.lower() not in config.AUDIO_EXTENSIONS:
                continue
            full = Path(dirpath) / filename
            try:
                yield full, full.stat()
            except OSError:
                continue


# --- Ana tarama ---------------------------------------------------------------


def scan(full: bool = False) -> dict[str, Any]:
    """Kütüphaneyi tarar. Aynı anda yalnızca bir tarama çalışabilir.

    full=True ise mtime kontrolü atlanır ve bütün etiketler yeniden okunur.
    """
    if not _scan_lock.acquire(blocking=False):
        log.info("tarama zaten çalışıyor")
        return STATE.snapshot()

    try:
        return _scan_locked(full)
    except Exception as exc:
        log.exception("tarama başarısız")
        STATE.update(running=False, phase="hata", error=str(exc), finished_at=time.time())
        return STATE.snapshot()
    finally:
        _scan_lock.release()


def _scan_locked(full: bool) -> dict[str, Any]:
    if mutagen is None:
        raise RuntimeError("mutagen kurulu değil")

    root = config.MUSIC_ROOT
    if not root.is_dir():
        raise RuntimeError(f"müzik dizini bulunamadı: {root}")

    started = time.time()
    STATE.update(
        running=True, phase="dosyalar", total=0, processed=0, added=0, updated=0,
        removed=0, current="", error="", started_at=started, finished_at=0.0,
    )

    conn = db.connect()
    conn.execute("UPDATE tracks SET seen = 0")
    conn.commit()

    # Mevcut kayıtları tek seferde belleğe al: dosya başına SELECT yapmayalım.
    known: dict[str, tuple[int, float, int]] = {
        row["path"]: (row["id"], row["mtime"] or 0.0, row["size"] or 0)
        for row in conn.execute("SELECT id, path, mtime, size FROM tracks")
    }

    files = list(_walk_audio(root))
    STATE.update(phase="etiketler", total=len(files))
    log.info("%d ses dosyası bulundu", len(files))

    cache = _IdCache(conn)
    #: Klasör bazlı kapak önbelleği — aynı albümün her parçası için diski
    #: yeniden taramamak adına.
    folder_art: dict[Path, str | None] = {}
    added = updated = processed = 0
    now = time.time()

    for path, stat in files:
        processed += 1
        if processed % 25 == 0:
            STATE.update(processed=processed, current=path.name, added=added, updated=updated)

        try:
            rel = str(path.relative_to(root))
        except ValueError:
            continue

        existing = known.pop(rel, None)
        if existing and not full:
            track_id, old_mtime, old_size = existing
            if abs(old_mtime - stat.st_mtime) < 1.0 and old_size == stat.st_size:
                conn.execute("UPDATE tracks SET seen = 1 WHERE id = ?", (track_id,))
                continue

        try:
            mf = mutagen.File(path)
        except Exception as exc:
            log.debug("okunamadı %s: %s", rel, exc)
            continue
        if mf is None:
            continue

        tags = _read_tags(mf)

        art_key = store_art(_embedded_art(mf))
        if art_key is None:
            directory = path.parent
            if directory not in folder_art:
                folder_art[directory] = store_art(_folder_art(directory))
            art_key = folder_art[directory]

        artist_name = tags["artist"] or tags["album_artist"]
        album_artist = tags["album_artist"] or artist_name
        # Albüm, albüm sanatçısına bağlanır: aynı isimli albümler farklı
        # sanatçılarda birbirine karışmasın, derlemeler de tek albümde toplansın.
        album_artist_id = cache.artist(album_artist)
        artist_id = cache.artist(artist_name)
        album_id = cache.album(
            tags["album"], album_artist_id, album_artist, tags["year"], tags["genre"], now,
        )
        title = tags["title"] or path.stem

        values = (
            title, artist_name, artist_id, album_id, tags["genre"], tags["year"],
            tags["track_no"], tags["disc_no"], tags["duration"], tags["bitrate"],
            stat.st_mtime, stat.st_size, art_key,
        )

        if existing:
            conn.execute(
                "UPDATE tracks SET title=?, artist=?, artist_id=?, album_id=?, genre=?, year=?, "
                "track_no=?, disc_no=?, duration=?, bitrate=?, mtime=?, size=?, art_key=?, seen=1 "
                "WHERE id=?",
                (*values, existing[0]),
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO tracks(title, artist, artist_id, album_id, genre, year, track_no, "
                "disc_no, duration, bitrate, mtime, size, art_key, path, added_at, seen) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (*values, rel, now),
            )
            added += 1

        if processed % 500 == 0:
            conn.commit()

    conn.commit()

    # Diskte kalmayan parçalar (known içinde artakalanlar) silinir.
    removed = 0
    if known:
        conn.executemany("DELETE FROM tracks WHERE id = ?", [(v[0],) for v in known.values()])
        removed = len(known)
    conn.execute("DELETE FROM tracks WHERE seen = 0")
    conn.commit()

    STATE.update(phase="ozet", processed=processed, added=added, updated=updated, removed=removed)
    _rebuild_aggregates(conn)
    _rebuild_search(conn)
    _prune_art(conn)

    STATE.update(
        running=False, phase="bitti", current="", finished_at=time.time(),
        processed=processed, added=added, updated=updated, removed=removed,
    )
    db.set_pref("last_scan", str(time.time()))
    log.info(
        "tarama bitti: %d eklendi, %d güncellendi, %d silindi (%.1f sn)",
        added, updated, removed, time.time() - started,
    )
    return STATE.snapshot()


def _rebuild_aggregates(conn) -> None:
    """Albüm/sanatçı sayaçlarını ve kapaklarını parçalardan yeniden türetir."""
    with conn:
        # Parçası kalmayan albüm ve sanatçıları temizle.
        conn.execute("DELETE FROM albums WHERE id NOT IN (SELECT DISTINCT album_id FROM tracks WHERE album_id IS NOT NULL)")
        conn.execute(
            "DELETE FROM artists WHERE id NOT IN ("
            "  SELECT DISTINCT artist_id FROM tracks WHERE artist_id IS NOT NULL"
            "  UNION SELECT DISTINCT artist_id FROM albums WHERE artist_id IS NOT NULL)"
        )

        conn.execute("""
            UPDATE albums SET
                track_count = (SELECT COUNT(*) FROM tracks t WHERE t.album_id = albums.id),
                duration    = (SELECT IFNULL(SUM(t.duration), 0) FROM tracks t WHERE t.album_id = albums.id),
                year        = COALESCE(year, (SELECT MIN(t.year) FROM tracks t WHERE t.album_id = albums.id)),
                genre       = COALESCE(genre, (SELECT t.genre FROM tracks t WHERE t.album_id = albums.id AND t.genre IS NOT NULL LIMIT 1)),
                added_at    = COALESCE(added_at, (SELECT MIN(t.added_at) FROM tracks t WHERE t.album_id = albums.id)),
                art_key     = (SELECT t.art_key FROM tracks t
                               WHERE t.album_id = albums.id AND t.art_key IS NOT NULL
                               ORDER BY t.disc_no, t.track_no LIMIT 1)
        """)

        conn.execute("""
            UPDATE artists SET
                track_count = (SELECT COUNT(*) FROM tracks t WHERE t.artist_id = artists.id),
                album_count = (SELECT COUNT(*) FROM albums a WHERE a.artist_id = artists.id),
                art_key     = (SELECT a.art_key FROM albums a
                               WHERE a.artist_id = artists.id AND a.art_key IS NOT NULL
                               ORDER BY a.year DESC LIMIT 1)
        """)


def _rebuild_search(conn) -> None:
    """FTS indeksini sıfırdan kurar (artımlı tutmaktan daha ucuz ve güvenli).

    İndekse metnin fold()'lanmış hâli yazılır. FTS5'in unicode61 tokenleştiricisi
    Türkçe 'ı' harfini 'i' ile eşleştirmez (ayrı bir kod noktası, aksan değil);
    fold() bunu ve diğer Türkçe harfleri ASCII'ye indirdiği için "sarki" araması
    "Şarkı" kaydını da bulur. Görüntülenecek veriler zaten ref_id ile asıl
    tablolardan çekiliyor, indekste okunabilir metin tutmaya gerek yok.
    """
    with conn:
        conn.execute("DELETE FROM search")
        conn.execute("""
            INSERT INTO search(text, kind, ref_id)
            SELECT fold(IFNULL(t.title,'') || ' ' || IFNULL(t.artist,'') || ' ' || IFNULL(al.name,'')),
                   'track', t.id
            FROM tracks t LEFT JOIN albums al ON al.id = t.album_id
        """)
        conn.execute("""
            INSERT INTO search(text, kind, ref_id)
            SELECT fold(IFNULL(name,'') || ' ' || IFNULL(album_artist,'')), 'album', id FROM albums
        """)
        conn.execute("INSERT INTO search(text, kind, ref_id) SELECT fold(name), 'artist', id FROM artists")
        conn.execute("INSERT INTO search(search) VALUES('optimize')")


def _prune_art(conn) -> None:
    """Artık hiçbir kaydın işaret etmediği kapak dosyalarını siler."""
    live = {
        row[0] for row in conn.execute(
            "SELECT art_key FROM tracks WHERE art_key IS NOT NULL "
            "UNION SELECT art_key FROM albums WHERE art_key IS NOT NULL "
            "UNION SELECT art_key FROM artists WHERE art_key IS NOT NULL"
        )
    }
    removed = 0
    for bucket in config.ART_DIR.iterdir() if config.ART_DIR.is_dir() else []:
        if not bucket.is_dir():
            continue
        for image in bucket.glob("*.jpg"):
            key = image.stem[:-2] if image.stem.endswith("_t") else image.stem
            if key not in live:
                try:
                    image.unlink()
                    removed += 1
                except OSError:
                    pass
    if removed:
        log.info("%d artık kapak dosyası silindi", removed)


def scan_in_background(full: bool = False) -> threading.Thread:
    """Taramayı ayrı bir iş parçacığında başlatır (HTTP isteğini bloklamamak için)."""
    thread = threading.Thread(target=scan, args=(full,), name="library-scan", daemon=True)
    thread.start()
    return thread
