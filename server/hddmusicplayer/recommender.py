"""Öneri ve akıllı karıştırma motoru.

Tamamen yerel çalışır — internet, Last.fm veya harici bir servis gerekmez.
Kullandığı sinyaller:

  * etiketler       : sanatçı, albüm, tür, yıl
  * çalma geçmişi   : ne zaman, kaç kez, sonuna kadar mı yoksa atlanarak mı
  * geçişler        : hangi parçadan sonra hangisi çalınmış (birlikte anılma)
  * beğeni          : sevilen / yasaklı işaretleri
  * saat profili    : günün hangi saatinde hangi tür dinleniyor

Skorlama çarpımsaldır: her sinyal 1.0 etrafında bir katsayı üretir, hepsi
çarpılır. Sonuçtan seçim ağırlıklı ve tekrarsız yapılır (Gumbel/üstel anahtar
yöntemi), üstüne "aynı sanatçı arka arkaya gelmesin" gibi çeşitlilik kuralları
uygulanır.
"""

from __future__ import annotations

import logging
import math
import random
import time
from typing import Any, Iterable, Sequence

from . import config, db, library

log = logging.getLogger(__name__)

#: Skorlanacak aday havuzunun üst sınırı. Tüm kütüphaneyi Python'da skorlamak
#: yerine rastgele örneklem alınır; 50 bin parçalık arşivde bile sonuç
#: istatistiksel olarak aynı, maliyet ise Pi 3'te ~30 ms.
POOL_SIZE = 2500

_rng = random.Random()


def shuffle_ids(ids: list[int]) -> None:
    """Listeyi yerinde karıştırır (modül genelinde tek bir rastgelelik kaynağı)."""
    _rng.shuffle(ids)


_CANDIDATE_SELECT = """
SELECT t.id, t.artist_id, t.album_id, t.genre, t.year, t.duration,
       t.play_count, t.skip_count, t.last_played, t.rating
FROM tracks t
"""


# --- Geçmiş kaydı -------------------------------------------------------------


def record_play(track_id: int, completed: bool, source: str = "queue",
                previous_id: int | None = None) -> None:
    """Bir çalmayı geçmişe işler ve öğrenme sinyallerini günceller."""
    now = time.time()
    local = time.localtime(now)
    conn = db.connect()
    with conn:
        conn.execute(
            "INSERT INTO plays(track_id, played_at, hour, weekday, completed, source) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (track_id, now, local.tm_hour, local.tm_wday, int(completed), source),
        )
        if completed:
            conn.execute(
                "UPDATE tracks SET play_count = play_count + 1, last_played = ? WHERE id = ?",
                (now, track_id),
            )
        else:
            conn.execute(
                "UPDATE tracks SET skip_count = skip_count + 1, last_played = ? WHERE id = ?",
                (now, track_id),
            )
        # Geçiş sinyali yalnızca sonuna kadar dinlenmiş parçalar için anlamlı:
        # atlanan parçadan sonrasını "birlikte sevilen" saymak yanıltıcı olur.
        if previous_id and previous_id != track_id and completed:
            conn.execute(
                "INSERT INTO transitions(prev_id, next_id, count) VALUES(?, ?, 1) "
                "ON CONFLICT(prev_id, next_id) DO UPDATE SET count = count + 1",
                (previous_id, track_id),
            )


# --- Saat bazlı zevk profili --------------------------------------------------

_hour_profile_cache: tuple[float, int, dict[str, float]] = (0.0, -1, {})


