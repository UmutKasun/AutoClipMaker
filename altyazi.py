#!/usr/bin/env python3
"""altyazi.py - Dikey klibe kelime vurgulu Türkçe / Almanca altyazı.

Akış:
  1. ffmpeg ile ses (16 kHz mono float32)
  2. faster-whisper (large-v3-turbo) ile kelime zamanları
  3. <video>.kelimeler.json  (ham transkripsiyon korunur, "duzeltme" alanı elle düzenlenir)
  4. 2-4 kelimelik gruplar, konuşulan kelime sarı -> <video>.ass
  5. ffmpeg (libass) ile yakılmış yeni dosya -> <video>-altyazili.mp4

JSON varsa yeniden tanıma yapılmaz; elle yapılan düzeltmeler kullanılır.

Örnek:
    python altyazi.py klipler/dikey/032030-GECTI-dikey.mp4 --dil tr
    python altyazi.py klip.mp4 --dil de --maskesiz        # küfür maskeleme kapalı
"""

import argparse
import json
import re
import subprocess
import sys
import time
import types
from datetime import datetime
from pathlib import Path

from sohbet_klip import tr_lower, tr_upper

GEN, YUK = 1080, 1920
YAZI_TIPI = "Segoe UI Black"
FONT_KLASORU = "C:/Windows/Fonts"
BEYAZ, SARI = "&H00FFFFFF", "&H0000FFFF"          # ASS renkleri &HAABBGGRR
# bir grupta en fazla; 78 px'te ölçüm: 24 karakter ~640 px (en kötü "MMM…" 1022 px),
# güvenli alan 1000 px. Daha uzun tek kelimeler orantılı küçültülür.
MAX_KELIME, MAX_KARAKTER = 4, 24
BOSLUK_KIR = 0.6                                   # bu kadar sn sessizlik yeni grup açar
ZINCIR = 0.3                                       # bu kadar yakın tek kelimelik ünlemler birleşir
SON_TUT = 0.3                                      # grup son kelimeden sonra bu kadar kalır
SUPHELI = 0.60                                     # bu olasılığın altı raporlanır
SICAKLIK = (0.0, 0.2, 0.4)                         # Whisper yeniden deneme sıcaklıkları (rastgelelik sınırlı)
TANIMA_SURUMU = 4                                  # 2: sınırlı sıcaklık + tohum + uydurma süzgeci; 3: kalıplar; 4: VAD yedeği
VAD_YEDEK_KELIME = 10                              # bundan az kelime çıkarsa VAD açık bir kez daha tanınır
# Whisper'ın eğitim verisindeki altyazı imzaları: konuşma yokken üretilir, gerçek konuşma olamaz
UYDURMA_CUMLE = re.compile(r"altyaz[ıi]\s*:?\s*m\s*\.?\s*k\b|izlediğiniz için teşekkür|izlediginiz icin tesekkur"
                           r"|amara\.org|subtitles? (?:by|created by)|transcri(?:bed|pt|ption) by"
                           r"|untertitel (?:im auftrag|von|der amara)", re.I)
# YouTube kapanış kalıpları: yayıncı da söyleyebilir, atılmaz ama inceleme listesine girer
YOUTUBE_KALIP = re.compile(r"abone ol|abone değil|like butonu|beğenmeyi unutma|bildirimleri aç|subscribe"
                           r"|thanks? (?:you )?for watching|hit (?:that|the) (?:like|bell)|see you (?:in the )?next (?:video|time)"
                           r"|^\s*(?:thank you|you)\.?\s*$", re.I)    # sessizlikte tek başına "Thank you." sık uydurma
UYDURMA_LOGPROB = -1.2                            # bölüm ortalaması bunun altındaysa atılır
LATIN_DISI = re.compile(r"[^\W\d_a-zA-ZÀ-ɏ]")      # tr/de/en için Latin dışı harf (Kiril, Arap…)

