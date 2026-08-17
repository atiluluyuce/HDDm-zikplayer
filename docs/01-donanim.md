# Donanım

## Kart seçimi: Raspberry Pi 3 Model B v1.2

Elde iki kart var, proje Pi 3 üzerine kuruluyor.

| | Pi 1 Model B rev 2 | **Pi 3 Model B v1.2** |
|---|---|---|
| SoC | BCM2835, ARM11 tek çekirdek 700 MHz | **BCM2837, 4× Cortex-A53 1.2 GHz** |
| RAM | 512 MB | **1 GB** |
| USB | 2× USB 2.0 | **4× USB 2.0** |
| Ağ | 100 Mbit (USB üzerinden) | 100 Mbit + **WiFi 802.11n + BT 4.1** |
| GPIO | 26 pin — I2S pinleri ayrı **P5 header**'da, lehim ister | **40 pin, I2S hazır** |
| Depolama | Tam boy SD | microSD |

Pi 1 neden elendi: tek çekirdekli ARM11, dokunmatik arayüzü (Chromium kiosk) çeviremez.
50 bin parçalık bir kütüphaneyi taraması saatler sürer, kapak görseli üretmesi ise günler.
I2S pinleri de P5 header'ında olduğu için DAC bağlantısı ayrıca lehim işi gerektirirdi.

Pi 3 bu iş için fazlasıyla yeterli. MP3/AAC çözümü tek çekirdeğin ~%2-3'ünü kullanır;
kalan güç arayüz ve öneri motoruna kalır.

### Pi 3'te dikkat edilecekler (mevcut kartın fotoğraflarından)

**1. GPIO'ya lehimli besleme kabloları.** Kartın 40-pin header'ının 1-6 pin bölgesine
kırmızı/siyah besleme kablosu lehimlenmiş durumda. Bunun iki sonucu var:

- Micro-USB girişindeki koruma (polyfuse + TVS) **baypas ediliyor**. Ters polarite veya
  aşırı akım artık kartı korumaz. Kaliteli, ölçülü bir **5V / 3A** kaynak kullan.
- Besleme GPIO'dan geldiği için gerilim düşümü doğrudan SoC'ye yansır. Kablo kesiti ince
  ise HDD kalkışında kart resetlenebilir.

İyi haber: bu kablolar I2S pinlerini (12, 35, 40) engellemiyor, DAC bağlantısı sorunsuz.

**2. USB akım bütçesi.** Pi 3'ün dört portu **toplam ~1.2 A** verir. 2.5" taşınabilir bir
HDD kalkış anında 0.9 A'e kadar çekebilir. Seçenekler, iyiden kötüye:

1. **Beslemeli USB hub** — en sağlam çözüm, disk kendi gücünü hub'dan alır. Önerilen.
2. **USB Y-kablosu** — ikinci ucu ayrı bir 5V kaynağa/hub'a takılır.
3. Doğrudan Pi'ye takmak + `/boot/config.txt` içine `max_usb_current=1` — çalışabilir ama
   sınırda. Rastgele "disk kayboldu" (I/O error) hatalarının bir numaralı sebebi budur.

**3. SD kart.** Sistem SD'de, müzik HDD'de. Sınıf 10 / A1 bir microSD yeterli.
Kurulum `install.sh` içinde log'ları tmpfs'e alıp swap'ı kapatarak SD ömrünü uzatıyor.

---

## PCM5102A DAC bağlantısı

PCM5102A, Pi'nin I2S hattına doğrudan bağlanır. Linux tarafında `hifiberry-dac`
overlay'i bu yongayı tanır — ayrı sürücü gerekmez.

### Kablolama (Pi 3 40-pin header)

| PCM5102A ucu | Pi pin | Pi sinyali | Not |
|---|---|---|---|
| `VIN` / `VCC` | **Pin 4** | 5V | Modülün üstünde regülatör var; 3.3V (pin 1) de çalışır |
| `GND` | **Pin 6** | GND | |
| `BCK` | **Pin 12** | GPIO18 / PCM_CLK | Bit clock |
| `LCK` / `LRCK` | **Pin 35** | GPIO19 / PCM_FS | Word/frame clock |
| `DIN` | **Pin 40** | GPIO21 / PCM_DOUT | Ses verisi |
| `SCK` | **Pin 9 veya 39** | GND | ⚠️ **Mutlaka GND'ye çekilecek** |

