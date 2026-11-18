# Yayından Shorts

Twitch ya da Kick yayın bağlantısından en iyi anları bulup dikey (1080x1920), altyazılı Shorts üretir.

## Tek komut

```
python uretim.py otomatik "https://www.twitch.tv/videos/<id>"
python uretim.py otomatik "https://kick.com/<kanal>/videos/<uuid>"
```

Önce birkaç saniyelik ön kontrol yapılır (ffmpeg, modeller, disk, GPU); eksik varsa en başta durur.
Ayrıca: `python uretim.py kontrol`.

Süre: sohbet Twitch'te birkaç dk, Kick'te kalabalık yayında ~1 saat (yarıda kesilirse aynı komut
kaldığı yerden sürer); 10 klip ~10-15 dk.

## Sonuç: yalnız `Shorts/` klasörüne bakın

```
Shorts/<kanal> <tarih> <yayın başlığı>/
   01 - <başlık önerisi>.mp4 … en fazla 10 video
   RAPOR.md               her klibin kontrolü, başlık/açıklama önerileri
   ALTYAZI DÜZELT.txt     altyazıyı düz metinden düzeltme
```

**Altyazı düzeltme:** `ALTYAZI DÜZELT.txt`'te her satır videodaki bir altyazı satırıdır. Metni
düzeltin, ekrana gelmesin istiyorsanız `|` sonrasını boşaltın, `!` işaretli (model emin değil)
satır doğruysa `!`'i silin. Sonra aynı `otomatik` komutu: yalnız değişen klipler yeniden yakılır.

**Elle bakılacaklar (~5 dk):** içerik uygunluğu (çıplaklık vb.), başlık, müzik. Ses orijinaldir,
yalnız yüksekliği (-14 LUFS) eşitlenir; müziği ayırma denemesi için `--muzik-ayir`.

## Nasıl çalışır

**An seçimi** (`uretim.py`, `sohbet_klip.py`): bot, komut, link, bildirim ve selamlaşma mesajları
sayılmaz. Sohbetin doygunluğuna göre yöntem kendiliğinden seçilir:

| Sohbet | Örnek | Yöntem |
|---|---|---|
| Normal (patlamalar tabanın 3+ katı) | Kick, küçük/orta Twitch | mesaj sayısının ani artışı; yayının en yoğun %10'u |
| Doygun (tavanda, 2 katın altı) | ohnePixel | tepki mesajlarının (LOL, ????, W, emote) sıçraması |
| Arada | — | iki yöntemin adayları birlikte |

Birbirine 2 dk'dan yakın adaylar tek sahne olur. Kamera görünmeyen ve olağan tanımada konuşma
bulunmayan klip (tanıtım videosu, ara ekran) elenir, yerine yedek aday üretilir.

**Kamera** (`kamera_tespit.py`), sırayla:

1. Yüzün çevresinde kamera çerçevesinin kenarları (16:9'a yaslanır, komşu sohbet satırı kırpılır)
2. Bulunamazsa aynı yayının başka klibinde doğrulanmış kamera ölçüsü (yüz boyutuyla eşleşerek)
3. Çerçevesiz (arka planı kaldırılmış) kamera: kutu yüzden kurulur, oyunda yalnız gövde bulanıklaşır
4. Hiçbiri olmazsa kırpılmadan tam görüntü (raporda "incele: kadraj")

Bitmiş videonun kamera bölümünde yüz aranır; yoksa klip "kadraj" sorunu olarak işaretlenir.

**Altyazı** (`altyazi.py`): faster-whisper large-v3-turbo, GPU varsa GPU'da (tr / de / en).
Tekrarlanabilir; başka dile kayma, "Altyazı M.K." / "Amara.org" gibi uydurmalar atılır; az
kelime çıkarsa VAD açık yeniden tanınır.

## Dosyalar

| Dosya | Görevi |
|---|---|
| `uretim.py` | Ana akış: sohbet → adaylar → indirme → kadraj → altyazı → ses → `Shorts/` ve rapor |
| `sohbet_klip.py` | Sohbeti okur/indirir (Twitch, Kick, YouTube), eler, anları puanlar |
| `kamera_tespit.py` | Yayıncının kamerasını karelerden bulur |
| `dikey_kompozisyon.py` | FFmpeg ile dikey yerleşim (kamera üstte, altyazı şeridi, oyun altta) |
| `altyazi.py` | Kelime vurgulu altyazı ve düzeltme dosyası |
| `ses_ayristir.py` | Ses yüksekliği eşitleme; istenirse müzik ayırma (UVR Kim_Vocal_2) |
| `duzen/`, `duzenler/` | Elle doğrulanmış yayıncı düzen profilleri (varsa otomatik tespitin önüne geçer) |
| `degerlendirme/` | Aday seçiminin elle etiketlerle ölçülmesi |
| `testler/` | `python -m unittest discover -s testler -t .` |

Adım adım kullanım (aynı akışın parçaları): `aday`, `sec`, `uret`, `incele`, `durum`
(`python uretim.py <komut> -h`).

## Klasörler

| Klasör | İçerik | Git'te |
|---|---|---|
| `Shorts/` | Nihai videolar, yayın başına bir klasör | hayır |
| `yayinlar/<id>/` | Sohbet kaydı, indirilen parçalar, ara dosyalar (`uretim/klipler/`), kayıt (`uretim.json`) | hayır |
| `modeller/` | YuNet yüz modeli, UVR Kim_Vocal_2 ses modeli (adresleri ilgili .py başında) | hayır |
| `araclar/` | TwitchDownloaderCLI | hayır |

Yeniden üretimde değişen ara dosyalar silinmez, `klipler/eski/` altına tarihli taşınır.

## Gerekenler

Python 3.12, FFmpeg, `faster-whisper`, `numpy`, `onnxruntime`, `opencv-python-headless`.
GPU için (isteğe bağlı): `pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"`.
