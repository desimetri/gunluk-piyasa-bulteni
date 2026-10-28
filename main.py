import os
import re
import html
import time
import math
import logging
import smtplib
import requests
import unicodedata

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google import genai


# ============================================================
# AYARLAR
# ============================================================

EMAIL_ADDRESS = os.environ.get("GMAIL_USER")
EMAIL_PASSWORD = os.environ.get("GMAIL_PASS")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

TO_EMAIL = "muratuslu@gmail.com"
TIMEZONE = ZoneInfo("Europe/Istanbul")

GEMINI_MODEL = "gemini-3.8-flash"

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YAHOO_API_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"

CHANNEL_HANDLES = [
    "@EMRAHLAFCI",
    "@barissoydan",
    "@KanalFinans",
]

# Her kanalda en yeni videodan geriye doğru en fazla kaç video incelensin?
MAX_VIDEOS_TO_CHECK = 5

REQUEST_TIMEOUT = 40
GEMINI_TIMEOUT_SECONDS = 1800

# GC=F ve SI=F vadeli işlem sembolleridir, spot fiyat değildir.
MARKET_ASSETS = [
    ("Altın Vadeli İşlem", "GC=F", "USD/ons", 2),
    ("Gümüş Vadeli İşlem", "SI=F", "USD/ons", 3),
    ("Brent Petrol", "BZ=F", "USD/varil", 2),
    ("BIST 100", "XU100.IS", "puan", 2),
    ("Nasdaq Composite", "^IXIC", "puan", 2),
    ("Bitcoin", "BTC-USD", "USD", 2),
    ("USD/TRY", "USDTRY=X", "TRY", 4),
    ("EUR/TRY", "EURTRY=X", "TRY", 4),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


# ============================================================
# 1. GENEL YARDIMCI FONKSİYONLAR
# ============================================================

def istanbul_saati():
    return datetime.now(TIMEZONE)


def api_get(url, params=None):
    response = requests.get(
        url,
        params=params,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; DailyFinanceNewsletter/1.0)"
            )
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def fiyat_formatla(value, decimals=2):
    text = f"{value:,.{decimals}f}"
    return (
        text.replace(",", "#")
        .replace(".", ",")
        .replace("#", ".")
    )


def tarih_formatla(value):
    if not value:
        return "Bilinmiyor"

    return value.astimezone(TIMEZONE).strftime(
        "%d.%m.%Y %H:%M:%S (TSİ)"
    )


