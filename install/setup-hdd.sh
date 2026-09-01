#!/usr/bin/env bash
# Müzik diskini kalıcı olarak bağlar.
#
#   sudo ./install/setup-hdd.sh              # uygun bölümü otomatik bulur
#   sudo ./install/setup-hdd.sh /dev/sda1    # bölümü elle belirt
#
# UUID ile /etc/fstab'a nofail bayrağıyla yazılır: disk takılı değilken de
# Raspberry Pi normal şekilde açılır, arayüz "müzik yok" durumunu gösterir.

set -euo pipefail

MOUNT_POINT="${MOUNT_POINT:-/media/music}"

log()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }

[[ $EUID -eq 0 ]] || { warn "root olarak çalıştır: sudo $0"; exit 1; }

DEVICE="${1:-}"

if [[ -z "$DEVICE" ]]; then
  log "Uygun bölüm aranıyor"
  # Sistem diski (mmcblk*) hariç, dosya sistemi olan en büyük bölümü seç.
  DEVICE=$(lsblk -rpno NAME,SIZE,FSTYPE,MOUNTPOINT | awk '
    $1 !~ /mmcblk/ && $3 != "" && $3 != "swap" && $4 == "" { print $1, $2 }' |
    sort -k2 -h | tail -1 | awk '{print $1}')
  [[ -n "$DEVICE" ]] || { warn "Bölüm bulunamadı. Diski tak ve elle belirt: $0 /dev/sda1"; exit 1; }
  log "Bulundu: $DEVICE"
fi

[[ -b "$DEVICE" ]] || { warn "$DEVICE bir blok aygıtı değil"; exit 1; }

UUID=$(blkid -s UUID -o value "$DEVICE" || true)
FSTYPE=$(blkid -s TYPE -o value "$DEVICE" || true)
[[ -n "$UUID" && -n "$FSTYPE" ]] || { warn "$DEVICE üzerinde tanınan dosya sistemi yok"; exit 1; }

log "Aygıt: $DEVICE  ·  tip: $FSTYPE  ·  UUID: $UUID"

# --- Dosya sistemine göre paket ve bağlama seçenekleri -----------------------

case "$FSTYPE" in
  ext4|ext3|ext2)
    # Yerli dosya sistemi: en düşük CPU maliyeti.
    OPTIONS="defaults,noatime,nofail,x-systemd.device-timeout=15"
    ;;
  ntfs|ntfs3)
    if ! command -v ntfs-3g >/dev/null 2>&1; then
      log "ntfs-3g kuruluyor"
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ntfs-3g
    fi
    FSTYPE=ntfs-3g
    # uid/gid: mpd ve hddmusicplayer okuyabilsin. big_writes kopyalamayı hızlandırır.
    OPTIONS="defaults,noatime,nofail,x-systemd.device-timeout=15,uid=0,gid=audio,umask=0022,big_writes"
    ;;
  exfat)
    if ! command -v exfatlabel >/dev/null 2>&1 && ! command -v mkfs.exfat >/dev/null 2>&1; then
      log "exfatprogs kuruluyor"
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends exfatprogs || \
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends exfat-fuse exfat-utils
    fi
    OPTIONS="defaults,noatime,nofail,x-systemd.device-timeout=15,uid=0,gid=audio,umask=0022"
    ;;
  vfat)
    # iocharset=utf8: FAT32 dosya adlarını UTF-16'dan çevirirken çekirdeğin
    # varsayılanı UTF-8 olmayabiliyor; o zaman "Şarkı" gibi adlar bozuk gelir ve
    # hem tarama hem MPD dosyayı açamaz. Açıkça belirtiyoruz.
    OPTIONS="defaults,noatime,nofail,x-systemd.device-timeout=15,uid=0,gid=audio,umask=0022,iocharset=utf8"
    warn "FAT32 dosya sistemi: 4 GB üstü dosya desteklenmez, uzun yollarda sorun çıkabilir."
    ;;
  hfsplus)
    warn "HFS+ Linux'ta yazma açısından güvenilir değil. Salt okunur bağlanacak."
    OPTIONS="ro,noatime,nofail,x-systemd.device-timeout=15"
    ;;
  *)
    warn "Bilinmeyen dosya sistemi: $FSTYPE — varsayılan seçeneklerle denenecek"
    OPTIONS="defaults,noatime,nofail,x-systemd.device-timeout=15"
    ;;
esac

# --- fstab -------------------------------------------------------------------

mkdir -p "$MOUNT_POINT"
cp -n /etc/fstab /etc/fstab.hddmusicplayer-bak 2>/dev/null || true

if grep -q "UUID=$UUID" /etc/fstab; then
  log "fstab kaydı zaten var, güncelleniyor"
  sed -i "\|UUID=$UUID|d" /etc/fstab
fi

printf 'UUID=%s  %s  %s  %s  0  0\n' "$UUID" "$MOUNT_POINT" "$FSTYPE" "$OPTIONS" >> /etc/fstab
log "fstab güncellendi"

systemctl daemon-reload
mountpoint -q "$MOUNT_POINT" && umount "$MOUNT_POINT" || true
mount "$MOUNT_POINT"

log "Bağlandı: $MOUNT_POINT"
df -h "$MOUNT_POINT" | tail -1

COUNT=$(find "$MOUNT_POINT" -maxdepth 6 -type f \
  \( -iname '*.mp3' -o -iname '*.m4a' -o -iname '*.flac' -o -iname '*.aac' \) 2>/dev/null |
  head -20000 | wc -l)
log "İlk bakışta $COUNT ses dosyası görünüyor (en fazla 20000'e kadar sayıldı)"

if [[ "$MOUNT_POINT" != "/media/music" ]]; then
  warn "Bağlama noktası varsayılandan farklı."
  warn "/etc/hddmusicplayer/hddmusicplayer.env içindeki HDDMUSICPLAYER_MUSIC_ROOT ve"
  warn "/etc/mpd.conf içindeki music_directory değerlerini $MOUNT_POINT yap."
fi

cat <<EOF

Sıradaki:
    sudo systemctl restart mpd hddmusicplayer-api
    # ardından web arayüzünün Ayarlar sekmesinden "Tara"ya bas
EOF
