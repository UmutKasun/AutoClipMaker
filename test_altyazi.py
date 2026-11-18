"""altyazi.py testleri (ses/model gerektirmez).  python -m unittest -v test_altyazi"""

import unittest

import altyazi as a


def kel(*kelimeler, aralik=0.4):
    return [{"no": i + 1, "metin": w, "bas": i * aralik, "son": i * aralik + 0.3,
             "olasilik": 1.0, "duzeltme": None} for i, w in enumerate(kelimeler)]


class Maske(unittest.TestCase):

    def test_agir_kufur_maskelenir(self):
        self.assertEqual(a.maskele("amk"), "a**")
        self.assertEqual(a.maskele("Hurensohn!"), "H********!")
        for w, dil in [("amk", "tr"), ("Siktir!", "tr"), ("AMINA", "tr"), ("Hurensohn", "de"),
                       ("fuck", "en")]:
            with self.subTest(w=w):
                self.assertTrue(a.agir_kufur_mu(w, dil))

    def test_hafif_ve_normal_kelimeler_maskelenmez(self):
        for w, dil in [("Scheiße!", "de"), ("sıkıntı", "tr"), ("sikinti", "tr"), ("geçti", "tr"),
                       ("Amca", "tr"), ("ama", "tr")]:
            with self.subTest(w=w):
                self.assertFalse(a.agir_kufur_mu(w, dil))

    def test_maskesiz_secenegi_ve_ham_metin(self):
        veri = {"dil": "tr", "kelimeler": kel("oha", "amk")}
        self.assertEqual([k["goster"] for k in a.gorunen(veri, True, True)], ["OHA", "A**"])
        self.assertEqual([k["goster"] for k in a.gorunen(veri, False, True)], ["OHA", "AMK"])
        self.assertEqual(veri["kelimeler"][1]["metin"], "amk")      # ham transkripsiyon değişmez


class Duzeltme(unittest.TestCase):

    def test_duzeltme_ve_gizleme(self):
        k = kel("60K", "'li", "olsun", "eee")
        k[1]["duzeltme"] = "hayırlı"
        k[3]["duzeltme"] = ""
        g = a.gorunen({"dil": "tr", "kelimeler": k}, True, True)
        self.assertEqual([x["goster"] for x in g], ["60K", "HAYIRLI", "OLSUN"])

    def test_dil_buyuk_harf(self):
        tr = a.gorunen({"dil": "tr", "kelimeler": kel("bismillah", "ışık")}, True, True)
        de = a.gorunen({"dil": "de", "kelimeler": kel("ist", "Straße")}, True, True)
        self.assertEqual([x["goster"] for x in tr], ["BİSMİLLAH", "IŞIK"])
        self.assertEqual([x["goster"] for x in de], ["IST", "STRASSE"])