def tarih_sirala(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def metni_normalize_et(value):
    """
    Türkçe karakterleri ve büyük/küçük harf farklılıklarını
    karşılaştırılabilir hale getirir.
    """
    value = unicodedata.normalize(
        "NFKD",
        (value or "").casefold(),
    )

    value = "".join(
        char
        for char in value
        if not unicodedata.combining(char)
    )

    return value.replace("ı", "i")


def html_sayfa(body, title="Günlük Finans ve Piyasa Bülteni"):
    return (
        "<!DOCTYPE html>"
        "<html lang='tr'>"
        "<head>"
        "<meta charset='UTF-8'>"
        "<meta name='viewport' "
        "content='width=device-width, initial-scale=1.0'>"
        f"<title>{html.escape(title)}</title>"
        "</head>"
        "<body style='font-family:Arial,sans-serif;color:#202124;'>"
        f"{body}"
        "</body></html>"
    )


def html_belgesini_duzenle(content):
    content = (content or "").strip()

    content = re.sub(
        r"^\s*```(?:html)?\s*",
        "",
        content,
        flags=re.IGNORECASE,
    )

    content = re.sub(
        r"\s*```\s*$",
        "",
        content,
    ).strip()

    if not content:
        raise RuntimeError("HTML içeriği boş.")

    # Tam HTML belgesi yoksa verilen içeriği belgeye dönüştür.
    if "<html" not in content.lower():
        content = html_sayfa(content)

    # BODY etiketi yoksa güvenli bir gövde oluştur.
    if "<body" not in content.lower():
        html_open = re.search(
            r"<html\b[^>]*>",
            content,
            flags=re.IGNORECASE,
        )

        if html_open:
            head_end = re.search(
                r"</head\s*>",
                content,
                flags=re.IGNORECASE,
            )

            if head_end:
                content = (
                    content[:head_end.end()]
                    + "<body style='font-family:Arial,sans-serif;color:#202124;'>"
                    + content[head_end.end():]
                )
            else:
                content = (
                    content[:html_open.end()]
                    + "<head><meta charset='UTF-8'></head>"
                    + "<body style='font-family:Arial,sans-serif;color:#202124;'>"
                    + content[html_open.end():]
                )
        else:
            content = html_sayfa(content)

    # BODY kapanışını garanti et.
    if "</body>" not in content.lower():
        html_end = re.search(
            r"</html\s*>",
            content,
            flags=re.IGNORECASE,
        )

        if html_end:
            content = (
                content[:html_end.start()]
                + "</body>"
                + content[html_end.start():]
            )
        else:
            content += "</body></html>"

    if len(content) < 300:
        raise RuntimeError(
            "Üretilen HTML beklenenden kısa."
        )

    return content


def gemini_hata_mesaji(error):
    message = str(error).lower()

    if any(token in message for token in (
        "resource_exhausted",
        "quota",
        "rate limit",
        "429",
        "requests per day",
        "limit exceeded",
    )):
        return (
            "Gemini API isteği kota, hız sınırı veya işlem kapasitesi "
            "nedeniyle başarısız oldu. Bu çalıştırmada otomatik video "
            "ve haber analizi tamamlanamadı. Google AI Studio kullanım "
            "ve kota ekranını kontrol edin. Bir sonraki zamanlanmış "
            "çalıştırmada tekrar denenebilir."
        )

    if any(token in message for token in (
        "503",
        "unavailable",
        "high demand",
    )):
        return (
            "Gemini hizmeti geçici olarak kullanılamadığı için otomatik "
            "analiz tamamlanamadı. Sonraki zamanlanmış çalıştırma "
            "yeniden deneyebilir."
        )

    return (
        "Gemini üzerinden otomatik analiz bu çalıştırmada "
        "tamamlanamadı. Aşağıda doğrudan veri kaynağından "
        "alınabilen fiyatlar ve video seçim bilgileri sunulmuştur."
    )


# ============================================================
# 2. REKLAM / TANITIM ELEME KRİTERİ
# ============================================================

def video_promosyon_mu(video):
    """
    Bir videoyu sırf açıklamasında sponsorluk bildirimi bulunduğu
    için elemez.

    Öncelik sırası:
    1. Başlık finans/piyasa konusunu belirtiyorsa video korunur.
    2. Finans dışı, açıkça ürün tanıtımı olan başlık elenir.
    3. Finans konusu taşımayan başlıkta açık reklam/işbirliği
       işareti varsa video elenir.
    4. Yalnızca açıklamada genel bir sponsorluk bildirimi bulunması
       videoyu elemek için yeterli değildir.

    Bu, başlık ve açıklama üzerinden çalışan sezgisel bir filtredir.
    Videonun tüm içeriğini izlemiş gibi kesin sınıflandırma yapmaz.
    """

    title = metni_normalize_et(
        video.get("title", "")
    )

    description = metni_normalize_et(
        video.get("description", "")
    )

    # Token tabanlı kontrol kullanılır. Böylece "telefon" kelimesi,
    # içindeki "fon" harfleri nedeniyle finans konusu sayılmaz.
    tokens = re.findall(
        r"[a-z0-9]+",
        title,
    )

    finance_terms = [
        "borsa",
        "ekonomi",
        "finans",
        "piyasa",
        "altin",
        "gumus",
        "petrol",
        "doviz",
        "dolar",
        "euro",
        "bitcoin",
        "kripto",
        "faiz",
        "enflasyon",
        "tcmb",
        "fed",
        "nasdaq",
        "bist",
        "hisse",
        "fon",
        "rezerv",
        "bilanco",
        "yatirim",
        "sirket",
        "astor",
        "koc",
        "endeks",
        "portfoy",
        "getiri",
        "yatirimci",
        "merkez",
        "bankasi",
        "serbest",
        "odeme",
        "plan",
        "dusus",
        "yukselis",
        "satis",
        "analiz",
        "piyasalar",
        "makroekonomi",
        "enstruman",
        "varlik",
        "borsalar",
        "secim",
        "rezervde",
        "bilancolar",
    ]

    title_has_finance_topic = any(
        token == term
        or (
            len(term) >= 3
            and token.startswith(term)
        )
        for token in tokens
        for term in finance_terms
    )

    # Açık reklam ve işbirliği işaretleri.
    promo_terms = [
        "#isbirligi",
        "#reklam",
        "#sponsorlu",
        "ucretli isbirligi",
        "sponsorlu icerik",
        "sponsorlu video",
        "reklam isbirligi",
        "ucretli tanitim",
        "paid promotion",
        "sponsored content",
        "this video is sponsored",
        "contains paid promotion",
        "isbirligi kapsaminda",
        "reklamdir",
    ]

    title_has_promo_marker = any(
        term in title
        for term in promo_terms
    )

    description_has_promo_marker = any(
        term in description
        for term in promo_terms
    )

    # Finans dışı ürün/teknoloji incelemesi sinyalleri.
    product_terms = [
        "urun tanitimi",
        "kutu acilimi",
        "unboxing",
        "telefon incelemesi",
        "telefon tanitimi",
        "cihaz incelemesi",
        "urun incelemesi",
        "otomobil incelemesi",
        "alisveris videosu",
        "galaxyzfold",
        "iphone incelemesi",
        "akilli telefon",
        "telefon karsilastirmasi",
        "teknoloji incelemesi",
        "samsung galaxy",
    ]

    title_has_product_topic = any(
        term in title
        for term in product_terms
    )

    # KRİTİK DÜZELTME:
    # Başlıkta finansal konu varsa yalnızca sponsor/işbirliği
    # bildirimi nedeniyle videoyu ASLA eleme.
    #
    # Örnek:
    # "Koç nereye? Fonlarda ödeme planı | Rezervde erime |
    # BİST'i Astor tuttu"
    #
    # Bu başlık finansal konu içerdiğinden korunmalıdır.
    if title_has_finance_topic:

        if (
            title_has_promo_marker
            or description_has_promo_marker
        ):
            reason = (
                "Başlıkta finansal/piyasa konusu tespit edildi. "
                "Sponsorluk veya işbirliği işareti bulunsa da video "
                "elenmedi; finansal içeriği analiz edilecek."
            )

            logging.info(
                "FİNANSAL İÇERİK KORUNDU | %s | %s",
                video.get("title", ""),
                reason,
            )

            return False, reason

        return False, (
            "Başlık finansal/piyasa konusu içeriyor; video korunuyor."
        )

    # Finansal konu yoksa ve başlık bir ürün tanıtımını gösteriyorsa
    # video analiz dışı bırakılabilir.
    if title_has_product_topic:
        return True, (
            "Başlık finans dışı ürün/teknoloji tanıtımı veya "
            "incelemesine işaret ediyor."
        )

    # Finansal konu bulunmayan bir başlıktaki açık reklam veya
    # işbirliği işareti eleme için yeterli olabilir.
    if title_has_promo_marker:
        return True, (
            "Başlıkta açık reklam/işbirliği işareti bulundu ve "
            "başlıkta finansal konu sinyali bulunamadı."
        )

    # Açıklamada sponsorluk bildirimi bulunması tek başına yeterli değil.
    # Açıklamalarda bazen kanalın genel işbirliği bilgileri bulunabilir.
    # Başlık finansal değil ama belirgin bir ürün tanıtımını gösteriyorsa
    # reklam bildirimi ile birlikte elenebilir.
    if (
        description_has_promo_marker
        and title_has_product_topic
    ):
        return True, (
            "Başlık finans dışı ürün tanıtımı gösteriyor ve "
            "açıklamada sponsorluk bildirimi bulunuyor."
        )

    return False, ""


# ============================================================
# 3. ÜÇ KANALIN EN YENİ UYGUN VİDEOLARINI SEÇ
# ============================================================

def son_videolari_getir():
    """
    Her kanaldan en fazla bir video seçer.

    En yeni videodan geriye doğru en fazla MAX_VIDEOS_TO_CHECK
    aday kontrol edilir. Finansal konu taşıyan video, başlığında
    reklam işareti veya açıklamasında sponsorluk bildirimi bulunsa
    bile sırf bu nedenle elenmez.
    """

    if not YOUTUBE_API_KEY:
        raise RuntimeError(
            "YOUTUBE_API_KEY bulunamadı. "
            "GitHub Secrets ayarını kontrol et."
        )

    secilen_videolar = []
    kanal_durumlari = []

    for handle in CHANNEL_HANDLES:

        logging.info(
            "YouTube kanalı aranıyor: %s",
            handle,
        )

        channel_data = api_get(
            f"{YOUTUBE_API_BASE}/channels",
            {
                "part": "id,snippet,contentDetails",
                "forHandle": handle,
                "key": YOUTUBE_API_KEY,
            },
        )

        channels = channel_data.get("items", [])

        if not channels:
            raise RuntimeError(
                f"YouTube kanalı bulunamadı: {handle}"
            )

        channel = channels[0]

        channel_name = channel["snippet"].get(
            "title",
            handle,
        )

        uploads_playlist = (
            channel["contentDetails"]
            ["relatedPlaylists"]["uploads"]
        )

        # Silinmiş veya erişilemeyen videoların listede bulunabilmesi
        # nedeniyle kontrol edilecek aday sayısından biraz fazla getir.
        playlist_data = api_get(
            f"{YOUTUBE_API_BASE}/playlistItems",
            {
                "part": "snippet,contentDetails",
                "playlistId": uploads_playlist,
                "maxResults": 10,
                "key": YOUTUBE_API_KEY,
            },
        )

        candidate_ids = []
        playlist_metadata = {}

        for item in playlist_data.get("items", []):

            snippet = item.get("snippet", {})
            details = item.get("contentDetails", {})

            video_id = (
                details.get("videoId")
                or snippet.get("resourceId", {}).get("videoId")
            )

            if not video_id:
                continue

            if video_id in playlist_metadata:
                continue

            candidate_ids.append(video_id)

            playlist_metadata[video_id] = {
                "published_at": (
                    details.get("videoPublishedAt")
                    or snippet.get("publishedAt")
                    or ""
                ),
                "playlist_title": (
                    snippet.get("title") or ""
                ),
            }

        if not candidate_ids:
            logging.warning(
                "%s kanalında video bulunamadı.",
                channel_name,
            )

            kanal_durumlari.append({
                "channel": channel_name,
                "handle": handle,
                "checked_count": 0,
                "selected_title": None,
                "selected_url": None,
                "selection_note": (
                    "YouTube yükleme listesinde video bulunamadı."
                ),
                "skipped_promotional": [],
            })
            continue

        # Videoların gerçek başlık ve açıklamalarını al.
        video_details = api_get(
            f"{YOUTUBE_API_BASE}/videos",
            {
                "part": "snippet",
                "id": ",".join(candidate_ids),
                "key": YOUTUBE_API_KEY,
            },
        )

        details_by_id = {}

        for item in video_details.get("items", []):
            details_by_id[item["id"]] = item.get(
                "snippet",
                {},
            )

        channel_videos = []

        for video_id in candidate_ids:

            snippet = details_by_id.get(video_id)

            # Silinmiş, özel veya artık erişilemeyen videoları atla.
            if not snippet:
                continue

            title = (
                snippet.get("title") or ""
            ).strip()

            if title.lower() in (
                "private video",
                "deleted video",
            ):
                continue

            published_at = (
                snippet.get("publishedAt")
                or playlist_metadata[video_id]["published_at"]
                or ""
            )

            channel_videos.append({
                "channel": channel_name,
                "handle": handle,
                "title": title or "Başlıksız video",
                "description": snippet.get("description", ""),
                "published_at": published_at,
                "url": (
                    "https://www.youtube.com/watch?v="
                    + video_id
                ),
            })

        channel_videos.sort(
            key=lambda video: tarih_sirala(
                video["published_at"]
            ),
            reverse=True,
        )

        # En yeni beş geçerli adayla sınırla.
        channel_videos = channel_videos[
            :MAX_VIDEOS_TO_CHECK
        ]

        skipped_promotional = []
        selected = None

        for candidate in channel_videos:

            is_promo, reason = video_promosyon_mu(
                candidate
            )

            if is_promo:
                skipped_promotional.append({
                    "title": candidate["title"],
                    "url": candidate["url"],
                    "published_at": candidate["published_at"],
                    "reason": reason,
                })

                logging.info(
                    "REKLAM/ÜRÜN TANITIMI NEDENİYLE ATLANDI | "
                    "%s | %s | %s",
                    channel_name,
                    candidate["title"],
                    reason,
                )

                continue

            # Finansal içerik sponsorluk ibaresiyle korunmuşsa
            # seçim gerekçesini rapora aktar.
            candidate["screening_note"] = reason

            selected = candidate
            break

        if selected:

            if skipped_promotional:
                selection_note = (
                    f"Bu kanalda {len(skipped_promotional)} daha yeni "
                    "video finans dışı reklam/ürün tanıtımı ölçütü "
                    "nedeniyle atlandı. En yeni uygun video seçildi."
                )
            else:
                selection_note = (
                    "Kontrol edilen videolar içinde en yeni uygun video "
                    "seçildi; reklam işareti tek başına eleme nedeni "
                    "olarak kullanılmadı."
                )

            if selected.get("screening_note"):
                selection_note += " " + selected["screening_note"]

            selected["selection_note"] = selection_note
            selected["skipped_promotional"] = skipped_promotional

            secilen_videolar.append(selected)

            logging.info(
                "SEÇİLEN VİDEO | %s | %s | %s",
                channel_name,
                selected["title"],
                selected["url"],
            )

        else:
            selection_note = (
                f"Kontrol edilen {len(channel_videos)} videoda "
                "analize uygun aday bulunamadı."
            )

            logging.warning(
                "%s: %s",
                channel_name,
                selection_note,
            )

        kanal_durumlari.append({
            "channel": channel_name,
            "handle": handle,
            "checked_count": len(channel_videos),
            "selected_title": (
                selected["title"] if selected else None
            ),
            "selected_url": (
                selected["url"] if selected else None
            ),
            "selection_note": selection_note,
            "skipped_promotional": skipped_promotional,
        })

    logging.info(
        "Video seçimi tamamlandı. "
        "Seçilen video sayısı: %s/%s",
        len(secilen_videolar),
        len(CHANNEL_HANDLES),
    )

    for index, video in enumerate(
        secilen_videolar,
        start=1,
    ):
        logging.info(
            "ANALİZ ADAYI %s | %s | %s",
            index,
            video["channel"],
            video["title"],
        )

    # Bazı kanallarda uygun aday bulunamasa bile bulunan videolar
    # analiz edilir. Hiç video kalmadıysa kontrollü hata verilir.
    if not secilen_videolar:
        raise RuntimeError(
            "Üç kanalda da analiz için uygun video bulunamadı."
        )

    return secilen_videolar, kanal_durumlari


# ============================================================
# 4. GEMINI INTERACTIONS SONUCUNU BEKLE
# ============================================================

def interaction_sonucunu_bekle(
    client,
    interaction,
    timeout_seconds=GEMINI_TIMEOUT_SECONDS,
):
    start = time.monotonic()
    transient_poll_errors = 0

    while True:

        raw_status = getattr(
            interaction,
            "status",
            "",
        )

        status = str(raw_status).lower().split(".")[-1]

        if status not in (
            "in_progress",
            "pending",
            "running",
            "queued",
        ):
            break

        if time.monotonic() - start > timeout_seconds:
            raise TimeoutError(
                "Gemini arka plan işlemi zaman aşımına uğradı."
            )

        time.sleep(5)

        try:
            interaction = client.interactions.get(
                interaction.id
            )
            transient_poll_errors = 0

        except Exception as error:
            transient_poll_errors += 1

            logging.warning(
                "Gemini işlem durumu sorgulanamadı (%s/3): %s",
                transient_poll_errors,
                error,
            )

            if transient_poll_errors >= 3:
                raise

            time.sleep(5)

    raw_status = getattr(
        interaction,
        "status",
        "",
    )

    status = str(raw_status).lower().split(".")[-1]

    if status not in (
        "completed",
        "succeeded",
        "success",
    ):
        try:
            details = interaction.model_dump(
                exclude_none=True
            )
        except Exception:
            details = repr(interaction)

        logging.error(
            "Gemini işlemi tamamlanamadı. Durum=%s, ayrıntı=%r",
            status,
            details,
        )

        errors = (
            details.get("errors", [])
            if isinstance(details, dict)
            else []
        )

        error_codes = ", ".join(
            str(entry.get("code", "unknown"))
            for entry in errors
            if isinstance(entry, dict)
        )

        error_messages = "; ".join(
            str(entry.get("message", ""))
            for entry in errors
            if isinstance(entry, dict)
        )

        raise RuntimeError(
            f"Gemini işlemi başarısız. Durum: {status}. "
            f"API hata kodları: {error_codes or 'ayrıntı yok'}. "
            f"API mesajı: {error_messages or 'ayrıntı yok'}."
        )

    return interaction


# ============================================================
# 5. PİYASA VERİLERİNİ AL
# ============================================================

def piyasa_verisini_getir(
    name,
    symbol,
    unit,
    decimals,
):
    url = YAHOO_API_BASE + quote(
        symbol,
        safe="",
    )

    data = api_get(
        url,
        {
            "range": "1mo",
            "interval": "1d",
        },
    )

    chart = data.get("chart", {})

    if chart.get("error"):
        raise RuntimeError(
            f"Yahoo Finance hatası: {chart['error']}"
        )

    results = chart.get("result") or []

    if not results:
        raise RuntimeError(
            f"{symbol} için piyasa verisi bulunamadı."
        )

    result = results[0]
    meta = result.get("meta", {})

    timestamps = result.get("timestamp") or []

    quote_data = (
        result.get("indicators", {}).get("quote") or [{}]
    )[0]

    closes = quote_data.get("close") or []
    highs = quote_data.get("high") or []
    lows = quote_data.get("low") or []

    valid_bars = []

    for i, timestamp in enumerate(timestamps):

        close = closes[i] if i < len(closes) else None
        high = highs[i] if i < len(highs) else None
        low = lows[i] if i < len(lows) else None

        try:
            close_float = float(close)
        except (TypeError, ValueError):
            continue

        if timestamp is None or not math.isfinite(close_float):
            continue

        try:
            high_float = (
                float(high)
                if high is not None
                and math.isfinite(float(high))
                else None
            )
        except (TypeError, ValueError):
            high_float = None

        try:
            low_float = (
                float(low)
                if low is not None
                and math.isfinite(float(low))
                else None
            )
        except (TypeError, ValueError):
            low_float = None

        valid_bars.append({
            "timestamp": int(timestamp),
            "close": close_float,
            "high": high_float,
            "low": low_float,
        })

    price = meta.get("regularMarketPrice")
    timestamp = meta.get("regularMarketTime")

    try:
        price = float(price) if price is not None else None

        if price is not None and not math.isfinite(price):
            price = None

    except (TypeError, ValueError):
        price = None

    if price is None:
        if not valid_bars:
            raise RuntimeError(
                f"{symbol} için geçerli fiyat yok."
            )

        price = valid_bars[-1]["close"]
        timestamp = valid_bars[-1]["timestamp"]

    if timestamp is None or int(timestamp) <= 0:
        if not valid_bars:
            raise RuntimeError(
                f"{symbol} için fiyat zaman damgası yok."
            )

        timestamp = valid_bars[-1]["timestamp"]

    observed_at = datetime.fromtimestamp(
        int(timestamp),
        tz=timezone.utc,
    ).astimezone(TIMEZONE)

    recent_bars = valid_bars[-5:]

    recent_highs = [
        bar["high"]
        for bar in recent_bars
        if bar["high"] is not None
    ]

    recent_lows = [
        bar["low"]
        for bar in recent_bars
        if bar["low"] is not None
    ]

    return {
        "name": name,
        "symbol": symbol,
        "unit": unit,
        "decimals": decimals,
        "price": price,
        "observed_at": observed_at,
        "retrieved_at": istanbul_saati(),
        "high_5d": max(recent_highs) if recent_highs else None,
        "low_5d": min(recent_lows) if recent_lows else None,
        "source": "Yahoo Finance",
        "source_url": (
            "https://finance.yahoo.com/quote/"
            + quote(symbol, safe="")
            + "/"
        ),
    }


def piyasa_verilerini_getir():
    data = []

    for name, symbol, unit, decimals in MARKET_ASSETS:

        try:
            item = piyasa_verisini_getir(
                name,
                symbol,
                unit,
                decimals,
            )

            logging.info(
                "PİYASA VERİSİ | %s | Fiyat: %s | "
                "Veri zamanı: %s | Alınma: %s",
                name,
                item["price"],
                item["observed_at"].isoformat(),
                item["retrieved_at"].isoformat(),
            )

            data.append(item)

        except Exception as error:
            logging.exception(
                "%s fiyatı alınamadı.",
                symbol,
            )

            data.append({
                "name": name,
                "symbol": symbol,
                "unit": unit,
                "decimals": decimals,
                "price": None,
                "observed_at": None,
                "retrieved_at": istanbul_saati(),
                "high_5d": None,
                "low_5d": None,
                "source": "Yahoo Finance",
                "source_url": (
                    "https://finance.yahoo.com/quote/"
                    + quote(symbol, safe="")
                    + "/"
                ),
                "error": str(error),
            })

    if not any(
        item["price"] is not None
        for item in data
    ):
        logging.error(
            "Hiçbir piyasa fiyatı alınamadı. "
            "Bülten fiyat verisi olmadan hazırlanacak."
        )

    return data


def piyasa_metni_olustur(piyasa_verileri):
    lines = []

    for item in piyasa_verileri:

        if item["price"] is None:
            lines.append(
                f"{item['name']}: VERİ ALINAMADI; "
                f"sembol: {item['symbol']}."
            )
            continue

        low = (
            fiyat_formatla(
                item["low_5d"],
                item["decimals"],
            )
            if item["low_5d"] is not None
            else "Yok"
        )

        high = (
            fiyat_formatla(
                item["high_5d"],
                item["decimals"],
            )
            if item["high_5d"] is not None
            else "Yok"
        )

        lines.append(
            f"{item['name']} ({item['symbol']}): "
            f"{fiyat_formatla(item['price'], item['decimals'])} "
            f"{item['unit']}; "
            f"son veri zamanı: "
            f"{tarih_formatla(item['observed_at'])}; "
            f"son 5 günlük mum düşük/yüksek: "
            f"{low}/{high}; "
            f"kaynak: {item['source_url']}"
        )

    return "\n".join(lines)


# ============================================================
# 6. KANAL SEÇİM RAPORU
# ============================================================

def kanal_secim_notlarini_olustur(kanal_durumlari):
    lines = []

    for item in kanal_durumlari:

        lines.append(
            f"{item['channel']}: {item['selection_note']}"
        )

        for skipped in item.get(
            "skipped_promotional",
            [],
        ):
            lines.append(
                f"  Atlanan video: {skipped['title']} | "
                f"Gerekçe: {skipped['reason']} | "
                f"URL: {skipped['url']}"
            )

    return "\n".join(lines)


# ============================================================
# 7. SEÇİLEN EN FAZLA 3 VİDEOYLA GEMINI BÜLTENİ OLUŞTUR
# ============================================================

def gemini_ile_guncel_bulten_uret(
    videolar,
    piyasa_verileri,
    kanal_durumlari,
):
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY bulunamadı."
        )

    if not videolar:
        raise RuntimeError(
            "Gemini analizi için seçilmiş video yok."
        )

    client = genai.Client(
        api_key=GEMINI_API_KEY
    )

    now = istanbul_saati()

    market_text = piyasa_metni_olustur(
        piyasa_verileri
    )

    video_metadata = []

    for i, video in enumerate(
        videolar,
        start=1,
    ):
        video_metadata.append(
            f"{i}. Kanal: {video['channel']}\n"
            f"Başlık: {video['title']}\n"
            f"Yayın: {video['published_at']}\n"
            f"URL: {video['url']}\n"
            f"Seçim notu: {video.get('selection_note', '')}"
        )

    video_metadata_text = "\n\n".join(
        video_metadata
    )

    selection_notes = kanal_secim_notlarini_olustur(
        kanal_durumlari
    )

    prompt = f"""
Sen deneyimli bir finansal araştırma analisti ve HTML e-posta
tasarımcısısın. Türkçe, kaynak odaklı ve yatırım tavsiyesi
vermeyen bir günlük finans bülteni hazırla.

RAPOR TARİHİ:
{now.strftime('%d.%m.%Y %H:%M:%S')} Türkiye saati.

==================================================
A. VİDEO SEÇİMİ
==================================================

Her kanaldan en yeni uygun video seçilmeye çalışıldı.
Daha yeni bir video yalnızca başlık ve içerik sinyalleri
finans dışı reklam/ürün tanıtımına işaret ediyorsa atlandı.

KANAL SEÇİM NOTLARI:
{selection_notes}

GEMINI İLE ANALİZ EDİLMESİ İSTENEN VİDEOLAR:
{video_metadata_text}

VİDEO ANALİZİ KURALLARI:

- Her videonun gerçek içeriğini incelemeye çalış.
- Başlığa dayanarak konuşmacının söylemediği görüşleri,
  rakamları veya hedefleri ona atfetme.
- Videoya erişemiyorsan veya içeriği doğrulayamıyorsan bunu
  açıkça belirt; içeriği izlemiş gibi davranma.
- Destek, direnç ve hedefleri yalnızca videoda gerçekten
  belirtilmişse yaz.
- Her videoyu "Analiz edildi", "Analiz dışı" veya
  "İçerik doğrulanamadı" durumlarından biriyle göster.

ÖNEMLİ SPONSORLUK KURALI:

- Bir videonun başlığında veya açıklamasında #işbirliği,
  reklam, sponsorluk, sponsor bağlantısı veya benzeri bir
  bildirim bulunması, videonun tamamının reklam olduğu
  anlamına gelmez.
- Video finansal piyasalar, ekonomi, şirketler, fonlar, faiz,
  rezervler, borsa veya yatırım konularını ele alıyorsa
  finansal içeriğini analiz et.
- Videoda sponsorlu bir bölüm varsa bu bölümü finansal görüş
  olarak kullanma; ancak sponsorlu bölüm nedeniyle videonun
  geri kalanındaki piyasa analizini atlama.
- Sponsorluk bildiriminin analize etkisi varsa bunu raporda
  şeffaf biçimde belirt.
- Videonun gerçek içeriğine erişemiyorsan bunu açıkça söyle.

==================================================
B. DOĞRUDAN ALINAN PİYASA VERİLERİ
==================================================

{market_text}

PİYASA VERİSİ KURALLARI:

1. Yukarıdaki fiyatlar Python tarafından Yahoo Finance'tan
   alınmıştır. Bu sayılar dışında doğrulanmış anlık fiyat
   varmış gibi başka rakamlar verme.

2. Son veri zamanı rapor saatinden eski olabilir. Kapanış
   veya gecikmeli veriyi anlık fiyat diye sunma.

3. GC=F altın vadeli işlem, SI=F gümüş vadeli işlem
   sembolleridir; spot fiyat olarak adlandırma.

4. Son beş günlük mumların düşük/yüksek aralığını kesin
   destek/direnç seviyesi olarak sunma.

5. Veri alınamayan varlık için fiyat uydurma.

6. Videoda geçen eski fiyat hedeflerini güncel fiyat olarak
   gösterme. Video hedefi ile güncel piyasa verisini ayır.

==================================================
C. GÜNCEL GÜNDEM ARAŞTIRMASI
==================================================

Google Search aracını kullanarak rapor tarihine en yakın
güvenilir kaynaklardan şu konuları araştır:

- TCMB, faiz ve enflasyon
- SPK ve yatırım fonları
- Önemli şirket bilançoları
- ABD piyasaları ve merkez bankaları
- Jeopolitik gelişmeler ve emtia fiyatlarını etkileyen haberler

En önemli 2-3 konuyu seç. Yayın tarihini, kaynağını ve
olası piyasa etkisini belirt. Doğrulanmamış veya gelecek
tarihli haberi olmuş gibi sunma. Kaynak bağlantıları ver.

==================================================
D. BÜLTEN BÖLÜMLERİ
==================================================

1. STRATEJİ NOTU

İki cümleyle piyasalardaki ortak temaları, ana belirsizlikleri
ve riskleri belirt. Görüşler ayrışıyorsa yapay bir konsensüs
oluşturma.

2. EMTİALAR

Altın, gümüş ve Brent petrol için kısa görünüm, katalizörler
ve riskler. Yön rozeti kullanılabilir; veri yetersizse kesin
yön veya hedef uydurma.

3. MAJÖR PİYASALAR

BIST 100, Nasdaq Composite, Bitcoin, USD/TRY ve EUR/TRY
için görünüm ve önemli riskler. Destek/direnç/hedefleri
yalnızca kaynak bunları destekliyorsa belirt.

USD/TRY artışının TL'nin dolar karşısında; EUR/TRY
artışının TL'nin euro karşısında değer kaybı anlamına
geldiğini doğru açıkla.

4. GÜNCEL MAKRO GÜNDEM

Doğrulanmış 2-3 gelişme; tarih, kaynak ve olası etki.

5. UZMAN SENTEZİ

Seçilmiş videolardaki ortak ve ayrışan görüşleri karşılaştır.
Konuşmacının gerçek görüşüyle kendi analitik çıkarımını
birbirinden ayır.

6. RİSKLER VE TAKİP LİSTESİ

Önemli riskler ve takip edilecek doğrulanmış ekonomik
gelişmeler. Takvim bilgisi uydurma.

7. VİDEO DÖKÜMÜ

Her kanal için seçilmiş videoyu, seçim gerekçesini ve
analiz durumunu göster. Atlanan reklam/tanıtım adaylarını
da başlıklarıyla listele.

Seçim yapılmayan bir kanal varsa bunu açıkça belirt.
Eksik kanala ait piyasa görüşü uydurma.

8. KAYNAKLAR

Haber, analiz ve dış verilerin tıklanabilir bağlantılarını sun.

Son bölümde şu uyarıyı ekle:
"Bu içerik yatırım tavsiyesi değildir."

==================================================
E. HTML TASARIMI
==================================================

Yalnızca tam HTML dokümanı üret. Markdown veya kod bloğu
kullanma. Türkçe, mobil uyumlu ve okunaklı tasarım oluştur.
Inline CSS tercih et. JavaScript veya uzak görsel kullanma.

Python'ın ayrıca ekleyeceği doğrudan fiyat tablosunu yeniden
oluşturma. HTML içinde ikinci bir piyasa fiyat tablosu üretme.
Kaynak bağlantıları görünür metne sahip olsun.

Finansal olguları yorumlardan ayır. Video içeriğine erişim
başarısızsa bunu açıkça belirt. Yanıtın ilk karakteri HTML
dokümanına ait olsun; HTML dışında açıklama ekleme.
"""

    video_inputs = [
        {
            "type": "video",
            "uri": video["url"],
        }
        for video in videolar
    ]

    logging.info(
        "Gemini tek istekte %s seçilmiş video ve güncel gündemi "
        "analiz ediyor.",
        len(videolar),
    )

    # Tek istek kullanılır. Kota hatasında aynı isteği tekrar tekrar
    # göndermek yerine kontrollü biçimde yedek rapora geçilir.
    interaction = client.interactions.create(
        model=GEMINI_MODEL,
        input=[
            {"type": "text", "text": prompt}
        ] + video_inputs,
        tools=[
            {"type": "google_search"}
        ],
        background=True,
    )

    interaction = interaction_sonucunu_bekle(
        client,
        interaction,
        timeout_seconds=GEMINI_TIMEOUT_SECONDS,
    )

    html_code = getattr(
        interaction,
        "output_text",
        None,
    )

    if not html_code or not html_code.strip():
        raise RuntimeError(
            "Gemini boş bülten döndürdü."
        )

    html_code = html_belgesini_duzenle(
        html_code
    )

    logging.info(
        "Gemini HTML bülten hazır. Karakter sayısı: %s",
        len(html_code),
    )

    return html_code