def _hour_affinity() -> dict[str, float]:
    """Şu anki saat için tür -> katsayı eşlemesi.

    Bir türün bu saatteki payı genel payına bölünür. 1.0'dan büyükse "bu
    saatlerde daha çok dinlenen tür" demektir. Sonuç 10 dakika önbelleklenir.
    """
    global _hour_profile_cache
    now = time.time()
    hour = time.localtime(now).tm_hour
    cached_at, cached_hour, cached = _hour_profile_cache
    if cached_hour == hour and now - cached_at < 600:
        return cached

    total_plays = db.query_one("SELECT COUNT(*) AS n FROM plays")
    if not total_plays or total_plays["n"] < 60:
        # Yeterli geçmiş yok; bu sinyali devre dışı bırak.
        _hour_profile_cache = (now, hour, {})
        return {}

    # Saat penceresi: ±1 saat, gün dönümünü sarmalayarak.
    window = {(hour - 1) % 24, hour, (hour + 1) % 24}
    placeholders = ",".join("?" * len(window))

    overall = {
        r["genre"]: r["n"] for r in db.query(
            "SELECT t.genre AS genre, COUNT(*) AS n FROM plays p "
            "JOIN tracks t ON t.id = p.track_id WHERE t.genre IS NOT NULL GROUP BY t.genre"
        )
    }
    windowed = {
        r["genre"]: r["n"] for r in db.query(
            "SELECT t.genre AS genre, COUNT(*) AS n FROM plays p "
            "JOIN tracks t ON t.id = p.track_id "
            f"WHERE p.hour IN ({placeholders}) AND t.genre IS NOT NULL GROUP BY t.genre",
            sorted(window),
        )
    }

    overall_total = sum(overall.values()) or 1
    window_total = sum(windowed.values()) or 1
    profile: dict[str, float] = {}
    for genre, count in windowed.items():
        if count < 3:
            continue
        share = count / window_total
        baseline = overall.get(genre, 1) / overall_total
        if baseline <= 0:
            continue
        # 0.7 - 1.6 arasına sıkıştır: sinyal yön versin ama tek başına belirlemesin.
        profile[genre] = max(0.7, min(1.6, share / baseline))

    _hour_profile_cache = (now, hour, profile)
    return profile


# --- Skorlama -----------------------------------------------------------------


def _recency_factor(last_played: float | None, now: float) -> float:
    """Yakın zamanda çalınanı bastırır, uzun süredir çalınmayanı öne çıkarır."""
    if not last_played:
        return 1.25  # hiç çalınmamış
    hours = (now - last_played) / 3600.0
    if hours < 6:
        return 0.05
    if hours < 24:
        return 0.20
    if hours < 72:
        return 0.45
    if hours < 168:
        return 0.70
    if hours < 720:
        return 1.00
    return 1.25


def _skip_factor(play_count: int, skip_count: int) -> float:
    total = play_count + skip_count
    if total < 3:
        return 1.0
    ratio = skip_count / total
    if ratio > 0.6:
        return 0.25
    if ratio > 0.35:
        return 0.60
    return 1.0


def _duration_factor(duration: float | None) -> float:
    """Çok kısa (intro/skit) ve çok uzun parçaları karıştırmada geri plana atar."""
    if not duration:
        return 1.0
    if duration < 30:
        return 0.15
    if duration < 60:
        return 0.55
    if duration > 900:
        return 0.60
    return 1.0


#: Radyo modunda tamamen ilgisiz parçaların ağırlık tabanı. Sıfır değil — ara
#: sıra beklenmedik bir parçanın düşmesi listeyi canlı tutuyor — ama benzer bir
#: parçanın ~25-70 katı daha az.
SEED_FLOOR = 0.04
SEED_GAIN = 3.0


def _seed_factor(row, seed: dict[str, Any], transitions: dict[int, int]) -> float:
    """Radyo modunda tohum parçaya benzerlik katsayısı.

    Benzerlik önce 0..1+ aralığında toplanır, sonra tek seferde ölçeklenir.
    Katsayıları tek tek çarpmak yerine böyle yapılıyor: çarpımsal yaklaşımda
    "hiçbir ortak yanı yok" durumu 1.0'da kalıyor ve ilgisiz parçalar nötr
    sayıldığı için listeye doluşuyordu. Toplamalı benzerlik + düşük taban,
    ilgisiz parçayı açıkça cezalandırır.
    """
    similarity = 0.0
    if seed.get("artist_id") and row["artist_id"] == seed["artist_id"]:
        similarity += 0.55
    if seed.get("genre") and row["genre"] and row["genre"] == seed["genre"]:
        similarity += 0.35
    if seed.get("album_id") and row["album_id"] == seed["album_id"]:
        similarity += 0.10

    seed_year, row_year = seed.get("year"), row["year"]
    if seed_year and row_year:
        gap = abs(seed_year - row_year)
        if gap <= 3:
            similarity += 0.20
        elif gap <= 8:
            similarity += 0.10
        elif gap > 25:
            similarity -= 0.05

    count = transitions.get(row["id"], 0)
    if count:
        # Geçmişte gerçekten arka arkaya dinlenmiş olmak en güçlü sinyal.
        similarity += min(0.60, 0.25 * count)

    return SEED_FLOOR + max(0.0, similarity) * SEED_GAIN


