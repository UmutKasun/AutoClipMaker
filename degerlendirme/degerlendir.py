"""Ağırlık duyarlılık karşılaştırması (v1 tasarım ölçütleri).

İki gerçek VOD'da farklı ağırlık setlerinin tasarım ölçütlerini geçip geçmediğine
bakar. Etiketler sohbet okunarak verilmiştir; kesin başarı için bkz. rapor.py.

Çalıştırma (proje kökünden):  python degerlendirme/degerlendir.py
Gerekli yerel veriler: chat.json, chat-emotes.json,
                       yayinlar/2931374918/chat.json, yayinlar/2931374918/chat-emotes.json
"""
import json
import sys
from pathlib import Path

K = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(K))
sys.stdout.reconfigure(encoding="utf-8")
import sohbet_klip as sk  # noqa: E402


def yukle(yol, emo):
    d = json.load(open(yol, encoding="utf-8")); ms, em, sure = sk.oku_twitch(d)
    em |= set(json.load(open(emo, encoding="utf-8"))["emotes"]); kalan, _ = sk.ele(ms)
    n = int(max(sure or 0, max(m["t"] for m in kalan)) // 15) + 1
    return kalan, n, sk.Adlandirici(em, sk.dil_tahmin(m["metin"] for m in kalan))


V = {"Se7en": yukle(K / "chat.json", K / "chat-emotes.json"),
     "SCHRADIN": yukle(K / "yayinlar/2931374918/chat.json",
                       K / "yayinlar/2931374918/chat-emotes.json")}


def sira(secilen, a, b):   # [a,b) aralığına düşen ilk anın sırası (1 tabanlı), yoksa None
    for r, s in enumerate(secilen, 1):
        if a <= s["t"] < b: return r


def olc(ad):
    kalan, n, adci = V[ad]; _, sec = sk.anlari_sec(kalan, n, 15, adci, 10)
    if ad == "Se7en":
        r = [sira(sec, 12000, 12060), sira(sec, 13800, 13860)]; k = [sira(sec, 13140, 13170), sira(sec, 13575, 13605)]
        kotu = [sira(sec, 14190, 14250), sira(sec, 5010, 5040)]
        ok = all(x and x <= 2 for x in r) and all(k) and not any(kotu)
        return ok, f"büyük GEÇTİ {r}, küçük {k}, spam {kotu}"
    r = [sira(sec, 2190, 2220), sira(sec, 5685, 5715), sira(sec, 7125, 7155)]
    spam = sira(sec, 430, 450); qa = [sira(sec, 720, 870), sira(sec, 840, 870), sira(sec, 2640, 2700)]
    mx = max(x or 99 for x in r)
    ok = all(x and x <= 5 for x in r) and not spam and all((q or 99) > mx for q in qa)
    return ok, f"olay {r}, spam(00:07:15) {spam}, soru-cevap {qa}"


setler = {
    "A başlangıç (30/20/30/20, ceza 0.6)": ({"hacim": .30, "kisi": .20, "ivme": .30, "tepki": .20}, .6, 2),
    "B hacim ağırlıklı (60/20/10/10)":     ({"hacim": .60, "kisi": .20, "ivme": .10, "tepki": .10}, .6, 2),
    "C ivme yok (40/30/0/30)":             ({"hacim": .40, "kisi": .30, "ivme": .00, "tepki": .30}, .6, 2),
    "D tepki yok (40/20/40/0)":            ({"hacim": .40, "kisi": .20, "ivme": .40, "tepki": .00}, .6, 2),
    "E eşit (25/25/25/25)":                ({"hacim": .25, "kisi": .25, "ivme": .25, "tepki": .25}, .6, 2),
    "F A, soru cezası yok":                ({"hacim": .30, "kisi": .20, "ivme": .30, "tepki": .20}, 1.0, 2),
    "G A, kişi sınırı yok":                ({"hacim": .30, "kisi": .20, "ivme": .30, "tepki": .20}, .6, 10**9),
}
for ad_, (w, c, kt) in setler.items():
    sk.AGIRLIK, sk.SORU_CEZA, sk.KISI_TAVAN = w, c, kt
    sonuc = [olc(v) for v in V]
    print(f"{'GEÇTİ' if all(o for o, _ in sonuc) else 'KALDI':5}  {ad_}")
    for v, (o, s) in zip(V, sonuc): print(f"        {v:8} {'✓' if o else '✗'} {s}")
