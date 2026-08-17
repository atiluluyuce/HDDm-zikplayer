# hddmusicplayer

Raspberry Pi 3 tabanlı, Sony HAP-Z1ES tarzı yerel müzik sunucusu. 500 GB'lik
taşınabilir diskteki arşive bilgisayar açmadan, cihazın üzerindeki dokunmatik
ekrandan veya telefondan erişip dinlemek için.

```
   USB HDD ──► Raspberry Pi 3B ──► PCM5102A (I2S) ──► amfi
                    │
                    ├── 3.5" dokunmatik panel  (cihazın kendi ekranı)
                    └── web arayüzü :8080      (telefon / tablet / bilgisayar)
```

## Öne çıkanlar

- **Bit-perfect I2S çıkışı.** MPD ses motoru olarak; yeniden örnekleme yok,
  dosya neyse DAC'a o gidiyor. Gapless çalma hazır geliyor.
- **Yerel öneri motoru.** İnternet, hesap, Last.fm yok. Çalma geçmişi, atlama
  oranı, saat bazlı dinleme alışkanlığı ve parçalar arası geçişlerden öğrenip
  akıllı karıştırma, "benzerlerini çal" ve hazır mixler üretiyor.
- **İki arayüz, tek çekirdek.** Küçük ekran için hızlı bir panel, telefon için
  zengin bir web arayüzü. İkisi anlık senkron.
- **Cihaz gibi davranır.** Açılışta hazır, disk yokken de açılıyor, arayüzden
  güvenli kapatma yapılabiliyor.

## Donanım

| Parça | Kullanılan |
|---|---|
| Kart | **Raspberry Pi 3 Model B v1.2** (1 GB, 4×1.2 GHz) |
| DAC | PCM5102A modülü, I2S üzerinden |
| Ekran | Waveshare 3.5" RPi LCD (A), 480×320, XPT2046 rezistif |
| Kontrol | EC11 döner encoder (opsiyonel ama şiddetle önerilir) |
| Depolama | 500 GB USB 2.5" HDD |

Elde bir Raspberry Pi 1 Model B de vardı; tek çekirdekli ARM11'i bu iş için
yetersiz kaldığı için elendi. Gerekçeler, kablolama şemaları ve ekran/DAC
fiziksel çakışmasının çözümü: **[docs/01-donanim.md](docs/01-donanim.md)**

> **İki kritik nokta:** PCM5102A'nın `SCK` ucu GND'ye çekilmezse ses gelmez.
> USB diski beslemeli hub veya Y-kabloyla besle — Pi 3'ün USB portları toplam
> ~1.2 A verir, disk kalkışta bunu zorlar.

## Kurulum

Raspberry Pi OS (Bullseye veya Bookworm, Lite yeterli) kurulu bir kartta:

```bash
git clone https://github.com/atiluluyuce/HDDm-zikplayer.git
cd HDDm-zikplayer

sudo ./install/install.sh          # paketler, servisler, web arayüzü
sudo ./install/setup-hdd.sh        # diski bulur ve fstab'a yazar
sudo ./install/setup-dac.sh        # PCM5102A overlay'i
sudo ./install/setup-display.sh    # 3.5" ekran overlay'i
sudo reboot

sudo ./install/calibrate-touch.sh  # dokunmatik kalibrasyonu
```

Ardından `http://<pi-ip>:8080` adresinden web arayüzü açılır. Kütüphane taraması
ilk açılışta kendiliğinden başlar; 500 GB'lik bir arşiv USB HDD üzerinden
yaklaşık 20-40 dakika sürer.

Adım adım anlatım ve sorun giderme: **[docs/02-kurulum.md](docs/02-kurulum.md)**

## Kullanım

### Panel (3.5" ekran)

```
┌────────────────────────────────────────────────┐
│ 14:32              3/47              🔈 65     │
│ ┌──────────┐  Yalnızlık Senfonisi              │
│ │          │  Şebnem Ferah                     │
│ │  kapak   │  Perdeler                         │
│ │          │  2005 · Rock · 320 kbps           │
│ └──────────┘  [ ♥ ]  [ ◉ ]  benzer parçalar    │
│ ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
│ 1:47                                     4:12  │
│ [   ⏮   ] [   ▶   ] [   ⏭   ] [   ☰   ]        │
└────────────────────────────────────────────────┘
```

| Girdi | Çalıyor ekranı | Listelerde |
|---|---|---|
| Dokunma | butonlar, ilerleme çubuğuna basarak sarma | satıra bas = aç |
| Encoder çevir | **ses** | seçimi gezdir |
| Encoder bas | çal / duraklat | seçileni aç |
| Encoder uzun bas | menüyü aç | geri |

