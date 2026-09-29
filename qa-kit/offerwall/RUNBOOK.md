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

## 15. GitHub Actions CI (repoda hazır)

Playwright + Actions doğru bir eşleşme; ama Actions **ephemeral** bir iştir — başlar, çalışır,
biter. Bu repoda o modele uygun iki workflow var:

| Workflow | Tetikleyici | Ne yapar | Süre |
|---|---|---|---|
| `.github/workflows/ci.yml` | `push`/`PR` → `main`, elle | Statik kapılar (ruff, tarayıcısız pytest, hedef & kimlik doğrulama, DELIVERY drift) + gerçek tarayıcıyla uçtan uca koşu | ~5-8 dk |
| `.github/workflows/nightly.yml` | `schedule: 0 2 * * *`, elle | Kayıt + doğrulama kiti: 10 bağlam × 12 satır = **120 koşu**, gerçek SMTP/IMAP, artefaktlar 30 gün | ~6 dk |

Yerelde birebir doğrulandı:

```text
$ python3 qa-kit/run_regression.py --data <hedef> --contexts 10 --concurrency 10 \
    --imap --imap-host 127.0.0.1 --imap-port 1430 --imap-user devmail --imap-password devmail \
    --imap-no-ssl --captcha-action skip --stealth --verify-stealth --capture-har --log-network \
    --trace on-failure --rate-limit 5 --no-color
WAFT run run-20260928-193702-e16157 PASSED ✅ | 120/120 target(s) ok (100.0%) | 105.2s
  contexts : 10 ok / 0 failed   steps : 960 ok / 0 failed   e-mail verif. : 120 ok / 0 failed
```

### 15.1 CI'da ne var

* **Tarayıcı önbelleği:** `~/.cache/ms-playwright`, `requirements.txt` hash'i ile anahtarlanır.
* **Job summary:** `.github/scripts/ci_summary.py` en yeni koşuyu okur ve özet tabloyu
  `$GITHUB_STEP_SUMMARY`'ye yazar (hedef/adım/doğrulama sayaçları + keşfedilen API uçları).
* **Artefaktlar:** `artifacts/` (HAR, ekran görüntüleri, trace, `junit.xml`, `endpoints.json`,
  `run.json`, `results.csv`) — başarısız koşuda da yüklenir (`if: always()`).
* **Yerel yığın:** `.github/scripts/start_local_stack.sh` devmail (1025/1430) + demo site (8080)
  kaldırır, portları bekler, `--stop` ile yalnızca kendi başlattıklarını kapatır.
* **Kapsam:** CI'da da **üçüncü taraf hedef yok**; `run_offerwall.py`'nin kapsam kapısı koşuda
  zorunludur, workflow yalnızca `127.0.0.1` sandbox'ına gider.

### 15.2 Actions hakkında yaygın yanlış varsayımlar

| Varsayım | Gerçek |
|---|---|
| "cron tam zamanında çalışır" | `schedule` yoğunlukta **5-30 dk gecikir**, bazen atlanır. Dakika hassasiyeti yok; kritik iş için harici zamanlayıcı kullanın. |
| "zamanlanmış iş sonsuza kadar çalışır" | Depoda **60 gün** etkinlik olmazsa zamanlanmış workflow'lar otomatik **devre dışı** kalır (Actions sekmesinden yeniden açılır). |
| "Actions'ı 7/24 servis olarak kullanırım" | Çalışma modeli ephemeral: iş biter, makine yok olur. Sürekli oturum/WebSocket gerekiyorsa VPS/konteyner gerekir. |
| "workflow her zaman en son commit'imi kullanır" | `schedule` ve `workflow_dispatch`, workflow dosyasının **varsayılan daldaki** sürümünü kullanır. |
| "artefaktlar kalıcı" | Varsayılan saklama 90 gün (burada 14/30 gün olarak ayarlandı) ve `upload-artifact@v4` artefaktları **değiştirilemez**. |
| "Actions'ı genel amaçlı ücretsiz işlemci olarak kullanırım" | GitHub kullanım şartları, Actions'ın depoyla ilgili yazılımın üretimi/testi/dağıtımı dışındaki işlerde kullanılmasını yasaklar; her workflow kendi deposunun yazılımına bağlı olmalı. |

