"""sohbet_klip.py regresyon testleri.  Çalıştırma:  python -m unittest -v test_sohbet_klip

Örnekler 2930392468 numaralı gerçek VOD sohbetinden alındı, kullanıcı adları değiştirildi.
"""

import unittest

import sohbet_klip as sk


def twitch_yorum(govde, ad="kullanici", t=10.0, bits=0):
    return {"content_offset_seconds": t, "commenter": {"name": ad},
            "message": {"body": govde, "bits_spent": bits, "user_notice_params": None,
                        "fragments": [{"text": govde, "emoticon": None}]}}


def bildirim_mi(govde):
    mesajlar, _, _ = sk.oku_twitch({"comments": [twitch_yorum(govde)]})
    return mesajlar[0]["bildirim"]


class TwitchBildirim(unittest.TestCase):
    """Düzeltme 1: user_notice_params=null olan düz metin bildirimler."""

    BILDIRIMLER = [
        "Ali__Veli gifted a Tier 1 sub to ornek123! ",
        "Ali__Veli gifted a Tier 1 sub to ornek123! They have given 12 Gift Subs in the channel! ",
        "Ali__Veli is gifting 200 Tier 1 Subs to Se7en's community! They've gifted a total of 400 in the channel! ",
        "ornek_kisi subscribed at Tier 1. ",
        "ornek_kisi subscribed at Tier 1. They've subscribed for 14 months! ",
        "ornek_kisi subscribed at Tier 1. They've subscribed for 9 months, currently on a 3 month streak! sa sa sa",
        "ornek_kisi subscribed with Prime. They've subscribed for 2 months! ",
        "An anonymous user gifted a Tier 1 sub to ornek123! ",
        "25 raiders from BaskaKanal have joined!",
    ]

    # aynı sözcükleri içeren, elenmemesi gereken gerçek sohbet mesajları
    NORMAL = [
        "@Ali__Veli hediye abonelik için teşekkürler!",
        "abi bana gelmedi abonelik ya",
        "6 aydir tier 1 sub olmususn onurum kutlu olsun",
        "abone pls :D",
        "abonelik hediye edecek var mı",
        "biri bana gifted a Tier 1 sub to atsın",        # kalıp cümle başında değil
        "raid atalım mı",
        "geçti",
    ]

    def test_bildirimler_yakalanir(self):
        for govde in self.BILDIRIMLER:
            with self.subTest(govde=govde):
                self.assertTrue(bildirim_mi(govde))

    def test_normal_mesajlar_elenmez(self):
        for govde in self.NORMAL:
            with self.subTest(govde=govde):
                self.assertFalse(bildirim_mi(govde))

    def test_eski_yollar_calisir(self):
        bits = sk.oku_twitch({"comments": [twitch_yorum("Cheer100", bits=100)]})[0][0]
        self.assertTrue(bits["bildirim"])
        y = twitch_yorum("selam")
        y["message"]["user_notice_params"] = {"msg-id": "resub"}
        self.assertTrue(sk.oku_twitch({"comments": [y]})[0][0]["bildirim"])


class KomutEleme(unittest.TestCase):
    """Düzeltme 2: ! komutları yoğunluktan çıkar, ayrı sayaçta raporlanır."""

    def m(self, metin, ad="kullanici", bildirim=False):
        return sk.mesaj(0, ad, metin, bildirim)

    def test_komutlar_ayri_sayilir(self):
        mesajlar = [self.m("!ümit"), self.m("!ÜMİT"), self.m("  !item shard +9"),
                    self.m("!knightpin https://knightpin.com"),     # link değil komut
                    self.m("geçti"), self.m("bu !ümit ne işe yarıyor"),
                    self.m("bak https://ornek.com"),
                    self.m("!komutlar", ad="Nightbot"),              # önce bot
                    self.m("!ümit", bildirim=True)]                  # önce bildirim
        kalan, say = sk.ele(mesajlar)
        self.assertEqual([m["metin"] for m in kalan], ["geçti", "bu !ümit ne işe yarıyor"])
        self.assertEqual(dict(say), {"bot": 1, "bildirim": 1, "komut": 4, "link": 1})

    def test_komut_yigini_patlama_olmaz(self):
        # 04:09:30 benzeri: 50 kişi !ümit yazıyor, 2 gerçek mesaj var
        mesajlar = [self.m("!ümit", ad=f"k{i}") for i in range(50)]
        mesajlar += [self.m("FÜMEZ kazansın burda bari"), self.m("selam")]
        kalan, say = sk.ele(mesajlar)
        self.assertEqual(len(kalan), 2)
        self.assertEqual(say["komut"], 50)


class WatchStreak(unittest.TestCase):
    """Watch streak bildirimi (VOD 2931374918'den, ad değiştirildi)."""

    def test_watch_streak_bildirim(self):
        self.assertTrue(bildirim_mi("Ornek2087 watched 245 consecutive streams and sparked a watch streak! "))
        self.assertTrue(bildirim_mi("ornek watched 12 consecutive streams and sparked a watch streak! hi"))

    def test_benzer_normal_mesaj(self):
        self.assertFalse(bildirim_mi("I watched 3 streams today"))
        self.assertFalse(bildirim_mi("watch streak kaç gün oldu"))


def akis(dilimler, dilim=15):
    """[(kullanıcı, metin) listesi, ...] -> her dilime bir liste düşen mesaj akışı."""
    mesajlar = []
    for i, d in enumerate(dilimler):
        for j, (ad, metin) in enumerate(d):
            mesajlar.append(sk.mesaj(i * dilim + j * 0.1, ad, metin, False))
    return mesajlar


