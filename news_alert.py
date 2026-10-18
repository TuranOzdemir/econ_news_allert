"""
Ücretsiz Ekonomi Haber Botu
---------------------------
RSS kaynaklarından ekonomi haberlerini alır, filtreler, yabancı haberleri
Türkçe'ye çevirir ve Telegram üzerinden kullanıcıya gönderir.

Özellikler:
- Türkçe ve İngilizce ekonomi haberleri
- Finansal haber filtreleme
- Aciliyet ve finansal önem ayrımı
- Telegram komutları (/son, /haber, /guncel, /güncel)
- Tekrarlanan haberleri engelleme
- RSS kaynaklarını paralel ve zaman aşımı korumalı okuma
- Çeviri (deep-translator): zaman aşımı, devre kesici ve orijinal metne dönüş
- Haber geçmişini JSON dosyasında saklama

Gerekli paketler:
    pip install requests feedparser deep-translator

Kullanım:
    python news_alert.py             # normal çalışma
    python news_alert.py --force     # komut beklemeden güncel haberleri gönder
    python news_alert.py --dry-run   # Telegram'a göndermeden terminale yazdır
"""

import os
import sys
import re
import json
import html
import time
import logging
import calendar
import threading

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests
import feedparser

try:
    from deep_translator import GoogleTranslator, MyMemoryTranslator
except ImportError:  # Paket yoksa bot çevirisiz çalışmaya devam eder.
    GoogleTranslator = None
    MyMemoryTranslator = None


# ============================================================
# 1. AYARLAR
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BASE_DIR = Path(__file__).resolve().parent
SEEN_FILE = BASE_DIR / "news_seen.json"

# Haber geçmişinde tutulacak maksimum kayıt sayısı.
MAX_SEEN_NEWS = 3000

# Her RSS kaynağından alınacak maksimum (filtreden geçen) haber sayısı.
MAX_ARTICLES_PER_FEED = 15

# /son komutunda gönderilecek maksimum haber.
MAX_NORMAL_NEWS = 10

# Otomatik çalışmada tek seferde gönderilecek maksimum haber.
# Kalanlar bir sonraki çalıştırmada gönderilir (seen'e eklenmedikleri için).
MAX_AUTO_NEWS = 15

# Otomatik modda bu süreden eski haberler gönderilmez (saat).
# Geçmiş boşken ilk çalıştırmada eski haberlerin akmasını engeller.
MAX_ARTICLE_AGE_HOURS = 24

# Zaman aşımları: (bağlantı, okuma) saniye.
REQUEST_TIMEOUT = (5, 15)
RSS_TIMEOUT = (5, 10)
RSS_RETRIES = 2
RSS_MAX_WORKERS = 5

# Telegram mesajları arasındaki bekleme (saniye) ve 429 yeniden deneme sayısı.
TELEGRAM_SEND_DELAY = 0.5
TELEGRAM_MAX_ATTEMPTS = 3

# Çeviri ayarları.
TRANSLATE_TIMEOUT = 12         # tek çeviri çağrısı için saniye
TRANSLATE_MIN_GAP = 0.3        # çağrılar arası en az bekleme (Google: saniyede 5 istek)
TRANSLATE_BUDGET = 60          # tek çalıştırmada toplam çeviri bütçesi (saniye)
TRANSLATE_MAX_FAILURES = 3     # üst üste bu kadar hatadan sonra çeviri kapatılır
SUMMARY_MAX_CHARS = 250

DRY_RUN = "--dry-run" in sys.argv[1:]

USER_AGENT = "Mozilla/5.0 (compatible; FreeEconomyNewsBot/1.0)"


# ============================================================
# 2. LOG SİSTEMİ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def redact(value):
    """
    Hata mesajlarındaki bot token'ını gizler.

    requests hataları istek URL'sini içerir ve Telegram URL'si
    token'ı barındırır; log'a token sızmasın diye maskeliyoruz.
    """

    text = str(value)

    if TELEGRAM_BOT_TOKEN:
        text = text.replace(TELEGRAM_BOT_TOKEN, "***")

    return text


