```python
import os
import re
import html
import logging
import smtplib
import requests

from datetime import datetime
from zoneinfo import ZoneInfo
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

# Google AI Studio hesabında erişime açık bir model kullan.
GEMINI_MODEL = "gemini-3.8-flash"

CHANNEL_HANDLES = [
    "@EMRAHLAFCI",
    "@barissoydan",
    "@KanalFinans",
]

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================

def istanbul_saati():
    return datetime.now(TIMEZONE)


def api_get(url, params):
    """HTTP hatalarını ve başarısız API cevaplarını yakalar."""
    response = requests.get(
        url,
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


# ============================================================
# 1. YOUTUBE KANALLARININ SON 3 VİDEOSUNU BUL
# ============================================================

def son_videolari_getir():
    if not YOUTUBE_API_KEY:
        raise RuntimeError(
            "YOUTUBE_API_KEY eksik. GitHub Secrets ayarını kontrol et."
        )

    videolar = []

    for handle in CHANNEL_HANDLES:
        logging.info("%s kanalı kontrol ediliyor.", handle)

        # Kanalı handle üzerinden bul.
        kanal_verisi = api_get(
            f"{YOUTUBE_API_BASE}/channels",
            {
                "part": "id,snippet,contentDetails",
                "forHandle": handle,
                "key": YOUTUBE_API_KEY,
            },
        )

        kanallar = kanal_verisi.get("items", [])

        if not kanallar:
            raise RuntimeError(
                f"YouTube kanalı bulunamadı: {handle}"
            )

        kanal = kanallar[0]
        kanal_adi = kanal["snippet"]["title"]
        uploads_id = kanal["contentDetails"][
            "relatedPlaylists"
        ]["uploads"]

        # Yükleme listesinden en son 3 videoyu al.
        liste_verisi = api_get(
            f"{YOUTUBE_API_BASE}/playlistItems",
            {
                "part": "snippet,contentDetails",
                "playlistId": uploads_id,
                "maxResults": 3,
                "key": YOUTUBE_API_KEY,
            },
        )

        items = liste_verisi.get("items", [])

        if not items:
            raise RuntimeError(
                f"{kanal_adi} kanalında video bulunamadı."
            )

        for item in items:
            video_id = item["contentDetails"].get("videoId")

            if not video_id:
                continue

            video_basligi = item["snippet"]["title"]
            yayin_tarihi = item["contentDetails"].get(
                "videoPublishedAt"
            ) or item["snippet"].get("publishedAt", "")

            videolar.append({
                "channel": kanal_adi,
                "handle": handle,
                "title": video_basligi,
                "published_at": yayin_tarihi,
                "url": f"https://www.youtube.com/watch?v={video_id}",
            })

        logging.info(
            "%s kanalından %s video alındı.",
            kanal_adi,
            min(len(items), 3),
        )

    if len(videolar) != 9:
        raise RuntimeError(
            f"9 video bekleniyordu, {len(videolar)} video bulundu. "
            "Eksik videolarla bülten gönderilmeyecek."
        )

    return videolar


# ============================================================
# 2. GEMINI İLE VİDEO ANALİZİ VE GÜNCEL PİYASA BÜLTENİ
# ============================================================

def gemini_ile_guncel_bulten_uret(videolar):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY eksik.")

    client = genai.Client(api_key=GEMINI_API_KEY)

    simdi = istanbul_saati()
    tarih = simdi.strftime("%d.%m.%Y")
    saat = simdi.strftime("%H:%M")

    video_listesi = "\n".join(
        f"- Kanal: {v['channel']}\n"
        f"  Başlık: {v['title']}\n"
        f"  Yayın tarihi: {v['published_at']}\n"
        f"  URL: {v['url']}"
        for v in videolar
    )

    prompt = f"""
Sen deneyimli bir finansal araştırma analisti ve HTML
e-posta tasarımcısısın.

RAPOR TARİHİ:
{tarih}, Türkiye saati {saat}.

GÖREV 1 — YOUTUBE VİDEO ANALİZİ

Aşağıdaki dokuz herkese açık YouTube videosunu video girdisi
olarak incele. Sadece başlıklarına, açıklamalarına veya kanal
isimlerine bakarak analiz yaptığını varsayma.

{video_listesi}

Her videoda mümkün olduğu ölçüde:
- Konuşmacının belirttiği piyasa görüşünü,
- Altın, gümüş, petrol, BIST, Nasdaq, Bitcoin ve döviz
  hakkındaki değerlendirmelerini,
- Söylenen somut fiyat seviyelerini, destekleri, dirençleri
  ve hedefleri,
- Görüşün hangi koşullarda geçerli olduğunu,
- Analizin dayandığı temel veya teknik gerekçeleri çıkar.

Videolara erişemezsen bunu açıkça belirt. Videoda söylenmeyen
görüşleri konuşmacıya atfetme. Eksik videoyu analiz edilmiş
gibi gösterme.

GÖREV 2 — GÜNCEL FİNANSAL VERİLER

Google Search aracını kullanarak rapor hazırlanırken
erişilebilen en güncel piyasa verilerini araştır.

Şu varlıkları ve göstergeleri incele:
- Gram altın ve ons altın
- Ons gümüş
- Brent petrol
- BIST 100
- Nasdaq Composite veya Nasdaq-100; kullandığın endeksi belirt
- Bitcoin
- USD/TRY ve EUR/TRY
- Gerektiğinde ABD tahvil faizleri, DXY ve önemli makro veriler

Her fiyat için:
- Kaynağı ve mümkünse doğrudan kaynak URL'sini,
- Fiyatın ait olduğu tarih ve saati,
- İşlem anlık mı, gecikmeli mi, yoksa son kapanış mı olduğunu
  belirt.

Türkiye saati {saat} itibarıyla rapor hazırlandığını dikkate al.
Piyasa kapalıysa son kapanışı güncel anlık fiyat gibi sunma.
Arama sonuçlarında yalnızca eski bir değer bulunuyorsa bunu
güncel fiyat olarak kullanma. Doğrulanamayan fiyatı açıkça
"doğrulanamadı" olarak işaretle; rakam uydurma.

GÖREV 3 — YORUM VE KRİTERLER

1. STRATEJİ NOTU:
En başta iki cümlelik ortak strateji notu yaz.
Genel piyasa yönünü, ortak temaları ve önemli belirsizlikleri
belirt. Uzmanlar aynı fikirde değilse yapay bir konsensüs üretme.

2. EMTİALAR:
Altın, gümüş ve Brent petrol için:
- Yön rozeti: 🟢 veya 🔴
- Doğrulanmış fiyat ve veri zamanı
- Kritik destek, direnç veya stop seviyesi
- 1-2 cümlelik teknik ve temel gerekçe

Yön rozetini yakın vadeli teknik görünümüne göre belirle.
Yukarı yönlü görünüm için yeşil, aşağı yönlü görünüm için
kırmızı kullan. Yeterli veri yoksa nötr/belirsiz olduğunu yaz;
sırf formatı tamamlamak için kesin yön uydurma.

3. MAJÖR PİYASALAR:
BIST 100, Nasdaq, Bitcoin, USD/TRY ve EUR/TRY için:
- Yön rozeti
- Önemli destek, direnç veya teknik hedef
- Hareketin arkasındaki temel katalizör
- Veri tarihi ve kaynağı

Teknik seviyeleri kaynakta veya analiz edilen verilerde
desteklenmedikçe kesin seviyeler gibi sunma.

4. GÜNDEM VE MAKRO:
TCMB, enflasyon, faiz, fon/SPK gelişmeleri ve jeopolitik
risklerden en önemli 2-3 konuyu seç. Her biri için olası
piyasa etkisini açıkla.

5. UZMAN SENTEZİ:
Üç kanalın incelenen videolarında gerçekten görülen
görüşlerini karşılaştır.
Ortaklaşılan konuları, ayrışan noktaları, farklı zaman
ufuklarını ve çelişkileri ayrı ayrı vurgula.
Her görüşü mümkün olduğunda ilgili videoya bağlantıyla
ilişkilendir. Videoda bulunmayan bir yorumu uzmana atfetme.

6. KAYNAKÇA:
Piyasa verilerinin ve makro haberlerin kaynak bağlantılarını
raporun sonuna ekle. Kaynaklar arasındaki tarih farklılıklarını
gizleme.

ÖNEMLİ:
Bu rapor yatırım tavsiyesi değildir. Veriler ve yorumlar
birbirinden ayrılmalıdır. Kesinlik bulunmayan konularda
belirsizliği açıkça belirt.

HTML TASARIMI:
- Yalnızca tam bir HTML dokümanı üret.
- Markdown veya ```html kod bloğu kullanma.
- Türkçe karakterleri doğru kullan.
- Mobilde okunabilen, sade ve modern e-posta düzeni oluştur.
- CSS stillerini mümkün olduğunca inline kullan.
- Renkli yön rozetleri, fiyat kartları, başlıklar ve kaynak
  bağlantıları ekle.
