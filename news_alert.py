import os
import sys
import json
import requests
import feedparser

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Nokta Atışı Finansal RSS Kaynakları
RSS_FEEDS = [
    # Türkiye Kaynakları
    {"url": "https://www.bloomberght.com/rss", "type": "TR", "name": "Bloomberg HT"},
    {"url": "https://www.dunya.com/rss", "type": "TR", "name": "Dünya Gazetesi"},
    
    # Küresel & Bölgesel Finans Kaynakları (ABD, Çin, Japonya, AB)
    {"url": "https://www.cnbc.com/id/20910252/device/rss/rss.html", "type": "GLOBAL", "name": "CNBC World Economy"},
    {"url": "http://feeds.marketwatch.com/marketwatch/topstories/", "type": "GLOBAL", "name": "MarketWatch"},
    {"url": "https://www.investing.com/rss/news_14.rss", "type": "GLOBAL", "name": "Investing.com Economy"}
]

# NEGATİF FİLTRE: Bu kelimeler geçiyorsa haber KESİNLİKLE atlanır (Çöp haber engelleyici)
NEGATIVE_KEYWORDS = [
    "sport", "football", "soccer", "movie", "celebrity", "actor", 
    "murder", "crime", "accident", "west bank", "entertainment", "court case"
]

# ZORUNLU POZİTİF FİLTRE (Yabancı haberlerin geçmesi için bunlardan en az biri veya kombinasyonu olmalı)
ENGLISH_FINANCIAL_PHRASES = [
    "fed", "powell", "tcmb", "karahan", "boj", "pboc", "ecb", "central bank",
    "interest rate", "inflation", "cpi", "ppi", "gdp", "recession",
    "tariff", "trade war", "sanction", "stock market", "wall street",
    "treasury", "yield", "bond", "dollar", "yuan", "yen", "gold", "crude oil",
    "trump", "xi jinping", "biden", "yellen"
]

# KRİTİK FLAŞ HABER FİLTRESİ
FLASH_KEYWORDS = [
    "trump", "fed", "powell", "tcmb", "boj", "pboc", 
    "faiz", "interest rate", "enflasyon", "inflation", "rate decision",
    "breaking", "son dakika", "flaş", "flash", "savaş", "war", "kriz", "crisis", "tariff"
]

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("HATA: Telegram bilgileri eksik.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Mesaj hatası: {e}")

def check_telegram_commands():
    if not TELEGRAM_BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        res = requests.get(url, timeout=10).json()
        if not res.get("ok"): return False
        for update in res.get("result", []):
            msg = update.get("message", {})
            text = msg.get("text", "").strip().lower()
            if any(cmd in text for cmd in ["/son", "/haber", "/güncel", "son"]):
                update_id = update["update_id"]
                requests.get(f"{url}?offset={update_id + 1}")
                return True
    except Exception as e:
        print(f"Komut hatası: {e}")
    return False

def is_valid_news(title, feed_type):
    title_lower = title.lower()
    
    # 1. Negatif Filtre Kontrolü (Varsa doğrudan elenir)
    if any(neg in title_lower for neg in NEGATIVE_KEYWORDS):
        return False
    
    # 2. Türkçe Kaynaklar Negatif Filtreden Geçtiyse Kabul Edilir
    if feed_type == "TR":
        return True
    
    # 3. Yabancı Kaynaklar İçin Pozitif Finansal Kelime Şartı
    return any(phrase in title_lower for phrase in ENGLISH_FINANCIAL_PHRASES)

def is_flash_news(title):
    title_lower = title.lower()
    return any(kw in title_lower for kw in FLASH_KEYWORDS)

def fetch_news():
    articles = []
    for feed in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed["url"])
            for entry in parsed.entries[:5]:
                title = entry.get("title", "Başlıksız")
                link = entry.get("link", "#")
                
                if is_valid_news(title, feed["type"]):
                    articles.append({
                        "title": title,
                        "link": link,
                        "source": feed["name"],
                        "type": feed["type"]
                    })
        except Exception as e:
            print(f"RSS Okuma Hatası ({feed['name']}): {e}")
    return articles

def main():
    force_send = check_telegram_commands()
    if len(sys.argv) > 1 and sys.argv[1] == "--force":
        force_send = True

    seen_file = "news_seen.json"
    seen_ids = set()
    if os.path.exists(seen_file):
        try:
            with open(seen_file, "r", encoding="utf-8") as f:
                seen_ids = set(json.load(f))
        except Exception:
            seen_ids = set()

    articles = fetch_news()
    
    if force_send:
        send_telegram_message("🌐 <b>Anlık İstek: Filtrelenmiş Nokta Atışı Ekonomi Haberleri</b>")
        if not articles:
            send_telegram_message("⚠️ Şu an kriterlere uygun haber bulunamadı.")
        for art in articles[:7]:
            msg = f"📍 <b>[{art['source']}]</b>\n📰 <b>{art['title']}</b>\n🔗 {art['link']}"
            send_telegram_message(msg)
            seen_ids.add(art['link'])
    else:
        new_count = 0
        for art in articles:
            if art['link'] not in seen_ids:
                if is_flash_news(art['title']):
                    msg = f"🚨 <b>KRİTİK FLAŞ HABER</b> 🚨\n📍 <i>{art['source']}</i>\n\n<b>{art['title']}</b>\n🔗 {art['link']}"
                else:
                    msg = f"📊 <b>Ekonomi Haberi</b>\n📍 <i>{art['source']}</i>\n\n<b>{art['title']}</b>\n🔗 {art['link']}"
                
                send_telegram_message(msg)
                seen_ids.add(art['link'])
                new_count += 1
                
        if new_count == 0:
            print("Yeni filtrelenmiş haber bulunamadı.")

    with open(seen_file, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()