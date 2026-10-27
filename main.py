import os
import smtplib
import feedparser
import google.generativeai as genai
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Ortam Değişkenleri
EMAIL_ADDRESS = os.environ.get("GMAIL_USER")
EMAIL_PASSWORD = os.environ.get("GMAIL_PASS")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TO_EMAIL = "muratuslu@gmail.com"

# Takip Edilecek YouTube Kanalları
YOUTUBE_CHANNELS = {
    "@EMRAHLAFCI": "https://www.youtube.com/feeds/videos.xml?channel_id=UC6Y0q12c3f8v2L1k9Z2x5XQ", # RSS beslemesi
    "@barissoydan": "https://www.youtube.com/feeds/videos.xml?user=barissoydan",
    "@KanalFinans": "https://www.youtube.com/feeds/videos.xml?user=KanalFinans"
}

# Alternatif güvenli RSS adresleri
RSS_URLS = [
    "https://www.youtube.com/feeds/videos.xml?user=EmrahLafci",
    "https://www.youtube.com/feeds/videos.xml?user=barissoydan",
    "https://www.youtube.com/feeds/videos.xml?user=KanalFinans"
]

def youtube_videolarini_topla():
    """Kanallardan son videoların başlıklarını toplar."""
    metinler = []
    # Alternatif feedparser yöntemleri
    channels = {
        "Emrah Lafçı": "https://www.youtube.com/feeds/videos.xml?user=EmrahLafci",
        "Barış Soydan": "https://www.youtube.com/feeds/videos.xml?user=barissoydan",
        "Kanal Finans": "https://www.youtube.com/feeds/videos.xml?user=KanalFinans"
    }
    
    for isim, url in channels.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:2]:
                metinler.append(f"- {isim}: {entry.title}")
        except Exception as e:
            print(f"{isim} çekilirken hata: {e}")
            
    return "\n".join(metinler) if metinler else "Güncel video başlığı alınamadı, genel piyasa verilerini baz al."

def bulten_uret_ve_gonder():
    if not GEMINI_API_KEY or not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        print("Eksik kimlik bilgileri!")
        return

    # Video verilerini topla
    video_ozetleri = youtube_videolarini_topla()

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')

    prompt = f"""
    Sen uzman bir finans analistisin. Aşağıda @EMRAHLAFCI, @barissoydan ve @KanalFinans kanallarının en son yayınladığı video başlıkları yer almaktadır:
    {video_ozetleri}

    Bugünün tarihini (27 Ekim 2026) ve küresel piyasalardaki güncel durumu baz alarak eksiksiz bir "Günlük Finans & Piyasa Bülteni" HTML e-postası üret.

    Kriterler KESİNLİKLE uygulanmalıdır:
    1. En başta 2 cümlelik ortak Strateji Notu (konsensüs yönü ve ana tema).
    2. Emtialar (Altın, Gümüş, Petrol): Yön rozeti (🔴/🟢), güncel fiyat/seviye, kritik stop/destek ve 1-2 cümlelik net gerekçe.
    3. Majör Piyasalar (BIST 100, Nasdaq, Bitcoin, Döviz): Yön rozeti (🔴/🟢), teknik hedef/destek ve temel katalizör.
    4. Gündem & Makro (TCMB, Fon/SPK gelişmeleri, jeopolitik): 2-3 maddelik odaklı mikro-analiz.
    5. Bültenin sonuna kanallardaki görüşlerin ayrıştığı ve birleştiği noktaları vurgulayan geniş bir özet sentez paragrafı ekle.
    
    Tasarım:
    - Renkli badge'lere, temiz kenarlıklı kartlara ve dengeli metin açıklamalarına sahip modern, taranabilir bir HTML e-posta şablonu oluştur.
    - Sadece ham (raw) HTML kodu döndür, ```html gibi markdown blokları kesinlikle kullanma.
    """

    try:
        response = model.generate_content(prompt)
        html_icerik = response.text.replace("```html", "").replace("```", "").strip()
    except Exception as e:
        print(f"Gemini analiz hatası: {e}")
        return

    # E-posta Gönderimi
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Günlük Finans & Piyasa Bülteni"
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = TO_EMAIL

    msg.attach(MIMEText(html_icerik, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, TO_EMAIL, msg.as_string())
        print("Bülten başarıyla gönderildi!")
    except Exception as e:
        print(f"E-posta gönderim hatası: {e}")

if __name__ == "__main__":
    bulten_uret_ve_gonder()