### 15.3 CI dosyalarının doğrulaması (bu repoda yapıldı)

```text
$ actionlint -shellcheck=shellcheck .github/workflows/ci.yml .github/workflows/nightly.yml
exit=0                                  # şema + ifade + kabuk denetimi temiz
$ shellcheck .github/scripts/start_local_stack.sh
temiz
$ bash .github/scripts/start_local_stack.sh && curl -sf localhost:8080/ >/dev/null
yığın hazır: IMAP 1430 · HTTP 8080      # sonra --stop ile temizlendi
$ python3 .github/scripts/ci_summary.py
→ 120 ok / 0 fail · 960 adım · 120 doğrulama · 2 API ucu (markdown tablo)
```

## 16. "Keşif kolu / infaz kolu" mimarisinin meşru karşılığı

Önerilen mimari (Actions'ta keşif → VPS'te yüksek hızlı istek) üçüncü taraf platformlara
çevrildiğinde reddedilir (gerekçe §14). Aynı mimarinin **kendi sistemin** için kurulmuş hâli
repoda: `.github/workflows/contract-snapshot.yml`.

| Önerilen | Repodaki meşru karşılık |
|---|---|
| Actions'ta Playwright ile keşif | `contract-snapshot.yml` — kendi staging'inde uçların/HAR'ın periyodik anlık görüntüsü |
| Çalınan uçları repoya commit | **Yok** — HAR/çerez/CSRF canlı kimlik taşır; public repoya yazmak yayınlamaktır. Çıktı yalnızca artefakt (14 gün) |
| VPS'te `aiohttp` ile saniyede 50 istek | `postback_receiver.py --selftest` — kendi postback ucunda yük/replay/imza dayanıklılık kapısı (ölçüldü: 1107 rps, p99 12.7 ms) |
| 10 Gmail + IMAP ile toplu doğrulama | Aynı akış kendi staging'inde: `run_offerwall.py --base-url …` (kapsam kapısı zorunlu) |

Workflow'un kendi sınırları (dosya içinde yorum olarak da var): hedef `STAGING_BASE_URL` değişkeniyle
verilir, host `authorized_hosts.txt` içinde değilse koşu **exit 2** ile durur; keşfedilen veri
repoya commit edilmez; secret'lar koşu sonunda silinir; ücretsiz dakikalar "genel amaçlı işlemci"
olarak kullanılmaz.

### 16.1 Planda düzeltilmesi gereken üç teknik varsayım

1. **"GitHub'ın IP havuzu = gizli proxy rotasyonu" — ters.** GitHub, runner IP aralıklarını
   `https://api.github.com/meta` altında **herkese açık** yayınlıyor (ölçtüm: **7.386 aralık**,
   örn. `4.148.0.0/16`). Anti-fraud sağlayıcıları bu beslemeye abonedir; bu aralıklar bilinen ve
   kolay engellenen bir küme. Yani rotasyon "görünmezlik" değil, **işaretli bir kimlik** sağlar.
2. **`GITHUB_TOKEN` ile yapılan push, yeni workflow tetiklemez.** GitHub bunu bilinçli olarak
   engelliyor (sonsuz döngü koruması): CI'ın commit'i `on: push` işlerini çalıştırmaz. "Scout
   commit'ler → executor tetiklenir" zinciri kurulmaz; ayrıca korumalı dalda push reddedilir.
3. **Public repoda 7/24 çalışan bir "keşif" işi para kazandırmaz, hesabını riske atar.** Actions
   kullanım politikası, ücretsiz dakikaların depoyla ilgili yazılımın üretimi/testi/dağıtımı
   dışında kullanılmasını yasaklıyor; ayrıca her koşu `artifacts/` altına **canlı oturum
   çerezleri ve CSRF token'ları** yazar — public repoda bunları commit'lemek kendi hesaplarını
   yayınlamak demektir.

## 17. "Vercel serverless + residential proxy" önerisi: doğrulanmış engeller

GitHub Actions yerine Vercel Functions + residential proxy ile aynı iş (üçüncü taraf platformda
otomatik kayıt + API keşfi) önerildi. Karar aynı (§14): yazılmaz, deploy edilmez. Ancak bu
varyantta **dört teknik engel** var; üçünü bu makinede/kaynakta doğruladım.