# Görünen altyazıda maskelenecek ağır küfürler (normalize küçük harf, önek)
AGIR_KUFUR = ("amk", "amq", "amına", "amina", "amın", "amcık", "amcik", "orosp", "yarra",
              "yarak", "siktir", "sikerim", "sikeyim", "sikik", "sikiş", "sikis", "piç",
              "pezeven", "kahpe", "yavşak", "yavsak", "ibne", "götveren", "ananı", "anani",
              "hurensohn", "fotze", "wichser", "fick", "missgeburt", "schlampe", "arschloch",
              "spast", "fuck", "motherf", "cunt", "bitch")
AGIR_KUFUR_TAM = {"aq", "mk", "oç", "oc", "sik", "awk"}
NASIL = ("Görünen metni değiştirmek için ilgili kelimede \"duzeltme\": \"yeni metin\" yazın; "
         "kelimeyi gizlemek için \"duzeltme\": \"\". \"metin\" ham transkripsiyondur, değiştirmeyin. "
         "Zamanlar (bas/son, sn) gerekirse elle düzeltilebilir. Sonra aynı komutu yeniden çalıştırın.")


# --------------------------------------------------------------------------
# 1-2. Ses ve tanıma
# --------------------------------------------------------------------------

def ses_oku(video):
    import numpy as np
    ham = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-f", "f32le",
                          "-ac", "1", "-ar", "16000", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(ham, np.float32)


def cuda_dll_yolu():
    """NVIDIA'nın pip paketleri (nvidia-cublas-cu12, nvidia-cudnn-cu12) DLL'lerini site-packages/nvidia/*/bin
    altına koyar; Windows bunları kendiliğinden bulmaz. ctranslate2 yüklenmeden önce arama yoluna eklenir."""
    import importlib.util
    import os
    spec = importlib.util.find_spec("nvidia")
    if sys.platform != "win32" or not spec or not spec.submodule_search_locations:
        return
    for kok in spec.submodule_search_locations:
        for bin_ in Path(kok).glob("*/bin"):
            os.add_dll_directory(str(bin_))
            os.environ["PATH"] = f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}"


def model_yukle(ad, cihaz):
    cuda_dll_yolu()
    try:
        import av  # noqa: F401
    except ImportError:
        # PyAV'ın DLL'i bu sistemde Windows Uygulama Denetimi'nce engelli. faster-whisper onu
        # yalnız ses dosyası çözmek için kullanır; ses ffmpeg ile okunduğu için gerekmez.
        sys.modules["av"] = types.ModuleType("av")
    import numpy as np
    from faster_whisper import WhisperModel
    for c in (["cuda", "cpu"] if cihaz == "auto" else [cihaz]):
        try:
            m = WhisperModel(ad, device=c, compute_type="float16" if c == "cuda" else "int8")
            if c == "cuda":      # CUDA kütüphaneleri ancak ilk kodlamada yüklenir: dene
                list(m.transcribe(np.zeros(16000, np.float32), language="en", beam_size=1)[0])
            return m, c
        except Exception as e:  # noqa: BLE001
            print(f"  {c} kullanılamadı: {str(e).splitlines()[0][:110]}")
    sys.exit("Model yüklenemedi.")


def uydurma_kelime(metin):
    """Whisper uydurması: Latin dışı harf ya da uzun hece döngüsü ("MÖMÖMÖ…"); "ahahah", "Ooooo" kalır."""
    return bool(LATIN_DISI.search(metin)) or (len(metin) > 12 and bool(re.search(r"(\w{1,3})\1{3,}", metin)))