def _score(row, now: float, hour_profile: dict[str, float],
           seed: dict[str, Any] | None, transitions: dict[int, int]) -> float:
    if row["rating"] < 0:
        return 0.0

    score = 1.0
    if row["rating"] > 0:
        score *= 2.5

    play_count = row["play_count"] or 0
    if play_count == 0:
        score *= 1.35                                   # keşfe teşvik
    else:
        score *= 1.0 + 0.06 * min(play_count, 10)       # tanıdıklık, doyuma ulaşan

    score *= _recency_factor(row["last_played"], now)
    score *= _skip_factor(play_count, row["skip_count"] or 0)
    score *= _duration_factor(row["duration"])

    if hour_profile and row["genre"]:
        score *= hour_profile.get(row["genre"], 1.0)

    if seed:
        score *= _seed_factor(row, seed, transitions)

    return score


# --- Seçim --------------------------------------------------------------------


def _sample(rows: Sequence[Any], scores: Sequence[float], count: int) -> list[int]:
    """Ağırlıklı, tekrarsız seçim + çeşitlilik kısıtları.

    Üstel anahtar yöntemi: her aday için key = -ln(U)/w üretilir; anahtarlara
    göre artan sıralama, ağırlıklarla orantılı tekrarsız bir örneklem verir.
    Sonra sıralı gezinirken "aynı sanatçı/albüm çok yakın olmasın" kuralları
    uygulanır. Kurallar yüzünden liste dolmazsa kısıtlar gevşetilerek ikinci
    tur yapılır.
    """
    keyed: list[tuple[float, int]] = []
    for index, weight in enumerate(scores):
        if weight <= 0:
            continue
        keyed.append((-math.log(max(_rng.random(), 1e-12)) / weight, index))
    keyed.sort()

    picked: list[int] = []
    picked_indices: set[int] = set()
    recent_artists: list[int | None] = []
    recent_albums: list[int | None] = []

    for artist_gap, album_gap in (
        (config.DIVERSITY_ARTIST_GAP, config.DIVERSITY_ALBUM_GAP),
        (2, 3),
        (0, 0),
    ):
        for _, index in keyed:
            if len(picked) >= count:
                break
            if index in picked_indices:
                continue
            row = rows[index]
            if artist_gap and row["artist_id"] and row["artist_id"] in recent_artists[-artist_gap:]:
                continue
            if album_gap and row["album_id"] and row["album_id"] in recent_albums[-album_gap:]:
                continue

            picked.append(row["id"])
            picked_indices.add(index)
            recent_artists.append(row["artist_id"])
            recent_albums.append(row["album_id"])

        if len(picked) >= count:
            break

    return picked


def _fetch_pool(exclude: Iterable[int], extra_sql: str = "", extra_params: Sequence = ()) -> list:
    exclude_ids = list(dict.fromkeys(exclude))
    where = ["t.rating >= 0"]
    params: list[Any] = []

    if exclude_ids:
        # Çok uzun IN listeleri SQLite'ın değişken sınırını zorlar; son 400 yeter.
        recent = exclude_ids[-400:]
        where.append(f"t.id NOT IN ({','.join('?' * len(recent))})")
        params += recent

    if extra_sql:
        where.append(extra_sql)
        params += list(extra_params)

    sql = _CANDIDATE_SELECT + " WHERE " + " AND ".join(where) + " ORDER BY RANDOM() LIMIT ?"
    params.append(POOL_SIZE)
    return db.query(sql, params)


