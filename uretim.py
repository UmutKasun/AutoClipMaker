#!/usr/bin/env python3
"""uretim.py - Tek Twitch VOD'undan Shorts adayları: sohbet analizi -> seçim -> parça
indirme -> dikey kompozisyon -> altyazı -> doğrulama -> elle inceleme kaydı.

Mevcut araçları çağırır, yerlerine geçmez: sohbet_klip.py (analiz), TwitchDownloaderCLI
(yalnız gereken aralıklar), dikey_kompozisyon.py (--duzen / --guvenli), altyazi.py.

    python uretim.py aday   <VOD linki|ID>        # 10-15 aday, puan/zaman/sohbet
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
from datetime import datetime
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


class AdimHatasi(Exception):
    pass


# --------------------------------------------------------------------------
# Kayıt (uretim.json)
# --------------------------------------------------------------------------

def vod_id(s):
    m = re.fullmatch(r"\s*(?:https?://)?(?:www\.|m\.)?(?:twitch\.tv/videos/)?(\d{5,})(?:[/?#].*)?\s*", s)
    if not m:
        raise SystemExit(f"VOD bağlantısı/ID'si tanınmadı: {s!r} (örn. https://www.twitch.tv/videos/2931374918)")
    return m.group(1)


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
        os.replace(gecici, hedef)
    finally:
        gecici.unlink(missing_ok=True)            # başarısız deneme kalıntısı; önceki hedef korunur
    klip["adimlar"][ad] = {"dosya": goreli(hedef), "imza": imz, "boyut": hedef.stat().st_size,
                           "tarih": simdi(), "uyarilar": uyarilar}
    return "üretildi"


# --------------------------------------------------------------------------
# Yerleşim kararı
# --------------------------------------------------------------------------

def yerlesim_karari(kanal, oyun_araligi=None, secim=None):
    """-> {'tur': 'profil'|'guvenli', 'neden', 'onay_gerekli', 'args'}.
    secim='guvenli': kullanıcı bu klip için tam görüntüyü seçti (ör. klipte farklı sahne var).
    Profil bozuksa ProfilHatasi (sessizce güvenliye düşülmez, düzeltilmeli)."""
    if secim == "guvenli":
        return {"tur": "guvenli", "onay_gerekli": False, "args": ["--guvenli"],
                "neden": "kullanıcı bu klip için güvenli tam görüntüyü seçti"}
    if not kanal or not profil.profil_yolu(kanal).is_file():
        return {"tur": "guvenli", "onay_gerekli": True, "args": ["--guvenli"],
                "neden": f"'{kanal}' kanalı için düzen profili yok; kırpmasız tam görüntü önerilir"}
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


def analiz(sohbet, adet):
    platform, mesajlar, emotelar, yayin_suresi, veri = sk.sohbet_oku(str(sohbet))
    if platform != "Twitch":
        raise SystemExit(f"Şimdilik yalnız Twitch VOD'u destekleniyor (bulunan: {platform}).")
    emotelar |= sk.twitch_kanal_emotelari(veri, Path(sohbet).resolve().parent)
    kalan, elenen = sk.ele(mesajlar)
    if not kalan:
        raise SystemExit("Sayılacak mesaj kalmadı.")
    uzunluk = float((veri.get("video") or {}).get("length") or 0) or max(m["t"] for m in kalan)
    son = max(yayin_suresi or 0, uzunluk, max(m["t"] for m in kalan))
    n_dilim = int(son // DILIM) + 1
    dil = sk.dil_tahmin(m["metin"] for m in kalan)
    adci = sk.Adlandirici(emotelar, dil)
    ozet, secilen = sk.anlari_sec(kalan, n_dilim, DILIM, adci, adet)
    adaylar = []
    for sira, s in enumerate(secilen, 1):
        ad = adci.ad(s["metinler"]) or f"AN {sira}"
        bas = max(0.0, s["t"] - ONCE)
        sure = round(min(SURE, uzunluk - bas), 3)
        gorulen, ornek = set(), []
        for m in s["metinler"]:
            if m.strip().lower() not in gorulen and len(ornek) < 3:
                gorulen.add(m.strip().lower())
                ornek.append(m.strip()[:60])
        adaylar.append({"sira": sira, "kok": f"{saat(s['t'])}-{sk.ascii_ad(ad, dil)[:30]}",
                        "t": s["t"], "bas": bas, "sure": sure, "ad": ad, "puan": round(s["puan"], 1),
                        "mesaj": s["ham"], "kisi": s["kisi"], "ivme": round(s["ivme"], 2),
                        "tepki": round(s["tepki"], 3), "soru": round(s["soru"], 3),
                        "soru_cevap": s["soru_cevap"], "ornekler": ornek})
    st = veri.get("streamer") or {}
    meta = {"kanal": (st.get("login") or "").lower(), "yayinci": st.get("name") or "",
            "baslik": (veri.get("video") or {}).get("title") or "", "uzunluk": uzunluk, "dil": dil,
            "analiz": {"mesaj": len(mesajlar), "sayilan": len(kalan), "esik": round(ozet["esik"], 2),
                       "aday_dilim": ozet["aday"], "elenen": elenen}}
    return meta, adaylar


def aday_tablosu(k):
    print(f"\n{k['yayinci']} · VOD {k['vod']} · {sk.sure_yaz(k['uzunluk'])} · dil {k['dil']}")
    print(f"Başlık: {k['baslik']}")
    if REACTION.search(k["baslik"]):
        print("  ! Başlıkta reaction/tepki geçiyor: üçüncü taraf içerik olabilir, telif incelemesi şart.")
    an = k["analiz"]
    print(f"Sohbet: {an['mesaj']} mesaj, {an['sayilan']} sayıldı; eşik {an['esik']} -> {an['aday_dilim']} "
          f"aday dilim, en iyi {len(k['adaylar'])} an (birbirinden >= {sk.ARALIK} sn)")
    secili = set(k.get("secim", []))
    print(f"\n {'':1} {'#':>2}  {'An':8}  {'Klip aralığı':17}  {'Puan':>5} {'Msj':>4} {'Kişi':>4} "
          f"{'İvme':>5} {'Tepki':>5} {'Soru':>4}  Ad")
    for a in k["adaylar"]:
        isaret = "*" if a["kok"] in secili else " "
        print(f" {isaret} {a['sira']:2d}  {sk.sure_yaz(a['t'])}  {sk.sure_yaz(a['bas'])}-"
              f"{sk.sure_yaz(a['bas'] + a['sure'])}  {a['puan']:5.1f} {a['mesaj']:4d} {a['kisi']:4d} "
              f"{a['ivme']:4.1f}x {a['tepki'] * 100:4.0f}% {a['soru'] * 100:3.0f}%  {a['ad']}"
              f"{'  [soru-cevap?]' if a['soru_cevap'] else ''}")
        print(f"        sohbet: " + " · ".join(f'"{o}"' for o in a["ornekler"]))
    if len(k["adaylar"]) < 5:
        print(f"\nEşiği aşan yalnız {len(k['adaylar'])} an var; sayıyı tamamlamak için zayıf an eklenmedi.")


def komut_aday(a):
    vod = vod_id(a.vod)
    sohbet = Path(a.sohbet) if a.sohbet else klasor(vod) / "chat.json"
    if not sohbet.exists():
        if a.sohbet:
            raise SystemExit(f"Sohbet dosyası yok: {sohbet}")
        sohbet.parent.mkdir(parents=True, exist_ok=True)
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
                ytxt += "  [üretimde --guvenli-onay gerekir]"
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


def riskler(k, klip, yer):
    r = []
    if REACTION.search(k.get("baslik", "")):
        r.append("başlıkta reaction/tepki: üçüncü taraf video/ses olabilir")
    if yer["tur"] == "guvenli":
        r.append("tam görüntü: sohbet katmanı, overlay ve logolar görünür olabilir")
    else:
        r.append("profil yalnız oyun sahnesini tanır; klipte tam kamera/reaction sahnesi varsa kadraj "
                 "yanlış olur (önizlemeye bakın; gerekirse sec --yerlesim SIRA:guvenli)")
    r.append("müzik, yayıncı izni ve üçüncü taraf görüntüler otomatik denetlenmedi")
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


def klip_uret(k, aday, a):
    kok = aday["kok"]
    klip = klip_kaydi(k, kok)
    yer = yerlesim_karari(k["kanal"], k.get("oyun_araliklari", {}).get(kok),
                          k.get("yerlesim_secimi", {}).get(kok))
    klip["yerlesim"] = {"tur": yer["tur"], "neden": yer["neden"]}
    print(f"\n[{aday['sira']:02d}] {kok}  ({sk.sure_yaz(aday['bas'])} +{aday['sure']:g} sn)")
    print(f"    yerleşim: {'güvenli tam görüntü' if yer['tur'] == 'guvenli' else 'profil'} - {yer['neden']}")
    if yer["onay_gerekli"]:
        if not a.guvenli_onay:
            klip.update(durum="onay bekliyor", hata=None)
            print("    güvenli tam görüntü için onay gerekiyor: --guvenli-onay ile yeniden çalıştırın. Üretilmedi.")
            return
        klip["yerlesim"]["onay"] = simdi()
    if aday["sure"] < EN_KISA:
        klip.update(durum="atlandı", hata=f"klip {aday['sure']} sn; VOD sonuna çok yakın")
        print(f"    {klip['hata']}")
        return

    kok_dir = klasor(k["vod"]) / "uretim"
    ib, ie, ofs = parca_araligi(aday["bas"], aday["sure"], k["uzunluk"])
    parca = kok_dir / "kaynak" / f"parca_{saat(ib)}-{saat(ie)}.mp4"
    dikey = kok_dir / "klipler" / f"{kok}-dikey.mp4"
    altyazili = kok_dir / "klipler" / f"{kok}-dikey-altyazili.mp4"
    dil = a.dil or k["dil"]

    # 1. yalnız gereken aralığı indir
    imz_parca = imza(k["vod"], ib, ie, a.kalite)
    sonuc = adim(klip, "indir", parca, imz_parca,
                 lambda g: calistir([TDCLI, "videodownload", "--id", k["vod"], "-q", a.kalite,
                                     "-b", sk.sure_yaz(ib), "-e", sk.sure_yaz(ie), "-o", g,
                                     "--collision", "Overwrite", "--banner", "false"], "video indirme"),
                 lambda y: dogrula_dosya(y, en_az=ie - ib, tam=False))
    print(f"    indir   : {sonuc} -> {goreli(parca)}")

    # 2. dikey kompozisyon
    imz_ses = imza(imz_parca, ofs, aday["sure"])
    imz_dikey = imza(imz_ses, yer["args"])
    onceki_ses = (klip["adimlar"].get("dikey") or {}).get("ses_imza")
    sonuc = adim(klip, "dikey", dikey, imz_dikey,
                 lambda g: calistir([sys.executable, PROJE / "dikey_kompozisyon.py", parca, "--bas", f"{ofs:g}",
                                     "--sure", f"{aday['sure']:g}", *yer["args"], "-o", g], "dikey kompozisyon"),
                 lambda y: dogrula_dosya(y, beklenen=aday["sure"], boyut=(1080, 1920)))
    klip["adimlar"]["dikey"]["ses_imza"] = imz_ses
    if sonuc == "üretildi" and onceki_ses and onceki_ses != imz_ses:
        eski = dikey.with_name(dikey.stem + ".kelimeler.json")
        if eski.exists():                       # ses değişti: eski transkripsiyon geçersiz, silinmez
            eski.replace(eski.with_name(f"{eski.name}.{datetime.now():%Y%m%d-%H%M%S}.eski"))
    print(f"    dikey   : {sonuc} -> {goreli(dikey)}")

    # 3. altyazı
    son_dosya = dikey
    if dil in ALTYAZI_DILLERI:
        y_args = ["--y", str(GUVENLI_ALTYAZI_Y)] if yer["tur"] == "guvenli" else []
        sonuc = adim(klip, "altyazi", altyazili, imza(imz_dikey, dil, y_args),
                     lambda g: calistir([sys.executable, PROJE / "altyazi.py", dikey, "--dil", dil, *y_args,
                                         "-o", g], "altyazı"),
                     lambda y: dogrula_dosya(y, beklenen=aday["sure"], boyut=(1080, 1920)))
        son_dosya = altyazili
        print(f"    altyazı : {sonuc} -> {goreli(altyazili)}")
    else:
        klip["adimlar"].pop("altyazi", None)
        print(f"    altyazı : atlandı (dil '{dil}' desteklenmiyor: tr/de/en)")

    son_adim = "altyazi" if dil in ALTYAZI_DILLERI else "dikey"     # ses aynı: uyarı bir kez
    uyarilar = list(klip["adimlar"][son_adim].get("uyarilar", []))
    if dil not in ALTYAZI_DILLERI:
        uyarilar.append(f"altyazı yok (dil '{dil}')")
    else:
        n = altyazi_incelemesi(dikey)
        if n:
            uyarilar.append(f"altyazı: {n} kelime sesle kontrol edilmeli "
                            f"({goreli(dikey.with_name(dikey.stem + '.inceleme.md'))})")
    onizleme = son_dosya.with_name(f"{kok}-onizleme.jpg")
    if not onizleme.exists() or onizleme.stat().st_mtime < son_dosya.stat().st_mtime:
        try:
            onizleme_uret(son_dosya, onizleme, aday["sure"])
        except AdimHatasi as e:
            uyarilar.append(f"önizleme üretilemedi ({e})")
    if onizleme.exists():
        klip["onizleme"] = goreli(onizleme)
        print(f"    önizleme: {goreli(onizleme)}")
    yenilendi = klip.get("dosya") != goreli(son_dosya) or any(
        klip["adimlar"].get(ad, {}).get("tarih", "") > (klip.get("uretim") or "") for ad in ("dikey", "altyazi"))
    i = klip["inceleme"]
    if yenilendi and i.get("tarih"):              # eski render'ın incelemesi yenisine geçmez
        i["not"] = (f"[önceki inceleme {i['tarih']}: telif {i['telif']}, kalite {i['kalite']}; "
                    f"klip yeniden üretildi] {i.get('not', '')}").strip()
        i.update(telif="bekliyor", kalite="bekliyor", tarih=None)
        print("    inceleme: klip yeniden üretildi, önceki inceleme sıfırlandı (not olarak saklandı)")
    klip.update(durum="üretildi", hata=None, dosya=goreli(son_dosya), uyarilar=uyarilar,
                riskler=riskler(k, klip, yer), uretim=simdi())
    for u in uyarilar:
        print(f"    uyarı   : {u}")


def komut_uret(a):
    k = kayit_oku(vod_id(a.vod))
    if not k.get("secim"):
        raise SystemExit(f"Seçili aday yok: python uretim.py sec {k['vod']} 1,3,4")
    if not TDCLI.exists():
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
    {"aday": komut_aday, "sec": komut_sec, "uret": komut_uret, "incele": komut_incele,
     "durum": komut_durum}[a.komut](a)


if __name__ == "__main__":
    main()