# ============================================================
# 8. DOĞRUDAN PİYASA FİYATI HTML TABLOSU
# ============================================================

def piyasa_html_tablosu(veriler):
    rows = []

    for item in veriler:

        if item["price"] is None:
            price_text = "VERİ ALINAMADI"
            time_text = "Doğrulanamadı"
            range_text = "—"

        else:
            price_text = fiyat_formatla(
                item["price"],
                item["decimals"],
            )

            time_text = tarih_formatla(
                item["observed_at"]
            )

            if (
                item["high_5d"] is not None
                and item["low_5d"] is not None
            ):
                range_text = (
                    fiyat_formatla(
                        item["low_5d"],
                        item["decimals"],
                    )
                    + " – "
                    + fiyat_formatla(
                        item["high_5d"],
                        item["decimals"],
                    )
                )
            else:
                range_text = "—"

        source_link = (
            '<a href="'
            + html.escape(
                item["source_url"],
                quote=True,
            )
            + '">Kaynak</a>'
        )

        rows.append(
            "<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>"
            f"{html.escape(item['name'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;"
            f"text-align:right;font-weight:bold'>"
            f"{html.escape(price_text)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>"
            f"{html.escape(item['unit'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>"
            f"{html.escape(time_text)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>"
            f"{html.escape(range_text)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>"
            f"{source_link}</td>"
            "</tr>"
        )

    retrieved_at = tarih_formatla(
        istanbul_saati()
    )

    return f"""
    <section style="margin:24px 0;font-family:Arial,sans-serif;color:#202124;">
      <h2 style="font-size:21px;margin-bottom:8px">
        Piyasa Fiyatları — Doğrudan Veri Kaynağı
      </h2>
      <p style="font-size:12px;color:#666">
        Veri sorgulama zamanı: {html.escape(retrieved_at)}.
        Her fiyatın yanında sağlayıcının bildirdiği son veri zamanı yer alır.
      </p>
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
          <thead>
            <tr style="background:#eef1f5">
              <th style="padding:9px;text-align:left">Varlık</th>
              <th style="padding:9px;text-align:right">Fiyat</th>
              <th style="padding:9px;text-align:left">Birim</th>
              <th style="padding:9px;text-align:left">Son veri zamanı</th>
              <th style="padding:9px;text-align:left">Son 5 günlük mum aralığı</th>
              <th style="padding:9px;text-align:left">Kaynak</th>
            </tr>
          </thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
      <p style="font-size:11px;color:#777">
        Kaynak: Yahoo Finance. Fiyatlar gecikmeli olabilir.
        GC=F ve SI=F vadeli işlem sembolleridir. Beş günlük
        yüksek/düşük değerleri kesin destek veya direnç seviyesi değildir.
      </p>
    </section>
    """


