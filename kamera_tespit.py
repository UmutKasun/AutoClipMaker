#!/usr/bin/env python3
"""kamera_tespit.py - Yayın karelerinden yayıncı kamerasını (yüz + kamera çerçevesi) bulur.

Yalnız dikey_kompozisyon.py'ye bölge vermek için küçük bir analiz:
  1. Klip boyunca eşit aralıklı N kare (OpenCV).
  2. YuNet (OpenCV) ile yüzler; aynı yerde kalan yüz kümesi = yayıncı.
     Oyun içi yüzler yer değiştirir, kümeye giremez.
  3. Kamera çerçevesi: yüzün çevresinde kareler boyunca kalıcı ve yüzün tüm
     yüksekliği/genişliği boyunca süren düz kenarlar (ya da görüntü kenarı).
  4. Karar: 'kamera' (bölgeler hazır) | 'tam_kamera' | 'yok' | 'degisken' -> son üçü
     güvenli tam görüntüye düşer; neden kayda yazılır.

    python kamera_tespit.py parca.mp4 [--bas 5 --sure 40] [--gorsel kontrol.jpg]
"""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

MODEL = Path(__file__).resolve().parent / "modeller" / "face_detection_yunet_2023mar.onnx"
KARE = 16                      # örneklenen kare
YUZ_ESIK = 0.75                # YuNet güveni
KUME_ORAN = 0.6                # yüz karelerin en az bu oranında aynı yerde olmalı
TAM_KAMERA_YUZ = 0.30          # yüz yüksekliği / kare yüksekliği bunu aşarsa tam kamera sahnesi
KENAR_DOLULUK = 0.75           # kamera kenarı, yüz boyunca bu oranda kalıcı olmalı
KENAR_MIN = 8.0                # o oranda ortalama |gradyan| en az bu kadar
KENAR_TEPE = 3.0               # kenar, iki yanından en az birinin bu katı keskin olmalı
DURAGAN = 6.0                  # zamansal std bunun altındaysa bölge durağan (kamera arka planı)
OYUN_ORAN = 1.2                # alt bölüme giden oyun kırpımının en/boy oranı (varsayılan)
UST_MIN, UST_MAX = 0.26, 0.40  # kamera bölümünün dikey videodaki payı
BANT = 150                     # oyunun üstünde/altında altyazı şeridi, px


def kareleri_oku(video, bas=0.0, sure=None, n=KARE):
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"video açılamadı: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    toplam = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    son = min(toplam, bas + sure) if sure else toplam
    kareler = []
    for i in range(n):
        t = bas + (son - bas) * (i + 0.5) / n
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, kare = cap.read()
        if ok:
            kareler.append((t, kare))
    cap.release()
    if not kareler:
        raise RuntimeError("kare okunamadı")
    return kareler


def yuzler(kareler):
    h, w = kareler[0][1].shape[:2]
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), YUZ_ESIK, 0.3, 50)
    sonuc = []
    for i, (_, k) in enumerate(kareler):
        _, bulunan = det.detect(k)
        for f in (bulunan if bulunan is not None else []):
            x, y, fw, fh = (float(v) for v in f[:4])
            if fw >= 24 and fh >= 24:
                sonuc.append((i, x, y, fw, fh, float(f[-1])))
    return sonuc


def kumele(tespitler):
    """Yer olarak aynı kalan yüz kümeleri: [(kare kümesi, medyan kutu)] büyükten küçüğe."""
    kumeler = []
    for d in sorted(tespitler, key=lambda d: -d[5]):
        cx, cy = d[1] + d[3] / 2, d[2] + d[4] / 2
        for k in kumeler:
            # kamera içinde kafa hareketi: ~1.5 yüz genişliği aynı kişi sayılır
            if abs(cx - k["cx"]) < 1.5 * k["w"] and abs(cy - k["cy"]) < 1.5 * k["w"]:
                k["d"].append(d)
                break
        else:
            kumeler.append({"cx": cx, "cy": cy, "w": d[3], "d": [d]})
    sonuc = []
    for k in kumeler:
        kare = {d[0] for d in k["d"]}
        kutu = tuple(float(np.median([d[j] for d in k["d"]])) for j in (1, 2, 3, 4))
        sonuc.append((kare, kutu))
    return sorted(sonuc, key=lambda s: -len(s[0]))


