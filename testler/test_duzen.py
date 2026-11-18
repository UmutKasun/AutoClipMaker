import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import dikey_kompozisyon as d
from duzen import profil as p

# Profillerden önce kullanılan, klipler üretilirken doğrulanmış 1080p koordinatlar
SE7EN_1080 = {"kamera": "968,762,1452,1040", "pencere": "1475,0,1900,740", "oyun": "636,0,1284,760"}
SCHRADIN_1080 = {"kamera": "0,0,540,302", "pencere": "540,0,1460,1080"}


def arg(**kw):
    v = dict(duzen=None, sahne=None, kamera=None, pencere=None, oyun_bolge=None, ust_oran=None, oyun="")
    v.update(kw)
    return SimpleNamespace(**v)


def coz_yok():
    raise AssertionError("--duzen yokken çözünürlük sorulmamalı")


class Donusum(unittest.TestCase):

    def test_1080p_eski_koordinatlarla_birebir(self):
        for ad, eski in (("se7en", SE7EN_1080), ("schradin", SCHRADIN_1080)):
            y = p.yerlesim(p.yukle(ad), None, 1920, 1080)
            for bolge, deger in eski.items():
                self.assertEqual(y[bolge], d.bolge(deger), f"{ad}.{bolge}")
            self.assertEqual(y["ust_oran"], 0.34)
        self.assertNotIn("oyun", p.yerlesim(p.yukle("schradin"), None, 1920, 1080))

    def test_cozunurlukler(self):
        for gen, yuk in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160), (1366, 768), (854, 480)):
            o = gen / 1920
            for ad, eski in (("se7en", SE7EN_1080), ("schradin", SCHRADIN_1080)):
                y = p.yerlesim(p.yukle(ad), None, gen, yuk)
                for bolge, deger in eski.items():
                    x, yy, w, h = y[bolge]
                    self.assertTrue(w % 2 == 0 and h % 2 == 0, (gen, ad, bolge))
                    self.assertTrue(x >= 0 and yy >= 0 and x + w <= gen and yy + h <= yuk, (gen, ad, bolge))
                    x1, y1, x2, y2 = (int(v) for v in deger.split(","))
                    for gercek, beklenen in zip((x, yy, x + w, yy + h),
                                                (x1 * o, y1 * yuk / 1080, x2 * o, y2 * yuk / 1080)):
                        self.assertLessEqual(abs(gercek - beklenen), 2.0, (gen, yuk, ad, bolge))

    def test_720p_ve_4k_tam_degerler(self):
        y = p.yerlesim(p.yukle("se7en"), None, 1280, 720)
        self.assertEqual(y["kamera"], (645, 508, 324, 186))      # 968*2/3=645.3, 762*2/3=508, w 323->324
        self.assertEqual(y["pencere"], (983, 0, 284, 494))        # 1475*2/3=983.3, 1900*2/3=1266.7->1267
        y = p.yerlesim(p.yukle("se7en"), None, 3840, 2160)
        self.assertEqual(y["kamera"], (1936, 1524, 968, 556))     # tam iki kat
        self.assertEqual(y["oyun"], (1272, 0, 1296, 1520))
        y = p.yerlesim(p.yukle("schradin"), None, 1280, 720)
        self.assertEqual(y["kamera"], (0, 0, 360, 202))           # 302*2/3=201.3 -> 201 -> 202
        self.assertEqual(y["pencere"], (360, 0, 614, 720))

    def test_kenarda_tek_genislik_kareden_tasmaz(self):
        # 1475..1920 = 445 px: çifte yukarı yuvarlama 1921'e taşardı, aşağı yuvarlanır
        self.assertEqual(p.piksele([1475 / 1920, 0, 1.0, 1.0], 1920, 1080), (1475, 0, 444, 1080))
        self.assertEqual(p.piksele([0, 1 / 1080, 1, 1.0], 1920, 1080), (0, 1, 1920, 1078))

    def test_yarimda_yukari_yuvarlar(self):
        self.assertEqual(p._yuvarla(0.5), 1)
        self.assertEqual(p._yuvarla(2.5), 3)                      # round(2.5) == 2 olurdu