def fiyat_tablosunu_html_e_pkle(
    html_content,
    piyasa_verileri,
):
    table = piyasa_html_tablosu(
        piyasa_verileri
    )

    match = re.search(
        r"<body\b[^>]*>",
        html_content,
        flags=re.IGNORECASE,
    )

    if not match:
        html_content = html_sayfa(
            html_content
        )

        match = re.search(
            r"<body\b[^>]*>",
            html_content,
            flags=re.IGNORECASE,
        )

    if not match:
        raise RuntimeError(
            "HTML gövdesi bulunamadı; fiyat tablosu eklenemedi."
        )

    return (
        html_content[:match.end()]
        + table
        + html_content[match.end():]
    )


# ============================================================
# 9. GEMINI BAŞARISIZ OLURSA GÖNDERİLECEK YEDEK BÜLTEN
# ============================================================

def yedek_bulten_uret(
    videolar,
    kanal_durumlari,
    hata,
):
    now_text = istanbul_saati().strftime(
        "%d.%m.%Y %H:%M:%S (TSİ)"
    )

    durum = html.escape(
        gemini_hata_mesaji(hata)
    )

    channel_rows = []

    for item in kanal_durumlari:

        channel_rows.append(
            "<li style='margin:10px 0'>"
            f"<strong>{html.escape(item['channel'])}</strong><br>"
            f"{html.escape(item['selection_note'])}"
        )

        for skipped in item.get(
            "skipped_promotional",
            [],
        ):
            channel_rows.append(
                "<div style='margin:6px 0 6px 14px;"
                "font-size:12px;color:#666'>"
                f"Atlanan aday: {html.escape(skipped['title'])} — "
                f"{html.escape(skipped['reason'])}"
                "</div>"
            )

        channel_rows.append("</li>")

    channel_html = (
        "<h2>Kanal seçim raporu</h2><ul>"
        + "".join(channel_rows)
        + "</ul>"
        if channel_rows
        else "<p>Kanal seçim raporu oluşturulamadı.</p>"
    )

    if videolar:
        video_rows = []

        for video in videolar:
            video_rows.append(
                "<li style='margin:10px 0'>"
                f"<strong>{html.escape(video['channel'])}</strong> — "
                f"<a href='{html.escape(video['url'], quote=True)}'>"
                f"{html.escape(video['title'])}</a>"
                f"<br><span style='color:#666;font-size:12px'>"
                f"{html.escape(video.get('published_at', ''))}<br>"
                f"{html.escape(video.get('selection_note', ''))}"
                "</span></li>"
            )

        video_html = (
            "<h2>Analiz için seçilen videolar</h2>"
            "<ol>"
            + "".join(video_rows)
            + "</ol>"
        )

    else:
        video_html = (
            "<p>Bu çalıştırmada analiz için video seçilemedi.</p>"
        )

    body = f"""
    <main style="max-width:900px;margin:0 auto;padding:16px;
                font-family:Arial,sans-serif;color:#202124;line-height:1.55">
      <header style="border-bottom:3px solid #263b59;padding-bottom:12px">
        <h1 style="margin-bottom:4px">Günlük Finans ve Piyasa Bülteni</h1>
        <p style="color:#666;margin-top:0">
          Oluşturulma zamanı: {html.escape(now_text)}
        </p>
      </header>

      <section style="background:#fff7e6;border-left:4px solid #d97706;
                      padding:14px;margin:18px 0">
        <h2 style="margin-top:0;font-size:18px">
          Kısmi rapor — otomatik analiz tamamlanamadı
        </h2>
        <p>{durum}</p>
        <p>
          Bu e-posta eksikliği gizlememek için kısmi rapor olarak
          gönderilmiştir. Aşağıda doğrudan veri kaynağından alınabilen
          fiyatlar ve video seçim raporu sunulmuştur.
        </p>
      </section>

      {channel_html}
      {video_html}

      <p style="font-size:12px;color:#666">
        Video bağlantılarının listelenmesi videoların analiz edildiği
        anlamına gelmez.
      </p>

      <hr style="border:none;border-top:1px solid #ddd;margin:24px 0">

      <p style="font-size:12px;color:#666">
        Bu içerik yatırım tavsiyesi değildir. Piyasa fiyatları
        gecikmeli olabilir ve yatırım kararı için tek başına
        kullanılmamalıdır.
      </p>
    </main>
    """

    return html_sayfa(body)


