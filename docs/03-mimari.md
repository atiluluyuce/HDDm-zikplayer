# Mimari

## Genel görünüm

```
                     ┌────────────────────────────────────┐
  USB HDD ───────────┤  MPD (ses motoru)                  │
  /media/music       │  çözme · gapless · ALSA            ├──► PCM5102A ──► amfi
                     └─────────────────┬──────────────────┘
                                       │ MPD protokolü (localhost:6600)
                     ┌─────────────────┴──────────────────┐
                     │  hddmusicplayer-api                │
                     │  ┌──────────────────────────────┐  │
                     │  │ player  durum + geçmiş       │  │
                     │  │ library gezinme / arama      │  │
                     │  │ recomm. öneri motoru         │  │
                     │  │ scanner artımlı tarama       │  │
                     │  └──────────────────────────────┘  │
                     │  SQLite: /var/lib/hddmusicplayer   │
                     └──┬───────────────────────┬─────────┘
                HTTP+SSE│                       │HTTP+SSE
            ┌───────────┬──────────┐  ┌─────────┬──────────┐
            │ hddmusicplayer-panel │  │ web arayüzü :8080  │
            │ /dev/fb1 · evdev     │  │ telefon / tablet   │
            │ EC11 encoder         │  │ / bilgisayar       │
            └──────────────────────┘  └────────────────────┘
```

Üç ayrı süreç: **mpd**, **hddmusicplayer-api**, **hddmusicplayer-panel**. Panel çökerse
müzik kesilmez; API yeniden başlarsa MPD çalmaya devam eder.

---

## Neden bu tercihler

### MPD'yi ses motoru olarak kullanmak

Kendi çalarımızı yazmak yerine MPD kullanılıyor çünkü gapless çalma, ALSA
tampon yönetimi, ReplayGain ve onlarca kodek yıllardır olgunlaşmış durumda.
hddmusicplayer'ın işi gezinme, öneri ve arayüz.

Ama **gezinme ve arama MPD üzerinden yapılmıyor.** MPD'nin sorgu dili
(`find`, `list`) etiket üzerinde doğrusal tarama yapar; 50 bin parçalık bir
kütüphanede Pi 3'te saniyeler sürer. Bunun yerine kendi SQLite indeksimiz var:
indeksli, FTS destekli, milisaniyeler mertebesinde.

İki tarafın ortak noktası dosya yolu: veritabanındaki `tracks.path`,
MPD'nin `uri`'siyle birebir aynı (ikisi de `music_directory`'ye göreli).

### Starlette, FastAPI değil

FastAPI pydantic'e bağımlı; pydantic v2'nin çekirdeği Rust ile derlenmiş.
32-bit Raspberry Pi OS (armv7) için hazır wheel yayınlanmıyor, pip kaynaktan
derlemeye kalkıyor ve Pi 3'te bu işlem çok uzun sürüyor. Starlette + uvicorn
tamamen saf Python — kurulum saniyeler.

### WebSocket değil SSE

Durum akışı tek yönlü: sunucu → istemci. Komutlar zaten normal POST. SSE bunun
için yeterli, ek paket gerektirmiyor ve tarayıcı `EventSource` ile kopan
bağlantıyı kendisi yeniden kuruyor.

### Panelde tarayıcı değil framebuffer

3.5" ekran SPI üzerinden sürülüyor: 320×480×16 bit = 300 KB'lik kare, 32 MHz'de
kabaca 10-15 fps. Üstüne X sunucusu + Chromium eklemek 1 GB RAM'in yarısını
yiyor ve arayüz takılıyor.

Bunun yerine Pillow ile RAM'de kare üretilip `/dev/fb1`'e yazılıyor. İki
optimizasyon SPI hattını rahat bırakıyor:

- kare RGB565'e **numpy** ile çevriliyor (saf Python'da ~100 ms, numpy ile ~2 ms)
- yalnızca **değişen satır aralığı** yazılıyor (`framebuffer.py:show`)

