# Kurulum

## Hazırlık

**İşletim sistemi:** Raspberry Pi OS Lite (64-bit önerilir, 32-bit de çalışır).
Masaüstü sürümüne gerek yok — panel arayüzü doğrudan framebuffer'a çiziyor,
X sunucusu kullanılmıyor.

Raspberry Pi Imager ile yazarken gelişmiş ayarlardan şunları aç:

- SSH (parola veya anahtar ile)
- WiFi bilgileri (kablosuz kullanılacaksa)
- Kullanıcı adı / parola

İlk açılışta:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo raspi-config nonint do_hostname hddmusicplayer   # ağda hddmusicplayer.local olarak görünsün
```

## 1. Depoyu al ve kur

```bash
sudo apt install -y git
git clone https://github.com/atiluluyuce/HDDm-zikplayer.git
cd HDDm-zikplayer
sudo ./install/install.sh
```

Kurulum betiği şunları yapar:

| Adım | Ayrıntı |
|---|---|
| Paketler | `mpd`, `alsa-utils`, `python3-pil`, `python3-numpy`, `python3-mutagen`, `python3-evdev`, `python3-gpiozero`, `python3-lgpio` |
| Kullanıcı | `hddmusicplayer` sistem kullanıcısı; `audio`, `video`, `input`, `gpio` gruplarında |
| Sanal ortam | `/opt/hddmusicplayer/venv` — `--system-site-packages` ile, içine sadece `starlette` + `uvicorn` |
| Yapılandırma | `/etc/hddmusicplayer/hddmusicplayer.env`, `/etc/mpd.conf`, `/etc/asound.conf` |
| Servisler | `hddmusicplayer-api`, `hddmusicplayer-panel` |

> Mevcut `/etc/mpd.conf` varsa `/etc/mpd.conf.hddmusicplayer-bak` olarak yedeklenir.

**Bit-perfect çıkış istiyorsan** (ses seviyesini amfiden ayarlayacaksan):

```bash
sudo VOLUME_MODE=fixed ./install/install.sh
```

## 2. Diski bağla

```bash
sudo ./install/setup-hdd.sh              # otomatik bul
sudo ./install/setup-hdd.sh /dev/sda1    # veya elle belirt
```

Betik dosya sistemini tespit eder, gerekirse `ntfs-3g` / `exfatprogs` kurar ve
UUID ile `/etc/fstab`'a **`nofail`** bayrağıyla yazar. `nofail` sayesinde disk
takılı değilken de Pi normal açılır.

Diski hangi biçimde tutmalı:

| Dosya sistemi | Değerlendirme |
|---|---|
| **ext4** | En iyi. Yerli destek, en düşük CPU. Diski biçimlendirebiliyorsan bunu seç |
| **exFAT** | İyi. Windows/Mac ile de okunur |
| **NTFS** | Çalışır. FUSE üzerinden, CPU maliyeti var ama MP3 akışı için sorun değil |

> NTFS'te Pi 3'ün bir çekirdeği `ntfs-3g` ile meşgul olur. Tek dosya çalarken
> fark etmez; ilk tarama biraz uzar.

## 3. DAC'ı etkinleştir

Önce kabloları bağla — **[kablolama şeması: wiring.svg](wiring.svg)** — sonra:

```bash
sudo ./install/setup-dac.sh
sudo reboot
```

Yeniden başladıktan sonra doğrula:

```bash
aplay -l
# ... card 0: sndrpihifiberry [snd_rpi_hifiberry_dac], device 0: ...