# ============================================================
# 3. RSS HABER KAYNAKLARI
# ============================================================

# type:
# TR     -> Türkçe kaynak
# GLOBAL -> Uluslararası kaynak

RSS_FEEDS = [
    {
        "url": "https://www.bloomberght.com/rss/tum-haberler.xml",
        "type": "TR",
        "name": "Bloomberg HT",
    },
    {
        "url": "https://www.dunya.com/rss",
        "type": "TR",
        "name": "Dünya Gazetesi",
    },
    {
        "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
        "type": "GLOBAL",
        "name": "CNBC Economy",
    },
    {
        "url": "https://feeds.marketwatch.com/marketwatch/topstories",
        "type": "GLOBAL",
        "name": "MarketWatch",
    },
    {
        "url": "https://www.investing.com/rss/news_14.rss",
        "type": "GLOBAL",
        "name": "Investing.com Economy",
    },
]


# ============================================================
# 4. HABER FİLTRELERİ
# ============================================================

NEGATIVE_KEYWORDS = [
    "football",
    "soccer",
    "celebrity",
    "entertainment",
    "movie review",
    "box office",
    "sports results",
]


TURKISH_FINANCIAL_KEYWORDS = [
    # Genel ekonomi
    "ekonomi", "ekonomik", "büyüme", "resesyon",
    "işsizlik", "istihdam", "bütçe", "bütçe açığı",
    "cari açık", "kamu borcu", "borçlanma",
    "kamu harcaması", "vergi", "vergi artışı",
    "milli gelir", "gayrisafi yurt içi hasıla",

    # Merkez bankaları ve para politikası
    "tcmb", "merkez bankası", "faiz", "faiz kararı",
    "para politikası", "rezerv", "likidite",
    "dolar", "euro", "döviz", "kur", "kur krizi",
    "devalüasyon", "sermaye hareketleri",
    "politika faizi", "faiz indirimi", "faiz artırımı",

    # Enflasyon ve ekonomik veriler
    "enflasyon", "tüfe", "üfe", "fiyat artışı",
    "üretici fiyatları", "tüketici fiyatları",
    "fiyat endeksi", "ekonomik veri",
    "sanayi üretimi", "kapasite kullanımı",
    "perakende satış", "tüketici güveni",

    # Borsa ve finans
    "borsa", "bist", "hisse", "endeks",
    "tahvil", "bono", "getiri", "fon",
    "bankacılık", "finansman", "kredi",
    "takasbank", "halka arz", "temettü",
    "bilanço", "net kâr", "net zarar",
    "sermaye artırımı", "borç yapılandırma",
    "kredi notu", "risk primi", "cds",

    # Altın, enerji ve emtia
    "altın", "gram altın", "gümüş",
    "petrol", "doğalgaz", "doğal gaz",
    "elektrik fiyatı", "enerji fiyatı",
    "emtia", "opec", "brent petrol",
    "ons altın", "bakır fiyatı",

    # Ticaret ve lojistik
    "ticaret", "ihracat", "ithalat", "dış ticaret",
    "ticaret açığı", "ticaret fazlası",
    "gümrük vergisi", "ticaret savaşı",
    "tedarik zinciri", "sanayi üretimi",
    "üretim maliyeti", "lojistik maliyeti",
    "navlun", "liman", "taşımacılık",
    "gümrük", "ihracatçı", "ithalatçı",

    # Küresel gelişmeler
    "abd", "çin", "fed", "powell", "ecb",
    "boj", "pboc", "imf", "dünya bankası",
    "yaptırım", "jeopolitik risk",
]