> **`SCK` neden GND'ye gidiyor?** Raspberry Pi, I2S hattında master clock (MCLK) üretmez.
> PCM5102A'nın `SCK` girişi şaseye çekilirse yonga kendi dahili PLL'ini devreye alıp
> sistem saatini `BCK`'dan türetir. Bu bağlantı unutulursa DAC ya hiç ses vermez ya da
> cızırtı üretir. En sık yapılan hata budur.

### Modül üzerindeki jumper'lar

Yaygın GY-PCM5102 kartının arkasında dört lehim köprüsü bulunur. Doğru konum:

| Köprü | Konum | Anlamı |
|---|---|---|
| `FLT` | **L** | Normal gecikmeli filtre |
| `DEMP` | **L** | De-emphasis kapalı |
| `XSMT` | **H** | Soft mute kapalı — **bu H olmazsa ses gelmez** |
| `FMT` | **L** | I2S formatı (Left-justified değil) |

Kartların çoğu fabrikada `L, L, H, L` gelir; yine de multimetreyle doğrula.

### Ses seviyesi kontrolü hakkında

PCM5102A'da **donanımsal ses seviyesi kaydı yoktur** — yonga her zaman tam çıkış verir.
Bu, HAP-Z1ES gibi cihazların analog kademeli seviye kontrolünden farklı bir durum.
İki seçenek var, ikisi de kurulu geliyor:

- **Sabit çıkış (bit-perfect, önerilen):** DAC hep %100 çalar, sesi amfiden ayarlarsın.
  Sinyal hiç değiştirilmez. `ZIK_VOLUME_MODE=fixed`
- **Yazılım seviyesi (softvol):** ALSA'nın `softvol` eklentisi araya girer, arayüzden
  ses ayarlanır. Çok kısık seviyelerde teorik olarak bit derinliği kaybı olur; pratikte
  %50 üstünde duyulur bir fark yoktur. `ZIK_VOLUME_MODE=softvol` (varsayılan)

---

## Depolama

500 GB taşınabilir HDD USB'ye takılır ve UUID ile sabit bir noktaya bağlanır.

Dosya sistemi tarafı:

| Dosya sistemi | Durum | Not |
|---|---|---|
| **ext4** | En iyi | Yerli destek, en düşük CPU. Diski biçimlendirebiliyorsan bunu seç |
| **exFAT** | İyi | `exfatprogs` ile. Windows/Mac ile de okunur |
| **NTFS** | Çalışır | `ntfs-3g` FUSE üzerinden; CPU maliyeti var ama MP3 için sorun değil |
| HFS+ | Sorunlu | Önerilmez |

`install/setup-hdd.sh` diski otomatik bulur, dosya sistemini tespit eder, gerekli paketi
kurar ve `nofail` bayrağıyla `/etc/fstab`'a yazar. `nofail` önemli: disk takılı değilken
Pi yine de açılır, arayüz "disk yok" uyarısı gösterir.

---

## Ekran: Waveshare 3.5inch RPi LCD (A) Rev4.0

| Özellik | Değer |
|---|---|
| Panel | 3.5", 320×480 (yatay kullanımda **480×320**) |
| LCD sürücü | ILI9486, **SPI** üzerinden |
| Dokunmatik | XPT2046, **rezistif**, tek nokta |
| Header | **26 pin** (2×13) |

Bu ekranın üç özelliği projenin yazılım mimarisini doğrudan belirledi:

### 1. Rezistif dokunmatik → kaydırma jesti yok

XPT2046 tek noktalı ve basınca duyarlıdır. Çoklu dokunma, pinch, akışkan swipe yoktur;
sürtünerek kaydırma güvenilir çalışmaz. Bu yüzden panel arayüzü **tamamen dokunma
(tap) tabanlı** tasarlandı: büyük butonlar, sayfa sayfa gezinme, kaydırma çubuğu yerine
yukarı/aşağı okları. Parmakla rahat basılabilmesi için tüm dokunma hedefleri en az
**64×64 piksel**.

### 2. SPI arayüzü → tarayıcı arayüzü uygun değil

Ekran SPI üzerinden sürülür. 320×480×16 bit = 300 KB'lik bir kare, 32 MHz SPI'da
kabaca **10-15 fps** eder. Buna X sunucusu + Chromium kiosk eklemek Pi 3'ün 1 GB
RAM'inin yarısını yer ve arayüz belirgin şekilde takılır.