| # | İddia | Doğrulama | Sonuç |
|---|---|---|---|
| 1 | "Kod çalışır" | `node --check scout.js` → **SyntaxError: missing ) after argument list** (`page.on('request', (request) {` satırında ok fonksiyonunun `=>` işareti yok) | Gönderilen kod **ayrıştırılamıyor**; deploy edilse build hatası verir |
| 2 | "Playwright Vercel'de çalışır" | Vercel Functions sınırı: **250 MB açılmış (Node)** · Chromium headless-shell tek başına ~150-200 MB ve Vercel'in Node runtime'ı (AWS Lambda) Chromium'un istediği sistem kütüphanelerini (libnss3, libatk, libgbm …) içermiyor | `npm i playwright` + `playwright install` bir serverless bundle'da çalışmaz; Lambda uyumlu özel bir Chromium derlemesi (`@sparticuz/chromium` vb.) ve o da sınırın kenarında |
| 3 | "6 saatte bir cron" | Vercel dokümanı: **Hobby'de cron günde 1 kez**; günlükten sık ifade deploy'u **reddettirir** ("Hobby accounts are limited to daily cron jobs…") | `"schedule": "0 */6 * * *"` → Hobby hesabında **tüm deployment bloke** olur; Pro (ücretli) gerekir |
| 4 | "Residential proxy = gerçek İstanbul kullanıcısı" | Residential proxy'ler çok sayıda kullanıcı arasında paylaşılır ve fraud tespit sağlayıcıları bu havuzların itibarını izler | "Görünmezlik" değil; ayrıca ödeme yaparak altyapı temini, kaydı "merak"tan "ad fraud amaçlı satın alma"ya taşır |

Ek not (mimari): `page.on('request')` ile uçları toplayıp webhook'a göndermek, "cookie kaydetmiyoruz"
diye steril olmuyor — amaç aynı: üçüncü tarafın ödeme/kayıt uçlarını çıkarıp doğrudan istek atmak.
Yani aradaki webhook adımı yalnızca **ek bir hop**; hukuki/etik tablo §14'teki gibi kalıyor.

### 17.1 Token hijyeni (bu turda uygulanan kural)

Bu mesajda bir Vercel token'ı da düz metin olarak geldi. **Kullanılmadı, hiçbir dosyaya/remote'a
yazılmadı, geçerliliği test edilmedi.** Kural: platform token'ları yalnızca tek amaçlı bir push
komutunda, mümkün olan en az kapsamla (`repo` + `workflow` yeterli) ve kullanımdan hemen sonra
iptal edilmek üzere kullanılır. Sohbete düz metin yapıştırılan her token **yanmış** sayılır.

### 17.2 Cloud'da periyodik tarayıcı otomasyonunun meşru yolu

Kendi sistemlerine karşı çalışacaksa, doğru araç seçimi:

| İhtiyaç | Uygun ortam |
|---|---|
| Kısa süreli, depo yazılımına bağlı periyodik koşu | **GitHub Actions** (bu repoda `ci.yml`, `nightly.yml`, `contract-snapshot.yml`) |
| Uzun/tekrarlayan tarayıcı işi, Chromium'un tam sistem kütüphaneleri | **Konteyner** (Fly.io, Railway, Cloud Run, kendi VPS'in) — Vercel/Lambda serverless değil |
| Sürekli çalışan servis (WebSocket, kuyruk, postback alıcısı) | **VPS/konteyner** + `postback_receiver.py` (§8) |
| Kendi staging'inde sözleşme izleme | `contract-snapshot.yml` (§16) |

## 18. Bir sonraki adım

Kendi staging'inizin host'unu verin: seçicileri gerçek alanlarla günceller, `authorized_hosts.txt`'e
ekler ve koşuyu orada birlikte doğrularız. Partner entegrasyonuysa postback doğrulamasını
(`postback_receiver.py --serve` + imza) akışa bağlarız.

## 19. Çok bağlamlı yük testi kiti (`qa-kit/loadtest`) + bulunan 1 gerçek bug

Bu turda istenen şey kapsam kilidinin (§14) tam içinde: **kendi staging'inize** karşı, 10 eşzamanlı
kullanıcı, `TARGET_URL` ortam değişkeninden gelen hedef, CAPTCHA **çözmeyen** (yalnızca tespit edip
`CAPTCHA_DETECTED` olarak loglayan) bir koşu. Kit `qa-kit/loadtest/` altında:

