"""Yayıncı düzen profilleri (duzenler/<ad>.json).

Bir profil bir yayıncının bir ya da daha fazla sahne düzenini tutar. Bölgeler kaynak
karenin 0-1 arası oranları olarak saklanır: [x1, y1, x2, y2]; her çözünürlükte
aynı profil kullanılır.

    {
      "surum": 1,
      "yayinci": "Se7en",
      "referans_cozunurluk": [1920, 1080],      # koordinatların ölçüldüğü kare
      "varsayilan_sahne": "oyun",
      "sahneler": {
        "oyun": {
          "tur": "oyun",                        # şimdilik yalnız "oyun"
          "ust_oran": 0.34,
          "bolgeler": {
            "kamera":  {"kutu": [...], "kaynak": "elle", "dogrulandi": true},
            "pencere": {...},                   # alt bölümün varsayılan içeriği
            "oyun":    {...}                    # isteğe bağlı: --oyun aralıklarında alt bölüm
          }
        }
      }
    }

Güvenlik kuralı: emin olunamayan hiçbir bölge kullanılmaz. Doğrulanmamış bölge, bozuk
kutu, desteklenmeyen sahne ya da farklı en-boy oranlı kaynak ProfilHatasi verir;
yanlış koordinatla video üretilmez. İleride otomatik tespit güvenilir değilse buradan
tam görüntüyü koruyan güvenli bir sahneye geçilecek (henüz yok).
"""

import json
import math
from pathlib import Path

SURUM = 1
DESTEKLENEN_TURLER = {"oyun"}
GELECEK_TURLER = {"reaction", "tam_kamera", "tam_goruntu"}
BOLGELER = {"kamera": True, "pencere": True, "oyun": False}     # ad -> zorunlu mu
KAYNAKLAR = {"elle", "otomatik"}
ORAN_TOLERANSI = 0.01                # kaynak en-boy oranı referanstan en fazla %1 sapabilir
EN_KUCUK_PIKSEL = 16
PROFIL_KLASORU = Path(__file__).resolve().parent.parent / "duzenler"


class ProfilHatasi(ValueError):
    pass


def _alanlar(d, yer, zorunlu, istege_bagli=()):
    if not isinstance(d, dict):
        raise ProfilHatasi(f"{yer}: nesne ({{...}}) olmalı, {type(d).__name__} bulundu")
    eksik = [k for k in zorunlu if k not in d]
    if eksik:
        raise ProfilHatasi(f"{yer}: eksik alan(lar): {', '.join(eksik)}")
    fazla = sorted(set(d) - set(zorunlu) - set(istege_bagli))
    if fazla:
        raise ProfilHatasi(f"{yer}: bilinmeyen alan(lar): {', '.join(fazla)}")