- Dış JavaScript, harici CSS ve uzak görseller kullanma.
- E-posta istemcilerinde çalışmayabilecek karmaşık tasarımlardan
  kaçın.
- Raporun en üstünde üretim tarihini ve saatini göster.
- Videoların başlıklarını, yayın tarihlerini ve bağlantılarını
  raporda ayrıca listele.
"""

    # Her video ayrı bir video girdisi olarak gönderiliyor.
    # Dokuz video + metin girdisi tek istekte kullanılır.
    model_input = [
        {
            "type": "text",
            "text": prompt,
        }
    ]

    for video in videolar:
        model_input.append({
            "type": "video",
            "uri": video["url"],
        })

    logging.info("Gemini'a %s video gönderiliyor.", len(videolar))

    response = client.interactions.create(
        model=GEMINI_MODEL,
        input=model_input,
        tools=[{"type": "google_search"}],
    )

    html_kod = response.output_text

    if not html_kod or not html_kod.strip():
        raise RuntimeError("Gemini boş bir yanıt döndürdü.")

    html_kod = html_kod.strip()

    # Modelin yanlışlıkla eklediği Markdown kod bloğunu temizle.
    html_kod = re.sub(
        r"^\s*```(?:html)?\s*",
        "",
        html_kod,
        flags=re.IGNORECASE,
    )
    html_kod = re.sub(r"\s*```\s*$", "", html_kod).strip()

    if "<html" not in html_kod.lower() or "<body" not in html_kod.lower():
        raise RuntimeError(
            "Gemini yanıtı geçerli bir HTML e-posta dokümanı değil."
        )

    if len(html_kod) < 1000:
        raise RuntimeError(
            "Gemini yanıtı beklenenden kısa; eksik bülten gönderilmeyecek."
        )

    logging.info("HTML bülten üretildi: %s karakter.", len(html_kod))

    return html_kod


# ============================================================
# 3. E-POSTA GÖNDER
# ============================================================

def eposta_gonder(html_icerik):
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        raise RuntimeError(
            "GMAIL_USER veya GMAIL_PASS eksik."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"Günlük Finans & Piyasa Bülteni — "
        f"{istanbul_saati().strftime('%d.%m.%Y')}"
    )
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = TO_EMAIL

    # HTML görüntülenemeyen e-posta istemcileri için metin alternatifi.
    metin = (
        "Günlük Finans & Piyasa Bülteni HTML formatında hazırlanmıştır. "
        "Lütfen HTML görüntülemeyi destekleyen bir e-posta istemcisi kullanın."
    )

    msg.attach(MIMEText(metin, "plain", "utf-8"))
    msg.attach(MIMEText(html_icerik, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(
            EMAIL_ADDRESS,
            [TO_EMAIL],
            msg.as_string(),
        )

    logging.info("Bülten %s adresine gönderildi.", TO_EMAIL)


# ============================================================
# ANA PROGRAM
# ============================================================

def main():
    logging.info(
        "Günlük bülten işlemi başladı: %s",
        istanbul_saati().isoformat(),
    )

    # Her aşama hata verirse işlem durur; hatalı/şablon bülten
    # başarıyla gönderilmiş gibi gösterilmez.
    videolar = son_videolari_getir()
    html_bulten = gemini_ile_guncel_bulten_uret(videolar)
    eposta_gonder(html_bulten)

    logging.info("Bütün işlemler başarıyla tamamlandı.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception(
            "BÜLTEN OLUŞTURULAMADI VEYA GÖNDERİLEMEDİ."
        )
        raise
```
