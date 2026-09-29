# Dolandırıcılık Risk Tarama Raporu — demo_tablo.xlsx

Bu rapor bir **kaçınma** raporudur: hangi kaydın hangi dolandırıcılık desenini taşıdığını gösterir. Bir hedef listesi değildir; sıralama yalnızca **risk** eksenindedir. Düşük puan 'güvenli' demek değildir — yalnızca metinde bilinen bir risk desenine rastlanmadığı anlamına gelir.

Taranan kayıt: **6** · DOKUNMA: 2 · TEMKİNLİ OL: 2 · DOĞRULA: 0 · DÜŞÜK RİSK: 2

## Özet

| # | Kayıt | Risk Skoru | Etiket | Başlıca bulgu |
|---|-------|-----------:|--------|----------------|
| 1 | PaketKazan | 90/100 | DOKUNMA | Ön ödeme / aktivasyon / paket-VIP kapısı |
| 2 | VipKulup | 90/100 | DOKUNMA | Ön ödeme / aktivasyon / paket-VIP kapısı |
| 6 | SessizBot | 35/100 | TEMKİNLİ OL | Yalnızca kripto ile geri döndürülemez ödeme kanalı |
| 3 | TakimKur | 35/100 | TEMKİNLİ OL | Referans piramidi / ponzi şeması |
| 4 | AnketOdak | 0/100 | DÜŞÜK RİSK | — |
| 5 | PTCİzle | 0/100 | DÜŞÜK RİSK | — |

## Gerekçeli bulgular

### 1. PaketKazan — **DOKUNMA** (90/100)
Bağlantı: `t.me/ornek-paket`
- **Ön ödeme / aktivasyon / paket-VIP kapısı** (+40 puan) — `note` alanında "Önce yatırım" ifadesi
  - **Neden riskli:** Kazanca erişmek için önce sizden para isteniyor (aktivasyon ücreti, paket, VIP, bakiye yükleme). Gerçek görev/anket platformları sizden ücret almaz — ödemeyi onlar size yapar. Bu, 'ön ödeme tuzağı'nın (advance-fee fraud) anahtarıdır.
  - **Ne yapmalı:** DOKUNMA. Ödenen para geri alınamaz; sonrasında görünen 'kazanç' yalnızca ekran rakamıdır ve çekim aşamasında yeni şartlarla engellenir.
- **Gerçek dışı / garantili getiri vaadi** (+25 puan) — `mechanism` alanında "günde %" ifadesi
  - **Neden riskli:** Yüksek ve 'garantili' getiri piyasada yoktur; vaadin büyüklüğü tek başına dolandırıcılık göstergesidir. Gerçek platformlarda kazanç değişkendir ve emek/veri karşılığıdır.
  - **Ne yapmalı:** Bağımsız, doğrulanabilir ödeme kanıtı olmadan bu tür vaatleri ciddiye almayın.
- **Hareketli çekim eşiği / davet şartı baskısı** (+15 puan) — `note` alanında "şart" ifadesi
  - **Neden riskli:** Çekim, davet/aktivite/şart tamamlamaya bağlanmış. Bu kurgunun amacı eşiğe hiç ulaştırmamaktır: eşiğe yaklaştıkça yeni bir şart eklenir ('hareketli çekim eşiği').
  - **Ne yapmalı:** Şartları yazılı olarak isteyin ve sabit mi diye bakın. Değişiyorsa veya davet gerektiriyorsa çıkın.
- **Yalnızca kripto ile geri döndürülemez ödeme kanalı** (+10 puan) — `payout` alanında "USDT" ifadesi
  - **Neden riskli:** Ödeme yalnızca kripto ise işlem geri alınamaz ve muhatap genellikle tüzel kişilik değildir; şikâyet edilecek bir merci bulunmaz.
  - **Ne yapmalı:** Kripto tek başına suç kanıtı değildir; ama şirket bilgisi yoksa riski büyütür.

### 2. VipKulup — **DOKUNMA** (90/100)
Bağlantı: `t.me/ornek-vip`
- **Ön ödeme / aktivasyon / paket-VIP kapısı** (+40 puan) — `note` alanında "Aktivasyon" ifadesi
  - **Neden riskli:** Kazanca erişmek için önce sizden para isteniyor (aktivasyon ücreti, paket, VIP, bakiye yükleme). Gerçek görev/anket platformları sizden ücret almaz — ödemeyi onlar size yapar. Bu, 'ön ödeme tuzağı'nın (advance-fee fraud) anahtarıdır.
  - **Ne yapmalı:** DOKUNMA. Ödenen para geri alınamaz; sonrasında görünen 'kazanç' yalnızca ekran rakamıdır ve çekim aşamasında yeni şartlarla engellenir.
- **Gerçek dışı / garantili getiri vaadi** (+25 puan) — `mechanism` alanında "sınırsız kazan" ifadesi
  - **Neden riskli:** Yüksek ve 'garantili' getiri piyasada yoktur; vaadin büyüklüğü tek başına dolandırıcılık göstergesidir. Gerçek platformlarda kazanç değişkendir ve emek/veri karşılığıdır.
  - **Ne yapmalı:** Bağımsız, doğrulanabilir ödeme kanıtı olmadan bu tür vaatleri ciddiye almayın.
- **Hareketli çekim eşiği / davet şartı baskısı** (+15 puan) — `threshold` alanında "100 USD" ifadesi
  - **Neden riskli:** Çekim, davet/aktivite/şart tamamlamaya bağlanmış. Bu kurgunun amacı eşiğe hiç ulaştırmamaktır: eşiğe yaklaştıkça yeni bir şart eklenir ('hareketli çekim eşiği').
  - **Ne yapmalı:** Şartları yazılı olarak isteyin ve sabit mi diye bakın. Değişiyorsa veya davet gerektiriyorsa çıkın.
