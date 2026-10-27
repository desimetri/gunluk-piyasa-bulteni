import os
import re
import html
import time
import math
import logging
import smtplib
import requests

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

# Yahoo Finance'ta XAUUSD=X ve XAGUSD=X bazı çağrılarda 404
# döndürdüğünden GC=F ve SI=F vadeli işlem sembolleri kullanılıyor.
# Bu fiyatlar spot altın/gümüş fiyatı olarak etiketlenmez.
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

REQUEST_TIMEOUT = 40
GEMINI_TIMEOUT_SECONDS = 5400

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


# ============================================================
# GENEL YARDIMCI FONKSİYONLAR
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
    return text.replace(",", "#").replace(".", ",").replace("#", ".")


def tarih_formatla(value):
    if not value:
        return "Bilinmiyor"
    return value.astimezone(TIMEZONE).strftime("%d.%m.%Y %H:%M:%S (TSİ)")


def html_sayfa(body, title="Günlük Finans ve Piyasa Bülteni"):
    """Verilen HTML içeriğini eksiksiz HTML belgesine dönüştürür."""
    return (
        "<!DOCTYPE html><html lang='tr'><head>"
        "<meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
        f"<title>{html.escape(title)}</title>"
        "</head><body style='font-family:Arial,sans-serif;color:#202124;'>"
        f"{body}</body></html>"
    )


def html_belgesini_duzenle(content):
    """Gemini yanıtını, fiyat tablosu eklenebilecek bir HTML belgesine çevirir."""
    content = (content or "").strip()
    content = re.sub(
        r"^\s*```(?:html)?\s*", "", content, flags=re.IGNORECASE
    )
    content = re.sub(r"\s*```\s*$", "", content).strip()

    if not content:
        raise RuntimeError("HTML içeriği boş.")

    if "<html" not in content.lower():
        content = html_sayfa(content)

    if "<body" not in content.lower():
        body_tag = re.search(r"<html\b[^>]*>", content, flags=re.IGNORECASE)
        if body_tag:
            head_end = re.search(r"</head\s*>", content, flags=re.IGNORECASE)
            if head_end:
                body_open = "<body style='font-family:Arial,sans-serif;color:#202124;'>"
                html_close = re.search(r"</html\s*>", content, flags=re.IGNORECASE)
                if html_close:
                    before_close = content[:html_close.start()]
                    after_close = content[html_close.start():]
                    content = (
                        before_close[:head_end.end()]
                        + body_open
                        + before_close[head_end.end():]
                        + "</body>"
                        + after_close
                    )
                else:
                    content = content[:head_end.end()] + body_open + content[head_end.end():] + "</body>"
            else:
                content = (
                    content[:body_tag.end()]
                    + "<head><meta charset='UTF-8'></head>"
                    + "<body style='font-family:Arial,sans-serif;color:#202124;'>"
                    + content[body_tag.end():]
                    + "</body>"
                )
        else:
            content = html_sayfa(content)

    if len(content) < 300:
        raise RuntimeError("Üretilen HTML beklenenden kısa.")
    return content


def gemini_hata_mesaji(error):
    """E-postada kullanıcıya gösterilecek kısa ve güvenli hata açıklaması."""
    message = str(error).lower()
    if any(token in message for token in (
        "resource_exhausted", "quota", "rate limit", "429",
        "requests per day", "limit exceeded",
    )):
        return (
            "Gemini API kullanım kotası dolduğu için otomatik video ve haber "
            "analizi bu çalıştırmada tamamlanamadı. Kota yenilendiğinde "
            "sonraki zamanlanmış çalıştırma yeniden deneyebilir."
        )
    if any(token in message for token in ("503", "unavailable", "high demand")):
        return (
            "Gemini hizmeti geçici olarak kullanılamadığı için otomatik analiz "
            "tamamlanamadı. Sonraki zamanlanmış çalıştırma yeniden deneyebilir."
        )
    return (
        "Gemini üzerinden otomatik analiz bu çalıştırmada tamamlanamadı. "
        "Aşağıda yalnızca doğrudan veri kaynağından alınabilen piyasa verileri "
        "ve bulunan video bağlantıları sunulmuştur."
    )


