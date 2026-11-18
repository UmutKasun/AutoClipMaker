"""Klip adayı değerlendirme raporu: Precision@k, başarılı / başarısız / belirsiz.

Yalnız video incelemesiyle doğrulanmış etiketler (dogrulama = "video") kesin sayılır.
Sohbetten çıkarılmış ön etiketler ve etiketsiz adaylar BELİRSİZ raporlanır; bu yüzden
Precision@k iki sınırla verilir:
    alt sınır = başarılı / k              (belirsizlerin hepsi başarısız olsaydı)
    üst sınır = (k - başarısız) / k       (belirsizlerin hepsi başarılı olsaydı)

Çalıştırma (proje kökünden):  python degerlendirme/rapor.py
Çıktı: degerlendirme/RAPOR.md (ve terminal özeti). Algoritmaya dokunmaz.
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

K = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(K))
import sohbet_klip as sk  # noqa: E402

KLER = (3, 5)
LISTE = 10


def sn(s):
    h, m, x = (int(v) for v in s.split(":"))
    return h * 3600 + m * 60 + x


def surum():
    try:
        return subprocess.run(["git", "-C", str(K), "describe", "--tags", "--always", "--dirty"],
                              capture_output=True, text=True).stdout.strip() or "?"
    except OSError:
        return "?"


def adaylar(y):
    veri = json.loads((K / y["sohbet"]).read_text(encoding="utf-8"))
    ms, em, sure = sk.oku_twitch(veri)
    try:
        em |= set(json.loads((K / y["emoteler"]).read_text(encoding="utf-8"))["emotes"])
    except (OSError, KeyError, ValueError):
        pass
    kalan, _ = sk.ele(ms)
    n = int(max(sure or 0, max(m["t"] for m in kalan)) // 15) + 1
    adci = sk.Adlandirici(em, sk.dil_tahmin(m["metin"] for m in kalan))
    _, secilen = sk.anlari_sec(kalan, n, 15, adci, LISTE)
    for s in secilen:
        s["ad"] = adci.ad(s["metinler"]) or "-"
    return secilen


def siniflandir(t, etiketler):
    """(durum, not) - durum: başarılı | başarısız | belirsiz"""
    for e in etiketler:
        if sn(e["bas"]) <= t < sn(e["son"]):
            if e["dogrulama"] == "video":
                return ("başarılı" if e["durum"] == "basarili" else "başarısız"), e["not"]
            on = "olası başarılı" if e["durum"] == "basarili" else "olası başarısız"
            return "belirsiz", f"sohbete göre {on}: {e['not']}"
    return "belirsiz", "etiket yok; video incelenmedi"


def pk(durumlar, k):
    ilk = durumlar[:k]
    b, f = ilk.count("başarılı"), ilk.count("başarısız")
    return b, f, k - b - f


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    tanim = json.loads((Path(__file__).parent / "etiketler.json").read_text(encoding="utf-8"))
    md = ["# Klip adayı değerlendirme raporu", "",
          f"Tarih: {date.today().isoformat()} · Algoritma: `{surum()}` · "
          f"Ağırlıklar: {sk.AGIRLIK}, soru-cevap x{sk.SORU_CEZA}, kişi başı ≤{sk.KISI_TAVAN}", "",
          "Yalnız **video incelemesiyle doğrulanmış** etiketler kesin sayılır. Sohbetten çıkarılan "
          "ön etiketler ve etiketsiz adaylar **belirsiz**dir. Precision@k alt sınırı belirsizleri "
          "başarısız, üst sınırı başarılı varsayar.", ""]
    toplam = {k: [0, 0, 0] for k in KLER}
    ozet = ["| Yayın | " + " | ".join(f"P@{k} (alt–üst)" for k in KLER) + " | Kesinleşen ilk 5 |",
            "|---|" + "---|" * len(KLER) + "---|"]
    bolumler = []
    for y in tanim["yayinlar"]:
        try:
            sec = adaylar(y)
        except OSError as e:
            bolumler += [f"## {y['yayinci']} ({y['id']})", "", f"Sohbet dosyası okunamadı: {e}", ""]
            continue
        sonuc = [(s, *siniflandir(s["t"], y["etiketler"])) for s in sec]
        durumlar = [d for _, d, _ in sonuc]
        hucre = []
        for k in KLER:
            b, f, u = pk(durumlar, k)
            for j, v in enumerate((b, f, u)):
                toplam[k][j] += v
            hucre.append(f"{b / k:.2f}–{(k - f) / k:.2f}")
        b5, f5, _ = pk(durumlar, 5)
        ozet.append(f"| {y['yayinci']} ({y['id']}) | " + " | ".join(hucre) + f" | {b5 + f5}/5 |")

        araliklar = ", ".join(f"{a}–{b}" for a, b in y.get("incelenen_video", [])) or "yok"
        bolumler += [f"## {y['yayinci']} ({y['id']})", "", f"İncelenen video aralıkları: {araliklar}", ""]
        for baslik, d in (("Başarılı", "başarılı"), ("Başarısız", "başarısız"), ("Belirsiz", "belirsiz")):
            satir = [(r, s, n) for r, (s, dd, n) in enumerate(sonuc, 1) if dd == d]
            bolumler += [f"### {baslik} ({len(satir)})", ""]
            if not satir:
                bolumler += ["—", ""]
                continue
            bolumler += ["| Sıra | Zaman | Mesaj / kişi | Puan | Ad | Not |", "|---|---|---|---|---|---|"]
            bolumler += [f"| {r} | {sk.sure_yaz(s['t'])} | {s['ham']} / {s['kisi']} | "
                         f"{s['puan']:.1f} | {s['ad']} | {n} |" for r, s, n in satir]
            bolumler.append("")
        kacan = [e for e in y["etiketler"] if e["dogrulama"] == "video" and e["durum"] == "basarili"
                 and not any(sn(e["bas"]) <= s["t"] < sn(e["son"]) for s in sec)]
        if kacan:
            bolumler += [f"**İlk {LISTE}'da olmayan doğrulanmış başarılar:** " +
                         "; ".join(f"{e['bas']}–{e['son']} ({e['not']})" for e in kacan), ""]

    ozet.append("| **Toplam** | " + " | ".join(
        f"**{b / (b + f + u):.2f}–{(b + u) / (b + f + u):.2f}**" if b + f + u else "-"
        for b, f, u in (toplam[k] for k in KLER)) + " | |")
    md += ["## Özet", ""] + ozet + [""] + bolumler + [
        "## Yeni etiket ekleme", "",
        "`etiketler.json` içine aralık ekleyin. Adayın videosunu izleyip doğruladıysanız "
        "`\"dogrulama\": \"video\"`, yalnız sohbeti okuduysanız `\"sohbet\"` yazın.", ""]
    (Path(__file__).parent / "RAPOR.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(ozet))
    print(f"\nRapor: {Path(__file__).parent / 'RAPOR.md'}")


if __name__ == "__main__":
    main()
