"""Kamera tespiti ve doygun sohbet kararı: yapay verilerle (video/model gerektirmez)."""

import unittest

import numpy as np

import kamera_tespit as kt
import uretim as u


def kareler(n=16, h=720, w=1280):
    return [(i, np.full((h, w, 3), 60, np.uint8)) for i in range(n)]


class Doygunluk(unittest.TestCase):

    def test_tavandaki_sohbet_doygun(self):                  # ohnePixel: 15 sn'de hep 80-190 mesaj
        duz = [100 + (i % 7) * 10 for i in range(2000)] + [190] * 20
        self.assertTrue(u.doygun_mu(duz))

    def test_sicramali_sohbet_doygun_degil(self):            # Eray/RRaenee: sakin taban + ani patlamalar
        sicrama = [30] * 2000 + [250] * 40
        self.assertFalse(u.doygun_mu(sicrama))
        self.assertGreater(u.doygunluk_orani(sicrama), u.KARMA_ORAN)


class Cercevesiz(unittest.TestCase):

    def test_hareket_eden_yayinci_tek_kisi_ve_kutu_ikisini_kapsar(self):
        k = kareler()
        kumeler = [(set(range(8)), (300.0, 480.0, 80.0, 100.0)),     # yarı karede bir konumda,
                   (set(range(8, 16)), (420.0, 490.0, 80.0, 100.0))]  # yarısında yana kaymış
        s = kt.cercevesiz(k, kumeler, {}, "kenar yok")
        self.assertEqual(s["karar"], "kamera")
        x1, y1, x2, y2 = s["kamera"]
        self.assertLessEqual(x1, 300)
        self.assertGreaterEqual(x2, 500)
        self.assertLessEqual(s["govde"][0], 300)
        self.assertGreaterEqual(s["govde"][2], 500)

    def test_tek_karelik_yanlis_tespit_kutuyu_kaydirmaz(self):
        k = kareler()
        ana = (set(range(15)), (400.0, 480.0, 80.0, 100.0))
        tek = kt.cercevesiz(k, [ana], {}, "")
        gurultu = kt.cercevesiz(k, [ana, ({3}, (250.0, 490.0, 70.0, 90.0))], {}, "")
        self.assertEqual(tek["kamera"], gurultu["kamera"])

    def test_orta_ustteki_yuz_yayinci_sayilmaz(self):          # izlenen video, oyun karakteri
        k = kareler()
        self.assertIsNone(kt.cercevesiz(k, [(set(range(16)), (600.0, 150.0, 90.0, 110.0))], {}, ""))


class OranYasla(unittest.TestCase):

    def test_sohbet_satiri_katilan_kutu_gercek_kenardan_kirpilir(self):
        k = kareler()
        for _, f in k:
            f[100:280, 0:320] = 200                            # kamera: 320x180 (16:9)
            f[280:292, 0:320] = 20                             # hemen altında koyu sohbet satırı
        y1, y2, not_ = kt.oran_yasla(k, 0, 100, 320, 292, (140, 150, 40, 50))
        self.assertEqual((y1, y2), (100, 280))
        self.assertIn("alttan", not_)

    def test_169_kutuya_dokunulmaz(self):
        y1, y2, not_ = kt.oran_yasla(kareler(), 0, 100, 320, 280, (140, 150, 40, 50))
        self.assertEqual((y1, y2, not_), (100, 280, None))


if __name__ == "__main__":
    unittest.main()