def kenar_haritalari(kareler):
    """Kareler boyunca ortalama |dI/dx| ve |dI/dy|: kalıcı kenarlar parlak kalır."""
    ex = ey = top = kare = None
    for _, k in kareler:
        g = cv2.cvtColor(k, cv2.COLOR_BGR2GRAY).astype(np.float32)
        dx = np.abs(np.diff(g, axis=1))
        dy = np.abs(np.diff(g, axis=0))
        ex = dx if ex is None else ex + dx
        ey = dy if ey is None else ey + dy
        top = g if top is None else top + g
        kare = g * g if kare is None else kare + g * g
    n = len(kareler)
    degisim = np.sqrt(np.maximum(kare / n - (top / n) ** 2, 0))     # piksel başına zamansal std
    return ex / n, ey / n, degisim


def kamera_kutusu(kareler, yuz):
    """Yüz kutusundan kamera çerçevesi: her yönde yüze en yakın, yüz boyunca süren kalıcı kenar.
    Bulunamayan yön görüntü kenarına dayanır (köşe kameraları)."""
    h, w = kareler[0][1].shape[:2]
    fx, fy, fw, fh = yuz
    ex, ey, degisim = kenar_haritalari(kareler)
    esik = KENAR_MIN
    satir = (int(max(0, fy)), int(min(h - 1, fy + fh)))
    sutun = (int(max(0, fx)), int(min(w - 1, fx + fw)))

    def tara(harita, dg, aralik, adaylar, yon):
        # Overlay kenarı sabit olduğundan ortalamada tek piksellik keskin bir tepe kalır;
        # saç/omuz kenarları kareler boyunca yayılır (komşu satırlar da güçlü).
        # Kamera arka planındaki sabit çizgiler (raf, duvar) da keskin kalır; onların iki yanı
        # da durağandır. Gerçek sınırın en az bir yanı (oyun ya da yayıncının kendisi) değişir.
        bolum = harita[aralik[0]:aralik[1]]
        med = np.median(bolum, axis=0)
        q = np.percentile(bolum, 100 * (1 - KENAR_DOLULUK), axis=0)
        akt = np.median(dg[aralik[0]:aralik[1]], axis=0)
        n = harita.shape[1]
        for c in adaylar:
            if not (3 <= c < n - 3):
                continue
            yan_a, yan_b = max(med[c - 2], med[c - 3]), max(med[c + 2], med[c + 3])
            if q[c] >= KENAR_MIN and med[c] >= KENAR_TEPE * min(yan_a, yan_b) + 1 \
                    and med[c] >= 1.3 * max(med[c - 1], med[c + 1]):
                a_ = float(np.median(akt[max(0, c - 13):c - 2])) if c > 3 else 0.0
                b_ = float(np.median(akt[c + 4:c + 15])) if c + 4 < n else 0.0
                if max(a_, b_) >= DURAGAN:
                    return c + 1
        return None

    satir_dg = degisim[:, :-1]
    sutun_dg = degisim[:-1, :].T
    sinir = {"sol": tara(ex, satir_dg, satir, range(int(fx - 0.15 * fw), int(max(-1, fx - 5 * fw)), -1), -1),
             "sag": tara(ex, satir_dg, satir, range(int(fx + 1.15 * fw), int(min(w - 1, fx + 6 * fw))), 1),
             "ust": tara(ey.T, sutun_dg, sutun, range(int(fy - 0.15 * fh), int(max(-1, fy - 3 * fh)), -1), -1),
             "alt": tara(ey.T, sutun_dg, sutun, range(int(fy + 1.15 * fh), int(min(h - 1, fy + 4 * fh))), 1)}
    x1 = sinir["sol"] if sinir["sol"] is not None else 0
    x2 = sinir["sag"] if sinir["sag"] is not None else w
    y1 = sinir["ust"] if sinir["ust"] is not None else 0
    y2 = sinir["alt"] if sinir["alt"] is not None else h
    return (x1, y1, x2, y2), sinir, esik, degisim