Sonuç: ~40 MB RAM, anında açılış, duraklamışken SPI hattı tamamen boşta.

---

## Veri modeli

```
artists ──┬── albums ──┬── tracks ──┬── plays
          │            │            └── transitions (prev_id, next_id)
          └────────────┴─────────────── search (FTS5)
```

| Tablo | Rolü |
|---|---|
| `tracks` | Ana kayıt. Yol, etiketler, süre + öğrenilen sayaçlar (`play_count`, `skip_count`, `last_played`, `rating`) |
| `albums` / `artists` | Tarama sonunda parçalardan **türetilir** (`_rebuild_aggregates`). Gezinmede GROUP BY maliyetini ortadan kaldırır |
| `plays` | Her çalma bir satır: zaman, saat, haftanın günü, sonuna kadar mı, hangi bağlamdan |
| `transitions` | Hangi parçadan sonra hangisi çalınmış. Öneri motorunun en güçlü sinyali |
| `search` | FTS5 sanal tablosu; parça, albüm ve sanatçı tek indekste |

### Türkçe arama

FTS5'in `unicode61` tokenleştiricisi `remove_diacritics 2` ile `ş→s`, `ğ→g`,
`ü→u` gibi aksanları çözer — ama **`ı` harfini `i` ile eşleştirmez**, çünkü `ı`
ayrı bir kod noktası, `i`'nin aksanlı hâli değil.

Bu yüzden indekse metnin `fold()`'lanmış hâli yazılıyor (`db.py:fold`):
Türkçe harfler ASCII'ye indirgeniyor, sorgu da aynı işlemden geçiyor. Sonuç:
"sarki" araması "Şarkı"yı buluyor. Görüntülenecek veriler zaten `ref_id` ile
asıl tablolardan çekildiği için indekste okunabilir metin tutmaya gerek yok.

Kullanıcı girdisi doğrudan FTS5'e verilmiyor — tırnak, yıldız, `NOT` gibi
işaretler sözdizimi hatası fırlatır. `library._fts_query` yalnızca kelimeleri
alıp her birini tırnaklayarak önek sorgusuna çeviriyor.

---

## Tarama

`scanner.py` artımlı çalışır:

1. `UPDATE tracks SET seen = 0`
2. Mevcut kayıtlar tek seferde belleğe alınır (dosya başına SELECT yok)
3. Dizin ağacı gezilir; **mtime + boyut** değişmemişse etiket okunmaz
4. Değişen/yeni dosyalar `mutagen` ile okunur
5. Kapak: önce gömülü (ID3 APIC / MP4 covr / FLAC picture), yoksa klasördeki
   `cover.jpg` / `folder.jpg` (klasör bazında önbelleklenir)
6. Diskte kalmayanlar silinir
7. Albüm/sanatçı sayaçları ve FTS indeksi yeniden türetilir
8. Artık işaret edilmeyen kapak dosyaları temizlenir

Kapaklar **içerik özetine (sha1) göre** saklanır: aynı kapağı paylaşan bütün
parçalar tek dosyayı işaret eder. Her kapak iki boyutta üretilir — 320 px
(liste ve ızgara) ve 800 px (tam ekran). İçerik adresli olduğu için HTTP
katmanında `immutable` önbellek başlığıyla sunulabiliyor.

---

## Öneri motoru

`recommender.py`. Tamamen yerel: internet, hesap veya harici servis yok.

### Skorlama

Her aday parça için katsayılar çarpılır:

```
skor = beğeni × tanıdıklık × tazelik × atlama × süre × saat_profili [× benzerlik]
```

| Katsayı | Değer |
|---|---|
| Sevilen | ×2.5 · Yasaklı → 0 (tamamen elenir) |
| Hiç çalınmamış | ×1.35 |
| Çalma sayısı | `1 + 0.06 × min(sayı, 10)` — doyuma ulaşan tanıdıklık |
| Son çalınma | <6sa ×0.05 · <24sa ×0.2 · <3g ×0.45 · <7g ×0.7 · <30g ×1.0 · sonrası ×1.25 |
| Atlama oranı | >%60 ×0.25 · >%35 ×0.6 |
| Süre | <30sn ×0.15 · <60sn ×0.55 · >15dk ×0.6 |
| Saat profili | 0.7 – 1.6 arası |