def tani(video, dil, model_adi, cihaz, vad=False):
    t0 = time.time()
    model, cihaz = model_yukle(model_adi, cihaz)
    ses = ses_oku(video)
    kelimeler, atlanan = _tani(model, ses, dil, vad)
    yedek = None
    if not vad and len(kelimeler) < VAD_YEDEK_KELIME:
        # Müzik altında konuşma: VAD'sız Whisper bölümün tamamını "Altyazı M.K." diye uydurabiliyor
        # (Eray 00:15: 0 kelime); VAD konuşma parçalarını ayırınca gerçek metin çıkıyor (22 kelime).
        k2, a2 = _tani(model, ses, dil, True)
        print(f"  Az kelime ({len(kelimeler)}); VAD açık yeniden tanıma: {len(k2)} kelime")
        if len(k2) > len(kelimeler):
            kelimeler, atlanan, vad, yedek = k2, a2, True, "az kelime: VAD açık yeniden tanındı"
    print(f"Tanıma: {model_adi} / {cihaz}, VAD {'açık' if vad else 'kapalı'}, "
          f"{len(ses) / 16000:.1f} sn ses, {time.time() - t0:.1f} sn sürdü, {len(kelimeler)} kelime"
          f"{f', {atlanan} sessiz segment atlandı' if atlanan else ''}")
    return {"video": Path(video).name, "dil": dil, "model": model_adi, "cihaz": cihaz, "vad": vad,
            "tanima_surumu": TANIMA_SURUMU, **({"yedek_tanima": yedek} if yedek else {}),
            "olusturma": datetime.now().isoformat(timespec="seconds"),
            "nasil_duzeltilir": NASIL, "kelimeler": kelimeler}


def _tani(model, ses, dil, vad):
    # VAD varsayılan kapalı: iki gerçek klipte oyun sesi altındaki konuşmayı kesti
    # Varsayılan yeniden deneme 1.0 sıcaklığa kadar rastgele örnekliyordu: her çalıştırma farklı,
    # kimi zaman başka dilde uydurma metin (Eray 04:10: "także", "полож"). Sıcaklık 0 tek başına
    # da bağırışta "MÖMÖMÖ…" döngüsüne giriyor. Ilımlı sıcaklıklar + sabit tohum = tekrarlanabilir.
    import ctranslate2
    ctranslate2.set_random_seed(0)
    segler, _ = model.transcribe(ses, language=dil, word_timestamps=True, vad_filter=vad,
                                 condition_on_previous_text=False, beam_size=5,
                                 temperature=SICAKLIK)
    kelimeler, atlanan = [], 0
    for s in segler:
        if s.no_speech_prob > 0.6 and s.avg_logprob < -0.8:     # sessizlikte uydurma koruması
            atlanan += 1
            continue
        if s.avg_logprob < UYDURMA_LOGPROB or UYDURMA_CUMLE.search(s.text):
            atlanan += 1                                         # emin değil / eğitim verisi kalıbı
            continue
        kalip = bool(YOUTUBE_KALIP.search(s.text))               # gerçek de olabilir: inceleme listesine
        for w in s.words or []:
            metin = w.word.strip()
            if metin and uydurma_kelime(metin):
                continue
            if metin:
                kelimeler.append({"no": len(kelimeler) + 1, "metin": metin,
                                  "bas": round(w.start, 2), "son": round(w.end, 2),
                                  "olasilik": round(w.probability, 2), "duzeltme": None,
                                  **({"kalip": True} if kalip else {})})
    return kelimeler, atlanan


# --------------------------------------------------------------------------
# 3-4. Görünen metin, gruplar, ASS
# --------------------------------------------------------------------------

def agir_kufur_mu(kelime, dil):
    w = (tr_lower if dil == "tr" else str.lower)(re.sub(r"[^\w]", "", kelime))
    return w in AGIR_KUFUR_TAM or w.startswith(AGIR_KUFUR)


def maskele(kelime):
    """'Siktir!' -> 'S*****!'  (baş/son noktalama korunur)"""
    m = re.match(r"^(\W*)(\w)(\w*)(\W*)$", kelime)
    return f"{m[1]}{m[2]}{'*' * len(m[3])}{m[4]}" if m else "*" * len(kelime)


def gorunen(veri, maske, buyuk):
    dil = veri["dil"]
    ust = tr_upper if dil == "tr" else str.upper
    sonuc = []
    for k in veri["kelimeler"]:
        t = k["metin"] if k.get("duzeltme") is None else k["duzeltme"]
        t = t.replace("{", "").replace("}", "").replace("\\", "").strip()
        if not t:
            continue
        if maske and agir_kufur_mu(t, dil):
            t = maskele(t)
        sonuc.append({**k, "goster": ust(t) if buyuk else t})
    return sonuc


def cumle_sonu(k):
    return k["goster"].endswith((".", "?", "!", "…"))