# ============================================================
# 1. YOUTUBE KANALLARININ SON 3 VİDEOSUNU BUL
# ============================================================

def son_videolari_getir():
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY bulunamadı. GitHub Secrets ayarını kontrol et.")

    videolar = []

    for handle in CHANNEL_HANDLES:
        logging.info("YouTube kanalı aranıyor: %s", handle)
        channel_data = api_get(
            f"{YOUTUBE_API_BASE}/channels",
            {"part": "id,snippet,contentDetails", "forHandle": handle, "key": YOUTUBE_API_KEY},
        )

        channels = channel_data.get("items", [])
        if not channels:
            raise RuntimeError(f"YouTube kanalı bulunamadı: {handle}")

        channel = channels[0]
        channel_name = channel["snippet"].get("title", handle)
        uploads_playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]

        playlist_data = api_get(
            f"{YOUTUBE_API_BASE}/playlistItems",
            {
                "part": "snippet,contentDetails",
                "playlistId": uploads_playlist,
                "maxResults": 10,
                "key": YOUTUBE_API_KEY,
            },
        )

        channel_videos = []
        for item in playlist_data.get("items", []):
            snippet = item.get("snippet", {})
            details = item.get("contentDetails", {})
            video_id = (
                details.get("videoId")
                or snippet.get("resourceId", {}).get("videoId")
            )
            if not video_id:
                continue

            title = snippet.get("title", "").strip()
            if title.lower() in ("private video", "deleted video"):
                continue

            published_at = (
                details.get("videoPublishedAt")
                or snippet.get("publishedAt")
                or ""
            )

            channel_videos.append({
                "channel": channel_name,
                "handle": handle,
                "title": title or "Başlıksız video",
                "published_at": published_at,
                "url": f"https://www.youtube.com/watch?v={video_id}",
            })

        channel_videos.sort(key=lambda video: video["published_at"], reverse=True)
        channel_videos = channel_videos[:3]

        if len(channel_videos) != 3:
            raise RuntimeError(
                f"{channel_name} kanalından 3 video alınamadı. "
                f"Bulunan video sayısı: {len(channel_videos)}"
            )

        videolar.extend(channel_videos)
        logging.info("%s: %s video bulundu.", channel_name, len(channel_videos))

    if len(videolar) != 9:
        raise RuntimeError("Toplam 9 video bulunamadı.")

    for index, video in enumerate(videolar, start=1):
        logging.info(
            "VİDEO %s | %s | %s | %s",
            index, video["channel"], video["title"], video["url"]
        )
    return videolar


# ============================================================
# 2. GEMINI İLE VİDEOLARI TOPLU ANALİZ ET VE BÜLTENİ OLUŞTUR
# ============================================================

def interaction_sonucunu_bekle(client, interaction, timeout_seconds=GEMINI_TIMEOUT_SECONDS):
    start = time.monotonic()
    transient_poll_errors = 0

    while True:
        raw_status = getattr(interaction, "status", "")
        status = str(raw_status).lower().split(".")[-1]

        if status not in ("in_progress", "pending", "running", "queued"):
            break

        if time.monotonic() - start > timeout_seconds:
            raise TimeoutError("Gemini arka plan işlemi zaman aşımına uğradı.")

        time.sleep(5)
        try:
            interaction = client.interactions.get(interaction.id)
            transient_poll_errors = 0
        except Exception as error:
            transient_poll_errors += 1
            logging.warning("Gemini işlem durumu sorgulanamadı (%s/3): %s", transient_poll_errors, error)
            if transient_poll_errors >= 3:
                raise
            time.sleep(5)

    raw_status = getattr(interaction, "status", "")
    status = str(raw_status).lower().split(".")[-1]

    if status not in ("completed", "succeeded", "success"):
        try:
            details = interaction.model_dump(exclude_none=True)
        except Exception:
            details = repr(interaction)
        logging.error("Gemini işlemi tamamlanamadı. Durum=%s, ayrıntı=%r", status, details)
        raise RuntimeError(f"Gemini işlemi tamamlanamadı. Durum: {status}. Ayrıntılar Actions logunda.")

    return interaction


