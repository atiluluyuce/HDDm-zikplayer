#!/usr/bin/env bash
# hddmusicplayer ana kurulum betiği (Raspberry Pi OS: Bookworm veya Trixie).
#
#   sudo ./install/install.sh
#
# Yaptıkları:
#   * paketleri kurar (mpd, python bağımlılıkları)
#   * hddmusicplayer sistem kullanıcısını ve veri dizinlerini oluşturur
#   * sanal ortamı hazırlar (apt paketlerini de görebilen)
#   * yapılandırmaları /etc altına kurar
#   * systemd servislerini kurup başlatır
#
# DAC, ekran ve disk ayarları ayrı betiklerde — bunlar yeniden başlatma
# gerektirdiği için kurulumdan bağımsız tutuldu:
#   ./install/setup-dac.sh
#   ./install/setup-display.sh
#   ./install/setup-hdd.sh /dev/sda1

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="$REPO_DIR/install/config"
SERVICE_USER="hddmusicplayer"
VENV_DIR="/opt/hddmusicplayer/venv"
DATA_DIR="/var/lib/hddmusicplayer"
ETC_DIR="/etc/hddmusicplayer"
MUSIC_ROOT="${MUSIC_ROOT:-/media/music}"

log()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }

[[ $EUID -eq 0 ]] || { warn "root olarak çalıştır: sudo $0"; exit 1; }

# --- Paketler ----------------------------------------------------------------

log "Paketler kuruluyor"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  mpd mpc alsa-utils \
  python3 python3-venv python3-pip \
  python3-pil python3-numpy python3-mutagen \
  python3-evdev python3-gpiozero python3-lgpio \
  fonts-dejavu-core

# --- Kullanıcı ve dizinler ---------------------------------------------------

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  log "Sistem kullanıcısı oluşturuluyor: $SERVICE_USER"
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# audio: ALSA · video: /dev/fb1 · input: dokunmatik · gpio: EC11 encoder
for group in audio video input gpio plugdev; do
  getent group "$group" >/dev/null 2>&1 && usermod -aG "$group" "$SERVICE_USER" || true
done

log "Dizinler hazırlanıyor"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 "$DATA_DIR" "$DATA_DIR/art"
install -d -m 0755 "$ETC_DIR" "$MUSIC_ROOT"
install -d -m 0755 /opt/hddmusicplayer

# --- Python ortamı -----------------------------------------------------------
# --system-site-packages: PIL, numpy, mutagen, evdev, gpiozero apt'ten gelir.
# Bunları pip ile kurmak 32-bit Pi'de kaynaktan derleme demek ve çok uzun sürer.
# Sanal ortama sadece saf Python olan starlette + uvicorn giriyor.

log "Python sanal ortamı hazırlanıyor"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv --system-site-packages "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$REPO_DIR/server/requirements.txt"

# --- Yapılandırma ------------------------------------------------------------

VOLUME_MODE="${VOLUME_MODE:-softvol}"

log "Yapılandırma yazılıyor (ses modu: $VOLUME_MODE)"
if [[ ! -f "$ETC_DIR/hddmusicplayer.env" ]]; then
  sed -e "s|@MUSIC_ROOT@|$MUSIC_ROOT|g" \
      -e "s|@VOLUME_MODE@|$VOLUME_MODE|g" \
      "$CONFIG_DIR/hddmusicplayer.env" > "$ETC_DIR/hddmusicplayer.env"
else
  log "  $ETC_DIR/hddmusicplayer.env zaten var, dokunulmadı"
fi
chmod 0644 "$ETC_DIR/hddmusicplayer.env"

# MPD yapılandırması. Mevcut dosya yedeklenir.
if [[ -f /etc/mpd.conf && ! -f /etc/mpd.conf.hddmusicplayer-bak ]]; then
  cp /etc/mpd.conf /etc/mpd.conf.hddmusicplayer-bak
  log "  eski /etc/mpd.conf -> /etc/mpd.conf.hddmusicplayer-bak"
fi

if [[ "$VOLUME_MODE" == "fixed" ]]; then
  MPD_DEVICE="hw:CARD=sndrpihifiberry,DEV=0"
  MPD_MIXER='mixer_type      "none"'
else
  MPD_DEVICE="hddmusicplayer"
  MPD_MIXER='mixer_type      "hardware"
    mixer_device    "hw:CARD=sndrpihifiberry"
    mixer_control   "SoftMaster"'
fi

sed -e "s|@MUSIC_ROOT@|$MUSIC_ROOT|g" \
    -e "s|@MPD_DEVICE@|$MPD_DEVICE|g" \
    -e "s|@MPD_MIXER@|$MPD_MIXER|g" \
    "$CONFIG_DIR/mpd.conf" > /etc/mpd.conf
chmod 0644 /etc/mpd.conf

install -m 0644 "$CONFIG_DIR/asound.conf" /etc/asound.conf
install -m 0440 "$CONFIG_DIR/hddmusicplayer-sudoers" /etc/sudoers.d/hddmusicplayer
visudo -cf /etc/sudoers.d/hddmusicplayer >/dev/null

# --- systemd -----------------------------------------------------------------

log "Servisler kuruluyor"
for unit in hddmusicplayer-api hddmusicplayer-panel; do
  sed -e "s|@REPO_DIR@|$REPO_DIR|g" \
      -e "s|@VENV_DIR@|$VENV_DIR|g" \
      -e "s|@USER@|$SERVICE_USER|g" \
      "$CONFIG_DIR/$unit.service" > "/etc/systemd/system/$unit.service"
done

systemctl daemon-reload
systemctl enable --now mpd
systemctl enable --now hddmusicplayer-api

if [[ -e /dev/fb1 ]]; then
  systemctl enable --now hddmusicplayer-panel
else
  warn "/dev/fb1 yok — panel servisi kuruldu ama başlatılmadı."
  warn "Önce ./install/setup-display.sh çalıştırıp yeniden başlat."
  systemctl enable hddmusicplayer-panel
fi

# --- Özet --------------------------------------------------------------------

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

$(log "Kurulum tamamlandı")

  Web arayüzü : http://${IP:-<pi-ip>}:8080
  Müzik dizini: $MUSIC_ROOT
  Ayarlar     : $ETC_DIR/hddmusicplayer.env
  Günlükler   : journalctl -u hddmusicplayer-api -f

Sıradaki adımlar:

  1. Diski bağla        : sudo ./install/setup-hdd.sh /dev/sda1
  2. DAC'ı etkinleştir  : sudo ./install/setup-dac.sh
  3. Ekranı etkinleştir : sudo ./install/setup-display.sh
  4. Yeniden başlat     : sudo reboot
  5. Dokunmatiği ayarla : sudo ./install/calibrate-touch.sh

Kütüphane taraması ilk açılışta kendiliğinden başlar. 500 GB'lik bir arşiv
USB HDD üzerinden yaklaşık 20-40 dakika sürebilir; ilerlemeyi web arayüzünün
Ayarlar sekmesinden izleyebilirsin.
EOF