ENGLISH_FINANCIAL_KEYWORDS = [
    # Macroeconomics
    "economy", "economic growth", "recession",
    "budget deficit", "fiscal deficit",
    "government spending", "government debt",
    "national debt", "public debt",
    "unemployment", "payrolls", "labor market",
    "consumer spending", "retail sales",
    "manufacturing", "industrial production",
    "economic outlook", "economic data",
    "business activity", "consumer confidence",

    # Inflation and economic data
    "inflation", "consumer prices", "producer prices",
    "wholesale prices", "consumer price index",
    "producer price index", "price index",
    "cpi", "ppi", "gdp", "pce",
    "price growth", "price pressures",

    # Central banks and monetary policy
    "federal reserve", "fed", "powell",
    "interest rate", "rate decision",
    "central bank", "monetary policy",
    "rate hike", "rate cut", "yield curve",
    "treasury yield", "bond yield",
    "liquidity", "currency intervention",
    "interest rate decision", "borrowing costs",
    "quantitative easing", "quantitative tightening",

    # Financial markets
    "stock market", "stocks", "equities",
    "s&p 500", "nasdaq", "dow jones",
    "wall street", "market rally",
    "market selloff", "earnings growth",
    "corporate earnings", "bond market",
    "credit market", "banking crisis",
    "financial crisis", "government bonds",
    "treasury", "imf", "world bank",
    "quarterly earnings", "profit warning",
    "stock futures", "market volatility",

    # Currencies and commodities
    "dollar", "dollar index", "yuan", "yen",
    "exchange rate", "forex", "currency",
    "gold", "silver", "crude oil", "oil prices",
    "natural gas", "energy prices", "opec",
    "commodity prices", "safe haven",

    # International trade and geopolitics
    "tariff", "tariffs", "trade war",
    "trade deficit", "trade surplus",
    "trade agreement", "trade policy",
    "sanctions", "supply chain",
    "export", "exports", "import", "imports",
    "trade", "shipping costs", "freight rates",
    "xi jinping", "trump administration",
]

# Acil gelişme olabilecek başlıklar. Tek başına haberin gerçekten
# flaş olduğunu kanıtlamaz; yalnızca önceliği yükseltmek için kullanılır.
FLASH_KEYWORDS = [
    "son dakika",
    "acil açıklama",
    "acil müdahale",
    "faiz kararı",
    "rate decision",
    "emergency meeting",
    "emergency rate",
    "unscheduled meeting",
    "intervention",
    "interest rate decision",
    "unexpected rate",
    "rate hike",
    "rate cut",
    "fed decision",
    "fomc decision",
    "tcmb decision",
    "central bank decision",
    "surprise inflation",
    "market intervention",
]

# Başlıkta geçerse önceliği yükselten, yatırım açısından önemli konular.
HIGH_PRIORITY_KEYWORDS = [
    "fed",
    "federal reserve",
    "powell",
    "tcmb",
    "merkez bankası",
    "ecb",
    "boj",
    "pboc",
    "faiz kararı",
    "interest rate decision",
    "inflation",
    "enflasyon",
    "cpi",
    "tariff",
    "gümrük vergisi",
    "trade war",
    "ticaret savaşı",
    "sanctions",
    "yaptırım",
    "intervention",
    "rezerv",
    "currency crisis",
    "kur krizi",
]


# ============================================================
# 5. YARDIMCI FONKSİYONLAR
# ============================================================

def normalize_text(text):
    """Metni küçük harfe çevirir ve gereksiz boşlukları temizler."""

    text = (text or "").lower()
    return re.sub(r"\s+", " ", text).strip()


def contains_keyword(text, keyword):
    """
    Anahtar kelimenin metinde bulunup bulunmadığını kontrol eder.

    Tek kelimelerde kelime sınırı kullanılır ('war' -> 'warning'
    eşleşmez). Çok kelimeli ifadelerde doğrudan metin eşleşmesi yapılır.
    """

    text = normalize_text(text)
    keyword = normalize_text(keyword)

    if not text or not keyword:
        return False

    if " " in keyword:
        return keyword in text

    pattern = rf"(?<!\w){re.escape(keyword)}(?!\w)"

    return re.search(pattern, text, flags=re.UNICODE) is not None


def contains_any_keyword(text, keywords):
    """Anahtar kelimelerden en az biri metinde varsa True döndürür."""

    return any(
        contains_keyword(text, keyword)
        for keyword in keywords
    )


def get_news_priority(title):
    """HIGH veya NORMAL öncelik döndürür (tahmine dayalı basit sistem)."""

    if contains_any_keyword(title, HIGH_PRIORITY_KEYWORDS):
        return "HIGH"

    return "NORMAL"


