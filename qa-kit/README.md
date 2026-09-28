# WAFT · QA Regression Kit — Kayıt + E-posta Doğrulama Akışı

Bu kit, "form doldur → gönder → e-posta doğrulama linkini yakala → linki aç → akışı tamamla"
senaryosunu **sahibi olduğunuz / yazılı izin aldığınız** sistemlerde yük ve regresyon testi
olarak çalıştırır: kendi uygulamanız, staging ortamınız, müşteri test ortamı, bug-bounty
kapsamı ya da bir sağlayıcının size verdiği resmî sandbox.

> ⚠️ **Kapsam sınırı (bilerek katı):** timewall.io, jumptask.io ve benzeri 3. parti
> offerwall / mikro görev platformlarında otomatik hesap açma, proxy rotasyonu, stealth ve
> doğrulama-linki toplama bu kitin **dışındadır** ve `run_regression.py` bunu **her koşulda
> reddeder** (`--i-am-authorized` ile bile). O platformlarla entegrasyon testi, yalnızca
> sağladıkları resmî API/sandbox ve yazılı izinle yapılır — aksi hâlde bu bir yük testi değil,
> ödül sistemini manipüle etmeye yönelik bir abuse girişimidir (kullanım şartları ihlali +
> dolandırıcılık kapsamı). Aynı senaryoyu **kendi** kayıt akışınızda çalıştırmak istiyorsanız
> tek yapmanız gereken `--base-url` ile hedefi değiştirmek.

---

## 1. İçindekiler

| Dosya | Ne işe yarar |
|---|---|
| `targets.xlsx` | **Veri kaynağı** — 12 kayıt satırı + `columns` dokümantasyon sayfası |
| `make_targets.py` | Yukarıdaki Excel'i üreten, tipli ve parametrik script (`--base-url`, `--rows`, `--no-verification`) |
| `selectors.json` | **Yedekli (fallback) seçici kataloğu** — alan başına sıralı CSS + XPath zincirleri, host bazlı bloklar |
| `selector_resolver.py` | Kataloğu gerçek tarayıcıda deneyip `selectors.resolved.json` üretir (WAFT `--selectors` formatı) |
| `run_regression.py` | **WAFT driver'ı** — kapsam kontrolü, pre-flight, selector probe, 10 context, IMAP, manifest |
| `run_regression.sh` | Terminal komutu (env değişkenleriyle) + saf `python -m waft …` eşdeğeri |
| `authorized_hosts.txt` | **Kapsam dosyası** — çalıştırılabilecek host'ların allow-list'i |
| `devmail.py` | Bağımlılıksız SMTP (1025) + IMAP (1430) test sunucusu — Docker'sız doğrulama akışı |
| `docker-compose.mail.yml` | Alternatif: Mailpit (SMTP 1025 · IMAP 1430 · Web UI 8025) |
| `selectors.resolved.json` | Üretilen çıktı (örnek koşudan) — `--selectors` ile WAFT'a verilir |
| `selectors.resolved.report.json` | Hangi yedek seçicinin kazandığı / hangilerinin öldüğü raporu |

---

## 2. Doğrulama koşusunun içindeki adımlar

`run.json` içindeki her hedef çalışması şu adımlarla raporlanır:

```
navigate → form-scan → form-fill → submit → outcome
        → verification-link → email-verification → network-traffic
```

| Adım | Ne yapılır | Kanıt |
|---|---|---|
| `navigate` | Hedef URL açılır, `--verify-stealth` invariant'ları denetlenir | `01-loaded-*.png` |
| `form-scan` / `form-fill` | Alanlara eşleşme + doldurma (parolalar maskeli loglanır) | `02-filled-*.png` |
| `submit` / `outcome` | Gönder butonu bulunur-tıklanır; `success_selector` / URL / metin ile sonuç tespiti | `03-outcome-*.png` |
| `verification-link` | E-postadaki link **yeni sekmede** açılır | `04-verification-link-*.png` |
| `email-verification` | IMAP'ten link/OTP çıkarılır, doğrulama tamamlanır | `04-verified-*.png` |
| `network-traffic` | İstek/yanıt özeti, API adayları | `network.jsonl`, `network_summary.json`, `har/` |

