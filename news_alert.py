import os
import sys
import json
import requests
import feedparser

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

RSS_FEEDS = [
    "https://www.bloomberght.com/rss",
    "https://www.dunya.com/rss",
    "https://feeds.feedburner.com/reuters/topNews"
]

FLASH_KEYWORDS = [
    "trump", "fed", "powell", "tcmb", "karahan", "faiz", 
    "enflasyon", "son dakika", "flaş", "savaş", "kriz", "yaptırım"
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
            
            # /son, /haber veya mesaj iceriginde bu kelimeler var mi?
            if any(cmd in text for cmd in ["/son", "/haber", "/güncel", "son"]):
                update_id = update["update_id"]
                # Mesajı işlendi olarak işaretle
                requests.get(f"{url}?offset={update_id + 1}")
                return True
    except Exception as e:
        print(f"Komut kontrol hatası: {e}")
    return False

def is_flash_news(title):
    title_lower = title.lower()
    return any(keyword in title_lower for keyword in FLASH_KEYWORDS)

def fetch_latest_news(limit=5):
    all_articles = []
    for feed_url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
            for entry in parsed.entries[:limit]:
                all_articles.append({
                    "title": entry.get("title", "Başlıksız"),
                    "link": entry.get("link", "#")
                })
        except Exception as e:
            print(f"Feed okuma hatası ({feed_url}): {e}")
    return all_articles[:limit]

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

    articles = fetch_latest_news(limit=5)
    
    if force_send:
        # İsteği aldığını teyit et
        send_telegram_message("📊 <b>Anlık İstek Alındı: En Son Ekonomi Haberleri Getiriliyor...</b>")
        if not articles:
            send_telegram_message("⚠️ Şu an haber kaynaklarından veri alınamadı.")
        for art in articles:
            msg = f"📰 <b>{art['title']}</b>\n🔗 {art['link']}"
            send_telegram_message(msg)
            seen_ids.add(art['link'])
    else:
        new_count = 0
        for art in articles:
            if art['link'] not in seen_ids:
                if is_flash_news(art['title']):
                    msg = f"🚨 <b>KRİTİK FLAŞ HABER</b> 🚨\n\n<b>{art['title']}</b>\n🔗 {art['link']}"
                else:
                    msg = f"📰 <b>Ekonomi Haberi</b>\n\n<b>{art['title']}</b>\n🔗 {art['link']}"
                
                send_telegram_message(msg)
                seen_ids.add(art['link'])
                new_count += 1
                
        if new_count == 0:
            print("Yeni haber bulunamadı.")

    with open(seen_file, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()