class Hatalar(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.temel = json.loads((p.PROFIL_KLASORU / "se7en.json").read_text(encoding="utf-8"))

    def tearDown(self):
        self.tmp.cleanup()

    def yaz(self, icerik, ad="x.json"):
        yol = self.dir / ad
        yol.write_text(icerik if isinstance(icerik, str) else json.dumps(icerik), encoding="utf-8")
        return str(yol)

    def bozuk(self, degistir):
        v = copy.deepcopy(self.temel)
        degistir(v)
        return self.yaz(v)

    def hata(self, yol, parca, gen=1920, yuk=1080, sahne=None):
        with self.assertRaises(p.ProfilHatasi) as c:
            p.yerlesim(p.yukle(yol), sahne, gen, yuk)
        self.assertIn(parca, str(c.exception))

    def bolge(self, v, ad="kamera"):
        return v["sahneler"]["oyun"]["bolgeler"][ad]

    def test_eksik_dosya(self):
        self.hata(str(self.dir / "yok.json"), "profil bulunamadı")
        self.hata("boyle_bir_yayinci_yok", "mevcut profiller: schradin, se7en")

    def test_bozuk_json(self):
        self.hata(self.yaz('{"surum": 1,'), "geçersiz JSON, satır 1")
        self.hata(self.yaz("[]"), "nesne")

    def test_sema(self):
        self.hata(self.bozuk(lambda v: v.pop("sahneler")), "eksik alan(lar): sahneler")
        self.hata(self.bozuk(lambda v: v.update(surum=2)), "desteklenmeyen surum")
        self.hata(self.bozuk(lambda v: v.update(varsayilan_sahne="reaction")), "sahneler içinde yok")
        self.hata(self.bozuk(lambda v: v["sahneler"]["oyun"]["bolgeler"].pop("pencere")),
                  "eksik alan(lar): pencere")
        self.hata(self.bozuk(lambda v: v["sahneler"]["oyun"]["bolgeler"].update(kamere={})),
                  "bilinmeyen alan(lar): kamere")
        self.hata(self.bozuk(lambda v: self.bolge(v).pop("dogrulandi")), "eksik alan(lar): dogrulandi")
        self.hata(self.bozuk(lambda v: self.bolge(v).update(kaynak="tahmin")), "kaynak")
        self.hata(self.bozuk(lambda v: v["sahneler"]["oyun"].update(ust_oran=1.5)), "ust_oran")
        self.hata(self.bozuk(lambda v: v["sahneler"]["oyun"].update(tur="oyunn")), "bilinmeyen sahne türü")

    def test_gecersiz_kutu(self):
        self.hata(self.bozuk(lambda v: self.bolge(v).update(kutu=[968, 762, 1452, 1040])), "0-1 arasında")
        self.hata(self.bozuk(lambda v: self.bolge(v).update(kutu=[0.5, 0.1, 0.4, 0.2])), "x1 < x2")
        self.hata(self.bozuk(lambda v: self.bolge(v).update(kutu=[0.1, 0.2, 0.3])), "dört sayılık")
        self.hata(self.bozuk(lambda v: self.bolge(v).update(kutu=[0.1, "0", 0.3, 0.4])), "dört sayılık")
        self.hata(self.bozuk(lambda v: self.bolge(v).update(kutu=[0.1, 0.1, 0.101, 0.2])), "çok küçük")

    def test_dogrulanmamis_bolge_kullanilmaz(self):
        self.hata(self.bozuk(lambda v: self.bolge(v, "oyun").update(dogrulandi=False, kaynak="otomatik")),
                  "'oyun' doğrulanmamış")

    def test_farkli_en_boy_orani(self):
        self.hata("se7en", "en-boy oranı", 1920, 1200)
        self.hata("se7en", "en-boy oranı", 1080, 1920)

    def test_sahneler(self):
        def ekle(v):
            v["sahneler"]["reaction"] = {"tur": "reaction", "bolgeler": {}}
        yol = self.bozuk(ekle)
        p.yerlesim(p.yukle(yol), None, 1920, 1080)                 # gelecek tür profili bozmaz
        self.hata(yol, "henüz desteklenmiyor", sahne="reaction")
        self.hata("se7en", "profilde yok", sahne="tam_kamera")


class Oncelik(unittest.TestCase):

    def test_duzen_yokken_eski_davranis(self):
        b, ust, k = d.yerlesimi_belirle(arg(), coz_yok)
        self.assertEqual(b, {ad: d.bolge(v) for ad, v in SE7EN_1080.items()})
        self.assertEqual((ust, k), (0.34, None))
        b, ust, _ = d.yerlesimi_belirle(arg(kamera="0,0,540,302", ust_oran=0.4), coz_yok)
        self.assertEqual((b["kamera"], b["pencere"], ust), ((0, 0, 540, 302), d.bolge(d.PENCERE), 0.4))

    def test_komut_satiri_profili_gecersiz_kilar(self):
        b, ust, k = d.yerlesimi_belirle(arg(duzen="se7en", kamera="10,10,500,300", ust_oran=0.3),
                                        lambda: (1920, 1080))
        self.assertEqual(b["kamera"], (10, 10, 490, 290))
        self.assertEqual(b["pencere"], d.bolge(SE7EN_1080["pencere"]))
        self.assertEqual((k["kamera"], k["pencere"], ust), ("komut satırı", "profil", 0.3))

    def test_komut_satiri_kare_disi_reddedilir(self):
        with self.assertRaises(p.ProfilHatasi):
            d.yerlesimi_belirle(arg(duzen="se7en", kamera="1000,500,1500,800"), lambda: (1280, 720))

    def test_oyun_bolgesi_olmayan_profil(self):
        with self.assertRaises(p.ProfilHatasi) as c:
            d.yerlesimi_belirle(arg(duzen="schradin", oyun="10-20"), lambda: (1920, 1080))
        self.assertIn("--oyun-bolge", str(c.exception))              # Se7en'in oyun bölgesine düşmez
        b, _, k = d.yerlesimi_belirle(arg(duzen="schradin", oyun="10-20", oyun_bolge="600,0,1400,1000"),
                                      lambda: (1920, 1080))
        self.assertEqual((b["oyun"], k["oyun"]), ((600, 0, 800, 1000), "komut satırı"))

    def test_sahne_duzensiz_kullanilamaz(self):
        with self.assertRaises(p.ProfilHatasi):
            d.yerlesimi_belirle(arg(sahne="oyun"), coz_yok)


class Regresyon(unittest.TestCase):
    """Mevcut üç klibin filtre zinciri profil öncesi komutlarla birebir aynı olmalı."""

    KLIPLER = [  # (eski komut argümanları, profil)
        (dict(oyun="26.05-40"), "se7en"),                                       # 032030-GECTI
        (dict(oyun="28.45-40"), "se7en"),                                       # 035015-GECTI
        (dict(kamera="0,0,540,302", pencere="540,0,1460,1080"), "schradin"),    # 013500-FALLSCHIRM
    ]

    def filtre(self, a, coz):
        b, ust, _ = d.yerlesimi_belirle(a, coz)
        return d.filtre_olustur(b["kamera"], b["pencere"], b["oyun"], d.araliklar(a.oyun), ust)

    def test_profil_eski_komutla_ayni_filtre(self):
        for eski, ad in self.KLIPLER:
            onceki = self.filtre(arg(**eski), coz_yok)
            yeni = self.filtre(arg(duzen=ad, oyun=eski.get("oyun", "")), lambda: (1920, 1080))
            self.assertEqual(onceki, yeni, ad)

    def test_eski_komut_filtresi_degismedi(self):
        # Profil öncesi koddan alınmış dizgenin parmak izi (032030 komutu)
        f = self.filtre(arg(oyun="26.05-40"), coz_yok)[0]
        self.assertIn("crop=484:278:968:762", f)
        self.assertIn("crop=426:740:1475:0", f)
        self.assertIn("crop=648:760:636:0", f)
        self.assertIn("enable='between(t,26.05,40.0)'", f)


if __name__ == "__main__":
    unittest.main()
