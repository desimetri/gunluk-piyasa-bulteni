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

YOUTUBE_API_BASE = (
    "https://www.googleapis.com/youtube/v3"
)

YAHOO_API_BASE = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
)

CHANNEL_HANDLES = [
    "@EMRAHLAFCI",
    "@barissoydan",
    "@KanalFinans",
]

# Yahoo Finance sembolleri.
# Sağlayıcının verdiği fiyat ve zaman bilgisi ayrıca kontrol edilir.
MARKET_ASSETS = [
    ("Ons Altın", "XAUUSD=X", "USD/ons", 2),
    ("Ons Gümüş", "XAGUSD=X", "USD/ons", 3),
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
        timeout=40,
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


# ============================================================
# 1. YOUTUBE KANALLARININ SON 3 VİDEOSUNU BUL
# ============================================================

def son_videolari_getir():
    if not YOUTUBE_API_KEY:
        raise RuntimeError(
            "YOUTUBE_API_KEY bulunamadı. "
            "GitHub Secrets ayarını kontrol et."
        )

    videolar = []

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

        channel_name = channel["snippet"]["title"]

        uploads_playlist = (
            channel["contentDetails"]
            ["relatedPlaylists"]["uploads"]
        )

        playlist_data = api_get(
            f"{YOUTUBE_API_BASE}/playlistItems",
            {
                "part": "snippet,contentDetails",
                "playlistId": uploads_playlist,
                "maxResults": 10,
                "key": YOUTUBE_API_KEY,
            },
        )

        items = playlist_data.get("items", [])

        channel_videos = []

        for item in items:

            snippet = item.get("snippet", {})
            details = item.get("contentDetails", {})

            video_id = (
                details.get("videoId")
                or snippet.get("resourceId", {}).get("videoId")
            )

            if not video_id:
                continue

            title = snippet.get("title", "")

            # Kullanılamayan veya silinmiş videoları atla.
            if title.lower() in (
                "private video",
                "deleted video",
            ):
                continue

            published_at = (
                details.get("videoPublishedAt")
                or snippet.get("publishedAt")
                or ""
            )

            channel_videos.append({
                "channel": channel_name,
                "handle": handle,
                "title": title,
                "published_at": published_at,
                "url": (
                    "https://www.youtube.com/watch?v="
                    + video_id
                ),
            })

        # YouTube yükleme listesi normalde en yeniden eskiye
        # sıralanır. Yine de tarih bilgisi varsa sıralamayı yap.
        channel_videos.sort(
            key=lambda video: video["published_at"],
            reverse=True,
        )

        channel_videos = channel_videos[:3]

        if len(channel_videos) != 3:
            raise RuntimeError(
                f"{channel_name} kanalından 3 video alınamadı. "
                f"Bulunan video sayısı: {len(channel_videos)}"
            )

        videolar.extend(channel_videos)

        logging.info(
            "%s: %s video bulundu.",
            channel_name,
            len(channel_videos),
        )

    if len(videolar) != 9:
        raise RuntimeError(
            "Toplam 9 video bulunamadı. "
            "Eksik verilerle bülten oluşturulmayacak."
        )

    for index, video in enumerate(videolar, start=1):
        logging.info(
            "VİDEO %s | %s | %s | %s",
            index,
            video["channel"],
            video["title"],
            video["url"],
        )

    return videolar


# ============================================================
# 2. HER YOUTUBE VİDEOSUNU GEMINI İLE AYRI AYRI ANALİZ ET
# ============================================================


def interaction_sonucunu_bekle(
    client,
    interaction,
    timeout_seconds=1800,
):
    start = time.monotonic()

    while str(
        getattr(interaction, "status", "")
    ).lower() == "in_progress":

        if time.monotonic() - start > timeout_seconds:
            raise TimeoutError(
                "Gemini işlemi zaman aşımına uğradı."
            )

        time.sleep(5)

        interaction = client.interactions.get(
            interaction.id
        )

    status = str(
        getattr(interaction, "status", "")
    ).lower()

    if status in ("failed", "cancelled", "incomplete"):
        # API'nin döndürdüğü hata ayrıntılarını bulmaya çalış.
        hata = getattr(interaction, "error", None)
        eksik_bilgi = getattr(
            interaction, "incomplete_details", None
        )

        # SDK nesnesinin tüm yanıtını da inceleme için kaydet.
        try:
            if hasattr(interaction, "model_dump"):
                ayrinti = interaction.model_dump(
                    exclude_none=True
                )
            else:
                ayrinti = repr(interaction)
        except Exception as e:
            ayrinti = f"Yanıt ayrıntıları okunamadı: {e!r}"

        logging.error("Gemini işlem durumu: %s", status)
        logging.error("Gemini hata alanı: %r", hata)
        logging.error(
            "Gemini eksik işlem ayrıntıları: %r",
            eksik_bilgi,
        )
        logging.error(
            "Gemini API yanıt ayrıntıları: %r",
            ayrinti,
        )

        raise RuntimeError(
            f"Gemini işlemi tamamlanamadı. Durum: {status}. "
            f"Hata: {hata!r}. Ayrıntılar GitHub Actions logunda."
        )

    return interaction


def video_analiz_et(client, video, index):

    logging.info(
        "Video analizi başlıyor (%s/9): %s",
        index,
        video["title"],
    )

    prompt = f"""
Aşağıdaki YouTube videosunu doğrudan video girdisi
olarak incele. Sadece video başlığına, açıklamasına veya
konuşmacının genel görüşlerine dayanarak içerik uydurma.

KANAL: {video['channel']}
VİDEO BAŞLIĞI: {video['title']}
YAYIN TARİHİ: {video['published_at']}
VİDEO URL: {video['url']}

Türkçe olarak şu başlıklarda analiz hazırla:

1. Konuşmacının ana piyasa görüşü ve analiz ufku.
2. Altın, gümüş, petrol, BIST, Nasdaq, Bitcoin ve
   döviz hakkındaki somut değerlendirmeleri.
3. Videoda gerçekten belirtilen fiyat seviyeleri,
   destekler, dirençler ve hedefler.
4. Temel gerekçeler, ekonomik gelişmeler ve katalizörler.
5. Konuşmacının yükseliş veya düşüş beklentisinin
   hangi koşullara bağlı olduğu.
6. Görüşün kısa vadeli mi, orta vadeli mi olduğu.

ÖNEMLİ KURALLAR:

- Videoda gerçekten söylenmeyen fiyat seviyelerini üretme.
- Videoda bulunmayan bir görüşü konuşmacıya atfetme.
- Videoya erişemez veya içeriğini analiz edemezsen bunu
  açıkça "VİDEO ANALİZ EDİLEMEDİ" diye belirt.
- Başlıktan hareketle videoyu izlemiş gibi davranma.
- Konuşmacının kendi söylediği rakamlarla senin
  yorumlarını birbirinden ayır.
- Mümkünse önemli ifadelerin zaman damgalarını ekle.
- Yalnızca bu videoyu analiz et.
"""

    interaction = client.interactions.create(
        model=GEMINI_MODEL,
        input=[
            {
                "type": "text",
                "text": prompt,
            },
            {
                "type": "video",
                "uri": video["url"],
            },
        ],
        background=True,
    )

    interaction = interaction_sonucunu_bekle(
        client,
        interaction,
        timeout_seconds=1800,
    )

    analysis = getattr(
        interaction,
        "output_text",
        None,
    )

    if not analysis or not analysis.strip():
        raise RuntimeError(
            f"Gemini boş analiz döndürdü: {video['url']}"
        )

    if "VİDEO ANALİZ EDİLEMEDİ" in analysis.upper():
        raise RuntimeError(
            "Video içeriği analiz edilemedi. "
            "Eksik video analiziyle bülten gönderilmeyecek: "
            + video["url"]
        )

    logging.info(
        "Video analizi tamamlandı (%s/9).",
        index,
    )

    return analysis.strip()


def videolari_analiz_et(videolar):
    client = genai.Client(
        api_key=GEMINI_API_KEY
    )

    analizler = []

    for index, video in enumerate(videolar, start=1):

        analiz = video_analiz_et(
            client,
            video,
            index,
        )

        analizler.append({
            "channel": video["channel"],
            "title": video["title"],
            "published_at": video["published_at"],
            "url": video["url"],
            "analysis": analiz,
        })

    if len(analizler) != 9:
        raise RuntimeError(
            "Dokuz videonun tamamı analiz edilemedi."
        )

    return analizler


# ============================================================
# 3. PİYASA VERİLERİNİ GEMINI'DAN BAĞIMSIZ AL
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

    indicators = result.get("indicators", {})
    quote_data = (
        indicators.get("quote") or [{}]
    )[0]

    closes = quote_data.get("close") or []
    highs = quote_data.get("high") or []
    lows = quote_data.get("low") or []

    valid_bars = []

    for i, timestamp in enumerate(timestamps):

        close = (
            closes[i]
            if i < len(closes)
            else None
        )

        high = (
            highs[i]
            if i < len(highs)
            else None
        )

        low = (
            lows[i]
            if i < len(lows)
            else None
        )

        if (
            timestamp is not None
            and close is not None
            and math.isfinite(float(close))
        ):
            valid_bars.append({
                "timestamp": int(timestamp),
                "close": float(close),
                "high": (
                    float(high)
                    if high is not None
                    and math.isfinite(float(high))
                    else None
                ),
                "low": (
                    float(low)
                    if low is not None
                    and math.isfinite(float(low))
                    else None
                ),
            })

    price = meta.get("regularMarketPrice")
    timestamp = meta.get("regularMarketTime")

    if price is not None:
        price = float(price)

        if not math.isfinite(price):
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

    high_5d = (
        max(recent_highs)
        if recent_highs
        else None
    )

    low_5d = (
        min(recent_lows)
        if recent_lows
        else None
    )

    return {
        "name": name,
        "symbol": symbol,
        "unit": unit,
        "decimals": decimals,
        "price": price,
        "observed_at": observed_at,
        "retrieved_at": istanbul_saati(),
        "high_5d": high_5d,
        "low_5d": low_5d,
        "source": "Yahoo Finance",
        "source_url": (
            "https://finance.yahoo.com/quote/"
            + quote(symbol, safe="")
            + "/"
        ),
    }


def piyasa_verilerini_getir():

    data = []

    for asset in MARKET_ASSETS:

        name, symbol, unit, decimals = asset

        try:

            item = piyasa_verisini_getir(
                name,
                symbol,
                unit,
                decimals,
            )

            logging.info(
                "PİYASA VERİSİ | %s | Fiyat: %s | "
                "Veri zamanı: %s | Alınma zamanı: %s",
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

    available = [
        item
        for item in data
        if item["price"] is not None
    ]

    if not available:
        raise RuntimeError(
            "Hiçbir piyasa fiyatı alınamadı. "
            "Bülten oluşturulmayacak."
        )

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
            f"<td>{html.escape(item['name'])}</td>"
            f"<td style='text-align:right;font-weight:bold'>"
            f"{html.escape(price_text)}</td>"
            f"<td>{html.escape(item['unit'])}</td>"
            f"<td>{html.escape(time_text)}</td>"
            f"<td>{html.escape(range_text)}</td>"
            f"<td>{source_link}</td>"
            "</tr>"
        )

    retrieved_at = tarih_formatla(
        istanbul_saati()
    )

    return f"""
    <section style="
        margin:24px 0;
        font-family:Arial,sans-serif;
        color:#202124;
    ">

      <h2 style="font-size:21px">
        Piyasa Fiyatları — Doğrudan Veri Kaynağı
      </h2>

      <p style="font-size:12px;color:#666">
        Veri sorgulama zamanı: {html.escape(retrieved_at)}.
        Her fiyatın yanında sağlayıcının bildirdiği son
        veri zamanı yer alır. Sorgulama zamanı ile fiyatın
        veri zamanı aynı olmak zorunda değildir.
      </p>

      <div style="overflow-x:auto">
      <table style="
          width:100%;
          border-collapse:collapse;
          font-size:12px;
      ">
        <thead>
          <tr style="background:#eef1f5">
            <th style="padding:9px;text-align:left">Varlık</th>
            <th style="padding:9px;text-align:right">Fiyat</th>
            <th style="padding:9px;text-align:left">Birim</th>
            <th style="padding:9px;text-align:left">
              Son veri zamanı
            </th>
            <th style="padding:9px;text-align:left">
              Son 5 günlük mum aralığı
            </th>
            <th style="padding:9px;text-align:left">Kaynak</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
      </div>

      <p style="font-size:11px;color:#777">
        Kaynak: Yahoo Finance. Fiyatlar gecikmeli olabilir.
        Beş günlük yüksek/düşük değerleri otomatik olarak
        kesin destek veya direnç seviyesi anlamına gelmez.
      </p>

    </section>
    """


# ============================================================
# 5. GEMINI İLE GÜNLÜK FİNANS BÜLTENİ OLUŞTUR
# ============================================================

def gemini_ile_guncel_bulten_uret(
    analizler,
    piyasa_verileri,
):

    client = genai.Client(
        api_key=GEMINI_API_KEY
    )

    now = istanbul_saati()

    # Python tarafından alınmış doğrudan piyasa verileri.
    market_text_lines = []

    for item in piyasa_verileri:

        if item["price"] is None:

            market_text_lines.append(
                f"{item['name']}: VERİ ALINAMADI."
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

        market_text_lines.append(
            f"{item['name']}: "
            f"{fiyat_formatla(item['price'], item['decimals'])} "
            f"{item['unit']}; "
            f"son veri zamanı: "
            f"{tarih_formatla(item['observed_at'])}; "
            f"son 5 günlük mum düşük/yüksek: "
            f"{low}/{high}; "
            f"kaynak: {item['source_url']}"
        )

    market_text = "\n".join(
        market_text_lines
    )

    # Dokuz videonun ayrı ayrı oluşturulmuş analizlerini
    # son sentez için bir araya getir.
    video_sections = []

    for index, item in enumerate(
        analizler,
        start=1,
    ):

        video_sections.append(
            f"""
VİDEO {index}
Kanal: {item['channel']}
Başlık: {item['title']}
Yayın tarihi: {item['published_at']}
URL: {item['url']}

ANALİZ:
{item['analysis']}
"""
        )

    all_video_analyses = "\n\n".join(
        video_sections
    )

    prompt = f"""
Sen deneyimli bir finansal araştırma analisti ve
HTML e-posta tasarımcısısın.

RAPOR TARİHİ:
{now.strftime('%d.%m.%Y %H:%M:%S')} Türkiye saati.

==================================================
A. GÜNCEL PİYASA VERİLERİ
==================================================

Bu fiyatlar Python tarafından doğrudan veri kaynağından
alındı. Fiyat tablosu HTML e-postasına ayrıca eklenecek.

{market_text}

ZORUNLU KURALLAR:

1. Bu fiyat tablosundaki sayısal fiyatlar tek doğrulanmış
   fiyat girdisidir. Başka bir fiyatı "anlık fiyat" olarak
   sunma ve yeni fiyat rakamları uydurma.

2. Veri zamanı eskiyse bunu gizleme. Son kapanış veya
   gecikmeli veri ile anlık fiyatı birbirinden ayır.

3. Kaynağı belirtilmeyen piyasa fiyatını doğrulanmış
   güncel fiyat olarak yazma.

4. Son 5 günlük mumların en düşük ve en yüksek değerleri
   kesin destek/direnç seviyeleri değildir. Bunları yalnızca
   referans aralığı olarak kullan.

==================================================
B. YOUTUBE ANALİZLERİ
==================================================

Aşağıda üç kanalın son üç videosunun ayrı ayrı analizleri var.
Bu analizler dışında kalan görüşleri konuşmacılara atfetme.

{all_video_analyses}

==================================================
C. RAPOR İÇERİĞİ
==================================================

1. STRATEJİ NOTU

En başta iki cümlelik ortak strateji notu yaz.
Kanalların gerçekten ortaklaştığı piyasa temalarını,
konsensüs yönünü ve ana belirsizlikleri belirt.

Görüşler farklıysa yapay bir konsensüs oluşturma.

2. EMTİALAR

Altın, gümüş ve Brent petrol için:
- 🟢 veya 🔴 yön rozeti.
- Kısa vadeli teknik görünüm.
- Videolarda gerçekten belirtilmişse ilgili destek,
  direnç veya hedef seviyesi ve hangi videoda geçtiği.
- Temel gerekçe ve önemli katalizör.

Veri yetersizse kesin yön veya fiyat seviyesi uydurma.
Sağlanan fiyat tablosundaki rakamlarla çelişme.

3. MAJÖR PİYASALAR

BIST 100, Nasdaq Composite, Bitcoin, USD/TRY ve
EUR/TRY için:
- Yön rozeti.
- Teknik görünüm ve ilgili katalizörler.
- Destek veya direnç seviyeleri yalnızca analizlerde
  veya gerçek fiyat verilerinde dayanak varsa belirtilsin.
- Hedefin doğrudan video görüşü mü, yoksa analitik
  çıkarım mı olduğu açıklansın.

Döviz çiftlerindeki yükselişin TL açısından anlamını
doğru yorumla. Örneğin USD/TRY artışı TL'nin dolar
karşısında değer kaybetmesi demektir.

4. GÜNDEM VE MAKRO

Google Search kullanarak rapor tarihine en yakın güvenilir
kaynaklardan TCMB, faiz, enflasyon, fon/SPK ve jeopolitik
gelişmeleri araştır.

En önemli 2-3 konuyu seç. Her gelişmenin tarihi, kaynağı
ve olası piyasa etkisi bulunsun.

Gelecek tarihli veya doğrulanamayan bir haberi olmuş
gibi sunma.

5. UZMAN SENTEZİ

Üç kanalın analizlerini karşılaştır:
- Ortak görüşler.
- Ayrışan görüşler.
- Kısa ve orta vadeli beklenti farklılıkları.
- Görüşlerin hangi koşullarda geçerli olduğu.

Her görüşü mümkün olduğunda ilgili videoya bağla.
Bir konuşmacının söylemediği rakamı veya görüşü ona
atfetme.

6. RİSKLER VE TAKİP LİSTESİ

Gün içinde izlenmesi gereken önemli ekonomik gelişmeleri,
veri açıklamalarını veya piyasa koşullarını belirt.
Doğrulanmamış bir olay saati veya ekonomik veri takvimi
üretme.

7. KAYNAKLAR

Güncel makro haberlerin ve analizde kullanılan
dış kaynakların bağlantılarını HTML içinde göster.

==================================================
D. TASARIM
==================================================

Yalnızca tam HTML dokümanı üret.
Markdown veya ```html kod bloğu kullanma.

- Türkçe dilinde, okunabilir bir HTML e-posta hazırla.
- CSS stillerini mümkün olduğunca inline kullan.
- Mobil uyumlu, sade kartlar ve renkli yön rozetleri kullan.
- Üretim tarihini ve saatini en üstte göster.
- Kaynak bağlantılarını görünür metinlerle sun.
- JavaScript, dış CSS veya uzak görsel kullanma.
- Python'ın ayrıca ekleyeceği doğrudan fiyat tablosunu
  yeniden oluşturma.
- Fiyat tablosunu tekrar etme ve farklı fiyat tablosu üretme.
- Finansal gerçeklerle analitik yorumları birbirinden ayır.
- Son bölümde bunun yatırım tavsiyesi olmadığını belirt.

Yanıtın ilk karakteri HTML dokümanına ait olmalı.
Yanıtın içinde açıklama veya Markdown kod çiti bulunmamalı.
"""

    logging.info(
        "Gemini güncel bülten sentezini hazırlıyor."
    )

    interaction = client.interactions.create(
        model=GEMINI_MODEL,
        input=prompt,
        tools=[
            {
                "type": "google_search"
            }
        ],
        background=True,
    )

    interaction = interaction_sonucunu_bekle(
        client,
        interaction,
        timeout_seconds=1800,
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

    html_code = html_code.strip()

    # Markdown kod çitlerini temizle.
    html_code = re.sub(
        r"^\s*```(?:html)?\s*",
        "",
        html_code,
        flags=re.IGNORECASE,
    )

    html_code = re.sub(
        r"\s*```\s*$",
        "",
        html_code,
    ).strip()

    if "<html" not in html_code.lower():
        html_code = (
            "<!DOCTYPE html>"
            "<html lang='tr'>"
            "<head>"
            "<meta charset='UTF-8'>"
            "<meta name='viewport' "
            "content='width=device-width, initial-scale=1.0'>"
            "<title>Günlük Finans ve Piyasa Bülteni</title>"
            "</head>"
            "<body>"
            + html_code
            + "</body></html>"
        )

    if "<body" not in html_code.lower():
        html_code = re.sub(
            r"</head\s*>",
            "</head><body>",
            html_code,
            count=1,
            flags=re.IGNORECASE,
        )

        if "<body" not in html_code.lower():
            html_code = (
                "<html><body>"
                + html_code
                + "</body></html>"
            )

    if len(html_code) < 500:
        raise RuntimeError(
            "Bülten beklenenden kısa. E-posta gönderilmeyecek."
        )

    # Fiyat tablosunu Python'ın ürettiği güvenilir
    # verilerden oluştur ve gövdenin başına ekle.
    table = piyasa_html_tablosu(
        piyasa_verileri
    )

    html_code, replacements = re.subn(
        r"(<body\b[^>]*>)",
        lambda match: match.group(1) + table,
        html_code,
        count=1,
        flags=re.IGNORECASE,
    )

    if replacements != 1:
        raise RuntimeError(
            "Doğrudan fiyat tablosu HTML'e eklenemedi."
        )

    logging.info(
        "HTML bülten hazır. Karakter sayısı: %s",
        len(html_code),
    )

    return html_code


# ============================================================
# 6. HTML E-POSTA GÖNDER
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

    plain_text = html_to_plain_text(
        html_icerik
    )

    msg.attach(
        MIMEText(
            plain_text,
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
# 7. ANA PROGRAM
# ============================================================

def main():

    logging.info(
        "Günlük bülten başladı: %s",
        istanbul_saati().isoformat(),
    )

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY bulunamadı."
        )

    if not YOUTUBE_API_KEY:
        raise RuntimeError(
            "YOUTUBE_API_KEY bulunamadı."
        )

    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        raise RuntimeError(
            "Gmail kimlik bilgileri eksik."
        )

    # Önce en yeni videoları bul.
    videolar = son_videolari_getir()

    # Her videoyu ayrı ayrı analiz et.
    analizler = videolari_analiz_et(
        videolar
    )

    # Fiyatları analizlerin ardından al ki bülten
    # oluşturulmadan önce mümkün olduğunca yeni olsun.
    piyasa_verileri = piyasa_verilerini_getir()

    # Güncel fiyatlar ve video analizleriyle HTML hazırla.
    html_bulten = gemini_ile_guncel_bulten_uret(
        analizler,
        piyasa_verileri,
    )

    # Hatalı/boş analizde buraya gelinmez.
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