# ============================================================
# 10. E-POSTA GÖNDER
# ============================================================

def html_to_plain_text(html_content):
    text = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        " ",
        html_content,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text = re.sub(
        r"<br\s*/?>|</p>|</div>|</tr>|</h[1-6]>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = html.unescape(text)

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    text = re.sub(
        r"\n\s*\n+",
        "\n\n",
        text,
    )

    return text.strip()


def eposta_gonder(html_icerik):
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        raise RuntimeError(
            "GMAIL_USER veya GMAIL_PASS bulunamadı."
        )

    msg = MIMEMultipart("alternative")

    subject = (
        "Günlük Finans & Piyasa Bülteni — "
        + istanbul_saati().strftime("%d.%m.%Y")
    )

    msg["Subject"] = Header(
        subject,
        "utf-8",
    )

    msg["From"] = EMAIL_ADDRESS
    msg["To"] = TO_EMAIL

    msg.attach(
        MIMEText(
            html_to_plain_text(html_icerik),
            "plain",
            "utf-8",
        )
    )

    msg.attach(
        MIMEText(
            html_icerik,
            "html",
            "utf-8",
        )
    )

    logging.info(
        "E-posta gönderiliyor: %s",
        TO_EMAIL,
    )

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        timeout=40,
    ) as server:

        server.login(
            EMAIL_ADDRESS,
            EMAIL_PASSWORD,
        )

        server.sendmail(
            EMAIL_ADDRESS,
            [TO_EMAIL],
            msg.as_string(),
        )

    logging.info(
        "Bülten başarıyla gönderildi: %s",
        TO_EMAIL,
    )