def karakter(g):
    return sum(len(x["goster"]) for x in g) + len(g) - 1


def dengeli_bol(ifade):
    """Bir ifadeyi (cümle / duraklamasız parça) en az sayıda gruba, eşit uzunluklarla böler.
    Maliyet: (grup sayısı, karakter uzunluklarının kareleri toplamı - virgülden bölme ödülü).
    4+4+1 yerine 3+3+3; tek kelime sınırı aşsa da tek başına grup olabilir."""
    n = len(ifade)
    en_iyi = [None] * (n + 1)
    en_iyi[0] = (0, 0, 0)
    for j in range(1, n + 1):
        for i in range(max(0, j - MAX_KELIME), j):
            g = ifade[i:j]
            if en_iyi[i] is None or (len(g) > 1 and karakter(g) > MAX_KARAKTER):
                continue
            odul = 40 if j < n and g[-1]["goster"].endswith((",", ";", ":")) else 0
            aday = (en_iyi[i][0] + 1, en_iyi[i][1] + karakter(g) ** 2 - odul, i)
            if en_iyi[j] is None or aday[:2] <= en_iyi[j][:2]:      # eşitlikte ilk grup uzun
                en_iyi[j] = aday
    parcalar, j = [], n
    while j > 0:
        i = en_iyi[j][2]
        parcalar.insert(0, ifade[i:j])
        j = i
    return parcalar


def grupla(kelimeler):
    # 1. ifadelere ayır: cümle sonu ve >= BOSLUK_KIR sessizlik
    ifadeler, g = [], []
    for k in kelimeler:
        if g and (k["bas"] - g[-1]["son"] >= BOSLUK_KIR or cumle_sonu(g[-1])):
            ifadeler.append(g)
            g = []
        g.append(k)
    if g:
        ifadeler.append(g)
    # 2. her ifadeyi en fazla 4 kelime / 24 karakterlik dengeli gruplara böl
    gruplar = [p for ifade in ifadeler for p in dengeli_bol(ifade)]
    # 3. ünlem zinciri: art arda, birbirine yakın tek kelimelik cümleler aynı satırda
    #    ("GEÇER. GEÇER. GEÇER."); arası sessiz olan ünlemler ("HADİ.") tek kalır
    sonuc = []
    for g in gruplar:
        if (sonuc and len(g) == 1 and cumle_sonu(g[0]) and all(map(cumle_sonu, sonuc[-1]))
                and g[0]["bas"] - sonuc[-1][-1]["son"] < ZINCIR
                and len(sonuc[-1]) < MAX_KELIME and karakter(sonuc[-1] + g) <= MAX_KARAKTER):
            sonuc[-1].append(g[0])
        else:
            sonuc.append(g)
    return sonuc


# --------------------------------------------------------------------------
# İnceleme listesi (hiçbir kelimeyi kendiliğinden düzeltmez)
# --------------------------------------------------------------------------

def _yalin(t):
    return re.sub(r"[^\w]", "", str.lower(t or ""))


def inceleme_satirlari(veri, alternatif=None):
    """Sesle kontrol edilmesi gereken kelimeler: düşük güven, ikinci tanımayla uyuşmazlık,
    sesle doğrulanmamış elle düzeltme. "dogrulandi": true olanlar listeye girmez."""
    alt = (alternatif or {}).get("kelimeler", [])
    satirlar = []
    for k in veri["kelimeler"]:
        if k.get("dogrulandi"):
            continue
        nedenler, alt_metin = [], ""
        if alternatif is not None:
            ortak = [x["metin"] for x in alt if x["bas"] < k["son"] and k["bas"] < x["son"]]
            alt_metin = " ".join(ortak)
            if not ortak:
                nedenler.append("ikinci tanımada yok")
            elif _yalin(k["metin"]) not in {_yalin(x) for x in ortak}:
                nedenler.append("ikinci tanımayla uyuşmuyor")
        if k["olasilik"] < SUPHELI:
            nedenler.append(f"düşük güven ({k['olasilik']:.2f})")
        if k["son"] - k["bas"] < 0.05:
            nedenler.append("zaman damgası şüpheli (süre ~0)")
        if k.get("kalip"):
            nedenler.append("olası Whisper uydurması (YouTube kapanış kalıbı); dinleyin")
        if k.get("duzeltme") is not None:
            nedenler.append("elle düzeltildi, sesle doğrulanmadı"
                            + (f" ({k['duzeltme_notu']})" if k.get("duzeltme_notu") else ""))
        if nedenler:
            satirlar.append({**k, "alternatif": alt_metin, "nedenler": nedenler})
    return satirlar


