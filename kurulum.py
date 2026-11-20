#!/usr/bin/env python3
"""kurulum.py - Depoda olmayan büyük dosyaları resmi kaynaklarından indirir ve doğrular.

    python -m pip install -r requirements.txt
    python kurulum.py

İndirilenler (SHA-256 ile doğrulanır; zaten varsa ve doğruysa atlanır):
  modeller/face_detection_yunet_2023mar.onnx   OpenCV model deposu (yüz tespiti)
  modeller/Kim_Vocal_2.onnx                     UVR model deposu (isteğe bağlı müzik ayırma)
  araclar/TwitchDownloaderCLI/                  lay295/TwitchDownloader 1.56.5 (Twitch sohbet/video)
Sonra `uretim.py kontrol` ile eksik kalan (FFmpeg vb.) var mı bakılır.
"""

import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PROJE = Path(__file__).resolve().parent
INDIRILECEK = [
    ("modeller/face_detection_yunet_2023mar.onnx",
     "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
     "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    ("modeller/Kim_Vocal_2.onnx",
     "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx",
     "ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b"),
]
TDCLI_URL = ("https://github.com/lay295/TwitchDownloader/releases/download/1.56.5/"
             "TwitchDownloaderCLI-1.56.5-Windows-x64.zip")
TDCLI_SHA = "8b1b0695f2b1b6bf0d2535fab4b84032951cded8cf4078dfdf4d58e391c813a0"
TDCLI_KLASOR = PROJE / "araclar" / "TwitchDownloaderCLI"


def sha256(yol):
    h = hashlib.sha256()
    with open(yol, "rb") as f:
        for parca in iter(lambda: f.read(1 << 20), b""):
            h.update(parca)
    return h.hexdigest()


def indir(url, hedef, beklenen):
    """Geçici dosyaya indirir, özet tutarsa yerine koyar; tutmazsa siler ve durur."""
    hedef.parent.mkdir(parents=True, exist_ok=True)
    gecici = hedef.with_name(hedef.name + ".part")
    print(f"  indiriliyor: {url}")
    istek = urllib.request.Request(url, headers={"User-Agent": "kurulum.py"})
    with urllib.request.urlopen(istek, timeout=60) as r, open(gecici, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    ozet = sha256(gecici)
    if ozet != beklenen:
        gecici.unlink(missing_ok=True)
        sys.exit(f"  HATA: {hedef.name} doğrulanamadı (SHA-256 {ozet[:16]}…, beklenen {beklenen[:16]}…)")
    gecici.replace(hedef)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    for yol, url, ozet in INDIRILECEK:
        hedef = PROJE / yol
        if hedef.exists() and sha256(hedef) == ozet:
            print(f"tamam   {yol}")
            continue
        print(f"eksik   {yol}")
        indir(url, hedef, ozet)
        print(f"tamam   {yol}")

    exe = TDCLI_KLASOR / "TwitchDownloaderCLI.exe"
    if exe.exists():
        print(f"tamam   {exe.relative_to(PROJE)}")
    elif sys.platform != "win32":
        print("atlandı TwitchDownloaderCLI (burada yalnız Windows sürümü bağlı; Twitch için gerekli)")
    else:
        print("eksik   araclar/TwitchDownloaderCLI")
        zip_yolu = PROJE / "araclar" / "TwitchDownloaderCLI.zip"
        indir(TDCLI_URL, zip_yolu, TDCLI_SHA)
        with zipfile.ZipFile(zip_yolu) as z:
            z.extractall(TDCLI_KLASOR)
        zip_yolu.unlink()
        print(f"tamam   {exe.relative_to(PROJE)}")

    print("\nÖn kontrol:", flush=True)                # alt sürecin çıktısı araya karışmasın
    subprocess.run([sys.executable, str(PROJE / "uretim.py"), "kontrol"], cwd=PROJE)


if __name__ == "__main__":
    main()
