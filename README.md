# WAFT — Web Automation & Form-Test Framework

> **Playwright tabanlı, çok bağlamlı (multi-context) web otomasyon ve form doldurma test
> çerçevesi.** Tek tarayıcı motoru altında **10+ izole browser context** çalıştırır; her
> bağlama kendi **proxy'sini, user-agent'ını, saat dilimini, konumunu ve parmak izini** atar;
> Excel/JSON'dan okuduğu form verilerini doldurup gönderir; tüm ağ trafiğini loglar ve
> gerektiğinde **IMAP** ile "hesap doğrulama" e-postasındaki linki yakalayıp akışı tamamlar.

```text
┌────────────┐   ┌──────────────────────────────────────────────────────────────────┐
│  Excel /   │   │                    WAFT Orchestrator                             │
│  JSON      │──▶│  planlama · kaynak yönetimi · sinyaller · raporlama              │
│  veri      │   └───────┬───────────────┬───────────────┬───────────────┬──────────┘
└────────────┘           │               │               │               │
┌────────────┐   ┌───────▼──────┐ ┌──────▼───────┐ ┌─────▼────────┐ ┌────▼─────────┐
│ proxies.txt│──▶│ ctx-01       │ │ ctx-02       │ │ ctx-03       │ │ ctx-N        │
│ .env       │   │ proxy+UA+TZ  │ │ proxy+UA+TZ  │ │ proxy+UA+TZ  │ │ proxy+UA+TZ  │
└────────────┘   │ stealth      │ │ stealth      │ │ stealth      │ │ stealth      │
                 │ cookies/localStorage izole       │               │               │
                 └───────┬──────┘ └──────┬───────┘ └─────┬────────┘ └────┬─────────┘
                         │  form doldur · submit · outcome · IMAP · ekran görüntüsü · trace
                 ┌───────▼──────────────▼───────────────▼───────────────▼─────────┐
                 │               artifacts/run-…/  (run.json · results.csv ·      │
                 │               junit.xml · summary.md · screenshots · traces)   │
                 └────────────────────────────────────────────────────────────────┘
```

---

## İçindekiler

