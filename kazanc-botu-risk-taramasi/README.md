# kazanc-botu-risk-taramasi

Telegram "kazanç botu" tablosunu **çevrimdışı** tarayan, her kaydı gerekçeli bir **dolandırıcılık
riski** skoruyla etiketleyen ve kaçınma raporu üreten araç.

## Bu araç ne yapar / ne YAPMAZ

| Yapar | YAPMAZ |
|-------|--------|
| Excel/CSV'deki metinleri yerel olarak analiz eder | Hiçbir ağ isteği atmaz (kaynakta HTTP istemcisi yok; bu bir testle sabitlendi) |
| Her kaydı 0–100 **risk** skoruyla etiketler | "Kazanç potansiyeli" / "hızlı para" / "en iyi hedef" sıralaması yapmaz |
| Bulguyu **gerekçe + öneri + eşleşen ifade** ile raporlar | Botlara bağlanmaz, mesaj atmaz, hesap açmaz, hesap/veri toplamaz |
| Kaçınma tavsiyesi ve doğrulama kontrol listesi verir | "Şu bota saldır / şunu çek" gibi bir yönlendirme üretmez |

Skor **yüksek = tehlikeli**. Düşük skor "güvenli" demek **değildir** — yalnızca metinde bilinen bir
desene rastlanmadığı anlamına gelir; raporda bu uyarı her seferinde yazılır.

## Kurulum

```bash
pip3 install pandas openpyxl
```

## Kullanım

```bash
# 1) Elinizde tablo yoksa aracı denemek için sentetik tablo üret
python3 risk_screen.py --demo-sheet demo_tablo.xlsx
python3 risk_screen.py --input demo_tablo.xlsx --sheet "🤖 Botlar"

# 2) Kendi tablonuzu tara (varsayılan dosya adı: telegram_bot_grup_kazanc_arastirmasi (2).xlsx)
python3 risk_screen.py --input "telegram_bot_grup_kazanc_arastirmasi (2).xlsx"
python3 risk_screen.py --input tablo.xlsx --sheet "Botlar" --out-md rapor.md --out-json rapor.json

# 3) Ağsız kendi kendini sınama (sentetik veri, geçici dizin)
python3 risk_screen.py --self-test

# 4) Birim testleri
python3 -m pytest test_risk_screen.py -q
```

Çıkış kodları: `0` tehlikeli bulgu yok · `1` en az bir DOKUNMA/TEMKİNLİ OL var · `2` kullanım/dosya hatası.

## Skorlama

| Ağırlık | Kural | Neye bakar |
|--------:|-------|-----------|
| 40 *(fatal)* | Ön ödeme / aktivasyon / paket-VIP kapısı | "para yatır", "aktivasyon", "paket", "VIP", "kazanma paketi", "önce yatırım" |
| 25 | Gerçek dışı / garantili getiri vaadi | "günde %", "garantili", "sınırsız kazan" |
| 25 | Referans piramidi / ponzi | "takım kur", "referans", "davet et" |
| 15 | Hareketli çekim eşiği / davet şartı | para birimi + eşik/davet/şart ifadeleri |
| 15 | Kurumsal iz yokluğu / anonimlik | "admin dm", "resmi site yok" |
| 10 | Geri döndürülemez ödeme kanalı | yalnızca kripto/USDT/TRX |
| 10 | Şeffaflık eksikliği | "otomatik kazan", "sırrı verilmez" |
| 10 | Aciliyet baskısı | "bugün bitiyor", "son şans" |

Etiketler: **DOKUNMA** ≥70 (veya ön ödeme kapısı tek başına) · **TEMKİNLİ OL** ≥30 · **DOĞRULA** ≥10
· **DÜŞÜK RİSK** <10.

Kural alan kısıtı taşır: para birimi deseni yalnızca çekim eşiği/not alanlarında, kripto deseni
yalnızca ödeme kanalı/not alanlarında çalışır — "20 USD kazanç" gibi normal bir ifade yanlış pozitif
üretmez (testle sabitlendi).

## Çıktılar

- **`rapor.md`** — özet tablo + kayıt başına gerekçeli bulgular (neden riskli / ne yapmalı),
  doğrulama kontrol listesi (7 madde) ve bildirim adresi (`https://www.siberay.gov.tr`).
- **`rapor.json`** — aynı içerik makine okunur biçimde: `source`, `sheet`, `columns`, `scanned`,
  `results[]` (`row`, `name`, `link`, `risk_score`, `label`, `findings[]`).
- Terminal: en yüksek riskli 5 (bulgu kanıtlarıyla) ve en düşük riskli 5 kayıt + etiket sayıları.

## Dosyalar

| Dosya | İçerik |
|-------|--------|
| `risk_screen.py` | Tarayıcı (tek dosya, stdlib + pandas, tip anotasyonlu, ağ erişimi yok) |
| `test_risk_screen.py` | 17 birim testi (etiket eşikleri, anahtar kelimeler, rapor biçimi, "ağ kütüphanesi yok" kontrolü) |
| `rapor.md` / `rapor.json` | Son taramanın çıktısı (demo verisi) |
| `demo_tablo.xlsx` | `--demo-sheet` ile üretilen sentetik örnek tablo |