Sonuçlar: `artifacts/<run-id>/{run.json, summary.md, results.csv, contexts.csv, junit.xml, endpoints.json}`
+ her context için ekran görüntüleri, HAR, konsol/ağ kayıtları, `*-failure-bundle.zip`.
Koşu parametreleri ve girdi hash'leri: `artifacts/run_manifest.json`.

---

## 3. Kurulum (5 dakika)

```bash
# 1) bağımlılıklar + tarayıcı
python -m pip install -r requirements.txt
python -m playwright install --with-deps chromium

# 2) yerel posta sunucusu (Docker yoksa devmail.py) — ayrı terminal
python qa-kit/devmail.py --smtp-port 1025 --imap-port 1430 --verbose
#    Docker varsa alternatif:
#    docker compose -f qa-kit/docker-compose.mail.yml up -d

# 3) test edilecek uygulama (bu repodaki demo; kendi staging'iniz için atlayın)
python examples/demo_site.py --port 8080 --quiet \
  --smtp-host 127.0.0.1 --smtp-port 1025 --public-url http://127.0.0.1:8080

# 4) veri dosyası (kendi hedefiniz için --base-url'i değiştirin)
python qa-kit/make_targets.py --base-url http://127.0.0.1:8080 --rows 12 --mail-domain demo.waft.local

# 5) seçicileri gerçek sayfada doğrula
python qa-kit/selector_resolver.py --data qa-kit/targets.xlsx
```

`demo_site.py --smtp-host …` verildiğinde kayıt sonrası gerçek bir doğrulama e-postası gönderir
(konu: *"Hesabınızı doğrulayın (verify your account)"*, gövdede `/verify?token=…` linki), yani
zincirin tamamı gerçek SMTP + gerçek IMAP üzerinden çalışır — mock yok.

---

## 4. Çalıştırma

### 4.1 Yerel, uçtan uca (10 bağlam, 10 paralel)

```bash
python qa-kit/run_regression.py \
  --data qa-kit/targets.xlsx \
  --contexts 10 --concurrency 10 \
  --proxy-mode off \
  --imap --imap-host 127.0.0.1 --imap-port 1430 \
  --imap-user devmail --imap-password devmail --imap-no-ssl \
  --imap-subject-regex "(doğrula|dogrula|verify|aktivasyon|activat)" \
  --captcha-action skip \
  --stealth --verify-stealth --capture-har --log-network \
  --trace on-failure --rate-limit 5 --no-color
```

Bu koşunun doğrulanmış çıktısı (bu repoda çalıştırıldı):

```
WAFT run run-20260928-162335-dd3780 PASSED ✅ | 120/120 target(s) ok (100.0%) | 101.4s
  contexts      : 10 ok / 0 failed (of 10)
  targets       : 120 run, 120 ok, 0 failed, 100.0% success
  steps         : 960 ok / 0 failed
  e-mail verif. : 120 ok / 0 failed
```

### 4.2 Kendi staging ortamınız (proxy + gerçek IMAP)

```bash
# .env:  IMAP_HOST=imap.sirketiniz.com  IMAP_USER=qa@…  IMAP_PASSWORD=…
export BASE_URL=https://staging.sirketiniz.com
./qa-kit/run_regression.sh                 # proxy listesi: proxies.txt
```

veya doğrudan:

```bash
python qa-kit/run_regression.py \
  --data qa-kit/targets.xlsx --base-url https://staging.sirketiniz.com \
  --contexts 10 --concurrency 5 \
  --proxies proxies.txt --proxy-mode auto \
  --mode cli \
  --imap --imap-host imap.sirketiniz.com --imap-user qa@… --imap-password '…' \
  --captcha-action skip --stealth --verify-stealth --capture-har --log-network
```

