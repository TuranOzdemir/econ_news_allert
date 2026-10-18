"""
Ücretsiz Ekonomi Haber Botu
---------------------------
RSS ve BOJ Basın Açıklamalarından ekonomi haberlerini alır, filtreler, 
yabancı haberleri ve BOJ PDF özetlerini Türkçe'ye çevirir 
ve Telegram üzerinden kullanıcıya gönderir.

Gerekli paketler:
    pip install requests feedparser deep-translator beautifulsoup4 pypdf
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
import io
import urllib.parse
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import feedparser
from bs4 import BeautifulSoup
from pypdf import PdfReader

try:
    from deep_translator import GoogleTranslator, MyMemoryTranslator
except ImportError:
    GoogleTranslator = None
    MyMemoryTranslator = Nones


# ============================================================
# 1. AYARLAR
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BASE_DIR = Path(__file__).resolve().parent
SEEN_FILE = BASE_DIR / "news_seen.json"

MAX_SEEN_NEWS = 3000
MAX_ARTICLES_PER_FEED = 15
MAX_NORMAL_NEWS = 10
MAX_AUTO_NEWS = 15
MAX_ARTICLE_AGE_HOURS = 24

REQUEST_TIMEOUT = (5, 15)
RSS_TIMEOUT = (5, 10)
RSS_RETRIES = 2
RSS_MAX_WORKERS = 5

TELEGRAM_SEND_DELAY = 0.5
TELEGRAM_MAX_ATTEMPTS = 3

TRANSLATE_TIMEOUT = 12
TRANSLATE_MIN_GAP = 0.3
TRANSLATE_BUDGET = 60
TRANSLATE_MAX_FAILURES = 3
SUMMARY_MAX_CHARS = 250

DRY_RUN = "--dry-run" in sys.argv[1:]
USER_AGENT = "Mozilla/5.0 (compatible; FreeEconomyNewsBot/1.0)"

# BOJ (Japonya Merkez Bankası) Basın Açıklamaları Kaynağı
BOJ_PRESS_URL = "https://www.boj.or.jp/en/about/press/"
BOJ_BASE_URL = "https://www.boj.or.jp"


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
    text = str(value)
    if TELEGRAM_BOT_TOKEN:
        text = text.replace(TELEGRAM_BOT_TOKEN, "***")
    return text


# ============================================================
# 3. RSS VE ÖZEL HABER KAYNAKLARI
# ============================================================

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
    "football", "soccer", "celebrity", "entertainment",
    "movie review", "box office", "sports results",
]

TURKISH_FINANCIAL_KEYWORDS = [
    "ekonomi", "ekonomik", "büyüme", "resesyon", "işsizlik", "istihdam",
    "bütçe", "bütçe açığı", "cari açık", "kamu borcu", "borçlanma",
    "kamu harcaması", "vergi", "vergi artışı", "milli gelir",
    "gayrisafi yurt içi hasıla", "tcmb", "merkez bankası", "faiz",
    "faiz kararı", "para politikası", "rezerv", "likidite", "dolar",
    "euro", "döviz", "kur", "kur krizi", "devalüasyon", "sermaye hareketleri",
    "politika faizi", "faiz indirimi", "faiz artırımı", "enflasyon", "tüfe",
    "üfe", "fiyat artışı", "üretici fiyatları", "tüketici fiyatları",
    "fiyat endeksi", "ekonomik veri", "sanayi üretimi", "kapasite kullanımı",
    "perakende satış", "tüketici güveni", "borsa", "bist", "hisse", "endeks",
    "tahvil", "bono", "getiri", "fon", "bankacılık", "finansman", "kredi",
    "takasbank", "halka arz", "temettü", "bilanço", "net kâr", "net zarar",
    "sermaye artırımı", "borç yapılandırma", "kredi notu", "risk primi",
    "cds", "altın", "gram altın", "gümüş", "petrol", "doğalgaz", "doğal gaz",
    "elektrik fiyatı", "enerji fiyatı", "emtia", "opec", "brent petrol",
    "ons altın", "bakır fiyatı", "ticaret", "ihracat", "ithalat", "dış ticaret",
    "ticaret açığı", "ticaret fazlası", "gümrük vergisi", "ticaret savaşı",
    "tedarik zinciri", "sanayi üretimi", "üretim maliyeti", "lojistik maliyeti",
    "navlun", "liman", "taşımacılık", "gümrük", "ihracatçı", "ithalatçı",
    "abd", "çin", "fed", "powell", "ecb", "boj", "pboc", "imf", "dünya bankası",
    "yaptırım", "jeopolitik risk",
]

ENGLISH_FINANCIAL_KEYWORDS = [
    "economy", "economic growth", "recession", "budget deficit",
    "fiscal deficit", "government spending", "government debt",
    "national debt", "public debt", "unemployment", "payrolls",
    "labor market", "consumer spending", "retail sales", "manufacturing",
    "industrial production", "economic outlook", "economic data",
    "business activity", "consumer confidence", "inflation",
    "consumer prices", "producer prices", "wholesale prices",
    "consumer price index", "producer price index", "price index",
    "cpi", "ppi", "gdp", "pce", "price growth", "price pressures",
    "federal reserve", "fed", "powell", "interest rate", "rate decision",
    "central bank", "monetary policy", "rate hike", "rate cut", "yield curve",
    "treasury yield", "bond yield", "liquidity", "currency intervention",
    "interest rate decision", "borrowing costs", "quantitative easing",
    "quantitative tightening", "stock market", "stocks", "equities",
    "s&p 500", "nasdaq", "dow jones", "wall street", "market rally",
    "market selloff", "earnings growth", "corporate earnings", "bond market",
    "credit market", "banking crisis", "financial crisis", "government bonds",
    "treasury", "imf", "world bank", "quarterly earnings", "profit warning",
    "stock futures", "market volatility", "dollar", "dollar index", "yuan",
    "yen", "exchange rate", "forex", "currency", "gold", "silver",
    "crude oil", "oil prices", "natural gas", "energy prices", "opec",
    "commodity prices", "safe haven", "tariff", "tariffs", "trade war",
    "trade deficit", "trade surplus", "trade agreement", "trade policy",
    "sanctions", "supply chain", "export", "exports", "import", "imports",
    "trade", "shipping costs", "freight rates", "xi jinping", "trump administration",
]

FLASH_KEYWORDS = [
    "son dakika", "acil açıklama", "acil müdahale", "faiz kararı",
    "rate decision", "emergency meeting", "emergency rate",
    "unscheduled meeting", "intervention", "interest rate decision",
    "unexpected rate", "rate hike", "rate cut", "fed decision",
    "fomc decision", "tcmb decision", "central bank decision",
    "surprise inflation", "market intervention", "boj policy",
]

HIGH_PRIORITY_KEYWORDS = [
    "fed", "federal reserve", "powell", "tcmb", "merkez bankası",
    "ecb", "boj", "pboc", "faiz kararı", "interest rate decision",
    "inflation", "enflasyon", "cpi", "tariff", "gümrük vergisi",
    "trade war", "ticaret savaşı", "sanctions", "yaptırım",
    "intervention", "rezerv", "currency crisis", "kur krizi",
]


# ============================================================
# 5. YARDIMCI FONKSİYONLAR
# ============================================================

def normalize_text(text):
    text = (text or "").lower()
    return re.sub(r"\s+", " ", text).strip()


def contains_keyword(text, keyword):
    text = normalize_text(text)
    keyword = normalize_text(keyword)
    if not text or not keyword:
        return False
    if " " in keyword:
        return keyword in text
    pattern = rf"(?<!\w){re.escape(keyword)}(?!\w)"
    return re.search(pattern, text, flags=re.UNICODE) is not None


def contains_any_keyword(text, keywords):
    return any(contains_keyword(text, keyword) for keyword in keywords)


def get_news_priority(title):
    if contains_any_keyword(title, HIGH_PRIORITY_KEYWORDS):
        return "HIGH"
    return "NORMAL"


def is_flash_news(title):
    return contains_any_keyword(title, FLASH_KEYWORDS)


TAG_RE = re.compile(r"<[^>]+>")


def clean_text(raw):
    text = TAG_RE.sub(" ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def shorten_text(text, limit=SUMMARY_MAX_CHARS):
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.-") + "…"


TRACKING_PARAMS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "cmpid", "ocid", "ref_src",
}


def normalize_link(link):
    link = (link or "").strip()
    try:
        parts = urlsplit(link)
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
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
            return {"seen_ids": data, "last_update_id": 0}
        if isinstance(data, dict):
            seen_ids = data.get("seen_ids", [])
            last_update_id = data.get("last_update_id", 0)
            return {
                "seen_ids": seen_ids if isinstance(seen_ids, list) else [],
                "last_update_id": last_update_id if isinstance(last_update_id, int) else 0,
            }
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("Haber geçmişi okunamadı: %s", error)

    return default_state


def save_state(state):
    if DRY_RUN:
        return
    state["seen_ids"] = state["seen_ids"][-MAX_SEEN_NEWS:]
    temporary_file = SEEN_FILE.with_suffix(".tmp")
    try:
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
        os.replace(temporary_file, SEEN_FILE)
    except OSError as error:
        logger.error("Haber geçmişi kaydedilemedi: %s", error)


# ============================================================
# 8. ÇEVİRİ
# ============================================================

class Translator:
    def __init__(self):
        self.enabled = (
            GoogleTranslator is not None
            or MyMemoryTranslator is not None
        )
        self.failures = 0
        self.spent = 0.0
        self.cache = {}
        self.disabled = set()
        self.last_call = 0.0

        if not self.enabled:
            logger.warning("deep-translator bulunamadı; çeviri devre dışı.")

    def translate(self, text):
        if not text:
            return text
        if text in self.cache:
            return self.cache[text]
        if not self.enabled or self.failures >= TRANSLATE_MAX_FAILURES or self.spent >= TRANSLATE_BUDGET:
            return text

        providers = []
        if GoogleTranslator is not None:
            providers.append(("google", lambda: GoogleTranslator(source="auto", target="tr")))
        if MyMemoryTranslator is not None:
            providers.append(("mymemory", lambda: MyMemoryTranslator(source="en-US", target="tr-TR")))

        result = {}

        def worker():
            errors = []
            for name, factory in providers:
                if name in self.disabled:
                    continue
                try:
                    output = factory().translate(text)
                    if output and output.strip() and "MYMEMORY WARNING" not in output.upper():
                        result["text"] = output
                        return
                    errors.append(f"{name}: boş/uyarı yanıtı")
                except Exception as error:
                    errors.append(f"{name}: {error}")
                    if "too many requests" in str(error).lower():
                        self.disabled.add(name)
            result["error"] = "; ".join(errors) or "çevirmen yok"

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
            logger.warning("Çeviri başarısız: %s", result.get("error", "zaman aşımı"))
            return text

        self.failures = 0
        self.cache[text] = translated
        return translated


def prepare_article(article, translator):
    article["display_title"] = article["title"]
    article["display_summary"] = article["summary"]

    if article["type"] == "GLOBAL":
        article["display_title"] = translator.translate(article["title"])
        article["display_summary"] = translator.translate(article["summary"])

    return article


# ============================================================
# 9. TELEGRAM MESAJ GÖNDERME
# ============================================================

def send_telegram_message(message):
    if DRY_RUN:
        print("-" * 60)
        print(message)
        return True

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram token veya chat ID eksik.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    for attempt in range(1, TELEGRAM_MAX_ATTEMPTS + 1):
        try:
            response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            if response.status_code == 429:
                try:
                    retry_after = int(response.json().get("parameters", {}).get("retry_after", 5))
                except (ValueError, TypeError):
                    retry_after = 5
                time.sleep(min(retry_after, 30))
                continue

            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                return False
            return True
        except requests.RequestException as error:
            response_obj = getattr(error, "response", None)
            if response_obj is not None and 400 <= response_obj.status_code < 500:
                return False
            time.sleep(2 * attempt)
        except ValueError:
            return False
    return False


def format_news_message(article):
    title = html.escape(article.get("display_title") or article["title"])
    summary = html.escape(article.get("display_summary", article.get("summary", "")))
    source = html.escape(article["source"])
    link = html.escape(article["link"], quote=True)

    if article["flash"]:
        heading = "🚨 <b>ÖNCELİKLİ GELİŞME</b>"
    elif article["priority"] == "HIGH":
        heading = "📌 <b>ÖNEMLİ EKONOMİ HABERİ</b>"
    else:
        heading = "📊 <b>Ekonomi Haberi</b>"

    parts = [heading, "", f"📍 <i>{source}</i>", f"📰 <b>{title}</b>"]
    if summary:
        parts.extend(["", summary])
    parts.extend(["", f'🔗 <a href="{link}">Haberi oku</a>'])
    return "\n".join(parts)


# ============================================================
# 10. RSS VE BOJ BASIN AÇIKLAMALARINI OKUMA
# ============================================================

def fetch_feed(feed):
    articles = []
    try:
        logger.info("RSS kaynağı okunuyor: %s", feed["name"])
        response = http_get(feed["url"], RSS_TIMEOUT, headers={"User-Agent": USER_AGENT})
        parsed = feedparser.parse(response.content)

        if not parsed.entries:
            return []

        for entry in parsed.entries:
            if len(articles) >= MAX_ARTICLES_PER_FEED:
                break

            title = clean_text(entry.get("title", ""))
            link = entry.get("link", "").strip()
            summary_raw = entry.get("summary", "") or entry.get("description", "")
            summary_full = clean_text(summary_raw)

            if not title or not link:
                continue
            if not is_valid_news(title, feed["type"], summary_full):
                continue

            published = entry.get("published_parsed") or entry.get("updated_parsed")
            published_ts = calendar.timegm(published) if published else None

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
    except Exception:
        logger.exception("RSS işlenirken hata oluştu: %s", feed["name"])
    return articles


def fetch_boj_press_releases(limit=3):
    """
    Japonya Merkez Bankası (BOJ) basın sayfasındaki son PDF açıklamalarını tarar, 
    metnini okur ve haber listesine ekler.
    """
    boj_articles = []
    try:
        logger.info("BOJ Basın Açıklamaları taranıyor...")
        response = http_get(BOJ_PRESS_URL, RSS_TIMEOUT, headers={"User-Agent": USER_AGENT})
        soup = BeautifulSoup(response.content, "html.parser")
        
        pdf_entries = []
        for a in soup.find_all("a", href=True):
            href = a['href']
            title = a.get_text(strip=True)
            if href.endswith(".pdf") and title:
                full_url = urllib.parse.urljoin(BOJ_BASE_URL, href)
                pdf_entries.append((title, full_url))
                if len(pdf_entries) >= limit:
                    break

        for title, pdf_url in pdf_entries:
            try:
                pdf_res = http_get(pdf_url, RSS_TIMEOUT, headers={"User-Agent": USER_AGENT})
                reader = PdfReader(io.BytesIO(pdf_res.content))
                extracted_text = ""
                for page in reader.pages[:2]:
                    extracted_text += page.extract_text() or ""

                clean_summary = re.sub(r"\s+", " ", extracted_text).strip()
                summary_short = clean_summary[:250] + "..." if len(clean_summary) > 250 else clean_summary

                boj_articles.append({
                    "title": title,
                    "link": pdf_url,
                    "id": normalize_link(pdf_url),
                    "source": "Bank of Japan (BOJ) Press",
                    "type": "GLOBAL",
                    "summary": shorten_text(summary_short),
                    "priority": "HIGH",
                    "flash": True,
                    "published_ts": time.time(), # Anlık veya güncel kabul edilir
                })
            except Exception as pdf_err:
                logger.error("BOJ PDF okuma hatası (%s): %s", pdf_url, pdf_err)
    except Exception as err:
        logger.error("BOJ basın sayfası okunamadı: %s", err)

    return boj_articles


def fetch_news():
    with ThreadPoolExecutor(max_workers=RSS_MAX_WORKERS) as pool:
        results = list(pool.map(fetch_feed, RSS_FEEDS))

    # BOJ Basın Açıklamalarını da ekle
    results.append(fetch_boj_press_releases())

    articles = []
    seen_ids = set()
    seen_titles = set()

    for feed_articles in results:
        for article in feed_articles:
            title_key = normalize_text(article["title"])
            if article["id"] in seen_ids or title_key in seen_titles:
                continue
            seen_ids.add(article["id"])
            seen_titles.add(title_key)
            articles.append(article)

    articles.sort(
        key=lambda article: (
            not article["flash"],
            article["priority"] != "HIGH",
            -(article["published_ts"] or 0),
        )
    )

    logger.info("Filtreleme tamamlandı. Uygun haber sayısı: %s", len(articles))
    return articles


def is_recent(article):
    published_ts = article["published_ts"]
    if published_ts is None:
        return True
    age_seconds = time.time() - published_ts
    return age_seconds <= MAX_ARTICLE_AGE_HOURS * 3600


# ============================================================
# 11. TELEGRAM KOMUTLARINI KONTROL ETME
# ============================================================

def check_telegram_commands(state):
    if DRY_RUN or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {
        "offset": state["last_update_id"] + 1,
        "timeout": 0,
        "allowed_updates": json.dumps(["message"]),
    }

    force_send = False
    try:
        response = http_get(url, REQUEST_TIMEOUT, retries=1, params=params)
        result = response.json()
        if not result.get("ok"):
            return False

        for update in result.get("result", []):
            state["last_update_id"] = max(state["last_update_id"], update["update_id"])
            message = update.get("message", {})
            chat_id = str(message.get("chat", {}).get("id", ""))
            if chat_id != str(TELEGRAM_CHAT_ID):
                continue
            text = (message.get("text") or "").strip().lower()
            command = text.split()[0] if text else ""
            command = command.split("@")[0]
            if command in ("/son", "/haber", "/guncel", "/güncel"):
                force_send = True

        save_state(state)
    except Exception:
        pass

    return force_send


# ============================================================
# 12. ANA PROGRAM
# ============================================================

def main():
    logger.info("Ekonomi haber botu başlatıldı.")

    if not DRY_RUN and (not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID):
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tanımlı değil.")
        sys.exit(1)

    state = load_state()
    seen_list = list(dict.fromkeys(state["seen_ids"]))
    seen_set = set(seen_list)

    def is_seen(article):
        return article["id"] in seen_set or article["link"] in seen_set

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

        if force_send:
            if not articles:
                send_telegram_message("⚠️ Şu an filtrelere uygun haber bulunamadı.")
            else:
                send_telegram_message("🌐 <b>Ekonomi Haberleri</b>\nFiltrelenmiş güncel haberler:")
                sent_count = 0
                for article in articles[:MAX_NORMAL_NEWS]:
                    prepare_article(article, translator)
                    if send_telegram_message(format_news_message(article)):
                        mark_seen(article)
                        sent_count += 1
                    time.sleep(0 if DRY_RUN else TELEGRAM_SEND_DELAY)
                logger.info("İstek üzerine gönderilen haber: %s", sent_count)
        else:
            candidates = [
                article for article in articles
                if not is_seen(article) and is_recent(article)
            ]
            to_send = candidates[:MAX_AUTO_NEWS]
            sent_count = 0

            for article in to_send:
                prepare_article(article, translator)
                if send_telegram_message(format_news_message(article)):
                    mark_seen(article)
                    sent_count += 1
                time.sleep(0 if DRY_RUN else TELEGRAM_SEND_DELAY)

            logger.info("Yeni gönderilen haber sayısı: %s", sent_count)

    finally:
        state["seen_ids"] = seen_list
        save_state(state)

    logger.info("Bot çalışmasını tamamladı.")


if __name__ == "__main__":
    main()