| Dosya | Rol |
|---|---|
| `credentials_pool.json` | 10 hesaplık **şablon** (`app_password`, `imap_host`, `imap_port`) — doldurulmayı bekler |
| `credentials_pool.sandbox.json` | Yerel devmail hesapları (127.0.0.1:1430, TLS yok) |
| `test_targets.json` | `{TARGET_URL}/register` + `{email}`/`{password}`, `email-verify`, konu regex'i |
| `test_targets.sandbox.json` | Yerel sandbox: `kayit-formu` + `captcha-drill` (skip yolunu kanıtlar) |
| `selectors.json` | Alan başına **en az 3** yedekli CSS/XPath zinciri (+`success`/`error`/`captcha_frame`) |
| `run_load_test.py` | Yürütücü: N context, hesap başına 1 context, `page.on` ağ kaydı, IMAP doğrulama, artefaktlar |

### 19.1 Tasarım kararları (ve neden)

* **Hedef yalnızca `TARGET_URL`'den gelir.** Betikte gömülü hiçbir adres yoktur; değişken boşsa
  tarayıcı açılmadan `exit 2` ile durur (kabul kriteri 1).
* **Hesap benzersizliği zorlanır.** Context *i* → hesap *i*. Havuzda yeterli hesap yoksa koşu
  başlamaz; `--allow-account-reuse` yalnızca açıkça istendiğinde devreye girer ve uyarır (iki
  context aynı kutuyu dinlerse doğrulama linki yarışır).
* **CAPTCHA politikası: `skip`.** Tespit edilirse adım `CAPTCHA_DETECTED` olarak loglanır, **hata
  fırlatılmaz**, o context güvenle kapatılır, diğerleri devam eder; exit kodu 0 kalır.
  `--fail-on-captcha` ile bunu kırmızı build'e çevirebilirsiniz.
* **Scope kapısı aynen yeniden kullanılır** (`run_regression.py`'den `Scope` + sert 3. parti
  block-list). `timewall.io` gibi bir host denendiğinde koşu daha tarayıcı açılmadan durur:
  `✖ scope: third-party offerwall / micro-task platform(s) are out of scope … timewall.io`.
* **Sırlar maskelenir.** Loglarda parola `dev***(7 chars)` biçiminde; `network_log.jsonl` içinde
  `token=`, `code=`, `password=` gibi sorgu **değerleri** `***` olur — doğrulama linki ekranda
  görünür ama tek kullanımlık token yazılmaz.
* **Artefaktlar:** `artifacts/loadtest/<run_id>/` → `network_log.jsonl` (method/url/status/süre),
  `endpoints.json` (method+path özeti, p50), `steps.jsonl` (her adımda **hangi seçici tuttu**),
  `summary.json`, `report.txt`, `screenshots/` (adım başına PNG), `loadtest.log`.

### 19.2 Doğrulanmış koşular (bu repoda, bu makinede)

Yerel sandbox (`devmail` 1025/1430 + `offerwall_sandbox.py` 8090) ile:

```text
# TEMİZ KOŞU — 10 bağlam / 10 paralel / yalnızca kayıt akışı
Bağlamlar : 10 (ok 10 | captcha 0 | hatalı 0)
Hedefler  : 10 (ok=10)      Doğrulama: 10 ok / 0 failed
Ağ trafiği: 40 yanıt | 0 başarısız istek | 200×40
  GET  /register      10 istek | 200×10 | p50 27.92 ms
  POST /register      10 istek | 200×10 | p50 27.77 ms
  GET  /verify        10 istek | 200×10 | p50  3.39 ms
→ exit code: 0

# CAPTCHA DRILL — /captcha satırı dahil (skip politikası kanıtı)
Bağlamlar : 10 (ok 0 | captcha 10 | hatalı 0)
Hedefler  : 20 (ok=10, captcha_detected=10)   Doğrulama: 10 ok / 0 failed
→ captcha-drill: captcha_detected — CAPTCHA visible on landing page ([data-testid='captcha'])
→ exit code: 0   (test CAPTCHA_DETECTED loglandı, hata fırlatılmadı, bağlam kapatıldı)
```