def is_flash_news(title):
    """Başlık acil gelişme ifadelerinden birini içeriyor mu?"""

    return contains_any_keyword(title, FLASH_KEYWORDS)


TAG_RE = re.compile(r"<[^>]+>")


def clean_text(raw):
    """RSS özetindeki HTML etiketlerini ve HTML kodlarını temizler."""

    text = TAG_RE.sub(" ", raw or "")
    text = html.unescape(text)

    return re.sub(r"\s+", " ", text).strip()


def shorten_text(text, limit=SUMMARY_MAX_CHARS):
    """Metni kelime sınırında keserek en fazla `limit` karaktere indirir."""

    if len(text) <= limit:
        return text

    cut = text[:limit].rsplit(" ", 1)[0]

    return cut.rstrip(" ,;:.-") + "…"


# Haber kimliği için temizlenecek takip parametreleri.
TRACKING_PARAMS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "cmpid", "ocid", "ref_src",
}


def normalize_link(link):
    """
    Bağlantıdan takip parametrelerini ve #parça kısmını çıkarır.

    Aynı haber farklı utm parametreleriyle gelirse tekrar
    gönderilmez.
    """

    link = (link or "").strip()

    try:
        parts = urlsplit(link)

        query = [
            (key, value)
            for key, value in parse_qsl(
                parts.query, keep_blank_values=True
            )
            if not key.lower().startswith("utm_")
            and key.lower() not in TRACKING_PARAMS
        ]

        return urlunsplit((
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            urlencode(query),
            "",
        ))

    except ValueError:
        return link


def http_get(url, timeout, retries=RSS_RETRIES, **kwargs):
    """
    GET isteği atar; geçici hatalarda (bağlantı, zaman aşımı, 5xx, 429)
    artan beklemeyle yeniden dener. 403/404 gibi kalıcı hatalarda
    hemen vazgeçer.
    """

    last_error = None

    for attempt in range(retries + 1):
        try:
            response = requests.get(url, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response

        except requests.HTTPError as error:
            status = error.response.status_code if error.response else 0

            if status < 500 and status != 429:
                raise

            last_error = error

        except requests.RequestException as error:
            last_error = error

        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))

    raise last_error


# ============================================================
# 6. HABER FİLTRELEME
# ============================================================

def is_valid_news(title, feed_type, summary=""):
    """
    Haber başlığını ve açıklamasını birlikte değerlendirir.

    Negatif filtre yalnızca başlıkta uygulanır.
    """

    title_text = normalize_text(title)
    summary_text = normalize_text(summary)

    if not title_text:
        return False

    if contains_any_keyword(title_text, NEGATIVE_KEYWORDS):
        return False

    combined_text = f"{title_text} {summary_text}".strip()

    if feed_type == "TR":
        keywords = TURKISH_FINANCIAL_KEYWORDS

    elif feed_type == "GLOBAL":
        keywords = ENGLISH_FINANCIAL_KEYWORDS

    else:
        return False

    return contains_any_keyword(combined_text, keywords)


# ============================================================
# 7. KALICI HABER GEÇMİŞİ
# ============================================================

def load_state():
    """
    Haber geçmişini ve Telegram komutlarının son ID'sini yükler.

    Eski sürümde dosyada doğrudan bir liste varsa onu da okur.
    """

    default_state = {
        "seen_ids": [],
        "last_update_id": 0,
    }

    if not SEEN_FILE.exists():
        return default_state

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            return {
                "seen_ids": data,
                "last_update_id": 0,
            }

        if isinstance(data, dict):
            seen_ids = data.get("seen_ids", [])
            last_update_id = data.get("last_update_id", 0)

            return {
                "seen_ids": (
                    seen_ids if isinstance(seen_ids, list) else []
                ),
                "last_update_id": (
                    last_update_id
                    if isinstance(last_update_id, int)
                    else 0
                ),
            }

    except (OSError, json.JSONDecodeError) as error:
        logger.warning("Haber geçmişi okunamadı: %s", error)

    return default_state


