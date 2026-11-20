#!/usr/bin/env python3
"""uretim.py - Tek Twitch VOD'undan Shorts adayları: sohbet analizi -> seçim -> parça
indirme -> dikey kompozisyon -> altyazı -> doğrulama -> elle inceleme kaydı.

Mevcut araçları çağırır, yerlerine geçmez: sohbet_klip.py (analiz, Kick sohbeti),
TwitchDownloaderCLI ya da Kick HLS + ffmpeg (yalnız gereken aralıklar),
dikey_kompozisyon.py (--duzen / --guvenli), altyazi.py.

    python uretim.py aday   <VOD linki|ID>        # 10-15 aday, puan/zaman/sohbet
                            # Twitch: https://www.twitch.tv/videos/<id>
                            # Kick:   https://kick.com/<kanal>/videos/<uuid>
    python uretim.py sec    <VOD> 2,5,7 [--oyun 5:26.05-40]
    python uretim.py uret   <VOD> [--guvenli-onay]
    python uretim.py incele <VOD> <sıra> --telif temiz --kalite onay [--not "..."]
    python uretim.py durum  <VOD>

Kurallar:
  * Her VOD'un kaydı yayinlar/<id>/uretim/uretim.json; çıktılar aynı klasörde.
  * Her adım önce <ad>.part.<uzantı> üretir; yalnız doğrulanırsa eski dosyanın yerine
    geçer. Hata olursa önceki başarılı dosya olduğu gibi kalır.
  * Parametreleri değişmemiş, kayıtlı ve yerinde duran çıktı yeniden üretilmez.
  * Kayıtta olmayan mevcut dosyanın üzerine yazılmaz.
  * Profil bilinmiyorsa ya da profilin sahne geçişi bu klip için belirsizse kırpılmaz:
    güvenli tam görüntü. Bilinmeyen profilde bu yerleşim için --guvenli-onay gerekir.
  * Ses akışı yoksa hata; düşük ses / sessizlik yalnız inceleme uyarısı.
  * Telif "temiz" yalnız elle inceleme yapıldığını kaydeder; risk sıfırlanmış sayılmaz.
"""

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import sohbet_klip as sk
from dikey_kompozisyon import araliklar
from duzen import profil

PROJE = Path(__file__).resolve().parent
TDCLI = PROJE / "araclar" / "TwitchDownloaderCLI" / "TwitchDownloaderCLI.exe"

SURE, ONCE, PAY, ADET, DILIM = 40.0, 25.0, 5.0, 15, 15.0
HEDEF = "5-8"
EN_KISA = 5.0                   # bundan kısa kalan (VOD sonu) aday üretilmez
SURE_TOLERANS = 0.1             # sn
PARCA_TOLERANS = 0.5            # indirilen parça en az gereken süre - bu kadar olmalı
DUSUK_SES_DB = -35.0            # ortalama ses bunun altındaysa uyarı
SESSIZLIK_DB, SESSIZLIK_SN = -50, 2.0
GUVENLI_ALTYAZI_Y = 1420        # tam görüntü bandı 656-1264 px; altyazı bandın altında
ALTYAZI_DILLERI = {"tr", "de", "en"}
TELIF = ("bekliyor", "temiz", "sorunlu")
KALITE = ("bekliyor", "onay", "red")
TELIF_NOTU = ("Telif 'temiz' yalnız elle inceleme yapıldığını kaydeder. Müzik, yayıncı izni ve "
              "üçüncü taraf görüntü riskleri ortadan kalkmış sayılmaz.")
REACTION = re.compile(r"react|reaktion|reagier|tepki", re.I)
# Sohbette üçüncü taraf video/müzik ipucu (reaction, şarkı, YouTube/TikTok izleme)
UCUNCU_TARAF = re.compile(r"\b(react|reaction|youtube|tiktok|video|klip|şarkı|sarki|müzik|muzik|song|"
                          r"music|lied|musik|spotify)\b", re.I)

# Olay bazlı zamanlama (sabit "25 sn önce" yerine)
GECIKME = 6.0                   # sohbet tepkisi olaydan ~bu kadar sonra başlar (yayın gecikmesi + yazma)
ONCE_OLAY = 10.0                # olaydan önce bağlam
SONRA_EN_AZ = 12.0              # olaydan sonra en az
KLIP_MIN, KLIP_MAX = 20.0, 60.0
INDIR_ONCE, INDIR_SONRA = 25, 55     # olay çevresinde indirilen pencere (sn); tüm yayın indirilmez
OTOMATIK_ADET = 10
SAHNE = 120                     # birbirine bu kadar sn'den yakın adaylar tek sahne (tek klip) sayılır
SAHNE_BAGLAM = 20               # birleşen sahnede en güçlü olaydan en fazla bu kadar önce başlanır
YOGUN_YUZDE = 0.90              # aday dilim, yayının bu yüzdelik dilim yoğunluğunda ya da üstünde olmalı
ZAYIF_DK_MESAJ = 20            # analiz aralığında dakikada bundan az mesaj: sohbet an seçmeye yetmez -> ses
ZAYIF_KISI_MIN = 8              # zayıf sohbette yine de sayılan sohbet anı: dilimde en az bu kadar kişi
SES_KARE = 0.5                  # ses seviyesi dilimi, sn
SES_TABAN_SN = 240              # olağan ses seviyesi: çevredeki bu kadar sn'nin ortancası
SES_ESIK_DB = 8.0               # olağan seviyenin bu kadar üstü "yükselme"
SES_EN_AZ_SN = 1.0              # yükselme en az bu kadar sürmeli (tek patlama sesi değil)
SES_SESSIZ_DB = -55.0           # olağan seviye bunun altındaysa yayın sessiz/duraklatılmış sayılır
CAGRI_PAY = 0.30               # dilimin bu payı aynı, heyecan olmayan ifadeyse (me, jbl, ps5) çağrı cevabı
HEYECAN_EK = set("""w ww www l wtf omg noway nooo letsgo lets pog poggers kekw lul lulw omegalul icant lmao
lmfao lmaoo holy clutch insane crazy dead yoo yooo sheesh bruh""".split())
KARMA_ORAN = 3.0               # 2.0-3.0 arası: yarı doygun, iki yöntemin adayları birlikte
DOYGUN_ORAN = 2.0              # en yoğun dilimler ortancanın bu katına çıkamıyorsa sohbet doygun (tavanda)
YEDEK = 5                      # boş çıkan klip yerine sırayla üretilecek yedek aday sayısı
BOS_KELIME = 10                 # kamera yok + bundan az konuşma = yayıncı anı değil (tanıtım/ara ekran)
UST_YUZ_MIN = 0.5               # bitmiş videoda kamera bölümünde yüz bundan az karede görünürse: kadraj sorunu
BASLIK_EN_FAZLA = 8             # başlık önerisi en fazla bu kadar kelime
BASLIK_SON_ATILIR = set("a an the and but or to of with for is ve ile bir ama da de ki ya mi mı".split())
CIKTI = PROJE / "Shorts"      # nihai MP4'ler: Shorts/<kanal> <tarih> <başlık>/NN - <başlık>.mp4
KENAR = 180                     # yayının ilk/son bu kadar sn'si aday olmaz


class AdimHatasi(Exception):
    pass


# --------------------------------------------------------------------------
# Kayıt (uretim.json)
# --------------------------------------------------------------------------

KICK_VOD = re.compile(r"\s*(?:https?://)?(?:www\.)?kick\.com/([^/\s]+)/videos?/"
                      r"([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})(?:[/?#].*)?\s*")
UUID = re.compile(r"\s*([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})\s*")


def vod_coz(s):
    """-> (platform, kimlik, kanal). Twitch: sayısal ID; Kick: video UUID'si (+ kanal adresten)."""
    m = re.fullmatch(r"\s*(?:https?://)?(?:www\.|m\.)?(?:twitch\.tv/videos/)?(\d{5,})(?:[/?#].*)?\s*", s)
    if m:
        return "twitch", m.group(1), None
    m = KICK_VOD.fullmatch(s)
    if m:
        return "kick", m.group(2).lower(), m.group(1).lower()
    m = UUID.fullmatch(s)
    if m:
        return "kick", m.group(1).lower(), None
    raise SystemExit(f"VOD bağlantısı/ID'si tanınmadı: {s!r} (örn. https://www.twitch.tv/videos/2931374918 "
                     "ya da https://kick.com/<kanal>/videos/<uuid>)")


def vod_id(s):
    return vod_coz(s)[1]


def klasor(vod):
    return PROJE / "yayinlar" / vod


def kayit_yolu(vod):
    return klasor(vod) / "uretim" / "uretim.json"


