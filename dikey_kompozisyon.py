#!/usr/bin/env python3
"""dikey_kompozisyon.py - Yatay yayın görüntüsünden 1080x1920 dikey kompozisyon.

Üst bölüm (~%34): yayıncı kamerası.  Alt bölüm (~%66): Item Upgrade penceresi;
--oyun ile verilen zaman aralıklarında alt bölüm oyun görüntüsüne geçer.
Her bölge en-boy oranı korunarak sığdırılır, boşluklar aynı bölgenin
bulanık/karartılmış kopyasıyla doldurulur. Yalnız standart kütüphane ve ffmpeg.

Örnek:
    python dikey_kompozisyon.py kaynak/parca.mp4 --bas 25 --sure 40 \
        --oyun 26.05-40 -o klipler/dikey/klip.mp4
"""

import argparse
import subprocess
import sys
from pathlib import Path

GEN, YUK, FPS = 1080, 1920, 60

# Kaynak 1920x1080 koordinatları: x1,y1,x2,y2
KAMERA = "968,762,1452,1040"
PENCERE = "1475,0,1900,740"
OYUN = "636,0,1284,760"      # karakter ortada; kamera (y>=762) ve sohbet katmanı dışarıda


def bolge(s):
    """'x1,y1,x2,y2' -> (x, y, w, h); w/h çift sayıya yuvarlanır (yuv420p)."""
    x1, y1, x2, y2 = (int(v) for v in s.split(","))
    w, h = x2 - x1, y2 - y1
    return x1, y1, w + (w % 2), h + (h % 2)


def araliklar(s):
    """'26.05-40,50-55' -> [(26.05, 40.0), (50.0, 55.0)]"""
    sonuc = []
    for parca in filter(None, (s or "").split(",")):
        a, b = parca.split("-")
        sonuc.append((float(a), float(b)))
    return sonuc


def cift(v):
    return max(2, int(v) // 2 * 2)


def yerlestir(giris, b, kw, kh, cikis):
    """Bölgeyi kw x kh kutusuna oranı bozmadan sığdırır; arka plan bulanık kopya."""
    x, y, w, h = b
    olcek = min(kw / w, kh / h)
    sw, sh = cift(w * olcek), cift(h * olcek)
    bw, bh = cift(kw / 4), cift(kh / 4)
    return (f"[{giris}]crop={w}:{h}:{x}:{y},split=2[{cikis}_a][{cikis}_b];"
            f"[{cikis}_a]scale={sw}:{sh}:flags=lanczos[{cikis}_on];"
            f"[{cikis}_b]scale={bw}:{bh}:force_original_aspect_ratio=increase,"
            f"crop={bw}:{bh},boxblur=6:2,eq=brightness=-0.18:saturation=0.8,"
            f"scale={kw}:{kh}[{cikis}_arka];"
            f"[{cikis}_arka][{cikis}_on]overlay=(W-w)/2:(H-h)/2[{cikis}]")


def filtre_olustur(kamera, pencere, oyun, oyun_araliklari, ust_oran):
    ust = cift(YUK * ust_oran)
    alt = YUK - ust
    oyunlu = bool(oyun_araliklari)
    parcalar = [f"[0:v]split={3 if oyunlu else 2}[k][p]{'[o]' if oyunlu else ''}",
                yerlestir("k", kamera, GEN, ust, "ust"),
                yerlestir("p", pencere, GEN, alt, "pen")]
    if oyunlu:
        kosul = "+".join(f"between(t,{a},{b})" for a, b in oyun_araliklari)
        parcalar += [yerlestir("o", oyun, GEN, alt, "oyn"),
                     f"[pen][oyn]overlay=0:0:enable='{kosul}'[alt]"]
    else:
        parcalar.append("[pen]null[alt]")
    parcalar.append(f"[ust][alt]vstack=inputs=2,fps={FPS},format=yuv420p,setsar=1[v]")
    return ";".join(parcalar), ust, alt


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Kamera üstte, Upgrade penceresi altta dikey video.")
    ap.add_argument("kaynak", help="1920x1080 kaynak video")
    ap.add_argument("-o", "--cikti", required=True, help="çıktı .mp4")
    ap.add_argument("--bas", type=float, default=0, help="kaynakta başlangıç saniyesi")
    ap.add_argument("--sure", type=float, default=40, help="süre, sn (40)")
    ap.add_argument("--oyun", default="", help="alt bölümün oyuna geçtiği aralıklar, "
                    "klibe göre sn: '26.05-40' ya da '10-12,30-40'")
    ap.add_argument("--kamera", default=KAMERA, help=f"x1,y1,x2,y2 ({KAMERA})")
    ap.add_argument("--pencere", default=PENCERE, help=f"x1,y1,x2,y2 ({PENCERE})")
    ap.add_argument("--oyun-bolge", default=OYUN, help=f"x1,y1,x2,y2 ({OYUN})")
    ap.add_argument("--ust-oran", type=float, default=0.34, help="üst bölüm oranı (0.34)")
    ap.add_argument("--filtre-goster", action="store_true", help="filter_complex'i yazdır")
    a = ap.parse_args()

    if not Path(a.kaynak).exists():
        sys.exit(f"Kaynak bulunamadı: {a.kaynak}")
    cikti = Path(a.cikti)
    if cikti.resolve() == Path(a.kaynak).resolve():
        sys.exit("Çıktı kaynakla aynı olamaz.")
    filtre, ust, alt = filtre_olustur(bolge(a.kamera), bolge(a.pencere), bolge(a.oyun_bolge),
                                      araliklar(a.oyun), a.ust_oran)
    if a.filtre_goster:
        print(filtre.replace(";", ";\n"))
    cikti.parent.mkdir(parents=True, exist_ok=True)
    komut = ["ffmpeg", "-y", "-v", "error", "-stats", "-ss", f"{a.bas:.3f}", "-i", a.kaynak,
             "-t", f"{a.sure:.3f}", "-filter_complex", filtre,
             "-map", "[v]", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "medium",
             "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(cikti)]
    print(f"{a.kaynak} [{a.bas:g}+{a.sure:g} sn] -> {cikti}  (üst {ust}px, alt {alt}px, "
          f"oyun: {a.oyun or 'yok'})", flush=True)
    r = subprocess.run(komut)
    if r.returncode != 0:
        sys.exit(f"ffmpeg hata verdi ({r.returncode}).")


if __name__ == "__main__":
    main()
