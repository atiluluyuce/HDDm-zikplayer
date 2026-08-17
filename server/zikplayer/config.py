"""Yapılandırma. Her ayar ZIK_* ortam değişkeniyle ezilebilir.

Servisler /etc/zikplayer/zikplayer.env dosyasını okur (systemd EnvironmentFile).
"""

from __future__ import annotations

import os
from pathlib import Path


def _str(name: str, default: str) -> str:
    return os.environ.get(f"ZIK_{name}", default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return _str(name, "1" if default else "0").lower() in ("1", "true", "yes", "on", "evet")


def _path(name: str, default: str) -> Path:
    return Path(_str(name, default)).expanduser()


# --- Dosya yolları -----------------------------------------------------------

#: Müzik arşivinin kökü. MPD'nin music_directory ayarıyla AYNI olmak zorunda —
#: veritabanındaki yollar bu köke göreli tutulur ve doğrudan MPD'ye verilir.
MUSIC_ROOT = _path("MUSIC_ROOT", "/media/music")

DATA_DIR = _path("DATA_DIR", "/var/lib/zikplayer")
DB_PATH = DATA_DIR / "library.db"
ART_DIR = DATA_DIR / "art"

#: Web arayüzü varlıkları (repo içindeki ui/ dizini).
UI_DIR = _path("UI_DIR", str(Path(__file__).resolve().parents[2] / "ui"))

# --- MPD ---------------------------------------------------------------------

MPD_HOST = _str("MPD_HOST", "127.0.0.1")
MPD_PORT = _int("MPD_PORT", 6600)

# --- HTTP --------------------------------------------------------------------

HTTP_HOST = _str("HTTP_HOST", "0.0.0.0")
HTTP_PORT = _int("HTTP_PORT", 8080)

# --- Ses ---------------------------------------------------------------------

#: "softvol" -> ALSA softvol ile yazılımsal seviye (arayüzden ayarlanır)
#: "fixed"   -> DAC hep tam çıkış, seviye amfiden ayarlanır (bit-perfect)
VOLUME_MODE = _str("VOLUME_MODE", "softvol")
VOLUME_MAX = _int("VOLUME_MAX", 100)

# --- Kütüphane taraması ------------------------------------------------------

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".mp4", ".flac", ".ogg", ".oga",
    ".opus", ".wav", ".wma", ".alac", ".aiff", ".aif", ".ape",
}

#: Klasörde gömülü kapak yoksa bu isimler aranır (sırayla).
COVER_FILENAMES = (
    "cover.jpg", "cover.jpeg", "cover.png",
    "folder.jpg", "folder.jpeg", "folder.png",
    "front.jpg", "front.jpeg", "front.png",
    "album.jpg", "albumart.jpg", "artwork.jpg",
)

#: Kapak görselleri bu iki boyutta önbelleğe alınır. Panel 320px'i, web arayüzü
#: ızgarada 320px'i, tam ekran görünümde full'ü kullanır.
ART_THUMB_SIZE = _int("ART_THUMB_SIZE", 320)
ART_FULL_SIZE = _int("ART_FULL_SIZE", 800)
ART_QUALITY = _int("ART_QUALITY", 85)

#: Açılışta kütüphaneyi otomatik tara (değişen dosyalar için hızlı geçiş).
SCAN_ON_START = _bool("SCAN_ON_START", True)

# --- Öneri motoru ------------------------------------------------------------

#: Otomatik kuyruk: çalma listesinde bundan az parça kalırsa öneri motoru doldurur.
AUTOQUEUE_MIN = _int("AUTOQUEUE_MIN", 5)
AUTOQUEUE_BATCH = _int("AUTOQUEUE_BATCH", 15)

#: Çeşitlilik kısıtları — akıllı karıştırmada aynı sanatçı/albüm arka arkaya gelmesin.
DIVERSITY_ARTIST_GAP = _int("DIVERSITY_ARTIST_GAP", 5)
DIVERSITY_ALBUM_GAP = _int("DIVERSITY_ALBUM_GAP", 8)

#: Bir parçanın "çalındı" sayılması için gereken oran (altında kalırsa "atlandı").
PLAY_COMPLETION_RATIO = float(_str("PLAY_COMPLETION_RATIO", "0.5"))

# --- Panel (3.5" dokunmatik ekran) -------------------------------------------

PANEL_FB = _str("PANEL_FB", "/dev/fb1")
PANEL_WIDTH = _int("PANEL_WIDTH", 480)
PANEL_HEIGHT = _int("PANEL_HEIGHT", 320)
#: Dokunmatik girdi aygıtı. Boş bırakılırsa evdev aygıtları taranıp ilk
#: dokunmatik ekran otomatik seçilir.
PANEL_TOUCH_DEVICE = _str("PANEL_TOUCH_DEVICE", "")
PANEL_FPS = _int("PANEL_FPS", 10)
#: Ekran koruyucu: bu kadar saniye dokunulmazsa panel kararır (0 = kapalı).
PANEL_DIM_AFTER = _int("PANEL_DIM_AFTER", 0)

# --- EC11 döner encoder ------------------------------------------------------
# Rezistif dokunmatik yavaş/isabetsiz kalırsa fiziksel kontrol devreye girer.
# BCM numaraları. Varsayılanlar 26 pinlik ekranın ALTINDA KALMAYAN pinlerden
# seçildi (pin 29/31/33), yani ekran takılıyken de doğrudan erişilebilirler.
ENCODER_ENABLED = _bool("ENCODER_ENABLED", True)
ENCODER_PIN_A = _int("ENCODER_PIN_A", 5)       # CLK -> fiziksel pin 29
ENCODER_PIN_B = _int("ENCODER_PIN_B", 6)       # DT  -> fiziksel pin 31
ENCODER_PIN_SW = _int("ENCODER_PIN_SW", 13)    # SW  -> fiziksel pin 33
#: Encoder yönünü ters çevirir (saat yönü ses açmıyorsa bunu 1 yap).
ENCODER_REVERSED = _bool("ENCODER_REVERSED", False)
#: Bir tık başına ses adımı (yüzde).
ENCODER_VOLUME_STEP = _int("ENCODER_VOLUME_STEP", 2)
#: Butona bu süreden uzun basılırsa "geri/ana ekran" sayılır (saniye).
ENCODER_LONG_PRESS = float(_str("ENCODER_LONG_PRESS", "0.6"))


def ensure_dirs() -> None:
    """Veri dizinlerini oluşturur. Servis başlangıcında çağrılır."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ART_DIR.mkdir(parents=True, exist_ok=True)
