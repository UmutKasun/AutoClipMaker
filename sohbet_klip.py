#!/usr/bin/env python3
"""sohbet_klip.py - Yayın sohbetindeki patlama anlarını bulup dikey klip keser.

Sohbet kaynağı: Twitch (TwitchDownloaderCLI chat.json), YouTube (yt-dlp
.live_chat.json) ya da Kick video adresi (sohbeti kendisi indirir).
Yalnız Python standart kütüphanesi ve ffmpeg/ffprobe kullanır.

Örnek:
    python sohbet_klip.py chat.json yayin.mp4
    python sohbet_klip.py https://kick.com/kanal/videos/<id> --sadece-liste
"""

import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ARALIK = 60          # birbirine bundan yakın patlamalardan yalnız en yoğunu kalır
UZUN_ORAN = 0.40     # uzun ifade, tek kelimenin en az bu oranı kadar geçmeli
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------

def tr_lower(s):
    return s.translate(str.maketrans({"I": "ı", "İ": "i"})).lower()


def tr_upper(s):
    return s.translate(str.maketrans({"i": "İ", "ı": "I"})).upper()


ASCII_MAP = str.maketrans({"İ": "I", "ı": "i", "Ğ": "G", "ğ": "g", "Ü": "U", "ü": "u",
                           "Ş": "S", "ş": "s", "Ö": "O", "ö": "o", "Ç": "C", "ç": "c"})


ASCII_DE = str.maketrans({"Ä": "AE", "Ö": "OE", "Ü": "UE", "ä": "ae", "ö": "oe", "ü": "ue",
                          "ß": "ss"})


def ascii_ad(s, dil="tr"):
    s = s.translate(ASCII_DE if dil == "de" else ASCII_MAP)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"\s+", "_", s.strip())
    return re.sub(r"[^A-Za-z0-9_]", "", s) or "KLIP"


def sure_yaz(sn):
    sn = int(round(sn))
    return f"{sn // 3600:02d}:{sn % 3600 // 60:02d}:{sn % 60:02d}"


