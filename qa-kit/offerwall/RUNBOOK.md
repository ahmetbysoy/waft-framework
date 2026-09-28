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

## 10. Bir sonraki adım

Kendi staging'inizin host'unu verin: seçicileri gerçek alanlarla günceller, `authorized_hosts.txt`'e
ekler ve koşuyu orada birlikte doğrularız. Partner entegrasyonuysa postback doğrulamasını
(`postback_receiver.py --serve` + imza) akışa bağlarız.