**Saat profili** (`_hour_affinity`): bir türün şu anki saat ±1 penceresindeki
payı, genel payına bölünür. 1.0'ın üstü "bu saatlerde daha çok dinlenen tür"
demek. 60'tan az çalma varsa sinyal devre dışı; sonuç 10 dakika önbelleklenir.

### Seçim

Ağırlıklı, tekrarsız örneklem için **üstel anahtar yöntemi**: her aday için
`key = -ln(U) / w` üretilip anahtarlara göre sıralanır. Bu, ağırlıklarla
orantılı tekrarsız bir örneklem verir ve tek geçişte hesaplanır.

Sonra sıralı gezinirken çeşitlilik kuralları uygulanır: aynı sanatçı 5, aynı
albüm 8 parça arayla. Kurallar yüzünden liste dolmazsa kısıtlar kademeli
gevşetilir (5/8 → 2/3 → kısıtsız).

Tüm kütüphaneyi skorlamak yerine **2500 parçalık rastgele örneklem** alınıyor;
50 bin parçalık arşivde sonuç istatistiksel olarak aynı, maliyet Pi 3'te
~30-50 ms.

### "Benzerlerini çal" (radyo)

Tohum parçaya benzerlik **toplamalı** hesaplanıp tek seferde ölçekleniyor:

```
benzerlik = 0.55·aynı_sanatçı + 0.35·aynı_tür + 0.10·aynı_albüm
          + yıl_yakınlığı + min(0.60, 0.25 × geçiş_sayısı)

katsayı   = 0.04 + benzerlik × 3.0
```

> **Neden toplamalı?** İlk sürümde her sinyal ayrı ayrı çarpılıyordu; o
> yaklaşımda "hiçbir ortak yanı yok" durumu 1.0'da kalıyor, yani ilgisiz
> parçalar nötr sayılıp listeye doluşuyordu. Testte radyo listesinin yalnızca
> %27'si aynı türdendi. Düşük taban (`0.04`) + tek ölçekleme ile bu oran
> %60'a çıktı ve ilgisiz parçalar ~25 kat bastırıldı — tamamen elenmediler,
> arada bir sürpriz çıkması listeyi canlı tutuyor.

Rastgele örneklem tek başına yetmiyor: 50 bin parçalık arşivde tohum sanatçının
2500'lük örnekleme düşme ihtimali düşük. Bu yüzden `_targeted_rows` aynı
sanatçı, aynı tür ve geçiş ilişkisi olan parçaları ayrıca çekip havuza ekliyor.

### Hazır mixler

`mixes()` ana ekran için üretir; 5'ten az parçası olanlar elenir.

| Mix | İçerik |
|---|---|
| Akıllı Karıştır | Tüm skorlama devrede |
| Favorilerin | `rating > 0` |
| Hiç Çalmadıkların | `play_count = 0` |
| Yeniden Keşfet | Çalınmış ama 60+ gündür dokunulmamış |
| En Çok Çaldıkların | `play_count >= 3` |
| Bu Saatlerde | Saat profilinin en yüksek 3 türü |
| Tür mixleri | En kalabalık 4 tür |

---

## Çalma geçmişi nasıl kaydediliyor

`player.py` MPD'nin `songid` değerini izler. Değiştiği anda **biten** parça
geçmişe işlenir:

```
tamamlandı = son_konum / süre >= 0.5      (HDDMUSICPLAYER_PLAY_COMPLETION_RATIO)
```

- Tamamlandıysa `play_count++`, değilse `skip_count++`
- `transitions(önceki → biten)` sayacı artırılır — **yalnızca tamamlanmışsa**.
  Atlanan bir parçadan sonrasını "birlikte sevilen" saymak yanıltıcı olurdu.