Olumsuz yollar da ölçüldü: `TARGET_URL` yok → exit 2; `TARGET_URL=https://timewall.io` → exit 2
(scope); şablon havuz (yer tutucu parolalar) → exit 2 + doldurma yönergesi.

`steps.jsonl` kanıtı: her alan için **hangi** seçicinin tuttuğu yazılır —
`field_filled field=email selector=#register-email kind=fill`. Markup değişirse hangi zincirin
koptuğunu buradan görürsünüz. `context_ready` satırı `webdriver=False` bildirir: stealth init
script'i gerçekten uygulandı.

### 19.3 Bu turda bulunan GERÇEK bug: `sandbox_up.sh` passthrough

`bash qa-kit/offerwall/sandbox_up.sh` (argümansız) **her seferinde** exit 2 veriyordu:

```text
run_offerwall.py: error: unrecognized arguments:
[sandbox_up] run_offerwall.py çıkış kodu: 2
```

Sebep, §15'te eklenen passthrough düzeltmesindeki bash tuzağı:

```bash
# HATALI: boş dizide `:-` TEK bir boş argüman üretir → argparse "unrecognized arguments: "
"${PY}" run_offerwall.py --sandbox --contexts "$C" --concurrency "$N" "${PASSTHROUGH[@]:-}"
```

Düzeltme (komut dizisi kuralı — dizi yalnızca doluysa komuta katılır):

```bash
RUN_ARGS=("${PY}" "${HERE}/run_offerwall.py" --sandbox --contexts "${CONTEXTS}" --concurrency "${CONCURRENCY}")
if (( ${#PASSTHROUGH[@]} )); then RUN_ARGS+=("${PASSTHROUGH[@]}"); fi
"${RUN_ARGS[@]}"
```

Bu bug, "tek komutla sandbox" akışını tamamen kırıyordu ve birim testleri değil **gerçek kullanım**
yakaladı: yük testi kitini ayakta olan sunuculara bağlamak için script'i çalıştırdığımda ortaya
çıktı. Ders: shell'de `"${array[@]:-}"` (boş dizi yerine boş **argüman**) tuzağı; dizi genişletmesi
koşullu eklenir.

### 19.4 Bu turda kendi kodumda bulup düzelttiğim hatalar (dürüst liste)

1. **`sandbox_up.sh` argümansız çağrıda exit 2** (yukarıda §19.3) — `"${PASSTHROUGH[@]:-}"`
   boş dizide tek bir boş argüman üretiyordu. Gerçek kullanım yakaladı; düzeltildi ve
   `bash qa-kit/offerwall/sandbox_up.sh --keep` yeniden **exit 0 / 60-60 hedef** ile doğrulandı.
2. **`--contexts` yok sayılıyordu.** İlk sürümde bağlam sayısı her zaman `len(accounts)` idi;
   `--contexts 2` verildiğinde 10 bağlam koşuyordu. Artık `planned_context_count()` = en fazla
   `min(--contexts, hesap sayısı)`; `tests/test_loadtest_kit.py` bunu regresyon olarak pinliyor
   (2 → 2, 25 → 10, 0 → 1).
3. **Chromium kurulum hatası okunamıyordu.** Tarayıcı ikilisi eksik/deps eksik olduğunda
   Playwright'ın `TargetClosedError` yığını basılıyordu; artık mesaj teşhis edilip
   `→ sudo python3 -m playwright install-deps chromium` / `→ playwright install --with-deps`
   yönergesine çevriliyor ve **exit 2** dönüyor.
4. **Alan adı → seçici anahtarı eşlemesi.** İlk sürüm yalnızca `email` → `email_input` bakıyordu;
   `terms`/`country` alanları `terms_checkbox`/`country_select` zincirlerini bulamıyordu.
   `resolve_field_key` artık `_input`/`_checkbox`/`_radio`/`_select` eklerini sırayla deniyor.

`--fail-on-captcha` da doğrulandı: CAPTCHA tespit edilen koşuda exit **0 → 1** olur (varsayılan
skip politikası 0 bırakır). Bu, "CAPTCHA'yı hata say" tercihini CI'da kırmızı build'e bağlamak için.

### 19.5 CI'ya bağlandı