Çözüm: panel arayüzü **doğrudan framebuffer'a** çizen küçük bir Python uygulaması
(Pillow ile render, `/dev/fb1`'e blit, dokunma `evdev` ile). X yok, tarayıcı yok,
~40 MB RAM, anında açılış. Sadece değişen bölgeler yeniden çizildiği için SPI hattı
boşuna meşgul edilmez.

### 3. 480×320 küçük → iki ayrı arayüz

Bu boyutta kapak duvarı gezdirmek anlamsız. Sony HAP-Z1ES'in yaptığını yapıyoruz:
**küçük ön panel + zengin uzak kumanda.**

- **Panel arayüzü** (3.5" ekran): çalan parça, büyük kapak, transport tuşları, ses,
  favori, "benzerlerini çal", hazır mixler. Günlük kullanımın %90'ı.
- **Web arayüzü** (telefon/tablet/PC, aynı WiFi): tüm kütüphane, kapak duvarı, arama,
  kuyruk düzenleme, istatistikler. Sony'nin "HDD Audio Remote" uygulamasının karşılığı.

İkisi de aynı çekirdeği kullanır, durum anlık olarak senkron kalır.

---

## ⚠️ Ekran + DAC fiziksel çakışması ve çözümü

Ekran **26 pinlik**. Pi 3'ün 40-pin header'ına takıldığında **pin 1-26 arasını kaplar**,
**pin 27-40 açıkta kalır**. Elektriksel olarak çakışma yok — ekran SPI0'ı
(GPIO 7,8,9,10,11) + GPIO 17/24/25'i kullanır, I2S ise GPIO 18/19/21'i. Sorun tamamen
fiziksel: hangi pine elle ulaşabildiğin.

DAC'ın ihtiyaç duyduğu pinlerin durumu:

| DAC ucu | Pi pin | Ekran takılıyken |
|---|---|---|
| `LCK` | 35 | ✅ **Açıkta** |
| `DIN` | 40 | ✅ **Açıkta** |
| `GND` | 39 | ✅ **Açıkta** |
| `BCK` | 12 | ❌ Ekranın altında kalıyor (ekranda **NC**, yani boşta duruyor) |
| `VIN` | 2 / 4 (5V) | ❌ Ekranın altında kalıyor |

Yani sadece **iki bağlantı** sorunlu. İki çözüm var:

### Çözüm A — 2×20 uzun bacaklı (stacking) header · *önerilen*

Pi ile ekran arasına takılır, ekranı ~11 mm yükseltir ve **40 pinin tamamı altta
erişilebilir** kalır. Birkaç liralık parça, lehim gerekmez, her şeyi tek seferde çözer.
Ayrıca ekranın altında DAC'a yer açar.

### Çözüm B — hiçbir şey almadan, lehimle · *mevcut modifikasyona uygun*

Kartta zaten GPIO'ya lehimlenmiş besleme kabloları var, aynı yöntem:

1. `LCK`, `DIN`, `GND` → **pin 35, 40, 39**'a normal jumper ile (açıkta, sorun yok).
2. `BCK` → Pi'nin **altından pin 12'nin lehim adasına** ince bir kablo lehimle.
3. `VIN` → header'a hiç dokunma; hâlihazırda lehimli **5V besleme kablosunun** üzerinden
   ayır. Zaten aynı rayı besliyor.

Her iki durumda da `SCK` ucunu GND'ye çekmeyi unutma.

> **Ses kalitesi notu:** SPI ekran ve I2S ses aynı anda çalışır, birbirini etkilemez —
> ayrı çevre birimleri, ayrı DMA kanalları. Ekran yenilenirken sesin kesilmesi gibi bir
> durum olmaz.

---

## Kernel / boot ayarları özeti

`install/setup-dac.sh` ve `install/setup-display.sh` bunları otomatik yazar
(Bookworm'da `/boot/firmware/config.txt`, öncesinde `/boot/config.txt`):

```ini
# --- PCM5102A I2S DAC ---
dtparam=audio=off          # dahili analog çıkışı kapat, DAC card 0 olsun
dtoverlay=hifiberry-dac    # PCM5102A bu overlay ile tanınır

# --- Waveshare 3.5" (A) ---
dtparam=spi=on
dtoverlay=waveshare35a:rotate=90   # yoksa: dtoverlay=piscreen,speed=16000000,rotate=90
```

`waveshare35a.dtbo` Raspberry Pi OS ile gelmez; Waveshare'in `LCD-show` deposundan
kopyalanır. Gelmiyorsa mainline `piscreen` overlay'i de ILI9486 uyumludur ve genelde
çalışır. Kurulum betiği önce `waveshare35a`'yı arar, bulamazsa `piscreen`'e düşer.

Rezistif dokunmatik ilk kullanımda **kalibrasyon** ister:
`install/calibrate-touch.sh` çalıştırıp ekrandaki dört hedefe sırayla bas.