def tespit(video, bas=0.0, sure=None, n=KARE):
    kareler = kareleri_oku(video, bas, sure, n)
    h, w = kareler[0][1].shape[:2]
    t = yuzler(kareler)
    kumeler = kumele(t)
    rapor = {"kare": len(kareler), "cozunurluk": [w, h], "yuz_tespiti": len(t),
             "kumeler": [{"kare": len(k), "kutu": [round(v) for v in b]} for k, b in kumeler[:4]]}
    if not kumeler or len(kumeler[0][0]) < KUME_ORAN * len(kareler):
        en = len(kumeler[0][0]) if kumeler else 0
        return {**rapor, "karar": "yok",
                "neden": f"yayıncı yüzü karelerin yalnız {en}/{len(kareler)}'inde aynı yerde bulundu"}
    kare_kumesi, yuz = kumeler[0]
    for k2, _ in kumeler[1:]:
        if len(k2) >= 0.25 * len(kareler) and len(k2 - kare_kumesi) >= 0.2 * len(kareler):
            return {**rapor, "karar": "degisken",
                    "neden": "klip içinde yüz iki farklı konumda: kamera düzeni değişiyor olabilir"}
    rapor["yuz"] = [round(v) for v in yuz]
    if yuz[3] / h > TAM_KAMERA_YUZ:
        return {**rapor, "karar": "tam_kamera",
                "neden": f"yüz kare yüksekliğinin %{100 * yuz[3] / h:.0f}'i: tam kamera sahnesi"}
    (x1, y1, x2, y2), sinir, esik, degisim = kamera_kutusu(kareler, yuz)
    # oyun kırpımı için sütun etkinliği: kamera dışında kareler arası değişim (menü, olay)
    akt = degisim.copy()
    akt[max(0, y1):y2, max(0, x1):x2] = 0
    rapor["_sutun_aktivite"] = akt.mean(axis=0)
    kw, kh = x2 - x1, y2 - y1
    bulunan = sum(v is not None for v in sinir.values())
    rapor.update(kamera=[x1, y1, x2, y2], sinirlar=sinir, kenar_esik=round(esik, 1))
    oran = kw / max(kh, 1)
    if kw * kh > 0.45 * w * h or not (0.8 <= oran <= 2.6) or kw < 1.8 * yuz[2] or kh < 1.4 * yuz[3] \
            or bulunan < 2:
        return {**rapor, "karar": "yok",
                "neden": f"kamera çerçevesi güvenilir bulunamadı ({kw}x{kh}, {bulunan}/4 kenar)"}
    return {**rapor, "karar": "kamera", "neden": f"yüz {len(kare_kumesi)}/{len(kareler)} karede; "
                                                   f"çerçeve {kw}x{kh} ({bulunan}/4 kenar bulundu)"}