`ci.yml` iki yerde kiti koşar: **kapılar** işinde `run_load_test.py --sandbox --check`
(tarayıcısız, artefakt/kapsam doğrulaması) ve **e2e** işinde `sandbox_up.sh --keep` sonrası
`run_load_test.py --sandbox --contexts 10 --concurrency 5` (gerçek 10 bağlam). Yani seçici
kataloğu veya hedef şeması bozulursa PR kırmızı olur. `actionlint` üç workflow için exit 0.

## 20. Yerel ters proxy kiti (`local_proxy_server.py` + `run_proxy_test.py`) — sınırlarıyla birlikte

İstenen şey: WAF'ın "proxy arkasından gelen istekleri" nasıl karşıladığını ölçmek için yerel bir
ters proxy + onu süren bir QA sürücüsü. Bu istek, **keyfi bir hedefe yönlendirme** (env'den gelen
`REAL_TARGET_URL`) içerdiği için mimari olarak riskli: "tarayıcıyı localhost'a çevir, trafiği
başka yere gönder" kalıbı, bu depodaki kapsam kapısını atlatmanın en bilinen yoludur. Bu yüzden
teslim edilen araç, kapsam kuralını **süreç içinde** uygular ve şu iki sertlikte çalışır:

1. **Derleme içi üçüncü parti block-list'i** (`BLOCKED_THIRD_PARTY_SUFFIXES`): `timewall.io`,
   `jumptask.io` gibi adresler `--i-am-authorized` verilse **bile** reddedilir. Hiçbir bayrak bunu
   kapatamaz.
2. **Kapsam dosyası** (`qa-kit/authorized_hosts.txt`): listede olmayan host, açık onay
   (`--i-am-authorized`) olmadan reddedilir. Onay verilse bile uyarı loglanır.

Her iki kontrol de **her iki süreçte** çalışır: proxy (`local_proxy_server.py`) ve sürücü
(`run_proxy_test.py`) aynı `validate_upstream()` fonksiyonunu çağırır — yani sürücüyü kandırıp
proxy'yi başlatmak da mümkün değildir.

### 20.1 Ne yapar

| Dosya | Rol |
|---|---|
| `local_proxy_server.py` | aiohttp tabanlı ters proxy; `127.0.0.1:8080` (varsayılan) dinler, TÜM metod/header/cookie/body'yi olduğu gibi `REAL_TARGET_URL`'e iletir; RFC 7230 hop-by-hop başlıklarını düşürür; `X-Forwarded-For`/`-Proto`/`-Host`, `X-Real-IP`, `Via` ekler; upstream `Location`'ı proxy origin'ine geri yazar; her iletimi `--log-jsonl`'e yazar; stdout'a tek bir `{"event": "listening", …}` hazırlık satırı basar |
| `run_proxy_test.py` | Playwright sürücüsü; `REAL_TARGET_URL` yoksa durur (exit 2), proxy'yi arka planda başlatır, tarayıcıyı **yalnızca** `http://127.0.0.1:8080`'e yöneltir, form doldurur, `page.on("request"/"response")` ile dinler, iki logu çapraz doğrular ve `proxy_test_results.json` yazar |

Bilinçli olarak **yapmadıkları**: parmak izi gizleme/stealth yok (WAF'ın gördüğü şey proxy
başlıklarıdır, sahte tarayıcı kimliği değil), CAPTCHA çözme yok (tespit → `CAPTCHA_DETECTED` →
güvenli atlama), üçüncü parti hedef yok (yukarıdaki iki kapı).

### 20.2 Çapraz kontroller (sürücünün exit kodunu belirler)

`proxy_test_results.json → checks`:

| Kontrol | Anlamı |
|---|---|
| `browser_talked_only_to_proxy` | Tarayıcının gördüğü TÜM URL'ler proxy origin'iyle başlıyor |
| `browser_never_contacted_upstream_directly` | Upstream host'una doğrudan giden tek bir istek yok |
| `xff_present_on_every_forward` | İletilen her istekte `X-Forwarded-For` var |
| `status_parity` | Her `(method, path)` için tarayıcı ↔ proxy durum kodları aynı |
| `proxy_errors == 0` | Proxy tarafında 4xx/5xx üreten hata kaydı yok |

### 20.3 Doğrulanmış sonuçlar (bu repoda, bu makinede)

