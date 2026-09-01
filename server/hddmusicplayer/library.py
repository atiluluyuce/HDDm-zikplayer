"""Kütüphane sorguları: gezinme, arama, istatistik.

Tüm okuma yolları burada toplanır; API ve panel arayüzü aynı fonksiyonları
kullanır, böylece iki arayüz arasında davranış farkı oluşmaz.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Sequence

from . import db

_WORD_RE = re.compile(r"\w+", re.UNICODE)

_TRACK_SELECT = """
SELECT t.id, t.path, t.title, t.artist, t.artist_id, t.album_id, t.genre, t.year,
       t.track_no, t.disc_no, t.duration, t.bitrate, t.art_key, t.play_count,
       t.skip_count, t.last_played, t.rating, al.name AS album
FROM tracks t
LEFT JOIN albums al ON al.id = t.album_id
"""

_ALBUM_SELECT = """
SELECT a.id, a.name, a.album_artist, a.artist_id, a.year, a.genre, a.art_key,
       a.track_count, a.duration, a.added_at, ar.name AS artist
FROM albums a
LEFT JOIN artists ar ON ar.id = a.artist_id
"""

ALBUM_SORTS = {
    "recent": "a.added_at DESC, a.sort_key",
    "name": "a.sort_key",
    "artist": "ar.sort_key, a.year, a.sort_key",
    "year": "a.year DESC, a.sort_key",
    "random": "RANDOM()",
}


# --- Serileştirme -------------------------------------------------------------


def track_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "path": row["path"] if "path" in keys else None,
        "title": row["title"],
        "artist": row["artist"],
        "artistId": row["artist_id"] if "artist_id" in keys else None,
        "album": row["album"] if "album" in keys else None,
        "albumId": row["album_id"] if "album_id" in keys else None,
        "genre": row["genre"] if "genre" in keys else None,
        "year": row["year"] if "year" in keys else None,
        "trackNo": row["track_no"] if "track_no" in keys else None,
        "discNo": row["disc_no"] if "disc_no" in keys else None,
        "duration": row["duration"],
        "bitrate": row["bitrate"] if "bitrate" in keys else None,
        "art": row["art_key"],
        "playCount": row["play_count"] if "play_count" in keys else 0,
        "lastPlayed": row["last_played"] if "last_played" in keys else None,
        "rating": row["rating"] if "rating" in keys else 0,
    }


def album_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    # Albümün sanatçısı önce artists tablosundan, o yoksa etiketten gelen
    # album_artist alanından alınır (derlemelerde artist_id boş olabiliyor).
    artist = row["artist"] if "artist" in keys else None
    return {
        "id": row["id"],
        "name": row["name"],
        "artist": artist or row["album_artist"],
        "artistId": row["artist_id"],
        "year": row["year"],
        "genre": row["genre"] if "genre" in keys else None,
        "art": row["art_key"],
        "trackCount": row["track_count"],
        "duration": row["duration"],
    }


def artist_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "art": row["art_key"],
        "trackCount": row["track_count"],
        "albumCount": row["album_count"],
    }


# --- Albümler -----------------------------------------------------------------


def albums(
    offset: int = 0,
    limit: int = 100,
    sort: str = "artist",
    artist_id: int | None = None,
    genre: str | None = None,
) -> list[dict[str, Any]]:
    where, params = [], []
    if artist_id is not None:
        # Sanatçının kendi albümleri + sadece konuk olduğu albümler.
        where.append(
            "(a.artist_id = ? OR a.id IN (SELECT DISTINCT album_id FROM tracks WHERE artist_id = ?))"
        )
        params += [artist_id, artist_id]
    if genre:
        where.append("a.genre = ?")
        params.append(genre)

    sql = _ALBUM_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {ALBUM_SORTS.get(sort, ALBUM_SORTS['artist'])} LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [album_dict(r) for r in db.query(sql, params)]


def album_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM albums")
    return row["n"] if row else 0


def album(album_id: int) -> dict[str, Any] | None:
    row = db.query_one(_ALBUM_SELECT + " WHERE a.id = ?", (album_id,))
    if row is None:
        return None
    data = album_dict(row)
    data["tracks"] = album_tracks(album_id)
    return data


def album_tracks(album_id: int) -> list[dict[str, Any]]:
    rows = db.query(
        _TRACK_SELECT + " WHERE t.album_id = ? ORDER BY IFNULL(t.disc_no,1), IFNULL(t.track_no,0), t.path",
        (album_id,),
    )
    return [track_dict(r) for r in rows]


# --- Sanatçılar ---------------------------------------------------------------


def artists(offset: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT id, name, art_key, track_count, album_count FROM artists "
        "WHERE track_count > 0 ORDER BY sort_key LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return [artist_dict(r) for r in rows]


def artist_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM artists WHERE track_count > 0")
    return row["n"] if row else 0


def artist(artist_id: int) -> dict[str, Any] | None:
    row = db.query_one(
        "SELECT id, name, art_key, track_count, album_count FROM artists WHERE id = ?",
        (artist_id,),
    )
    if row is None:
        return None
    data = artist_dict(row)
    data["albums"] = albums(artist_id=artist_id, sort="year", limit=500)
    return data


def artist_tracks(artist_id: int, limit: int = 1000) -> list[dict[str, Any]]:
    rows = db.query(
        _TRACK_SELECT + " WHERE t.artist_id = ? "
        "ORDER BY t.year, al.sort_key, IFNULL(t.disc_no,1), IFNULL(t.track_no,0) LIMIT ?",
        (artist_id, limit),
    )
    return [track_dict(r) for r in rows]


# --- Parçalar -----------------------------------------------------------------


def track(track_id: int) -> dict[str, Any] | None:
    row = db.query_one(_TRACK_SELECT + " WHERE t.id = ?", (track_id,))
    return track_dict(row) if row else None


def track_by_path(path: str) -> dict[str, Any] | None:
    row = db.query_one(_TRACK_SELECT + " WHERE t.path = ?", (path,))
    return track_dict(row) if row else None


def tracks_by_ids(ids: Sequence[int]) -> list[dict[str, Any]]:
    """Verilen sırayı koruyarak parçaları döndürür."""
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = db.query(_TRACK_SELECT + f" WHERE t.id IN ({placeholders})", list(ids))
    by_id = {r["id"]: track_dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def paths_for_ids(ids: Sequence[int]) -> list[str]:
    """MPD'ye verilecek göreli yolları, istenen sırayla döndürür."""
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = db.query(f"SELECT id, path FROM tracks WHERE id IN ({placeholders})", list(ids))
    by_id = {r["id"]: r["path"] for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def set_rating(track_id: int, rating: int) -> None:
    """rating: 1 sevilen, 0 nötr, -1 yasaklı (öneri motoru yasaklıyı hiç seçmez)."""
    rating = max(-1, min(1, int(rating)))
    conn = db.connect()
    with conn:
        conn.execute("UPDATE tracks SET rating = ? WHERE id = ?", (rating, track_id))


# --- Arama --------------------------------------------------------------------


def _fts_query(text: str) -> str | None:
    """Serbest metni güvenli bir FTS5 önek sorgusuna çevirir.

    Kullanıcı girdisi doğrudan FTS5'e verilirse tırnak, yıldız, NOT gibi
    işaretler sözdizimi hatası fırlatır. Sadece kelimeleri alıp her birini
    tırnaklayarak önek eşlemesi yapıyoruz.
    """
    tokens = _WORD_RE.findall(db.fold(text))
    if not tokens:
        return None
    return " ".join(f'"{token}"*' for token in tokens)


def search(text: str, limit: int = 40) -> dict[str, list[dict[str, Any]]]:
    query = _fts_query(text)
    empty: dict[str, list[dict[str, Any]]] = {"artists": [], "albums": [], "tracks": []}
    if not query:
        return empty

    try:
        rows = db.query(
            "SELECT kind, ref_id FROM search WHERE search MATCH ? ORDER BY rank LIMIT ?",
            (query, limit * 4),
        )
    except sqlite3.OperationalError:
        return empty

    buckets: dict[str, list[int]] = {"artist": [], "album": [], "track": []}
    for row in rows:
        bucket = buckets.get(row["kind"])
        if bucket is not None and len(bucket) < limit:
            bucket.append(row["ref_id"])

    result = dict(empty)
    if buckets["artist"]:
        placeholders = ",".join("?" * len(buckets["artist"]))
        result["artists"] = [
            artist_dict(r) for r in db.query(
                "SELECT id, name, art_key, track_count, album_count FROM artists "
                f"WHERE id IN ({placeholders}) ORDER BY sort_key", buckets["artist"],
            )
        ]
    if buckets["album"]:
        placeholders = ",".join("?" * len(buckets["album"]))
        result["albums"] = [
            album_dict(r) for r in db.query(
                _ALBUM_SELECT + f" WHERE a.id IN ({placeholders}) ORDER BY ar.sort_key, a.year",
                buckets["album"],
            )
        ]
    if buckets["track"]:
        result["tracks"] = tracks_by_ids(buckets["track"])
    return result


# --- Keşif listeleri ----------------------------------------------------------


def genres(limit: int = 100) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT genre AS name, COUNT(*) AS track_count FROM tracks "
        "WHERE genre IS NOT NULL AND genre <> '' GROUP BY genre "
        "ORDER BY track_count DESC LIMIT ?",
        (limit,),
    )
    return [{"name": r["name"], "trackCount": r["track_count"]} for r in rows]