def piyasa_metni_olustur(piyasa_verileri):
    lines = []
    for item in piyasa_verileri:
        if item["price"] is None:
            lines.append(f"{item['name']}: VERİ ALINAMADI; sembol: {item['symbol']}.")
            continue

        low = fiyat_formatla(item["low_5d"], item["decimals"]) if item["low_5d"] is not None else "Yok"
        high = fiyat_formatla(item["high_5d"], item["decimals"]) if item["high_5d"] is not None else "Yok"
        lines.append(
            f"{item['name']} ({item['symbol']}): "
            f"{fiyat_formatla(item['price'], item['decimals'])} {item['unit']}; "
            f"son veri zamanı: {tarih_formatla(item['observed_at'])}; "
            f"son 5 günlük mum düşük/yüksek: {low}/{high}; "
            f"kaynak: {item['source_url']}"
        )
    return "\n".join(lines)


def gemini_ile_guncel_bulten_uret(videolar, piyasa_verileri):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY bulunamadı.")

    client = genai.Client(api_key=GEMINI_API_KEY)
    now = istanbul_saati()
    market_text = piyasa_metni_olustur(piyasa_verileri)

    # Dokuz ayrı uygunluk ve analiz isteği yerine dokuz video tek istekte
    # gönderilir. Gemini 3.8 Flash bir istekte birden çok YouTube videosunu
    # işleyebilir; bu yöntem günlük API istek tüketimini ciddi biçimde azaltır.
    video_inputs = [
        {
            "type": "video",
            "uri": video["url"],
            "processing": "agentic",
        }
        for video in videolar
    ]

    video_metadata = "\n".join(
        f"{i}. Kanal: {v['channel']} | Başlık: {v['title']} | "
        f"Yayın: {v['published_at']} | URL: {v['url']}"
        for i, v in enumerate(videolar, start=1)
    )

    prompt = f"""
Sen deneyimli bir finansal araştırma analisti ve HTML e-posta tasarımcısısın.
Türkçe, dikkatli, kaynak odaklı ve yatırım tavsiyesi vermeyen bir günlük
finans bülteni hazırla.

RAPOR TARİHİ: {now.strftime('%d.%m.%Y %H:%M:%S')} Türkiye saati.

AŞAĞIDAKİ DOKUZ VİDEO GİRDİSİNİ TEK TEK İNCELE.
Video başlıkları/kanalları/URL'leri:
{video_metadata}

VİDEO İÇERİĞİNE DAİR KURALLAR:
- Her videonun gerçek içeriğini incelemeye çalış; yalnızca başlıktan hareketle
  konuşmacının söylemediği bir görüşü veya fiyat seviyesini ona atfetme.
- Videonun finans ve ekonomiyle ilgisi yoksa ya da sponsorlu ürün tanıtımıysa
  bunu açıkça “Analiz dışı” olarak işaretle ve finansal görüş çıkarmaya çalışma.
- Videoya erişemiyorsan, içeriği doğrulayamıyorsan veya belirli bir ifadeyi
  teyit edemiyorsan bunu açıkça belirt; içeriği izlemiş gibi davranma.
- Analiz edilebilen her video için kanal, başlık, kısa ana tez, varlık/piyasa,
  görüşün yönü, destek/direnç/hedef (yalnızca videoda gerçekten söylendiyse),
  gerekçe, katalizör ve riskleri özetle.
- Her sayısal hedefi mümkün olduğunda ilgili video başlığıyla ilişkilendir.
  Fiyat seviyesi uydurma.

DOĞRUDAN VERİ KAYNAĞINDAN ALINAN PİYASA VERİLERİ:
{market_text}

PİYASA VERİLERİ KURALLARI:
- Yukarıdaki fiyatlar Python tarafından Yahoo Finance'tan alınmıştır.
  Bu sayılar dışında doğrulanmış anlık fiyat varmış gibi başka rakam yazma.
- Son veri zamanının rapor saatinden eski olabileceğini belirt; kapanış veya
  gecikmeli fiyatı anlık fiyat diye sunma.
- Altın Vadeli İşlem (GC=F) ve Gümüş Vadeli İşlem (SI=F) vadeli işlem
  göstergeleridir; spot fiyat diye adlandırma.
- “Son 5 günlük mum düşük/yüksek” aralığını kesin destek/direnç kabul etme.
- Veri alınamayan varlık için fiyat uydurma.

GÜNCEL GÜNDEM:
Google Search aracını kullanarak rapor tarihine en yakın güvenilir kaynaklardan
TCMB, faiz, enflasyon, fon/SPK, şirket bilançoları ve jeopolitik gelişmeleri
araştır. En önemli 2-3 konuyu seç; tarihi, kaynağı ve olası piyasa etkisini
belirt. Tarihi doğrulanamayan veya gelecek tarihli haberi olmuş gibi yazma.
Kaynak bağlantılarını HTML'de tıklanabilir olarak ekle.

BÜLTENDE ŞU BÖLÜMLER OLSUN:
1. İki cümlelik strateji notu: kanalların gerçekten ortaklaştığı temalar,
   konsensüs ve ana belirsizlikler. Görüşler farklıysa yapay konsensüs kurma.
2. Emtialar: altın, gümüş ve Brent için kısa görünüm, katalizör, risk ve
   gerekiyorsa yön rozeti. Kesin yön veya hedef için yeterli veri yoksa söyle.
3. Majör piyasalar: BIST 100, Nasdaq Composite, Bitcoin, USD/TRY, EUR/TRY.
   Görüşleri ve destek/dirençleri yalnızca kaynak varsa ver. Analitik çıkarımları
   doğrudan video görüşlerinden ayır. USD/TRY veya EUR/TRY artışının TL'nin
   ilgili para birimi karşısında değer kaybı anlamına geldiğini doğru açıkla.
4. Güncel makro gündem: doğrulanmış 2-3 konu ve kaynak bağlantısı.
5. Uzman sentezi: üç kanalın ortaklaştığı ve ayrıştığı noktalar; kısa/orta
   vade farkları ve görüşlerin geçerli olabileceği koşullar.
6. Riskler ve takip listesi: sadece doğrulanabilir takvim bilgileri ve koşullar.
7. Video dökümü: dokuz videonun her birini “Analiz edildi”, “Analiz dışı” veya
   “İçerik doğrulanamadı” olarak ayrı ayrı göster.
8. Kaynaklar ve son bölümde “Bu içerik yatırım tavsiyesi değildir” uyarısı.

HTML TASARIMI:
- Yalnızca tam HTML dokümanı üret; Markdown veya ```html kullanma.
- Türkçe, mobil uyumlu ve okunaklı e-posta tasarımı; inline CSS kullan.
- JavaScript, uzak CSS veya uzak görsel kullanma.
- Üretim tarihini/saatini en üstte göster.
- Kaynak bağlantılarının görünür metni olsun.
- Python'ın ayrıca ekleyeceği doğrudan fiyat tablosunu yeniden oluşturma;
  herhangi bir fiyat tablosu oluşturma.
- Finansal olgularla analitik yorumları birbirinden ayır.
- Yanıtın ilk karakteri HTML dokümanına ait olsun; açıklama ekleme.
"""

    # Bütün videolar ve nihai bülten bir istekte işlenir. Kota hataları tekrar
    # tekrar çağrı yapılarak büyütülmez; hata halinde e-posta yedek raporla gider.
    logging.info("Gemini dokuz videoyu ve güncel gündemi tek istekte analiz ediyor.")
    interaction = client.interactions.create(
        model=GEMINI_MODEL,
        input=[{"type": "text", "text": prompt}] + video_inputs,
        tools=[{"type": "google_search"}],
        background=True,
    )

    interaction = interaction_sonucunu_bekle(
        client, interaction, timeout_seconds=GEMINI_TIMEOUT_SECONDS
    )

    html_code = getattr(interaction, "output_text", None)
    if not html_code or not html_code.strip():
        raise RuntimeError("Gemini boş bülten döndürdü.")

    html_code = html_belgesini_duzenle(html_code)
    logging.info("Gemini HTML bülten hazır. Karakter sayısı: %s", len(html_code))
    return html_code


