"""uretim.py birim testleri: ağ, TwitchDownloaderCLI ve ffmpeg gerektirmez."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dikey_kompozisyon as d
import uretim as u
from duzen import profil


def probe(sure=40.0, gen=1080, yuk=1920, fps="60/1", ses=True, video=True):
    s = []
    if video:
        s.append({"codec_type": "video", "width": gen, "height": yuk, "r_frame_rate": fps})
    if ses:
        s.append({"codec_type": "audio"})
    return {"streams": s, "format": {"duration": str(sure)}}


SES_IYI = {"ortalama_db": -20.0, "tepe_db": -1.0, "sessizlik": []}


class Kimlik(unittest.TestCase):

    def test_vod_id(self):
        for s in ("2931374918", "https://www.twitch.tv/videos/2931374918", "twitch.tv/videos/2931374918?t=1h",
                  " https://m.twitch.tv/videos/2931374918 "):
            self.assertEqual(u.vod_id(s), "2931374918")
        for s in ("https://twitch.tv/schradin", "abc", "123"):
            with self.assertRaises(SystemExit):
                u.vod_id(s)

    def test_parca_araligi(self):
        self.assertEqual(u.parca_araligi(5675, 40, 12776), (5670, 5720, 5))       # her iki yanda 5 sn
        self.assertEqual(u.parca_araligi(2.0, 40, 12776), (0, 47, 2.0))           # VOD başı
        self.assertEqual(u.parca_araligi(12740, 36, 12776), (12735, 12776, 5))    # VOD sonu kırpılır
        self.assertEqual(u.parca_araligi(100.5, 40, 0), (95, 146, 5.5))


class Dogrulama(unittest.TestCase):

    def test_saglam_klip(self):
        self.assertEqual(u.degerlendir(probe(), 40, boyut=(1080, 1920), ses=SES_IYI), ([], []))

    def test_ses_akisi_yoksa_hata(self):
        h, _ = u.degerlendir(probe(ses=False), 40, boyut=(1080, 1920))
        self.assertIn("ses akışı yok", h)

    def test_dusuk_ses_ve_sessizlik_yalniz_uyari(self):
        h, w = u.degerlendir(probe(), 40, boyut=(1080, 1920),
                             ses={"ortalama_db": -41.2, "tepe_db": -12.0, "sessizlik": [(3.0, 6.5), (37.0, None)]})
        self.assertEqual(h, [])
        self.assertIn("düşük ses seviyesi (ortalama -41.2 dB)", w)
        self.assertIn("sessizlik 3.0-6.5 sn", w)
        self.assertIn("sessizlik 37.0-son sn", w)
        h, w = u.degerlendir(probe(), 40, ses={"ortalama_db": float("-inf"), "tepe_db": float("-inf"),
                                              "sessizlik": []})
        self.assertEqual(h, [])
        self.assertTrue(any("tamamen sessiz" in x for x in w))

    def test_sure_goruntu_butunluk_hatalari(self):
        self.assertTrue(u.degerlendir(probe(sure=39.5), 40)[0])
        self.assertEqual(u.degerlendir(probe(sure=40.08), 40)[0], [])               # ±0.1 tolerans
        self.assertTrue(u.degerlendir(probe(gen=1920, yuk=1080), 40, boyut=(1080, 1920))[0])
        self.assertTrue(u.degerlendir(probe(fps="30/1"), 40, boyut=(1080, 1920))[0])
        self.assertIn("görüntü akışı yok", u.degerlendir(probe(video=False), 40)[0])
        h, _ = u.degerlendir(probe(), 40, cozum=["[h264] error while decoding MB 10 20"])
        self.assertTrue(any("dosya bütünlüğü" in x for x in h))
        self.assertIn("süre okunamadı", u.degerlendir({"streams": [], "format": {}}, 40)[0])

    def test_parca_en_az_sure(self):
        self.assertEqual(u.degerlendir(probe(sure=49.7, gen=1920, yuk=1080, fps="30/1"), en_az=50)[0], [])
        self.assertTrue(u.degerlendir(probe(sure=45, gen=1920, yuk=1080), en_az=50)[0])


class Adim(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.hedef = self.tmp / "k" / "a-dikey.mp4"
        self.klip = {"adimlar": {}}
        self.calisma = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def uret(self, icerik=b"yeni"):
        def f(g):
            self.calisma += 1
            g.write_bytes(icerik)
        return f

    @staticmethod
    def gecer(_):
        return [], ["düşük ses"]

    @staticmethod
    def kalir(_):
        return ["ses akışı yok"], []

    def test_uretir_sonra_atlar(self):
        self.assertEqual(u.adim(self.klip, "dikey", self.hedef, "i1", self.uret(), self.gecer), "üretildi")
        self.assertEqual(self.klip["adimlar"]["dikey"]["uyarilar"], ["düşük ses"])
        self.assertEqual(u.adim(self.klip, "dikey", self.hedef, "i1", self.uret(), self.gecer), "atlandı")
        self.assertEqual(self.calisma, 1)                                  # gereksiz render yok
        self.assertFalse(u.gecici_ad(self.hedef).exists())

    def test_parametre_degisince_yeniden_uretir(self):
        u.adim(self.klip, "dikey", self.hedef, "i1", self.uret(b"bir"), self.gecer)
        self.assertEqual(u.adim(self.klip, "dikey", self.hedef, "i2", self.uret(b"iki"), self.gecer), "üretildi")
        self.assertEqual(self.hedef.read_bytes(), b"iki")

    def test_dogrulanamayan_cikti_oncekini_bozmaz(self):
        u.adim(self.klip, "dikey", self.hedef, "i1", self.uret(b"saglam"), self.gecer)
        onceki = dict(self.klip["adimlar"]["dikey"])
        with self.assertRaises(u.AdimHatasi):
            u.adim(self.klip, "dikey", self.hedef, "i2", self.uret(b"bozuk"), self.kalir)
        self.assertEqual(self.hedef.read_bytes(), b"saglam")
        self.assertEqual(self.klip["adimlar"]["dikey"], onceki)
        self.assertFalse(u.gecici_ad(self.hedef).exists())

    def test_cokme_oncekini_bozmaz(self):
        u.adim(self.klip, "dikey", self.hedef, "i1", self.uret(b"saglam"), self.gecer)

        def coker(g):
            g.write_bytes(b"yari")
            raise u.AdimHatasi("ffmpeg: çıkış kodu 1")
        with self.assertRaises(u.AdimHatasi):
            u.adim(self.klip, "dikey", self.hedef, "i2", coker, self.gecer)
        self.assertEqual(self.hedef.read_bytes(), b"saglam")
        self.assertFalse(u.gecici_ad(self.hedef).exists())

    def test_kayitta_olmayan_dosyanin_uzerine_yazmaz(self):
        self.hedef.parent.mkdir(parents=True)
        self.hedef.write_bytes(b"elle uretilmis")
        with self.assertRaises(u.AdimHatasi) as c:
            u.adim(self.klip, "dikey", self.hedef, "i1", self.uret(), self.gecer)
        self.assertIn("üzerine yazılmadı", str(c.exception))
        self.assertEqual(self.hedef.read_bytes(), b"elle uretilmis")
        self.assertEqual(self.calisma, 0)

    def test_dosya_elle_degistiyse_yeniden_uretir(self):
        u.adim(self.klip, "dikey", self.hedef, "i1", self.uret(b"bir"), self.gecer)
        self.hedef.write_bytes(b"baska boyut")
        self.assertEqual(u.adim(self.klip, "dikey", self.hedef, "i1", self.uret(b"bir"), self.gecer), "üretildi")


class Yerlesim(unittest.TestCase):

    def test_tek_sahneli_profil(self):
        y = u.yerlesim_karari("schradin")
        self.assertEqual((y["tur"], y["args"], y["onay_gerekli"]), ("profil", ["--duzen", "schradin"], False))

    def test_gecis_bilgisi_yoksa_guvenli_tam_goruntu(self):
        y = u.yerlesim_karari("se7en")
        self.assertEqual((y["tur"], y["args"], y["onay_gerekli"]), ("guvenli", ["--guvenli"], False))
        self.assertIn("geçiş bilgisi", y["neden"])                     # tercih raporlanır
        y = u.yerlesim_karari("se7en", "26.05-40")
        self.assertEqual(y["args"], ["--duzen", "se7en", "--oyun", "26.05-40"])

    def test_kullanici_klip_icin_guvenli_secebilir(self):
        for kanal in ("schradin", "se7en", "boyle_bir_kanal_yok"):
            y = u.yerlesim_karari(kanal, "26.05-40", "guvenli")
            self.assertEqual((y["tur"], y["args"], y["onay_gerekli"]), ("guvenli", ["--guvenli"], False))
        self.assertTrue(any("tam kamera" in x for x in u.riskler({"baslik": ""}, {}, {"tur": "profil"})))

    def test_bilinmeyen_profil_onay_ister(self):
        y = u.yerlesim_karari("boyle_bir_kanal_yok")
        self.assertEqual((y["tur"], y["onay_gerekli"]), ("guvenli", True))
        self.assertTrue(u.yerlesim_karari("")["onay_gerekli"])

    def test_bozuk_profil_sessizce_guvenliye_dusmez(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "bozuk.json").write_text("{", encoding="utf-8")
            with mock.patch.object(profil, "PROFIL_KLASORU", Path(t)):
                with self.assertRaises(profil.ProfilHatasi):
                    u.yerlesim_karari("bozuk")

    def test_guvenli_filtre_kirpmaz(self):
        f = d.guvenli_filtre(1920, 1080)
        self.assertIn("crop=1920:1080:0:0", f)                          # tüm kare
        self.assertIn("scale=1080:606", f)                              # oran korunur (607.5 -> çift)
        self.assertTrue(f.endswith("fps=60,format=yuv420p,setsar=1[v]"))
        self.assertIn("crop=1280:720:0:0", d.guvenli_filtre(1280, 720))


class Inceleme(unittest.TestCase):

    def klip(self, telif="bekliyor", kalite="bekliyor", durum="üretildi"):
        return {"durum": durum, "inceleme": {"telif": telif, "kalite": kalite}}

    def test_durumlar(self):
        self.assertEqual(u.klip_durumu(None), "üretilmedi")
        self.assertEqual(u.klip_durumu(self.klip(durum="hata")), "hata")
        self.assertEqual(u.klip_durumu(self.klip()), "inceleme bekliyor")
        self.assertEqual(u.klip_durumu(self.klip("temiz")), "inceleme bekliyor")   # kalite de gerekli
        self.assertEqual(u.klip_durumu(self.klip("temiz", "onay")), "elle onaylandı")
        self.assertEqual(u.klip_durumu(self.klip("sorunlu", "onay")), "reddedildi")
        self.assertEqual(u.klip_durumu(self.klip("temiz", "red")), "reddedildi")

    def test_otomatik_guvenli_sayilmaz(self):
        # hiçbir durum adı "güvenli"/"telifsiz" değil; riskler her zaman kayıtta
        self.assertNotIn("güvenli", u.klip_durumu(self.klip("temiz", "onay")))
        r = u.riskler({"baslik": "REACTION HUGO, GTA V"}, {}, {"tur": "guvenli"})
        self.assertTrue(any("reaction" in x for x in r))
        self.assertTrue(any("müzik" in x for x in r))
        self.assertTrue(any("müzik" in x for x in u.riskler({"baslik": "GTA"}, {}, {"tur": "profil"})))
        self.assertIn("ortadan kalkmış sayılmaz", u.TELIF_NOTU)


class Kayit(unittest.TestCase):

    def test_atomik_yazma_ve_okuma(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(u, "PROJE", Path(t)):
            k = {"vod": "123456", "adaylar": [], "secim": [], "klipler": {}, "not": "ğüşİ"}
            u.kayit_yaz(k)
            yol = u.kayit_yolu("123456")
            self.assertEqual(json.loads(yol.read_text(encoding="utf-8")), k)
            self.assertFalse(yol.with_name(yol.name + ".part").exists())
            self.assertEqual(u.kayit_oku("123456"), k)
            yol.write_text("{bozuk", encoding="utf-8")
            with self.assertRaises(SystemExit):
                u.kayit_oku("123456")

    def test_aday_bul(self):
        k = {"vod": "1", "adaylar": [{"sira": 1, "kok": "013500-A"}, {"sira": 12, "kok": "020000-B"}]}
        self.assertEqual(u.aday_bul(k, "01")["kok"], "013500-A")
        self.assertEqual(u.aday_bul(k, "12")["kok"], "020000-B")
        self.assertEqual(u.aday_bul(k, "020000-B")["sira"], 12)
        with self.assertRaises(SystemExit):
            u.aday_bul(k, "3")


if __name__ == "__main__":
    unittest.main()