class Gruplama(unittest.TestCase):

    def gr(self, *w, **kw):
        return [[x["goster"] for x in g]
                for g in a.grupla(a.gorunen({"dil": "tr", "kelimeler": kel(*w, **kw)}, True, True))]

    def test_en_fazla_dort_kelime_ve_karakter_siniri(self):
        self.assertEqual(self.gr("bak", "çok", "iyi", "bu", "adam"), [["BAK", "ÇOK", "İYİ"], ["BU", "ADAM"]])
        uzun = self.gr("güvendiğim", "isimler", "geçti", "diyor", "birazdan", "damlayacağım")
        self.assertTrue(all(len(g) <= a.MAX_KELIME and
                            (sum(len(w) + 1 for w in g) - 1 <= a.MAX_KARAKTER or len(g) == 1) for g in uzun))

    def test_dengeli_bolme_yalniz_kuyruk_birakmaz(self):
        # 9 kelimelik cümle: açgözlü bölme 4+4+1 verirdi; dengeli bölme en az grup (3),
        # karakter uzunluğunu dengeler ve tek kelimelik kuyruk bırakmaz
        g = self.gr("bak,", "çok", "güvendiğim", "isimler", "geçti", "diyor", "şu", "anda", "ya.")
        self.assertEqual(len(g), 3)
        self.assertTrue(all(len(x) >= 2 for x in g))
        self.assertEqual(g[-1][-2:], ["ANDA", "YA."])                    # "şu anda" bölünmez
        self.assertEqual(self.gr("ja,", "das", "ist", "so,", "ne?"), [["JA,", "DAS", "İST"], ["SO,", "NE?"]])

    def test_virgulden_bolmeyi_tercih_eder(self):
        g = [[x["goster"] for x in gg] for gg in a.grupla(a.gorunen(
            {"dil": "de", "kelimeler": kel("lass", "es", "lieber", "nein,", "nein,", "nein")}, True, True))]
        self.assertEqual(g, [["LASS", "ES", "LIEBER"], ["NEIN,", "NEIN,", "NEIN"]])

    def test_cumle_sonu_ve_sessizlik_boler(self):
        self.assertEqual(self.gr("sadece", "bakalım.", "oha", "geçti"),
                         [["SADECE", "BAKALIM."], ["OHA", "GEÇTİ"]])
        self.assertEqual(self.gr("hadi", "geçer", aralik=1.0), [["HADİ"], ["GEÇER"]])

    def test_unlem_zinciri_birlesir_yalniz_unlem_korunur(self):
        self.assertEqual(self.gr("geçer.", "geçer.", "geçer."), [["GEÇER.", "GEÇER.", "GEÇER."]])
        self.assertEqual(self.gr("hadi.", "çat.", aralik=1.0), [["HADİ."], ["ÇAT."]])
        self.assertEqual(self.gr("oha!", "geçti!", "cidden", "geçti", "diyor."),
                         [["OHA!", "GEÇTİ!"], ["CİDDEN", "GEÇTİ", "DİYOR."]])


class Inceleme(unittest.TestCase):

    def test_nedenler(self):
        v = {"kelimeler": kel("Volkan", "sen", "60K", "'li", "olsun")}
        v["kelimeler"][0]["olasilik"] = 0.54
        v["kelimeler"][3]["duzeltme"] = "hayırlı"
        v["kelimeler"][4]["son"] = v["kelimeler"][4]["bas"]          # sıfır süre
        alt = {"kelimeler": [dict(k) for k in kel("Lohkan", "sen", "60K")]}
        s = {k["no"]: k["nedenler"] for k in a.inceleme_satirlari(v, alt)}
        self.assertIn("ikinci tanımayla uyuşmuyor", s[1])
        self.assertTrue(any("düşük güven" in n for n in s[1]))
        self.assertNotIn(2, s)                                           # uyuşuyor, güven yüksek
        self.assertTrue(any("elle düzeltildi" in n for n in s[4]))
        self.assertIn("ikinci tanımada yok", s[5])
        self.assertIn("zaman damgası şüpheli (süre ~0)", s[5])

    def test_dogrulanan_listeye_girmez(self):
        v = {"kelimeler": kel("Volkan")}
        v["kelimeler"][0].update(olasilik=0.2, dogrulandi=True)
        self.assertEqual(a.inceleme_satirlari(v, None), [])


class Ass(unittest.TestCase):

    def test_zaman_bicimi(self):
        self.assertEqual(a.ass_zaman(0), "0:00:00.00")
        self.assertEqual(a.ass_zaman(83.456), "0:01:23.46")

    def test_konusulan_kelime_sari(self):
        g = a.grupla(a.gorunen({"dil": "tr", "kelimeler": kel("oha", "geçti")}, True, True))
        olaylar = [s for s in a.ass_olustur(g, 652, 78).splitlines() if s.startswith("Dialogue")]
        self.assertEqual(len(olaylar), 2)
        self.assertIn(f"{{\\c{a.SARI}&}}OHA{{\\c{a.BEYAZ}&}} GEÇTİ", olaylar[0])
        self.assertIn(f"OHA {{\\c{a.SARI}&}}GEÇTİ", olaylar[1])
        self.assertIn("\\pos(540,652)", olaylar[0])


if __name__ == "__main__":
    unittest.main()
