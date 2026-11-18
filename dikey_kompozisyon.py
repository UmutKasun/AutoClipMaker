#!/usr/bin/env python3
"""dikey_kompozisyon.py - Yatay yayın görüntüsünden 1080x1920 dikey kompozisyon.

Üst bölüm (~%34): yayıncı kamerası.  Alt bölüm (~%66): Item Upgrade penceresi;
--oyun ile verilen zaman aralıklarında alt bölüm oyun görüntüsüne geçer.
Her bölge en-boy oranı korunarak sığdırılır, boşluklar aynı bölgenin
bulanık/karartılmış kopyasıyla doldurulur. Yalnız standart kütüphane ve ffmpeg.

Örnek:
    python dikey_kompozisyon.py kaynak/parca.mp4 --bas 25 --sure 40 \
        --oyun 26.05-40 -o klipler/dikey/klip.mp4
    python dikey_kompozisyon.py kaynak/parca.mp4 --duzen se7en --oyun 26.05-40 -o klip.mp4

Bölge önceliği: komut satırı (--kamera ...) > --duzen profili > yerleşik varsayılan.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from duzen import profil

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


def yerlestir(giris, b, kw, kh, cikis, ust_bosluk=None):
    """Bölgeyi kw x kh kutusuna oranı bozmadan sığdırır; arka plan bulanık kopya.
    ust_bosluk verilirse ortalamak yerine üstten bu kadar px aşağı yerleşir."""
    x, y, w, h = b
    olcek = min(kw / w, kh / h)
    sw, sh = cift(w * olcek), cift(h * olcek)
    bw, bh = cift(kw / 4), cift(kh / 4)
    return (f"[{giris}]crop={w}:{h}:{x}:{y},split=2[{cikis}_a][{cikis}_b];"
            f"[{cikis}_a]scale={sw}:{sh}:flags=lanczos[{cikis}_on];"
            f"[{cikis}_b]scale={bw}:{bh}:force_original_aspect_ratio=increase,"
            f"crop={bw}:{bh},boxblur=6:2,eq=brightness=-0.18:saturation=0.8,"
            f"scale={kw}:{kh}[{cikis}_arka];"
            f"[{cikis}_arka][{cikis}_on]overlay=(W-w)/2:"
            f"{'(H-h)/2' if ust_bosluk is None else f'min({int(ust_bosluk)}\,H-h)'}[{cikis}]")


def maskele(giris, m, cikis):
    """Kamera alt bölümde ikinci kez görünmesin: o dikdörtgen bulanık ve karartılmış."""
    x, y, w, h = m
    r = max(1, min(24, min(w, h) // 4 - 1))
    return (f"[{giris}]split=2[{cikis}_0][{cikis}_1];[{cikis}_1]crop={w}:{h}:{x}:{y},"
            f"boxblur={r}:4,eq=brightness=-0.3:saturation=0.5[{cikis}_b];"
            f"[{cikis}_0][{cikis}_b]overlay={x}:{y}[{cikis}]")


def filtre_olustur(kamera, pencere, oyun, oyun_araliklari, ust_oran, maske=None, oyun_ust=None):
    ust = cift(YUK * ust_oran)
    alt = YUK - ust
    oyunlu = bool(oyun_araliklari)
    parcalar = [f"[0:v]split={3 if oyunlu else 2}[k][p0]{'[o]' if oyunlu else ''}",
                maskele("p0", maske, "p") if maske else "[p0]null[p]",
                yerlestir("k", kamera, GEN, ust, "ust"),
                yerlestir("p", pencere, GEN, alt, "pen", oyun_ust)]
    if oyunlu:
        kosul = "+".join(f"between(t,{a},{b})" for a, b in oyun_araliklari)
        parcalar += [yerlestir("o", oyun, GEN, alt, "oyn", oyun_ust),
                     f"[pen][oyn]overlay=0:0:enable='{kosul}'[alt]"]
    else:
        parcalar.append("[pen]null[alt]")
    parcalar.append(f"[ust][alt]vstack=inputs=2,fps={FPS},format=yuv420p,setsar=1[v]")
    return ";".join(parcalar), ust, alt


def guvenli_filtre(gen, yuk):
    """Güvenli yerleşim: tüm kaynak kare kırpılmadan 1080x1920'ye sığar (bulanık arka plan).
    Kamera/oyun bölgesi bilinmediğinde ya da güvenilmediğinde önemli görüntü kesilmez."""
    filtre = yerlestir("0:v", (0, 0, cift(gen), cift(yuk)), GEN, YUK, "tam")
    return filtre + f";[tam]fps={FPS},format=yuv420p,setsar=1[v]"


def kaynak_cozunurluk(yol):
    try:
        cikti = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height", "-of", "csv=p=0", str(yol)],
                               capture_output=True, text=True, check=True).stdout
        gen, yuk = (int(v) for v in cikti.strip().split(",")[:2])
    except (OSError, subprocess.CalledProcessError, ValueError) as e:
        raise profil.ProfilHatasi(f"kaynak çözünürlüğü okunamadı ({yol}): {e}") from None
    return gen, yuk


def yerlesimi_belirle(a, cozunurluk):
    """-> (bölgeler {kamera, pencere, oyun}, ust_oran, kaynaklar ya da None).
    --duzen yoksa eski davranış: CLI ya da yerleşik varsayılan, çözünürlük sorulmaz."""
    cli = {"kamera": a.kamera, "pencere": a.pencere, "oyun": a.oyun_bolge}
    if not a.duzen:
        if a.sahne:
            raise profil.ProfilHatasi("--sahne yalnız --duzen ile kullanılabilir")
        varsayilan = {"kamera": KAMERA, "pencere": PENCERE, "oyun": OYUN}
        return ({ad: bolge(cli[ad] or varsayilan[ad]) for ad in cli},
                0.34 if a.ust_oran is None else a.ust_oran, None)
    gen, yuk = cozunurluk()
    y = profil.yerlesim(profil.yukle(a.duzen), a.sahne, gen, yuk)
    b, kaynak = {}, {}
    for ad in cli:
        if cli[ad]:
            b[ad] = profil.kare_icinde(bolge(cli[ad]), gen, yuk,
                                       f"--{'oyun-bolge' if ad == 'oyun' else ad}")
            kaynak[ad] = "komut satırı"
        elif ad in y:
            b[ad], kaynak[ad] = y[ad], "profil"
        elif a.oyun:
            raise profil.ProfilHatasi(f"--oyun aralıkları verildi ama profilde '{ad}' bölgesi yok; "
                                      "--oyun-bolge ile verin")
        else:
            b[ad], kaynak[ad] = None, "yok"
    return b, (y["ust_oran"] if a.ust_oran is None else a.ust_oran), kaynak


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Kamera üstte, Upgrade penceresi altta dikey video.")
    ap.add_argument("kaynak", help="1920x1080 kaynak video")
    ap.add_argument("-o", "--cikti", required=True, help="çıktı .mp4")
    ap.add_argument("--bas", type=float, default=0, help="kaynakta başlangıç saniyesi")
    ap.add_argument("--sure", type=float, default=40, help="süre, sn (40)")
    ap.add_argument("--oyun", default="", help="alt bölümün oyuna geçtiği aralıklar, "
                    "klibe göre sn: '26.05-40' ya da '10-12,30-40'")
    ap.add_argument("--duzen", help="yayıncı profili: duzenler/<ad>.json adı (se7en) ya da dosya yolu")
    ap.add_argument("--sahne", help="profildeki sahne (varsayılan: profilin varsayilan_sahne)")
    ap.add_argument("--kamera", help=f"x1,y1,x2,y2 kaynak pikseli; profili geçersiz kılar ({KAMERA})")
    ap.add_argument("--pencere", help=f"x1,y1,x2,y2 kaynak pikseli; profili geçersiz kılar ({PENCERE})")
    ap.add_argument("--oyun-bolge", help=f"x1,y1,x2,y2 kaynak pikseli; profili geçersiz kılar ({OYUN})")
    ap.add_argument("--ust-oran", type=float, help="üst bölüm oranı (profil ya da 0.34)")
    ap.add_argument("--oyun-ust", type=int, help="alt bölümde oyunun üstündeki boşluk, px "
                    "(verilmezse ortalanır; alttaki Shorts arayüzünden uzak tutmak için)")
    ap.add_argument("--maske", help="x1,y1,x2,y2: alt bölümde bulanıklaştırılacak bölge "
                    "(oyun kırpımına düşen kamera; yayıncı iki kez görünmesin)")
    ap.add_argument("--guvenli", action="store_true",
                    help="güvenli tam görüntü: kırpma yok, kaynak karenin tamamı sığdırılır")
    ap.add_argument("--filtre-goster", action="store_true", help="filter_complex'i yazdır")
    a = ap.parse_args()

    if not Path(a.kaynak).exists():
        sys.exit(f"Kaynak bulunamadı: {a.kaynak}")
    cikti = Path(a.cikti)
    if cikti.resolve() == Path(a.kaynak).resolve():
        sys.exit("Çıktı kaynakla aynı olamaz.")
    if a.guvenli:
        catisan = [s for s, v in (("--duzen", a.duzen), ("--sahne", a.sahne), ("--kamera", a.kamera),
                                  ("--pencere", a.pencere), ("--oyun-bolge", a.oyun_bolge),
                                  ("--oyun", a.oyun), ("--ust-oran", a.ust_oran), ("--maske", a.maske),
                                  ("--oyun-ust", a.oyun_ust)) if v]
        if catisan:
            sys.exit(f"--guvenli şunlarla birlikte kullanılamaz: {', '.join(catisan)}\nVideo üretilmedi.")
        try:
            gen, yuk = kaynak_cozunurluk(a.kaynak)
        except profil.ProfilHatasi as e:
            sys.exit(f"Düzen hatası: {e}\nVideo üretilmedi.")
        filtre, ust, alt = guvenli_filtre(gen, yuk), 0, YUK
        print(f"Düzen: güvenli tam görüntü ({gen}x{yuk} kırpılmadan)")
    else:
        try:
            b, ust_oran, kaynaklar = yerlesimi_belirle(a, lambda: kaynak_cozunurluk(a.kaynak))
        except profil.ProfilHatasi as e:
            sys.exit(f"Düzen hatası: {e}\nVideo üretilmedi.")
        if kaynaklar:
            print(f"Düzen: {a.duzen} / sahne {a.sahne or 'varsayılan'}: " + "; ".join(
                f"{ad} {'—' if b[ad] is None else '{},{} {}x{}'.format(*b[ad])} ({k})"
                for ad, k in kaynaklar.items()))
        filtre, ust, alt = filtre_olustur(b["kamera"], b["pencere"], b["oyun"], araliklar(a.oyun),
                                          ust_oran, bolge(a.maske) if a.maske else None, a.oyun_ust)
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