`--mode cli` seçilirse aynı parametreler `python -m waft …` alt sürecine çevrilir;
`--print-command` sadece eşdeğer CLI komutunu yazdırır (runbook/CI için).

### 4.3 Yeni hedef ekleme (kendi uygulamanız)

1. `authorized_hosts.txt` → host'u ekleyin (`staging.sirketiniz.com` ya da `*.sirketiniz.com`).
2. `selectors.json` → `hosts` altına host bloğunu ekleyin (alan başına 3-6 yedek seçici;
   CSS'i önce, XPath'i sonra yazın). Generic `*` bloğu zaten çalışır durumda.
3. `python qa-kit/selector_resolver.py --data qa-kit/targets.xlsx` → hangi alan çözülemedi?
4. `python qa-kit/make_targets.py --base-url https://staging.sirketiniz.com --rows 15`.
5. `./qa-kit/run_regression.sh --dry-run` ile pre-flight + planı doğrulayın, sonra gerçek koşu.

---

## 5. Veri şeması (`targets.xlsx`)

| Sütun | Zorunlu | Açıklama |
|---|---|---|
| `target_url` | ✅ | Açılacak kayıt formu URL'i |
| `scenario` | – | `email-verify` (bu kit varsayılanı), `form-submit`, `smoke`, `load-test` … |
| `email` | ✅ | Forma yazılan adres **ve** IMAP'te beklenen alıcı |
| `password` | ✅ | Parola (loglarda maskelenir) |
| `first_name` | – | Alias eşleştirmesini test eder (name/ad/isim/…) |
| `success_selector` | – | Başarıyı kanıtlayan CSS (ör. `[data-testid='success']`) |
| `requires_email_verification` | – | `true` → gönderim sonrası IMAP adımı çalışır |
| `verification_email` | – | IMAP'te aranacak posta kutusu (genelde `email` ile aynı) |
| `verification_subject_regex` | – | Konu filtresi: `(doğrula\|dogrula\|verify\|aktivasyon\|activat)` |
| `last_name` / `password_confirm` / `terms` | – | Çoğu kayıt formunun zorunlu alanları (şablonda hazır) |
| `name` / `iterations` / `tags` / `notes` | – | Raporlama, tekrar sayısı, CI filtreleme |

Excel'e **kendi posta kutunuz dışında adres yazmayın**: `verification_email` sütunu IMAP'ten
okunacak hesabı belirtir; başkasının kutusunu izlemek teknik olarak da mümkün değildir.

---

## 6. Selector kataloğu ve resolver

`selectors.json` alan başına **sıralı yedekleme** tanımlar; resolver bunları gerçek tarayıcıda
dener, kazananı WAFT'ın anladığı kanonik forma çevirir (`#id`, `[name='…']`, `[placeholder='…']`)
ve `selectors.resolved.json` olarak yazar:

```json
{ "email": "#email", "password": "#password", "password_confirm": "#password_confirm",
  "first_name": "#fname", "last_name": "#lname", "terms": "#terms" }
```

* `submit` / `success` / `verified` / `error` / `captcha` seçicileri **gönderim sonrası**
  elemanlardır; landing page'de bulunamazlar → raporda "informational" olarak işaretlenir ve
  0 çıkış kodu verilir. Sonuç tespiti için `success_selector` sütununu kullanın.
* Sayfa yeniden tasarlanırsa: `selector_resolver.py` tekrar çalıştırın; rapor hangi yedeğin
  kazandığını, hangilerinin kırıldığını gösterir. Dead selector'ları temizleyin.

---

## 7. CAPTCHA politikası

| Değer | Davranış |
|---|---|
| `skip` (kit varsayılanı, şartnamedeki "blocked_captcha") | CAPTCHA görülen **hedef** başarısız/atlanmış olarak işaretlenir, paket devam eder |
| `continue` | CAPTCHA yok sayılır, otomasyon elinden geldiğince devam eder |
| `error` | O hedef hata ile durur (varsayılan WAFT davranışı) |