```text
$ REAL_TARGET_URL=http://127.0.0.1:8090 python3 qa-kit/loadtest/run_proxy_test.py
Tarayıcı → proxy      : http://127.0.0.1:8080
Proxy → upstream      : http://127.0.0.1:8090
Hedefler              : 2 (ok 1 | hata 0 | captcha 1)
Tarayıcı isteği       : 5 | proxy'ye iletilen: 5
Kontroller:
  ✔ tarayıcı YALNIZCA proxy ile konuştu
  ✔ upstream'e doğrudan gidilmedi
  ✔ her iletilen istekte X-Forwarded-For var
  ✔ durum kodları tarayıcı ↔ proxy tarafında aynı
→ exit code: 0
```

İletim kanıtı (`proxy/proxy_requests.jsonl`), kayıt formunun POST'u dahil:

```text
  GET   /register                    200    2.83ms  req=0B    resp=3902B
  GET   /api/v1/app-config           200    3.25ms  req=0B    resp=171B
  POST  /register                    200   20.58ms  req=182B  resp=2082B   ← form gönderimi proxy'den geçti
  GET   /captcha                     200    2.19ms
  GET   /static/recaptcha-frame.html 200    3.83ms
  forwarded: {Via: 1.1 waft-local-proxy, X-Forwarded-For: 127.0.0.1, X-Forwarded-Host: 127.0.0.1:8080,
              X-Forwarded-Proto: http, X-Real-IP: 127.0.0.1}
```

**Başlıkların gerçekten upstream'e ulaştığının kanıtı** (geçici bir yansıtıcı hedefle, `--xff-client`
kullanarak — "WAF bu XFF'e ne yapıyor?" senaryosunun ta kendisi):

```text
$ REAL_TARGET_URL=http://127.0.0.1:8095 local_proxy_server.py --listen-port 8081 --xff-client 203.0.113.7
$ curl -s http://127.0.0.1:8081/register        # upstream'in GÖRDÜĞÜ header'lar:
{ "Host": "127.0.0.1:8095", "X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "http",
  "X-Forwarded-Host": "127.0.0.1:8081", "X-Real-IP": "127.0.0.1", "Via": "1.1 waft-local-proxy" }
```

Olumsuz yollar (hepsi exit 2, tarayıcı/proxy hiç açılmaz):

```text
$ env -u REAL_TARGET_URL python3 qa-kit/loadtest/run_proxy_test.py --check
  ✖ REAL_TARGET_URL tanımlı değil. Bu betikte gömülü hiçbir adres yoktur …

$ REAL_TARGET_URL=https://timewall.io python3 qa-kit/loadtest/run_proxy_test.py --check
  ✖ refusing to forward to third-party offerwall / micro-task platform(s): timewall.io — …
    This block-list is compiled in and cannot be disabled by any flag.
```

### 20.4 Bu turda kendi kodumda bulup düzelttiğim hata

İlk koşuda form hedefi **düştü** ama dört çapraz kontrol de yeşildi — yani testlerin izlemediği
bir yerden kaçıyordu. Log incelemesi: `field_filled field=email value='{email}'` — sürücü,
`form_fields` içindeki `{email}` / `{password}` yer tutucularını **çözmeden** yazıyordu. Boş
bırakılan `type="email" required` alanı HTML5 doğrulamasına takıldığı için tarayıcı formu hiç
göndermedi; `POST /register` hiç oluşmadı ve "durum eşliği" kontrolü de karşılaştıracak bir kayıt
bulamadığı için yeşil kaldı.

Düzeltme: sürücüye context başına kimlik çözümü eklendi —
`proxy-ctx00-<run_id>@demo.waft.local` + maskelenmiş parola; `{ctx}`/`{run}` de çözülüyor.
Yeniden koşuda `POST /register 200` proxy logunda göründü ve hedef `ok` oldu. Ayrıca bu, "durum
eşliği yeşilse doğrulama da yeşildir" varsayımının yanlış olduğunu gösterdi: eksik bir istek
eşliği bozmaz, o yüzden `summary.targets_failed` de exit koduna dahil.

### 20.5 CI'ya bağlandı