# --------------------------------------------------------------------------
# Düz metin düzeltme: videodaki her altyazı satırı bir satır (JSON düzenlemeden)
# --------------------------------------------------------------------------

DUZ_SATIR = re.compile(r"^\[(\d+)\]\s+[\d:.]+\s+(!?)\s*\|\s?(.*)$")


def duz_imza(veri):
    """Kelime kaydının özeti: düz metin dosyası eski bir kayda uygulanmasın."""
    import hashlib
    return hashlib.sha1(json.dumps(veri["kelimeler"], ensure_ascii=False, sort_keys=True)
                        .encode("utf-8")).hexdigest()[:8]


def duz_satirlar(veri, alternatif=None):
    """[(sıra, başlangıç sn, şüpheli mi, görünen metin)] - videoda görünen gruplarla aynı."""
    supheli = {k["no"] for k in inceleme_satirlari(veri, alternatif)}
    gruplar = grupla(gorunen(veri, True, True))
    return [(i, g[0]["bas"], any(x["no"] in supheli for x in g), " ".join(x["goster"] for x in g))
            for i, g in enumerate(gruplar, 1)]


def duz_uygula(veri, duzenlenen, alternatif=None):
    """duzenlenen: {sıra: (işaret kaldı mı, yeni metin)}. Değişen satır: kelime sayısı aynıysa
    kelime kelime, değilse ilk kelimeye tüm satır (diğerleri gizlenir); boş satır gizlenir.
    Kullanıcının değiştirdiği ya da '!' işaretini kaldırdığı satır 'dogrulandi' olur (elle onay).
    Döner: değişen satır sayısı."""
    ust = tr_upper if veri["dil"] == "tr" else str.upper
    supheli = {k["no"] for k in inceleme_satirlari(veri, alternatif)}
    no = {k["no"]: k for k in veri["kelimeler"]}
    degisen = 0
    for i, g in enumerate(grupla(gorunen(veri, True, True)), 1):
        if i not in duzenlenen:
            continue
        isaret, yeni = duzenlenen[i]
        yeni = " ".join(yeni.split())
        eski = " ".join(x["goster"] for x in g)
        kel = [no[x["no"]] for x in g]
        if ust(yeni) == eski:
            if not isaret and any(x["no"] in supheli for x in g):
                for k in kel:
                    k["dogrulandi"] = True
                degisen += 1
            continue
        parca = yeni.split()
        if len(parca) == len(g):
            for k, x, t in zip(kel, g, parca):
                if ust(t) != x["goster"]:
                    k["duzeltme"] = t
        else:                                     # kelime sayısı değişti: zamanlara sırayla dağıt
            son = len(kel) - 1
            for j, k in enumerate(kel):
                if j < son:
                    k["duzeltme"] = parca[j] if j < len(parca) else ""
                else:
                    k["duzeltme"] = " ".join(parca[son:])
            if parca:
                kel[0]["duzeltme_notu"] = "satır olarak düzeltildi; vurgu zamanı yaklaşık"
        for k in kel:
            k["dogrulandi"] = True
        degisen += 1
    return degisen