1. [Gereksinim eşleme tablosu](#1-gereksinim-eşleme-tablosu)
2. [Kurulum](#2-kurulum)
3. [Hızlı başlangıç (60 saniye)](#3-hızlı-başlangıç-60-saniye)
4. [Komut satırı referansı](#4-komut-satırı-referansı)
5. [Veri kaynağı şeması (Excel / JSON / CSV)](#5-veri-kaynağı-şeması-excel--json--csv)
6. [Proxy yönetimi](#6-proxy-yönetimi)
7. [Stealth / anti-detection](#7-stealth--anti-detection)
8. [Form doldurma motoru](#8-form-doldurma-motoru)
9. [Ağ trafiği dinleme & API keşfi](#9-ağ-trafiği-dinleme--api-keşfi)
10. [IMAP ile e-posta doğrulama](#10-imap-ile-e-posta-doğrulama)
11. [Artefaktlar ve raporlar](#11-artefaktlar-ve-raporlar)
12. [Programatik kullanım](#12-programatik-kullanım)
13. [Mimari / modül haritası](#13-mimari--modül-haritası)
14. [Testler ve kalite](#14-testler-ve-kalite)
15. [Ölçekleme, performans ve sınırlar](#15-ölçekleme-performans-ve-sınırlar)
16. [Sorun giderme](#16-sorun-giderme)
17. [Yasal uyarı](#17-yasal-uyarı)

---

## 1. Gereksinim eşleme tablosu

| # | İstenen | Nerede / nasıl |
|---|---------|----------------|
| 1 | **Çoklu bağlam yönetimi** (≥10 izole context, ayrı cookie/localStorage/session) | `waft/orchestrator.py` (`_run_context`, `Semaphore` ile eşzamanlılık), `waft/engine.py` (`ContextEngine.start`) — her context `browser.new_context()` ile ayrı cookie jar + storage alır. Doğrulanmış çalıştırma: **10 context × 14 hedef = 140/140 başarılı**. |
| 2 | **Stealth & anti-detection** | `waft/stealth.py` — `playwright-stealth` (varsa) + WAFT'un kendi init script'i: `navigator.webdriver` silme, canvas/WebGL/Audio parmak izi, `chrome.*` nesnesi, WebRTC sızıntı engeli, CDP/automation izi temizliği, `Function.prototype.toString` native kamuflajı. |
| 3 | **Proxy rotasyonu** | `waft/proxy_manager.py` — `proxies.txt` / `.env` / satır bazlı `proxy` kolonu; `http`, `https`, `socks5(5h)`, `socks4`; least-used veya round-robin rotasyon; health-check + cooldown + hata sonrası otomatik proxy değiştirme. |
| 4 | **Form otomasyonu** | `waft/forms.py` — DOM taraması, alias/skor tabanlı alan eşleştirme (Türkçe + İngilizce), checkbox/select/file desteği, çok adımlı formlar, submit ve sonuç tespiti. Veri: Excel/JSON (`waft/data_source.py`). |
| 5 | **Ağ trafiği dinleme** | `waft/network_monitor.py` — `page.on("request"/"response"/"requestfailed"/"console"/"pageerror"/"dialog")`; terminale tek satır log + `network.jsonl` + API endpoint keşfi (`endpoints.json`). |
| 6 | **IMAP entegrasyonu** | `waft/imap_client.py` — doğrulama e-postasını bekler, linki/OTP kodunu çıkarır; `waft/engine.py` `_step_email_verification` linki açar veya kodu girer. |
| 7 | **Excel veri kaynağı (pandas)** | `waft/data_source.py` — `pd.read_excel`, çoklu sayfa, `|` ile çok değerli hücreler, eksik/boş hücre toleransı, ikinci sayfada kolon dokümantasyonu. |
| 8 | **Hata yönetimi & snapshot** | `waft/artifacts.py` — her adımda ekran görüntüsü, `on-failure` Playwright trace `.zip`, HTML dökümü, `.zip` başarısızlık paketi (`manifest.json` + tüm kanıtlar), disk bütçesi koruması. |
| 9 | **Asenkron yapı** | Tüm çalıştırma `asyncio`: context'ler `asyncio.gather` + `Semaphore`, IMAP senkron kısmı `asyncio.to_thread`, iptal/sinyal yönetimi `asyncio` ile. |
| 10 | **Komut satırı arayüzü** | `waft/cli.py` + `waft/config.py` (`argparse`): `--data`, `--proxy-file`, `--contexts`, `--concurrency`, `--iterations`, `--scenario`, `--imap-*`, `--trace`, `--dry-run`, `--print-config` … (tam liste: `python -m waft --help`). |

---

## 2. Kurulum

```bash
# 1) Python bağımlılıkları
python -m pip install -r requirements.txt
#    (veya: pip install -e ".[stealth]")

# 2) Tarayıcı + sistem kütüphaneleri (Docker/CI için --with-deps şart)
python -m playwright install --with-deps chromium
#    firefox/webkit için:  python -m playwright install --with-deps firefox webkit
```

`requirements.txt` içeriği: `playwright`, `python-dotenv`, `pandas`, `openpyxl`, `xlrd`,
`rich`, `playwright-stealth`. Hiçbiri zorunlu değildir: eksik olan opsiyonel paketler
(`rich`, `playwright-stealth`, `python-dotenv`, `pandas`) çalışma anında uyarı verir ve
framework built-in yedek yollarını kullanır.

Kontrol:

```bash
python -m waft --version
python -m waft --help
python -m pytest tests/ -q -m "not integration"    # tarayıcı gerektirmeyen 70 test
```

---

## 3. Hızlı başlangıç (60 saniye)

Örnek veri dosyalarını üret ve demo siteyi başlat:

```bash
python examples/make_sample_data.py          # examples/data/*.xlsx|.json + proxies.txt + .env.example
python examples/demo_site.py --port 8080 &   # gerçekçi formlar, sihirbaz, XHR, doğrulama sayfası
```

### En temel çalıştırma — 10 izole bağlam, Excel verisi

```bash
python -m waft \
  --data examples/data/targets.xlsx \
  --contexts 10 \
  --concurrency 10 \
  --proxy-mode off \
  --headless
```

Çıktının sonunda:

```text
WAFT run run-20260928-145831-f295f9 PASSED ✅ | 140/140 target(s) ok (100.0%) | 308.9s
  contexts          : 10 ok / 0 failed (of 10)
  targets           : 140 run, 140 ok, 0 failed, 100.0% success
  steps             : 816 ok / 0 failed
  artifacts         : artifacts/run-20260928-145831-f295f9
```

### Proxy'li, tam donanımlı çalıştırma

```bash
python -m waft \
  --data examples/data/targets.xlsx \
  --proxy-file proxies.txt \
  --contexts 12 --concurrency 6 \
  --devices "chrome-win-1920x1080,android-pixel7,chrome-mac-1440x900" \
  --locales tr-TR,en-US \
  --timezone Europe/Istanbul \
  --stealth --verify-stealth \
  --log-network --log-headers \
  --trace on-failure --capture-har \
  --retries 2 --rate-limit 1.5 \
  --artifacts artifacts
```

### Diğer hazır komutlar

```bash
make demo          # 10 context'lik Excel demosu
make demo-json     # sihirbaz adımları + load test + API keşfi + CAPTCHA tespiti
make dry-run       # tarayıcı açmadan planı göster
make test-all      # birim + gerçek tarayıcı testleri
```

---

## 4. Komut satırı referansı

Tam liste: `python -m waft --help`. Öne çıkanlar:

| Amaç | Parametreler |
|------|--------------|
| **Veri** | `--data FILE` (`-d`), `--sheet SHEET`, `--url-column COL`, `--urls u1,u2` |
| **Çalıştırma şekli** | `--contexts N` (`-c`), `--concurrency N` (`-j`), `--iterations N` (`-i`), `--max-targets-per-context N`, `--shuffle --seed 42`, `--rate-limit 2.0`, `--scenario auto\|load-test\|smoke\|form-submit\|email-verify\|api-discovery\|full-journey`, `--dry-run` |
| **Tarayıcı** | `--browser chromium\|firefox\|webkit`, `--headful/--headed`, `--slow-mo MS`, `--timeout MS`, `--navigation-timeout MS`, `--outcome-timeout MS`, `--no-humanize`, `--typing-budget MS`, `--block-resource-types image,media,font`, `--block-hosts analytics.example.com`, `--browser-arg "--flag"` |
| **Kimlik** | `--devices "random"` veya liste, `--locale tr-TR,en-US`, `--timezone TR`, `--geolocation 41.0,28.9`, `--user-agents FILE`, `--header "X-Tenant: acme"` |
| **Stealth** | `--stealth/--no-stealth`, `--no-canvas-noise`, `--no-webgl-spoof`, `--no-audio-noise`, `--allow-webrtc`, `--verify-stealth` |
| **Proxy** | `--proxy-file FILE` (`-p`), `--proxy-mode off\|auto\|require`, `--proxy-cycle/--no-proxy-cycle`, `--no-proxy-health-check`, `--proxy-health-url URL`, `--proxy-cooldown SN`, `--proxy-fail-closed` |
| **Ağ** | `--log-network/--no-network-log`, `--log-headers`, `--log-bodies`, `--ignore-network-host HOST`, `--capture-har` |
| **Form** | `--selectors selectors.json`, `--steps steps.json`, `--no-submit`, `--field-match-threshold 45`, `--captcha-action error\|skip\|continue` |
| **IMAP** | `--imap`, `--no-imap`, `--imap-host H`, `--imap-port 993`, `--imap-user U`, `--imap-password P`, `--imap-mailbox INBOX`, `--imap-timeout 120`, `--imap-link-regex RE`, `--imap-otp-regex RE`, `--imap-subject-regex RE`, `--imap-sender ADRES` |
| **Çıktı** | `--artifacts DIR` (`-o`), `--log-file F`, `--log-level LEVEL`, `--trace on\|off\|on-failure\|retain-on-failure`, `--no-screenshots`, `--save-storage-state`, `--bundle`, `--no-redact`, `--quiet`, `--no-color` |
| **Çalışma davranışı** | `--retries N`, `--retry-backoff SN`, `--fail-fast`, `--print-config`, `-v/-vv`, `--debug` |

**Öncelik sırası:** CLI > `.env`/ortam değişkenleri > `--profile` JSON > varsayılanlar.
Her ayar `WAFT_<ALAN>` ortam değişkeniyle de verilebilir (ör. `WAFT_CONTEXTS=10`,
`WAFT_RETRIES=2`). Çıkış kodları: `0` başarı · `1` başarısız hedef · `2` yapılandırma hatası ·
`3` proxy hatası · `130` Ctrl+C.

---

## 5. Veri kaynağı şeması (Excel / JSON / CSV)

`examples/make_sample_data.py` iki örnek üretir: **`targets.xlsx`** (Excel akışı) ve
**`targets.json`** (sihirbaz adımları, load test, API keşfi, CAPTCHA senaryoları).
Kolon adları büyük/küçük harf ve Türkçe karakterden bağımsızdır (`Hedef URL`, `E-Posta`,
`Şifre` çalışır; `hedef_url`, `e_posta`, `sifre`'ye normalize edilir).

| Kolon | Zorunlu | Açıklama |
|---|---|---|
| `target_url` (alias: `url`, `hedef_url`, `link`) | ✅ | Formun/ULAŞILACAK sayfanın URL'i |
| `name` | – | Satır adı (raporlarda görünür) |
| `scenario` | – | `form-submit`, `load-test`, `smoke`, `api-discovery`, `email-verify` |
| **Form alanları** | – | `email`, `first_name`, `last_name`, `phone`, `company`, `city`, `password`, `password_confirm`, `birthdate`, `website`, `message`, `terms`, `newsletter`, `remember_me`, `otp` … (tanınmayan anahtarlar da deneyerek eşleştirilir) |
| `submit_button_text` / `submit_selector` | – | Gönder düğmesini bulma ipucu |
| `success_selector` / `success_url_regex` / `success_text` | – | Başarı tespiti |
| `error_selector` / `failure_text` | – | Hata tespiti |
| `wait_after_submit_ms` | – | Sonuç bekleme süresi (ms) |
| `expect_error` (alias `beklenen_hata`) | – | **Negatif test**: formun reddedilmesi beklenir → reddedilirse test **geçer** |
| `requires_email_verification` + `verification_email` + `verification_*` | – | IMAP doğrulama akışı (regex, gönderen, OTP alanı, timeout) |
| `steps` | – | JSON adım listesi (aşağıda) |
| `selectors` | – | `email=#email, password=input[name=pass]` veya JSON eşleme |
| `submit` | – | `false` → formu doldur ama göndermeye kalkma |
| `proxy` | – | Bu satıra özel proxy (`host:port`, `user:pass@host:port`, `socks5://…`) |
| `iterations` / `tags` / `active` | – | Tekrar sayısı · etiketler · satırı devre dışı bırakma |

**Çok değerli hücre:** `email` sütununa `a@x.com|b@x.com` yazarsanız iki ayrı test satırı üretilir.

### Deklaratif adım (steps) örneği

```json
[
  {"action": "goto", "value": "https://ornek.com/kayit"},
  {"action": "fill", "target": "#wizard-email", "value": "{email}"},
  {"action": "click", "target": "#wizard-continue"},
  {"action": "wait_for_selector", "target": "#wizard-password"},
  {"action": "fill", "target": "#wizard-password", "value": "{password}"},
  {"action": "check", "target": "#terms"},
  {"action": "click", "target": "button:has-text('Tamamla')"},
  {"action": "expect_url", "value": "/dashboard"},
  {"action": "screenshot", "value": "son-adim"}
]
```

Desteklenen eylemler: `goto`, `reload`, `go_back`, `fill`, `type`, `click`, `dblclick`,
`hover`, `press`, `check`, `uncheck`, `select`, `upload`, `wait`, `sleep`,
`wait_for_selector`, `wait_for_url`, `wait_for_load_state`, `wait_for_response`,
`expect_text`, `expect_visible`, `expect_url`, `screenshot`, `evaluate`, `scroll`,
`frame_fill`, `frame_click`, `new_tab`, `close_tab`. `{email}` / `{row_index}` /
`{iteration}` / `{uuid}` gibi yer tutucular çalışır. Excel hücresinde kısa biçim de
geçerlidir: `fill #email: {email} | click text=Kaydol | expect_url /welcome`.

---

## 6. Proxy yönetimi

Listeyi örnekten kopyalayın: `cp proxies.example.txt proxies.txt` (`proxies.txt`, `.env` ve `artifacts/` sürüm kontrolüne **girmez**).

`proxies.txt` (veya `WAFT_PROXIES` ortam değişkeni) formatları:

```text
127.0.0.1:8080                      # host:port (varsayılan http)
user:pass@proxy.ornek.com:8080      # kimlik bilgili
http://user:pass@10.0.0.5:3128      # şema açık
socks5://user:pass@10.0.0.6:1080    # SOCKS5
socks5h://10.0.0.7:1080             # DNS'i proxy'de çözer
1.2.3.4:8080 kullanici parola       # boşluklu biçim
# yorum satırları ve boş satırlar yok sayılır
```

* **Rotasyon politikaları:** varsayılan **sıralı (round-robin)** — `--proxy-cycle` (config:
  `proxy_cycle = true`); en az kullanılan sağlıklı proxy'yi seçmek için `--no-proxy-cycle`
  (least-used). Her context kendi proxy'sine **sticky**'dir; hata sonrası otomatik olarak yeni
  proxy ile context yeniden kurulur.
* **Health check:** başlangıçta her proxy ayrı bir gizli context ile sınanır (kimlik doğrulama
  + CONNECT tüneli), başarısızlar cooldown'a alınır. `--no-proxy-health-check` kapatır, hiçbiri
  geçemezse `--proxy-fail-closed` çalıştırmayı durdurur.
* **Sır koruması:** loglar, raporlar ve HAR dosyalarında parolalar maskelenir
  (`user:***@host`), `Authorization`/`Cookie` başlıkları redakte edilir.
* `--proxy-mode off` → proxy'siz çalış (demo/CI için), `require` → proxy yoksa başlamadan hata ver.

---

## 7. Stealth / anti-detection

`waft/stealth.py` iki katmanı birleştirir:

1. **`playwright-stealth`** (kuruluysa) — webdriver, plugin, permission, iframe, medya codec
   evasions.
2. **WAFT init script'i** (`context.add_init_script`, her iframe'de çalışır):
   * `navigator.webdriver` → kaldırılmış/`false`;
   * **canvas**: deterministik, bağlama özel gürültü. Aynı canvas iki kez okunduğunda **byte
     byte aynı** sonucu verir (gerçek cihaz davranışı) ama farklı context'lerde farklıdır;
   * **WebGL**: `UNMASKED_VENDOR/RENDERER` gerçek GPU adlarıyla spoof edilir, `SwiftShader`
     (headless imzası) filtrelenir;
   * **AudioContext**: Analyser/`getChannelData` gürültüsü (deterministik);
   * **WebRTC** varsayılan olarak bloklanır (gerçek IP sızıntısını önler), `--allow-webrtc` açar;
   * `navigator.plugins/mimeTypes`, `languages`, `platform`, `hardwareConcurrency`,
     `deviceMemory`, `maxTouchPoints`, `vendor`, `connection`;
   * `window.chrome.*`, `navigator.permissions.query` tutarlılığı;
   * **CDP/automation izleri** (`cdc_*`, `__webdriver*`, `__playwright*`, `$cdc_*`) temizlenir;
   * `Function.prototype.toString` native kamuflajı (yamalanan fonksiyonlar `[native code]` der);
   * `Intl.DateTimeFormat` saat dilimi + locale tutarlılığı (proxy ülkesi ↔ TZ ↔ dil).

**Kendi kendini doğrulama:** `--verify-stealth` her navigasyondan sonra sayfa içinde şu
kontrolleri yapar: `webdriver` yok · plugin'ler var · `window.chrome` var · `languages` dolu ·
canvas stabil · WebGL mevcut ve SwiftShader değil · WebRTC bloklu. Sonuçlar rapora yazılır.

**Gerçekçi insan davranışı (opsiyonel, varsayılan açık):** alanlara karakter karakter yazma
(8–30 ms, alan başına ≤900 ms bütçe), tıklama öncesi küçük gecikmeler, kaydırma ve fare
hareketleri. Tamamen kapatmak için `--no-humanize` (testler 3-5× hızlanır).

> ⚠️ Hiçbir stealth katmanı %100 anonimlik garanti etmez. Bot yönetimi sizi hedefliyorsa
> konut (residential) proxy + gerçek cihaz profilleri + makul istek hızı gerekir. Bu araç
> **kendi sistemlerinizi** test etmek içindir (bkz. [Yasal uyarı](#17-yasal-uyarı)).

---

## 8. Form doldurma motoru

1. **Tarama:** sayfadaki tüm `input`/`textarea`/`select`/`[contenteditable]` elemanları
   parmak izlenir (name, id, label, aria-label, placeholder, autocomplete, type, required,
   maxlength, pattern, `options`, form action).
2. **Eşleştirme:** her veri anahtarı, alias tablosu (TR+EN: `şifre/pass/password`,
   `adsoyad/full name`, `cep/gsm/phone`, `kvkk/terms` …), birebir attribute eşitliği, kanonik
   alan tipi uyumu, kelime parçalama, substring ve fuzzy benzerlik ile puanlanır. Eşik
   `--field-match-threshold` (varsayılan 45). `selectors.json` / satır `selectors` kolonu
   her zaman kazanır.
3. **Doldurma:** metin alanları (insansı yazma / anında doldurma), `password` maskeleme, tarih
   format dönüşümü (`28.09.2026` → `2026-09-28`), sayı/telefon temizliği, `maxlength` kırpma,
   `select` (value → label → index), checkbox/radio (`true/false/evet/hayir/1/0`), dosya yükleme.
4. **Çok adımlı formlar:** form büyüdükçe tekrar tarama (sihirbazlar) veya `steps` ile tam kontrol.
5. **Gönderim & sonuç:** `submit_button_text`/`submit_selector` → otomatik düğme keşfi → tıklama
   (başarısızsa JS click) → `success_selector`, `success_url_regex`, metin analizi, `error_selector`
   ile **success / error / captcha / unknown** sonucu. `expect_error` satırlarında "error"
   beklenen sonuçtur ve test **geçer**.
6. **CAPTCHA:** reCAPTCHA/hCaptcha/Turnstile/Cloudflare challenge sayfaları **tespit edilir**
   (çözülmez). Politika: `--captcha-action error` (varsayılan, hedefi başarısız say) ·
   `skip` · `continue`.

Her alan için raporda ne yazıldığı görünür (parolalar maskeli):

```text
✎ email          → a***@example.com      (<input type=email name=email …>, score 250)
✎ password       → <14 chars masked>     (<input type=password name=password …>, score 270)
Form fill finished: 10 filled, 2 checked, 1 selected, 0 uploaded, 0 unmatched (412 ms)
```

---

## 9. Ağ trafiği dinleme & API keşfi

Her context için `page.on(...)` ile **request / response / requestfailed / console /
pageerror / dialog / download** olayları dinlenir:

```text
→ POST   http://127.0.0.1:8080/submit  body=email=ayse%40...&first_name=Ay%C5%9Fe
← POST   200 http://127.0.0.1:8080/submit (3 ms, 3.4 KB)
[API] discovered POST 127.0.0.1:8080/api/v1/register
✖ FAILED GET   https://cdn.example.com/x.js → net::ERR_BLOCKED_BY_CLIENT
```

* Terminal logu (seviye renkli) + `contexts/<ctx>/network.jsonl` (tam kayıt) +
  `console.jsonl` + `network_summary.json`.
* **API keşfi:** XHR/fetch, POST/PUT/PATCH/DELETE ve `/api/`, `/graphql`, `/v3/` gibi desenler
  otomatik işaretlenir; method + host + path + query anahtarları + görülen status kodları
  `endpoints.json` ve `run.json → api_endpoints` içine yazılır.
* **Güvenlik:** `Authorization`, `Cookie`, `X-Api-Key`, JWT'ler, e-postalar, kart/TCKN
  numaraları loglardan ve `--capture-har` çıktısından redakte edilir (`--no-redact` ile kapatılır).
* Gövdeler varsayılan olarak **kaydedilmez**; `--log-bodies` ile (kırpılmış + redakte) açılır.
* Redaksiyon tamamen `--no-redact` ile kapatılabilir — **yalnızca güvenilir yerel hata ayıklama**
  için; kapalıyken loglar/HAR'lar gerçek token ve parolaları içerir.

---

## 10. IMAP ile e-posta doğrulama

Form gönderildikten sonra hedef sistem "hesabını doğrula" e-postası yolluyorsa WAFT bunu
bekleyip akışı tamamlar:

```bash
export IMAP_HOST=imap.gmail.com IMAP_USER=test@gmail.com IMAP_PASSWORD="uygulama-sifresi"
python -m waft --data examples/data/targets.xlsx --contexts 5 --imap \
  --imap-subject-regex "(doğrula|verify|aktivasyon)" \
  --imap-sender no-reply@ --imap-timeout 180
```

* `SINCE` + `UNSEEN` (+ varsa `FROM`/`TO`) ile hızlı arama, ardından yerel **skorlama**
  (konu regex'i, gönderen, alıcı adresi, tazelik, doğrulama anahtar kelimeleri).
* MIME çözümlemesi: iç içe multipart, quoted-printable, base64, HTML-only, tüm charset'ler.
* **Link** yakalanır → yeni sekmede açılır, ekran görüntüsü alınır; **OTP** yakalanır → ilgili
  alana yazılıp gönderilir. Aynı e-posta ikinci kez kullanılmaz (UID takibi).
* Doğrulama başarısızsa adım **failed** olur ve trace/bundle üretilir; sessizce "geçmiş"
  sayılmaz. `--no-imap` ile tamamen kapatılabilir.
* Posta kutusu temizliği: `IMAP_MARK_SEEN=true`, `IMAP_MARK_PROCESSED=true` (işlenenler ayrı
  klasöre taşınır).

---

## 11. Artefaktlar ve raporlar

```text
artifacts/run-20260928-145831-f295f9/
├── run.json                 # makine-okunur tam özet (context'ler, adımlar, metrikler)
├── results.csv              # her hedef çalışması için 1 satır (Excel/BI dostu)
├── contexts.csv             # her bağlam için proxy/cihaz/süre özeti
├── junit.xml                # CI entegrasyonu (Jenkins/GitLab/GitHub Actions)
├── endpoints.json           # keşfedilen API uç noktaları (method+host+path+query)
├── failures.json            # sadece başarısızlar + kanıt yolları
├── summary.md               # okunabilir Markdown rapor (PR/ticket için)
├── artifact-index.json      # üretilen tüm dosyaların envanteri + boyutları
├── logs/waft.log            # döner (rotating) tam detay log
└── contexts/ctx-00-ab01/
    ├── screenshots/01_loaded_….png, 02_filled_….png, 03_outcome_….png, 05_final_….png
    ├── traces/ctx-00-ab01-….zip        # hata durumunda Playwright trace (trace viewer ile açılır)
    ├── html/failure-3.html             # hata anındaki DOM
    ├── har/network.har                 # --capture-har (redakte edilmiş)
    ├── network.jsonl / console.jsonl / network_summary.json
    ├── ctx-00-ab01-storage.json        # cookie + localStorage (oturum tekrarı için)
    ├── summary.json                    # bu bağlamın tüm sonuçları
    └── ctx-00-ab01-failure-bundle.zip  # tüm kanıtlar + manifest.json
```

Trace'i görüntülemek için: `python -m playwright show-trace artifacts/run-…/contexts/…/traces/….zip`.
Disk bütçesi `--max-artifacts-mb` (varsayılan 2 GB) aşılırsa yeni snapshot/trace alınmaz,
uyarı loglanır — disk doldurma riski yoktur.

---

## 12. Programatik kullanım

```python
import asyncio
from waft import Config, Orchestrator

config = Config(
    data_file="examples/data/targets.xlsx",
    contexts=10,
    concurrency=5,
    proxy_file="proxies.txt",
    headless=True,
    scenario="auto",
)
config.validate()
exit_code = asyncio.run(Orchestrator(config).run())
print("exit:", exit_code)
```

Kendi verinizi koddan üretmek / tek bağlamla oynamak:

```python
import asyncio
from waft import Config, TargetRow
from waft.engine import ContextEngine, EngineHooks, profile_from_config
from waft.artifacts import ArtifactManager
from waft.proxy_manager import ProxyPool
from waft.stealth import StealthLayer
from playwright.async_api import async_playwright

async def main():
    config = Config(contexts=1, proxy_mode="off", humanize=False)
    config.validate()
    artifacts = ArtifactManager(config, "manual-run")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        pool = ProxyPool(config); pool.load()
        engine = ContextEngine(
            config=config,
            profile=profile_from_config(config, 0),
            browser=browser,
            proxy_pool=pool,
            proxy=None,
            artifacts=artifacts,
            stealth=StealthLayer(config),
            hooks=EngineHooks(),
        )
        await engine.start()
        row = TargetRow(index=1, target_url="https://example.com/kayit",
                        form_data={"email": "a@b.com", "first_name": "Ada", "terms": "true"})
        result = await engine.run_target(row)
        print(result.status, result.fill_report.to_dict())
        await engine.close()
        await browser.close()

asyncio.run(main())
```

---

## 13. Mimari / modül haritası

| Modül | Sorumluluk |
|-------|------------|
| `waft/cli.py`, `waft/__main__.py` | `argparse` arayüzü, çıkış kodları, banner |
| `waft/config.py` | ~150 alanlı `Config`, `.env`/ortam/JSON katmanları, cihaz profilleri, senaryo alias'ları, ortam kontrolü |
| `waft/orchestrator.py` | Çalıştırma planı, tarayıcı yaşam döngüsü, `Semaphore` ile context eşzamanlılığı, sinyal yönetimi (Ctrl+C → graceful), toplulaştırma, dry-run |
| `waft/engine.py` | **Bir context = bir sanal kullanıcı**: context oluşturma, stealth, ağ monitörü, senaryo yürütücü, adım motoru, IMAP adımı, retry, proxy değiştirme, sayfa geri dönüşümü |
| `waft/forms.py` | DOM taraması, alan eşleştirme/skorlama, doldurma, CAPTCHA tespiti, submit ve sonuç tespiti |
| `waft/data_source.py` | Excel/JSON/CSV yükleyici, başlık normalizasyonu, çok değerli hücreler, satır bazlı override'lar |
| `waft/proxy_manager.py` | Proxy ayrıştırma, havuz, rotasyon, health-check, cooldown, sır maskeleme |
| `waft/stealth.py` | Stealth katmanı (playwright-stealth + WAFT init script + doğrulama) |
| `waft/network_monitor.py` | İstek/yanıt/console olayları, metrikler, API keşfi, JSONL artefaktları |
| `waft/imap_client.py` | IMAP bağlantısı (thread), MIME ayrıştırma, link/OTP çıkarma, skorlama, UID takibi |
| `waft/artifacts.py` | Dizin şeması, ekran görüntüsü, trace, HAR redaksiyonu, başarısızlık paketi, disk bütçesi |
| `waft/reporting.py` | run.json / CSV / JUnit / Markdown / konsol özeti |
| `waft/models.py` | Veri modeli (`TargetRow`, `TargetRunResult`, `ContextResult`, `RunSummary` …) |
| `waft/errors.py` | Hata hiyerarşisi + `is_retryable` sınıflandırması |
| `waft/utils.py` | Yer tutucu interpolasyonu, retry, rate limiter, redaksiyon, JSON/CSV yardımcıları |
| `waft/logging_setup.py` | rich/plain konsol + döner dosya logu + `[ctx-id]` adaptörü |
| `examples/demo_site.py` | Test/demo amaçlı gerçekçi web uygulaması (form, sihirbaz, XHR, doğrulama, CAPTCHA) |
| `examples/make_sample_data.py` | Örnek Excel/JSON/proxy/.env üretici |
| `tests/` | 70 birim testi + 5 gerçek tarayıcı entegrasyon testi |

---

## 14. Testler ve kalite

```bash
pytest tests/ -q -m "not integration"   # 70 birim testi (tarayıcı gerekmez)
pytest tests/ -q                        # + 5 entegrasyon testi (Chromium gerekir)
ruff check waft/ examples/ tests/       # statik analiz
```

Entegrasyon testleri kendi demo sunucusunu rastgele bir portta ayağa kaldırır ve şunları
doğrular: 3 context paralel çalışıp 9/9 hedefi tamamlar · cookie/localStorage izolasyonu ·
stealth invariant'ları (`webdriver` yok, plugin'ler var, WebRTC bloklu, canvas stabil,
WebGL SwiftShader değil) · deklaratif 9 adımlı sihirbaz akışı · hata → ekran görüntüsü +
trace + `.zip` paket + retry.

---

## 15. Ölçekleme, performans ve sınırlar

| Ölçüm | Değer |
|-------|-------|
| 10 context × 14 hedef (Excel, insansı yazma açık) | **140/140 başarılı, ~309 s**, 816 adım, 44 MB artefakt |
| 3 context × 6 hedef (JSON, sihirbaz + load test + API keşfi) | 18/18 başarılı, ~46 s |
| Bağlam başına bellek | ~60–120 MB (sayfa içeriğine göre) |
| Önerilen üst sınır (2 GB RAM / 2 vCPU) | 10–12 context; daha fazlası için `--concurrency` ile sıraya alın |

* Bellek küçükse: `--concurrency 5`, `--context-restart-pages 25` (sayfa geri dönüşümü),
  `--block-resource-types image,media,font`, `--no-screenshots`, `--no-trace`.
* Hız için: `--no-humanize`, `--trace off`, `--devices` ile mobil profillere geçmek
  (mobil sayfalar daha küçük), `--scenario load-test` (form doldurma yerine ölçüm).
* Yük testi yaparken hedef sistemi korumak için: `--rate-limit 2` (saniyede 2 navigasyon),
  `--iterations` ile kontrollü tekrar, `--rate-limit` + `--shuffle --seed` kombinasyonu.

---

## 16. Sorun giderme

| Belirti | Çözüm |
|---|---|
| `error while loading shared libraries: libnspr4.so` | `python -m playwright install-deps chromium` |
| `Executable doesn't exist … playwright install` | `python -m playwright install chromium` |
| Chromium açılmıyor / `Target page … has been closed` | Container'da `--no-sandbox` (varsayılan açık); `--allow-chromium-sandbox` ile kapatabilirsiniz |
| "No form fields detected" | Form iframe içinde olabilir (`--steps` + `frame_fill`) veya JS ile geç yükleniyor (`--steps` ile `wait_for_selector` ekleyin) |
| Alanlar eşleşmiyor | `--selectors selectors.json` ile açık seçici verin, `--field-match-threshold` düşürün, `-vv` ile skorları görün |
| Sonuç "unknown" | `success_selector` / `success_url_regex` / `error_selector` tanımlayın |
| Tüm proxy'ler başarısız | `--no-proxy-health-check` ile kontrolü atlayın, `--proxy-health-url` değiştirin, HTTP/SOCKS şemasını doğrulayın |
| IMAP login hatası | Gmail için **uygulama şifresi** gereklidir; `IMAP_PORT=993` + `IMAP_SSL=true`; 2FA açık hesaplarda normal şifre çalışmaz |
| Doğrulama e-postası bulunamıyor | `--imap-subject-regex`, `--imap-sender` verin; `IMAP_MAILBOX` spam klasörünü de deneyin; `--imap-timeout` artırın |
| Bellek şişmesi | `--concurrency` düşürün, `--context-restart-pages`, `--no-screenshots` |
| Türkçe karakter bozulması | Excel'i UTF-8/UTF-16 kaydedin; CSV'de BOM'lu (utf-8-sig) tercih edin — loader ikisini de destekler |

`-v` ayrıntılı log, `-vv` başlık/gövde logu, `--print-config` etkin yapılandırmayı JSON olarak
yazdırır, `--dry-run` hiç tarayıcı açmadan planı gösterir.

---

## 17. Yasal uyarı

Bu çerçeve **yalnızca yetkili olduğunuz sistemleri** test etmek için tasarlanmıştır: kendi
uygulamalarınız, müşteri sözleşmesi/pentest kapsamındaki hedefler, bug-bounty kapsamı veya
staging ortamları. Stealth ve proxy özellikleri **meşru test trafiğini gerçek kullanıcı
trafiğine benzetmek** içindir — başkasının sisteminin erişim kontrollerini aşmak için
kullanmak yasa dışı olabilir. Yük testlerini hedef sistemin sahibinin onayı ve makul
sınırlar içinde (rate-limit, iterations) yapın. Sorumluluk kullanıcıdadır.

---

> Yasal uyarının tam metni: [`NOTICE.md`](NOTICE.md) · Lisans: [MIT](LICENSE)

### Hızlı komut özeti

```bash
make install                                  # bağımlılıklar + Chromium
make sample-data && make demo-site            # örnek veri + demo uygulaması
python -m waft --data examples/data/targets.xlsx --contexts 10 --concurrency 10 --proxy-mode off
python -m pytest tests/ -q -m "not integration"
```