def recently_added(limit: int = 30) -> list[dict[str, Any]]:
    rows = db.query(
        _ALBUM_SELECT + " WHERE a.added_at IS NOT NULL ORDER BY a.added_at DESC LIMIT ?",
        (limit,),
    )
    return [album_dict(r) for r in rows]


def recently_played(limit: int = 30) -> list[dict[str, Any]]:
    rows = db.query(
        _TRACK_SELECT + " WHERE t.last_played IS NOT NULL ORDER BY t.last_played DESC LIMIT ?",
        (limit,),
    )
    return [track_dict(r) for r in rows]


def most_played(limit: int = 30) -> list[dict[str, Any]]:
    rows = db.query(
        _TRACK_SELECT + " WHERE t.play_count > 0 ORDER BY t.play_count DESC, t.last_played DESC LIMIT ?",
        (limit,),
    )
    return [track_dict(r) for r in rows]


def favourites(limit: int = 200) -> list[dict[str, Any]]:
    rows = db.query(
        _TRACK_SELECT + " WHERE t.rating > 0 ORDER BY t.last_played DESC, t.id LIMIT ?",
        (limit,),
    )
    return [track_dict(r) for r in rows]


def stats() -> dict[str, Any]:
    row = db.query_one("""
        SELECT (SELECT COUNT(*) FROM tracks)  AS tracks,
               (SELECT COUNT(*) FROM albums)  AS albums,
               -- artists.track_count taramanın sonunda toplu hesaplanıyor; tarama
               -- sürerken hepsi 0 olduğu için sayaç 0 görünüyordu. Doğrudan
               -- parçalardan sayıyoruz: tracks_artist indeksi var, maliyeti düşük.
               (SELECT COUNT(DISTINCT artist_id) FROM tracks WHERE artist_id IS NOT NULL) AS artists,
               (SELECT IFNULL(SUM(duration), 0) FROM tracks) AS duration,
               (SELECT IFNULL(SUM(size), 0) FROM tracks)     AS bytes,
               (SELECT COUNT(*) FROM plays)   AS plays,
               (SELECT COUNT(*) FROM tracks WHERE rating > 0) AS favourites,
               (SELECT COUNT(*) FROM tracks WHERE play_count = 0) AS unplayed
    """)
    data = dict(row) if row else {}
    data["lastScan"] = float(db.get_pref("last_scan") or 0) or None
    return data