def inceleme_yaz(veri, alternatif, yol):
    s = inceleme_satirlari(veri, alternatif)

    def vad(v):
        return {True: "açık", False: "kapalı"}.get(v.get("vad"), "bilinmiyor")

    ikinci = f"var (.bak, VAD {vad(alternatif)})" if alternatif else "yok"
    md = [f"# Altyazı inceleme listesi: {veri['video']}", "",
          f"Dil: {veri['dil']} · Model: {veri.get('model')} · VAD {vad(veri)} · "
          f"İkinci tanıma: {ikinci}", "",
          "Bu kelimeler **sesle doğrulanmadı**. Dinleyip doğruysa JSON'da kelimeye "
          "`\"dogrulandi\": true` ekleyin; yanlışsa `\"duzeltme\"` yazıp `\"dogrulandi\": true` ekleyin.", "",
          "| No | Zaman (sn) | Ham metin | Görünen | Güven | İkinci tanıma | Neden |",
          "|---|---|---|---|---|---|---|"]
    for k in s:
        gor = k["metin"] if k.get("duzeltme") is None else k["duzeltme"]
        md.append(f"| {k['no']} | {k['bas']:.2f}–{k['son']:.2f} | {k['metin']} | {gor or '(gizli)'} | "
                  f"{k['olasilik']:.2f} | {k['alternatif'] or '—'} | {'; '.join(k['nedenler'])} |")
    if not s:
        md.append("| — | | | | | | İncelenecek kelime yok |")
    yol.write_text("\n".join(md) + "\n", encoding="utf-8")
    return s