Kendi ortamınızda CAPTCHA görüyorsanız doğru çözüm politikayı yumuşatmak değil, staging'de
test anahtarı kullanmak ya da CAPTCHA'yı devre dışı bırakmaktır — CAPTCHA bir bot korumasıdır.

---

## 8. Çıkış kodları ve pre-flight

| Kod | Anlamı |
|---|---|
| `0` | Tüm hedefler geçti |
| `1` | En az bir hedef başarısız (artefaktlarda kanıt var) |
| `2` | Kullanım/kapsam/veri hatası (scope dışı host, bozuk Excel, eksik dosya) |
| `3` | Proxy gereksinimi karşılanamadı (`--proxy-mode require`) |
| `130` | Kullanıcı iptali (Ctrl+C) |

Pre-flight şunları kontrol eder: veri dosyası + satır sayısı, kapsam listesi, proxy dosyası,
Playwright tarayıcıları, boş disk alanı (≥ 2 GB), IMAP ayarları ve context/paralellik dengesi.
Hata varsa **tarayıcı hiç açılmadan** çıkılır.

---

## 9. IMAP notları

* **Yerel**: `devmail.py` (bağımlılıksız) ya da Mailpit → `--imap-no-ssl --imap-port 1430`.
* **Gmail**: normal parola çalışmaz; **uygulama şifresi** üretip `IMAP_HOST=imap.gmail.com`,
  `IMAP_PORT=993`, `IMAP_SSL=true` kullanın. Sadece kendi hesabınızı izleyin.
* **Kurumsal**: `STARTTLS` gerekiyorsa `--imap-starttls --imap-port 143`.
* Aynı e-posta iki kez tüketilmez (UID takibi); `IMAP_MARK_SEEN` / `IMAP_MARK_PROCESSED` ile
  kutuyu düzenli tutabilirsiniz.

---

## 10. Bu kit hazırlanırken bulunup düzeltilen framework hataları

Kit, WAFT'ın gerçek koşullarda sınanmasıydı; dört gerçek hata çıktı ve düzeltildi (hepsi
75 testlik suite ile doğrulandı):

1. **`Stopwatch.elapsed_ms` donuyordu** — `__enter__()` sonrası değer güncellenmediği için
   IMAP bekleme döngüsü (`while elapsed < timeout`) hiç bitmiyordu. Artık `elapsed_ms`
   canlı bir property (`waft/utils.py`) ve IMAP döngüsü ayrıca `time.monotonic()` deadline
   kullanıyor (`waft/imap_client.py`).
2. **`FormFiller.plan` aynı değeri iki kez eşleştiriyordu** — açık seçici (override) ile
   yerleştirilen anahtar "kullanıldı" sayılmadığı için parola bir kez daha `password_confirm`
   alanına eşleşiyor ve `email`/`first_name` yanlışlıkla "eşleşmedi" diye raporlanıyordu
   (`waft/forms.py`).
3. **Link sekmesi devralındıktan sonra eski sayfa kullanılıyordu** — `_apply_otp` kapanmış
   sayfada `Page.evaluate` çağırıp doğrulama adımını patlatıyordu (`waft/engine.py`).
4. **Şablon verisi eksikti** — şablon artık `last_name`, `password_confirm`, `terms`
   sütunlarını da üretiyor; HTML5 `required` alanları boş kalınca form hiç gönderilmiyordu
   (`qa-kit/make_targets.py`).

---

## 11. Yasal

Bu kiti yalnızca yetkili olduğunuz sistemlerde kullanın. `authorized_hosts.txt` bunun için var:
izinsiz bir üretim sistemine yük bindirmek veya otomatik hesap açmak suç teşkil edebilir ve
karşı tarafın kullanım şartlarını ihlal eder. Şüphedeyseniz: yazılı izin yoksa çalıştırmayın.
