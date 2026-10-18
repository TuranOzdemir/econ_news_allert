import requests
import feedparser

from news_alert import RSS_FEEDS, is_valid_news


# Terminal çıktılarının daha okunabilir olması için
SEPARATOR = "=" * 65

# RSS kaynaklarını kontrol etmek için kullanılacak bağlantı zaman aşımı
TIMEOUT = 20

# Her kaynak için gösterilecek maksimum haber sayısı
MAX_ACCEPTED_TO_SHOW = 10
MAX_FILTERED_TO_SHOW = 5


def get_entry_text(entry):
    """
    RSS haberinden başlık ve özet bilgilerini alır.
    Bazı RSS kaynakları özet yerine description kullanabilir.
    """

    title = entry.get("title", "").strip()

    summary = (
        entry.get("summary", "")
        or entry.get("description", "")
        or ""
    )

    return title, summary


def test_feed(feed):
    """
    Tek bir RSS kaynağını test eder.
    Bağlantıyı, haber sayısını ve filtre sonuçlarını gösterir.
    """

    name = feed.get("name", "İsimsiz kaynak")
    url = feed.get("url", "")
    feed_type = feed.get("type", "turkish")

    print(f"\n{SEPARATOR}")
    print(f"KAYNAK: {name}")
    print(f"URL: {url}")

    # Her kaynak için listeleri yeniden oluştur.
    # Böylece önceki kaynağın sonuçları birbirine karışmaz.
    accepted = []
    filtered = []

    try:
        # RSS bağlantısını kontrol et
        response = requests.get(
            url,
            timeout=TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; EconomyNewsBot/1.0)"
            },
        )

        print(f"HTTP DURUMU: {response.status_code}")

        response.raise_for_status()

        # RSS içeriğini oku
        parsed_feed = feedparser.parse(response.content)

        print(f"RSS HATASI: {parsed_feed.bozo}")

        if parsed_feed.bozo:
            print(
                "RSS UYARISI:",
                str(parsed_feed.bozo_exception)[:300]
            )

        entries = parsed_feed.entries

        print(f"HABER SAYISI: {len(entries)}")

        if not entries:
            print("UYARI: Bu kaynakta okunabilir haber bulunamadı.")
            return

        # Her haberi filtrele
        for entry in entries:
            title, summary = get_entry_text(entry)

            if not title:
                continue

            try:
                is_valid = is_valid_news(
                    title,
                    feed_type,
                    summary
                )

                if is_valid:
                    accepted.append(title)
                else:
                    filtered.append(title)

            except Exception as error:
                print(f"FİLTRE HATASI: {error}")
                print(f"HATALI HABER: {title}")
                continue

        # Sonuçları göster
        print("\n--- FİLTRE SONUÇLARI ---")
        print(f"Kabul edilen haber: {len(accepted)}")
        print(f"Filtrelenen haber: {len(filtered)}")

        if entries:
            processed_count = len(accepted) + len(filtered)

            if processed_count:
                acceptance_rate = (
                    len(accepted) / processed_count
                ) * 100

                print(
                    f"Kabul oranı: %{acceptance_rate:.1f}"
                )

        print("\n--- KABUL EDİLEN HABERLER ---")

        if accepted:
            for index, title in enumerate(
                accepted[:MAX_ACCEPTED_TO_SHOW],
                start=1
            ):
                print(f"{index}. {title}")
        else:
            print("Hiçbir haber kabul edilmedi.")

        print("\n--- FİLTRELENEN HABERLER ---")

        if filtered:
            for index, title in enumerate(
                filtered[:MAX_FILTERED_TO_SHOW],
                start=1
            ):
                print(f"{index}. {title}")
        else:
            print("Filtrelenen haber bulunamadı.")

    except requests.exceptions.Timeout:
        print(f"BAĞLANTI HATASI: {TIMEOUT} saniyede yanıt alınamadı.")

    except requests.exceptions.HTTPError as error:
        print(f"HTTP HATASI: {error}")

    except requests.exceptions.RequestException as error:
        print(f"BAĞLANTI HATASI: {error}")

    except Exception as error:
        print(f"KAYNAK HATASI: {type(error).__name__}: {error}")


def main():
    """
    Tüm RSS kaynaklarını sırayla test eder.
    """

    print(SEPARATOR)
    print("EKONOMİ HABER BOTU - RSS TESTİ")
    print(SEPARATOR)

    if not RSS_FEEDS:
        print("HATA: news_alert.py içerisinde RSS_FEEDS boş.")
        return

    print(f"Test edilecek kaynak sayısı: {len(RSS_FEEDS)}")

    for feed in RSS_FEEDS:
        test_feed(feed)

    print(f"\n{SEPARATOR}")
    print("TEST TAMAMLANDI")
    print(SEPARATOR)


if __name__ == "__main__":
    main()