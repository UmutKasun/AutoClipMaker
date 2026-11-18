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
MAX_KELIME, MAX_KARAKTER = 4, 18                   # bir grupta en fazla
BOSLUK_KIR = 0.6                                   # bu kadar sn sessizlik yeni grup açar
SON_TUT = 0.3                                      # grup son kelimeden sonra bu kadar kalır
SUPHELI = 0.60                                     # bu olasılığın altı raporlanır

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


def model_yukle(ad, cihaz):
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


def tani(video, dil, model_adi, cihaz, vad=False):
    t0 = time.time()
    model, cihaz = model_yukle(model_adi, cihaz)
    ses = ses_oku(video)
    # VAD varsayılan kapalı: iki gerçek klipte oyun sesi altındaki konuşmayı kesti
    segler, _ = model.transcribe(ses, language=dil, word_timestamps=True, vad_filter=vad,
                                 condition_on_previous_text=False, beam_size=5)
    kelimeler, atlanan = [], 0
    for s in segler:
        if s.no_speech_prob > 0.6 and s.avg_logprob < -0.8:     # sessizlikte uydurma koruması
            atlanan += 1
            continue
        for w in s.words or []:
            metin = w.word.strip()
            if metin:
                kelimeler.append({"no": len(kelimeler) + 1, "metin": metin,
                                  "bas": round(w.start, 2), "son": round(w.end, 2),
                                  "olasilik": round(w.probability, 2), "duzeltme": None})
    print(f"Tanıma: {model_adi} / {cihaz}, VAD {'açık' if vad else 'kapalı'}, "
          f"{len(ses) / 16000:.1f} sn ses, {time.time() - t0:.1f} sn sürdü, {len(kelimeler)} kelime"
          f"{f', {atlanan} sessiz segment atlandı' if atlanan else ''}")
    return {"video": Path(video).name, "dil": dil, "model": model_adi, "cihaz": cihaz, "vad": vad,
            "olusturma": datetime.now().isoformat(timespec="seconds"),
            "nasil_duzeltilir": NASIL, "kelimeler": kelimeler}


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


def grupla(kelimeler):
    gruplar, g = [], []
    for k in kelimeler:
        if g:
            bosluk = k["bas"] - g[-1]["son"]
            karakter = sum(len(x["goster"]) + 1 for x in g) + len(k["goster"])
            noktalama = g[-1]["goster"].endswith((".", "?", "!", "…"))
            # cümle sonu her zaman böler; tek kelimelik kısa cümleler ("HADİ.") tek kalır
            if (len(g) >= MAX_KELIME or bosluk >= BOSLUK_KIR or karakter > MAX_KARAKTER
                    or noktalama):
                gruplar.append(g)
                g = []
        g.append(k)
    if g:
        gruplar.append(g)
    return gruplar


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

    if js.exists() and not a.yeniden_tani:
        veri = json.loads(js.read_text(encoding="utf-8"))
        print(f"Transkripsiyon JSON'dan: {js} ({len(veri['kelimeler'])} kelime)")
        if veri.get("dil") != a.dil:
            print(f"  Uyarı: JSON dili {veri.get('dil')}, --dil {a.dil}")
    else:
        if js.exists():
            js.replace(js.with_suffix(".json.bak"))
        veri = tani(video, a.dil, a.model, a.cihaz, a.vad)
        js.write_text(json.dumps(veri, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Transkripsiyon yazıldı: {js}")

    kel = gorunen(veri, not a.maskesiz, not a.orijinal_harf)
    gruplar = grupla(kel)
    print()
    for g in gruplar:
        print(f"  [{g[0]['bas']:6.2f}-{g[-1]['son']:6.2f}] " + " ".join(x["goster"] for x in g))
    supheli = [k for k in veri["kelimeler"] if k["olasilik"] < SUPHELI and k.get("duzeltme") is None]
    duzeltilen = [k for k in veri["kelimeler"] if k.get("duzeltme") is not None]
    maskeli = sum("*" in k["goster"] for k in kel)
    print(f"\n{len(kel)} kelime, {len(gruplar)} grup, {maskeli} maskeli, {len(duzeltilen)} elle düzeltilmiş")
    if supheli:
        print(f"Şüpheli (olasılık < {SUPHELI}): " +
              ", ".join(f"#{k['no']} {k['metin']} ({k['bas']:.1f} sn, {k['olasilik']:.2f})" for k in supheli))
    if a.sadece_json:
        return
    ass.write_text(ass_olustur(gruplar, a.y, a.boyut), encoding="utf-8-sig")
    print(f"ASS: {ass}")
    yak(video, ass, cikti)
    print(f"Çıktı: {cikti}")


if __name__ == "__main__":
    main()