- **Yalnızca kripto ile geri döndürülemez ödeme kanalı** (+10 puan) — `payout` alanında "USDT" ifadesi
  - **Neden riskli:** Ödeme yalnızca kripto ise işlem geri alınamaz ve muhatap genellikle tüzel kişilik değildir; şikâyet edilecek bir merci bulunmaz.
  - **Ne yapmalı:** Kripto tek başına suç kanıtı değildir; ama şirket bilgisi yoksa riski büyütür.

### 6. SessizBot — **TEMKİNLİ OL** (35/100)
Bağlantı: `t.me/ornek-sessiz`
- **Yalnızca kripto ile geri döndürülemez ödeme kanalı** (+10 puan) — `payout` alanında "kripto" ifadesi
  - **Neden riskli:** Ödeme yalnızca kripto ise işlem geri alınamaz ve muhatap genellikle tüzel kişilik değildir; şikâyet edilecek bir merci bulunmaz.
  - **Ne yapmalı:** Kripto tek başına suç kanıtı değildir; ama şirket bilgisi yoksa riski büyütür.
- **Kurumsal iz yokluğu / anonimlik** (+15 puan) — `note` alanında "admin dm" ifadesi
  - **Neden riskli:** Muhatabın tüzel kişiliği, adresi veya resmî iletişimi yok. Sorun çıktığında hesap veren bir taraf bulunmaz.
  - **Ne yapmalı:** Doğrulama listesi: şirket unvanı/sicil, resmî site ve alan adı yaşı, destek kanalı, kullanım şartları, KVKK/gizlilik metni. Yoksa listeye almayın.
- **Şeffaflık eksikliği (belirsiz mekanizma)** (+10 puan) — `note` alanında "otomatik kazan" ifadesi
  - **Neden riskli:** Kazanç mekanizması açıklanmıyor: ne yapıldığı belli değilse, gelirin kaynağı da yoktur. 'Kim, neden, ne için ödüyor?' sorusu cevapsız kalır.
  - **Ne yapmalı:** Mekanizma net değilse (hangi görev, kim ödüyor, neden ödüyor) katılmayın.

### 3. TakimKur — **TEMKİNLİ OL** (35/100)
Bağlantı: `t.me/ornek-takim`
- **Referans piramidi / ponzi şeması** (+25 puan) — `mechanism` alanında "Takım kur" ifadesi
  - **Neden riskli:** Gelir, ürün/hizmet satışından değil yeni katılımcı getirmekten geliyor. Ponzi şemalarının tanımı budur: ödemeler eski katılımcıya yeni katılımcının parasından yapılır ve sistem matematiksel olarak er ya da geç çöker.
  - **Ne yapmalı:** Uzak dur. Getirdiğiniz kişiler de aynı kaybı yaşar; sosyal çevreniz zarar görür ve bazı durumlarda siz de sorumlu tutulursunuz.
- **Yalnızca kripto ile geri döndürülemez ödeme kanalı** (+10 puan) — `payout` alanında "TRX" ifadesi
  - **Neden riskli:** Ödeme yalnızca kripto ise işlem geri alınamaz ve muhatap genellikle tüzel kişilik değildir; şikâyet edilecek bir merci bulunmaz.
  - **Ne yapmalı:** Kripto tek başına suç kanıtı değildir; ama şirket bilgisi yoksa riski büyütür.

### 4. AnketOdak — **DÜŞÜK RİSK** (0/100)
Bağlantı: `t.me/ornek-anket`
- Bu kayıtta bilinen bir dolandırıcılık deseni bulunmadı. Bu **güvenlik kanıtı değildir**; aşağıdaki doğrulama listesini uygulayın.

### 5. PTCİzle — **DÜŞÜK RİSK** (0/100)
Bağlantı: `t.me/ornek-ptc`
- Bu kayıtta bilinen bir dolandırıcılık deseni bulunmadı. Bu **güvenlik kanıtı değildir**; aşağıdaki doğrulama listesini uygulayın.

## Her kazanç iddiası için doğrulama listesi

1. **Ödeme yönü:** Para sizden onlara mı gidiyor? Üyelik/aktivasyon/paket/VIP isteyen yerden kazanç değil, ürün satın almış olursunuz.
2. **Tüzel kişilik:** Şirket unvanı, sicil, adres, resmî destek kanalı, kullanım şartları, KVKK/gizlilik metni.
3. **Gelirin kaynağı:** 'Kim, neden, ne için ödüyor?' sorusunun net cevabı (anket müşterisi, reklam veren vb.).
4. **Ödeme kanıtı:** Platformun kendi ekran görüntüsü değil, bağımsız kaynaklardan doğrulanabilir ödeme kayıtları.
5. **Çekim şartları:** Eşik ve koşullar sabit mi? Davet/aktivite şartı ekleniyor mu? Değişiyorsa çıkın.
6. **Aciliyet:** 'Son şans / süreli / kontenjan' baskısı inceleme süresini kısaltmak içindir; durun.
7. **Kişisel veri:** TC kimlik, banka şifresi, SMS kodu, uygulama şifresi isteniyorsa kesinlikle vermeyin.

> Şüpheli durumda: platformu kullanmayı bırakın, ödeme yaptıysanız bankanıza iade/chargeback başvurusu yapın ve
> `https://www.siberay.gov.tr` üzerinden bildirimde bulunun; Telegram'da botu @notoscam/şikâyet kanallarına raporlayın.