# ============================================================
# 3. PİYASA VERİLERİNİ GEMINI'DAN BAĞIMSIZ AL
# ============================================================

def piyasa_verisini_getir(name, symbol, unit, decimals):
    url = YAHOO_API_BASE + quote(symbol, safe="")
    data = api_get(url, {"range": "1mo", "interval": "1d"})

    chart = data.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"Yahoo Finance hatası: {chart['error']}")

    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"{symbol} için piyasa verisi bulunamadı.")

    result = results[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
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
            high_float = float(high) if high is not None and math.isfinite(float(high)) else None
        except (TypeError, ValueError):
            high_float = None
        try:
            low_float = float(low) if low is not None and math.isfinite(float(low)) else None
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
            raise RuntimeError(f"{symbol} için geçerli fiyat yok.")
        price = valid_bars[-1]["close"]
        timestamp = valid_bars[-1]["timestamp"]

    if timestamp is None or int(timestamp) <= 0:
        if not valid_bars:
            raise RuntimeError(f"{symbol} için fiyat zaman damgası yok.")
        timestamp = valid_bars[-1]["timestamp"]

    observed_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(TIMEZONE)
    recent_bars = valid_bars[-5:]
    recent_highs = [bar["high"] for bar in recent_bars if bar["high"] is not None]
    recent_lows = [bar["low"] for bar in recent_bars if bar["low"] is not None]

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
        "source_url": "https://finance.yahoo.com/quote/" + quote(symbol, safe="") + "/",
    }