def _sayi(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _kutu_dogrula(kutu, yer):
    if not (isinstance(kutu, list) and len(kutu) == 4 and all(map(_sayi, kutu))):
        raise ProfilHatasi(f"{yer}.kutu: dört sayılık liste [x1, y1, x2, y2] olmalı: {kutu!r}")
    x1, y1, x2, y2 = kutu
    if not all(0 <= v <= 1 for v in kutu):
        raise ProfilHatasi(f"{yer}.kutu: değerler 0-1 arasında olmalı (oran, piksel değil): {kutu}")
    if not (x1 < x2 and y1 < y2):
        raise ProfilHatasi(f"{yer}.kutu: x1 < x2 ve y1 < y2 olmalı: {kutu}")


def dogrula(p, ad="profil"):
    """Profil sözlüğünü baştan sona denetler; ilk hatada ProfilHatasi."""
    _alanlar(p, ad, ["surum", "yayinci", "referans_cozunurluk", "varsayilan_sahne", "sahneler"],
             ["aciklama"])
    if p["surum"] != SURUM:
        raise ProfilHatasi(f"{ad}: desteklenmeyen surum {p['surum']!r} (beklenen {SURUM})")
    rc = p["referans_cozunurluk"]
    if not (isinstance(rc, list) and len(rc) == 2 and all(isinstance(v, int) and v > 0 for v in rc)):
        raise ProfilHatasi(f"{ad}.referans_cozunurluk: [genişlik, yükseklik] olmalı: {rc!r}")
    sahneler = p["sahneler"]
    if not isinstance(sahneler, dict) or not sahneler:
        raise ProfilHatasi(f"{ad}.sahneler: en az bir sahne içeren nesne olmalı")
    if p["varsayilan_sahne"] not in sahneler:
        raise ProfilHatasi(f"{ad}.varsayilan_sahne {p['varsayilan_sahne']!r} sahneler içinde yok "
                           f"({', '.join(sahneler)})")
    for sad, s in sahneler.items():
        yer = f"{ad}.sahneler.{sad}"
        _alanlar(s, yer, ["tur", "bolgeler"], ["ust_oran", "aciklama"])
        if s["tur"] not in DESTEKLENEN_TURLER | GELECEK_TURLER:
            raise ProfilHatasi(f"{yer}.tur: bilinmeyen sahne türü {s['tur']!r}")
        if s["tur"] not in DESTEKLENEN_TURLER:
            continue                                   # gelecek türler kullanılınca hata verir
        uo = s.get("ust_oran", 0.34)
        if not (_sayi(uo) and 0.1 <= uo <= 0.9):
            raise ProfilHatasi(f"{yer}.ust_oran: 0.1-0.9 arasında olmalı: {uo!r}")
        b = s["bolgeler"]
        _alanlar(b, f"{yer}.bolgeler", [k for k, z in BOLGELER.items() if z],
                 [k for k, z in BOLGELER.items() if not z])
        for bad, bolge in b.items():
            byer = f"{yer}.bolgeler.{bad}"
            _alanlar(bolge, byer, ["kutu", "kaynak", "dogrulandi"], ["guven", "aciklama", "dogrulama_notu"])
            _kutu_dogrula(bolge["kutu"], byer)
            if bolge["kaynak"] not in KAYNAKLAR:
                raise ProfilHatasi(f"{byer}.kaynak: {' / '.join(sorted(KAYNAKLAR))} olmalı: {bolge['kaynak']!r}")
            if not isinstance(bolge["dogrulandi"], bool):
                raise ProfilHatasi(f"{byer}.dogrulandi: true ya da false olmalı")
            if "guven" in bolge and not (_sayi(bolge["guven"]) and 0 <= bolge["guven"] <= 1):
                raise ProfilHatasi(f"{byer}.guven: 0-1 arasında olmalı")
    return p


def profil_yolu(ad):
    """'se7en' -> duzenler/se7en.json; yol verilmişse olduğu gibi."""
    y = Path(ad)
    if y.suffix.lower() != ".json" and len(y.parts) == 1:
        return PROFIL_KLASORU / f"{ad}.json"
    return y


def yukle(ad):
    yol = profil_yolu(ad)
    if not yol.is_file():
        mevcut = ", ".join(sorted(x.stem for x in PROFIL_KLASORU.glob("*.json"))) or "yok"
        raise ProfilHatasi(f"profil bulunamadı: {yol} (mevcut profiller: {mevcut})")
    try:
        p = json.loads(yol.read_text(encoding="utf-8"))
    except UnicodeDecodeError as e:
        raise ProfilHatasi(f"{yol}: UTF-8 okunamadı ({e})") from None
    except json.JSONDecodeError as e:
        raise ProfilHatasi(f"{yol}: geçersiz JSON, satır {e.lineno} sütun {e.colno}: {e.msg}") from None
    return dogrula(p, yol.name)


def _yuvarla(v):
    return int(math.floor(v + 0.5))                    # yarımda yukarı; banker yuvarlaması değil


def piksele(kutu, gen, yuk):
    """Oran kutusu -> (x, y, w, h) piksel. w/h dikey_kompozisyon.bolge gibi çifte
    yuvarlanır (yuv420p); yukarı yuvarlama kare kenarını taşırsa çift kalacak şekilde
    aşağı yuvarlanır (2 px geri)."""
    x1, y1, x2, y2 = (_yuvarla(v * o) for v, o in zip(kutu, (gen, yuk, gen, yuk)))
    w, h = x2 - x1, y2 - y1
    w, h = w + (w % 2), h + (h % 2)
    if x1 + w > gen:
        w -= 2
    if y1 + h > yuk:
        h -= 2
    return x1, y1, w, h


def kare_icinde(b, gen, yuk, ad):
    x, y, w, h = b
    if w < EN_KUCUK_PIKSEL or h < EN_KUCUK_PIKSEL:
        raise ProfilHatasi(f"{ad}: bölge çok küçük ({w}x{h} px, en az {EN_KUCUK_PIKSEL})")
    if x < 0 or y < 0 or x + w > gen or y + h > yuk:
        raise ProfilHatasi(f"{ad}: bölge ({x},{y} {w}x{h}) {gen}x{yuk} karenin dışına taşıyor")
    return b


def yerlesim(p, sahne, gen, yuk):
    """Profil + sahne + kaynak çözünürlüğü -> {'ust_oran', 'kamera', 'pencere', 'oyun'?} (piksel)."""
    sahne = sahne or p["varsayilan_sahne"]
    if sahne not in p["sahneler"]:
        raise ProfilHatasi(f"sahne {sahne!r} profilde yok ({', '.join(p['sahneler'])})")
    s = p["sahneler"][sahne]
    if s["tur"] not in DESTEKLENEN_TURLER:
        raise ProfilHatasi(f"sahne {sahne!r}: {s['tur']!r} türü henüz desteklenmiyor")
    rg, ry = p["referans_cozunurluk"]
    if abs((gen / yuk) / (rg / ry) - 1) > ORAN_TOLERANSI:
        raise ProfilHatasi(f"kaynak {gen}x{yuk} en-boy oranı profilin referansından ({rg}x{ry}) farklı; "
                           "düzen bu kaynağa uymayabilir. Bölgeleri --kamera/--pencere/--oyun-bolge ile elle verin.")
    sonuc = {"ust_oran": s.get("ust_oran", 0.34)}
    for ad, b in s["bolgeler"].items():
        if not b["dogrulandi"]:
            raise ProfilHatasi(f"sahne {sahne!r} bölge {ad!r} doğrulanmamış (kaynak: {b['kaynak']}); "
                               "kullanılmadı. Önizlemeyle kontrol edip \"dogrulandi\": true yapın "
                               f"ya da --{'oyun-bolge' if ad == 'oyun' else ad} ile elle verin.")
        sonuc[ad] = kare_icinde(piksele(b["kutu"], gen, yuk), gen, yuk, f"sahne {sahne!r} bölge {ad!r}")
    return sonuc
