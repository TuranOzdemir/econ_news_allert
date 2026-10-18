import os
import sys
import json
import requests
import feedparser

# GitHub Secrets veya Çevre Değişkenlerinden Bilgileri Al
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# RSS Haber Kaynakları
RSS_FEEDS = [
    "https://www.bloomberght.com/rss",
    "https://www.dünya.com/rss",
    "https://feeds.feedburner.com/reuters/topNews"
]

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("HATA: Telegram credentials bulunamadı.")
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
    """ Telegram'dan gelen komutları kontrol eder (/son, /haber vb.) """
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
            
            # /son veya /haber komutu geldi mi?
            if text in ["/son", "/haber", "/güncel"]:
                # Offset güncellemesi yaparak mesajın tekrar tekrar işlenmesini önle
                update_id = update["update_id"]
                requests.get(f"{url}?offset={update_id + 1}")
                return True
    except Exception as e:
        print(f"Komut kontrol hatası: {e}")
    return False

def fetch_latest_news(limit=5):
    """ En son eklenen ekonomi haberlerini getirir """
    all_articles = []
    for feed_url in RSS_FEEDS:
        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries[:limit]:
            all_articles.append({
                "title": entry.get("title", "Başlıksız"),
                "link": entry.get("link", "#")
            })
    return all_articles[:limit]

def main():
    # Telegram'dan kullanıcı komut gönderdi mi kontrol et
    force_send = check_telegram_commands()
    
    # Komut verilip verilmediğini kontrol etmek için parametre kontrolü de yap
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
        # Manuel tetikleme: Son haberleri direkt gönder
        send_telegram_message("📊 <b>Anlık İstek: En Son Ekonomi Haberleri</b>")
        for art in articles:
            msg = f"📰 <b>{art['title']}</b>\n🔗 {art['link']}"
            send_telegram_message(msg)
            seen_ids.add(art['link'])
    else:
        # Otomatik tarama: Sadece yeni (görülmemiş) haberleri gönder
        new_count = 0
        for art in articles:
            if art['link'] not in seen_ids:
                msg = f"🚨 <b>Yeni Ekonomi Haberi</b>\n\n<b>{art['title']}</b>\n🔗 {art['link']}"
                send_telegram_message(msg)
                seen_ids.add(art['link'])
                new_count += 1
        if new_count == 0:
            print("Yeni haber bulunamadı.")

    # Görülen haberleri kaydet
    with open(seen_file, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()