# ============================================================
# 11. ANA PROGRAM
# ============================================================

def main():
    logging.info(
        "Günlük bülten başladı: %s",
        istanbul_saati().isoformat(),
    )

    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        raise RuntimeError(
            "GMAIL_USER veya GMAIL_PASS bulunamadı."
        )

    videolar = []
    kanal_durumlari = []
    youtube_hatasi = None

    # Önce kanallardan video seç.
    try:
        videolar, kanal_durumlari = son_videolari_getir()

    except Exception as error:
        youtube_hatasi = error

        logging.exception(
            "YouTube videoları seçilemedi; "
            "kısmi bülten hazırlanacak."
        )

    # Piyasa fiyatlarını mümkün olduğunca geç al.
    try:
        piyasa_verileri = piyasa_verilerini_getir()

    except Exception:
        logging.exception(
            "Piyasa verileri alınırken beklenmeyen hata oluştu."
        )

        piyasa_verileri = [
            {
                "name": name,
                "symbol": symbol,
                "unit": unit,
                "decimals": decimals,
                "price": None,
                "observed_at": None,
                "retrieved_at": istanbul_saati(),
                "high_5d": None,
                "low_5d": None,
                "source": "Yahoo Finance",
                "source_url": (
                    "https://finance.yahoo.com/quote/"
                    + quote(symbol, safe="")
                    + "/"
                ),
            }
            for name, symbol, unit, decimals in MARKET_ASSETS
        ]

    # Video seçimi başarısız değilse tek Gemini isteğiyle analiz et.
    try:
        if youtube_hatasi:
            raise RuntimeError(
                f"YouTube API/video seçimi başarısız: {youtube_hatasi}"
            )

        html_bulten = gemini_ile_guncel_bulten_uret(
            videolar,
            piyasa_verileri,
            kanal_durumlari,
        )

    except Exception as error:
        logging.exception(
            "Tam bülten oluşturulamadı; kısmi rapor hazırlanacak."
        )

        html_bulten = yedek_bulten_uret(
            videolar,
            kanal_durumlari,
            error,
        )

    # Fiyat tablosu yalnızca Python'ın doğrudan aldığı verilerden eklenir.
    html_bulten = html_belgesini_duzenle(
        html_bulten
    )

    html_bulten = fiyat_tablosunu_html_e_pkle(
        html_bulten,
        piyasa_verileri,
    )

    eposta_gonder(
        html_bulten
    )

    logging.info(
        "Tüm işlemler tamamlandı: %s",
        istanbul_saati().isoformat(),
    )


if __name__ == "__main__":
    try:
        main()

    except Exception:
        logging.exception(
            "BÜLTEN OLUŞTURULAMADI VEYA GÖNDERİLEMEDİ."
        )
        raise