def piyasa_verilerini_getir():
    data = []
    for name, symbol, unit, decimals in MARKET_ASSETS:
        try:
            item = piyasa_verisini_getir(name, symbol, unit, decimals)
            logging.info(
                "PİYASA VERİSİ | %s | Fiyat: %s | Veri zamanı: %s | Alınma: %s",
                name,
                item["price"],
                item["observed_at"].isoformat(),
                item["retrieved_at"].isoformat(),
            )
            data.append(item)
        except Exception as error:
            logging.exception("%s fiyatı alınamadı.", symbol)
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
                "source_url": "https://finance.yahoo.com/quote/" + quote(symbol, safe="") + "/",
                "error": str(error),
            })
    # Hiç fiyat alınamasa bile hata raporu e-postayla gidebilsin.
    return data


# ============================================================
# 4. DOĞRUDAN ALINAN FİYATLARI HTML TABLOSUNA DÖNÜŞTÜR
# ============================================================

def piyasa_html_tablosu(veriler):
    rows = []
    for item in veriler:
        if item["price"] is None:
            price_text = "VERİ ALINAMADI"
            time_text = "Doğrulanamadı"
            range_text = "—"
        else:
            price_text = fiyat_formatla(item["price"], item["decimals"])
            time_text = tarih_formatla(item["observed_at"])
            if item["high_5d"] is not None and item["low_5d"] is not None:
                range_text = (
                    fiyat_formatla(item["low_5d"], item["decimals"])
                    + " – "
                    + fiyat_formatla(item["high_5d"], item["decimals"])
                )
            else:
                range_text = "—"

        source_link = (
            '<a href="' + html.escape(item["source_url"], quote=True) + '">Kaynak</a>'
        )
        rows.append(
            "<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>{html.escape(item['name'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:bold'>{html.escape(price_text)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>{html.escape(item['unit'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>{html.escape(time_text)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>{html.escape(range_text)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>{source_link}</td>"
            "</tr>"
        )

    retrieved_at = tarih_formatla(istanbul_saati())
    return f"""
    <section style="margin:24px 0;font-family:Arial,sans-serif;color:#202124;">
      <h2 style="font-size:21px;margin-bottom:8px">Piyasa Fiyatları — Doğrudan Veri Kaynağı</h2>
      <p style="font-size:12px;color:#666">Veri sorgulama zamanı: {html.escape(retrieved_at)}. Her fiyatın yanında sağlayıcının bildirdiği son veri zamanı yer alır.</p>
      <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead><tr style="background:#eef1f5">
          <th style="padding:9px;text-align:left">Varlık</th>
          <th style="padding:9px;text-align:right">Fiyat</th>
          <th style="padding:9px;text-align:left">Birim</th>
          <th style="padding:9px;text-align:left">Son veri zamanı</th>
          <th style="padding:9px;text-align:left">Son 5 günlük mum aralığı</th>
          <th style="padding:9px;text-align:left">Kaynak</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table></div>
      <p style="font-size:11px;color:#777">Kaynak: Yahoo Finance. Fiyatlar gecikmeli olabilir. Vadeli işlem fiyatları spot fiyatlarla aynı olmak zorunda değildir. Beş günlük yüksek/düşük değerleri kesin destek veya direnç seviyesi değildir.</p>
    </section>
    """