def save_state(state):
    """
    Haber geçmişini JSON dosyasına kaydeder.

    Önce geçici dosyaya yazar, sonra asıl dosyanın yerine geçirir.
    Son MAX_SEEN_NEWS kayıt tutulur (liste sıralı olduğu için en eskiler
    atılır).
    """

    if DRY_RUN:
        return

    state["seen_ids"] = state["seen_ids"][-MAX_SEEN_NEWS:]

    temporary_file = SEEN_FILE.with_suffix(".tmp")

    try:
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(
                state,
                file,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(temporary_file, SEEN_FILE)

    except OSError as error:
        logger.error("Haber geçmişi kaydedilemedi: %s", error)


# ============================================================
# 8. ÇEVİRİ
# ============================================================

class Translator:
    """
    deep-translator için zaman aşımı korumalı sarmalayıcı.

    deep-translator içindeki HTTP isteğinde zaman aşımı yoktur ve
    Google yanıt vermezse süresiz bekleyebilir. Bu yüzden çeviri ayrı bir
    daemon thread'de çalıştırılır ve belirli süre sonra beklemekten
    vazgeçilir.

    - Çeviri başarısız olursa orijinal metin döndürülür.
    - Art arda çok hata olursa veya toplam süre bütçesi dolarsa çeviri
      bu çalıştırma için kapatılır; böylece akış asla uzun süre takılmaz.
    """

    def __init__(self):
        self.enabled = (
            GoogleTranslator is not None
            or MyMemoryTranslator is not None
        )
        self.failures = 0
        self.spent = 0.0
        self.cache = {}
        self.disabled = set()   # hız sınırına takılan sağlayıcılar
        self.last_call = 0.0

        if not self.enabled:
            logger.warning(
                "deep-translator bulunamadı; çeviri devre dışı."
            )

    def translate(self, text):
        if not text:
            return text

        if text in self.cache:
            return self.cache[text]

        if not self.enabled:
            return text

        if self.failures >= TRANSLATE_MAX_FAILURES:
            return text

        if self.spent >= TRANSLATE_BUDGET:
            return text

        # Önce Google, hız sınırı veya hata olursa MyMemory denenir.
        providers = []

        if GoogleTranslator is not None:
            providers.append((
                "google",
                lambda: GoogleTranslator(source="auto", target="tr"),
            ))

        if MyMemoryTranslator is not None:
            providers.append((
                "mymemory",
                lambda: MyMemoryTranslator(
                    source="en-US",
                    target="tr-TR",
                ),
            ))

        result = {}

        def worker():
            errors = []

            for name, factory in providers:

                if name in self.disabled:
                    continue

                try:
                    output = factory().translate(text)

                    # MyMemory günlük kota dolunca hata yerine uyarı
                    # metni döndürebilir; bunu çeviri sayma.
                    if (
                        output
                        and output.strip()
                        and "MYMEMORY WARNING" not in output.upper()
                    ):
                        result["text"] = output
                        return

                    errors.append(f"{name}: boş/uyarı yanıtı")

                except Exception as error:  # noqa: BLE001
                    errors.append(f"{name}: {error}")

                    # Hız sınırına takıldıysa bu çalıştırmada
                    # aynı sağlayıcıyı tekrar deneme.
                    if "too many requests" in str(error).lower():
                        self.disabled.add(name)

            result["error"] = "; ".join(errors) or "çevirmen yok"

        # Google'ın saniyede 5 istek sınırını aşmamak için aralık bırak.
        wait = TRANSLATE_MIN_GAP - (time.monotonic() - self.last_call)

        if wait > 0:
            time.sleep(wait)

        self.last_call = time.monotonic()

        started = time.monotonic()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(TRANSLATE_TIMEOUT)

        self.spent += time.monotonic() - started

        translated = (result.get("text") or "").strip()

        if thread.is_alive() or not translated:
            self.failures += 1

            logger.warning(
                "Çeviri başarısız (%s/%s): %s",
                self.failures,
                TRANSLATE_MAX_FAILURES,
                "zaman aşımı"
                if thread.is_alive()
                else result.get("error", "boş yanıt"),
            )

            return text

        self.failures = 0
        self.cache[text] = translated

        return translated


def prepare_article(article, translator):
    """
    Gönderilecek habere çeviri alanlarını ekler.

    Çeviri yalnızca gerçekten gönderilecek yabancı haberlere uygulanır;
    böylece gereksiz çağrı yapılmaz.
    """

    article["display_title"] = article["title"]
    article["display_summary"] = article["summary"]

    if article["type"] == "GLOBAL":
        article["display_title"] = translator.translate(article["title"])
        article["display_summary"] = translator.translate(
            article["summary"]
        )

    return article


# ============================================================
# 9. TELEGRAM MESAJ GÖNDERME
# ============================================================

def send_telegram_message(message):
    """
    Telegram'a mesaj gönderir.

    Başarılıysa True, başarısızsa False döndürür. 429 (hız sınırı)
    durumunda Telegram'ın bildirdiği süre kadar bekleyip yeniden dener.
    """

    if DRY_RUN:
        print("-" * 60)
        print(message)
        return True

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error(
            "Telegram token veya chat ID eksik. "
            "Ortam değişkenlerini kontrol et."
        )
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    for attempt in range(1, TELEGRAM_MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 429:
                try:
                    retry_after = int(
                        response.json()
                        .get("parameters", {})
                        .get("retry_after", 5)
                    )
                except (ValueError, TypeError):
                    retry_after = 5

                logger.warning(
                    "Telegram hız sınırı; %s sn bekleniyor.",
                    retry_after,
                )
                time.sleep(min(retry_after, 30))
                continue

            response.raise_for_status()

            result = response.json()

            if not result.get("ok"):
                logger.error(
                    "Telegram API mesajı kabul etmedi: %s",
                    result.get("description", "Bilinmeyen hata"),
                )
                return False

            return True

        except requests.RequestException as error:
            logger.error(
                "Telegram bağlantı hatası (deneme %s/%s): %s",
                attempt,
                TELEGRAM_MAX_ATTEMPTS,
                redact(error),
            )

            # Kalıcı istemci hatalarında (400, 401, 403) yeniden deneme.
            response_obj = getattr(error, "response", None)

            if (
                response_obj is not None
                and 400 <= response_obj.status_code < 500
            ):
                return False

            time.sleep(2 * attempt)

        except ValueError as error:
            logger.error("Telegram yanıtı okunamadı: %s", error)
            return False

    return False


def format_news_message(article):
    """
    Haberi Telegram HTML biçiminde güvenli şekilde biçimlendirir.

    Yabancı haberlerde çevrilmiş başlık ve özet kullanılır
    (prepare_article çağrılmadıysa orijinal metin gösterilir).
    """

    title = html.escape(
        article.get("display_title") or article["title"]
    )
    summary = html.escape(
        article.get("display_summary", article.get("summary", ""))
    )
    source = html.escape(article["source"])
    link = html.escape(article["link"], quote=True)

    if article["flash"]:
        heading = "🚨 <b>ÖNCELİKLİ GELİŞME</b>"
    elif article["priority"] == "HIGH":
        heading = "📌 <b>ÖNEMLİ EKONOMİ HABERİ</b>"
    else:
        heading = "📊 <b>Ekonomi Haberi</b>"

    parts = [
        heading,
        "",
        f"📍 <i>{source}</i>",
        f"📰 <b>{title}</b>",
    ]

    if summary:
        parts.extend(["", summary])

    parts.extend(["", f'🔗 <a href="{link}">Haberi oku</a>'])

    return "\n".join(parts)


# ============================================================
# 10. RSS HABERLERİNİ OKUMA
# ============================================================

def fetch_feed(feed):
    """
    Tek bir RSS kaynağını okur ve uygun haberleri döndürür.

    Hata durumunda boş liste döner; böylece bir kaynağın sorunu
    diğerlerini etkilemez.
    """

    articles = []

    try:
        logger.info("RSS kaynağı okunuyor: %s", feed["name"])

        response = http_get(
            feed["url"],
            RSS_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )

        parsed = feedparser.parse(response.content)

        if not parsed.entries:
            logger.warning(
                "%s kaynağında haber bulunamadı.",
                feed["name"],
            )
            return []

        for entry in parsed.entries:

            if len(articles) >= MAX_ARTICLES_PER_FEED:
                break

            title = clean_text(entry.get("title", ""))
            link = entry.get("link", "").strip()

            summary_raw = (
                entry.get("summary", "")
                or entry.get("description", "")
            )
            summary_full = clean_text(summary_raw)

            if not title or not link:
                continue

            if not is_valid_news(title, feed["type"], summary_full):
                continue

            published = entry.get("published_parsed") or entry.get(
                "updated_parsed"
            )

            published_ts = (
                calendar.timegm(published) if published else None
            )

            articles.append({
                "title": title,
                "link": link,
                "id": normalize_link(link),
                "source": feed["name"],
                "type": feed["type"],
                "summary": shorten_text(summary_full),
                "priority": get_news_priority(title),
                "flash": is_flash_news(title),
                "published_ts": published_ts,
            })

    except requests.RequestException as error:
        logger.error(
            "RSS bağlantı hatası (%s): %s",
            feed["name"],
            redact(error),
        )

    except Exception:  # noqa: BLE001
        logger.exception(
            "RSS işlenirken hata oluştu: %s",
            feed["name"],
        )

    return articles


def fetch_news():
    """
    Tüm RSS kaynaklarını paralel okur, tekrarları ayıklar ve
    önceliğe göre sıralar.

    Toplam süre, tüm kaynakların toplamı yerine en yavaş kaynağa yakın
    olur.
    """

    with ThreadPoolExecutor(max_workers=RSS_MAX_WORKERS) as pool:
        results = list(pool.map(fetch_feed, RSS_FEEDS))

    articles = []
    seen_ids = set()
    seen_titles = set()

    for feed_articles in results:
        for article in feed_articles:

            title_key = normalize_text(article["title"])

            # Aynı bağlantı veya aynı başlık (farklı kaynaktan) tekrar alınmaz.
            if article["id"] in seen_ids or title_key in seen_titles:
                continue

            seen_ids.add(article["id"])
            seen_titles.add(title_key)
            articles.append(article)

    # Önce flaş, sonra yüksek öncelik, aynı grupta en yeni önce.
    articles.sort(
        key=lambda article: (
            not article["flash"],
            article["priority"] != "HIGH",
            -(article["published_ts"] or 0),
        )
    )

    logger.info(
        "Filtreleme tamamlandı. Uygun haber sayısı: %s",
        len(articles),
    )

    return articles


def is_recent(article):
    """Haber yaş sınırı içinde mi? Yayın tarihi yoksa kabul edilir."""

    published_ts = article["published_ts"]

    if published_ts is None:
        return True

    age_seconds = time.time() - published_ts

    return age_seconds <= MAX_ARTICLE_AGE_HOURS * 3600


# ============================================================
# 11. TELEGRAM KOMUTLARINI KONTROL ETME
# ============================================================

def check_telegram_commands(state):
    """
    Telegram'daki /son, /haber, /guncel komutlarını kontrol eder.

    last_update_id sayesinde işlenen mesajlar tekrar değerlendirilmez.
    Bot'ta webhook etkinse getUpdates çalışmaz.
    """

    if DRY_RUN or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    )

    params = {
        "offset": state["last_update_id"] + 1,
        "timeout": 0,
        "allowed_updates": json.dumps(["message"]),
    }

    force_send = False

    try:
        response = http_get(
            url,
            REQUEST_TIMEOUT,
            retries=1,
            params=params,
        )

        result = response.json()

        if not result.get("ok"):
            logger.error(
                "Telegram komut kontrolü başarısız: %s",
                result.get("description", "Bilinmeyen hata"),
            )
            return False

        for update in result.get("result", []):

            state["last_update_id"] = max(
                state["last_update_id"],
                update["update_id"],
            )

            message = update.get("message", {})

            # Yalnızca belirlediğimiz sohbetten gelen komutları kabul et.
            chat_id = str(message.get("chat", {}).get("id", ""))

            if chat_id != str(TELEGRAM_CHAT_ID):
                continue

            text = (message.get("text") or "").strip().lower()

            # /son@BotAdi gibi grup komutlarını da tanı.
            command = text.split()[0] if text else ""
            command = command.split("@")[0]

            if command in ("/son", "/haber", "/guncel", "/güncel"):
                force_send = True

        save_state(state)

    except requests.RequestException as error:
        logger.error(
            "Telegram komut bağlantı hatası: %s",
            redact(error),
        )

    except (ValueError, KeyError) as error:
        logger.error(
            "Telegram komut yanıtı işlenemedi: %s",
            error,
        )

    return force_send


# ============================================================
# 12. ANA PROGRAM
# ============================================================

def main():

    logger.info("Ekonomi haber botu başlatıldı.")

    if not DRY_RUN and (not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID):
        logger.error(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tanımlı değil."
        )
        sys.exit(1)

    state = load_state()

    # Sıralı ve tekrarsız geçmiş. Set yalnızca hızlı arama içindir;
    # kayıt sırası liste ile korunur ki en eski kayıtlar atılabilsin.
    seen_list = list(dict.fromkeys(state["seen_ids"]))
    seen_set = set(seen_list)

    def is_seen(article):
        # Eski sürümde ham bağlantı kaydediliyordu; ikisini de kontrol et.
        return (
            article["id"] in seen_set
            or article["link"] in seen_set
        )

    def mark_seen(article):
        if article["id"] not in seen_set:
            seen_set.add(article["id"])
            seen_list.append(article["id"])

    try:
        force_send = check_telegram_commands(state)

        if "--force" in sys.argv[1:]:
            force_send = True

        articles = fetch_news()
        translator = Translator()

        # ----------------------------------------------------
        # A) İSTEK ÜZERİNE HABER GÖNDERİMİ
        # ----------------------------------------------------

        if force_send:

            if not articles:
                send_telegram_message(
                    "⚠️ Şu an filtrelere uygun haber bulunamadı."
                )

            else:
                send_telegram_message(
                    "🌐 <b>Ekonomi Haberleri</b>\n"
                    "Filtrelenmiş güncel haberler:"
                )

                sent_count = 0

                for article in articles[:MAX_NORMAL_NEWS]:

                    prepare_article(article, translator)

                    if send_telegram_message(
                        format_news_message(article)
                    ):
                        mark_seen(article)
                        sent_count += 1

                    time.sleep(0 if DRY_RUN else TELEGRAM_SEND_DELAY)

                logger.info(
                    "İstek üzerine gönderilen haber: %s",
                    sent_count,
                )

        # ----------------------------------------------------
        # B) NORMAL OTOMATİK HABER GÖNDERİMİ
        # ----------------------------------------------------

        else:

            already_seen = [a for a in articles if is_seen(a)]
            too_old = [
                a for a in articles
                if not is_seen(a) and not is_recent(a)
            ]

            candidates = [
                article
                for article in articles
                if not is_seen(article) and is_recent(article)
            ]

            logger.info(
                "Toplam %s haber: %s zaten gönderilmiş, "
                "%s %s saatten eski, %s yeni aday.",
                len(articles),
                len(already_seen),
                len(too_old),
                MAX_ARTICLE_AGE_HOURS,
                len(candidates),
            )

            to_send = candidates[:MAX_AUTO_NEWS]

            if len(candidates) > len(to_send):
                logger.info(
                    "%s haber sonraki çalıştırmaya bırakıldı.",
                    len(candidates) - len(to_send),
                )

            sent_count = 0

            for article in to_send:

                prepare_article(article, translator)

                if send_telegram_message(format_news_message(article)):
                    mark_seen(article)
                    sent_count += 1

                time.sleep(0 if DRY_RUN else TELEGRAM_SEND_DELAY)

            logger.info(
                "Yeni gönderilen haber sayısı: %s",
                sent_count,
            )

    finally:
        # İş yarıda kesilse bile gönderilmiş haberler kaydedilir;
        # böylece bir sonraki çalıştırmada tekrar gönderilmez.
        state["seen_ids"] = seen_list
        save_state(state)

    logger.info("Bot çalışmasını tamamladı.")


if __name__ == "__main__":
    main()