def ass_zaman(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_olustur(gruplar, y, boyut):
    satirlar = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {GEN}", f"PlayResY: {YUK}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Shorts,{YAZI_TIPI},{boyut},{BEYAZ},{SARI},&H00000000,&H96000000,"
        "-1,0,0,0,100,100,0,0,1,6,3,5,40,40,0,1", "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for gi, g in enumerate(gruplar):
        sonraki = gruplar[gi + 1][0]["bas"] if gi + 1 < len(gruplar) else None
        bitis = g[-1]["son"] + SON_TUT
        if sonraki is not None:
            bitis = min(bitis, sonraki)
        bitis = max(bitis, g[-1]["son"])
        uzunluk = sum(len(x["goster"]) for x in g) + len(g) - 1
        fs = f"\\fs{int(boyut * MAX_KARAKTER / uzunluk)}" if uzunluk > MAX_KARAKTER else ""
        for j, k in enumerate(g):
            bas = g[0]["bas"] if j == 0 else k["bas"]
            son = g[j + 1]["bas"] if j + 1 < len(g) else bitis
            if son <= bas:
                continue
            metin = " ".join(f"{{\\c{SARI}&}}{x['goster']}{{\\c{BEYAZ}&}}" if x is k else x["goster"]
                             for x in g)
            satirlar.append(f"Dialogue: 0,{ass_zaman(bas)},{ass_zaman(son)},Shorts,,0,0,0,,"
                            f"{{\\pos({GEN // 2},{y}){fs}}}{metin}")
    return "\n".join(satirlar) + "\n"


# --------------------------------------------------------------------------
# 5. Yakma
# --------------------------------------------------------------------------

def yak(video, ass, cikti):
    # filtre yolları: ASS'i kendi klasöründen göreli ver, font klasöründeki ':' kaçışlı
    filtre = f"ass={ass.name}:fontsdir={FONT_KLASORU.replace(':', chr(92) * 2 + ':')}"
    komut = ["ffmpeg", "-y", "-v", "error", "-stats", "-i", str(video.resolve()), "-vf", filtre,
             "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "medium",
             "-crf", "18", "-c:a", "copy", "-movflags", "+faststart", str(cikti.resolve())]
    r = subprocess.run(komut, cwd=ass.parent)
    if r.returncode != 0:
        sys.exit(f"ffmpeg hata verdi ({r.returncode}).")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Dikey klibe kelime vurgulu altyazı.")
    ap.add_argument("video", help="dikey MP4 (değiştirilmez)")
    ap.add_argument("--dil", required=True, choices=["tr", "de", "en"], help="konuşma dili")
    ap.add_argument("-o", "--cikti", help="çıktı MP4 (<video>-altyazili.mp4)")
    ap.add_argument("--model", default="large-v3-turbo", help="faster-whisper modeli")
    ap.add_argument("--cihaz", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--yeniden-tani", action="store_true",
                    help="JSON olsa da yeniden tanı (eski JSON .bak olarak saklanır)")
    ap.add_argument("--vad", action="store_true",
                    help="sessizlik süzgecini aç (gürültülü oyun sesinde konuşmayı kesebilir)")
    ap.add_argument("--maskesiz", action="store_true", help="ağır küfürleri maskeleme")
    ap.add_argument("--orijinal-harf", action="store_true", help="BÜYÜK HARF'e çevirme")
    ap.add_argument("--y", type=int, default=652, help="altyazı dikey merkezi, px (652)")
    ap.add_argument("--boyut", type=int, default=78, help="yazı boyutu, px (78)")
    ap.add_argument("--sadece-json", action="store_true", help="yalnız tanı, video üretme")
    a = ap.parse_args()

    video = Path(a.video)
    if not video.exists():
        sys.exit(f"Video bulunamadı: {video}")
    kok = video.with_name(video.stem)
    js, ass = kok.with_suffix(".kelimeler.json"), kok.with_suffix(".ass")
    cikti = Path(a.cikti) if a.cikti else video.with_name(f"{video.stem}-altyazili.mp4")
    if cikti.resolve() == video.resolve():
        sys.exit("Çıktı kaynak videoyla aynı olamaz.")

    veri = json.loads(js.read_text(encoding="utf-8")) if js.exists() else None
    eski_surum = veri is not None and veri.get("tanima_surumu", 1) < TANIMA_SURUMU
    elle = veri is not None and any(k.get("duzeltme") is not None or k.get("dogrulandi")
                                    for k in veri["kelimeler"])
    if veri is not None and not a.yeniden_tani and not (eski_surum and not elle):
        print(f"Transkripsiyon JSON'dan: {js} ({len(veri['kelimeler'])} kelime)")
        if eski_surum:
            print("  Uyarı: JSON eski tanıma sürümüyle üretilmiş ama elle düzeltme içeriyor; korunuyor")
        if veri.get("dil") != a.dil:
            print(f"  Uyarı: JSON dili {veri.get('dil')}, --dil {a.dil}")
    else:
        if js.exists() and eski_surum and not a.yeniden_tani:
            # eski sürümün uydurma tanıması karşılaştırma (.bak) olmasın: eski/ altında tarihli saklanır
            print("  Eski tanıma sürümü (rastgele yeniden deneme); yeniden tanınıyor")
            (js.parent / "eski").mkdir(exist_ok=True)
            js.replace(js.parent / "eski" / f"{js.stem}.{datetime.now():%Y%m%d-%H%M%S}.json")
        elif js.exists():
            js.replace(js.with_suffix(".json.bak"))
        veri = tani(video, a.dil, a.model, a.cihaz, a.vad)
        js.write_text(json.dumps(veri, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Transkripsiyon yazıldı: {js}")

    kel = gorunen(veri, not a.maskesiz, not a.orijinal_harf)
    gruplar = grupla(kel)
    print()
    for g in gruplar:
        print(f"  [{g[0]['bas']:6.2f}-{g[-1]['son']:6.2f}] " + " ".join(x["goster"] for x in g))
    duzeltilen = [k for k in veri["kelimeler"] if k.get("duzeltme") is not None]
    maskeli = sum("*" in k["goster"] for k in kel)
    tek = sum(len(g) == 1 for g in gruplar)
    print(f"\n{len(kel)} kelime, {len(gruplar)} grup ({tek} tek kelimelik), {maskeli} maskeli, "
          f"{len(duzeltilen)} elle düzeltilmiş")
    bak = js.with_suffix(".json.bak")
    alternatif = json.loads(bak.read_text(encoding="utf-8")) if bak.exists() else None
    inc = kok.with_suffix(".inceleme.md")
    liste = inceleme_yaz(veri, alternatif, inc)
    print(f"İnceleme listesi: {inc} ({len(liste)} kelime sesle kontrol edilmeli)")
    if a.sadece_json:
        return
    ass.write_text(ass_olustur(gruplar, a.y, a.boyut), encoding="utf-8-sig")
    print(f"ASS: {ass}")
    yak(video, ass, cikti)
    print(f"Çıktı: {cikti}")


if __name__ == "__main__":
    main()