def fiyat_tablosunu_html_e_pkle(html_content, piyasa_verileri):
    table = piyasa_html_tablosu(piyasa_verileri)
    match = re.search(r"<body\b[^>]*>", html_content, flags=re.IGNORECASE)
    if not match:
        html_content = html_sayfa(html_content)
        match = re.search(r"<body\b[^>]*>", html_content, flags=re.IGNORECASE)
    if not match:
        raise RuntimeError("HTML gövdesi bulunamadı; fiyat tablosu eklenemedi.")
    return html_content[:match.end()] + table + html_content[match.end():]


# ============================================================
# 5. GEMINI BAŞARISIZ OLURSA GÖNDERİLECEK YEDEK BÜLTEN
# ============================================================

def yedek_bulten_uret(videolar, hata):
    now_text = istanbul_saati().strftime("%d.%m.%Y %H:%M:%S (TSİ)")
    durum = html.escape(gemini_hata_mesaji(hata))

    if videolar:
        video_rows = []
        for index, video in enumerate(videolar, start=1):
            video_rows.append(
                "<li style='margin:8px 0'>"
                f"<strong>{html.escape(video['channel'])}</strong> — "
                f"<a href='{html.escape(video['url'], quote=True)}'>"
                f"{html.escape(video['title'])}</a> "
                f"<span style='color:#666;font-size:12px'>({html.escape(video['published_at'] or 'Tarih bilinmiyor')})</span>"
                "</li>"
            )
        video_html = "<ol>" + "".join(video_rows) + "</ol>"
    else:
        video_html = "<p>Bu çalıştırmada YouTube video listesi alınamadı.</p>"

    body = f"""
    <main style="max-width:900px;margin:0 auto;padding:16px;font-family:Arial,sans-serif;color:#202124;line-height:1.55">
      <header style="border-bottom:3px solid #263b59;padding-bottom:12px">
        <h1 style="margin-bottom:4px">Günlük Finans ve Piyasa Bülteni</h1>
        <p style="color:#666;margin-top:0">Oluşturulma zamanı: {html.escape(now_text)}</p>
      </header>
      <section style="background:#fff7e6;border-left:4px solid #d97706;padding:14px;margin:18px 0">
        <h2 style="margin-top:0;font-size:18px">Kısmi rapor — otomatik analiz tamamlanamadı</h2>
        <p>{durum}</p>
        <p>Bu e-posta eksikliği gizlememek için kısmi rapor olarak gönderilmiştir. Aşağıdaki fiyatlar doğrudan veri kaynağından alınabilen verilerdir; video ve güncel haber analizleri oluşturulmamıştır.</p>
      </section>
      <h2>Son bulunan YouTube videoları</h2>
      {video_html}
      <p style="font-size:12px;color:#666">Video bağlantılarının listelenmesi videoların analiz edildiği anlamına gelmez.</p>
      <hr style="border:none;border-top:1px solid #ddd;margin:24px 0">
      <p style="font-size:12px;color:#666">Bu içerik yatırım tavsiyesi değildir. Piyasa fiyatları gecikmeli olabilir ve yatırım kararı için tek başına kullanılmamalıdır.</p>
    </main>
    """
    return html_sayfa(body)


