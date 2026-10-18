import os
import re
import json
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import requests
import feedparser

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MIN_SCORE = int(os.getenv("MIN_ALERT_SCORE", "5"))
STATE_FILE = "news_seen.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

SEARCHES = [
    ("Türkiye Ekonomi", '"TCMB" OR "Merkez Bankası" OR enflasyon OR faiz Türkiye', "tr-TR", "TR", "TR:tr"),
    ("Türkiye Piyasalar", '"döviz" OR "USD/TRY" OR "Borsa İstanbul" OR BIST', "tr-TR", "TR", "TR:tr"),
    ("ABD Fed", '"Federal Reserve" OR Fed OR "US inflation" OR "US CPI" OR payrolls', "en-US", "US", "US:en"),
    ("Avrupa ECB", '"European Central Bank" OR ECB OR "eurozone inflation"', "en-US", "US", "US:en"),
    ("Küresel Piyasalar", 'tariffs OR sanctions OR oil OR "bond yields" OR recession OR "market selloff"', "en-US", "US", "US:en"),
    ("Altın ve Emtia", 'gold OR crude oil OR OPEC OR "commodity prices"', "en-US", "US", "US:en"),
]

def make_url(query, lang, country, edition):
    return ("https://news.google.com/rss/search?q=" + quote_plus(query)
            + f"&hl={lang}&gl={country}&ceid={edition}")

FEEDS = [(name, make_url(q, lang, country, edition))
         for name, q, lang, country, edition in SEARCHES]
FEEDS.append(("TCMB Resmi Duyurular",
              "https://www.tcmb.gov.tr/wps/wcm/connect/EN/TCMB+EN/Bottom+Menu/Other/RSS/Press+Releases"))

WEIGHTS = {
    "emergency": 5, "rate decision": 5, "interest rate": 4, "policy rate": 4,
    "federal reserve": 3, "fed": 3, "tcmb": 4, "merkez bankası": 4,
    "central bank": 3, "ecb": 3, "inflation": 3, "cpi": 3, "ppi": 2,
    "payrolls": 3, "nonfarm": 3, "unemployment": 2, "gdp": 2, "recession": 3,
    "default": 4, "devaluation": 4, "capital controls": 5, "sanctions": 2,
    "tariff": 2, "trade war": 3, "oil": 2, "crude": 2, "gold": 1,
    "bist": 2, "banking crisis": 5, "liquidity crisis": 5, "bank collapse": 5,
    "unexpected": 2, "surprise": 2, "rate hike": 4, "rate cut": 4,
    "faiz": 3, "enflasyon": 3, "rezerv": 2, "ekonomi": 1, "işsizlik": 2,
    "büyüme": 2, "yaptırım": 2, "petrol": 2, "altın": 1, "acil": 4,
    "olağanüstü": 4,
}
CRITICAL_TERMS = (
    "emergency rate", "emergency meeting", "banking crisis", "capital controls",
    "bank collapse", "unexpected rate", "olağanüstü toplantı", "acil faiz",
    "sermaye kontrolü", "bankacılık krizi", "devalüasyon",
)

def score_item(title, summary=""):
    text = re.sub(r"\s+", " ", f"{title} {summary}").lower()
    score = sum(weight for term, weight in WEIGHTS.items() if term in text)
    critical = any(term in text for term in CRITICAL_TERMS)
    if critical:
        score = max(score, 8)
    return score, critical

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": message[:4000],
        "disable_web_page_preview": True,
    }, timeout=20)
    response.raise_for_status()
    if not response.json().get("ok"):
        raise RuntimeError("Telegram did not accept the message")

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_state(state):
    # Keep only the newest 1,000 IDs so the state file stays small.
    items = list(state.items())[-1000:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(dict(items), f, ensure_ascii=False, indent=2)

def published_at(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)

def main():
    seen = load_state()
    now = datetime.now(timezone.utc)
    new_count = 0
    for source, url in FEEDS:
        try:
            feed = feedparser.parse(url)
            if getattr(feed, "bozo", False) and not feed.entries:
                logging.warning("Could not parse feed: %s", source)
                continue
            for entry in reversed(feed.entries[:30]):
                title = re.sub(r"\s+", " ", entry.get("title", "")).strip()
                link = entry.get("link", "")
                if not title or not link:
                    continue
                item_id = entry.get("id") or link
                if item_id in seen:
                    continue
                seen[item_id] = now.isoformat()
                published = published_at(entry)
                if published and published < now - timedelta(hours=36):
                    continue
                summary = re.sub(r"<[^>]+>", " ", entry.get("summary", ""))
                score, critical = score_item(title, summary)
                if score < MIN_SCORE:
                    continue
                level = "🚨 CRITICAL" if critical or score >= 8 else "⚠️ HIGH IMPACT"
                pub_text = published.strftime("%Y-%m-%d %H:%M UTC") if published else "Time not provided"
                msg = (
                    f"{level} | Economic News\n\n"
                    f"Feed: {source}\nHeadline: {title}\n"
                    f"Importance score: {score}\nPublished: {pub_text}\n\n"
                    "Why it may matter: This headline matched macroeconomic/market-impact terms. "
                    "Verify the original report and actual market reaction before acting.\n\n"
                    f"Read: {link}"
                )
                send_telegram(msg)
                logging.info("Alert sent: %s", title)
                new_count += 1
        except Exception:
            logging.exception("Feed failed: %s", source)
    save_state(seen)
    logging.info("Finished. Sent %d alert(s).", new_count)

if __name__ == "__main__":
    main()
