# Klip adayı değerlendirme raporu

Tarih: 2026-11-18 · Algoritma: `v1` · Ağırlıklar: {'hacim': 0.3, 'kisi': 0.2, 'ivme': 0.3, 'tepki': 0.2}, soru-cevap x0.6, kişi başı ≤2

Yalnız **video incelemesiyle doğrulanmış** etiketler kesin sayılır. Sohbetten çıkarılan ön etiketler ve etiketsiz adaylar **belirsiz**dir. Precision@k alt sınırı belirsizleri başarısız, üst sınırı başarılı varsayar.

## Özet

| Yayın | P@3 (alt–üst) | P@5 (alt–üst) | Kesinleşen ilk 5 |
|---|---|---|---|
| Se7en (2930392468) | 0.67–1.00 | 0.40–1.00 | 2/5 |
| SCHRADIN (2931374918) | 0.33–1.00 | 0.20–1.00 | 1/5 |
| **Toplam** | **0.50–1.00** | **0.30–1.00** | |

## Se7en (2930392468)

İncelenen video aralıkları: 03:19:40–03:21:05, 03:49:25–03:50:55

### Başarılı (2)

| Sıra | Zaman | Mesaj / kişi | Puan | Ad | Not |
|---|---|---|---|---|---|
| 1 | 03:50:15 | 61 / 36 | 91.8 | GEÇTİ | Arka arkaya iki yükseltme başarısı (~03:49:52 ve ~03:50:17) |
| 2 | 03:20:30 | 58 / 31 | 83.0 | GEÇTİ | Item yükseltme tuttu; animasyon 03:20:30, ana sohbet dalgası 03:20:28-33 |

### Başarısız (0)

—

### Belirsiz (8)

| Sıra | Zaman | Mesaj / kişi | Puan | Ad | Not |
|---|---|---|---|---|---|
| 3 | 03:46:30 | 13 / 11 | 42.4 | GEÇTİ | sohbete göre olası başarılı: Küçük GEÇTİ dalgası (11 kişi); video incelenmedi |
| 4 | 03:39:15 | 14 / 11 | 38.2 | GEÇTİ | sohbete göre olası başarılı: Küçük GEÇTİ dalgası (11 kişi); video incelenmedi |
| 5 | 02:56:15 | 10 / 9 | 32.1 | ADAMA Bİ DÜNYA | etiket yok; video incelenmedi |
| 6 | 03:25:45 | 13 / 12 | 31.8 | ABONELİK | sohbete göre olası başarısız: Hediye abonelik sonrası teşekkür zinciri; klip değeri düşük |
| 7 | 03:36:45 | 10 / 9 | 30.9 | ALAN VARMI ARKADAŞA | etiket yok; video incelenmedi |
| 8 | 01:01:45 | 12 / 9 | 27.4 | MAKRO | etiket yok; video incelenmedi |
| 9 | 03:23:15 | 12 / 11 | 26.7 | ATMALISIN | etiket yok; video incelenmedi |
| 10 | 04:33:15 | 10 / 10 | 25.2 | AYNI | etiket yok; video incelenmedi |

## SCHRADIN (2931374918)

İncelenen video aralıkları: 01:34:15–01:35:45

### Başarılı (1)

| Sıra | Zaman | Mesaj / kişi | Puan | Ad | Not |
|---|---|---|---|---|---|
| 3 | 01:35:00 | 42 / 42 | 73.3 | FALLSCHIRM | Paraşüt açılmadan çakılma; atlayış 01:34:55, çakılma 01:34:58 |

### Başarısız (0)

—

### Belirsiz (9)

| Sıra | Zaman | Mesaj / kişi | Puan | Ad | Not |
|---|---|---|---|---|---|
| 1 | 00:36:45 | 49 / 46 | 80.0 | OHA DER MEISTER | sohbete göre olası başarılı: Kahkaha (46 kişi); reaksiyon bölümü, içerik başka bir üreticiye ait olabilir |
| 2 | 02:45:45 | 47 / 46 | 77.2 | GUT | sohbete göre olası başarılı: 47 mesajın 28'i kahkaha |
| 4 | 01:59:00 | 32 / 32 | 59.1 | ALTER SCHWEDE | sohbete göre olası başarılı: Kötü sürüş kahkahası (32 kişi) |
| 5 | 00:13:00 | 45 / 43 | 56.4 | DREIECK | sohbete göre olası başarısız: Soru-cevap: kontrol tuşları |
| 6 | 01:14:45 | 31 / 31 | 53.5 | PROBLEME | etiket yok; video incelenmedi |
| 7 | 02:33:00 | 26 / 25 | 52.8 | GEGNER | etiket yok; video incelenmedi |
| 8 | 00:07:45 | 35 / 31 | 50.9 | MOIN | sohbete göre olası başarısız: Mikrofon kapalı: 'Mikro ist aus' spamı ve selamlaşma emote'ları |
| 9 | 00:06:45 | 38 / 28 | 50.4 | MUTE | sohbete göre olası başarısız: Mikrofon kapalı: 'Mikro ist aus' spamı ve selamlaşma emote'ları |
| 10 | 01:18:00 | 27 / 27 | 49.4 | ERNST | etiket yok; video incelenmedi |

## Yeni etiket ekleme

`etiketler.json` içine aralık ekleyin. Adayın videosunu izleyip doğruladıysanız `"dogrulama": "video"`, yalnız sohbeti okuduysanız `"sohbet"` yazın.
