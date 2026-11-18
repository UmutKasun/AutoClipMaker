#!/usr/bin/env python3
"""ses_ayristir.py - Klipten konuşmayı ayırıp arka plan müziğini kaldırır (yerel, ücretsiz).

Model: UVR MDX-Net "Kim_Vocal_2" (ONNX, ~65 MB), onnxruntime ile; torch gerekmez.
Parametreler UVR model_data.json'dan (hash 970b3f94...): n_fft 7680, hop 1024,
dim_f 3072, dim_t 256, compensate 1.009, çıktı = konuşma/vokal.

Karar (otomatik telif onayı DEĞİLDİR):
  * konuşma = model çıktısı, arka plan = orijinal - konuşma
  * kalite: altyazı kelimelerinin geçtiği yerlerde konuşma, orijinalin en az -6 dB'i
    olmalı; değilse ayrıştırma konuşmayı bozmuş sayılır -> ORİJİNAL SES korunur
  * arka plan çok zayıfsa (orijinalin -20 dB altı) kaldırılacak bir şey yok -> orijinal
  * aksi halde arka plan (müzik + oyun sesleri) kaldırılır; bir vokal modeli müziği
    oyun seslerinden ayıramaz, oyun sesleri de kısılır. Şarkı sözleri konuşmayla
    birlikte kalabilir. Sonuç her durumda elle telif incelemesine gider.

    python ses_ayristir.py klip.mp4 -o klip-temiz.mp4 [--kelimeler klip.kelimeler.json]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

MODEL = Path(__file__).resolve().parent / "modeller" / "Kim_Vocal_2.onnx"
SR = 44100
N_FFT, HOP, DIM_F, DIM_T, TELAFI = 7680, 1024, 3072, 256, 1.009
KONUSMA_KORUMA_DB = -6.0      # kelime anlarında konuşma/orijinal en az bu kadar
ARKA_ZAYIF_DB = -20.0         # arka plan/orijinal bunun altındaysa ayrıştırma gereksiz
OLU_SESSIZLIK = 3.0           # ayrıştırma bu kadar sn yeni sessizlik yaratırsa kalite bozuk sayılır


def ses_oku(video):
    ham = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(video), "-map", "0:a:0", "-f", "f32le",
                          "-ac", "2", "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(ham, np.float32).reshape(-1, 2).T.copy()


def _pencere():
    n = np.arange(N_FFT)
    return (0.5 - 0.5 * np.cos(2 * np.pi * n / N_FFT)).astype(np.float32)      # periyodik Hann


def stft(x, w):
    """torch.stft(center=True, reflect) eşdeğeri: [kanal, frekans, zaman] karmaşık."""
    p = N_FFT // 2
    x = np.pad(x, ((0, 0), (p, p)), mode="reflect")
    n = 1 + (x.shape[1] - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    return np.fft.rfft(x[:, idx] * w, axis=-1).transpose(0, 2, 1)


def istft(X, w, uzunluk):
    kare = np.fft.irfft(X.transpose(0, 2, 1), n=N_FFT, axis=-1) * w
    n = kare.shape[1]
    top = np.zeros((X.shape[0], N_FFT + HOP * (n - 1)), np.float32)
    norm = np.zeros(N_FFT + HOP * (n - 1), np.float32)
    for i in range(n):
        top[:, i * HOP:i * HOP + N_FFT] += kare[:, i]
        norm[i * HOP:i * HOP + N_FFT] += w * w
    p = N_FFT // 2
    return (top / np.maximum(norm, 1e-8))[:, p:p + uzunluk]


def konusmayi_ayir(mix):
    import onnxruntime as ort
    oturum = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])
    w = _pencere()
    parca = HOP * (DIM_T - 1)
    kirp = N_FFT // 2
    uret = parca - 2 * kirp
    n = mix.shape[1]
    pad = uret - n % uret
    x = np.concatenate([np.zeros((2, kirp), np.float32), mix, np.zeros((2, pad + kirp), np.float32)], 1)
    cikti = []
    for i in range(0, n + pad, uret):
        dalga = x[:, i:i + parca]
        S = stft(dalga, w)[:, :DIM_F]                                  # [2, F, T]
        girdi = np.stack([S[0].real, S[0].imag, S[1].real, S[1].imag])[None].astype(np.float32)
        y = oturum.run(None, {"input": girdi})[0][0]
        Y = np.zeros((2, N_FFT // 2 + 1, DIM_T), np.complex64)
        Y[0, :DIM_F] = y[0] + 1j * y[1]
        Y[1, :DIM_F] = y[2] + 1j * y[3]
        cikti.append(istft(Y, w, parca)[:, kirp:-kirp])
    return (np.concatenate(cikti, 1)[:, :n] * TELAFI).astype(np.float32)


def db(a, b):
    return 20 * np.log10((np.sqrt(np.mean(a * a)) + 1e-9) / (np.sqrt(np.mean(b * b)) + 1e-9))


def en_uzun_sessizlik(x, esik_db=-50.0, kare=0.25):
    """Mono ortalamada esik_db altında kalan en uzun kesintisiz süre, sn."""
    m = x.mean(0)
    n = int(SR * kare)
    k = len(m) // n
    if k == 0:
        return 0.0
    g = 10 * np.log10((m[:k * n].reshape(k, n) ** 2).mean(1) + 1e-12) < esik_db
    en = cur = 0
    for v in g:
        cur = cur + 1 if v else 0
        en = max(en, cur)
    return en * kare


def karar_ver(mix, konusma, kelimeler):
    arka = mix - konusma
    rapor = {"arka_plan_db": round(float(db(arka, mix)), 1)}
    olu = en_uzun_sessizlik(konusma) - en_uzun_sessizlik(mix)
    rapor["yeni_sessizlik_sn"] = round(olu, 2)
    oranlar = []
    for k in kelimeler:
        a, b = int(k["bas"] * SR), int(k["son"] * SR)
        if b - a >= int(0.08 * SR):
            oranlar.append(db(konusma[:, a:b], mix[:, a:b]))
    rapor["kelime"] = len(oranlar)
    if oranlar:
        rapor["konusma_koruma_db"] = round(float(np.median(oranlar)), 1)
    if not oranlar:
        rapor.update(karar="orijinal", neden="konuşma bulunamadı; ayrıştırma sesi boşaltırdı")
    elif rapor["konusma_koruma_db"] < KONUSMA_KORUMA_DB:
        rapor.update(karar="orijinal", neden=f"ayrıştırma konuşmayı bozuyor ({rapor['konusma_koruma_db']} dB)")
    elif olu >= OLU_SESSIZLIK:
        rapor.update(karar="orijinal", neden=f"ayrıştırma {olu:.1f} sn ölü sessizlik bırakıyor (oyun sesleri de "
                                              "gidiyor); orijinal ses korundu, telif incelemesi gerekli")
    elif rapor["arka_plan_db"] < ARKA_ZAYIF_DB:
        rapor.update(karar="orijinal", neden=f"arka plan sesi çok zayıf ({rapor['arka_plan_db']} dB); "
                                              "kaldırılacak müzik yok gibi")
    else:
        rapor.update(karar="ayristirildi", neden="arka plan (müzik + oyun sesi) kaldırıldı, konuşma korundu")
    return rapor


def yaz(video, ses, cikti):
    """Görüntü olduğu gibi kopyalanır, ses AAC 192k."""
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(video), "-f", "f32le", "-ac", "2",
                          "-ar", str(SR), "-i", "-", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                          "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(cikti)],
                         stdin=subprocess.PIPE)
    p.communicate(np.ascontiguousarray(np.clip(ses, -1, 1).T).astype(np.float32).tobytes())
    if p.returncode != 0:
        sys.exit(f"ffmpeg hata verdi ({p.returncode}).")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Konuşmayı koru, arka plan müziğini kaldır.")
    ap.add_argument("video")
    ap.add_argument("-o", "--cikti", required=True)
    ap.add_argument("--kelimeler", help="altyazi.py kelime JSON'u (konuşma koruma ölçümü için)")
    ap.add_argument("--rapor", help="karar raporu JSON yolu")
    ap.add_argument("--alternatif", help="orijinal ses korunursa yalnız-konuşma sürümünün yazılacağı yol")
    a = ap.parse_args()
    t0 = time.time()
    mix = ses_oku(a.video)
    konusma = konusmayi_ayir(mix)
    kel = json.loads(Path(a.kelimeler).read_text(encoding="utf-8"))["kelimeler"] if a.kelimeler else []
    r = karar_ver(mix, konusma, kel)
    r["sure_sn"] = round(time.time() - t0, 1)
    tepe = float(np.max(np.abs(konusma))) or 1.0
    if r["karar"] == "ayristirildi":
        yaz(a.video, konusma * min(1.0, 0.98 / tepe), a.cikti)
    else:
        if a.alternatif and r.get("kelime"):          # elle seçilebilsin: yalnız konuşma sürümü
            yaz(a.video, konusma * min(1.0, 0.98 / tepe), a.alternatif)
            r["alternatif"] = str(a.alternatif)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", a.video, "-map", "0", "-c", "copy",
                        "-movflags", "+faststart", a.cikti], check=True)
    print(json.dumps(r, ensure_ascii=False))
    if a.rapor:
        Path(a.rapor).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