def _targeted_rows(seed: dict[str, Any], exclude: set[int]) -> list:
    """Radyo modunda tohumla doğrudan ilişkili adayları havuza ekler.

    Rastgele örneklem tek başına yeterli değil: 50 bin parçalık arşivde aynı
    sanatçının 2500'lük örnekleme düşme ihtimali düşük. Bu yüzden sanatçı, tür
    ve geçiş ilişkisi olan parçalar ayrıca çekilir.
    """
    rows: list = []
    seen: set[int] = set(exclude)

    def add(new_rows: Iterable[Any]) -> None:
        for row in new_rows:
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)

    if seed.get("artist_id"):
        add(db.query(
            _CANDIDATE_SELECT + " WHERE t.artist_id = ? AND t.rating >= 0 ORDER BY RANDOM() LIMIT 120",
            (seed["artist_id"],),
        ))
    if seed.get("genre"):
        add(db.query(
            _CANDIDATE_SELECT + " WHERE t.genre = ? AND t.rating >= 0 ORDER BY RANDOM() LIMIT 300",
            (seed["genre"],),
        ))
    if seed.get("id"):
        add(db.query(
            _CANDIDATE_SELECT +
            " JOIN transitions tr ON tr.next_id = t.id "
            " WHERE tr.prev_id = ? AND t.rating >= 0 ORDER BY tr.count DESC LIMIT 60",
            (seed["id"],),
        ))
    return rows


# --- Genel arayüz -------------------------------------------------------------


def smart_shuffle(count: int = 15, seed_track_id: int | None = None,
                  exclude: Iterable[int] = ()) -> list[int]:
    """Akıllı karıştırma. seed_track_id verilirse "benzerlerini çal" moduna geçer."""
    now = time.time()
    exclude_set = set(exclude)
    if seed_track_id:
        exclude_set.add(seed_track_id)

    seed: dict[str, Any] | None = None
    transitions: dict[int, int] = {}
    rows = list(_fetch_pool(exclude_set))

    if seed_track_id:
        seed_row = db.query_one(
            "SELECT id, artist_id, album_id, genre, year FROM tracks WHERE id = ?",
            (seed_track_id,),
        )
        if seed_row:
            seed = dict(seed_row)
            transitions = {
                r["next_id"]: r["count"] for r in db.query(
                    "SELECT next_id, count FROM transitions WHERE prev_id = ?", (seed_track_id,)
                )
            }
            existing = {r["id"] for r in rows}
            rows += _targeted_rows(seed, exclude_set | existing)

    if not rows:
        return []

    hour_profile = _hour_affinity()
    scores = [_score(row, now, hour_profile, seed, transitions) for row in rows]
    return _sample(rows, scores, count)


def radio(track_id: int, count: int = 25) -> list[int]:
    """Bir parçadan yola çıkıp benzerlerinden liste üretir."""
    return smart_shuffle(count=count, seed_track_id=track_id)


def next_up(count: int | None = None, exclude: Iterable[int] = (),
            seed_track_id: int | None = None) -> list[int]:
    """Otomatik kuyruk doldurma için parça önerir."""
    return smart_shuffle(
        count=count or config.AUTOQUEUE_BATCH,
        seed_track_id=seed_track_id,
        exclude=exclude,
    )


# --- Hazır mixler -------------------------------------------------------------


def _sample_art(sql: str, params: Sequence = ()) -> str | None:
    row = db.query_one(sql, params)
    return row["art_key"] if row and row["art_key"] else None


