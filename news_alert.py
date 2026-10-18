import os
import sys
import json
import requests
import feedparser

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Küresel ve Yerel Ekonomi Haber Kaynakları (ABD, Çin, Japonya, TR, Dünya)
RSS_FEEDS = [
    # Türkiye & Türkçe Kaynaklar
    "https://www.bloomberght.com/rss",
    "https://www.dunya.com/rss",
    
    # ABD & Küresel Finans (Reuters & CNBC)
    "https://feeds.feedburner.com/reuters/topNews",
    "https://search.cnbc.com/rs/search/combinedrender?source=0&id=10000664&ns1=1&searchtype=all", # CNBC Economy
    "https://search.cnbc.com/rs/search/combinedrender?source=0&id=100003114&ns1=1&searchtype=all", # CNBC World News
    
    # Çin & Asya Ekonomisi (South China Morning Post & CNBC Asia)
    "https://www.scmp.com/rss/92/feed", # SCMP China Economy
    "https://search.cnbc.com/rs/search/combinedrender?source=0&id=19832390&ns1=1&searchtype=all", # CNBC Asia-Pacific
    
    # Japonya & Asya (Nikkei Asia)
    "https://asia.nikkei.com/rss/feed/nar"
]

# Küresel Piyasaları Sarsabilecek Kritik Anahtar Kelimeler (TR + EN)
FLASH_KEYWORDS = [
    # Kişiler ve Kurumlar
    "trump", "fed", "powell", "tcmb", "karahan", "boj", "ueda", "pboc", "xi jinping", "biden",
    # Terimler ve Kavramlar
    "faiz", "rate", "interest", "enflasyon", "inflation", "cpi", "ppi",
    "tariff", "tariffs", "gümrük", "trade war", "ticaret savaşı", "yaptırım", "sanction",
    "son dakika", "breaking", "flaş", "flash", "savaş", "war", "kriz", "crisis",
    "dollar", "dolar", "yuan", "yen", "gold", "altın", "oil", "petrol"
]

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("HATA: Telegram bilgileri bulunamadı.")
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
        print(f"Mesaj gönderme hatası: {e}")

def check_telegram_commands():
    """ Telegram'daki son mesajları denetler """
    if not TELEGRAM_BOT_TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        response = requests.get(url, timeout=10).json()
        if not response.get("ok"):
            return False
        
        results = response.get("result", [])
        for update in results:
            message = update.get("message", {})
            text = message.get("text", "").strip().lower()
            
            if any(cmd in text for cmd in ["/son", "/haber", "/güncel", "son"]):
                update_id = update["update_id"]
                requests.get(f"{url}?offset={update_id + 1}")
                return True
    except Exception as e:
        print(f"Komut kontrol hatası: {e}")
    return False

def is_flash_news(title):
    title_lower = title.lower()
    return any(keyword in title_lower for keyword in FLASH_KEYWORDS)

def fetch_latest_news(limit_per_feed=3):
    all_articles = []
    for feed_url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
            for entry in parsed.entries[:limit_per_feed]:
                all_articles.append({
                    "title": entry.get("title", "Başlıksız"),
                    "link": entry.get("link", "#"),
                    "source": parsed.feed.get("title", "Küresel Kaynak")
                })
        except Exception as e:
            print(f"Feed okuma hatası ({feed_url}): {e}")
    return all_articles

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

    articles = fetch_latest_news(limit_per_feed=3)
    
    if force_send:
        send_telegram_message("🌍 <b>Anlık İstek: Küresel Ekonomi Haberleri (ABD, Çin, Japonya, TR)</b>")
        if not articles:
            send_telegram_message("⚠️ Şu an haber kaynaklarından veri alınamadı.")
        for art in articles[:8]:  # İsteğe bağlı ilk 8 küresel haberi dök
            msg = f"🌐 <b>[{art['source']}]</b>\n📰 <b>{art['title']}</b>\n🔗 {art['link']}"
            send_telegram_message(msg)
            seen_ids.add(art['link'])
    else:
        new_count = 0
        for art in articles:
            if art['link'] not in seen_ids:
                if is_flash_news(art['title']):
                    msg = f"🚨 <b>KRİTİK KÜRESEL FLAŞ HABER</b> 🚨\n📍 <i>{art['source']}</i>\n\n<b>{art['title']}</b>\n🔗 {art['link']}"
                else:
                    msg = f"🌍 <b>Küresel Ekonomi Haberi</b>\n📍 <i>{art['source']}</i>\n\n<b>{art['title']}</b>\n🔗 {art['link']}"
                
                send_telegram_message(msg)
                seen_ids.add(art['link'])
                new_count += 1
                
        if new_count == 0:
            print("Yeni küresel haber bulunamadı.")

    with open(seen_file, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()