MPD'nin `idle` komutu ilerleme için olay üretmediğinden, çalarken saniyede bir
`status` çekiliyor (`TICK_SECONDS`). Bu hem ilerleme çubuğunu besliyor hem de
geçiş anındaki son konumu biliyor olmamızı sağlıyor.

### Otomatik kuyruk

Kuyrukta `HDDMUSICPLAYER_AUTOQUEUE_MIN` (5) parçadan az kaldığında öneri motoru
`HDDMUSICPLAYER_AUTOQUEUE_BATCH` (15) parça ekler. Tohum olarak çalan parça kullanılır, o
anki kuyruk tamamen dışlanır. Böylece kuyruk hiç bitmez ve akış çalan parçaya
bağlı kalır.

---

## Panel arayüzü

`panel/` altında, ayrı süreç. Sunucuyla yalnızca HTTP + SSE üzerinden konuşur —
standart kütüphane dışında bir şey kullanmadan (`urllib`).

```
framebuffer.py  /dev/fb1'e RGB565 yazma, satır bazlı kısmi güncelleme
input.py        evdev dokunmatik + gpiozero EC11 -> tek olay kuyruğu
ui.py           tema, yazı tipleri, poligonla çizilen simgeler
app.py          ekran yığını (Çalıyor / Menü / Listeler / Ayarlar)
calibrate.py    dört noktalı dokunmatik kalibrasyonu
```

Girdi kaynakları tek kuyrukta birleşiyor: arayüz olayın dokunmadan mı encoder'dan
mı geldiğini bilmiyor. Biri çalışmasa diğeriyle cihaz tam kullanılabilir.

Simgeler yazı tipi glifleriyle değil poligonla çiziliyor — hangi fontun hangi
sembolü içerdiğine bağlı kalmamak ve küçük ekranda net görünmeleri için.

Çizim yalnızca gerektiğinde yapılıyor: çalarken ilerleme çubuğu için saniyede
`PANEL_FPS` kez, duraklamışken sadece durum değişince. Boştayken SPI hattı
tamamen serbest.

---

## Ses zinciri

```
MP3/AAC ──► MPD (çözme) ──► ALSA softvol ──► hw:sndrpihifiberry ──► I2S ──► PCM5102A
```

MPD'de `auto_resample`, `auto_format`, `auto_channels` kapalı: dosya neyse
DAC'a o gidiyor, yeniden örnekleme yapılmıyor.

**PCM5102A'nın donanımsal ses seviyesi kaydı yok** — yonga her zaman tam çıkış
verir. İki mod destekleniyor:

| Mod | Davranış |
|---|---|
| `softvol` (varsayılan) | ALSA softvol araya girer, arayüzden ses ayarlanır |
| `fixed` | Katman tamamen çıkar, çıkış bit-perfect, seviye amfiden ayarlanır |

`fixed` modda arayüzdeki ses kontrolleri gizlenir ve panel "SABİT ÇIKIŞ" yazar.

> softvol denetimi ALSA'da ancak PCM ilk kez açıldığında oluşur. `setup-dac.sh`
> bir saniyelik sessizlik çalarak bunu baştan yaratıyor; yoksa MPD ilk açılışta
> mikseri bulamayıp seviye kontrolünü kapatıyordu.

---

## Test durumu

`db`, `library`, `recommender`, `scanner` toplama katmanı 600 parçalık sentetik
bir kütüphaneyle doğrulandı: Türkçe arama, kötü niyetli arama girdisi, çeşitlilik
kuralları, yasaklı parça filtresi, geçiş kaydı, tüm mix türleri, dışlama mantığı.

Radyo modunun benzerlik oranı da bu testte ölçülüyor — yukarıda anlatılan
toplamalı skorlama düzeltmesi bu testin başarısız olması sayesinde bulundu.

Donanıma bağlı katmanlar (`mpd.py` protokol istemcisi, `framebuffer.py`,
`input.py`, `player.py`) gerçek kartta doğrulanmayı bekliyor.