def tarih(s):
    s = str(s).strip().replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def http_json(url, deneme=4, zaman_asimi=20):
    istek = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        "Referer": "https://kick.com/",
    })
    for i in range(deneme):
        try:
            with urllib.request.urlopen(istek, timeout=zaman_asimi) as yanit:
                return json.loads(yanit.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or i == deneme - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if i == deneme - 1:
                raise
        time.sleep(2 ** i)


def mesaj(t, gonderen, metin, bildirim):
    return {"t": float(t), "ad": gonderen or "", "metin": metin or "",
            "bildirim": bool(bildirim)}


# --------------------------------------------------------------------------
# 1. Sohbeti oku
# --------------------------------------------------------------------------

# TwitchDownloaderCLI 1.56 sistem bildirimlerini user_notice_params=null ile, düz
# metin olarak yazıyor. Kalıplar gerçek VOD sohbetinden alındı; metnin başına
# sabitli olduğu için "hediye abonelik için teşekkürler", "6 aydır tier 1 sub
# olmuşsun" gibi normal mesajlara uymaz.
TWITCH_BILDIRIM = re.compile(
    r"^(?:An anonymous user|\S+) gifted a Tier \d sub to \S+!"         # hediye abonelik
    r"|^\S+ is gifting \d+ Tier \d Subs? to .+'s community!"           # toplu hediye
    r"|^\S+ subscribed (?:at Tier \d|with Prime)\."                    # abonelik / yenileme
    r"|^\S+ watched \d+ consecutive streams and sparked a watch streak!"   # VOD 2931374918
    # bu VOD'da görülmedi, Twitch'in standart metinleri:
    r"|^\S+ is (?:continuing the Gift Sub|paying forward the Gift) they got from "
    r"|^\S+ converted from a Prime sub to a Tier \d sub!"
    r"|^\d+ raiders from \S+ have joined!")


def oku_twitch(veri):
    mesajlar, emotelar = [], set()
    for c in veri.get("comments") or []:
        m = c.get("message") or {}
        kisi = c.get("commenter") or {}
        notice = ((m.get("user_notice_params") or {}).get("msg-id")
                  or TWITCH_BILDIRIM.match(m.get("body") or ""))
        bits = m.get("bits_spent") or 0
        for f in m.get("fragments") or []:
            if f.get("emoticon") and f.get("text"):
                emotelar.add(f["text"].strip())
        mesajlar.append(mesaj(c.get("content_offset_seconds", 0),
                              kisi.get("name") or kisi.get("display_name"),
                              m.get("body"), bool(notice) or bits > 0))
    video = veri.get("video") or {}
    sure = float(video["length"]) if video.get("length") else None
    return mesajlar, emotelar, sure


def yt_runs(runs, emotelar):
    parcalar = []
    for r in runs or []:
        if "text" in r:
            parcalar.append(r["text"])
        elif "emoji" in r:
            e = r["emoji"]
            kisaltmalar = e.get("shortcuts") or []
            if e.get("isCustomEmoji") or kisaltmalar:
                ad = kisaltmalar[0] if kisaltmalar else e.get("emojiId", "")
                emotelar.add(ad)
                emotelar.update(kisaltmalar)
                parcalar.append(f" {ad} ")
            else:
                parcalar.append(e.get("emojiId", ""))
    return "".join(parcalar)


def oku_youtube(yol):
    mesajlar, emotelar = [], set()
    with open(yol, encoding="utf-8") as f:
        for satir in f:
            satir = satir.strip()
            if not satir:
                continue
            try:
                d = json.loads(satir)
            except json.JSONDecodeError:
                continue
            rep = d.get("replayChatItemAction") or {}
            try:
                ofs = int(rep.get("videoOffsetTimeMsec", d.get("videoOffsetTimeMsec")))
            except (TypeError, ValueError):
                continue
            if ofs <= 0:          # yayın öncesi bekleme sohbeti
                continue
            for act in rep.get("actions") or []:
                item = (act.get("addChatItemAction") or {}).get("item") or {}
                for tur, r in item.items():
                    if not isinstance(r, dict):
                        continue
                    if tur == "liveChatTextMessageRenderer":
                        bildirim = False
                    elif "authorName" in r:
                        bildirim = True          # super chat, üyelik vb.
                    else:
                        continue
                    mesajlar.append(mesaj(
                        ofs / 1000, (r.get("authorName") or {}).get("simpleText"),
                        yt_runs((r.get("message") or {}).get("runs"), emotelar), bildirim))
    return mesajlar, emotelar, None


KICK_EMOTE = re.compile(r"\[emote:\d+:([^\]]+)\]")


def oku_kick(veri):
    bas = tarih(veri["start_time"])
    mesajlar, emotelar = [], set()
    sure = (veri.get("duration") or 0) / 1000 or None
    for m in veri.get("messages") or []:
        try:
            t = (tarih(m["created_at"]) - bas).total_seconds()
        except (KeyError, ValueError):
            continue
        if t < 0 or (sure and t > sure):
            continue
        metin = m.get("content") or ""
        emotelar.update(KICK_EMOTE.findall(metin))
        metin = KICK_EMOTE.sub(lambda x: f" {x.group(1)} ", metin)
        gonderen = (m.get("sender") or {}).get("username")
        mesajlar.append(mesaj(t, gonderen, metin,
                              m.get("type") not in ("message", "reply")))
    return mesajlar, emotelar, sure


KICK_URL = re.compile(r"kick\.com/([^/\s]+)/videos?/([A-Za-z0-9-]+)")
KICK_KAPSAM_TOLERANS = 120       # sn: başlangıca bu kadar yaklaşılmadıysa sohbet eksik sayılır


def uuid7_zamani(vid):
    """UUIDv7'nin ilk 48 biti oluşturma zamanıdır (ms); değilse None."""
    h = vid.replace("-", "").lower()
    if len(h) != 32 or h[12] != "7" or not re.fullmatch(r"[0-9a-f]{32}", h):
        return None
    return datetime.fromtimestamp(int(h[:12], 16) / 1000, timezone.utc)


def kick_video_bilgisi(vid, kanal=None):
    """v1/video yanıtı. Kick web bağlantıları yeni UUIDv7 kimlik kullanıyor, API eski
    kimliği bekliyor: 404 olursa kanalın video listesinde başlangıç zamanından eşlenir."""
    try:
        return http_json(f"https://kick.com/api/v1/video/{vid}")
    except urllib.error.HTTPError as e:
        if e.code != 404 or not kanal:
            raise
    t = uuid7_zamani(vid)
    liste = http_json(f"https://kick.com/api/v2/channels/{kanal}/videos")
    uuidler = [(x.get("video") or {}).get("uuid") for x in liste]
    if vid in uuidler:
        sys.exit(f"Kick videosu {vid} listede var ama API 404 döndü.")
    eslesen = [x for x in liste if t and x.get("start_time")
               and abs((tarih(x["start_time"]) - t).total_seconds()) <= 2]
    if len(eslesen) != 1:
        sys.exit(f"Kick videosu bulunamadı: {vid} (API 404; kanal '{kanal}' listesindeki "
                 f"{len(liste)} videodan {len(eslesen)} tanesi zamanla eşleşti)")
    eski = eslesen[0]["video"]["uuid"]
    print(f"Kick kimliği eşlendi: {vid} -> {eski} (başlangıç {eslesen[0]['start_time']} UTC)")
    return http_json(f"https://kick.com/api/v1/video/{eski}")


def kick_indir(vid, kanal=None, hedef=None):
    hedef = Path(hedef or f"kick-{vid}.json")
    if hedef.exists():
        veri = json.loads(hedef.read_text(encoding="utf-8"))
        if (veri.get("kapsam") or {}).get("tamamlandi") is False:
            sys.exit(f"Önbellekteki Kick sohbeti eksik: {hedef}; silip yeniden indirin.")
        print(f"Kick sohbeti önbellekten: {hedef}")
        return veri
    print(f"Kick video bilgisi alınıyor: {vid}")
    meta = kick_video_bilgisi(vid, kanal)
    ls = meta.get("livestream") or {}
    kanal = ls.get("channel_id") or meta.get("channel_id")
    bas_s = ls.get("start_time") or meta.get("start_time")
    sure_ms = ls.get("duration") or meta.get("duration")
    if not (kanal and bas_s and sure_ms):
        sys.exit("Kick yanıtında channel_id / start_time / duration bulunamadı.")
    bas_us = int(tarih(bas_s).timestamp() * 1_000_000)
    imlec = bas_us + int(sure_ms) * 1000
    toplam_us = imlec - bas_us
    mesajlar, sayfa, bitis = {}, 0, None
    print("Sohbet sondan başa doğru indiriliyor...")
    while True:
        d = http_json(f"https://kick.com/api/v2/channels/{kanal}/messages?cursor={imlec}", deneme=8)
        data = d.get("data") or {}
        for m in data.get("messages") or []:
            # yalnız oku_kick'in kullandığı alanlar (tam nesne yayın başına yüzlerce MB eder)
            mesajlar[m.get("id") or (m.get("created_at"), m.get("content"))] = {
                "id": m.get("id"), "created_at": m.get("created_at"), "content": m.get("content"),
                "type": m.get("type"), "sender": {"username": (m.get("sender") or {}).get("username")}}
        sayfa += 1
        if sayfa % 20 == 0:
            yuzde = 100 * (1 - (imlec - bas_us) / toplam_us)
            print(f"  {sayfa} sayfa, {len(mesajlar)} mesaj (%{yuzde:.0f})", flush=True)
        try:
            yeni = int(data.get("cursor"))
        except (TypeError, ValueError):
            bitis = "cursor yok"
            break
        if yeni < bas_us:
            bitis = "yayın başlangıcına ulaşıldı"
            break
        if yeni >= imlec:
            bitis = "cursor ilerlemedi"
            break
        imlec = yeni
        time.sleep(0.15)
    kalan = max(0.0, (imlec - bas_us) / 1_000_000)
    tamam = bitis == "yayın başlangıcına ulaşıldı" or kalan <= KICK_KAPSAM_TOLERANS
    liste = sorted(mesajlar.values(), key=lambda m: str(m.get("created_at", "")))
    veri = {"platform": "kick", "video_id": vid, "api_uuid": meta.get("uuid"), "channel_id": kanal,
            "channel_slug": (ls.get("channel") or {}).get("slug"), "session_title": ls.get("session_title"),
            "language": ls.get("language"), "source": meta.get("source"),
            "start_time": bas_s, "duration": sure_ms,
            "kapsam": {"tamamlandi": tamam, "bitis_nedeni": bitis, "kapsanmayan_bas_sn": round(kalan, 1),
                       "sayfa": sayfa},
            "messages": liste}
    if not tamam:
        eksik = hedef.with_suffix(".eksik.json")
        eksik.write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")
        sys.exit(f"Kick sohbeti EKSİK: {bitis}; yayının ilk {kalan:.0f} sn'si indirilemedi. "
                 f"Kısmi veri {eksik} (kullanılmaz).")
    gecici = hedef.with_suffix(".json.part")
    gecici.write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")
    gecici.replace(hedef)
    print(f"Kick sohbeti kaydedildi: {hedef} ({len(liste)} mesaj, {sayfa} sayfa, {bitis})")
    return veri


def sohbet_oku(kaynak):
    """(platform, mesajlar, emote kümesi, yayın süresi, ham twitch verisi)"""
    km = KICK_URL.search(kaynak)
    if km:
        veri = kick_indir(km.group(2), km.group(1))
        return ("Kick",) + oku_kick(veri) + (veri,)
    yol = Path(kaynak)
    if not yol.exists():
        sys.exit(f"Sohbet dosyası bulunamadı: {kaynak}")
    try:
        veri = json.loads(yol.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        veri = None                      # satır satır JSON (YouTube)
    if isinstance(veri, dict) and "comments" in veri:
        return ("Twitch",) + oku_twitch(veri) + (veri,)
    if isinstance(veri, dict) and "start_time" in veri and "messages" in veri:
        if (veri.get("kapsam") or {}).get("tamamlandi") is False:
            sys.exit(f"Kick sohbeti eksik ({veri['kapsam'].get('bitis_nedeni')}); kullanılmadı: {kaynak}")
        return ("Kick",) + oku_kick(veri) + (veri,)
    if veri is None or (isinstance(veri, dict) and "replayChatItemAction" in veri):
        return ("YouTube",) + oku_youtube(yol) + (None,)
    sys.exit("Sohbet biçimi tanınamadı (Twitch chat.json, YouTube .live_chat.json "
             "ya da Kick adresi bekleniyor).")


# --------------------------------------------------------------------------
# Twitch kanal emote'ları (7TV, BTTV, FFZ) - önbellekli, hata olursa sessiz
# --------------------------------------------------------------------------

def twitch_kanal_emotelari(veri, klasor):
    sid = str(((veri or {}).get("streamer") or {}).get("id") or "")
    if not sid:
        return set()
    onbellek = klasor / "chat-emotes.json"
    try:
        o = json.loads(onbellek.read_text(encoding="utf-8"))
        if str(o.get("streamer_id")) == sid:
            return set(o.get("emotes") or [])
    except (OSError, ValueError, AttributeError):
        pass

    adlar, basarili = set(), False

    def cek(url, ayikla):
        nonlocal basarili
        try:
            adlar.update(ayikla(http_json(url, deneme=1, zaman_asimi=8)))
            basarili = True
        except Exception:
            pass

    def ffz(d):
        return [e["name"] for s in (d.get("sets") or {}).values()
                for e in s.get("emoticons") or []]

    cek(f"https://7tv.io/v3/users/twitch/{sid}",
        lambda d: [e["name"] for e in (d.get("emote_set") or {}).get("emotes") or []])
    cek("https://7tv.io/v3/emote-sets/global",
        lambda d: [e["name"] for e in d.get("emotes") or []])
    cek(f"https://api.betterttv.net/3/cached/users/twitch/{sid}",
        lambda d: [e["code"] for e in (d.get("channelEmotes") or []) + (d.get("sharedEmotes") or [])])
    cek("https://api.betterttv.net/3/cached/emotes/global",
        lambda d: [e["code"] for e in d or []])
    cek(f"https://api.frankerfacez.com/v1/room/id/{sid}", ffz)
    cek("https://api.frankerfacez.com/v1/set/global", ffz)

    if basarili:
        try:
            onbellek.write_text(json.dumps({"streamer_id": sid, "emotes": sorted(adlar)},
                                           ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return adlar


# --------------------------------------------------------------------------
# 2. Eleme
# --------------------------------------------------------------------------

BILINEN_BOTLAR = {"nightbot", "streamelements", "streamlabs", "moobot", "fossabot",
                  "wizebot", "botrix", "kickbot", "sery_bot", "soundalerts",
                  "commanderroot", "streamlootsbot", "pokemoncommunitygame"}
LINK = re.compile(r"(https?://|www\.)|\b[\w-]+\.(com|net|org|tv|gg|io|ly|me|co|tr|be|link|xyz|app)\b",
                  re.I)


def bot_mu(ad):
    a = (ad or "").lower()
    return a.endswith("bot") or a in BILINEN_BOTLAR


def _sikistir(w):
    return re.sub(r"(.)\1+", r"\1", w)                 # selammm -> selam, abii -> abi


# Yalnız selamlaşma/vedalaşma olan mesajlar (yayın başı/sonu "iyi geceler" yağmuru an değildir).
# Mesajın her kelimesi SELAM_ASIL ya da SELAM_EK'te olmalı, en az biri SELAM_ASIL'da.
SELAM_ASIL = {_sikistir(w) for w in """selam slm sa sea selamün selamun selamunaleyküm aleyküm aleykum
aleykümselam as merhaba merhabalar mrb meraba hoşgeldin hosgeldin hoşgeldiniz hg hoşbulduk hosbulduk hb
geldin geceler akşamlar aksamlar yayınlar yayinlar görüşürüz gorusuruz görüşmek gorusmek
günaydın gunaydin uykular baybay bb gelsin hi hello hey yo hallo servus moin gn goodnight night
bye byebye cya morning gm nacht tschüss tschüs tschau ciao n8 abend morgen""".split()}
SELAM_EK = {_sikistir(w) for w in """iyi hoş hos kolay good gute guten see you bis dir dich euch herkese
abi abim abicim abla kanka kardeşim hocam reis kral chat all everyone guys leute alle ve and und
tekrar yine sana size""".split()}


def selam_mi(metin):
    w = [_sikistir(tr_lower(x)) for x in re.findall(r"[^\W\d_]+", metin)]
    return 0 < len(w) <= 6 and all(x in SELAM_ASIL or x in SELAM_EK for x in w) \
        and any(x in SELAM_ASIL for x in w)


def ele(mesajlar):
    """Sayılacak mesajları ve elenenlerin sayaçlarını döndürür.
    Sıra: bot > bildirim > komut (!ümit gibi) > link > selamlaşma."""
    say = Counter(bot=0, bildirim=0, komut=0, link=0, selam=0)
    kalan = []
    for m in mesajlar:
        if bot_mu(m["ad"]):
            say["bot"] += 1
        elif m["bildirim"]:
            say["bildirim"] += 1
        elif m["metin"].lstrip().startswith("!"):
            say["komut"] += 1
        elif LINK.search(m["metin"]):
            say["link"] += 1
        elif selam_mi(m["metin"]):
            say["selam"] += 1
        else:
            kalan.append(m)
    return kalan, say


# --------------------------------------------------------------------------
# 8. Klip adı
# --------------------------------------------------------------------------

YAYGIN_EMOTE = set("""kekw lul omegalul pepelaugh kappa pogchamp pog poggers pogu sadge copium
monkas monkaw pepehands feelsbadman feelsgoodman feelsstrongman 4head residentsleeper
biblethump trihard kreygasm wutface notlikethis lulw ez clap catjam pepega widepeepohappy
peeposad peepohappy hypers kekl icant aware stare ratjam modcheck bedge xdd gigachad
copege okaychamp jebaited dansgame babyrage swiftrage smorc kappapride heyguys ayaya
weirdchamp pepepls pepejam sussy dogelaugh monkaomega kekwait lulwait pausechamp pausers
widehardo susge xqcl""".split())

DOLGU_EN = set("""chat the an is are and or to of in it you me my this that so lol lmao omg
xd ok okay""".split())
DOLGU = set("""ve bir bi bu şu su o ki da de mi mı mu mü ne ya yaa yani çok cok daha gibi
için icin ile ama fakat ben sen biz siz onlar var yok olm olum oğlum oglum lan la len ha he
evet hayır hayir tamam tm şey sey her hep en falan filan ise diye kadar hiç hic bile şimdi
simdi artık artik zaten sadece çünkü cunku neden niye kim beni bana seni sana onu ona bunu
buna şunu sunu bunlar değil degil abi abim abla aga kanka kardeş kardes kardeşim bro hocam
reis""".split()) | DOLGU_EN
DOLGU_DE = set("""ich du er sie es wir ihr der die das den dem des ein eine einen einem einer
und oder aber ist bin bist sind war wäre hat hast habe haben hab nicht nich kein keine ja jaa
nein nee so wie was wer wo wann warum wieso mal noch schon auch nur doch ganz sehr mit von zu
zum zur im in am an auf für aus bei nach über um man dich mich dir mir sein ihn ihm uns euch
denn dann da hier jetzt alles alle immer gar halt eben einfach wirklich voll mega bitte
digga bro""".split()) | DOLGU_EN

UNLEM = set("""ah oh eh uh ay of öf off uf uff hm hmm ım ıh ee aa oo ii uu üü öö ayy
vay yo ey hey aha ehe öhö ach na naja hä äh ähm öhm boah jo""".split())

# Dil tahmini için her dilin ayırt edici sık kelimeleri
DIL_ISARET = {
    "tr": set("ve bir bu ne çok abi için icin ama değil degil gibi mı mi da de ya yani".split()),
    "de": set("und ist das nicht ich du der die ein eine es was wie auch noch hat sind".split()),
    "en": set("the and is you it this that what are was have just".split()),
}

# Tepki kelimeleri (uzatma düzeltilmiş küçük harf): kahkaha/şaşkınlık/kutlama/başarısızlık
TEPKI_KELIME = set("""oha yuh vay helal efsane inanılmaz inanilmaz geçti gecti yandı yandi ho
bravo alter krass wahnsinn geil boah heftig hammer wow omg wtf lmao lol lul gg rip nice pog
insane""".split())
SORU_KELIME = set("""ne nasıl nasil neden niye kim hangi nerede nerde kaç kac wo wie was warum
wann welche welcher wer wieso weshalb what how why where when which who""".split())
GULME = re.compile(r"(?:h[aeiıo]){2,}|(?:[aeiıo]h){2,}|\bx+d+\b|\blo+l\b|lmao|😂|🤣|😆|😹",
                   re.I)
UZATMA = re.compile(r"([^\W\d_])\1{3,}")           # GEÇTİİİİ, neeeein, HOOOO

KUFUR_ONEK = ("sik", "amk", "amq", "amın", "amin", "amcık", "amcik", "orosp", "yarra",
              "yarak", "piç", "fuck", "shit", "bitch", "kahpe", "pezeven", "yavşak",
              "yavsak", "gavat", "ibne", "puşt", "şerefsiz", "serefsiz", "götveren",
              "gotveren", "dalyarak", "motherf",
              "scheiß", "scheiss", "fick", "arsch", "hurens", "wichs", "fotz", "schlamp",
              "missgeb", "spast")
KUFUR_ISTISNA = ("sikint", "sikil")      # sıkıntı / sıkıldım'ın klavyesiz yazımı
KUFUR_TAM = {"aq", "amk", "mk", "sg", "oç", "oc", "awk", "göt", "got", "götü", "gotu",
             "pic", "sktr", "skrm", "skm", "dick", "ass", "pussy", "wtf", "anan", "ananı",
             "anani", "siktir"}

UNLULER = set("aeıioöuüâîû")
KLAVYE = ("qwe", "wer", "rty", "tyu", "yui", "uıo", "uio", "iop", "asd", "sdf", "dfg",
          "fgh", "ghj", "hjk", "jkl", "klş", "zxc", "xcv", "cvb", "vbn", "bnm")
# QWERTZ; Almanca'da sık geçen "wer", "ert", "rtz" gibi parçalar bilerek yok
KLAVYE_DE = ("qwe", "asd", "sdf", "dfg", "fgh", "ghj", "hjk", "jkl", "yxc", "xcv", "cvb",
             "vbn", "bnm")
KARISIK = re.compile(r"[a-zçğıöşü][A-ZÇĞİÖŞÜ]")   # PepeLaugh, monkaS, catJAM
KENAR = re.compile(r"^[\W_]+|[\W_]+$")
HARF = re.compile(r"^[^\W\d_]+$")


def gurultu_mu(w, klavye=KLAVYE, dil="tr"):
    """Tek harf, ünlem, uzatılmış ses, kahkaha, klavye karalaması."""
    if len(w) < 2 or len(set(w)) == 1 or w in UNLEM:
        return True
    unlu = sum(ch in UNLULER for ch in w)
    if unlu == 0 or set(w) <= UNLULER:                  # hmm, sjsj / aeae
        return True
    # Almanca'da "sch" tek ses, ünsüz kümeleri doğal (schwer, Schritt)
    uzunluk, esik = (len(w.replace("sch", "ʃ")), 0.15) if dil == "de" else (len(w), 0.25)
    if len(w) >= 5 and unlu / uzunluk < esik:           # aksjdksj
        return True
    if len(w) >= 4 and set(w) <= set("haeıiou") and w.count("h") >= 2:   # hahaha, hehe
        return True
    for k in (2, 3, 4):                                 # asdasd, lalala
        if len(w) >= (3 * k if k == 2 else 2 * k) and w == (w[:k] * len(w))[:len(w)]:
            return True
    return any(g in w for g in klavye)


def kufur_mu(w):
    if w in KUFUR_TAM:
        return True
    if w.startswith(KUFUR_ISTISNA):
        return False
    return w.startswith(KUFUR_ONEK)


class Adlandirici:
    def __init__(self, emotelar, dil="tr"):
        self.dil = dil
        # Türkçe büyük/küçük harf (I/ı, İ/i) yalnız Türkçe yayında
        self.lower = tr_lower if dil == "tr" else str.lower
        self.upper = tr_upper if dil == "tr" else str.upper
        self.dolgu = {"tr": DOLGU, "de": DOLGU_DE}.get(dil, DOLGU_EN)
        self.klavye = KLAVYE_DE if dil == "de" else KLAVYE
        self.emote_tam = {e.strip() for e in emotelar if e and e.strip()}
        self.emote_kucuk = {self.lower(KENAR.sub("", e)) for e in self.emote_tam}
        self.emote_kucuk |= YAYGIN_EMOTE
        self.emote_kucuk.discard("")

    def kelime(self, ham):
        """Ad olabilecek kelimeyi küçük harfle döndürür, olamayacaksa None."""
        if ham.startswith("!") or ham in self.emote_tam:
            return None
        t = KENAR.sub("", ham)
        if not t or t in self.emote_tam or KARISIK.search(t):
            return None
        w = self.lower(re.split(r"['’`]", t)[0])          # Messi'nin -> messi
        if not w or w in self.emote_kucuk or not HARF.match(w):
            return None                                   # emote, sayı, sembol
        w = re.sub(r"(.)\1{2,}", r"\1", w)                # gooool -> gol, oooo -> o
        if w in self.emote_kucuk or gurultu_mu(w, self.klavye, self.dil) or kufur_mu(w):
            return None
        return w

    # --- 4. tepki / soru-cevap özellikleri (kesin sınıflandırma değil) ---

    def emote_mu(self, tok):
        t = KENAR.sub("", tok)
        if not t:                                         # yalnız sembol/emoji: 😭 :) 👀
            return True
        return (tok in self.emote_tam or t in self.emote_tam or bool(KARISIK.search(t))
                or self.lower(t) in self.emote_kucuk)

    def tepki_mi(self, metin):
        """Kahkaha, şaşkınlık, kutlama: gülme, uzatma, ???/!!!, yalnız emote, tepki kelimesi."""
        s = metin.strip()
        if not s or s.startswith("!"):
            return False
        if GULME.search(s) or UZATMA.search(s) or re.fullmatch(r"[?!. ]{2,}", s):
            return True
        toks = s.split()
        if all(self.emote_mu(t) for t in toks):
            return True
        kelimeler = {re.sub(r"(.)\1{2,}", r"\1", self.lower(KENAR.sub("", t))) for t in toks}
        return bool(kelimeler & TEPKI_KELIME) or self.lower(s) in ("f", "o7")

    def soru_mu(self, metin):
        """Soru ya da cevap gibi görünen mesaj: ?, @yanıt, soru kelimesi, 1-2 kelimelik düz cevap."""
        s = metin.strip()
        if not s or self.tepki_mi(s):
            return False
        if "?" in s or s.startswith("@"):
            return True
        toks = s.split()
        if self.lower(KENAR.sub("", toks[0])) in SORU_KELIME:
            return True
        return len(toks) <= 2 and all(HARF.match(KENAR.sub("", t) or "-") for t in toks)

    def ifadeler(self, metin):
        if metin.lstrip().startswith("!"):
            return set()
        kel = [self.kelime(h) for h in metin.split()]
        bulunan = set()
        for i, w in enumerate(kel):
            if w and w not in self.dolgu:
                bulunan.add((w,))
            for n in (2, 3):
                p = kel[i:i + n]
                if len(p) < n or None in p:
                    continue
                # "ne oluyor" olur, "oluyor abi" / "gol gol" olmaz; baştaki dolgu
                # yalnız Türkçe'de serbest ("ist in neuseeland" olmaz)
                if p[-1] in self.dolgu or len(set(p)) == 1 or (
                        self.dil != "tr" and p[0] in self.dolgu):
                    continue
                bulunan.add(tuple(p))
        return bulunan

    def ad(self, metinler):
        sayac = Counter()
        for m in metinler:
            sayac.update(self.ifadeler(m))       # her mesaj bir ifadeyi bir kez sayar
        tek = min(((c, p) for p, c in sayac.items() if len(p) == 1),
                  key=lambda x: (-x[0], x[1]), default=None)
        uzun = min(((c, p) for p, c in sayac.items() if len(p) > 1),
                   key=lambda x: (-x[0], -len(x[1]), x[1]), default=None)
        sec = tek
        if uzun:
            c1 = tek[0] if tek else 0
            if uzun[0] >= UZUN_ORAN * c1 and (uzun[0] >= 2 or c1 <= 1):
                sec = uzun
        return self.upper(" ".join(sec[1])) if sec else None


def dil_tahmin(metinler):
    """Yayın dilini sık kelimelerden tahmin eder: 'tr', 'de' ya da 'en'. Eşitlikte 'tr'."""
    say = Counter()
    for m in metinler:
        for w in m.lower().split():
            for dil, isaret in DIL_ISARET.items():
                if w in isaret:
                    say[dil] += 1
    if not say:
        return "tr"
    return max(say, key=lambda d: (say[d], d == "tr"))


# --------------------------------------------------------------------------
# 3-5. Dilim özellikleri, aday puanı ve seçim
# --------------------------------------------------------------------------

KISI_TAVAN = 2          # bir kullanıcının bir dilime en fazla katkısı (mesaj)
IVME_PENCERE = 60       # ivme önceki bu kadar saniyeye göre ölçülür
IVME_TAVAN = 4.0        # 4 kat hızlanma ivme bileşeninden tam puan alır
# başlangıç varsayımı; toplamı 1
AGIRLIK = {"hacim": 0.30, "kisi": 0.20, "ivme": 0.30, "tepki": 0.20}
SORU_ESIK, TEPKI_ESIK = 0.50, 0.25   # soru >= %50 ve tepki < %25 -> soru-cevap olası
SORU_CEZA = 0.6         # soru-cevap olası anın puan çarpanı (silinmez, aşağı iner)


def anlari_sec(kalan, n_dilim, dilim, adci, adet=10, pencerede=None):
    """Dilimleri açıklanabilir puanla sıralar; (özet, seçilen anlar) döndürür."""
    kisi_say = [Counter() for _ in range(n_dilim)]
    sayilan = [[] for _ in range(n_dilim)]            # kişi başı ilk KISI_TAVAN mesaj
    for m in kalan:
        i = min(int(m["t"] // dilim), n_dilim - 1)
        kisi_say[i][m["ad"]] += 1
        if kisi_say[i][m["ad"]] <= KISI_TAVAN:
            sayilan[i].append(m["metin"])
    agir = [len(s) for s in sayilan]
    ort, sd = statistics.fmean(agir), statistics.pstdev(agir)
    esik = ort + 2 * sd
    k = max(1, round(IVME_PENCERE / dilim))
    en_w = max(agir) or 1
    en_k = max(len(c) for c in kisi_say) or 1

    adaylar = []
    for i in range(n_dilim):
        if agir[i] <= esik:
            continue
        onceki = agir[max(0, i - k):i]
        onceki = statistics.fmean(onceki) if onceki else 0.0
        ivme = (agir[i] + ort) / (onceki + ort)
        tepki = sum(map(adci.tepki_mi, sayilan[i])) / agir[i]
        soru = sum(map(adci.soru_mu, sayilan[i])) / agir[i]
        soru_cevap = soru >= SORU_ESIK and tepki < TEPKI_ESIK
        puan = 100 * (AGIRLIK["hacim"] * agir[i] / en_w
                      + AGIRLIK["kisi"] * len(kisi_say[i]) / en_k
                      + AGIRLIK["ivme"] * min(ivme / IVME_TAVAN, 1.0)
                      + AGIRLIK["tepki"] * tepki)
        if soru_cevap:
            puan *= SORU_CEZA
        adaylar.append({"i": i, "t": i * dilim, "ham": sum(kisi_say[i].values()),
                        "kisi": len(kisi_say[i]), "agir": agir[i], "ivme": ivme,
                        "tepki": tepki, "soru": soru, "soru_cevap": soru_cevap,
                        "puan": puan, "metinler": sayilan[i]})

    icerde = [a for a in adaylar if pencerede is None or pencerede(a["t"])]
    icerde.sort(key=lambda a: (-a["puan"], a["i"]))
    secilen = []
    for a in icerde:
        if all(abs(a["t"] - s["t"]) >= ARALIK for s in secilen):
            secilen.append(a)
            if len(secilen) >= adet:
                break
    ozet = {"ort": ort, "sd": sd, "esik": esik, "aday": len(adaylar),
            "disarida": len(adaylar) - len(icerde)}
    return ozet, secilen


# --------------------------------------------------------------------------
# ffmpeg
# --------------------------------------------------------------------------

def video_suresi(yol):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(yol)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        sys.exit(f"ffprobe video süresini okuyamadı: {yol}\n{r.stderr.strip()}")


def klip_kes(video, bas, sure, cikti):
    filtre = ("crop='trunc(min(iw,ih*9/16)/2)*2':'trunc(min(ih,iw*16/9)/2)*2',"
              "scale=1080:1920,setsar=1")
    komut = ["ffmpeg", "-y", "-v", "error", "-ss", f"{bas:.3f}", "-i", str(video),
             "-t", f"{sure:.3f}", "-map", "0:v:0", "-map", "0:a?", "-vf", filtre,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(cikti)]
    r = subprocess.run(komut, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    ffmpeg hatası: {r.stderr.strip()[-500:]}")
        return False
    return True


# --------------------------------------------------------------------------
# Ana akış
# --------------------------------------------------------------------------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Sohbetin patladığı anları bulup 9:16 klip keser.")
    ap.add_argument("sohbet", help="chat.json, .live_chat.json ya da kick.com/<kanal>/videos/<id>")
    ap.add_argument("video", nargs="?", default="yayin.mp4", help="yayın videosu (yayin.mp4)")
    ap.add_argument("--adet", type=int, default=10, help="en fazla an sayısı (10)")
    ap.add_argument("--dilim", type=float, default=15, help="dilim uzunluğu, sn (15)")
    ap.add_argument("--once", type=float, default=25, help="patlamadan kaç sn önce başla (25)")
    ap.add_argument("--sure", type=float, default=40, help="klip süresi, sn (40)")
    ap.add_argument("--video-baslangic", type=float, default=0,
                    help="video yayının bir parçasıysa parçanın yayındaki başlangıç saniyesi")
    ap.add_argument("--sadece-liste", action="store_true",
                    help="klip kesme, yalnız anları listele (video gerekmez)")
    ap.add_argument("--cikti", default="klipler", help="klip klasörü (klipler)")
    a = ap.parse_args()
    if a.adet < 1 or a.dilim <= 0 or a.sure <= 0 or a.once < 0 or a.video_baslangic < 0:
        ap.error("--adet, --dilim, --sure pozitif; --once ve --video-baslangic negatif olmamalı.")

    baslangic = time.time()
    video = Path(a.video)
    if not a.sadece_liste:
        if not video.exists():
            sys.exit(f"Video bulunamadı: {video}  (yalnız liste için --sadece-liste)")
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            sys.exit("ffmpeg / ffprobe PATH'te bulunamadı.")

    # 1. oku
    platform, mesajlar, emotelar, yayin_suresi, twitch_veri = sohbet_oku(a.sohbet)
    if platform == "Twitch":
        emotelar |= twitch_kanal_emotelari(twitch_veri, Path(a.sohbet).resolve().parent)

    # 2. ele
    kalan, elenen = ele(mesajlar)
    if not kalan:
        sys.exit("Sayılacak mesaj kalmadı.")

    # 3. dilimle
    son = max(yayin_suresi or 0, max(m["t"] for m in kalan))
    n_dilim = int(son // a.dilim) + 1
    dil = dil_tahmin(m["metin"] for m in kalan)
    adci = Adlandirici(emotelar, dil)

    # video penceresi (--sadece-liste'de video varsa ve parça belirtildiyse yine bakılır)
    vb = a.video_baslangic
    vsure = None
    if video.exists() and (not a.sadece_liste or vb > 0):
        vsure = video_suresi(video)

    def pencerede(t):
        return vsure is None or vb <= t < vb + vsure

    # 4-5. özellikler, puan, seçim
    ozet, secilen = anlari_sec(kalan, n_dilim, a.dilim, adci, a.adet, pencerede)

    # 6 + 8. klip aralığı ve ad (ad da kişi başı ilk 2 mesajdan)
    anlar = []
    for sira, s in enumerate(secilen, 1):
        ad = adci.ad(s["metinler"]) or f"AN {sira}"
        anlar.append({**s, "sira": sira, "bas": max(0.0, s["t"] - a.once), "sayi": s["ham"],
                      "ad": ad, "dosya": f"{sira:02d}-{ascii_ad(ad, dil)}.mp4"})

    # 10. özet
    print()
    print(f"Platform        : {platform}")
    print(f"Toplam mesaj    : {len(mesajlar)}")
    print(f"Elenen          : {elenen['bot']} bot, {elenen['bildirim']} bildirim, "
          f"{elenen['komut']} komut, {elenen['link']} link, {elenen['selam']} selamlaşma -> sayılan {len(kalan)}")
    print(f"Dil             : {dil}")
    print(f"Dilim           : {n_dilim} x {a.dilim:g} sn")
    print(f"Ortalama        : {ozet['ort']:.2f} mesaj/dilim, kişi başı en fazla {KISI_TAVAN} "
          f"(std {ozet['sd']:.2f})")
    print(f"Eşik (ort+2std) : {ozet['esik']:.2f}  -> {ozet['aday']} aday dilim")
    print(f"Puan            : %{AGIRLIK['hacim']*100:.0f} hacim + %{AGIRLIK['kisi']*100:.0f} kişi"
          f" + %{AGIRLIK['ivme']*100:.0f} ivme + %{AGIRLIK['tepki']*100:.0f} tepki;"
          f" soru-cevap? x{SORU_CEZA}")
    if vsure is not None:
        print(f"Video penceresi : {sure_yaz(vb)} - {sure_yaz(vb + vsure)}"
              f"  ({ozet['disarida']} aday dilim dışarıda, atlandı)")
    print()
    if not anlar:
        print("Eşiği aşan an bulunamadı.")
    for an in anlar:
        print(f"  {an['sira']:02d}  {sure_yaz(an['t'])}  "
              f"(klip {sure_yaz(an['bas'])}-{sure_yaz(an['bas'] + a.sure)})  "
              f"{an['sayi']:3d} msj {an['kisi']:3d} kişi  ivme {an['ivme']:3.1f}x  "
              f"tepki %{an['tepki']*100:3.0f}  soru %{an['soru']*100:3.0f}  "
              f"puan {an['puan']:4.1f}  {an['ad']}{'  [soru-cevap?]' if an['soru_cevap'] else ''}")

    # 7 + 9. kes ve yaz
    toplam_klip = 0.0
    if a.sadece_liste:
        toplam_klip = a.sure * len(anlar)
    elif anlar:
        klasor = Path(a.cikti)
        klasor.mkdir(parents=True, exist_ok=True)
        print()
        satirlar = []
        for an in anlar:
            vbas = max(0.0, an["bas"] - vb)
            uzunluk = min(vsure, an["bas"] + a.sure - vb) - vbas
            if uzunluk <= 0.5:
                print(f"  [{an['sira']:02d}] videoda yer yok, atlandı")
                continue
            hedef = klasor / an["dosya"]
            print(f"  [{an['sira']:02d}/{len(anlar):02d}] kesiliyor -> {hedef}", flush=True)
            if klip_kes(video, vbas, uzunluk, hedef):
                toplam_klip += uzunluk
                satirlar.append(f"{an['dosya']}\t{sure_yaz(an['t'])}\t"
                                f"{an['sayi']} mesaj\t{an['kisi']} kişi\t"
                                f"puan {an['puan']:.1f}\t{an['ad']}")
        (klasor / "anlar.txt").write_text("\n".join(satirlar) + "\n", encoding="utf-8")
        print(f"\nAnlar listesi   : {klasor / 'anlar.txt'}")

    print()
    print(f"Toplam klip süresi : {sure_yaz(toplam_klip)}")
    print(f"Toplam süre        : {time.time() - baslangic:.1f} sn")


if __name__ == "__main__":
    main()
