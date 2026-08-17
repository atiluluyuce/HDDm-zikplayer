"""SQLite şeması ve bağlantı yönetimi.

Kütüphane indeksi, çalma geçmişi ve öneri motorunun öğrendiği sinyaller burada
tutulur. MPD'nin kendi veritabanı sadece çalma için kullanılır; gezinme, arama ve
öneri tamamen bu indeks üzerinden yapılır (MPD'nin sorgu dili bu iş için yavaş).
"""

from __future__ import annotations

import re
import sqlite3
import threading
import unicodedata
from typing import Any, Iterable

from . import config

SCHEMA_VERSION = 1

_local = threading.local()

# Türkçe karakterleri ASCII karşılıklarına indirger. Sıralama ve arama
# anahtarları bunun üzerinden üretilir ki "Şebnem" araması "sebnem" ile de bulsun.
_TR_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "I": "i",
    "ş": "s", "Ş": "s",
    "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u",
    "ö": "o", "Ö": "o",
    "ç": "c", "Ç": "c",
    "â": "a", "Â": "a", "î": "i", "Î": "i", "û": "u", "Û": "u",
})

_ARTICLE_RE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def fold(text: str | None) -> str:
    """Sıralama/arama için normalleştirilmiş anahtar üretir."""
    if not text:
        return ""
    s = text.translate(_TR_FOLD).casefold()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return _WS_RE.sub(" ", s).strip()


def sort_key(text: str | None) -> str:
    """fold() + baştaki İngilizce artikelleri atar ('The Cure' -> 'cure')."""
    s = fold(text)
    return _ARTICLE_RE.sub("", s) or s


SCHEMA = """
CREATE TABLE IF NOT EXISTS artists (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    sort_key    TEXT NOT NULL UNIQUE,
    art_key     TEXT,
    track_count INTEGER NOT NULL DEFAULT 0,
    album_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS albums (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    sort_key     TEXT NOT NULL,
    artist_id    INTEGER REFERENCES artists(id) ON DELETE SET NULL,
    album_artist TEXT,
    year         INTEGER,
    genre        TEXT,
    art_key      TEXT,
    track_count  INTEGER NOT NULL DEFAULT 0,
    duration     REAL NOT NULL DEFAULT 0,
    added_at     REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS albums_ident ON albums(sort_key, IFNULL(artist_id, 0));
CREATE INDEX IF NOT EXISTS albums_artist ON albums(artist_id);
CREATE INDEX IF NOT EXISTS albums_added  ON albums(added_at DESC);

CREATE TABLE IF NOT EXISTS tracks (
    id          INTEGER PRIMARY KEY,
    -- MUSIC_ROOT'a göreli yol. MPD'nin uri'siyle birebir aynı olmak zorunda.
    path        TEXT NOT NULL UNIQUE,
    title       TEXT,
    artist      TEXT,
    artist_id   INTEGER REFERENCES artists(id) ON DELETE SET NULL,
    album_id    INTEGER REFERENCES albums(id) ON DELETE SET NULL,
    genre       TEXT,
    year        INTEGER,
    track_no    INTEGER,
    disc_no     INTEGER,
    duration    REAL,
    bitrate     INTEGER,
    mtime       REAL,
    size        INTEGER,
    art_key     TEXT,
    added_at    REAL,
    play_count  INTEGER NOT NULL DEFAULT 0,
    skip_count  INTEGER NOT NULL DEFAULT 0,
    last_played REAL,
    rating      INTEGER NOT NULL DEFAULT 0,   -- 1 sevilen, -1 yasaklı
    seen        INTEGER NOT NULL DEFAULT 1    -- tarama sırasında işaretlenir
);
CREATE INDEX IF NOT EXISTS tracks_album  ON tracks(album_id, disc_no, track_no);
CREATE INDEX IF NOT EXISTS tracks_artist ON tracks(artist_id);
CREATE INDEX IF NOT EXISTS tracks_genre  ON tracks(genre);
CREATE INDEX IF NOT EXISTS tracks_played ON tracks(last_played);
CREATE INDEX IF NOT EXISTS tracks_rating ON tracks(rating);

-- Çalma geçmişi. Öneri motorunun ana besleyicisi.
CREATE TABLE IF NOT EXISTS plays (
    id        INTEGER PRIMARY KEY,
    track_id  INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    played_at REAL NOT NULL,
    hour      INTEGER NOT NULL,      -- 0-23, saat bazlı zevk profili için
    weekday   INTEGER NOT NULL,      -- 0=Pazartesi
    completed INTEGER NOT NULL,      -- 1 sonuna kadar, 0 atlandı
    source    TEXT                   -- album | artist | search | smart | radio | queue
);
CREATE INDEX IF NOT EXISTS plays_track ON plays(track_id);
CREATE INDEX IF NOT EXISTS plays_time  ON plays(played_at DESC);
CREATE INDEX IF NOT EXISTS plays_hour  ON plays(hour);

-- Ardışık çalınan parça çiftleri. "Bunu dinleyince şunu da dinliyor" sinyali.
CREATE TABLE IF NOT EXISTS transitions (
    prev_id INTEGER NOT NULL,
    next_id INTEGER NOT NULL,
    count   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (prev_id, next_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS prefs (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Birleşik arama indeksi: parça, albüm ve sanatçı aynı tabloda.
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
    text,
    kind    UNINDEXED,
    ref_id  UNINDEXED,
    tokenize = "unicode61 remove_diacritics 2"
);
"""


def connect() -> sqlite3.Connection:
    """Bu iş parçacığına ait bağlantıyı döndürür (yoksa açar)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn

    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")       # okuma yazmayı bloklamasın
    conn.execute("PRAGMA synchronous=NORMAL")     # SD kart ömrü için
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-8000")       # ~8 MB sayfa önbelleği
    conn.create_function("fold", 1, fold, deterministic=True)
    _local.conn = conn
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def init_db() -> None:
    conn = connect()
    with conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO prefs(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )


def get_pref(key: str, default: str | None = None) -> str | None:
    row = connect().execute("SELECT value FROM prefs WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_pref(key: str, value: str) -> None:
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO prefs(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, tuple(params)).fetchall()


def query_one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return connect().execute(sql, tuple(params)).fetchone()


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]
