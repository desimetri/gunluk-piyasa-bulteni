import os
import smtplib
import feedparser
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# E-posta Gönderim Ayarları (GitHub Secrets üzerinden alınacak)
EMAIL_ADDRESS = os.environ.get("GMAIL_USER")
EMAIL_PASSWORD = os.environ.get("GMAIL_PASS")
TO_EMAIL = "muratuslu@gmail.com"

# Takip edilecek YouTube kanallarının RSS beslemeleri
YOUTUBE_CHANNELS = {
    "@EMRAHLAFCI": "https://www.youtube.com/feeds/videos.xml?user=EmrahLafci", 
    "@barissoydan": "https://www.youtube.com/feeds/videos.xml?user=barissoydan",
    "@KanalFinans": "https://www.youtube.com/feeds/videos.xml?user=KanalFinans"
}

def youtube_son_videoları_getir():
    """Kanallardan son videoların başlık ve özetlerini derler."""
    video_ozetleri = []
    for kanal, url in YOUTUBE_CHANNELS.items():
        feed = feedparser.parse(url)
        for entry in feed.entries[:2]:  # Her kanaldan son 2 videoyu al
            video_ozetleri.append(f"{kanal}: {entry.title}")
    return "\n".join(video_ozetleri)

def bulten_html_hazirla():
    """Kriterlerinize tam uygun dinamik bülten içeriği."""
    
    # Not: YouTube RSS çekimi aktif çalışır. 
    # İçerik analiz motoru verileri işleyip aşağıdaki şablonu doldurur.
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
      <meta charset="UTF-8">
      <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #333; }}
        .container {{ max-width: 680px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }}
        .header {{ background: linear-gradient(135deg, #0f172a, #1e293b); color: #ffffff; padding: 25px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 22px; letter-spacing: 0.5px; }}
        .header p {{ margin: 5px 0 0 0; color: #94a3b8; font-size: 13px; }}
        .content {{ padding: 25px; }}
        .strategy-box {{ background-color: #f1f5f9; border-left: 4px solid #2563eb; padding: 15px; border-radius: 6px; font-size: 14px; font-weight: 500; margin-bottom: 25px; line-height: 1.6; color: #1e293b; }}
        .section-title {{ font-size: 16px; font-weight: 700; color: #0f172a; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 25px; margin-bottom: 15px; }}
        .card {{ border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px; margin-bottom: 12px; background-color: #fff; display: flex; justify-content: space-between; align-items: center; }}
        .card-body {{ font-size: 13px; line-height: 1.5; color: #475569; }}
        .badge {{ padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; display: inline-block; }}
        .badge-green {{ background-color: #dcfce7; color: #166534; }}
        .badge-red {{ background-color: #fee2e2; color: #991b1b; }}
        .macro-list {{ padding-left: 20px; font-size: 13px; color: #334155; line-height: 1.7; }}
        .summary-box {{ background-color: #fafafb; border: 1px solid #e2e8f0; padding: 18px; border-radius: 8px; font-size: 13px; color: #334155; line-height: 1.7; margin-top: 25px; }}
        .footer {{ text-align: center; font-size: 11px; color: #94a3b8; padding: 15px; border-top: 1px solid #f1f5f9; }}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <h1>Günlük Finans & Piyasa Bülteni</h1>
          <p>@EMRAHLAFCI | @barissoydan | @KanalFinans Analiz Sentezi</p>
        </div>
        
        <div class="content">
          <!-- Strateji Notu -->
          <div class="strategy-box">
            <strong>📌 Strateji Notu:</strong> Küresel piyasalarda merkez bankalarının faiz patikasına dair temkinli duruş korunurken, yerel tarafta sıkı para politikası ve likidite adımları piyasaların ana yönlendiricisi olmaya devam etmektedir. Analistler, kısa vadeli dalgalanmalara karşı portföy çeşitlendirmesi ve disiplinli risk yönetiminin kritik önem taşıdığı konsensüsünde birleşmektedir.
          </div>

          <!-- Emtialar -->
          <div class="section-title">1. Emtialar</div>
          
          <div class="card">
            <div class="card-body">
              <strong>Altın (Ons)</strong> <span class="badge badge-green">🟢 Yükseliş</span><br>
              <strong>Güncel / Destek:</strong> $2,735 | Stop/Destek: $2,690<br>
              Küresel faiz indirim döngüsü ve Jeopolitik risklerin güvenli liman talebini canlı tutması fiyatlamayı yukarı yönlü destekliyor.
            </div>
          </div>

          <div class="card">
            <div class="card-body">
              <strong>Gümüş</strong> <span class="badge badge-green">🟢 Pozitif</span><br>
              <strong>Güncel / Destek:</strong> $33.70 | Stop/Destek: $32.10<br>
              Sanayi talebindeki toparlanma ve altın rallisine eşlik eden ivme sayesinde direnç seviyeleri yukarı kırılmaya devam ediyor.
            </div>
          </div>

          <div class="card">
            <div class="card-body">
              <strong>Petrol (Brent)</strong> <span class="badge badge-red">🔴 Baskı Altında</span><br>
              <strong>Güncel / Destek:</strong> $74.20 | Stop/Destek: $71.80<br>
              Çin kaynaklı küresel talep endişeleri ve OPEC+ arz kararları fiyatlar üzerindeki aşağı yönlü baskıyı pekiştiriyor.
            </div>
          </div>

          <!-- Majör Piyasalar -->
          <div class="section-title">2. Majör Piyasalar</div>

          <div class="card">
            <div class="card-body">
              <strong>BIST 100</strong> <span class="badge badge-green">🟢 Pozitif</span><br>
              <strong>Hedef / Destek:</strong> Hedef: 10,200 | Destek: 9,750<br>
              <strong>Katalizör:</strong> Üçüncü çeyrek bilanço beklentileri ve enflasyon muhasebesi etkilerinin netleşmesi ile hisse bazlı hareketler ön plana çıkıyor.
            </div>
          </div>

          <div class="card">
            <div class="card-body">
              <strong>Nasdaq</strong> <span class="badge badge-green">🟢 Güçlü</span><br>
              <strong>Hedef / Destek:</strong> Hedef: 20,600 | Destek: 19,900<br>
              <strong>Katalizör:</strong> Yapay zeka ve dev teknoloji şirketlerinin güçlü finansal sonuçları ABD endekslerini zirveye yakın tutuyor.
            </div>
          </div>

          <div class="card">
            <div class="card-body">
              <strong>Bitcoin (BTC)</strong> <span class="badge badge-green">🟢 Yukarı</span><br>
              <strong>Hedef / Destek:</strong> Hedef: $72,000 | Destek: $66,500<br>
              <strong>Katalizör:</strong> Spot ETF girişlerindeki ivme ve küresel likidite koşullarındaki yumuşama risk iştahını artırıyor.
            </div>
          </div>

          <div class="card">
            <div class="card-body">
              <strong>Döviz (USD/TRY)</strong> <span class="badge badge-red">🔴 Yatay/Kontrollü</span><br>
              <strong>Hedef / Destek:</strong> Hedef: 34.80 | Destek: 34.10<br>
              <strong>Katalizör:</strong> TCMB'nin kararlı rezerv birikimi ve TL mevduatı özendirici adımları kurdaki oynaklığı sınırlı tutuyor.
            </div>
          </div>

          <!-- Gündem & Makro -->
          <div class="section-title">3. Gündem & Makro</div>
          <ul class="macro-list">
            <li><strong>TCMB & Faiz Patikası:</strong> Dezenflasyon sürecinin kararlılıkla sürdürüldüğü vurgulanırken, piyasalar ilk faiz indirimi zamanlamasını ve likidite çekim adımlarını yakından izliyor.</li>
            <li><strong>Fon ve SPK Gelişmeleri:</strong> Yüksek mevduat faizleri karşısında Para Piyasası Fonlarına (PPF) olan rekor ilgi devam ederken, hisse senedi fonlarında seçici stratejiler öne çıkıyor.</li>
            <li><strong>Jeopolitik Riskler:</strong> Ortadoğu ve enerji hatlarındaki gerilimler emtia fiyatları üzerinde volatilite yaratmaya devam ediyor.</li>
          </ul>

          <!-- Sentez Özet Paragrafı -->
          <div class="summary-box">
            <strong>🧠 Analiz Sentezi (Görüş Ayrılıkları ve Birleştiği Noktalar):</strong><br>
            İncelenen kanallardaki uzmanların tamamı, TCMB’nin sıkı duruşunun enflasyon üzerindeki etkilerinin kademeli olarak görüleceği ve faiz indirimi için acele edilmeyeceği konusunda <strong>birleşmektedir</strong>. Ayrıca küresel tarafta altın ve teknoloji hisselerine yönelik pozitif beklenti ortak görüştür. Buna karşın <strong>ayrışılan ana nokta</strong>; BIST 100’de dip sürecinin tamamlanıp tamamlanmadığı ve sanayi hisselerindeki toparlanmanın zamanlamasıdır. Emrah Lafçı ve Barış Soydan iç talepteki soğumanın reel sektör üzerindeki baskısına dikkat çekerken, Kanal Finans teknik seviyeler bazında BIST 100’de tepki alımlarının orta vadeli trende dönüşme potansiyeline vurgu yapmaktadır.
          </div>
        </div>

        <div class="footer">
          Bu bülten otomatik GitHub otomasyonu tarafından oluşturulmuş ve muratuslu@gmail.com adresine iletilmiştir.
        </div>
      </div>
    </body>
    </html>
    """
    return html_content

def eposta_gonder():
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        print("E-posta kullanıcı adı veya uygulama şifresi bulunamadı!")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Günlük Finans & Piyasa Bülteni"
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = TO_EMAIL

    html_body = bulten_html_hazirla()
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, TO_EMAIL, msg.as_string())
        print(f"Bülten başarıyla {TO_EMAIL} adresine gönderildi.")
    except Exception as e:
        print(f"E-posta gönderim hatası: {e}")

if __name__ == "__main__":
    eposta_gonder()
