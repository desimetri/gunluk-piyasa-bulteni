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
    "@EMRAHLAFCI": "https://www.youtube.com/feeds/videos.xml?user=EmrahLafci",
    "@barissoydan": "https://www.youtube.com/feeds/videos.xml?user=barissoydan",
    "@KanalFinans": "https://www.youtube.com/feeds/videos.xml?user=KanalFinans"
}

def youtube_son_videolari_cek():
    """Son 3 günde yayınlanan videoların başlık ve açıklamalarını toplar."""
    icerikler = []
    for kanal, feed_url in YOUTUBE_CHANNELS.items():
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:3]: # Her kanaldan son 3 video
            icerikler.append(f"Kanal: {kanal}\nBaşlık: {entry.title}\nÖzet: {entry.summary}\n---")
    return "\n".join(icerikler)

def gemini_ile_bulten_olustur(video_verileri):
    """Gemini API kullanarak canlı ve güncel içerikle HTML bülten üretir."""
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')

    prompt = f"""
    Sen uzman bir finans analistisin. Aşağıda belirtilen YouTube kanallarının (@EMRAHLAFCI, @barissoydan, @KanalFinans) son yayınlarından elde edilen veriler bulunmaktadır:

    {video_verileri}

    Bu verilere ve piyasanın GÜNCEL durumuna dayanarak e-posta formatında taranabilir modern bir HTML bülteni hazırla.
    
    Aşağıdaki kurallara KESİNLİKLE uy:
    1. En başta 2 cümlelik ortak Strateji Notu (konsensüs yönü ve ana tema).
    2. Emtialar (Altın, Gümüş, Petrol): Yön rozeti (🔴/🟢), güncel seviye, kritik stop/destek ve 1-2 cümlelik net gerekçe.
    3. Majör Piyasalar (BIST 100, Nasdaq, Bitcoin, Döviz): Yön rozeti (🔴/🟢), teknik hedef/destek ve hareketin arka planındaki temel katalizör.
    4. Gündem & Makro (TCMB, Fon/SPK gelişmeleri, jeopolitik): 2-3 maddelik odaklı mikro-analiz.
    5. Bültenin sonuna kanallardaki görüşlerin ayrıştığı ve birleştiği noktaları vurgulayan geniş özet sentez paragrafı ekle.
    
    Sadece temiz, doğrudan e-postada görüntülenebilir inline CSS içeren HTML kodu üret. ```html bloğu başlatma, doğrudan raw HTML döndür.
    """

    response = model.generate_content(prompt)
    return response.text

def eposta_gonder(html_icerik):
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        print("E-posta yetkilendirme bilgileri eksik!")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Günlük Finans & Piyasa Bülteni"
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = TO_EMAIL

    msg.attach(MIMEText(html_icerik, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, TO_EMAIL, msg.as_string())
        print(f"Güncel bülten başarıyla {TO_EMAIL} adresine gönderildi.")
    except Exception as e:
        print(f"E-posta gönderim hatası: {e}")

if __name__ == "__main__":
    if GEMINI_API_KEY:
        video_verileri = youtube_son_videolari_cek()
        html_bulten = gemini_ile_bulten_olustur(video_verileri)
        eposta_gonder(html_bulten)
    else:
        print("GEMINI_API_KEY bulunamadı!")
