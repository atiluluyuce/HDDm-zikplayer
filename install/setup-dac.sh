#!/usr/bin/env bash
# PCM5102A I2S DAC'ı etkinleştirir.
#
#   sudo ./install/setup-dac.sh
#
# Yaptığı: config.txt'e hifiberry-dac overlay'ini ekler, dahili analog çıkışı
# kapatır. Değişiklikler yeniden başlatmadan sonra etkin olur.

set -euo pipefail

log()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }

[[ $EUID -eq 0 ]] || { warn "root olarak çalıştır: sudo $0"; exit 1; }

# Bookworm'da firmware dosyaları /boot/firmware altına taşındı.
CONFIG=/boot/firmware/config.txt
[[ -f "$CONFIG" ]] || CONFIG=/boot/config.txt
[[ -f "$CONFIG" ]] || { warn "config.txt bulunamadı"; exit 1; }
log "Yapılandırma dosyası: $CONFIG"

cp -n "$CONFIG" "$CONFIG.hddmusicplayer-bak" 2>/dev/null || true

# Dahili analog çıkış kapalı olsun ki DAC 0 numaralı kart olsun.
if grep -qE '^\s*dtparam=audio=on' "$CONFIG"; then
  sed -i 's/^\s*dtparam=audio=on/#&  # hddmusicplayer: I2S DAC kullanılıyor/' "$CONFIG"
  log "dtparam=audio=on devre dışı bırakıldı"
fi

add_line() {
  if grep -qxF "$1" "$CONFIG"; then
    log "zaten var: $1"
  else
    printf '%s\n' "$1" >> "$CONFIG"
    log "eklendi: $1"
  fi
}

grep -q '# --- hddmusicplayer ---' "$CONFIG" || printf '\n# --- hddmusicplayer ---\n' >> "$CONFIG"
add_line 'dtparam=audio=off'
add_line 'dtoverlay=hifiberry-dac'

# --- Kart zaten görünüyorsa softvol denetimini şimdiden oluştur --------------
# softvol kontrolü, PCM ilk kez açılana kadar ALSA'da görünmez. MPD mikseri
# bulamayınca uyarı basıp seviye kontrolünü kapatıyor. Bir saniyelik sessizlik
# çalarak denetimi baştan yaratıyoruz.
if aplay -l 2>/dev/null | grep -qi 'sndrpihifiberry'; then
  log "DAC zaten etkin, softvol denetimi hazırlanıyor"
  aplay -D hddmusicplayer -f cd -d 1 /dev/zero >/dev/null 2>&1 || true
  amixer -c sndrpihifiberry sset SoftMaster 70% >/dev/null 2>&1 || true
  amixer -c sndrpihifiberry scontrols 2>/dev/null || true
else
  warn "DAC henüz görünmüyor — yeniden başlattıktan sonra etkin olacak."
fi

cat <<'EOF'

Kablolama hatırlatması (Pi 3 40-pin header):

    PCM5102A       Pi pin
    ---------      -----------------------
    VIN            4   (5V)
    GND            6   (GND)
    BCK            12  (GPIO18 / PCM_CLK)
    LCK            35  (GPIO19 / PCM_FS)
    DIN            40  (GPIO21 / PCM_DOUT)
    SCK            9 veya 39 (GND)   <-- UNUTMA

  SCK ucu GND'ye çekilmezse yonga dahili PLL'i devreye almaz;
  DAC ya hiç ses vermez ya da cızırtı üretir.

  Modül jumper'ları: FLT=L  DEMP=L  XSMT=H  FMT=L

Şimdi yeniden başlat:  sudo reboot

Sonrasında kontrol:
    aplay -l                              # sndrpihifiberry görünmeli
    speaker-test -D hddmusicplayer -c 2 -t sine -l 1
EOF
