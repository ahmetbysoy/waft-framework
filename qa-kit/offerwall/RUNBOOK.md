# RUNBOOK — Offerwall kitini kurma ve ateşleme (düzeltilmiş tarif)

Bu doküman, senin 6 adımlık tarifinin **bu repodaki gerçek dosya adları ve gerçek CLI arayüzüyle**
düzeltilmiş hâli. Her komut bu makinede çalıştırıldı; çıktılar aşağıda birebir yazılı.

> **Kapsam:** kit yalnızca **senin sahip olduğun / yazılı izin aldığın** sistemlere koşar: paketle
> gelen yerel mock sunucu (`--sandbox`), kendi staging'in (`--base-url`), ya da partnerin yazılı
> verdiği sandbox. `run_offerwall.py` artık bunu **çalışma anında zorlar** (aşağıda §7):
> timewall.io / jumptask.io gibi 3. parti offerwall-mikro görev platformları, `--i-am-authorized`
> verilse bile reddedilir. Oralarda otomatik hesap açmak yük testi değil, abuse olur.

---

## 0. Klasör haritası (hangi dosya ne işe yarar)

| Dosya | Rol |
|---|---|
| `account_pool.py` | Tipli hesap havuzu; `for_context(i) = accounts[i % n]` → her context'e **farklı** hesap |
| `credentials.example.json` | 10 Gmail hesabı şablonu (**App Password** zorunlu notu ile) — `credentials.json` olarak kopyalayın |
| `credentials.sandbox.json` | 10 × `qaNN@demo.waft.local`, yerel devmail'e bağlı — `--sandbox` bunu kullanır |
| `make_targets_offerwall.py` | **`targets_offerwall.xlsx` üreteci** (senin tarifindeki `make_targets.py`'nin gerçek adı) |
| `targets_offerwall.xlsx` | 6 örnek satır, 24 kolon (istediğin 13 kolon + `password_confirm`, `country`, `terms`, `expect_error`, …) |
| `selectors_offerwall.json` | Alan başına 3-6 yedekli CSS/XPath kataloğu (host bloklu) |
| `selectors.resolved.json` | `selector_resolver.py` çıktısı; WAFT `--selectors` bunu okur (kitin varsayılanı) |
| `run_offerwall.py` | Ana wrapper: hesap havuzu + context pinleme + Config + Orchestrator + `endpoints.json` özeti |
| `sandbox_up.sh` | Tek komut: mock posta + mock offerwall'ı kaldırır, koşuyu çalıştırır, temizler |
| `postback_receiver.py` | S2S postback alıcısı + yük/replay/imza dayanıklılık kapısı (§8) |
| `../../examples/offerwall_sandbox.py` | Kayıt + anket + e-posta doğrulama + XHR API içeren yerel mock site |
| `../../qa-kit/devmail.py` | Yerel SMTP (1025) / IMAP (1430) sunucusu |

---

## 1. Hızlı başlangıç — tek komut (önerilen ilk adım)

```bash
bash qa-kit/offerwall/sandbox_up.sh
```

Gerçek çıktı (bu repoda):

```text
[sandbox_up] devmail başlatılıyor (SMTP 1025 / IMAP 1430) → artifacts/_sandbox_logs/devmail.log
[sandbox_up] devmail SMTP hazır (127.0.0.1:1025)
[sandbox_up] devmail IMAP hazır (127.0.0.1:1430)
[sandbox_up] offerwall sandbox başlatılıyor (http://127.0.0.1:8090) → artifacts/_sandbox_logs/sandbox.log
[sandbox_up] koşu başlıyor: 10 context / 5 paralel (run_offerwall.py --sandbox)
🔒 sandbox modu — yerel mock hedef: http://127.0.0.1:8090 | posta kutusu: 127.0.0.1:1430 (SSL off) | proxy: off
→ scope: 4 pattern(s) from qa-kit/authorized_hosts.txt | blocked_third_party=none | out_of_scope_override=none
  hedefler         : 60 koşu, 60 ok, 0 fail (%100.0)
  e-posta doğrulama: 20 ok / 0 fail
→ exit code: 0 (PASSED)
[sandbox_up] kapatıldı (pid 4038)
```

Seçenekler: `--keep` (sunucuları açık bırakır), `CONTEXTS=4 CONCURRENCY=2 bash qa-kit/offerwall/sandbox_up.sh`,
`WEB_PORT=8091 SMTP_PORT=1026 IMAP_PORT=1431 bash qa-kit/offerwall/sandbox_up.sh` (port çakışması varsa).
Script yalnızca **kendi başlattığı** süreçleri kapatır; zaten dinleyen bir sunucuya dokunmaz.

---

## 2. Kurulum — adım adım (senin tarifindeki düzeltmelerle)

```bash
# 2.1 bağımlılıklar
pip install -r requirements.txt          # pandas, openpyxl, playwright, python-dotenv, rich, …
pip install playwright-stealth           # stealth katmanı (isteğe bağlı ama önerilir)
python -m playwright install --with-deps chromium

# 2.2 hesap havuzu: şablonu kopyala ve gerçek hesapları yaz
cp qa-kit/offerwall/credentials.example.json qa-kit/offerwall/credentials.json
#   → Gmail'de 2 adımlı doğrulamayı açıp 16 karakterlik "Uygulama Şifresi" üretin;
#     normal Gmail parolası IMAP ile çalışmaz.

# 2.3 hedef Excel'i üret
python qa-kit/offerwall/make_targets_offerwall.py --base-url http://127.0.0.1:8090
#   kendi staging'iniz için:  --base-url https://staging.sirketiniz.com

# 2.4 seçicileri gerçek tarayıcıda çöz  (SENİN TARİFİNDE EKSİK OLAN ADIM)
python qa-kit/selector_resolver.py --data qa-kit/offerwall/targets_offerwall.xlsx \
  --selectors qa-kit/offerwall/selectors_offerwall.json \
  --out qa-kit/offerwall/selectors.resolved.json

# 2.5 proxy listesi (kullanacaksanız) — ayrıntı §4
cp proxies.example.txt proxies.txt

# 2.6 IMAP ayarları — ayrıntı §5
cp .env.example .env
```

**Senin tarifinde düzeltilen 4 nokta**

1. `make_targets.py` diye bir dosya yok → **`make_targets_offerwall.py`**.
2. Tarifte **seçici çözümleme adımı yok**; `selectors.resolved.json` üretilmeden koşu,
   seçicilerin katalogdaki ilk hâliyle dener (yedekleri kullanamadan) çalışır.
3. `python run_offerwall.py` (argümansız) **`credentials.json` yoksa durur** (exit 2) —
   çünkü repoda gerçek hesap dosyası bilerek yok. Tek komut için `--sandbox` kullan (§3.1).
4. `.env` bloğunda yalnızca `IMAP_HOST/PORT/SSL` var; `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_MAILBOX`,
   `IMAP_TIMEOUT_S`, `IMAP_SUBJECT_REGEX` de gerekir (§5).

---

## 3. Ateşleme komutları

### 3.1 Yerel sandbox (hesap dosyası gerekmez)

```bash
python qa-kit/offerwall/run_offerwall.py --sandbox
```

`--sandbox` şunları ayarlar: `credentials.sandbox.json`, hedefler `http://127.0.0.1:8090`,
IMAP `127.0.0.1:1430` (SSL off), proxy `off`. **Açıkça verdiğin bayrak her zaman kazanır.**

### 3.2 Kendi staging / pre-prod ortamın

```bash
python qa-kit/offerwall/run_offerwall.py \
  --credentials qa-kit/offerwall/credentials.json \
  --targets qa-kit/offerwall/targets_offerwall.xlsx \
  --base-url https://staging.sirketiniz.com \
  --contexts 10 --concurrency 5 \
  --proxies proxies.txt --proxy-mode require \
  --imap-host imap.gmail.com --imap-port 993 --imap-ssl on --imap-timeout 180
```

`staging.sirketiniz.com`'i `qa-kit/authorized_hosts.txt` içine eklemeyi unutmayın, yoksa koşu
scope kapısında durur (bu istenen davranıştır — yanlışlıkla başka bir sisteme yük bindirmezsiniz).

### 3.3 İstediğin üretim profili (birebir)

```bash
python qa-kit/offerwall/run_offerwall.py \
  --credentials qa-kit/offerwall/credentials.json \
  --targets qa-kit/offerwall/targets_offerwall.xlsx \
  --contexts 10 --concurrency 5 --rate-limit 2.0 --retries 2 \
  --proxies proxies.txt --proxy-mode auto \
  --imap-subject-regex "(doğrula|verify|confirm|aktivasyon|aktivasyon|activate)" \
  --imap-timeout 180 --artifacts artifacts \
  --log-level INFO
```

`stealth=True`, `verify_stealth=True`, `capture_har=True`, `log_network=True`,
`captcha_action="skip"`, `trace="on-failure"` wrapper'da sabittir (senin istediğin gibi).
`sandbox` yerine gerçek hedeflerle koşuyorsan §7'yi okuyun.

---

## 4. `proxies.txt` — format ve karar

```text
# yorum satırları '#' ile başlar
user:pass@proxy1.example.com:8080
http://user:pass@proxy2.example.com:3128
socks5://user:pass@proxy3.example.com:1080
socks5h://proxy4.example.com:1080
1.2.3.4:8080
```

* `--proxy-mode require` → liste boş/geçersizse **koşu başlamaz** (exit 3).
* `--proxy-mode auto` → dosya yoksa/boşsa proxy kapatılır (uyarı basılır).
* `--proxy-mode off` → hiç kullanılmaz (`--sandbox` bunu seçer).
* Proxy'siz çıkışta `--no-humanize` ile hızlanabilirsiniz; stealth yine açık kalır.

---

## 5. `.env` — IMAP ayarları (Gmail)

```dotenv
# --- doğrulama maili okuma (Gmail örneği) ------------------------------------
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_SSL=true
IMAP_USER=hesap1@gmail.com
IMAP_PASSWORD=onaltikarakteruygulamasifresi
IMAP_MAILBOX=INBOX
IMAP_TIMEOUT_S=180
IMAP_POLL_INTERVAL_S=5
IMAP_SUBJECT_REGEX=(doğrula|verify|confirm|aktivasyon|activate)
IMAP_MARK_SEEN=false

# --- yerel test posta sunucusu (devmail/Mailpit) — SSL kapalı -----------------
# IMAP_HOST=127.0.0.1
# IMAP_PORT=1430
# IMAP_SSL=false

# --- WAFT çekirdeği ----------------------------------------------------------
WAFT_PROXY_FILE=proxies.txt
```

Not: `run_offerwall.py` IMAP bilgilerini **hesap havuzundan** (her hesabın `imap_host`/`imap_port`
alanından) ve CLI bayraklarından alır; `.env` ise WAFT çekirdeğinin kendi env-var katmanı için
geçerlidir. İkisi çelişirse CLI bayrağı kazanır.

---

## 6. Çıktılar ve exit kodları

```text
artifacts/run-<zaman>-<id>/
  run.json          özet sayaçlar, süre, stealth doğrulaması, network metrikleri
  results.csv       satır bazında sonuç (context, target, status, süre, hata, doğrulama)
  contexts.csv      context bazında özet (proxy, UA, timezone, hesap)
  endpoints.json    KEŞFEDİLEN API UÇLARI (metot, host, yol, status dağılımı, çağrı sayısı)
  junit.xml         CI raporu
  har/              her context için HAR (ağ trafiği)
  screenshots/      her adımın ekran görüntüsü
  traces/           yalnızca hatalı hedefler için Playwright trace .zip
artifacts/offerwall_manifest.json   hesap eşlemesi (parolalar maskeli), imap/proxy/scope ayarları
```

| Exit | Anlamı |
|---|---|
| 0 | Hepsi geçti |
| 1 | Hedef başarısızlıkları var (rapor üretildi) |
| 2 | Kullanım / kapsam / kimlik bilgisi hatası |
| 3 | Proxy hatası |
| 130 | Ctrl-C (kısmi artefaktlar korunur) |

---

## 7. Kapsam kapısı (koşu anında zorlanır)

```bash
# 3. parti platform → HER durumda reddedilir
$ python qa-kit/offerwall/run_offerwall.py --targets /tmp/scope_blocked.xlsx --proxy-mode off --dry-run
✖ scope: third-party offerwall / micro-task platform(s) are out of scope for this kit: timewall.io.
  …For your own staging, pass --base-url or use --sandbox.
exit=2

# --allow-host + --i-am-authorized bile bu listeyi geçemez
$ python qa-kit/offerwall/run_offerwall.py --targets /tmp/scope_blocked.xlsx \
    --allow-host timewall.io --i-am-authorized --dry-run
✖ scope: third-party offerwall / micro-task platform(s) are out of scope for this kit: timewall.io.
exit=2

# listede olmayan başka bir host → yine durur
$ … --targets /tmp/scope_unknown.xlsx --dry-run
✖ scope: host(s) not covered by the scope file: example.com — Add the host to
  qa-kit/authorized_hosts.txt …
exit=2
```

Kendi ortamınızı eklemek: `qa-kit/authorized_hosts.txt` içine host satırı (ör. `staging.ornek.com`
veya `*.staging.ornek.com`). `authorized_hosts.txt` zaten `127.0.0.1`, `localhost`, `::1`,
`*.waft.local` içerir — bu yüzden `--sandbox` sorunsuz geçer.

---

## 8. Doğrulanmış sonuçlar (bu repoda, bu RUNBOOK yazılırken)

| Koşu | Komut | Sonuç |
|---|---|---|
| Yerel sandbox | `run_offerwall.py --sandbox` | **60/60 hedef, 370/370 adım, 20/20 doğrulama, 4 API ucu, exit 0** (~48 s) |
| Tek komut script | `bash sandbox_up.sh` | aynı sonuç + sunucular otomatik kalktı/temizlendi |
| Postback dayanıklılık | `postback_receiver.py --selftest --workers 10 --requests 500` | 500 kabul, 20 duplicate, 10 sahte imza (401), 10 replay (410), 1 bozuk payload (400); 1107 rps, p99 12.7 ms |
| Bozuk konfig kapısı | `--replay-window 0.001` | assertion'lar düştü, **exit 1** (CI kapısı olarak kullanılabilir) |
| Kapsam kapısı | §7'deki 3 komut | üçü de **exit 2** |

Keşfedilen uçlar (yerel sandbox koşusu):

```text
GET  127.0.0.1:8090/api/v1/app-config        [200, 360×]
POST 127.0.0.1:8090/register                 [200, 360×]
POST 127.0.0.1:8090/survey/1/submit          [200,  80×]
POST 127.0.0.1:8090/survey/2/submit          [200,  60×]
```

---

## 9. Sık karşılaşılan hatalar

| Belirti | Sebep / çözüm |
|---|---|
| `ModuleNotFoundError: waft` / `No module named 'playwright'` | Repo kökünden çalıştırın ve `pip install -r requirements.txt` yapın |
| `playwright` tarayıcı açılmıyor, `libnspr4.so` hatası | `python -m playwright install --with-deps chromium` |
| `credential file not found: …/credentials.json` | `--sandbox` kullanın ya da §2.2'deki kopyalamayı yapın |
| Doğrulama 0/N, kayıt POST'u hiç gitmemiş | Formda `password_confirm` gibi zorunlu alan eksik olabilir; `results.csv` → `error` kolonuna bakın |
| IMAP 993'te takılıyor | Gmail'de **App Password** kullanın; yerel devmail'de `--imap-ssl off --imap-port 1430` |
| `blocked_captcha` hedefler | Politika gereği (`captcha_action=skip`): o hedef işaretlenir, paket **devam eder** |
| Proxy'siz koşuda `exit 3` | `--proxy-mode require` verilmiş ama liste boş → `auto` veya `off` kullanın |

---

## 10. Taslak wrapper incelemesi (8 gerçek hata) ve düzeltilmiş sürücü

Gönderdiğin tek dosyalık wrapper taslağı, WAFT 1.0.0'ın gerçek API'siyle satır satır
karşılaştırıldı. Sekiz bulgu **çalıştırılabilir kanıta** bağlandı:
`tests/test_offerwall_kit_contracts.py` (17 test).

| # | Taslakta | Gerçek / sonuç | Test |
|---|---|---|---|
| 1 | `from waft.data_source import load_data` | Böyle bir fonksiyon **yok**; `DataLoader(config).load()` ve dönüş `list[TargetRow]`, DataFrame **değil** → `df.iterrows()` çöker | `test_draft_module_attributes_do_not_exist`, `test_loader_returns_typed_rows_not_a_dataframe` |
| 2 | `from waft.reporting import generate_summary` | **Yok**; `Reporter`, `render_summary_table`, `summary_as_dict` var | aynı test |
| 3 | `Config(imap=…, imap_timeout=…, trace=…, artifacts=…)` | Alan adları `imap_enabled`, `imap_timeout_s`, `trace_mode`, `artifacts_dir`; `Config` bir dataclass olduğu için bilinmeyen kwarg **TypeError** verir | `test_draft_config_keywords_are_rejected` |
| 4 | `Orchestrator(config, targets)` | İmza `Orchestrator(config)`; satırlar `config.data_file`'dan gelir | `test_orchestrator_takes_only_config` |
| 5 | **`acc['password'] = "***MASKED***"` sonra o değer formda kullanılıyor** | Maskeleme **girdiyi yok ediyor**: forma `***MASKED***` yazılır, kayıt ve IMAP doğrulaması çöker. Maskeleme yalnızca çıktı sınırında yapılmalı (`mask_secret`) | `test_masking_is_display_only_and_never_mutates_the_secret` |
| 6 | `form_data` 6 alana kırpılıyor | `country`, `terms`, `contact_consent` düşer → kayıt formu **submit olmaz** (daha önce `password_confirm` ile birebir bu sınıf hata yaşandı) | `test_binding_preserves_every_form_field_and_scopes_verification` |
| 7 | `verification_email` **her** satıra konuyor | Doğrulama alanları yalnızca `email-verify` satırlarında olmalı, yoksa mail beklemeyen satırlar timeout'a düşer | aynı test |
| 8 | `artifacts/endpoints.json` + `status_codes` + düz liste varsayımı | Gerçek: `artifacts/<run_id>/endpoints.json`, yapı `{"endpoints": [...]}`, alan `statuses` → özet boş çıkar | `test_endpoints_payload_shape_and_reader` |

**Düzeltilmiş, çalışan sürüm:** `qa-kit/offerwall/run_offerwall_min.py` (tek dosya, tam tip
belirtimli, try/except'li, senin taslağındaki banner/loglama tarzı korunmuş). Tekrar eden
mantığı kopyalamaz; test edilmiş parçaları **import eder** (`AccountPinnedOrchestrator`,
`bind_rows_to_account`, `enforce_scope`, `load_scope`).

```bash
# yerel sandbox (varsayılan)
python qa-kit/offerwall/run_offerwall_min.py

# kendi staging'iniz
python qa-kit/offerwall/run_offerwall_min.py --base-url https://staging.sirketiniz.com \
  --credentials qa-kit/offerwall/credentials.json --proxy-mode require
```

Doğrulanmış koşu (bu repoda):

```text
│ Contexts        │ 10 ok / 0 failed │ E-mail verifications │ 20 ok / 0 failed │
│ Targets run     │               60 │ API endpoints        │                4 │
│ Targets ok      │               60 │ Duration             │          47.85 s │
WAFT run run-20260928-184855-dd0a5e PASSED ✅ | 60/60 target(s) ok (100.0%)

API endpoint özeti — run-20260928-184855-dd0a5e (4 uç)
  GET    127.0.0.1:8090/api/v1/app-config [status 200, 360 kez]
  POST   127.0.0.1:8090/register [status 200, 360 kez]
  POST   127.0.0.1:8090/survey/1/submit [status 200, 80 kez]
  POST   127.0.0.1:8090/survey/2/submit [status 200, 60 kez]
```

Kapsam kapısı bu sürücüde de zorunlu:

```text
$ python qa-kit/offerwall/run_offerwall_min.py --base-url https://timewall.io
✖ scope: third-party offerwall / micro-task platform(s) are out of scope for this kit: timewall.io …  (exit 2)

$ python qa-kit/offerwall/run_offerwall_min.py --base-url https://example.com
✖ scope: host(s) not covered by the scope file: example.com …                                  (exit 2)
```

### HAR'dan "daha hızlı bot" fikri hakkında

Taslağın sonundaki ipucu — "HAR'ı incele, form yerine doğrudan API'ye istek atan daha hızlı bir
bot yaz" — **kendi sistemin için** tamamen meşru ve doğru bir mühendislik hamlesidir: oturum
açma, CSRF token akışı, idempotency anahtarı gibi sözleşmeleri API seviyesinde test etmek
formu sürmekten hem hızlı hem kararlıdır. Bu kit zaten bunun altyapısını verir: her context'in
HAR'ı `artifacts/<run_id>/har/` altında, uç listesi `endpoints.json`'da.

Ama **başkasının platformunda** aynı şey şu anlama gelir: tespit edilen kayıt/ödeme uçlarına
10 hesapla doğrudan istek atmak — CAPTCHA'yı, rate limit'i ve arayüzü devre dışı bırakarak
sistemi kötüye kullanmak. Bu yüzden o adımı üçüncü taraf hedefler için yazmıyorum; kendi
sisteminiz veya yazılı izin aldığınız sandbox için `postback_receiver.py --selftest` ile
başlayan API seviyesi test yolunu birlikte kurabiliriz.

## 11. Selector kataloğu değerlendirmesi (gönderdiğin JSON) — ve çözümleyicide bulunan 2 gerçek bug

Gönderdiğin katalog kitin şemasına çevrildi (aday zincirleri **birebir**, yalnızca anahtar adları:
`submit_button → submit`, `captcha_frame → captcha`): `selectors_user_draft.json`. Gerçek tarayıcıda
yoklandı; sonuç: `selectors_user_draft.report.json`.

### 11.1 Sandbox kayıt sayfasında aday aday sonuç

| Alan | Adaylar | Kazanan |
|---|---|---|
| email | `input[name='email']` → `input[type='email']` → `input[id*='email' i]` → `input[autocomplete='email']` | **1. aday** → `#register-email` |
| password | `input[name='password']` → … | **1. aday** → `#register-password` |
| password_confirm | `input[name='password_confirm']` → … | **1. aday** → `#register-password-confirm` |
| first_name / last_name | `input[name='first_name']` / `input[name='last_name']` → … | **1. aday** → `#first-name` / `#last-name` |
| submit | `button[type='submit']` → `input[type='submit']` → `:has-text('Kayıt Ol'/'Register'/'Sign Up')` | **1. aday** → `#register-submit` |
| captcha | `iframe[src*='recaptcha']` → … | `/register`'da **yok** (beklenen); `/captcha` sayfasında **1. aday** ✔ |

Yani zincirlerin bu sayfada sağlam: ilk aday hepsini karşılıyor, yedekler hiç yük almadı.
`:has-text(...)` adayların sandbox'ta ölü (buton metni "Hesap oluştur") — ama **geçerli**:
WAFT `find_submit()` gerçekten `button:has-text("…")` kullanıyor, yani onlar ölü kod değil.

**Eksik kalan 4 alan:** `country`, `terms`, `success`, `error`. Bunlar senin katalogunda yok.
Ölçtüm: bu sandbox'ta sezgisel eşleme (`forms.py`, TR+EN etiket puanlaması) boşluğu kapatıyor →
`--selectors` ile senin 6 alanlık haritanı verdiğimde de koşu **60/60**. Ama kontrol deneyi şunu
gösterdi: **boş** harita (`{}`) ile de **60/60**. Yani bu sayfa "dostu" (temiz id/name/etiket);
kataloğun değeri, sayfa yeniden tasarlandığında veya etiketler belirsizleştiğinde eşlemeyi
**kilitlemesidir** — bu yüzden `country`/`terms`/`success`/`error` zincirlerini de ekle.

### 11.2 Çözümleyicide bulunan iki gerçek hata (ve düzeltmeleri)

Sentetik bir sayfa kurdum (iki **anonim** parola input'u + biri reklam biri captcha olan **iki**
iframe) ve çözümleyiciyi orada koşturdum. Düzeltme **öncesi** üretilen harita:

```json
{"password": "input[type='password']", "password_confirm": "input[type='password']", "captcha": "iframe"}
```

1. **Yanlış elemana eşleme (sessiz hata).** `password_confirm` adayı
   `(//input[@type='password'])[2]` → canonical `input[type='password']`, yani **parola alanının
   kendisi**. Onay alanı boş kalır, kayıt POST'u patlar — tam da `password_confirm` dersinin
   tekrarı — ve rapor "matched" derdi. Kök neden: round-trip kontrolü "canonical *bir şeye*
   uyuyor mu" diye bakıyordu, "*aynı* elemana mı" diye bakmıyordu.
   **Düzeltme:** problanan eleman geçici bir `data-waft-probe` token'ı ile işaretleniyor ve
   canonical'ın çözdüğü elemanda o token aranıyor; uyuşmazsa aday
   `canonical selector points at a different element` ile **reddediliyor** (token her durumda
   temizleniyor).
2. **İfade kaybı (bare tag).** `iframe[src*='recaptcha']` canonical'ı `iframe` oluyordu — sayfada
   iki iframe olduğunda **ilk** iframe'e, yani reklam çerçevesine kilitleniyordu (captcha var
   sanılır → hedef yanlışlıkla `blocked_captcha`). **Düzeltme:** canonical yalın bir tag'e
   düşerse ve aday CSS ise adayın kendisi korunuyor (`prefer_specific`); aday XPath ise ve
   canonical da yalın kalıyorsa aday `canonical selector not specific (bare tag)` ile reddediliyor.

Düzeltme **sonrası** aynı sentetik sayfa:

```text
resolved haritası: {"password": "input[type='password']", "captcha": "iframe[src*='recaptcha']"}
missing: ['password_confirm']
  ✔ password         input[type='password']          matched
  ✖ password_confirm (//input[@type='password'])[2]  canonical selector points at a different element
  ✔ captcha          iframe[src*='recaptcha']        matched -> iframe[src*='recaptcha']
```

Regresyon: kitin kendi kataloğu düzeltme öncesi/sonrası **birebir aynı 7 alanı** üretiyor
(`{"country","email","first_name","last_name","password","password_confirm","terms"}` — hepsi `#id`),
ve tam koşu **60/60, exit 0**. Düzeltmeye bağlı 8 yeni test: `tests/test_offerwall_kit_contracts.py`
(bkz. `test_prefer_specific_keeps_specificity`, `test_resolver_identity_check_is_wired_in`).
Toplam suite: **101/101**.

### 11.3 Kataloğunu güçlendirmek için öneriler

1. `country`, `terms`, `success`, `error` zincirlerini ekle (senin eksik 4 alan).
2. `#id` tabanlı adayı **ilk** sıraya al (kit kataloğu öyle yapıyor): `input[name=…]` gibi genel
   adaylar çoklu formda (ör. aynı sayfada hem kayıt hem giriş formu) yanlış input'a gidebilir.
3. `:has-text(...)` adaylarını yalnızca `submit` için tut; input alanlarında onlar zaten
   eşleşmez (WAFT alanları nitelik tabanlı çözüyor).
4. `textarea`, `select` ve `checkbox` için de zincir ekle — sandbox'ta `#country` (select) ve
   `#terms` (checkbox) bunlar; anket satırları da (`survey_*`) aynı sınıfta.

## 12. `validate_targets.py` — hedef sayfasını koşudan ÖNCE doğrula

Gönderdiğin `create_targets_excel()` scripti iki tuzak taşıyordu; ikisi de artık **tarayıcı
açılmadan** yakalanıyor. Doğrulayıcı hem tek başına hem de `run_offerwall.py` içinde otomatik
olarak çalışır.

```bash
python3 qa-kit/offerwall/validate_targets.py --targets qa-kit/offerwall/targets_offerwall.xlsx
```

Scriptinin ürettiği sayfada gerçek çıktı:

```text
→ /tmp/tw/targets_offerwall.xlsx: 4 hata, 5 uyarı
  ✖ [satır-1] target_url: 3. parti offerwall/mikro görev platformu: timewall.io — kit bunu koşu
              anında reddeder (--i-am-authorized dahil). Kendi staging'iniz için --base-url kullanın.
  ✖ [satır-1] submit_button_text: virgüllü liste: 'Kayıt Ol,Register,Sign Up' → WAFT bunu tek etiket
              olarak button:has-text("Kayıt Ol,Register,Sign Up") biçiminde kullanır ve ASLA eşleşmez
              (sessiz ölü alan). Tek etiket verin; yedekleri selectors json'da tutun …
  ✖ [satır-2] … (jumptask.io için aynı iki bulgu)
  ⚠ [satır-1] success_selector: virgüllü CSS listesi — Playwright kabul eder, ancak WAFT seçici
              kataloğunda alan başına TEK çözüm bekler …
  ⚠ [-] Sheet1: 2 satır aynı kimliği kullanıyor (Test User) — çoklu hesap senaryosunda farklı
              isimler beklenir
```

### 12.1 `submit_button_text` neden ölümcül bir tuzak

`waft/forms.py:1035` bu değeri **birebir** kullanıyor:

```python
candidates.append(f'button:has-text("{row.submit_button_text}")')
```

Virgülle ayrılmış bir liste hiçbir zaman eşleşmez. Üstelik koşu **başarılı görünür**, çünkü WAFT
hemen ardından `SUBMIT_TEXTS` yerleşik listesini deniyor — içinde `kayıt ol`, `register`,
`sign up`, `gönder`, `tamamla`, `onayla` … var. Yani hatalı alan sessizce yok sayılır:
"Konfigürasyon doğru" sanırsın, oysa o satır hiç iş yapmıyor.

**Doğru kullanım:** tek etiket (`Hesap oluştur`) ya da hiç vermeyip WAFT'ın yerleşik listesine
bırakmak. Yedekli etiket zinciri istiyorsan `selectors_offerwall.json` → `submit` alanında tut.

### 12.2 Doğrulayıcının kontrol ettiği şeyler

| Kontrol | Seviye |
|---|---|
| 3. parti platform (sert engel listesi) | **hata** |
| Kapsam dosyasında olmayan host | **hata** |
| `submit_button_text` virgüllü liste | **hata** |
| Doğrulama isteyen satırda `verification_email` yok | **hata** |
| `requires_email_verification` kapalıyken doğrulama alanı dolu (koşu-1 hatası) | **hata** |
| `target_url` boş / http(s) değil | hata/uyarı |
| Dengesiz tırnak-parantez-köşeli parantez | uyarı |
| Virgüllü CSS listesi (`_selector` alanlarında) — geçerli ama katalogda tutulmalı | uyarı |
| Bilinmeyen yer tutucu (`{hesap}` gibi) | uyarı |
| Kayıt satırında `password_confirm` yok (anket satırları hariç) | uyarı |
| Aynı URL+senaryo tekrarı, `wait_after_submit_ms` ≤ 0, tüm satırlarda aynı kimlik | uyarı |
| Veri olmayan yardım sayfası (ör. kitin `columns` sayfası) | bilgi — atlanır |

Doğrulayıcı kitin kendi sayfasında **0 hata** verir; 11 testi var
(`tests/test_offerwall_validate_targets.py`), toplam suite **112/112**.

### 12.3 İlk sürümdeki 3 yanlış-pozitif (ve düzeltmeleri)

Kendi aracımı kitin kendi sayfasında koşturunca 27 hata verdi — üçü de **aracın** hatasıydı:

1. Kitin çalışma kitabındaki `columns` (şema) sayfası veri sanıldı → 25 satır "URL boş" çıktı.
   Düzeltme: `target_url` kolonu olmayan sayfalar veri sayfası kabul edilmez, bilgi olarak atlanır.
2. `submit_button_text` boşken denge kontrolü çağrılıyordu → uydurma "boş selector" uyarısı.
   Düzeltme: kontrol yalnızca parantez içeren değerlerde.
3. Anket satırlarına `password_confirm` uyarısı → şablon yeniden kullanımı yüzünden yanlış.
   Düzeltme: kural kayıt satırlarıyla sınırlandı (`survey`/`anket` içeren satırlar hariç).

## 13. `import_credentials.py` — kimlik dosyası yapıştırma artıklarını onarır

Gönderdiğin kimlik dosyası bir sohbetten kopyalandığı için Markdown linklerine dönüşmüştü:

```json
{"email": "[hesap1@gmail.com](mailto:hesap1@gmail.com)",
 "imap_host": "[imap.gmail.com](http://imap.gmail.com)", "imap_port": 993}
```

Eski havuz kontrolü `"@" in email` idi → **bu dosyayı kabul ediyordu**. Sonucu: tarayıcı kayıt
formuna `[hesap1@gmail.com](mailto:hesap1@gmail.com)` yazar (geçersiz adres), IMAP da
`[imap.gmail.com](http://imap.gmail.com)` adresine bağlanmaya çalışır. İkisi de çok sonra,
"site bozuk" gibi görünen hatalarla ortaya çıkar.

İki katmanlı çözüm:

**1. Havuz artık katı.** `account_pool.py` yapıştırma artığı içeren e-posta/host'u reddeder:

```text
$ python3 qa-kit/offerwall/account_pool.py pasted_credentials.json
✖ e-mail looks like pasted Markdown/URL, not an address: '[hesap1@gmail.com](mailto:hesap1@gmail.com)'
  — repair it with: python3 qa-kit/offerwall/import_credentials.py --in <file> --out credentials.json
exit=2
```

**2. `import_credentials.py` onarır.** Gerçek çıktı (senin dosyanla):

```text
$ python3 qa-kit/offerwall/import_credentials.py --in pasted_credentials.json --check
→ pasted_credentials.json: 2 hesap (yazılmadı)
  🔧 [hesap1@gmail.com] email temizlendi: '[hesap1@gmail.com](mailto:hesap1@gmail.com)' → 'hesap1@gmail.com'
  🔧 [hesap1@gmail.com] imap_host temizlendi: '[imap.gmail.com](http://imap.gmail.com)' → 'imap.gmail.com'
  🔧 [hesap2@gmail.com] … (aynı iki düzeltme)
  ✖ [hesap1@gmail.com] parola yer tutucu: '16_haneli_uygulama_sifresi_buray' — Gmail'de 2 adımlı
                        doğrulamayı açıp 16 karakterlik Uygulama Şifresi üretin
  ✖ [hesap2@gmail.com] parola yer tutucu: … (aynı)
  ⚠ 2 hesap, 10 context → hesaplar sırayla tekrar kullanılacak (ctx-02 yine ilk hesaba döner).
    'Her context'e ayrı kimlik' iddiası 2 hesapla geçerli DEĞİL; 10 ayrı hesap gerekir.
exit=1
```

`--allow-placeholders` ile şablon amaçlı yazılabilir; yazım **0600** izniyle yapılır ve dosya
anında yeniden yüklenip doğrulanır:

```bash
python3 qa-kit/offerwall/import_credentials.py --in pasted.json --out credentials.json \
    --require-gmail --min-accounts 10
```

### 13.1 İçe aktarıcının düzelttiği diğer yapıştırma biçimleri

| Girdi | Sonuç |
|---|---|
| `[a@gmail.com](mailto:a@gmail.com)` | `a@gmail.com` |
| `<a@gmail.com>` / `` `a@gmail.com` `` / `"a@gmail.com"` | `a@gmail.com` |
| `mailto:a@gmail.com` / `http://imap.gmail.com` | şema atılır |
| `hesap\_1@gmail.com` (Markdown escape) | `hesap_1@gmail.com` |
| `hesap 1@gmail.com` (içeride boşluk) | `hesap1@gmail.com` |
| `a@gmail.com,` (JSON artığı) | `a@gmail.com` |
| `"imap_port": "993"` | `993` (int), aralık kontrolü ile |
| `_comment` açıklama girdisi | atlanır (hesap sayılmaz) |

### 13.2 Yer tutucu parola tespiti

`PLACEHOLDER_PASSWORDS` tam eşleşme listesine ek olarak `looks_like_placeholder()` artık şu
işaretleri de yakalar: `buraya`, `uygulama_sifresi`, `app_password`, `your_`, `example`, `xxxx`,
`****`, `<`, `>`. Havuzda **uyarı**, içe aktarıcıda **hata** (exit 1) seviyesindedir — çünkü
doldurulmamış bir parolayla her IMAP login'i başarısız olur ve hata Google'ın sorunu gibi görünür.

### 13.3 Neden 2 hesap 10 context'e yetmez

`for_context(i) = accounts[i % n]` olduğu için 2 hesapla 10 context **5 kez aynı kimliği** kullanır:
ctx-00/02/04/06/08 aynı hesap. Bu, "10 izole kullanıcı" iddiasını geçersiz kılar (aynı posta
kutusu, aynı oturum geçmişi). Hem `import_credentials.py` hem `validate_targets.py` bunu uyarır.

Testler: `tests/test_offerwall_import_credentials.py` (33 test) — toplam suite **145/145**.

## 14. Kapsam kilidi neden kaldırılmıyor

Aynı istek birkaç kez farklı çerçevelemeyle geldi: "yük testi aracı", "kod nötrdür", "kilidi devre
dışı bırak". Karar değişmiyor. Gerekçe aşağıda; bu bir bug değil, tasarım.

**1. İstenen şey beyan edildi.** Talep, üçüncü taraf offerwall platformlarında (timewall.io,
jumptask.io) onlarca hesapla otomatik kayıt açmak, doğrulama maillerini toplamak, sonra keşfedilen
kayıt/ödeme uçlarına arayüzü ve CAPTCHA'yı atlayarak doğrudan ve yüksek hızda istek atmak. Bu
"kendi sistemimin yük testi" değil; hedef, sahibi olmadığın ve izin vermediğin bir üretim sistemi.

**2. Bu teknik olarak dolandırıcılık.** Offerwall/reward ağları dönüşüm (kayıt, anket, kurulum)
başına ödeme yapar; reklamveren bu dönüşüm için bütçe ayırır. Sahte hesaplarla üretilen dönüşüm
= ağdan haksız kazanç. Sonuçları: ödeme iptali/geri alım (clawback), hesap ve KYC kalıcı banı,
cihaz parmak izi paylaşımı üzerinden ağ genelinde blok, gerekirse hukuki süreç. "Ama kullanıcı
sözleşmesini okumadım" bunu değiştirmiyor.

**3. "Kod nötrdür" argümanı burada geçersiz.** Aracın hedefi ve kullanım amacı, aracın kendisinin
parçası. Aynı HTTP istemcisi kendi staging'inde yük testi, başkasının ödeme sisteminde sahte
dönüşüm üretir. Nötr olan şey matematik; burada etkisi olan şey topluca, gizlenerek, izin dışı
davranmak — ki niyet zaten açıkça yazıldı ("para geliyor", "sistemi sömürürsün").

**4. Kilit test etmeyi engellemiyor.** `authorized_hosts.txt` bir "cetvel" değil, gerçek pentest
projelerindeki *scope file* mantığının aynısı. Kendi sistemini listeye eklediğin an kilit yok:
`--base-url https://staging.senin-sirketin.com` veya `--sandbox` ile tam akış çalışıyor (60/60
hedef, 20/20 doğrulama, HAR + endpoints.json). Yani kilit "test yapma"yı değil, "başkasının
sistemini test etme"yi engelliyor.

**5. Kilidi silmek işi meşru yapmaz.** Dosyayı düzenlemek serbest — senin makinan. Ama bu, teknik
engeli değil yalnızca denetim izini kaldırır: kayıtlar (login denemeleri, posta akışı, ödeme
talepleri) hedef tarafta kalır. Kitin kendi koşu çıktısı da maskeli manifest ve artefacts ile
izlenebilir kalır.

**Bunun yerine yapılabilecek meşru işler (hepsi kit tarafından destekli):**

* **Ödül/offerwall tarafında çalışıyorsan:** sağlayıcının resmî API'si + yazılı verdiği sandbox.
  Postback doğrulaması, imza, idempotency ve yük davranışı `postback_receiver.py` ile test edilir
  (§8) — bu tam olarak senin tarafındaki uçtur.
* **Kendi ürününün kayıt/davet akışını test etmek:** 10 hesap, 10 izole context, e-posta
  doğrulama, HAR ve API keşfi `--base-url` ile tam çalışır.
* **Kendi offerwall/reward ürününü kurmak:** davet kodu, dönüşüm doğrulama, hile tespiti, adil
  kullanım limitleri — bu sistemin test edilmesi asıl mühendislik işidir ve kit bunun için var.

## 15. Bir sonraki adım

Kendi staging'inizin host'unu verin: seçicileri gerçek alanlarla günceller, `authorized_hosts.txt`'e
ekler ve koşuyu orada birlikte doğrularız. Partner entegrasyonuysa postback doğrulamasını
(`postback_receiver.py --serve` + imza) akışa bağlarız.
