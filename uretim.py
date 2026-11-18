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
TDCLI = PROJE / "TwitchDownloaderCLI" / "TwitchDownloaderCLI.exe"

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
        if hedef.exists():                        # eski çıktı silinmez: tarihli .eski adıyla saklanır
            os.replace(hedef, hedef.with_name(f"{hedef.stem}.{datetime.now():%Y%m%d-%H%M%S}.eski{hedef.suffix}"))
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
            return {"tur": "otomatik", "onay_gerekli": False, "args": args, "altyazi_y": tespit["altyazi_y"],
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


def klip_sinirlari(olay, tepki_son, uzunluk):
    """Olaydan ONCE_OLAY önce başlar; sohbet tepkisi sönene (gecikme düşülerek) ya da en az
    SONRA_EN_AZ sonrasına kadar sürer; KLIP_MIN-KLIP_MAX arası."""
    bas = max(0.0, olay - ONCE_OLAY)
    son = max(olay + SONRA_EN_AZ, tepki_son - GECIKME + 3)
    son = max(min(son, bas + KLIP_MAX), bas + KLIP_MIN)
    son = min(son, uzunluk) if uzunluk else son
    return round(bas, 3), round(son - bas, 3)


def analiz(sohbet, adet):
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
    n_dilim = int(son // DILIM) + 1
    dil = sk.dil_tahmin(m["metin"] for m in kalan)
    adci = sk.Adlandirici(emotelar, dil)
    # yayının ilk/son KENAR sn'si: katılım ve veda dalgası (an değil)
    ozet, secilen = sk.anlari_sec(kalan, n_dilim, DILIM, adci, adet,
                                  pencerede=lambda t: KENAR <= t <= uzunluk - KENAR)
    ts = sorted(m["t"] for m in kalan)
    adaylar = []
    for sira, s in enumerate(secilen, 1):
        ad = adci.ad(s["metinler"]) or f"AN {sira}"
        tb, tp, tson = tepki_araligi(ts, s["t"])
        olay = max(0.0, tb - GECIKME)
        bas, sure = klip_sinirlari(olay, tson, uzunluk)
        gorulen, ornek = set(), []
        for m in s["metinler"]:
            if m.strip().lower() not in gorulen and len(ornek) < 3:
                gorulen.add(m.strip().lower())
                ornek.append(m.strip()[:60])
        ucuncu = sum(bool(UCUNCU_TARAF.search(m)) for m in s["metinler"])
        aciklama = (f"sohbet {s['ivme']:.1f}x hızlandı, {s['kisi']} kişi, tepki %{100 * s['tepki']:.0f}; "
                    f"tepki {sk.sure_yaz(tb)}-{sk.sure_yaz(tson)}")
        adaylar.append({"sira": sira, "kok": f"{saat(s['t'])}-{sk.ascii_ad(ad, dil)[:30]}",
                        "t": s["t"], "olay": olay, "bas": bas, "sure": sure, "ad": ad,
                        "puan": round(s["puan"], 1), "aciklama": aciklama,
                        "tepki_araligi": [tb, tp, tson], "ucuncu_taraf_ipucu": ucuncu,
                        "mesaj": s["ham"], "kisi": s["kisi"], "ivme": round(s["ivme"], 2),
                        "tepki": round(s["tepki"], 3), "soru": round(s["soru"], 3),
                        "soru_cevap": s["soru_cevap"], "ornekler": ornek})
    zamanlar = sorted(m["t"] for m in mesajlar)
    bosluk = max(((b - a, a) for a, b in zip(zamanlar, zamanlar[1:])), default=(0, 0))
    analiz_ozet = {"mesaj": len(mesajlar), "sayilan": len(kalan), "esik": round(ozet["esik"], 2),
                   "aday_dilim": ozet["aday"], "elenen": dict(elenen),
                   "kenar_disi": ozet["disarida"],
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
    meta, adaylar = analiz(sohbet, a.adet)
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
        r.append(f"müzik KALDIRILMADI ({ses.get('neden', 'ayrıştırma yok')}): elle telif incelemesi şart")
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
    bas, sure = klip_sinirlari(o, tson, uz)
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
    if yer["tur"] == "guvenli":
        sorun.append("kadraj")
    if not n_kelime or n_kelime < 5:
        sorun.append("altyazı")
    if any(u.startswith(("ses tamamen sessiz", "düşük ses")) for u in uyarilar):
        sorun.append("ses")
    if not (4 <= zaman["olay_klipte"] <= zaman["sure"] - 6):
        sorun.append("zaman")
    k["sorun"] = sorun
    return k, not sorun


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
            tespit = kamera_tespit.bolgeler(parca, ofs, sure)
        except Exception as e:                       # noqa: BLE001 - tespit hatası güvenliye düşer
            tespit = {"karar": "yok", "neden": f"tespit çalışmadı: {e}"}
        klip["kamera_tespit"] = {x: tespit.get(x) for x in ("karar", "neden", "yuz", "kamera", "oyun", "maske",
                                                            "ust_oran", "altyazi_y")}
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
        # ses değişti: eski transkripsiyon geçersiz (silinmez, tarihli saklanır)
        damga = f"{datetime.now():%Y%m%d-%H%M%S}"
        for eski_js in (kel_js, kel_js.with_suffix(".json.bak")):
            if eski_js.exists():
                eski_js.replace(eski_js.with_name(f"{eski_js.name}.{damga}.eski"))
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
        imz_son = imza(imz_dikey, dil, y_args)
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
        alternatif = shorts.with_name(f"{kok}-yalniz-konusma.mp4")
        sonuc = adim(klip, "ses", shorts, imza(imz_son, "ses_ayristir-2"),
                     lambda g: calistir([sys.executable, PROJE / "ses_ayristir.py", kaynak_dosya, "-o", g,
                                         *(["--kelimeler", kel_js] if kel_js.exists() else []),
                                         "--rapor", ses_rapor, "--alternatif", alternatif], "ses ayrıştırma"),
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
    hatali = 0
    for kok in k["secim"]:
        aday = next(x for x in k["adaylar"] if x["kok"] == kok)
        try:
            klip_uret(k, aday, a)
        except (AdimHatasi, profil.ProfilHatasi) as e:
            klip = klip_kaydi(k, kok)
            klip.update(durum="hata", hata=str(e))
            hatali += 1
            print(f"    HATA: {e}\n    (önceki başarılı dosyalar korundu)")
        finally:
            kayit_yaz(k)                             # her klipten sonra; yarıda kesilse de ilerleme kalır
    durum_tablosu(k)
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

def uretim_raporu(k):
    """Kısa insan okunur rapor: yayinlar/<id>/uretim/RAPOR.md"""
    sat = [f"# Üretim raporu: {k['yayinci']} · {k['vod']}", "", f"Başlık: {k['baslik']}  ",
           f"Tarih: {simdi()}", "",
           f"Aday: {len(k['adaylar'])} (eşiği aşan dilim {k['analiz']['aday_dilim']}; yayının ilk/son "
           f"{KENAR // 60} dk'sı ve selamlaşma/bot/komut/link mesajları sayılmadı: {k['analiz']['elenen']})", "",
           "| # | Olay | Klip | Puan | Açıklama | Kadraj | Ses | Altyazı | Otomatik kontrol | Dosya |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    uretilen = iyi = 0
    elle = []
    for a in k["adaylar"]:
        klip = k["klipler"].get(a["kok"])
        if a["kok"] not in k.get("secim", []):
            sat.append(f"| {a['sira']} | {sk.sure_yaz(a.get('olay', a['t']))} | — | {a['puan']} | "
                       f"{a.get('aciklama', '')} | seçilmedi{' (soru-cevap)' if a['soru_cevap'] else ''} | | | | |")
            continue
        if not klip or klip.get("durum") != "üretildi":
            sat.append(f"| {a['sira']} | {sk.sure_yaz(a.get('olay', a['t']))} | — | {a['puan']} | "
                       f"{a.get('aciklama', '')} | {(klip or {}).get('durum', '-')}: {(klip or {}).get('hata', '')} | | | | |")
            continue
        uretilen += 1
        iyi += klip.get("kalite_tahmini") == "iyi"
        z, kt = klip["zaman"], klip.get("kontrol", {})
        sat.append(f"| {a['sira']} | {sk.sure_yaz(z['olay_vod'])} | {sk.sure_yaz(z['bas_vod'])} +{z['sure']:g} sn | "
                   f"{a['puan']} | {a.get('aciklama', '')} | {kt.get('kadraj')} | {(klip.get('ses') or {}).get('karar')} | "
                   f"{kt.get('altyazi')} | {klip.get('kalite_tahmini')} | `{klip['dosya']}` |")
        nedenler = [r for r in klip.get("riskler", []) if r.startswith(("ÜÇÜNCÜ", "müzik KALDIRILMADI"))]
        if klip.get("kalite_tahmini") != "iyi":
            nedenler.append(klip.get("kalite_tahmini"))
        elle.append((a, nedenler))
    sat += ["", f"Üretilen: {uretilen} · otomatik kontrolden geçen: {iyi}", "",
            "## Elle inceleme", "",
            "Tüm klipler telif açısından elle incelenmeli: müzik kaldırma tek başına telif hakkı ya da YouTube "
            "'yeniden kullanılan içerik' sorununu çözmez; bu sistem otomatik telif onayı vermez.", ""]
    for a, n in elle:
        sat.append(f"- {a['sira']} {a['kok']}: " + ("; ".join(n) if n else "telif incelemesi (standart)"))
    yol = kayit_yolu(k["vod"]).with_name("RAPOR.md")
    yol.write_text("\n".join(sat) + "\n", encoding="utf-8")
    return yol, uretilen, iyi


def komut_otomatik(a):
    """Bağlantıdan uçtan uca: sohbet -> en iyi adaylar -> uygun olanların tamamı üretilir."""
    komut_aday(a)
    k = kayit_oku(vod_id(a.vod))
    uygun = [x for x in k["adaylar"] if not x["soru_cevap"] and x["sure"] >= KLIP_MIN]
    k["secim"] = [x["kok"] for x in uygun]
    kayit_yaz(k)
    atlanan = [x for x in k["adaylar"] if x not in uygun]
    print(f"\nOtomatik seçim: {len(uygun)} aday üretilecek" +
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
    p = alt.add_parser("otomatik", help="bağlantıdan uçtan uca: en iyi anları bul, hepsini üret, rapor yaz")
    p.add_argument("vod", help="Twitch ya da Kick VOD bağlantısı")
    p.add_argument("--adet", type=int, default=OTOMATIK_ADET, help=f"en fazla aday ({OTOMATIK_ADET})")
    p.add_argument("--sohbet", help="mevcut chat.json")
    p.add_argument("--kalite", default="1080p60")
    p.add_argument("--dil", help="altyazı dili (tr/de/en; varsayılan: sohbetten tahmin)")
    p.add_argument("--guvenli-onay", action="store_true", default=True, help=argparse.SUPPRESS)
    p = alt.add_parser("incele", help="elle telif/kalite inceleme sonucunu kaydet")
    p.add_argument("vod")
    p.add_argument("klip", help="aday sıra numarası ya da klip kökü")
    p.add_argument("--telif", choices=TELIF, help="temiz = elle incelendi (risk sıfır demek değildir)")
    p.add_argument("--kalite", choices=KALITE)
    p.add_argument("--not", dest="not_", help="inceleme notu")
    p = alt.add_parser("durum", help="adaylar, üretim ve inceleme durumu")
    p.add_argument("vod")
    a = ap.parse_args()
    if a.komut == "aday" and not 1 <= a.adet <= 30:
        ap.error("--adet 1-30 arası olmalı (önerilen 10-15)")
    if a.komut == "otomatik" and not 1 <= a.adet <= 30:
        ap.error("--adet 1-30 arası olmalı")
    {"aday": komut_aday, "sec": komut_sec, "uret": komut_uret, "incele": komut_incele,
     "durum": komut_durum, "otomatik": komut_otomatik}[a.komut](a)


if __name__ == "__main__":
    main()
