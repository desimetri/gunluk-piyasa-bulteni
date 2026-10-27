import os
import smtplib
import google.generativeai as genai
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Ortam Değişkenleri (GitHub Secrets üzerinden alınır)
EMAIL_ADDRESS = os.environ.get("GMAIL_USER")
EMAIL_PASSWORD = os.environ.get("GMAIL_PASS")
GEMINI_API_KEY = os.environ.get("GMAIL_PASS") # Yedek kontrol
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TO_EMAIL = "muratuslu@gmail.com"

def gemini_ile_guncel_bulten_uret():
    """Gemini'nin kendi güncel bilgi tabanını ve finansal analiz yeteneğini kullanarak bülten üretir."""
    genai.configure(api_key=GEMINI_API_KEY)
    
    # En kararlı çalışan Gemini modelini kullanıyoruz
    model = genai.GenerativeModel('gemini-1.5-flash')

    prompt = """
    Sen profesyonel bir finans analistisin. @EMRAHLAFCI, @barissoydan ve @KanalFinans YouTube kanallarının son dönemdeki analiz yaklaşımlarını, piyasa trendlerini ve güncel finansal verileri baz alarak kapsamlı bir Günlük Finans & Piyasa Bülteni hazirla.

    Aşağıdaki kurallara ve formata KESİNLİKLE uy:
    1. **Strateji Notu:** En başta 2 cümlelik ortak Strateji Notu (konsensüs yönü ve ana tema) yaz.
    2. **Emtialar (Altın, Gümüş, Petrol):** Her biri için yön rozeti (🔴 veya 🟢), güncel seviye, kritik stop/destek ve 1-2 cümlelik net gerekçe belirt.
    3. **Majör Piyasalar (BIST 100, Nasdaq, Bitcoin, Döviz):** Her biri için yön rozeti (🔴 veya 🟢), teknik hedef/destek ve hareketin arka planındaki temel katalizörü yaz.
    4. **Gündem & Makro (TCMB, Fon/SPK gelişmeleri, jeopolitik):** 2-3 maddelik odaklı ve doyurucu mikro-analiz ekle.
    5. **Uzman Sentezi:** Bültenin sonuna kanallardaki görüşlerin ayrıştığı ve birleştiği noktaları vurgulayan geniş bir özet sentez paragrafı ekle.
    
    Tasarım Kuralları:
    - Renkli badge'lere (etiketlere), temiz kenarlıklı kartlara ve dengeli metin açıklamalarına sahip modern, taranabilir bir HTML e-posta şablonu oluştur.
    - Sadece ham (raw) HTML kodu döndür, ```html gibi markdown blokları kullanma.
    """

    response = model.generate_content(prompt)
    html_kod = response.text
    
    # Eğer model yanlışlıkla markdown blok eklediyse temizleyelim
    html_kod = html_kod.replace("```html", "").replace("```", "").strip()
    return html_kod

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
        html_bulten = gemini_ile_guncel_bulten_uret()
        eposta_gonder(html_bulten)
    else:
        print("GEMINI_API_KEY bulunamadı!")
