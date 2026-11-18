# Yayından Shorts

Twitch ya da Kick yayın bağlantısından en iyi anları bulup dikey (1080x1920), altyazılı Shorts üretir.

## Tek komut

```
python uretim.py otomatik "https://kick.com/<kanal>/videos/<uuid>"
python uretim.py otomatik "https://www.twitch.tv/videos/<id>"
```

Sonuç (yalnız buraya bakmanız yeterli):

- `Shorts/<kanal> <tarih> <yayın başlığı>/01 - <başlık>.mp4` … en fazla 10 nihai video
- `yayinlar/<id>/uretim/RAPOR.md` (kopyası videoların yanında): başlık/açıklama önerileri, her klibin kontrolü, elle inceleme listesi
- `ALTYAZI DÜZELT.txt` (videoların yanında): her satır videodaki bir altyazı satırı. Metni düzeltin,
  silmek için boşaltın, doğruysa baştaki `!` işaretini kaldırın; aynı `otomatik` komutunu tekrar
  çalıştırın. Yalnız değişen klipler yeniden üretilir; ham tanıma kelime kaydında korunur.

`yayinlar/<id>/uretim/klipler/` ara dosyaları (altyazısız sürüm, kelime JSON'u, .ass, önizleme)
tutar; yeniden üretimde değişen dosyalar silinmez, `klipler/eski/` altına tarihli taşınır.

Seçim: yayının en yoğun %10'luk dilimine girmeyen anlar elenir, birbirine 2 dk'dan yakın
adaylar tek sahne (tek klip) olur; kamera bulunamayan ve neredeyse konuşma olmayan klip
(tanıtım videosu, ara ekran) çıktıya alınmaz, yerine sıradaki yedek aday üretilir.

Adım adım kullanım (aynı akışın parçaları):

```
python uretim.py aday   <link>                 # adayları bul ve listele
python uretim.py sec    <link> 1,3             # üretilecekleri seç
python uretim.py uret   <link>                 # seçilenleri üret
python uretim.py incele <link> 1 --telif temiz --kalite onay
python uretim.py durum  <link>
```

## Dosyalar

| Dosya | Görevi |
|---|---|
| `uretim.py` | Ana akış: sohbet → adaylar → indirme → kadraj → altyazı → ses → rapor |
| `sohbet_klip.py` | Sohbeti okur/indirir (Twitch, Kick, YouTube), bot/komut/selamlaşmayı eler, anları puanlar |
| `kamera_tespit.py` | Yayıncının kamerasını karelerden otomatik bulur |
| `dikey_kompozisyon.py` | FFmpeg ile dikey yerleşim (kamera üstte, oyun altta) |
| `altyazi.py` | faster-whisper ile kelime vurgulu altyazı (tr / de / en) |
| `ses_ayristir.py` | Konuşmayı ayırıp arka plan müziğini kaldırmayı dener |
| `duzen/`, `duzenler/` | Elle doğrulanmış yayıncı düzen profilleri (varsa otomatik tespitin önüne geçer) |
| `degerlendirme/` | Aday seçiminin elle etiketlerle ölçülmesi |
| `testler/` | Birim testleri: `python -m unittest discover -s testler -t .` |

## Klasörler

| Klasör | İçerik | Git'te |
|---|---|---|
| `Shorts/` | Nihai videolar, yayın başına bir klasör | hayır |
| `yayinlar/<id>/` | Her yayının sohbeti, indirilen parçaları, ara dosyaları ve kaydı | hayır |
| `modeller/` | YuNet yüz modeli, UVR Kim_Vocal_2 ses modeli | hayır |
| `araclar/` | TwitchDownloaderCLI (ve indirilen zip'i) | hayır |

Modellerin indirme adresleri `kamera_tespit.py` ve `ses_ayristir.py` başında yazılı.

## Gerekenler

Python 3.12, FFmpeg, `faster-whisper`, `numpy`, `onnxruntime`, `opencv-python-headless`.

## Kurallar

- Eski çıktılar silinmez; yeniden üretimde `eski/` alt klasörüne tarihli adla taşınır.
- Kamera bulunamazsa kırpılmadan tam görüntü kullanılır ve raporda belirtilir.
- Telif onayı otomatik verilmez; müzik kaldırma tek başına telif sorununu çözmez.