def oyun_bolgesi(w, h, kamera, oran=OYUN_ORAN, aktivite=None):
    """Alt bölüm için tam yükseklikte oyun kırpımı. Varsayılan: merkez (kamerayla çakışıyorsa
    kameradan en fazla %12 uzağa). Klipte kareler arası değişim (menü, olay) belirgin biçimde
    başka yerdeyse (merkezden %25 fazla) kırpım oraya kayar; kalan kamera çakışması maskelenir."""
    gw = min(w, int(h * oran) // 2 * 2)
    x = (w - gw) // 2
    kx1, _, kx2, _ = kamera
    kayma = int(0.12 * w)
    if kx2 > x and kx1 < x + gw:
        if (kx1 + kx2) / 2 < w / 2:
            x = min(x + kayma, kx2)
        else:
            x = max(x - kayma, kx1 - gw)
    x = max(0, min(w - gw, x))
    if aktivite is not None and gw < w:
        top = np.concatenate([[0.0], np.cumsum(aktivite)])
        puan = top[gw:] - top[:-gw]                     # puan[i]: [i, i+gw) penceresi
        en = int(np.argmax(puan))
        if puan[en] > 1.25 * puan[x]:
            x = en // 2 * 2
    return (x, 0, x + gw, h)


def kesisim(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


def dikey_duzen(s, gen=1080, yuk=1920):
    """1080x1920 için: üstte kamera (tam genişlik, oranı korunur), altta oyun; oyunun
    üstünde ve altında BANT px'lik bulanık şerit. Altyazı üst şeride gider: ne yüzü ne
    oyunu kapatır."""
    w, h = s["cozunurluk"]
    x1, y1, x2, y2 = s["kamera"]
    ic = 2                                              # çerçeve çizgisini dışarıda bırak
    kx1, ky1 = x1 + (ic if x1 > 0 else 0), y1 + (ic if y1 > 0 else 0)
    kx2, ky2 = x2 - (ic if x2 < w else 0), y2 - (ic if y2 < h else 0)
    ust = int(min(max(gen * (ky2 - ky1) / (kx2 - kx1), UST_MIN * yuk), UST_MAX * yuk)) // 2 * 2
    alt = yuk - ust
    oran = min(max(gen / (alt - 2 * BANT), 1.0), 1.45)
    oyun = oyun_bolgesi(w, h, s["kamera"], oran, s.get("_sutun_aktivite"))
    maske = kesisim(oyun, s["kamera"])
    bant = (alt - gen * h / (oyun[2] - oyun[0])) / 2
    return {"kamera_kirp": [kx1, ky1, kx2, ky2], "oyun": list(oyun), "maske": list(maske) if maske else None,
            "ust_oran": round(ust / yuk, 4), "altyazi_y": int(ust + bant / 2) if bant >= 110 else ust}


def bolgeler(video, bas=0.0, sure=None):
    """tespit + dikey yerleşim (kamera kırpımı, oyun kırpımı, maske, altyazı konumu)."""
    s = tespit(video, bas, sure)
    if s["karar"] == "kamera":
        s.update(dikey_duzen(s))
    s.pop("_sutun_aktivite", None)
    return s


def gorsel(video, sonuc, hedef, bas=0.0, sure=None):
    k = kareleri_oku(video, bas, sure, 1)[0][1].copy()
    if sonuc.get("yuz"):
        x, y, fw, fh = sonuc["yuz"]
        cv2.rectangle(k, (x, y), (x + fw, y + fh), (0, 255, 255), 3)
    for ad, renk in (("kamera", (0, 255, 0)), ("oyun", (255, 0, 255)), ("maske", (0, 0, 255))):
        if sonuc.get(ad):
            cv2.rectangle(k, tuple(sonuc[ad][:2]), tuple(sonuc[ad][2:]), renk, 4)
    cv2.imwrite(str(hedef), cv2.resize(k, (960, int(960 * k.shape[0] / k.shape[1]))))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Yayıncı kamerasını otomatik bul.")
    ap.add_argument("video")
    ap.add_argument("--bas", type=float, default=0)
    ap.add_argument("--sure", type=float)
    ap.add_argument("--gorsel", help="tespit kutularını çizen kontrol görüntüsü (.jpg)")
    a = ap.parse_args()
    s = bolgeler(a.video, a.bas, a.sure)
    print(json.dumps(s, ensure_ascii=False))
    if a.gorsel:
        gorsel(a.video, s, a.gorsel, a.bas, a.sure)


if __name__ == "__main__":
    main()