# ============================================================
# 6. HTML E-POSTA GÖNDER
# ============================================================

def html_to_plain_text(html_content):
    text = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>", " ", html_content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<br\s*/?>|</p>|</div>|</tr>|</h[1-6]>", "\n", text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def eposta_gonder(html_icerik):
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        raise RuntimeError("GMAIL_USER veya GMAIL_PASS bulunamadı.")

    msg = MIMEMultipart("alternative")
    subject = "Günlük Finans & Piyasa Bülteni — " + istanbul_saati().strftime("%d.%m.%Y")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = TO_EMAIL

    msg.attach(MIMEText(html_to_plain_text(html_icerik), "plain", "utf-8"))
    msg.attach(MIMEText(html_icerik, "html", "utf-8"))

    logging.info("E-posta gönderiliyor: %s", TO_EMAIL)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=40) as server:
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, [TO_EMAIL], msg.as_string())
    logging.info("Bülten başarıyla gönderildi: %s", TO_EMAIL)


# ============================================================
# 7. ANA PROGRAM
# ============================================================

def main():
    logging.info("Günlük bülten başladı: %s", istanbul_saati().isoformat())

    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        raise RuntimeError("GMAIL_USER veya GMAIL_PASS bulunamadı; e-posta gönderilemez.")

    # YouTube erişimi başarısız olsa bile fiyat tablosu ve durum raporu gönderilir.
    try:
        videolar = son_videolari_getir()
    except Exception as error:
        logging.exception("YouTube videoları alınamadı; kısmi bülten hazırlanacak.")
        videolar = []
        youtube_hatasi = error
    else:
        youtube_hatasi = None

    try:
        piyasa_verileri = piyasa_verilerini_getir()
    except Exception:
        # Normalde fonksiyon her varlığın hatasını ayrı kaydeder; bu blok
        # beklenmeyen genel bir hataya karşı son güvenlik katmanıdır.
        logging.exception("Piyasa verileri alınırken beklenmeyen hata oluştu.")
        piyasa_verileri = [
            {
                "name": name, "symbol": symbol, "unit": unit,
                "decimals": decimals, "price": None, "observed_at": None,
                "retrieved_at": istanbul_saati(), "high_5d": None,
                "low_5d": None, "source": "Yahoo Finance",
                "source_url": "https://finance.yahoo.com/quote/" + quote(symbol, safe="") + "/",
            }
            for name, symbol, unit, decimals in MARKET_ASSETS
        ]

    try:
        if youtube_hatasi:
            raise RuntimeError(f"YouTube API erişimi başarısız: {youtube_hatasi}")
        html_bulten = gemini_ile_guncel_bulten_uret(videolar, piyasa_verileri)
    except Exception as error:
        logging.exception("Tam bülten oluşturulamadı; kısmi rapor hazırlanacak.")
        html_bulten = yedek_bulten_uret(videolar, error)

    html_bulten = html_belgesini_duzenle(html_bulten)
    html_bulten = fiyat_tablosunu_html_e_pkle(html_bulten, piyasa_verileri)
    eposta_gonder(html_bulten)

    logging.info("Tüm işlemler tamamlandı: %s", istanbul_saati().isoformat())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("BÜLTEN OLUŞTURULAMADI VEYA GÖNDERİLEMEDİ.")
        raise