def mixes() -> list[dict[str, Any]]:
    """Ana ekranda gösterilecek hazır listeler. Boş olanlar elenir."""
    result: list[dict[str, Any]] = []

    def add(mix_id: str, title: str, subtitle: str, count_sql: str, art_sql: str,
            params: Sequence = ()) -> None:
        row = db.query_one(count_sql, params)
        total = row["n"] if row else 0
        if total < 5:
            return
        result.append({
            "id": mix_id,
            "title": title,
            "subtitle": subtitle.format(n=total),
            "count": total,
            "art": _sample_art(art_sql, params),
        })

    add(
        "smart", "Akıllı Karıştır", "Zevkine göre seçilmiş",
        "SELECT COUNT(*) AS n FROM tracks WHERE rating >= 0",
        "SELECT art_key FROM tracks WHERE art_key IS NOT NULL AND rating >= 0 ORDER BY RANDOM() LIMIT 1",
    )
    add(
        "favourites", "Favorilerin", "{n} sevilen parça",
        "SELECT COUNT(*) AS n FROM tracks WHERE rating > 0",
        "SELECT art_key FROM tracks WHERE rating > 0 AND art_key IS NOT NULL ORDER BY RANDOM() LIMIT 1",
    )
    add(
        "unplayed", "Hiç Çalmadıkların", "{n} keşfedilmemiş parça",
        "SELECT COUNT(*) AS n FROM tracks WHERE play_count = 0 AND rating >= 0",
        "SELECT art_key FROM tracks WHERE play_count = 0 AND art_key IS NOT NULL ORDER BY RANDOM() LIMIT 1",
    )
    add(
        "rediscover", "Yeniden Keşfet", "Uzun zamandır çalmadıkların",
        "SELECT COUNT(*) AS n FROM tracks WHERE play_count > 0 AND last_played < ?",
        "SELECT art_key FROM tracks WHERE play_count > 0 AND last_played < ? "
        "AND art_key IS NOT NULL ORDER BY RANDOM() LIMIT 1",
        (time.time() - 60 * 86400,),
    )
    add(
        "top", "En Çok Çaldıkların", "{n} parça",
        "SELECT COUNT(*) AS n FROM tracks WHERE play_count >= 3",
        "SELECT art_key FROM tracks WHERE play_count >= 3 AND art_key IS NOT NULL "
        "ORDER BY play_count DESC LIMIT 1",
    )

    hour_profile = _hour_affinity()
    if hour_profile:
        result.append({
            "id": "hour",
            "title": "Bu Saatlerde",
            "subtitle": "Günün bu vaktinde dinlediklerin",
            "count": 0,
            "art": _sample_art(
                "SELECT art_key FROM tracks WHERE genre = ? AND art_key IS NOT NULL "
                "ORDER BY RANDOM() LIMIT 1",
                (max(hour_profile, key=hour_profile.get),),
            ),
        })

    for row in db.query(
        "SELECT genre, COUNT(*) AS n FROM tracks WHERE genre IS NOT NULL AND genre <> '' "
        "GROUP BY genre HAVING n >= 20 ORDER BY n DESC LIMIT 4"
    ):
        result.append({
            "id": f"genre:{row['genre']}",
            "title": row["genre"],
            "subtitle": f"{row['n']} parça",
            "count": row["n"],
            "art": _sample_art(
                "SELECT art_key FROM tracks WHERE genre = ? AND art_key IS NOT NULL "
                "ORDER BY RANDOM() LIMIT 1",
                (row["genre"],),
            ),
        })

    return result


def mix_tracks(mix_id: str, count: int = 50) -> list[int]:
    """Bir mix kimliği için parça listesi üretir."""
    if mix_id == "smart":
        return smart_shuffle(count=count)

    if mix_id == "hour":
        profile = _hour_affinity()
        if not profile:
            return smart_shuffle(count=count)
        top_genres = sorted(profile, key=profile.get, reverse=True)[:3]
        placeholders = ",".join("?" * len(top_genres))
        rows = _fetch_pool((), f"t.genre IN ({placeholders})", top_genres)
        if not rows:
            return smart_shuffle(count=count)
        now = time.time()
        scores = [_score(r, now, profile, None, {}) for r in rows]
        return _sample(rows, scores, count)

    if mix_id.startswith("genre:"):
        genre = mix_id.split(":", 1)[1]
        rows = _fetch_pool((), "t.genre = ?", (genre,))
        if not rows:
            return []
        now = time.time()
        scores = [_score(r, now, {}, None, {}) for r in rows]
        return _sample(rows, scores, count)

    static_queries = {
        "favourites": ("SELECT id FROM tracks WHERE rating > 0 ORDER BY RANDOM() LIMIT ?", ()),
        "unplayed": (
            "SELECT id FROM tracks WHERE play_count = 0 AND rating >= 0 ORDER BY RANDOM() LIMIT ?",
            (),
        ),
        "rediscover": (
            "SELECT id FROM tracks WHERE play_count > 0 AND rating >= 0 AND last_played < ? "
            "ORDER BY RANDOM() LIMIT ?",
            (time.time() - 60 * 86400,),
        ),
        "top": (
            "SELECT id FROM tracks WHERE play_count >= 3 AND rating >= 0 "
            "ORDER BY play_count DESC, RANDOM() LIMIT ?",
            (),
        ),
    }
    entry = static_queries.get(mix_id)
    if entry is None:
        return []
    sql, params = entry
    ids = [r["id"] for r in db.query(sql, (*params, count))]
    _rng.shuffle(ids)
    return ids


def mix_track_dicts(mix_id: str, count: int = 50) -> list[dict[str, Any]]:
    return library.tracks_by_ids(mix_tracks(mix_id, count))