def arka_plan(i, n=4):
    return [(f"bg{i}_{j}", f"sohbet mesajı {j}") for j in range(n)]


class Secim(unittest.TestCase):
    """Kişi başı katkı, ivme ve tepki/soru-cevap ile seçim."""

    def setUp(self):
        self.adci = sk.Adlandirici({"KEKW", "LUL"}, "de")

    def sec(self, dilimler, adet=10):
        m = akis(dilimler)
        return sk.anlari_sec(m, len(dilimler), 15, self.adci, adet)

    def test_kisi_basi_en_fazla_iki(self):
        d = [arka_plan(i) for i in range(20)]
        d[10] = [("spamci", "Mikro ist aus")] * 26 + [("racer", "Wir hören dich nicht")] * 12
        ozet, secilen = self.sec(d)
        self.assertEqual(secilen, [])          # 38 mesaj ama yalnız 4 katkı: aday olmaz

    def test_ivme_ani_ve_suregiden(self):
        d = [arka_plan(i) for i in range(81)]
        d[10] = [(f"u{j}", f"mesaj {j}") for j in range(30)]              # sessizlikten sonra
        for i in range(40, 45):                                          # 75 sn süren yoğunluk
            d[i] = [(f"s{i}_{j}", f"mesaj {j}") for j in range(30)]
        _, secilen = self.sec(d)
        ivme = {s["i"]: s["ivme"] for s in secilen}
        self.assertGreater(ivme[10], 3)        # ani sıçrama
        self.assertLess(ivme[44], 1.1)         # aynı hacim ama 60 sn'dir süren yoğunluk
        sira = [s["i"] for s in secilen]
        self.assertLess(sira.index(10), sira.index(44))

    def test_tepki_soru_cevabin_ustunde(self):
        d = [arka_plan(i) for i in range(40)]
        cevaplar = ["Dreieck", "Touchpad", "R2", "Kreis", "Optionstaste", "L1", "Viereck",
                    "Share", "Quadrat", "Start"]
        d[8] = [(f"q{j}", cevaplar[j % 10]) for j in range(40)]           # 40 kişi, soru-cevap
        d[25] = [(f"r{j}", ["KEKW", "hahahaha", "LUL LUL", "Alter Schwede", "neeeein"][j % 5])
                 for j in range(32)]                                     # 32 kişi, kahkaha
        d[33] = [("spam1", "ÖMER ABİ <3")] * 6 + [("spam2", "ÖMER ABİ <3")] * 5 + \
                [(f"x{j}", "selam") for j in range(4)]
        _, secilen = self.sec(d)
        sira = [s["i"] for s in secilen]
        self.assertEqual(sira[0], 25)
        qa = next(s for s in secilen if s["i"] == 8)
        self.assertTrue(qa["soru_cevap"])      # silinmedi, etiketlendi ve aşağıda
        self.assertNotIn(33, sira)

    def test_tepki_ve_soru_ozellikleri(self):
        a = sk.Adlandirici({"omE"}, "tr")
        for m in ["hahaha", "KEKW", "LUL LUL", "GEÇTİİİİ", "????", "oha", "omE", "😂", "geçti lan"]:
            with self.subTest(m=m):
                self.assertTrue(a.tepki_mi(m))
        for m in ["Dreieck", "wo ist r5", "@ali evet", "ne zaman çekiliş?", "Touch Pad"]:
            with self.subTest(m=m):
                self.assertFalse(a.tepki_mi(m))
                self.assertTrue(a.soru_mu(m))
        self.assertFalse(a.soru_mu("bu adam bugün çok iyi oynuyor"))


class AlmancaAd(unittest.TestCase):

    def test_dil_tahmin(self):
        self.assertEqual(sk.dil_tahmin(["was ist das", "ich bin nicht da", "und du"]), "de")
        self.assertEqual(sk.dil_tahmin(["ne oluyor abi", "bu çok iyi", "geçti ya"]), "tr")
        self.assertEqual(sk.dil_tahmin([]), "tr")

    def test_almanca_ad_turkce_buyuk_harf_kullanmaz(self):
        a = sk.Adlandirici({"LUL"}, "de")
        ad = a.ad(["Alter Schwede LUL", "alter schwede", "Ist das krass", "ALTER SCHWEDE"])
        self.assertEqual(ad, "ALTER SCHWEDE")
        self.assertNotIn("İ", a.ad(["ist in Neuseeland", "Neuseeland ist weit", "Ich will Neuseeland"]))
        self.assertEqual(a.ad(["ist in Neuseeland", "Neuseeland ist weit"]), "NEUSEELAND")

    def test_almanca_dolgu_ve_klavye(self):
        a = sk.Adlandirici(set(), "de")
        self.assertIsNone(a.ad(["du", "der die das", "ist es"]))
        self.assertEqual(a.kelime("schwer"), "schwer")     # 'wer' Almanca'da karalama değil
        self.assertEqual(sk.ascii_ad("ÖFFNEN STRASSE", "de"), "OEFFNEN_STRASSE")

    def test_turkce_ad_degismedi(self):
        a = sk.Adlandirici(set(), "tr")
        self.assertEqual(a.ad(["insaflı", "çok insaflı adam", "İNSAFLI ya"]), "İNSAFLI")
        self.assertEqual(a.ad(["ne oluyor", "ne oluyor abi", "NE OLUYOR", "oluyor ne"]),
                         "NE OLUYOR")


if __name__ == "__main__":
    unittest.main()