speaker-test -D hddmusicplayer -c 2 -t sine -l 1
```

Ses gelmiyorsa: [Sorun giderme](#sorun-giderme).

## 4. Ekranı etkinleştir

```bash
sudo ./install/setup-display.sh
sudo reboot
```

Ardından:

```bash
ls -l /dev/fb1                      # ekranın framebuffer'ı
sudo ./install/calibrate-touch.sh   # dört hedefe sırayla bas
sudo systemctl restart hddmusicplayer-panel
```

`waveshare35a.dtbo` Raspberry Pi OS ile gelmez. Betik bulamazsa mainline
`piscreen` overlay'ine düşer (aynı ILI9486 denetleyici, çoğu kartta çalışır).
O da olmazsa Waveshare'in `LCD-show` deposundan `.dtbo` dosyasını
`/boot/firmware/overlays/` altına kopyala ve betiği tekrar çalıştır.

## 5. EC11 encoder (opsiyonel)

Rezistif dokunmatik yavaş kalırsa fiziksel kontrol devreye girer. Bağlantı:

| EC11 ucu | Pi fiziksel pin | BCM |
|---|---|---|
| CLK (A) | 29 | GPIO5 |
| DT (B) | 31 | GPIO6 |
| SW (buton) | 33 | GPIO13 |
| GND (C) | 39 | — |

Pinler bilinçli olarak **27-40 aralığından** seçildi: 3.5" ekran 26 pinlik
olduğu için bu bölge açıkta kalıyor, ekran takılıyken bile erişilebilir.

KY-040 tarzı bir kart kullanıyorsan `+` ucunu bağlamana gerek yok — Pi'nin
dahili pull-up dirençleri devreye alınıyor.

Saat yönü sesi kısıyorsa `/etc/hddmusicplayer/hddmusicplayer.env` içinde:

```ini
HDDMUSICPLAYER_ENCODER_REVERSED=1
```

Encoder yoksa `HDDMUSICPLAYER_ENCODER_ENABLED=0` yap; panel dokunmatikle çalışmaya devam eder.

## 6. İlk tarama

Servis açılışta taramayı kendiliğinden başlatır. İlerlemeyi izlemek için:

- Web arayüzü → Ayarlar sekmesi
- veya `journalctl -u hddmusicplayer-api -f`

500 GB / ~50 bin parçalık bir arşiv USB HDD üzerinden yaklaşık **20-40 dakika**
sürer. Sonraki taramalar yalnızca değişen dosyalara bakar, saniyeler sürer.

---

## Sorun giderme

### DAC'tan hiç ses gelmiyor

1. **`SCK` ucu GND'ye bağlı mı?** En sık sebep bu. Pi master clock üretmez;
   `SCK` şaseye çekilmezse yonga dahili PLL'ini devreye almaz.
2. **`XSMT` jumper'ı `H` konumunda mı?** `L` ise yonga sürekli sessizde kalır.
3. Kart görünüyor mu: `aplay -l` çıktısında `sndrpihifiberry` var mı?
4. `dmesg | grep -i hifiberry` — overlay yüklenmiş mi?
5. `cat /boot/firmware/config.txt | grep -E 'audio|hifiberry'`

### Ses var ama cızırtılı / kesik kesik

- Besleme yetersizliği. Özellikle GPIO'dan beslerken ince kablo gerilim
  düşürüyor. 5V/3A ölçülü bir kaynak kullan.
- USB disk aynı raydan besleniyorsa beslemeli hub'a al.
- `dmesg | grep -i 'under-voltage'` — düşük gerilim uyarısı var mı?

### MPD ses seviyesini bulamıyor

```
Failed to open mixer for 'PCM5102A I2S DAC'
```

softvol denetimi PCM ilk kez açılana kadar ALSA'da görünmez. Bir kez sessizlik
çalarak oluştur:

```bash
aplay -D hddmusicplayer -f cd -d 1 /dev/zero
amixer -c sndrpihifiberry sset SoftMaster 70%
sudo systemctl restart mpd
```

### Ekran beyaz / boş kalıyor

- `ls /dev/fb1` — yoksa overlay yüklenmemiş.
- `dmesg | grep -i fb` çıktısına bak.
- `piscreen` ile açılmıyorsa Waveshare'in kendi `.dtbo` dosyasını dene.
- SPI açık mı: `ls /dev/spidev*`

### Dokunma yanlış yere gidiyor

```bash
sudo ./install/calibrate-touch.sh
```

Yine olmuyorsa ham değerleri incele:

```bash
sudo evtest    # dokunmatik aygıtı seç, ekrana bas, ABS_X/ABS_Y değerlerini gör
```

`/var/lib/hddmusicplayer/touch-calibration.json` içindeki `swap_xy`, `invert_x`,
`invert_y` bayraklarını elle de düzeltebilirsin.

### Disk bağlanmıyor / kayboluyor

```bash
dmesg | tail -30      # USB hataları, akım uyarıları
lsblk -f              # bölüm ve dosya sistemi
sudo mount /media/music
```

Rastgele kopmalar neredeyse her zaman besleme kaynaklıdır. Beslemeli hub kullan.

### Web arayüzü açılmıyor

```bash
systemctl status hddmusicplayer-api
journalctl -u hddmusicplayer-api -n 50 --no-pager
curl -s localhost:8080/healthz        # "ok" dönmeli
```

`mpd yok` dönüyorsa MPD ayakta değil: `systemctl status mpd`.

### Panel açılmıyor

```bash
journalctl -u hddmusicplayer-panel -n 50 --no-pager
```

- `/dev/fb1` yoksa önce ekran overlay'i.
- İzin hatası varsa kullanıcı `video`/`input` gruplarında mı:
  `id hddmusicplayer`

---

## Güncelleme

```bash
cd HDDm-zikplayer
git pull
sudo systemctl restart hddmusicplayer-api hddmusicplayer-panel
```

Bağımlılıklar veya servis tanımları değiştiyse `sudo ./install/install.sh`
tekrar çalıştırılabilir — mevcut `hddmusicplayer.env` dosyasına dokunmaz.

## Kaldırma

```bash
sudo systemctl disable --now hddmusicplayer-api hddmusicplayer-panel
sudo rm /etc/systemd/system/hddmusicplayer-{api,panel}.service
sudo rm -rf /opt/hddmusicplayer /var/lib/hddmusicplayer /etc/hddmusicplayer /etc/sudoers.d/hddmusicplayer
sudo mv /etc/mpd.conf.hddmusicplayer-bak /etc/mpd.conf   # yedek varsa
sudo systemctl daemon-reload
```

`config.txt` ve `fstab` değişiklikleri için `.hddmusicplayer-bak` yedekleri kullanılabilir.