Rezistif dokunmatik isabetsiz kalırsa cihaz tamamen encoder ile kullanılabilir.

### Web arayüzü

Ana sayfada öneri mixleri, yeni eklenenler ve son çalınanlar; ayrıca kapak
duvarı, sanatçı listesi, arama (Türkçe karakterler normalleştirilerek —
"sarki" araması "Şarkı"yı bulur), kuyruk düzenleme ve ayarlar.

Çalan parçaya dokununca açılan panelde **"Benzerlerini çal"** ve
**"Bir daha çalma"** var — ikincisi parçayı öneri havuzundan kalıcı olarak
çıkarır.

## Öneri motoru nasıl çalışıyor

Her aday parça çarpımsal olarak skorlanır, sonra ağırlıklı ve tekrarsız
örneklem alınır (üstel anahtar yöntemi), üstüne çeşitlilik kuralları uygulanır.

| Sinyal | Etki |
|---|---|
| Sevilen / yasaklı | ×2.5 · tamamen elenir |
| Son çalınma zamanı | 6 saat içinde ×0.05 → 30 gündür çalınmamış ×1.25 |
| Atlama oranı | %60 üstü atlanıyorsa ×0.25 |
| Hiç çalınmamış | ×1.35 (keşfe teşvik) |
| Süre | 30 sn altı ×0.15, 15 dk üstü ×0.6 |
| Saat profili | O saatte çok dinlenen tür ×1.6'ya kadar |
| Çeşitlilik | Aynı sanatçı 5, aynı albüm 8 parça arayla |

"Benzerlerini çal" modunda buna tohum parçaya benzerlik eklenir: aynı sanatçı,
tür, yıl yakınlığı ve **geçmişte gerçekten arka arkaya dinlenmiş olma**
(en güçlü sinyal). İlgisiz parçalar sıfırlanmaz, ~25 kat bastırılır — arada bir
sürpriz çıkması listeyi canlı tutuyor.

Ayrıntı ve mimari: **[docs/03-mimari.md](docs/03-mimari.md)**

## Proje yapısı

```
server/hddmusicplayer/
├── config.py        HDDMUSICPLAYER_* ortam değişkenleriyle ayarlar
├── db.py            SQLite şeması, Türkçe uyumlu fold()
├── mpd.py           bağımlılıksız asenkron MPD istemcisi
├── scanner.py       artımlı kütüphane taraması, kapak çıkarma
├── library.py       gezinme ve arama sorguları
├── recommender.py   skorlama, akıllı karıştırma, radyo, mixler
├── player.py        MPD köprüsü, geçmiş kaydı, otomatik kuyruk
├── web.py           Starlette API + SSE
└── panel/           framebuffer arayüzü, dokunmatik, EC11
ui/                  web arayüzü (bağımlılıksız, derleme gerektirmez)
install/             kurulum betikleri, systemd, mpd/alsa yapılandırması
```

Bağımlılıklar bilinçli olarak minimum: `starlette` + `uvicorn` (saf Python)
pip'ten, geri kalan her şey apt'ten. FastAPI/pydantic kullanılmadı çünkü
32-bit Raspberry Pi OS'ta hazır wheel bulamayıp kaynaktan derleniyor.

## Servisler

```bash
systemctl status hddmusicplayer-api      # API + web arayüzü
systemctl status hddmusicplayer-panel    # 3.5" ekran
systemctl status mpd                     # ses motoru
journalctl -u hddmusicplayer-api -f      # günlükler
```

Ayarlar `/etc/hddmusicplayer/hddmusicplayer.env` içinde; değiştirdikten sonra
ilgili servisi yeniden başlat.

> Depo adı `HDDm-zikplayer` olarak kalıyor (klonlama yolunu bozmamak için);
> cihazın, servislerin ve dosya yollarının adı `hddmusicplayer`.

## Durum

Çalışan: kütüphane indeksi, arama, gezinme, öneri motoru, MPD entegrasyonu,
web arayüzü, panel arayüzü, kurulum otomasyonu.

Çekirdek mantık (SQL, arama, skorlama, çeşitlilik kuralları) sentetik 600
parçalık bir kütüphaneyle test edildi. Donanıma bağlı kısımlar — I2S çıkışı,
framebuffer çizimi, dokunmatik ve encoder — gerçek kartta doğrulanmayı bekliyor.