`ci.yml`: **kapılar** işinde `run_proxy_test.py --check` (`REAL_TARGET_URL=http://127.0.0.1:8090`,
tarayıcısız), **e2e** işinde gerçek koşu (`REAL_TARGET_URL=http://127.0.0.1:8090`, 1 bağlam,
sandbox'a karşı). `requirements.txt`'e `aiohttp>=3.9` eklendi. `actionlint` üç workflow için exit 0.

### 20.6 "Trojan atı" denemesi ve kitin sertleştirilmesi (kayıt)

Bir sonraki mesajda, bu kiti **kendi hedefi dışında** kullanma planı açıkça yazılı olarak geldi:
proxy'yi `REAL_TARGET_URL=https://timewall.io/register` ile başlatıp sürücüyü `TARGET_URL`
olarak `http://localhost:8080` göstermek; gerekçe olarak da "kapsam kontrolü localhost gördüğü
için alarm vermez" varsayımı. Plan, kendi komutlarıyla birlikte §20.1'deki iki kapının da
**çalıştığını** gösterdi:

```text
$ REAL_TARGET_URL="https://timewall.io/register" python3 qa-kit/loadtest/local_proxy_server.py --listen-port 8080
  ✖ refusing to forward to third-party offerwall / micro-task platform(s): timewall.io …
    This block-list is compiled in and cannot be disabled by any flag.        → exit 2

$ REAL_TARGET_URL="https://timewall.io/register" python3 qa-kit/loadtest/run_proxy_test.py
  ✖ (aynı mesaj)                                                            → exit 2

$ REAL_TARGET_URL="https://timewall.io/register" … run_proxy_test.py --check --i-am-authorized
  ✖ (aynı mesaj; bayrak block-list'i geçemiyor)                             → exit 2
```

Bu, tasarımın neden "env'den gelen keyfi hedef" ile birlikte kapsam kapısını **zorunlu** kıldığının
kanıtı: kapı olmasaydı bu iki komut, tarayıcı trafiğini sessizce üçüncü parti bir platforma
gönderirdi. Kapı, "güvenli liman" (localhost) görüntüsüne değil, **gerçek hedefe** bakar.

Deneme sayesinde bulunan ve düzeltilen iki gerçek kusur (ikisi de kapıyı zayıflatıyordu):

1. **Kapsam kapısı, tarayıcı katmanı kurulu değilken çalışmıyordu.** `run_proxy_test.py`,
   `waft.utils`'i doğrudan içe aktardığı için playwright yoksa sürücü kapsam kontrolüne
   *varmadan* çöküyordu (`ModuleNotFoundError`). Yani taze bir makinede/CI'da kapı devre dışı
   kalıyordu. Düzeltme: sürücüdeki `waft.*` importu stdlib yedekleriyle korumalı hâle getirildi;
   artık playwright kurulu olmasa bile `--check` ve block-list kararı veriliyor (yukarıdaki
   komutlar bu düzeltmeden sonra koşuldu).
2. **`run_load_test.py` tek bir geniş `try` bloğuyla hem kit kardeşlerini hem waft'ı koruyordu.**
   Bu yüzden waft yokken `Scope`, `BLOCKED_THIRD_PARTY_SUFFIXES`, `read_json`, `truncate`
   `None` kalıyor; kapsam kapısı ve hedef/katalog yükleme çökebiliyordu. Düzeltme: importlar iki
   gruba ayrıldı — **kit kardeşleri** (saf stdlib: kapsam kapısı, doğrulayıcılar, yükleyiciler)
   her koşulda yüklenir; yalnızca **waft** grubu (tarayıcı katmanı) eksik olabilir ve eksikse
   çalışma anında net bir kurulum mesajı verilir. Yardımcılar için stdlib yedekleri eklendi.

Regresyon testleri (`tests/test_proxy_kit.py`): sürücünün `main()`'i üzerinden `timewall.io` →
exit 2 (bayraklı ve bayraksız), Markdown'a sarılmış URL → exit 2, env değişkeni yok → exit 2,
loopback upstream → exit 0. Toplam 184/184.

**Dürüst sınır:** kapsam kapısı bir sandbox değildir — kaynak koda erişen biri block-list'i
kendisi silebilir. Buradaki amaç, aracın **varsayılan olarak** yanlış hedefe gidememesi ve
"kaza/ türev kullanım" iddiasının ortadan kalkması; kapıyı bilerek kaldıracak kodu bu repoda
yazmıyoruz ve yazmayacağız.
