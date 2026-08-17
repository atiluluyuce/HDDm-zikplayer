#!/usr/bin/env bash
# Waveshare 3.5inch RPi LCD (A) ekranını etkinleştirir.
#
#   sudo ./install/setup-display.sh
#
# Ekran ILI9486 sürücülü ve SPI üzerinden çalışıyor. Raspberry Pi OS bu ekran
# için hazır overlay ile gelmiyor; önce waveshare35a aranır, bulunamazsa
# mainline piscreen overlay'ine düşülür (aynı denetleyici, çoğu kartta çalışır).

set -euo pipefail

log()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }

[[ $EUID -eq 0 ]] || { warn "root olarak çalıştır: sudo $0"; exit 1; }

CONFIG=/boot/firmware/config.txt
OVERLAY_DIR=/boot/firmware/overlays
if [[ ! -f "$CONFIG" ]]; then
  CONFIG=/boot/config.txt
  OVERLAY_DIR=/boot/overlays
fi
[[ -f "$CONFIG" ]] || { warn "config.txt bulunamadı"; exit 1; }
log "Yapılandırma dosyası: $CONFIG"

cp -n "$CONFIG" "$CONFIG.hddmusicplayer-bak" 2>/dev/null || true

# rotate=90 -> 320x480 panel 480x320 yatay hâle gelir.
if [[ -f "$OVERLAY_DIR/waveshare35a.dtbo" ]]; then
  OVERLAY='dtoverlay=waveshare35a:rotate=90'
  log "waveshare35a overlay'i bulundu"
elif [[ -f "$OVERLAY_DIR/piscreen.dtbo" ]]; then
  OVERLAY='dtoverlay=piscreen,speed=16000000,rotate=90'
  warn "waveshare35a yok; piscreen kullanılıyor (ILI9486 uyumlu)."
  warn "Ekran açılmazsa Waveshare LCD-show deposundaki .dtbo dosyasını"
  warn "$OVERLAY_DIR altına kopyalayıp bu betiği tekrar çalıştır."
else
  warn "Uygun overlay bulunamadı."
  warn "Waveshare LCD-show deposundan waveshare35a.dtbo dosyasını"
  warn "$OVERLAY_DIR altına kopyala, sonra bu betiği tekrar çalıştır."
  exit 1
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
add_line 'dtparam=spi=on'
add_line "$OVERLAY"

# Konsolu SPI ekrana taşımıyoruz: panel arayüzü /dev/fb1'e doğrudan çiziyor,
# konsolun aynı framebuffer'a yazması görüntüyü bozar.
if grep -qE '^\s*fbcon=map:' "$CONFIG" 2>/dev/null; then
  warn "config.txt içinde fbcon=map: satırı var; panel görüntüsüyle çakışabilir."
fi

# Ekran koruyucuyu kapat (konsol framebuffer'ı uyutmasın).
BLANK=/etc/systemd/system/disable-console-blank.service
if [[ ! -f "$BLANK" ]]; then
  cat > "$BLANK" <<'UNIT'
[Unit]
Description=Konsol ekran koruyucusunu kapat (hddmusicplayer paneli için)

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'echo -e "\\033[9;0]" > /dev/tty1 || true'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable disable-console-blank.service >/dev/null
  log "konsol ekran koruyucusu kapatıldı"
fi

cat <<'EOF'

Ekran + DAC fiziksel çakışması hatırlatması:

  Ekran 26 pinlik; header'ın 1-26 aralığını kaplar, 27-40 açıkta kalır.
  DAC'ın LCK (35), DIN (40) ve GND (39) uçları sorunsuz erişilebilir.
  Sorun sadece BCK (pin 12) ve 5V (pin 2/4) — ikisi de ekranın altında.

  Çözüm A: 2x20 uzun bacaklı (stacking) header kullan, hepsi erişilebilir olur.
  Çözüm B: pin 12'ye kartın altından kablo lehimle, 5V'u mevcut besleme
           kablosundan ayır.

Şimdi yeniden başlat:  sudo reboot

Sonrasında:
    ls -l /dev/fb1                         # ekran framebuffer'ı görünmeli
    sudo ./install/calibrate-touch.sh      # dokunmatiği kalibre et
    sudo systemctl start hddmusicplayer-panel
EOF