def kayit_oku(vod, zorunlu=True):
    yol = kayit_yolu(vod)
    if not yol.exists():
        if zorunlu:
            raise SystemExit(f"Kayıt yok: {yol}\nÖnce: python uretim.py aday {vod}")
        return None
    try:
        return json.loads(yol.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{yol} bozuk (satır {e.lineno}): elle düzeltin; dosyalara dokunulmadı.")


def kayit_yaz(k):
    yol = kayit_yolu(k["vod"])
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_name(yol.name + ".part")
    gecici.write_text(json.dumps(k, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(gecici, yol)                       # yarım yazılmış kayıt kalmaz


def simdi():
    return datetime.now().isoformat(timespec="seconds")


def imza(*parcalar):
    return hashlib.sha1(json.dumps(parcalar, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def saat(t):
    return sk.sure_yaz(t).replace(":", "")


def goreli(yol):
    try:
        return str(Path(yol).resolve().relative_to(PROJE)).replace("\\", "/")
    except ValueError:
        return str(yol)


def aday_bul(k, secici):
    """Sıra numarası (aday tablosundaki) ya da klip kökü -> aday."""
    for a in k["adaylar"]:
        if str(a["sira"]) == str(secici).lstrip("0") or a["kok"] == secici:
            return a
    raise SystemExit(f"Aday bulunamadı: {secici} (python uretim.py durum {k['vod']})")


# --------------------------------------------------------------------------
# Komut çalıştırma ve doğrulama
# --------------------------------------------------------------------------

def calistir(komut, ad):
    print(f"    $ {' '.join(Path(c).name if i < 2 else c for i, c in enumerate(map(str, komut)))}", flush=True)
    try:
        r = subprocess.run([str(c) for c in komut], cwd=PROJE)
    except OSError as e:
        raise AdimHatasi(f"{ad}: çalıştırılamadı ({e})") from None
    if r.returncode != 0:
        raise AdimHatasi(f"{ad}: çıkış kodu {r.returncode}")


def _yakala(komut):
    return subprocess.run(komut, capture_output=True, text=True, encoding="utf-8", errors="replace")


def probe(yol):
    r = _yakala(["ffprobe", "-v", "error", "-show_entries",
                 "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", str(yol)])
    if r.returncode != 0:
        raise AdimHatasi(f"ffprobe okuyamadı: {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)


def cozum_hatalari(yol):
    """Dosyanın tamamını çözer; ffmpeg'in bildirdiği hata satırları (bütünlük)."""
    r = _yakala(["ffmpeg", "-v", "error", "-nostdin", "-i", str(yol), "-map", "0", "-f", "null", "-"])
    satirlar = [s for s in r.stderr.splitlines() if s.strip()]
    if r.returncode != 0 and not satirlar:
        satirlar = [f"ffmpeg çıkış kodu {r.returncode}"]
    return satirlar


def ses_olc(yol):
    r = _yakala(["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", str(yol), "-map", "0:a:0", "-af",
                 f"silencedetect=n={SESSIZLIK_DB}dB:d={SESSIZLIK_SN},volumedetect", "-f", "null", "-"])
    ort = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", r.stderr)
    tepe = re.search(r"max_volume: (-?[\d.]+|-inf) dB", r.stderr)
    bas = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    son = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", r.stderr)]
    sessiz = [(max(0.0, b), son[i] if i < len(son) else None) for i, b in enumerate(bas)]
    f = lambda m: None if not m else float("-inf") if m.group(1) == "-inf" else float(m.group(1))
    return {"ortalama_db": f(ort), "tepe_db": f(tepe), "sessizlik": sessiz}


def degerlendir(pr, beklenen=None, en_az=None, boyut=None, ses=None, cozum=()):
    """Saf kural: (hatalar, uyarılar). Hata dosyayı reddeder; uyarı yalnız incelemeye yazılır."""
    hatalar, uyarilar = [], []
    akis = pr.get("streams", [])
    v = [s for s in akis if s.get("codec_type") == "video"]
    a = [s for s in akis if s.get("codec_type") == "audio"]
    try:
        sure = float(pr["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        sure = None
    if sure is None:
        hatalar.append("süre okunamadı")
    elif beklenen is not None and abs(sure - beklenen) > SURE_TOLERANS:
        hatalar.append(f"süre {sure:.2f} sn, beklenen {beklenen:.2f} sn (±{SURE_TOLERANS})")
    elif en_az is not None and sure < en_az - PARCA_TOLERANS:
        hatalar.append(f"süre {sure:.2f} sn, en az {en_az:.2f} sn gerekiyordu")
    if not v:
        hatalar.append("görüntü akışı yok")
    elif boyut and (v[0].get("width"), v[0].get("height")) != boyut:
        hatalar.append(f"görüntü {v[0].get('width')}x{v[0].get('height')}, beklenen {boyut[0]}x{boyut[1]}")
    elif boyut and v[0].get("r_frame_rate") != "60/1":
        hatalar.append(f"kare hızı {v[0].get('r_frame_rate')}, beklenen 60/1")
    if not a:
        hatalar.append("ses akışı yok")
    if cozum:
        hatalar.append(f"dosya bütünlüğü: {len(cozum)} çözme hatası ({cozum[0][:120]})")
    if a and ses:
        o = ses.get("ortalama_db")
        if o is None or o == float("-inf") or (ses.get("tepe_db") or 0) <= -90:
            uyarilar.append("ses tamamen sessiz görünüyor (dinleyin)")
        elif o < DUSUK_SES_DB:
            uyarilar.append(f"düşük ses seviyesi (ortalama {o:.1f} dB)")
        for b, s in ses.get("sessizlik", []):
            uyarilar.append(f"sessizlik {b:.1f}-{'son' if s is None else f'{s:.1f}'} sn")
    return hatalar, uyarilar


def dogrula_dosya(yol, beklenen=None, en_az=None, boyut=None, tam=True):
    pr = probe(yol)
    ses = ses_olc(yol) if tam and any(s.get("codec_type") == "audio" for s in pr.get("streams", [])) else None
    return degerlendir(pr, beklenen, en_az, boyut, ses, cozum_hatalari(yol) if tam else ())


# --------------------------------------------------------------------------
# Adım: üret -> doğrula -> yerine koy; değişmediyse atla
# --------------------------------------------------------------------------

def gecici_ad(hedef):
    return hedef.with_name(f"{hedef.stem}.part{hedef.suffix}")


def eski_yere(yol):
    """Değiştirilen çıktıyı silmeden aynı klasördeki eski/ altına tarihli adla taşır."""
    eski = yol.parent / "eski"
    eski.mkdir(exist_ok=True)
    os.replace(yol, eski / f"{yol.stem}.{datetime.now():%Y%m%d-%H%M%S}{yol.suffix}")


def adim(klip, ad, hedef, imz, uret, dogrula):
    """uret(gecici_yol); dogrula(yol) -> (hatalar, uyarılar). Döner: 'atlandı' | 'üretildi'."""
    kayit = klip["adimlar"].get(ad)
    if hedef.exists():
        if kayit and kayit.get("imza") == imz and kayit.get("boyut") == hedef.stat().st_size:
            return "atlandı"
        if not kayit:
            raise AdimHatasi(f"{goreli(hedef)} zaten var ama bu üretim kaydında yok; üzerine yazılmadı "
                             "(başka bir yere taşıyın ya da silin)")
    hedef.parent.mkdir(parents=True, exist_ok=True)
    gecici = gecici_ad(hedef)
    gecici.unlink(missing_ok=True)
    try:
        uret(gecici)
        if not gecici.exists() or gecici.stat().st_size == 0:
            raise AdimHatasi(f"{ad}: çıktı oluşmadı")
        hatalar, uyarilar = dogrula(gecici)
        if hatalar:
            raise AdimHatasi(f"{ad} doğrulanamadı: " + "; ".join(hatalar))
        if hedef.exists():                        # eski çıktı silinmez: eski/ altında tarihli saklanır
            eski_yere(hedef)
        os.replace(gecici, hedef)
    finally:
        gecici.unlink(missing_ok=True)            # başarısız deneme kalıntısı; önceki hedef korunur
    klip["adimlar"][ad] = {"dosya": goreli(hedef), "imza": imz, "boyut": hedef.stat().st_size,
                           "tarih": simdi(), "uyarilar": uyarilar}
    return "üretildi"


# --------------------------------------------------------------------------
# Yerleşim kararı
# --------------------------------------------------------------------------

def yerlesim_karari(kanal, oyun_araligi=None, secim=None, tespit=None):
    """-> {'tur': 'profil'|'guvenli', 'neden', 'onay_gerekli', 'args'}.
    secim='guvenli': kullanıcı bu klip için tam görüntüyü seçti (ör. klipte farklı sahne var).
    Profil bozuksa ProfilHatasi (sessizce güvenliye düşülmez, düzeltilmeli)."""
    if secim == "guvenli":
        return {"tur": "guvenli", "onay_gerekli": False, "args": ["--guvenli"],
                "neden": "kullanıcı bu klip için güvenli tam görüntüyü seçti"}
    if not kanal or not profil.profil_yolu(kanal).is_file():
        if tespit is None:
            return {"tur": "guvenli", "onay_gerekli": True, "args": ["--guvenli"],
                    "neden": f"'{kanal}' kanalı için düzen profili yok; kırpmasız tam görüntü önerilir"}
        if tespit["karar"] == "kamera":
            f = lambda b: ",".join(str(int(v)) for v in b)
            args = ["--kamera", f(tespit["kamera_kirp"]), "--pencere", f(tespit["oyun"]),
                    "--ust-oran", f"{tespit['ust_oran']:g}"]
            if tespit.get("maske"):
                args += ["--maske", f(tespit["maske"])]
            if tespit.get("oyun_ust") is not None:
                args += ["--oyun-ust", str(tespit["oyun_ust"])]
            return {"tur": "otomatik", "onay_gerekli": False, "args": args, "altyazi_y": tespit["altyazi_y"],
                    "ust_oran": tespit["ust_oran"],
                    "neden": f"otomatik kamera tespiti: {tespit['neden']}; kamera {f(tespit['kamera'])}, "
                             f"oyun {f(tespit['oyun'])}" + (" (çakışan kamera maskelendi)" if tespit.get("maske") else "")}
        return {"tur": "guvenli", "onay_gerekli": False, "args": ["--guvenli"], "tespit_yok": True,
                "neden": f"KAMERA BULUNAMADI ({tespit['karar']}: {tespit['neden']}); güvenli tam görüntü"}
    p = profil.yukle(kanal)
    sahne = p["sahneler"][p["varsayilan_sahne"]]
    if "oyun" in sahne["bolgeler"] and not oyun_araligi:
        return {"tur": "guvenli", "onay_gerekli": False, "args": ["--guvenli"],
                "neden": (f"'{kanal}' profilinde alt bölüm pencere/oyun arasında geçiyor ama bu klip için "
                          "geçiş bilgisi (--oyun) yok; önemli görüntüyü kesmemek için tam görüntü")}
    args = ["--duzen", kanal] + (["--oyun", oyun_araligi] if oyun_araligi else [])
    return {"tur": "profil", "onay_gerekli": False, "args": args,
            "neden": f"'{kanal}' profili" + (f", oyun aralığı {oyun_araligi}" if oyun_araligi else "")}


# --------------------------------------------------------------------------
# aday
# --------------------------------------------------------------------------

def sohbet_indir(vod, hedef):
    if not TDCLI.exists():
        raise SystemExit(f"TwitchDownloaderCLI bulunamadı: {TDCLI}")
    gecici = hedef.with_name("chat.part.json")
    gecici.unlink(missing_ok=True)
    print(f"Sohbet indiriliyor: VOD {vod} -> {goreli(hedef)}")
    try:
        calistir([TDCLI, "chatdownload", "--id", vod, "-o", gecici, "--collision", "Overwrite",
                  "--banner", "false"], "sohbet indirme")
        json.loads(gecici.read_text(encoding="utf-8"))["comments"]
    except (AdimHatasi, OSError, ValueError, KeyError) as e:
        gecici.unlink(missing_ok=True)
        raise SystemExit(f"Sohbet indirilemedi: {e}")
    os.replace(gecici, hedef)


def tepki_araligi(ts, t, dilim=DILIM):
    """Seçilen dilimin çevresinde saniyelik sohbet hızı -> (tepki başı, tepe, tepki sonu), sn.
    Taban: 60-20 sn önceki medyan hız; başlangıç tepe yüksekliğinin %40'ı, bitiş %25'i."""
    import bisect
    import statistics
    a0, a1 = int(t) - 60, int(t + dilim) + 45
    say = [0] * (a1 - a0)
    for x in ts[bisect.bisect_left(ts, a0):bisect.bisect_left(ts, a1)]:
        say[int(x) - a0] += 1
    yum = [sum(say[max(0, i - 1):i + 2]) / len(say[max(0, i - 1):i + 2]) for i in range(len(say))]
    taban = statistics.median(yum[:40])
    pk = max(range(45, min(len(yum), 60 + int(dilim) + 15)), key=lambda i: yum[i])
    tepe = yum[pk]
    i = pk
    while i > 0 and yum[i - 1] >= taban + 0.4 * (tepe - taban):
        i -= 1
    j = pk
    while j + 1 < len(yum) and yum[j + 1] >= taban + 0.25 * (tepe - taban):
        j += 1
    return float(a0 + i), float(a0 + pk), float(a0 + j + 1)


def klip_sinirlari(olay, tepki_son, uzunluk, baglam=None):
    """Olaydan ONCE_OLAY önce başlar (birleşen sahnede `baglam`a kadar, en fazla SAHNE_BAGLAM);
    sohbet tepkisi sönene (gecikme düşülerek) ya da en az SONRA_EN_AZ sonrasına kadar sürer;
    KLIP_MIN-KLIP_MAX arası."""
    bas = olay - ONCE_OLAY
    if baglam is not None:
        bas = min(bas, max(baglam, olay - SAHNE_BAGLAM))
    bas = max(0.0, bas)
    son = max(olay + SONRA_EN_AZ, tepki_son - GECIKME + 3)
    son = max(min(son, bas + KLIP_MAX), bas + KLIP_MIN)
    son = min(son, uzunluk) if uzunluk else son
    return round(bas, 3), round(son - bas, 3)


def dilim_yogunlugu(kalan, n_dilim):
    """anlari_sec ile aynı sayım: dilim başına kişi başı en fazla KISI_TAVAN mesaj."""
    from collections import Counter
    say = [Counter() for _ in range(n_dilim)]
    for m in kalan:
        say[min(int(m["t"] // DILIM), n_dilim - 1)][m["ad"]] += 1
    return [sum(min(v, sk.KISI_TAVAN) for v in c.values()) for c in say]


def doygunluk_orani(yogunluk):
    """En yoğun dilimler (%99'luk) / ortanca dilim. Sohbet tavandaysa mesaj sayısı artamaz, oran düşer.
    Ölçülen: ohnePixel 1.5 (doygun); Se7en 3.7, RRaenee 3.9, SCHRADIN 4.9, Kick 7.3-8.7 (doygun değil)."""
    s = sorted(x for x in yogunluk if x > 0)
    return s[int(0.99 * (len(s) - 1))] / s[len(s) // 2] if s else float("inf")


def doygun_mu(yogunluk):
    return doygunluk_orani(yogunluk) < DOYGUN_ORAN


def doygun_anlar(kalan, n_dilim, adci, pencerede):
    """Doygun sohbette mesaj sayısı yerine tepki mesajı sayısının (lol, ????, W, oooo) sıçraması.
    anlari_sec ile aynı alanları döndürür; eşik: tepki sayısı ortalama + 2 sd."""
    import statistics
    from collections import Counter
    kisi_say = [Counter() for _ in range(n_dilim)]
    sayilan = [[] for _ in range(n_dilim)]
    for m in kalan:
        i = min(int(m["t"] // DILIM), n_dilim - 1)
        kisi_say[i][m["ad"]] += 1
        if kisi_say[i][m["ad"]] <= sk.KISI_TAVAN:
            sayilan[i].append(m["metin"])
    tepki = [sum(map(adci.tepki_mi, s)) for s in sayilan]
    ort, sd = statistics.fmean(tepki), statistics.pstdev(tepki)
    esik = ort + 2 * sd
    k = max(1, round(sk.IVME_PENCERE / DILIM))
    en_t, en_k = max(tepki) or 1, max(len(c) for c in kisi_say) or 1
    adaylar = []
    for i in range(n_dilim):
        if tepki[i] <= esik or not sayilan[i]:
            continue
        onceki = tepki[max(0, i - k):i]
        ivme = (tepki[i] + ort) / ((statistics.fmean(onceki) if onceki else 0.0) + ort)
        puan = 100 * (0.5 * tepki[i] / en_t + 0.2 * len(kisi_say[i]) / en_k + 0.3 * min(ivme / sk.IVME_TAVAN, 1.0))
        adaylar.append({"i": i, "t": i * DILIM, "ham": sum(kisi_say[i].values()), "kisi": len(kisi_say[i]),
                        "agir": len(sayilan[i]), "ivme": ivme, "tepki": tepki[i] / len(sayilan[i]),
                        "soru": sum(map(adci.soru_mu, sayilan[i])) / len(sayilan[i]), "soru_cevap": False,
                        "puan": puan, "metinler": sayilan[i]})
    icerde = [a for a in adaylar if pencerede(a["t"])]
    icerde.sort(key=lambda a: (-a["puan"], a["i"]))
    secilen = []
    for a in icerde:
        if all(abs(a["t"] - s["t"]) >= sk.ARALIK for s in secilen):
            secilen.append(a)
    return {"ort": ort, "sd": sd, "esik": esik, "aday": len(adaylar), "disarida": len(adaylar) - len(icerde)}, secilen


def heyecan_mi(metin, emotelar=frozenset()):
    """Sıkı heyecan ölçütü: gülme, şok, ????/!!!!, uzatılmış bağırış (NOOOO), W/L, tepki kelimeleri,
    kanal emote'ları. Çağrı cevapları ("me", "jbl", "ps5", "yes", "gta6") heyecan değildir."""
    t = metin.strip()
    if not t:
        return False
    if sk.GULME.search(t) or t.count("?") >= 2 or (t.count("!") >= 2 and len(t) < 30):
        return True
    if any(w in emotelar for w in t.split()):
        return True
    for w in re.findall(r"[^\W\d_]+", sk.tr_lower(t)):
        if w in sk.TEPKI_KELIME or w in HEYECAN_EK or (len(w) >= 4 and sk.UZATMA.search(w)):
            return True
    return False


def heyecan_suzgeci(adaylar, emotelar):
    """Çağrı cevabı dalgalarını eler, kalanların puanını heyecan payıyla ayarlar -> (adaylar, elenen sayısı).
    Clix: 'jbl'x114, 'me'x72, 'ps5'x118 dilimleri sohbetin en yoğunlarıydı ama heyecan değildi."""
    from collections import Counter
    kalan, elenen = [], 0
    for s in adaylar:
        if s.get("yontem") == "ses":                  # sesten bulunan an: sohbet içeriğine göre elenmez
            kalan.append(s)
            continue
        sayim = Counter(sk.tr_lower(x).strip() for x in s["metinler"] if x.strip())
        if not sayim:
            continue
        ust, adet = sayim.most_common(1)[0]
        if adet / sum(sayim.values()) >= CAGRI_PAY and not heyecan_mi(ust, emotelar):
            elenen += 1
            continue
        h = sum(heyecan_mi(x, emotelar) for x in s["metinler"]) / len(s["metinler"])
        kalan.append({**s, "puan": s["puan"] * (0.6 + 0.8 * h), "heyecan": h})
    return kalan, elenen


def sahnelere_ayir(secilen):
    """Birbirine SAHNE sn'den yakın adayları zincirleyip gruplar; gruplar en iyi puana göre sıralı."""
    gruplar = []
    for s in sorted(secilen, key=lambda x: x["t"]):
        if gruplar and s["t"] - gruplar[-1][-1]["t"] <= SAHNE:
            gruplar[-1].append(s)
        else:
            gruplar.append([s])
    return sorted(gruplar, key=lambda g: -max(x["puan"] for x in g))


def sure_coz(s):
    """'11:00:00', '90:00', '3600' -> saniye."""
    if s is None:
        return None
    parca = [float(x) for x in str(s).strip().split(":")]
    if not 1 <= len(parca) <= 3:
        raise SystemExit(f"Süre anlaşılmadı: {s!r} (örn. 11:00:00)")
    return sum(v * 60 ** i for i, v in enumerate(reversed(parca)))


def ses_zarfi_tam(yol, kare=SES_KARE):
    """Uzun ses dosyası -> kare sn'lik dilimlerin dB seviyesi (akışla okunur, bellek şişmez)."""
    import numpy as np
    sr = 8000
    n = int(sr * kare)
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-i", str(yol), "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "s16le", "-"], stdout=subprocess.PIPE)
    db, kalan = [], b""
    while True:
        parca = p.stdout.read(2 * n * 2000)
        if not parca:
            break
        veri = kalan + parca
        m = len(veri) // (2 * n)
        x = np.frombuffer(veri[:m * 2 * n], np.int16).astype(np.float32).reshape(m, n) / 32768
        db.append(10 * np.log10((x * x).mean(1) + 1e-10))
        kalan = veri[m * 2 * n:]
    p.wait()
    if p.returncode != 0 or not db:
        raise AdimHatasi("yayın sesi okunamadı")
    return np.concatenate(db).astype(np.float32)


def kick_ses_indir(k, ib, ie, hedef):
    """Kick'te yalnız-ses varyantı yok: en düşük bant genişlikli akışın yalnız sesi alınır."""
    master = k["kaynak_url"]
    metin = http_metin(master)
    bw = [(int(b), s) for b, s in re.findall(r"BANDWIDTH=(\d+)[^\n]*\n([^\n#]+)", metin)]
    if not bw:
        raise AdimHatasi("Kick HLS varyantı bulunamadı")
    taban = master.rsplit("/", 1)[0] + "/"
    url = min(bw)[1].strip()
    url = url if url.startswith("http") else taban + url
    bas = sk.tarih(k["baslangic"])
    sec, ofs = segment_sec(hls_segmentleri(http_metin(url), url),
                           bas + timedelta(seconds=ib), bas + timedelta(seconds=ie))
    liste = hedef.with_name(hedef.stem + ".m3u8")
    liste.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:%d\n#EXT-X-MEDIA-SEQUENCE:0\n"
                     % math.ceil(max(d for _, d, _ in sec)) +
                     "".join(f"#EXTINF:{d:.3f},\n{u}\n" for _, d, u in sec) + "#EXT-X-ENDLIST\n", encoding="utf-8")
    try:
        calistir(["ffmpeg", "-v", "error", "-y", "-nostdin", "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
                  "-i", liste, "-ss", f"{ofs:.3f}", "-t", f"{ie - ib:.3f}", "-vn", "-c:a", "copy", hedef],
                 "Kick ses indirme")
    finally:
        liste.unlink(missing_ok=True)


def ses_hazirla(vod, platform, veri, a0, a1):
    """Zayıf sohbet için yayının [a0, a1] sesinin seviye özeti (önbellekli). Büyük ses dosyası özeti
    çıkarılınca silinir; yalnız yayinlar/<id>/ses_zarfi.npz kalır."""
    import numpy as np
    ozet = klasor(vod) / "ses_zarfi.npz"
    if ozet.exists():
        z = np.load(ozet)
        if float(z["a0"]) <= a0 and float(z["a1"]) >= a1 - 1 and float(z["kare"]) == SES_KARE:
            bas = int((a0 - float(z["a0"])) / SES_KARE)
            return z["db"][bas:bas + int((a1 - a0) / SES_KARE)]
    gecici = klasor(vod) / "ses.part.m4a"
    gecici.unlink(missing_ok=True)
    print(f"Sohbet zayıf: yayının sesi indiriliyor ({sk.sure_yaz(a0)}-{sk.sure_yaz(a1)}, yalnız ses)...")
    try:
        if platform == "Kick":
            kick_ses_indir({"kaynak_url": veri.get("source"), "baslangic": veri.get("start_time")}, a0, a1, gecici)
        else:
            calistir([TDCLI, "videodownload", "--id", vod, "-q", "Audio", "-b", f"{int(a0)}s", "-e", f"{int(a1)}s",
                      "-o", gecici, "--collision", "Overwrite", "--banner", "false"], "ses indirme")
        db = ses_zarfi_tam(gecici)
    finally:
        gecici.unlink(missing_ok=True)
    np.savez_compressed(ozet, db=db, kare=SES_KARE, a0=a0, a1=a0 + len(db) * SES_KARE)
    return db


def ses_anlar(db, a0, kalan, adci, pencere):
    """Yayıncının o sıradaki olağan seviyesine (çevresindeki SES_TABAN_SN'nin ortancası) göre belirgin ses
    yükselmesi: bağırma, şaşkınlık, kahkaha, ani oyun olayı - hiçbiri ayrıca öne çıkarılmaz. Sohbette o an
    tepki varsa küçük bir ek puan. anlari_sec ile aynı alanlar + olay_ses/tepe_ses/son_ses."""
    import bisect
    import numpy as np
    k = int(DILIM / SES_KARE)
    nd = len(db) // k
    if nd < 3:
        return []
    dil_med = np.median(db[:nd * k].reshape(nd, k), axis=1)
    w = int(SES_TABAN_SN / DILIM / 2)
    taban = np.array([np.median(dil_med[max(0, i - w):i + w + 1]) for i in range(nd)])
    ts = [m["t"] for m in kalan]
    tepkili = sorted(m["t"] for m in kalan if adci.tepki_mi(m["metin"]))
    adaylar = []
    for i in range(nd):
        b = taban[i]
        if b < SES_SESSIZ_DB:
            continue                                   # yayın sessiz/duraklatılmış
        fr = db[i * k:(i + 1) * k]
        yuksek = fr > b + SES_ESIK_DB
        asim = float(np.percentile(fr, 95) - b)
        sure = float(yuksek.sum() * SES_KARE)
        t = a0 + i * DILIM
        if asim < SES_ESIK_DB or sure < SES_EN_AZ_SN or not pencere(t):
            continue
        j = i * k + int(np.argmax(fr))                 # en yüksek an
        ilk = j
        while ilk > i * k and db[ilk - 1] > b + SES_ESIK_DB - 3:
            ilk -= 1
        son = j
        while son + 1 < len(db) and son - j < 30 / SES_KARE and db[son + 1] > b + SES_ESIK_DB - 3:
            son += 1
        # sohbet: olaydan sonraki ~30 sn (yayın gecikmesi + yazma)
        i0, i1 = bisect.bisect_left(ts, t), bisect.bisect_left(ts, t + DILIM + 30)
        msj = [kalan[x] for x in range(i0, i1)]
        tepki = bisect.bisect_left(tepkili, t + DILIM + 30) - bisect.bisect_left(tepkili, t)
        puan = 100 * (0.6 * min(asim / 20, 1) + 0.25 * min(sure / 6, 1) + 0.15 * min(tepki / 5, 1))
        adaylar.append({"i": i, "t": t, "ham": len(msj), "kisi": len({m["ad"] for m in msj}), "agir": len(msj),
                        "ivme": asim, "tepki": tepki / max(len(msj), 1), "soru": 0.0, "soru_cevap": False,
                        "puan": puan, "metinler": [m["metin"] for m in msj], "yontem": "ses",
                        "olay_ses": a0 + ilk * SES_KARE, "tepe_ses": a0 + j * SES_KARE,
                        "son_ses": a0 + son * SES_KARE, "ses_sure": sure})
    adaylar.sort(key=lambda a: -a["puan"])
    secilen = []
    for a in adaylar:
        if all(abs(a["t"] - s["t"]) >= sk.ARALIK for s in secilen):
            secilen.append(a)
    return secilen


def analiz(sohbet, adet, aralik=None, ses_getir=None):
    """aralik: (bas, son) sn - yalnız yayının bu bölümü (ör. GTA kısmı; sonrası IRL) analiz edilir;
    eşik, yoğunluk ve doygunluk da yalnız bu bölümden hesaplanır.
    ses_getir(platform, veri, a0, a1) -> dB dizisi: sohbet zayıfsa (dakikada ZAYIF_DK_MESAJ'dan az) adaylar
    yayıncının sesinden bulunur; verilmezse ya da ses alınamazsa klip uydurulmaz, durulur."""
    platform, mesajlar, emotelar, yayin_suresi, veri = sk.sohbet_oku(str(sohbet))
    if platform not in ("Twitch", "Kick"):
        raise SystemExit(f"Şimdilik yalnız Twitch ve Kick VOD'u destekleniyor (bulunan: {platform}).")
    if platform == "Twitch":
        emotelar |= sk.twitch_kanal_emotelari(veri, Path(sohbet).resolve().parent)
    kalan, elenen = sk.ele(mesajlar)
    if not kalan:
        raise SystemExit("Sayılacak mesaj kalmadı.")
    if platform == "Kick":
        uzunluk = (veri.get("duration") or 0) / 1000 or max(m["t"] for m in kalan)
    else:
        uzunluk = float((veri.get("video") or {}).get("length") or 0) or max(m["t"] for m in kalan)
    son = max(yayin_suresi or 0, uzunluk, max(m["t"] for m in kalan))
    a0, a1 = (aralik or (None, None))
    a0, a1 = a0 or 0.0, min(a1 or son, son)
    if a0 or a1 < son:
        kalan = [m for m in kalan if a0 <= m["t"] < a1]
        if not kalan:
            raise SystemExit(f"{sk.sure_yaz(a0)}-{sk.sure_yaz(a1)} aralığında sayılacak mesaj yok.")
        son = a1
    n_dilim = int(son // DILIM) + 1
    dil = sk.dil_tahmin(m["metin"] for m in kalan)
    adci = sk.Adlandirici(emotelar, dil)
    # yayının ilk/son KENAR sn'si: katılım ve veda dalgası (an değil); aralık yayının ortasında bitiyorsa
    # o uçta kenar payı yok, yalnız klibin sığacağı kadar
    alt_sinir = a0 if a0 else KENAR
    ust_sinir = a1 - KLIP_MIN if a1 < uzunluk else uzunluk - KENAR
    pencere = lambda t: alt_sinir <= t <= ust_sinir              # noqa: E731
    ozet, ham_secilen = sk.anlari_sec(kalan, n_dilim, DILIM, adci, max(4 * adet, 40), pencerede=pencere)
    # mutlak eşik: sakin bölümdeki kısa hızlanma, yayının gerçekten yoğun anlarıyla yarışmasın
    yogunluk = dilim_yogunlugu(kalan, n_dilim)
    taban = sorted(yogunluk)[min(len(yogunluk) - 1, int(YOGUN_YUZDE * len(yogunluk)))]
    oran = doygunluk_orani(yogunluk)
    ts_hacim = sorted(m["t"] for m in kalan)
    ts_tepki = sorted(m["t"] for m in kalan if adci.tepki_mi(m["metin"]))
    hacim = [{**s, "yontem": "hacim"} for s in ham_secilen if s["agir"] >= taban]
    if oran < DOYGUN_ORAN:
        # sohbet tavanda: hacim artamıyor, tepki mesajlarının sıçramasına bakılır; zamanlama da onlarla
        yontem = "tepki (doygun sohbet)"
        ozet, tepki = doygun_anlar(kalan, n_dilim, adci, pencere)
        yogun = [{**s, "yontem": "tepki"} for s in tepki]
        ham_secilen = tepki
    elif oran < KARMA_ORAN:
        # ara bölge: iki yöntemin adayları birlikte yarışır (keskin eşik yerine kademeli geçiş)
        yontem = "karma (yarı doygun sohbet)"
        _, tepki = doygun_anlar(kalan, n_dilim, adci, pencere)
        yogun = hacim + [{**s, "yontem": "tepki"} for s in tepki]
    else:
        yontem = "hacim"
        yogun = hacim
    ozet["yogun_disi"] = len(ham_secilen) - len(hacim) if yontem == "hacim" else 0
    dk_mesaj = len(kalan) / max((a1 - a0) / 60, 1)
    if dk_mesaj < ZAYIF_DK_MESAJ:
        # 4-5 kişinin aynı anda yazması "patlama" sayılmasın: an yayıncının sesinden bulunur;
        # sohbette gerçekten çok kişinin tepki verdiği anlar da yarışmaya devam eder
        print(f"Sohbet zayıf: dakikada {dk_mesaj:.1f} mesaj (eşik {ZAYIF_DK_MESAJ}); anlar sesten aranıyor")
        if ses_getir is None:
            raise SystemExit("Sohbet anları ayırt etmeye yetmiyor ve ses analizi kullanılamıyor; "
                             "uydurma klip üretilmedi.")
        try:
            db = ses_getir(platform, veri, a0, a1)
        except AdimHatasi as e:
            raise SystemExit(f"Sohbet zayıf ve yayın sesi alınamadı ({e}); uydurma klip üretilmedi.")
        ses = ses_anlar(db, a0, kalan, adci, pencere)
        guclu = [s for s in yogun if s["kisi"] >= ZAYIF_KISI_MIN]
        yontem = f"ses (zayıf sohbet: dakikada {dk_mesaj:.1f} mesaj)" + (f" + {len(guclu)} sohbet anı" if guclu else "")
        yogun = ses + guclu
        if not yogun:
            raise SystemExit("Sohbet zayıf ve seste belirgin bir yükselme yok; güçlü an bulunamadı, "
                             "uydurma klip üretilmedi.")
    yogun, ozet["cagri"] = heyecan_suzgeci(yogun, emotelar)
    print(f"Seçim yöntemi: {yontem} (doygunluk oranı {oran:.2f}); çağrı cevabı olarak elenen: {ozet['cagri']}")
    sahneler = sahnelere_ayir(yogun)
    ozet["birlesen"] = sum(len(g) - 1 for g in sahneler)
    adaylar = []
    for sira, grup in enumerate(sahneler[:adet + YEDEK], 1):
        s = max(grup, key=lambda x: x["puan"])
        ad = adci.ad(s["metinler"]) or f"AN {sira}"
        zaman = lambda x: ts_tepki if x["yontem"] == "tepki" else ts_hacim   # noqa: E731 - kendi yöntemiyle

        def aralik_(x):
            """(olay, tepe, tepki sonu): ses adayında olay doğrudan seste (sohbet gecikmesi yok)."""
            if x["yontem"] == "ses":
                return x["olay_ses"], x["tepe_ses"], x["son_ses"] + GECIKME
            a, b, c = tepki_araligi(zaman(x), x["t"])
            return max(0.0, a - GECIKME), b, c

        olay, tp, tson = aralik_(s)
        tb = olay + (0 if s["yontem"] == "ses" else GECIKME)
        baglam = None
        if len(grup) > 1:                         # aynı sahne: tepkisi en uzun süreni ve en erken başlangıç
            uyeler = [aralik_(x) for x in grup]
            tson = max(u[2] for u in uyeler)
            baglam = min(u[0] for u in uyeler) - ONCE_OLAY
        bas, sure = klip_sinirlari(olay, tson, uzunluk, baglam)
        gorulen, ornek = set(), []
        for m in s["metinler"]:
            if m.strip().lower() not in gorulen and len(ornek) < 3:
                gorulen.add(m.strip().lower())
                ornek.append(m.strip()[:60])
        ucuncu = sum(bool(UCUNCU_TARAF.search(m)) for m in s["metinler"])
        if s["yontem"] == "ses":
            aciklama = (f"ses {s['ivme']:.0f} dB yükseldi, {s['ses_sure']:.1f} sn; sohbet {s['ham']} mesaj "
                        f"({s['kisi']} kişi)")
        else:
            aciklama = (f"sohbet {s['ivme']:.1f}x hızlandı, {s['kisi']} kişi, tepki %{100 * s['tepki']:.0f}; "
                        f"tepki {sk.sure_yaz(tb)}-{sk.sure_yaz(tson)}")
        adaylar.append({"sira": sira, "kok": f"{saat(s['t'])}-{sk.ascii_ad(ad, dil)[:30]}",
                        "t": s["t"], "olay": olay, "bas": bas, "sure": sure, "ad": ad,
                        "puan": round(s["puan"], 1), "aciklama": aciklama,
                        "tepki_araligi": [tb, tp, tson], "ucuncu_taraf_ipucu": ucuncu,
                        "baglam": baglam, "birlesen": [sk.sure_yaz(x["t"]) for x in grup if x is not s],
                        "yedek": sira > adet, "yontem": s["yontem"],
                        "mesaj": s["ham"], "kisi": s["kisi"], "ivme": round(s["ivme"], 2),
                        "tepki": round(s["tepki"], 3), "soru": round(s["soru"], 3),
                        "soru_cevap": s["soru_cevap"], "ornekler": ornek})
    zamanlar = sorted(m["t"] for m in mesajlar)
    bosluk = max(((b - a, a) for a, b in zip(zamanlar, zamanlar[1:])), default=(0, 0))
    analiz_ozet = {"mesaj": len(mesajlar), "sayilan": len(kalan), "esik": round(ozet["esik"], 2),
                   "aday_dilim": ozet["aday"], "elenen": dict(elenen),
                   "kenar_disi": ozet["disarida"], "yogun_disi": ozet["yogun_disi"],
                   "yogun_esik": taban, "birlesen": ozet["birlesen"], "yontem": yontem,
                   "cagri_cevabi": ozet["cagri"],
                   "kapsam": {"ilk_sn": round(zamanlar[0], 1), "son_sn": round(zamanlar[-1], 1),
                              "en_uzun_bosluk_sn": round(bosluk[0], 1), "bosluk_bas_sn": round(bosluk[1], 1)}}
    if platform == "Kick":
        meta = {"platform": "kick", "kanal": (veri.get("channel_slug") or "").lower(),
                "yayinci": veri.get("channel_slug") or "", "baslik": veri.get("session_title") or "",
                "uzunluk": uzunluk, "dil": dil, "platform_dil": veri.get("language"),
                "kaynak_url": veri.get("source"), "baslangic": veri.get("start_time"), "analiz": analiz_ozet}
    else:
        st = veri.get("streamer") or {}
        meta = {"kanal": (st.get("login") or "").lower(), "yayinci": st.get("name") or "",
                "baslik": (veri.get("video") or {}).get("title") or "", "uzunluk": uzunluk, "dil": dil,
                "baslangic": (veri.get("video") or {}).get("created_at"),
                "analiz": analiz_ozet}
    return meta, adaylar


def aday_tablosu(k):
    print(f"\n{k['yayinci']} · VOD {k['vod']} · {sk.sure_yaz(k['uzunluk'])} · dil {k['dil']}")
    print(f"Başlık: {k['baslik']}")
    if REACTION.search(k["baslik"]):
        print("  ! Başlıkta reaction/tepki geçiyor: üçüncü taraf içerik olabilir, telif incelemesi şart.")
    an = k["analiz"]
    print(f"Sohbet: {an['mesaj']} mesaj, {an['sayilan']} sayıldı; eşik {an['esik']} -> {an['aday_dilim']} "
          f"aday dilim, en iyi {len(k['adaylar'])} an (birbirinden >= {sk.ARALIK} sn)")
    kp = an.get("kapsam")
    if kp:
        print(f"Kapsam: ilk mesaj {sk.sure_yaz(kp['ilk_sn'])}, son {sk.sure_yaz(kp['son_sn'])} / "
              f"yayın {sk.sure_yaz(k['uzunluk'])}; en uzun sessizlik {kp['en_uzun_bosluk_sn']:g} sn "
              f"({sk.sure_yaz(kp['bosluk_bas_sn'])})")
    if k.get("platform_dil"):
        print(f"Platform dili: {k['platform_dil']} · sohbetten tahmin: {k['dil']}")
    secili = set(k.get("secim", []))
    if an.get("kenar_disi"):
        print(f"Yayının ilk/son {KENAR // 60} dk'sındaki {an['kenar_disi']} dilim (katılım/veda) aday sayılmadı.")
    print(f"\n {'':1} {'#':>2}  {'Olay':8}  {'Klip aralığı':17}  {'Puan':>5} {'Msj':>4} {'Kişi':>4} "
          f"{'İvme':>5} {'Tepki':>5} {'Soru':>4}  Ad")
    for a in k["adaylar"]:
        isaret = "*" if a["kok"] in secili else " "
        print(f" {isaret} {a['sira']:2d}  {sk.sure_yaz(a.get('olay', a['t']))}  {sk.sure_yaz(a['bas'])}-"
              f"{sk.sure_yaz(a['bas'] + a['sure'])}  {a['puan']:5.1f} {a['mesaj']:4d} {a['kisi']:4d} "
              f"{a['ivme']:4.1f}x {a['tepki'] * 100:4.0f}% {a['soru'] * 100:3.0f}%  {a['ad']}"
              f"{'  [soru-cevap?]' if a['soru_cevap'] else ''}"
              f"{'  [üçüncü taraf?]' if a.get('ucuncu_taraf_ipucu', 0) >= 3 else ''}")
        if a.get("aciklama"):
            print(f"        {a['aciklama']}")
        print(f"        sohbet: " + " · ".join(f'"{o}"' for o in a["ornekler"]))
    if len(k["adaylar"]) < 5:
        print(f"\nEşiği aşan yalnız {len(k['adaylar'])} an var; sayıyı tamamlamak için zayıf an eklenmedi.")


def komut_aday(a):
    platform, vod, kanal = vod_coz(a.vod)
    sohbet = Path(a.sohbet) if a.sohbet else klasor(vod) / "chat.json"
    if not sohbet.exists():
        if a.sohbet:
            raise SystemExit(f"Sohbet dosyası yok: {sohbet}")
        sohbet.parent.mkdir(parents=True, exist_ok=True)
        if platform == "kick":
            if not kanal:
                raise SystemExit("Kick için tam bağlantı verin: https://kick.com/<kanal>/videos/<uuid>")
            sk.kick_indir(vod, kanal, sohbet)       # eksik sohbet kaydedilmez, hata verir
        else:
            sohbet_indir(vod, sohbet)
    else:
        print(f"Sohbet kaydı mevcut, yeniden indirilmedi: {goreli(sohbet)}")
    aralik = (sure_coz(getattr(a, "bas", None)), sure_coz(getattr(a, "son", None)))
    if any(aralik):
        print(f"Analiz aralığı: {sk.sure_yaz(aralik[0] or 0)} - {sk.sure_yaz(aralik[1]) if aralik[1] else 'son'}")
    meta, adaylar = analiz(sohbet, a.adet, aralik if any(aralik) else None,
                           ses_getir=lambda p, v, a0, a1: ses_hazirla(vod, p, v, a0, a1))
    meta["analiz"]["aralik"] = list(aralik) if any(aralik) else None
    k = kayit_oku(vod, zorunlu=False) or {"surum": 1, "vod": vod, "secim": [], "oyun_araliklari": {},
                                           "klipler": {}}
    k.update(meta, sohbet=goreli(sohbet), adaylar=adaylar, guncelleme=simdi(),
             ayarlar={"sure": SURE, "once": ONCE, "pay": PAY, "adet": a.adet, "dilim": DILIM})
    kokler = {x["kok"] for x in adaylar}
    kayip = [s for s in k["secim"] if s not in kokler]
    if kayip:
        print(f"Uyarı: önceki seçimdeki {', '.join(kayip)} artık aday listesinde yok; seçimden çıkarıldı.")
        k["secim"] = [s for s in k["secim"] if s in kokler]
    kayit_yaz(k)
    aday_tablosu(k)
    print(f"\nKayıt: {goreli(kayit_yolu(vod))}\nSonraki adım (hedef {HEDEF} kaliteli klip, zorlanmaz):"
          f"\n  python uretim.py sec {vod} 1,3,4")


# --------------------------------------------------------------------------
# sec
# --------------------------------------------------------------------------

def komut_sec(a):
    k = kayit_oku(vod_id(a.vod))
    secim = []
    for s in filter(None, (x.strip() for x in a.siralar.split(","))):
        kok = aday_bul(k, s)["kok"]
        if kok not in secim:
            secim.append(kok)
    if not secim:
        raise SystemExit("En az bir aday seçin, örn. 1,3,4")
    oyun = dict(k.get("oyun_araliklari", {}))
    for o in a.oyun or []:
        sira, _, aralik = o.partition(":")
        aday = aday_bul(k, sira)
        try:
            ar = araliklar(aralik)
        except ValueError:
            ar = []
        if not ar or any(not (0 <= b < s <= aday["sure"]) for b, s in ar):
            raise SystemExit(f"--oyun {o}: klibe göre 0-{aday['sure']:g} sn arası 'bas-son' olmalı "
                             "(örn. 5:26.05-40)")
        oyun[aday["kok"]] = aralik
    yerlesim = dict(k.get("yerlesim_secimi", {}))
    for o in a.yerlesim or []:
        sira, _, tur = o.partition(":")
        if tur not in ("guvenli", "profil"):
            raise SystemExit(f"--yerlesim {o}: SIRA:guvenli ya da SIRA:profil olmalı")
        kok = aday_bul(k, sira)["kok"]
        if tur == "guvenli":
            yerlesim[kok] = "guvenli"
        else:
            yerlesim.pop(kok, None)
    k["secim"], k["oyun_araliklari"], k["yerlesim_secimi"] = secim, oyun, yerlesim
    kayit_yaz(k)
    print(f"Seçildi: {len(secim)} aday (hedef {HEDEF}, zorlanmaz)")
    for kok in secim:
        aday = next(x for x in k["adaylar"] if x["kok"] == kok)
        try:
            y = yerlesim_karari(k["kanal"], oyun.get(kok), yerlesim.get(kok))
            ytxt = ("güvenli tam görüntü" if y["tur"] == "guvenli" else "profil") + f" - {y['neden']}"
            if y["onay_gerekli"]:
                ytxt = ("otomatik kamera tespiti üretimde yapılacak; kamera bulunamazsa güvenli tam "
                        "görüntü (bildirilir)")
        except profil.ProfilHatasi as e:
            ytxt = f"PROFİL HATASI: {e}"
        print(f"  {aday['sira']:2d} {kok}: {ytxt}")
    print(f"\nSonraki adım: python uretim.py uret {k['vod']}")


# --------------------------------------------------------------------------
# uret
# --------------------------------------------------------------------------

def parca_araligi(bas, sure, uzunluk):
    """Klip [bas, bas+sure] için indirilecek tam saniyeli aralık ve klibin parçadaki başlangıcı."""
    ib = max(0, math.floor(bas - PAY))
    ie = math.ceil(min(uzunluk, bas + sure + PAY)) if uzunluk else math.ceil(bas + sure + PAY)
    return ib, ie, round(bas - ib, 3)


def klip_kaydi(k, kok):
    return k["klipler"].setdefault(kok, {"adimlar": {}, "inceleme": {
        "telif": "bekliyor", "kalite": "bekliyor", "not": "", "tarih": None}})


def riskler(k, klip, yer, aday=None):
    r = []
    if REACTION.search(k.get("baslik", "")):
        r.append("ÜÇÜNCÜ TARAF: başlıkta reaction/tepki; başkasına ait video/ses olabilir")
    if aday and aday.get("ucuncu_taraf_ipucu", 0) >= 3:
        r.append(f"ÜÇÜNCÜ TARAF?: sohbette {aday['ucuncu_taraf_ipucu']} mesaj video/şarkı/müzikten söz ediyor")
    if yer["tur"] == "guvenli":
        r.append("tam görüntü: sohbet katmanı, overlay ve logolar görünür olabilir")
    elif yer["tur"] == "otomatik":
        r.append("otomatik kadraj: oyun kırpımı kenarları keser; olay kenardaysa önizlemeye bakın")
    else:
        r.append("profil yalnız oyun sahnesini tanır; klipte tam kamera/reaction sahnesi varsa kadraj "
                 "yanlış olur (önizlemeye bakın; gerekirse sec --yerlesim SIRA:guvenli)")
    ses = klip.get("ses") or {}
    if ses.get("karar") == "ayristirildi":
        r.append("müzik ayrıştırıldı: oyun sesleri de kısıldı; şarkı sözleri konuşmayla kalmış olabilir")
    else:
        r.append(f"orijinal ses ({ses.get('neden', 'ayrıştırma yok')}): müzik varsa elle ele alınmalı")
    r.append("müzik kaldırma tek başına telif hakkı ya da YouTube 'yeniden kullanılan içerik' sorununu "
             "çözmez; otomatik telif onayı verilmez")
    return r


def altyazi_incelemesi(dikey):
    """altyazi.py'nin bıraktığı kelime kaydından sesle kontrol edilmesi gereken kelime sayısı."""
    import altyazi
    js = dikey.with_name(dikey.stem + ".kelimeler.json")
    if not js.exists():
        return None
    bak = js.with_suffix(".json.bak")
    veri = json.loads(js.read_text(encoding="utf-8"))
    alt = json.loads(bak.read_text(encoding="utf-8")) if bak.exists() else None
    return len(altyazi.inceleme_satirlari(veri, alt))


# --------------------------------------------------------------------------
# Kick: HLS'ten yalnız gereken segmentler, gerçek saate (PROGRAM-DATE-TIME) göre kesim.
# ffmpeg'in HLS üzerinde -ss araması 1-2 sn kayabiliyor; sohbet zamanları gerçek saat
# olduğundan segment saatleriyle hizalamak hem doğru hem tutarlı.
# --------------------------------------------------------------------------

def http_metin(url, deneme=4):
    istek = urllib.request.Request(url, headers={"User-Agent": sk.UA})
    for i in range(deneme):
        try:
            with urllib.request.urlopen(istek, timeout=30) as r:
                return r.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if i == deneme - 1:
                raise AdimHatasi(f"HLS listesi alınamadı ({url[:80]}...): {e}") from None
            time.sleep(2 ** i)


def hls_varyant_sec(metin, master_url, kalite):
    """master.m3u8 -> (varyant URL'si, ad). İstenen kalite yoksa en yüksek bant genişliği."""
    taban, varyant, son = master_url.rsplit("/", 1)[0] + "/", [], None
    for s in metin.splitlines():
        if s.startswith("#EXT-X-STREAM-INF:"):
            son = s
        elif s and not s.startswith("#") and son:
            ad = (re.search(r'VIDEO="([^"]+)"', son) or re.search(r"RESOLUTION=(\S+?)(?:,|$)", son))
            bw = re.search(r"BANDWIDTH=(\d+)", son)
            varyant.append((ad.group(1) if ad else s, int(bw.group(1)) if bw else 0,
                            s if s.startswith("http") else taban + s))
            son = None
    if not varyant:
        raise AdimHatasi("HLS ana listesinde varyant yok")
    for ad, _, url in varyant:
        if ad == kalite:
            return url, ad
    ad, _, url = max(varyant, key=lambda v: v[1])
    return url, ad


def hls_segmentleri(metin, url):
    """Medya listesi -> [(başlangıç datetime, süre sn, segment URL)]; her segmentte gerçek saat şart."""
    taban, segs, pdt, sure = url.rsplit("/", 1)[0] + "/", [], None, None
    for s in metin.splitlines():
        if s.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pdt = sk.tarih(s.split(":", 1)[1])
        elif s.startswith("#EXTINF:"):
            sure = float(s[8:].split(",")[0])
        elif s and not s.startswith("#"):
            if pdt is None or sure is None:
                raise AdimHatasi("HLS segmentinde PROGRAM-DATE-TIME/EXTINF yok; zaman hizalanamaz")
            segs.append((pdt, sure, s if s.startswith("http") else taban + s))
            pdt, sure = pdt + timedelta(seconds=sure), None
    return segs


def segment_sec(segs, w0, w1):
    """[w0, w1] gerçek saat aralığını kapsayan segmentler ve ilk segmente göre kesim noktası."""
    sec = [x for x in segs if x[0] + timedelta(seconds=x[1]) > w0 and x[0] < w1]
    if not sec or sec[0][0] > w0 + timedelta(seconds=0.5) or \
            sec[-1][0] + timedelta(seconds=sec[-1][1]) < w1 - timedelta(seconds=0.5):
        raise AdimHatasi("istenen aralık HLS listesinde yok ya da eksik")
    for (a, da, _), (b, _, _) in zip(sec, sec[1:]):
        if abs((b - (a + timedelta(seconds=da))).total_seconds()) > 0.5:
            raise AdimHatasi(f"HLS segmentleri arasında boşluk/çakışma var ({a.isoformat()})")
    return sec, (w0 - sec[0][0]).total_seconds()


def kick_parca_indir(k, ib, ie, hedef, kalite):
    master = k.get("kaynak_url")
    if not master or not k.get("baslangic"):
        raise AdimHatasi("Kick kaynak adresi/başlangıç zamanı kayıtta yok; 'aday' adımını yeniden çalıştırın")
    url, ad = hls_varyant_sec(http_metin(master), master, kalite)
    if ad != kalite:
        print(f"    uyarı: {kalite} yok, {ad} kullanılıyor")
    bas = sk.tarih(k["baslangic"])
    sec, ofs = segment_sec(hls_segmentleri(http_metin(url), url),
                           bas + timedelta(seconds=ib), bas + timedelta(seconds=ie))
    liste = hedef.with_name(hedef.stem + ".m3u8")
    liste.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:%d\n#EXT-X-MEDIA-SEQUENCE:0\n"
                     % math.ceil(max(d for _, d, _ in sec)) +
                     "".join(f"#EXTINF:{d:.3f},\n{u}\n" for _, d, u in sec) + "#EXT-X-ENDLIST\n",
                     encoding="utf-8")
    print(f"    Kick HLS {ad}: {len(sec)} segment, ilk segmentten {ofs:.3f} sn sonra kesim")
    try:
        calistir(["ffmpeg", "-v", "error", "-y", "-nostdin", "-protocol_whitelist",
                  "file,http,https,tcp,tls,crypto", "-i", liste, "-ss", f"{ofs:.3f}", "-t", f"{ie - ib:.3f}",
                  "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
                  "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", hedef], "Kick video indirme")
    finally:
        liste.unlink(missing_ok=True)


def onizleme_uret(video, hedef, sure, kare=5):
    """Kalite incelemesi için yan yana 5 kare (kadraj/sahne hatası tek bakışta görülsün)."""
    gecici = hedef.with_name(f"{hedef.stem}.part{hedef.suffix}")
    r = _yakala(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(video), "-vf",
                 f"fps={kare}/{max(sure, 1):g},scale=270:-2,tile={kare}x1", "-frames:v", "1",
                 "-q:v", "4", str(gecici)])
    if r.returncode != 0 or not gecici.exists():
        gecici.unlink(missing_ok=True)
        raise AdimHatasi(r.stderr.strip()[-200:] or "ffmpeg")
    os.replace(gecici, hedef)


def ses_zarfi(video, kare=0.25):
    """Mono ses gücü, dB, kare sn'lik dilimler."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(video), "-map", "0:a:0", "-ac", "1",
                        "-ar", "8000", "-f", "f32le", "-"], capture_output=True)
    x = np.frombuffer(r.stdout, np.float32)
    n = int(8000 * kare)
    m = len(x) // n
    if r.returncode != 0 or m == 0:
        raise AdimHatasi("parçanın sesi okunamadı")
    return 10 * np.log10((x[:m * n].reshape(m, n) ** 2).mean(1) + 1e-10)


def olay_hizala(parca, ib, aday):
    """Sohbetten tahmin edilen olay zamanını parçanın sesiyle düzeltir (yayıncının tepki
    patlaması), başı/sonu en yakın sessiz ana yaslar. Parça zamanına göre döner."""
    import numpy as np
    F = 0.25
    db = ses_zarfi(parca, F)
    uz = len(db) * F
    o = aday.get("olay", aday["bas"] + ONCE) - ib
    s = np.convolve(db, np.ones(4) / 4, "same")
    i0, i1 = int(max(0, (o - 10) / F)), int(min(len(s), (o + 5) / F))
    kaynak = "sohbet"
    if i1 > i0:
        pk = i0 + int(np.argmax(s[i0:i1]))
        if s[pk] >= float(np.median(s)) + 6:
            o, kaynak = pk * F, "ses tepesi"
    tson = (aday.get("tepki_araligi") or [0, 0, aday["bas"] + aday["sure"] + GECIKME - 3])[2] - ib
    baglam = aday.get("baglam")
    bas, sure = klip_sinirlari(o, tson, uz, None if baglam is None else baglam - ib)
    son = bas + sure

    def sessiz(a, b):
        ia, ib_ = int(max(0, a / F)), int(min(len(db), b / F))
        return (ia + int(np.argmin(db[ia:ib_]))) * F if ib_ > ia else None

    b2 = sessiz(bas - 2.5, min(bas + 1.0, o - 6))       # olaydan en az 6 sn önce kalsın
    bas = max(0.0, b2 if b2 is not None else bas)
    s2 = sessiz(son - 1.5, son + 2.5)
    son = min(s2 if s2 is not None else son, uz, bas + KLIP_MAX)
    if son - bas < KLIP_MIN:
        son = min(uz, bas + KLIP_MIN)
    return {"ofs": round(bas, 2), "sure": round(son - bas, 2), "olay_klipte": round(o - bas, 2),
            "olay_vod": round(ib + o, 2), "kaynak": kaynak}


def mevcut_parca(k, ib, ie, imz_f):
    """Önceki üretimlerden [ib, ie]'yi kapsayan, kayıtlı ve yerinde duran parça -> (yol, kayıt, pib, pie)."""
    for klip in k.get("klipler", {}).values():
        kayit = (klip.get("adimlar") or {}).get("indir")
        if not kayit:
            continue
        m = re.search(r"parca_(\d\d)(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)\.mp4$", kayit["dosya"])
        yol = PROJE / kayit["dosya"]
        if not m or not yol.exists() or yol.stat().st_size != kayit.get("boyut"):
            continue
        g = [int(x) for x in m.groups()]
        pib, pie = g[0] * 3600 + g[1] * 60 + g[2], g[3] * 3600 + g[4] * 60 + g[5]
        if pib <= ib and pie >= ie and kayit.get("imza") == imz_f(pib, pie):
            return yol, kayit, pib, pie
    return None


def otomatik_kontrol(zaman, yer, ses, n_kelime, n_incele, uyarilar):
    """Her klip için kadraj / altyazı / ses / zamanlama kontrolü -> (sonuç sözlüğü, iyi mi)."""
    k = {"kadraj": {"otomatik": "kamera otomatik bulundu", "profil": "doğrulanmış profil",
                    "guvenli": "KAMERA YOK: güvenli tam görüntü"}[yer["tur"]],
         "altyazi": f"{n_kelime} kelime, {n_incele or 0} şüpheli" if n_kelime is not None else "yok",
         "ses": (ses or {}).get("neden", "ayrıştırma yapılmadı"),
         "zaman": f"olay klibin {zaman['olay_klipte']:g}. sn'sinde ({zaman['kaynak']}), süre {zaman['sure']:g} sn"}
    sorun = []
    if yer["tur"] == "guvenli" or any(u.startswith("kadraj:") for u in uyarilar):
        sorun.append("kadraj")
    if not n_kelime or n_kelime < 5:
        sorun.append("altyazı")
    if any(u.startswith(("ses tamamen sessiz", "düşük ses")) for u in uyarilar):
        sorun.append("ses")
    if not (4 <= zaman["olay_klipte"] <= zaman["sure"] - 6):
        sorun.append("zaman")
    k["sorun"] = sorun
    return k, not sorun


def bilinen_kameralar(k, haric=None):
    """Aynı yayında kenarlarıyla doğrulanmış kamera ölçüleri: [(kamera, yüz, klip kökü)].
    Ölçü aktarımıyla bulunanlar kaynak sayılmaz (hata zincirlenmesin)."""
    return [(t["kamera"], t["yuz"], kok) for kok, c in k.get("klipler", {}).items()
            if kok != haric and (t := c.get("kamera_tespit") or {}).get("karar") == "kamera"
            and t.get("kaynak") not in ("olcu_aktarimi", "cercevesiz") and t.get("kamera") and t.get("yuz")]


def klip_uret(k, aday, a):
    kok = aday["kok"]
    klip = klip_kaydi(k, kok)
    print(f"\n[{aday['sira']:02d}] {kok}  olay ~{sk.sure_yaz(aday.get('olay', aday['t']))}"
          f"  {aday.get('aciklama', '')}")
    kok_dir = klasor(k["vod"]) / "uretim"
    olay = aday.get("olay", aday["bas"] + ONCE)
    ib = max(0, math.floor(olay - INDIR_ONCE))
    ie = math.ceil(min(k["uzunluk"], olay + INDIR_SONRA)) if k["uzunluk"] else math.ceil(olay + INDIR_SONRA)
    if ie - ib < KLIP_MIN:
        klip.update(durum="atlandı", hata=f"olay VOD sonuna çok yakın ({ie - ib} sn)")
        print(f"    {klip['hata']}")
        return
    dil = a.dil or k["dil"]

    # 1. yalnız olayın çevresini indir (önceki indirilmiş parça kapsıyorsa onu kullan)
    if k.get("platform") == "kick":
        imz_f = lambda x, y: imza("kick", k["vod"], x, y, a.kalite)
    else:
        imz_f = lambda x, y: imza(k["vod"], x, y, a.kalite)
    eski = mevcut_parca(k, ib, ie, imz_f)
    if eski:
        parca, kayit, ib, ie = eski
        klip["adimlar"]["indir"] = dict(kayit)
        print(f"    indir   : önceki parça kullanıldı -> {goreli(parca)}")
    else:
        parca = kok_dir / "kaynak" / f"parca_{saat(ib)}-{saat(ie)}.mp4"
        if k.get("platform") == "kick":
            indir = lambda g: kick_parca_indir(k, ib, ie, g, a.kalite)
        else:
            indir = lambda g: calistir([TDCLI, "videodownload", "--id", k["vod"], "-q", a.kalite,
                                        "-b", sk.sure_yaz(ib), "-e", sk.sure_yaz(ie), "-o", g,
                                        "--collision", "Overwrite", "--banner", "false"], "video indirme")
        sonuc = adim(klip, "indir", parca, imz_f(ib, ie), indir,
                     lambda y: dogrula_dosya(y, en_az=ie - ib, tam=False))
        print(f"    indir   : {sonuc} -> {goreli(parca)} ({ie - ib} sn)")
    imz_parca = klip["adimlar"]["indir"]["imza"]

    # 2. başlangıç/bitiş gerçek olaya göre
    z = olay_hizala(parca, ib, aday)
    ofs, sure = z["ofs"], z["sure"]
    klip["zaman"] = {**z, "bas_vod": round(ib + ofs, 2)}
    print(f"    zaman   : {sk.sure_yaz(ib + ofs)} +{sure:g} sn; olay {z['olay_klipte']:g}. sn ({z['kaynak']})")

    # 3. kamera tespiti ve yerleşim
    secim = k.get("yerlesim_secimi", {}).get(kok)
    tespit = None
    if secim != "guvenli" and not (k["kanal"] and profil.profil_yolu(k["kanal"]).is_file()):
        import kamera_tespit
        try:
            tespit = kamera_tespit.bolgeler(parca, ofs, sure, bilinen_kameralar(k, kok))
        except Exception as e:                       # noqa: BLE001 - tespit hatası güvenliye düşer
            tespit = {"karar": "yok", "neden": f"tespit çalışmadı: {e}"}
        klip["kamera_tespit"] = {x: tespit.get(x) for x in ("karar", "neden", "kaynak", "yuz", "kamera", "oyun",
                                                            "maske", "ust_oran", "oyun_ust", "altyazi_y")}
    yer = yerlesim_karari(k["kanal"], k.get("oyun_araliklari", {}).get(kok), secim, tespit)
    klip["yerlesim"] = {"tur": yer["tur"], "neden": yer["neden"]}
    print(f"    yerleşim: {yer['tur']} - {yer['neden']}")

    dikey = kok_dir / "klipler" / f"{kok}-dikey.mp4"
    altyazili = kok_dir / "klipler" / f"{kok}-dikey-altyazili.mp4"
    shorts = kok_dir / "klipler" / f"{kok}-shorts.mp4"

    # 4. dikey kompozisyon
    imz_ses = imza(imz_parca, ofs, sure)
    imz_dikey = imza(imz_ses, yer["args"])
    onceki_ses = (klip["adimlar"].get("dikey") or {}).get("ses_imza")
    sonuc = adim(klip, "dikey", dikey, imz_dikey,
                 lambda g: calistir([sys.executable, PROJE / "dikey_kompozisyon.py", parca, "--bas", f"{ofs:g}",
                                     "--sure", f"{sure:g}", *yer["args"], "-o", g], "dikey kompozisyon"),
                 lambda y: dogrula_dosya(y, beklenen=sure, boyut=(1080, 1920)))
    klip["adimlar"]["dikey"]["ses_imza"] = imz_ses
    kel_js = dikey.with_name(dikey.stem + ".kelimeler.json")
    if sonuc == "üretildi" and onceki_ses != imz_ses:
        # ses değişti: eski transkripsiyon geçersiz (silinmez, eski/ altında saklanır)
        if kel_js.exists():
            eski_yere(kel_js)
    print(f"    dikey   : {sonuc} -> {goreli(dikey)}")

    # 5. altyazı (yüzü ve oyunu kapatmayan şeritte)
    son_dosya, son_adim, imz_son = dikey, "dikey", imz_dikey
    if dil in ALTYAZI_DILLERI:
        if yer["tur"] == "guvenli":
            y_args = ["--y", str(GUVENLI_ALTYAZI_Y)]
        elif yer.get("altyazi_y"):
            y_args = ["--y", str(yer["altyazi_y"])]
        else:
            y_args = []
        duz = duzeltme_imzasi(kel_js)                 # elle düzeltme varsa altyazı yeniden yakılır
        imz_son = imza(imz_dikey, dil, y_args, "tani-4", "maske-2", *([duz] if duz else []))
        sonuc = adim(klip, "altyazi", altyazili, imz_son,
                     lambda g: calistir([sys.executable, PROJE / "altyazi.py", dikey, "--dil", dil, *y_args,
                                         "-o", g], "altyazı"),
                     lambda y: dogrula_dosya(y, beklenen=sure, boyut=(1080, 1920)))
        son_dosya, son_adim = altyazili, "altyazi"
        print(f"    altyazı : {sonuc} -> {goreli(altyazili)}")
    else:
        klip["adimlar"].pop("altyazi", None)
        print(f"    altyazı : atlandı (dil '{dil}' desteklenmiyor: tr/de/en)")

    # 6. arka plan müziğini ayrıştır (konuşma bozulursa orijinal ses korunur)
    ses_rapor = shorts.with_name(f"{kok}-shorts.ses.json")
    kaynak_dosya = son_dosya
    try:
        # varsayılan: müzik ayrılmaz (oyun sesini de götürüyordu; müziği kullanıcı kendisi ele alır),
        # ses yalnız -14 LUFS'a eşitlenir. --muzik-ayir ile eski davranış.
        ayir = bool(getattr(a, "muzik_ayir", False))
        ses_args = (["--alternatif", shorts.with_name(f"{kok}-yalniz-konusma.mp4")] if ayir else ["--yalniz-esitle"])
        sonuc = adim(klip, "ses", shorts, imza(imz_son, "ses_ayristir-4", ayir),
                     lambda g: calistir([sys.executable, PROJE / "ses_ayristir.py", kaynak_dosya, "-o", g,
                                         *(["--kelimeler", kel_js] if kel_js.exists() else []),
                                         "--rapor", ses_rapor, *ses_args], "ses"),
                     lambda y: dogrula_dosya(y, beklenen=sure, boyut=(1080, 1920)))
        ses = json.loads(ses_rapor.read_text(encoding="utf-8")) if ses_rapor.exists() else None
        son_dosya, son_adim = shorts, "ses"
        print(f"    ses     : {sonuc} -> {goreli(shorts)}  [{(ses or {}).get('karar')}: {(ses or {}).get('neden')}]")
    except AdimHatasi as e:
        ses = {"karar": "orijinal", "neden": f"ayrıştırma başarısız ({e}); orijinal ses"}
        print(f"    ses     : HATA {e}; orijinal sesli dosya kullanılıyor")
    klip["ses"] = ses

    uyarilar = list(klip["adimlar"][son_adim].get("uyarilar", []))
    n_kelime = n_incele = None
    if dil not in ALTYAZI_DILLERI:
        uyarilar.append(f"altyazı yok (dil '{dil}')")
    else:
        n_incele = altyazi_incelemesi(dikey)
        if kel_js.exists():
            n_kelime = len(json.loads(kel_js.read_text(encoding="utf-8"))["kelimeler"])
        if n_incele:
            uyarilar.append(f"altyazı: {n_incele} kelime sesle kontrol edilmeli "
                            f"({goreli(dikey.with_name(dikey.stem + '.inceleme.md'))})")
    if yer.get("tespit_yok"):
        uyarilar.append("kamera bulunamadı: güvenli tam görüntü kullanıldı")
    onizleme = son_dosya.with_name(f"{kok}-onizleme.jpg")
    if not onizleme.exists() or onizleme.stat().st_mtime < son_dosya.stat().st_mtime:
        try:
            onizleme_uret(son_dosya, onizleme, sure)
        except AdimHatasi as e:
            uyarilar.append(f"önizleme üretilemedi ({e})")
    if onizleme.exists():
        klip["onizleme"] = goreli(onizleme)
        print(f"    önizleme: {goreli(onizleme)}")
    if yer.get("ust_oran"):                          # son kontrol: kamera bölümünde gerçekten yüz var mı
        import kamera_tespit
        try:
            oran = kamera_tespit.ust_yuz_orani(son_dosya, yer["ust_oran"])
        except Exception as e:                       # noqa: BLE001 - kontrol çalışmazsa sorun sayılmaz, yazılır
            oran = None
            uyarilar.append(f"yüz kontrolü çalışmadı ({e})")
        klip["ust_yuz_orani"] = oran
        if oran is not None and oran < UST_YUZ_MIN:
            # yanlış kırpım çıktıya girmesin: bu klip tam görüntüyle yeniden üretilir (kalıcı seçim)
            print(f"    kadraj  : kamera bölümünde yüz karelerin yalnız %{100 * oran:.0f}'inde; "
                  "tam görüntüyle yeniden üretiliyor")
            k.setdefault("yerlesim_secimi", {})[kok] = "guvenli"
            klip["kadraj_geri_donus"] = (f"otomatik kırpımda kamera bölümünde yüz yalnız %{100 * oran:.0f}; "
                                         "tam görüntüye dönüldü")
            return klip_uret(k, aday, a)
    if yer["tur"] == "guvenli" and klip.get("kadraj_geri_donus"):
        uyarilar.append(f"kadraj: {klip['kadraj_geri_donus']}")
    kontrol, iyi = otomatik_kontrol(klip["zaman"], yer, ses, n_kelime, n_incele, uyarilar)
    klip["kontrol"] = kontrol
    klip["kalite_tahmini"] = "iyi" if iyi else "incele: " + ", ".join(kontrol["sorun"])
    yenilendi = klip.get("dosya") != goreli(son_dosya) or any(
        klip["adimlar"].get(ad, {}).get("tarih", "") > (klip.get("uretim") or "")
        for ad in ("dikey", "altyazi", "ses"))
    i = klip["inceleme"]
    if yenilendi and i.get("tarih"):              # eski render'ın incelemesi yenisine geçmez
        i["not"] = (f"[önceki inceleme {i['tarih']}: telif {i['telif']}, kalite {i['kalite']}; "
                    f"klip yeniden üretildi] {i.get('not', '')}").strip()
        i.update(telif="bekliyor", kalite="bekliyor", tarih=None)
        print("    inceleme: klip yeniden üretildi, önceki inceleme sıfırlandı (not olarak saklandı)")
    klip.update(durum="üretildi", hata=None, dosya=goreli(son_dosya), uyarilar=uyarilar,
                riskler=riskler(k, klip, yer, aday), uretim=simdi())
    # kamera yok + neredeyse konuşma yok: yayıncının anı değil (tanıtım videosu, ara ekran, reklam)
    # VAD yedeği gerektiyse olağan tanıma konuşma bulamamıştır: tanıtım videosunun içindeki eski yayın
    # kesitleri VAD'la tanınıyor (Eray 00:05: 4 -> 17 kelime), yine de yayıncının anı değil
    zayif = kel_js.exists() and bool(json.loads(kel_js.read_text(encoding="utf-8")).get("yedek_tanima"))
    # sesi tamamen yok: Twitch telifli müzik nedeniyle VOD'un o bölümünü sessize alır; böyle klip kullanılamaz
    sessiz = any(u.startswith("ses tamamen sessiz") for u in uyarilar)
    klip["bos_an"] = bool(sessiz or (yer.get("tespit_yok") and ((n_kelime or 0) < BOS_KELIME or zayif)))
    if sessiz:
        klip["kalite_tahmini"] = "elendi: ses yok (Twitch bu bölümün sesini telif nedeniyle kapatmış olabilir)"
    elif klip["bos_an"]:
        klip["kalite_tahmini"] = (f"elendi: kamera yok ve {'olağan tanımada konuşma bulunamadı' if zayif else f'{n_kelime or 0} kelime konuşma'}"
                                  " (hazır video/ara ekran olabilir)")
    print(f"    kontrol : {klip['kalite_tahmini']}")
    for u in uyarilar:
        print(f"    uyarı   : {u}")


def komut_uret(a):
    k = kayit_oku(vod_id(a.vod))
    if not k.get("secim"):
        raise SystemExit(f"Seçili aday yok: python uretim.py sec {k['vod']} 1,3,4")
    if k.get("platform") != "kick" and not TDCLI.exists():
        raise SystemExit(f"TwitchDownloaderCLI bulunamadı: {TDCLI}")
    if a.dil and a.dil not in ALTYAZI_DILLERI:
        raise SystemExit("--dil tr, de ya da en olmalı")
    try:
        yerlesim_karari(k["kanal"])                  # bozuk profil en başta durdurur
    except profil.ProfilHatasi as e:
        raise SystemExit(f"Profil hatası ({k['kanal']}): {e}\nHiçbir klip üretilmedi; profili düzeltin.")
    altyazi_dosyasi_uygula(k)                        # Shorts/<yayın>/ALTYAZI DÜZELT.txt
    hatali = tamam = 0
    hedef = getattr(a, "hedef", None)            # otomatik: bu kadar sağlam klip olunca yedeklere geçme
    for kok in list(k["secim"]):
        if hedef and tamam >= hedef:
            k["secim"].remove(kok)                   # gerekmeyen yedek
            continue
        aday = next(x for x in k["adaylar"] if x["kok"] == kok)
        try:
            klip_uret(k, aday, a)
            klip = k["klipler"].get(kok) or {}
            tamam += klip.get("durum") == "üretildi" and not klip.get("bos_an")
        except (AdimHatasi, profil.ProfilHatasi) as e:
            klip = klip_kaydi(k, kok)
            klip.update(durum="hata", hata=str(e))
            hatali += 1
            print(f"    HATA: {e}\n    (önceki başarılı dosyalar korundu)")
        finally:
            kayit_yaz(k)                             # her klipten sonra; yarıda kesilse de ilerleme kalır
    # kamerası bulunamayanlar: sonraki kliplerde ölçü öğrenildiyse bir kez daha dene
    tekrar = [kok for kok in k["secim"] if ((k["klipler"].get(kok) or {}).get("kamera_tespit") or {})
              .get("karar") == "yok" and bilinen_kameralar(k, kok)]
    for kok in tekrar:
        print(f"\n{kok}: kamera bulunamamıştı; aynı yayının kamera ölçüleriyle yeniden deneniyor")
        try:
            klip_uret(k, next(x for x in k["adaylar"] if x["kok"] == kok), a)
        except (AdimHatasi, profil.ProfilHatasi) as e:
            print(f"    HATA: {e} (önceki dosya korundu)")
        finally:
            kayit_yaz(k)
    durum_tablosu(k)
    hazir, n = cikti_paketi(k)
    print(f"\nShorts: {goreli(hazir)} ({n} MP4; başlık/açıklama önerileri RAPOR.md'de)")
    if hatali:
        raise SystemExit(f"\n{hatali} klip hatalı. Sorunu giderip aynı komutu tekrar çalıştırın; "
                         "tamamlanan adımlar yeniden yapılmaz.")


# --------------------------------------------------------------------------
# incele / durum
# --------------------------------------------------------------------------

def klip_durumu(klip):
    if not klip or klip.get("durum") != "üretildi":
        return (klip or {}).get("durum") or "üretilmedi"
    i = klip["inceleme"]
    if i["telif"] == "sorunlu" or i["kalite"] == "red":
        return "reddedildi"
    if i["telif"] == "temiz" and i["kalite"] == "onay":
        return "elle onaylandı"
    return "inceleme bekliyor"


def komut_incele(a):
    k = kayit_oku(vod_id(a.vod))
    aday = aday_bul(k, a.klip)
    klip = k["klipler"].get(aday["kok"])
    if not klip or klip.get("durum") != "üretildi":
        raise SystemExit(f"{aday['kok']} henüz üretilmedi (durum: {klip_durumu(klip)}); inceleme kaydedilmedi.")
    if not (a.telif or a.kalite or a.not_ is not None):
        raise SystemExit("--telif, --kalite ya da --not verin.")
    i = klip["inceleme"]
    if a.telif:
        i["telif"] = a.telif
    if a.kalite:
        i["kalite"] = a.kalite
    if a.not_ is not None:
        i["not"] = a.not_
    i["tarih"] = simdi()
    kayit_yaz(k)
    print(f"{aday['kok']}: telif {i['telif']}, kalite {i['kalite']} -> {klip_durumu(klip)}")
    if i["not"]:
        print(f"  not: {i['not']}")
    if i["telif"] == "temiz":
        print(f"  {TELIF_NOTU}")
    for r in klip.get("riskler", []):
        print(f"  risk: {r}")


def durum_tablosu(k):
    print(f"\n{k['yayinci']} · VOD {k['vod']} · seçili {len(k.get('secim', []))} / {len(k['adaylar'])} aday")
    sayac = {}
    for a in k["adaylar"]:
        klip = k["klipler"].get(a["kok"])
        if a["kok"] not in k.get("secim", []) and not klip:
            continue
        d = klip_durumu(klip)
        sayac[d] = sayac.get(d, 0) + 1
        yer = ((klip or {}).get("yerlesim") or {}).get("tur", "-")
        print(f"  {a['sira']:2d} {a['kok']:32} {d:18} yerleşim {'güvenli' if yer == 'guvenli' else yer:7} "
              f"telif {klip['inceleme']['telif'] if klip else '-':8} kalite {klip['inceleme']['kalite'] if klip else '-'}")
        if klip and klip.get("dosya"):
            print(f"       {klip['dosya']}")
        if klip and klip.get("onizleme"):
            print(f"       önizleme: {klip['onizleme']}")
        if klip and klip.get("hata"):
            print(f"       hata: {klip['hata']}")
        for u in (klip or {}).get("uyarilar", []):
            print(f"       uyarı: {u}")
    print("  özet: " + ", ".join(f"{v} {d}" for d, v in sayac.items()) +
          f"  (hedef {HEDEF} kaliteli klip; yalnız elle onaylananlar sayılır)")


def komut_durum(a):
    k = kayit_oku(vod_id(a.vod))
    durum_tablosu(k)


# --------------------------------------------------------------------------
# otomatik: bağlantı -> adaylar -> uygun olanların tamamı -> rapor
# --------------------------------------------------------------------------

def baslik_onerisi(k, aday, klip):
    """Kural tabanlı başlık: olay anında söylenen ilk 2-6 kelime; yoksa sohbetteki ifade."""
    import altyazi
    js = PROJE / klip["adimlar"]["dikey"]["dosya"]
    js = js.with_name(js.stem + ".kelimeler.json")
    o = klip["zaman"]["olay_klipte"]
    soz = []
    if js.exists():
        veri = json.loads(js.read_text(encoding="utf-8"))
        kel = [x["goster"] for x in altyazi.gorunen(veri, True, True)
               if o - 1.0 <= x["bas"] <= o + 8.0 and "*" not in x["goster"]][:BASLIK_EN_FAZLA]
        for i, w in enumerate(kel):                   # cümle sonunda kes ("SIX FIGURES ONLY?" gibi)
            if i >= 2 and w.endswith((".", "!", "?")):
                kel = kel[:i + 1]
                break
        else:
            kel = kel[:6]                             # cümle sonu yoksa kısa tut
        soz = [w.strip(".,;:") for w in kel]
        while len(soz) > 2 and sk.tr_lower(soz[-1]) in BASLIK_SON_ATILIR:
            soz.pop()                                 # "...CAN'T GET A" -> "...CAN'T GET"
    ust = sk.tr_upper if k.get("dil") == "tr" else str.upper
    metin = " ".join(soz) if len(soz) >= 2 else ust(aday["ad"])
    return metin[:70]


def dosya_adi(s, en_fazla=60):
    """Windows'ta geçerli, okunur dosya/klasör adı (emoji ve köşeli parantez yok: PowerShell'de joker)."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f\[\]]', " ", s)
    s = "".join(c for c in s if c.isalnum() or c in " -_.,'!()&+")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:en_fazla].rstrip(" .") or "klip"


def yayin_adi(k):
    """'eray 2026-11-15 GTA5ELVEDA': kanal, yayın günü, komut/etiket ayıklanmış başlık."""
    parca = [w for w in (k.get("baslik") or "").split() if not w.startswith(("!", "#", "@"))]
    kelimeler, tek = [], ""
    for w in parca:                               # "G T A 5" gibi aralıklı harfleri birleştir
        if len(w) == 1:
            tek += w
            continue
        if tek:
            kelimeler.append(tek)
            tek = ""
        kelimeler.append(w)
    if tek:
        kelimeler.append(tek)
    tarih = (k.get("baslangic") or "")[:10]
    return dosya_adi(" ".join(x for x in (k.get("kanal") or k["vod"], tarih, " ".join(kelimeler)) if x), 50)


def cikti_paketi(k):
    """Nihai Shorts'ları Shorts/<yayın>/NN - <başlık>.mp4 olarak toplar (yalnız MP4; sabit bağlantı,
    yer kaplamaz, olmazsa kopya). Başlık/açıklama önerileri k['cikti'] ile RAPOR.md'ye gider."""
    import shutil
    hedef = CIKTI / yayin_adi(k)
    hedef.mkdir(parents=True, exist_ok=True)
    for eski in hedef.glob("[0-9][0-9] - *.mp4"):  # önceki paket; asılları klipler/ altında duruyor
        eski.unlink()
    platform = "Kick" if k.get("platform") == "kick" else "Twitch"
    adres = (f"https://kick.com/{k['kanal']}/videos/{k['vod']}" if platform == "Kick"
             else f"https://www.twitch.tv/videos/{k['vod']}")
    etiketler = " ".join(["#shorts", f"#{re.sub(r'[^0-9A-Za-z_]', '', k['kanal'])}", f"#{platform.lower()}",
                          "#yayın" if k.get("dil") == "tr" else "#stream"])
    dosyalar, kullanilan = [], set()
    for a in k["adaylar"]:
        klip = k["klipler"].get(a["kok"])
        if (not klip or klip.get("durum") != "üretildi" or klip.get("bos_an")
                or a["kok"] not in k.get("secim", [])):
            continue
        baslik = baslik_onerisi(k, a, klip)
        ad = dosya_adi(baslik)
        if ad.lower() in kullanilan:
            ad = dosya_adi(f"{ad} {sk.sure_yaz(klip['zaman']['olay_vod']).replace(':', '.')}")
        kullanilan.add(ad.lower())
        mp4 = hedef / f"{len(dosyalar) + 1:02d} - {ad}.mp4"
        try:
            os.link(PROJE / klip["dosya"], mp4)
        except OSError:
            shutil.copy2(PROJE / klip["dosya"], mp4)
        dosyalar.append({"dosya": mp4.name, "kok": a["kok"], "baslik": f"{baslik} | {k['yayinci']}",
                         "aciklama": f"{k['yayinci']} yayınından ({platform}, "
                                     f"{sk.sure_yaz(klip['zaman']['bas_vod'])}). Yayın: {adres}",
                         "etiketler": etiketler})
    k["cikti"] = {"klasor": goreli(hedef), "dosyalar": dosyalar}
    kayit_yaz(k)
    altyazi_dosyasi_yaz(k, hedef)
    return hedef, len(dosyalar)


ALTYAZI_DOSYASI = "ALTYAZI DÜZELT.txt"
ALTYAZI_YARDIM = """# Altyazı düzeltme. Her satır videoda ekrana gelen bir altyazı satırıdır.
#   - Yanlışsa metni düzeltin (büyük/küçük harf önemli değil).
#   - Ekrana gelmesin istiyorsanız | işaretinden sonrasını boşaltın.
#   - '!' = sesle kontrol edilmeli. Dinleyip doğruysa '!' işaretini silin.
# Sonra aynı komutu tekrar çalıştırın:  python uretim.py otomatik <yayın bağlantısı>
# Yalnız değiştirdiğiniz klipler yeniden üretilir. [no] ve zaman kısmını değiştirmeyin.
"""


def _kelime_js(klip):
    d = PROJE / klip["adimlar"]["dikey"]["dosya"]
    return d.with_name(d.stem + ".kelimeler.json")


def _alternatif(js):
    bak = js.with_suffix(".json.bak")
    return json.loads(bak.read_text(encoding="utf-8")) if bak.exists() else None


def altyazi_dosyasi_yaz(k, hedef):
    """Shorts klasörüne düz metin düzeltme dosyası: her klip için videodaki altyazı satırları."""
    import altyazi
    sat = [ALTYAZI_YARDIM]
    for d in (k.get("cikti") or {}).get("dosyalar", []):
        js = _kelime_js(k["klipler"][d["kok"]])
        if not js.exists():
            continue
        veri = json.loads(js.read_text(encoding="utf-8"))
        sat += [f"# ===== {d['dosya']} =====", f"# klip {d['kok']} imza {altyazi.duz_imza(veri)}"]
        for i, bas, supheli, metin in altyazi.duz_satirlar(veri, _alternatif(js)):
            sat.append(f"[{i}] {int(bas // 60):02d}:{bas % 60:04.1f} {'!' if supheli else ' '} | {metin}")
        sat.append("")
    (hedef / ALTYAZI_DOSYASI).write_text("\n".join(sat), encoding="utf-8")


def altyazi_dosyasi_uygula(k):
    """Kullanıcının düz metin düzeltmelerini kelime kayıtlarına işler; değişen klipleri döndürür.
    Kayıt dosya yazıldıktan sonra değiştiyse (imza tutmuyor) o klip atlanır, uyarılır."""
    import altyazi
    yol = PROJE / ((k.get("cikti") or {}).get("klasor") or "-") / ALTYAZI_DOSYASI
    if not yol.exists():
        return []
    bloklar, kok, imz = {}, None, None
    for satir in yol.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#\s*klip\s+(\S+)\s+imza\s+(\w+)", satir)
        if m:
            kok, imz = m.groups()
            bloklar[kok] = (imz, {})
            continue
        m = altyazi.DUZ_SATIR.match(satir)
        if m and kok:
            bloklar[kok][1][int(m[1])] = (m[2] == "!", m[3])
    degisen = []
    for kok, (imz, duzenlenen) in bloklar.items():
        klip = k["klipler"].get(kok)
        if not klip or "dikey" not in klip.get("adimlar", {}):
            continue
        js = _kelime_js(klip)
        if not js.exists():
            continue
        veri = json.loads(js.read_text(encoding="utf-8"))
        if altyazi.duz_imza(veri) != imz:
            print(f"Uyarı: {kok} altyazı kaydı düzeltme dosyası yazıldıktan sonra değişmiş; "
                  f"bu klibin düzeltmeleri uygulanmadı ({goreli(yol)})")
            continue
        n = altyazi.duz_uygula(veri, duzenlenen, _alternatif(js))
        if n:
            js.write_text(json.dumps(veri, ensure_ascii=False, indent=1), encoding="utf-8")
            degisen.append((kok, n))
    if degisen:
        print("Altyazı düzeltmeleri uygulandı: " + ", ".join(f"{a} ({n} satır)" for a, n in degisen))
    return degisen


def duzeltme_imzasi(js):
    """Elle düzeltme/onay varsa onların özeti (altyazı adımı yeniden yapılsın), yoksa boş."""
    if not js.exists():
        return ""
    kel = json.loads(js.read_text(encoding="utf-8"))["kelimeler"]
    elle = [(x["no"], x.get("duzeltme"), bool(x.get("dogrulandi"))) for x in kel
            if x.get("duzeltme") is not None or x.get("dogrulandi")]
    return imza(elle) if elle else ""


def uretim_raporu(k):
    """Kısa insan okunur rapor: yayinlar/<id>/uretim/RAPOR.md"""
    sat = [f"# Üretim raporu: {k['yayinci']} · {k['vod']}", "", f"Başlık: {k['baslik']}  ",
           f"Tarih: {simdi()}", "",
           f"Aday: {len(k['adaylar'])} (eşiği aşan dilim {k['analiz']['aday_dilim']}; yayının ilk/son "
           f"{KENAR // 60} dk'sı ve selamlaşma/bot/komut/link mesajları sayılmadı: {k['analiz']['elenen']})  ",
           f"Yayının en yoğun %{100 - int(YOGUN_YUZDE * 100)}'luk diliminde olmadığı için elenen: "
           f"{k['analiz'].get('yogun_disi', 0)} · aynı sahnede birleşen: {k['analiz'].get('birlesen', 0)}", ""]
    cikti = k.get("cikti") or {}
    ad_dosya = {d["kok"]: d for d in cikti.get("dosyalar", [])}
    if cikti.get("dosyalar"):
        sat += [f"## Yayına hazır: `{cikti['klasor']}`", "",
                "| Dosya | Başlık önerisi | Açıklama önerisi |", "|---|---|---|"]
        sat += [f"| {d['dosya']} | {d['baslik']} | {d['aciklama']} {d['etiketler']} |" for d in cikti["dosyalar"]]
        sat += [""]
    sat += ["## Adaylar", "",
            "| # | Olay | Klip | Puan | Açıklama | Kadraj | Ses | Altyazı | Otomatik kontrol | Dosya |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    uretilen = iyi = 0
    elle = []
    for a in k["adaylar"]:
        klip = k["klipler"].get(a["kok"])
        if a.get("birlesen"):
            a = {**a, "aciklama": f"{a.get('aciklama', '')}; aynı sahne: {', '.join(a['birlesen'])}"}
        if a["kok"] not in k.get("secim", []):
            neden = ("soru-cevap" if a["soru_cevap"] else "yedek, gerekmedi" if a.get("yedek") else "seçilmedi")
            sat.append(f"| {a['sira']} | {sk.sure_yaz(a.get('olay', a['t']))} | — | {a['puan']} | "
                       f"{a.get('aciklama', '')} | {neden} | | | | |")
            continue
        if not klip or klip.get("durum") != "üretildi":
            sat.append(f"| {a['sira']} | {sk.sure_yaz(a.get('olay', a['t']))} | — | {a['puan']} | "
                       f"{a.get('aciklama', '')} | {(klip or {}).get('durum', '-')}: {(klip or {}).get('hata', '')} | | | | |")
            continue
        z, kt = klip["zaman"], klip.get("kontrol", {})
        dosya = (ad_dosya.get(a["kok"]) or {}).get("dosya") or f"— (ara dosya: `{klip['dosya']}`)"
        sat.append(f"| {a['sira']} | {sk.sure_yaz(z['olay_vod'])} | {sk.sure_yaz(z['bas_vod'])} +{z['sure']:g} sn | "
                   f"{a['puan']} | {a.get('aciklama', '')} | {kt.get('kadraj')} | {(klip.get('ses') or {}).get('karar')} | "
                   f"{kt.get('altyazi')} | {klip.get('kalite_tahmini')} | {dosya} |")
        if klip.get("bos_an"):
            continue
        uretilen += 1
        iyi += klip.get("kalite_tahmini") == "iyi"
        nedenler = [r for r in klip.get("riskler", []) if r.startswith("ÜÇÜNCÜ")]
        if klip.get("kalite_tahmini") != "iyi":
            nedenler.append(klip.get("kalite_tahmini"))
        elle.append((a, nedenler))
    sat += ["", f"Üretilen: {uretilen} · otomatik kontrolden geçen: {iyi}", "",
            "## Elle inceleme", "",
            "Tüm klipler telif açısından elle incelenmeli: müzik kaldırma tek başına telif hakkı ya da YouTube "
            "'yeniden kullanılan içerik' sorununu çözmez; bu sistem otomatik telif onayı vermez.", ""]
    for a, n in elle:
        sat.append(f"- {(ad_dosya.get(a['kok']) or {}).get('dosya', a['kok'])}: " +("; ".join(n) if n else "telif incelemesi (standart)"))
    yol = kayit_yolu(k["vod"]).with_name("RAPOR.md")
    yol.write_text("\n".join(sat) + "\n", encoding="utf-8")
    if (k.get("cikti") or {}).get("klasor"):      # videoların yanında da: bakılacak tek klasör
        (PROJE / k["cikti"]["klasor"] / "RAPOR.md").write_text("\n".join(sat) + "\n", encoding="utf-8")
    return yol, uretilen, iyi


DISK_EN_AZ_GB = 5                # 10 klip: parçalar + ara dosyalar ~2-3 GB


def on_kontrol(platform=None):
    """Uzun çalıştırmadan önce eksikleri bulur -> [(durum 'tamam'|'uyarı'|'HATA', ad, açıklama)].
    Model yüklemez, ağa çıkmaz; birkaç saniye sürer."""
    import importlib.util
    import shutil
    s = []
    for arac in ("ffmpeg", "ffprobe"):
        s.append(("tamam", arac, shutil.which(arac)) if shutil.which(arac)
                 else ("HATA", arac, "bulunamadı; FFmpeg kurulu ve PATH'te olmalı"))
    for modul, paket in (("faster_whisper", "faster-whisper"), ("onnxruntime", "onnxruntime"),
                         ("cv2", "opencv-python-headless"), ("numpy", "numpy")):
        s.append(("tamam", paket, "kurulu") if importlib.util.find_spec(modul)
                 else ("HATA", paket, f"kurulu değil: pip install {paket}"))
    for ad, yol in (("yüz modeli", PROJE / "modeller" / "face_detection_yunet_2023mar.onnx"),
                    ("ses modeli", PROJE / "modeller" / "Kim_Vocal_2.onnx")):
        s.append(("tamam", ad, goreli(yol)) if yol.is_file()
                 else ("HATA", ad, f"{goreli(yol)} yok (indirme adresi kamera_tespit.py / ses_ayristir.py başında)"))
    font = Path("C:/Windows/Fonts/seguibl.ttf")
    s.append(("tamam", "yazı tipi", "Segoe UI Black") if font.is_file() or sys.platform != "win32"
             else ("uyarı", "yazı tipi", "Segoe UI Black yok; altyazı başka yazı tipiyle çıkar"))
    if platform != "kick":
        s.append(("tamam", "TwitchDownloaderCLI", goreli(TDCLI)) if TDCLI.exists()
                 else ("HATA" if platform == "twitch" else "uyarı", "TwitchDownloaderCLI",
                       f"{goreli(TDCLI)} yok (yalnız Twitch için gerekli)"))
    bos = shutil.disk_usage(PROJE).free / 1e9
    s.append(("tamam" if bos >= DISK_EN_AZ_GB else "HATA", "disk", f"{bos:.0f} GB boş"
              + ("" if bos >= DISK_EN_AZ_GB else f"; en az {DISK_EN_AZ_GB} GB gerekli")))
    try:
        import ctranslate2
        gpu = ctranslate2.get_cuda_device_count()
    except Exception:                                # noqa: BLE001
        gpu = 0
    cublas = True
    if gpu and sys.platform == "win32":               # ekran kartı görünse de Whisper bu DLL'siz işlemciye düşer
        import ctypes
        import altyazi
        altyazi.cuda_dll_yolu()                       # altyazı da bu yolla bulur (nvidia-* pip paketleri)
        try:
            ctypes.WinDLL("cublas64_12.dll")
        except OSError:
            cublas = False
    if gpu and cublas:
        s.append(("tamam", "GPU", "CUDA hazır; altyazı ekran kartında"))
    else:
        s.append(("uyarı", "GPU", ("ekran kartı var ama cublas64_12.dll yok" if gpu else "CUDA yok")
                  + ": altyazı işlemcide (klip başına ~20 sn, sorun değil)"))
    return s


def on_kontrol_yaz(s):
    for durum, ad, aciklama in s:
        print(f"  {durum:6s} {ad:20s} {aciklama}")
    return not any(d == "HATA" for d, _, _ in s)


def komut_kontrol(a):
    print("Ön kontrol:")
    if not on_kontrol_yaz(on_kontrol()):
        raise SystemExit("\nEksikler var; giderilmeden üretim yarıda kalır.")
    print("\nHazır.")


def komut_otomatik(a):
    """Bağlantıdan uçtan uca: sohbet -> en iyi adaylar -> uygun olanların tamamı üretilir."""
    sonuc = on_kontrol(vod_coz(a.vod)[0])
    if not all(d != "HATA" for d, _, _ in sonuc):
        print("Ön kontrol:")
        on_kontrol_yaz(sonuc)
        raise SystemExit("\nEksikler var; sohbet indirilmeden durduruldu (python uretim.py kontrol).")
    komut_aday(a)
    k = kayit_oku(vod_id(a.vod))
    uygun = [x for x in k["adaylar"] if not x["soru_cevap"] and x["sure"] >= KLIP_MIN]
    k["secim"] = [x["kok"] for x in uygun]
    kayit_yaz(k)
    a.hedef = a.adet
    atlanan = [x for x in k["adaylar"] if x not in uygun]
    print(f"\nOtomatik seçim: en fazla {a.adet} klip; {len(uygun)} uygun aday (boş çıkan klip yerine "
          f"sıradaki yedek üretilir)" +
          (f"; {len(atlanan)} atlandı (soru-cevap / çok kısa): " + ", ".join(x["kok"] for x in atlanan)
           if atlanan else ""))
    try:
        komut_uret(a)
    finally:
        k = kayit_oku(vod_id(a.vod))
        yol, n, iyi = uretim_raporu(k)
        print(f"\nRapor: {goreli(yol)}  ({n} video üretildi, {iyi} otomatik kontrolden geçti)")


def main():
    for akis in (sys.stdout, sys.stderr):
        try:
            akis.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Tek VOD'dan Shorts üretim akışı.")
    alt = ap.add_subparsers(dest="komut", required=True)
    p = alt.add_parser("aday", help="sohbeti indir/analiz et, adayları listele")
    p.add_argument("vod", help="Twitch VOD bağlantısı ya da ID")
    p.add_argument("--adet", type=int, default=ADET, help=f"en fazla aday (10-15, varsayılan {ADET})")
    p.add_argument("--bas", help="yalnız bu andan sonrası, örn. 00:30:00")
    p.add_argument("--son", help="yalnız bu ana kadarı, örn. 11:00:00")
    p.add_argument("--sohbet", help="mevcut chat.json (verilmezse yayinlar/<id>/chat.json, yoksa indirilir)")
    p = alt.add_parser("sec", help="üretilecek adayları seç")
    p.add_argument("vod")
    p.add_argument("siralar", help="aday sıra numaraları, örn. 1,3,4")
    p.add_argument("--oyun", action="append",
                   help="SIRA:ARALIK - profilde pencere/oyun geçişi varsa alt bölümün oyuna geçtiği "
                        "aralık (klibe göre sn), örn. 5:26.05-40; tekrarlanabilir")
    p.add_argument("--yerlesim", action="append",
                   help="SIRA:guvenli (bu klipte kırpmasız tam görüntü) ya da SIRA:profil (geri al); "
                        "tekrarlanabilir")
    p = alt.add_parser("uret", help="seçilenleri indir, kompoze et, altyazıla, doğrula")
    p.add_argument("vod")
    p.add_argument("--guvenli-onay", action="store_true",
                   help="profili olmayan kanal için güvenli tam görüntü yerleşimini onayla")
    p.add_argument("--kalite", default="1080p60", help="TwitchDownloaderCLI kalitesi (1080p60)")
    p.add_argument("--dil", help="altyazı dili (varsayılan: sohbetten tahmin)")
    p.add_argument("--muzik-ayir", action="store_true", help="arka plan müziğini ayırmayı dene (oyun sesi de gider)")
    p = alt.add_parser("otomatik", help="bağlantıdan uçtan uca: en iyi anları bul, hepsini üret, rapor yaz")
    p.add_argument("vod", help="Twitch ya da Kick VOD bağlantısı")
    p.add_argument("--adet", type=int, default=OTOMATIK_ADET, help=f"en fazla aday ({OTOMATIK_ADET})")
    p.add_argument("--bas", help="yalnız bu andan sonrası, örn. 00:30:00")
    p.add_argument("--son", help="yalnız bu ana kadarı, örn. 11:00:00 (sonrası IRL vb.)")
    p.add_argument("--sohbet", help="mevcut chat.json")
    p.add_argument("--kalite", default="1080p60")
    p.add_argument("--dil", help="altyazı dili (tr/de/en; varsayılan: sohbetten tahmin)")
    p.add_argument("--muzik-ayir", action="store_true", help="arka plan müziğini ayırmayı dene (oyun sesi de gider)")
    p.add_argument("--guvenli-onay", action="store_true", default=True, help=argparse.SUPPRESS)
    p = alt.add_parser("incele", help="elle telif/kalite inceleme sonucunu kaydet")
    p.add_argument("vod")
    p.add_argument("klip", help="aday sıra numarası ya da klip kökü")
    p.add_argument("--telif", choices=TELIF, help="temiz = elle incelendi (risk sıfır demek değildir)")
    p.add_argument("--kalite", choices=KALITE)
    p.add_argument("--not", dest="not_", help="inceleme notu")
    p = alt.add_parser("durum", help="adaylar, üretim ve inceleme durumu")
    p.add_argument("vod")
    alt.add_parser("kontrol", help="uzun çalıştırmadan önce ffmpeg, modeller, disk vb. eksik mi")
    a = ap.parse_args()
    if a.komut == "aday" and not 1 <= a.adet <= 30:
        ap.error("--adet 1-30 arası olmalı (önerilen 10-15)")
    if a.komut == "otomatik" and not 1 <= a.adet <= 30:
        ap.error("--adet 1-30 arası olmalı")
    {"aday": komut_aday, "sec": komut_sec, "uret": komut_uret, "incele": komut_incele,
     "durum": komut_durum, "otomatik": komut_otomatik, "kontrol": komut_kontrol}[a.komut](a)


if __name__ == "__main__":
    main()
