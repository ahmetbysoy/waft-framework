# WAFT · Teslim Paketi — Kayıt Formu + E-posta Doğrulama Yük/Regresyon Testi

Dört artefaktı **kopyala-çalıştır** sırasıyla içerir. Çalıştırılabilir tam kaynaklar `qa-kit/`
altındadır (bu dosya onların indeksi + gömülü içeriğidir):

| # | Artefakt | Dosya |
|---|---|---|
| 1 | Veri şeması (`targets.xlsx`) + tipli üretici | `qa-kit/make_targets.py` → `targets.xlsx`, `targets.example-gmail.xlsx` |
| 2 | Yedekli CSS+XPath seçici kataloğu + resolver | `qa-kit/selectors.json`, `qa-kit/selector_resolver.py` |
| 3 | Wrapper scripti (10 context / proxies.txt / IMAP) | `qa-kit/run_harvest.py` → motor: `qa-kit/run_regression.py` |
| 4 | Terminal komutu | `qa-kit/run_regression.sh` + bölüm 4 |

**Kapsam:** hedef **senin sistemin** (staging / pre-prod / lokal demo). 3. parti offerwall ve
mikro görev platformları (timewall.io, jumptask.io …) `run_regression.py` içindeki scope guard
tarafından **her koşulda reddedilir** — orada otomatik hesap açmak yük testi değil, abuse'dır.

---

## 1) `targets.xlsx` — şema ve örnek satırlar

Sütunlar (ilk dokuzu istenen sözleşme; kalanı zorunlu alanlar + raporlama):

```text
target_url · scenario · email · password · first_name · success_selector ·
requires_email_verification · verification_email · verification_subject_regex ·
last_name · password_confirm · terms · name · iterations · tags · notes
```

Excel'e doğrudan yapıştırılabilecek 3 örnek satır (sekme ayraçlı) — `sizin.hesabiniz` yerine
kendi Gmail adresini yaz:

```tsv
target_url	scenario	email	password	first_name	success_selector	requires_email_verification	verification_email	verification_subject_regex	last_name	password_confirm	terms	name	iterations	tags	notes
https://staging.sirketiniz.com/register	email-verify	sizin.hesabiniz+signup001@gmail.com	Str0ng-Passw0rd!	Ayse	[data-testid='success']	true	sizin.hesabiniz+signup001@gmail.com	(doğrula|dogrula|verify|aktivasyon|activat)	Yilmaz	Str0ng-Passw0rd!	true	signup-verify-01	1	regression,email-verify,tr	Kayıt + doğrulama satırı (kendi ortamınız)
https://staging.sirketiniz.com/register	email-verify	sizin.hesabiniz+signup002@gmail.com	Str0ng-Passw0rd!	Mehmet	[data-testid='success']	true	sizin.hesabiniz+signup002@gmail.com	(doğrula|dogrula|verify|aktivasyon|activat)	Kaya	Str0ng-Passw0rd!	true	signup-verify-02	1	regression,email-verify,tr	Kayıt + doğrulama satırı (kendi ortamınız)
https://staging.sirketiniz.com/register	email-verify	sizin.hesabiniz+signup003@gmail.com	Str0ng-Passw0rd!	Zeynep	[data-testid='success']	true	sizin.hesabiniz+signup003@gmail.com	(doğrula|dogrula|verify|aktivasyon|activat)	Demir	Str0ng-Passw0rd!	true	signup-verify-03	1	regression,email-verify,tr	Kayıt + doğrulama satırı (kendi ortamınız)
```

**Gmail akışı (istenen senaryo):** tek Gmail hesabı + **plus-alias** yeterlidir;
`sizin.hesabiniz+signup001@gmail.com`, `+signup002`, … hepsi aynı gelen kutusuna düşer ve WAFT
IMAP'ten `TO` filtresiyle ilgili satırın mailini bulur. Kurulum: Google hesabında 2 adımlı
doğrulama açıp **uygulama şifresi** üret, sonra `.env`:

```dotenv
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_SSL=true
IMAP_USER=sizin.hesabiniz@gmail.com
IMAP_PASSWORD=<16 haneli uygulama şifresi>
```

`.xlsx` üretimi (önerilen; doğrulaması yapılmış tipli üretici):

```bash
# Gmail plus-alias'lı 12 satır → kendi staging hedefiniz
python qa-kit/make_targets.py \
  --base-url https://staging.sirketiniz.com/register \
  --gmail-user sizin.hesabiniz@gmail.com \
  --rows 12 --out qa-kit/targets.xlsx

# yerel/demo posta kutusu (devmail) ile
python qa-kit/make_targets.py --base-url http://127.0.0.1:8080 --rows 12
```

Doğrulanmış örnek çıktı (bu repoda üretildi):

```text
1. https://staging.sirketiniz.com/register/ | email-verify | sizin.hesabiniz+signup001@gmail.com | Str0ng-Passw0rd! | Ayse Yilmaz | terms=true | [data-testid='success'] | verify=true
2. https://staging.sirketiniz.com/register/ | email-verify | sizin.hesabiniz+signup002@gmail.com | Str0ng-Passw0rd! | Mehmet Kaya | terms=true | [data-testid='success'] | verify=true
3. https://staging.sirketiniz.com/register/ | email-verify | sizin.hesabiniz+signup003@gmail.com | Str0ng-Passw0rd! | Zeynep Demir | terms=true | [data-testid='success'] | verify=true
```

Sütun sözleşmesi:

| Sütun | Zorunlu | Anlam |
|---|---|---|
| `target_url` | ✅ | Açılacak kayıt formu URL'i |
| `scenario` | – | `email-verify` (bu kitin varsayılanı), `form-submit`, `smoke`, `load-test` |
| `email` | ✅ | Forma yazılan adres **ve** IMAP'te beklenen alıcı |
| `password` | ✅ | Parola (loglarda maskelenir) |
| `first_name` | – | Alias eşleştirme testi (name/ad/isim/…) |
| `success_selector` | – | Başarıyı kanıtlayan CSS (ör. `[data-testid='success']`) |
| `requires_email_verification` | – | `true` → gönderim sonrası IMAP adımı çalışır |
| `verification_email` | – | IMAP'te aranacak posta kutusu (genelde `email` ile aynı) |
| `verification_subject_regex` | – | Konu filtresi: `(doğrula\|dogrula\|verify\|aktivasyon\|activat)` |
| `last_name` / `password_confirm` / `terms` | – | Çoğu kayıt formunun zorunlu alanları |
| `name` / `iterations` / `tags` / `notes` | – | Raporlama, tekrar sayısı, CI filtreleme |

---

## 2) `selectors.json` — yedekli (fallback) seçici kataloğu

Alan başına **sıralı** aday listesi: önce CSS, sonra XPath. Bloklar `host:port` → `host` → `*`
sırasıyla birleşir, en özel kazanır. `selector_resolver.py` bu zincirleri gerçek tarayıcıda
dener, kazananı kanonik forma (`#id`, `[name='…']`) çevirip `selectors.resolved.json` yazar;
WAFT'a `--selectors` ile verilir. Kendi staging host'u için yeni blok ekle:
`hosts["staging.sirketiniz.com"] = { "email": [ … ], "password": [ … ], … }`.

```json
{
  "_comment": [
    "Fallback selector catalogue for the WAFT regression kit (see qa-kit/README.md).",
    "Each field holds an ORDERED list of candidates: CSS first, then XPath. The resolver",
    "(selector_resolver.py) probes them in a real browser and emits selectors.resolved.json,",
    "a flat WAFT-native map ({data_key: selector}) that is passed via --selectors.",
    "'hosts' blocks are matched on host:port (exact), then on host (exact), then '*'.",
    "Everything here targets YOUR OWN application. Do not add selectors for systems you",
    "are not authorised to test."
  ],
  "version": 1,
  "defaults": {
    "probe_timeout_ms": 10000,
    "require_visible": true
  },
  "hosts": {
    "127.0.0.1:8080": {
      "email": [
        "#email",
        "input[name='email']",
        "input[type='email']",
        "[data-testid='signup-email']",
        "[autocomplete='email']",
        "//input[@type='email']",
        "//label[contains(translate(., 'EPOSTAİ', 'epostaİ'), 'e-posta')]/following::input[1]"
      ],
      "password": [
        "#password",
        "input[name='password']",
        "input[type='password']",
        "[autocomplete='new-password']",
        "//input[@type='password']"
      ],
      "password_confirm": [
        "#password_confirm",
        "input[name='password_confirm']",
        "input[name='password2']",
        "input[name='confirm_password']",
        "//label[contains(., 'tekrar')]/following::input[@type='password'][1]"
      ],
      "first_name": [
        "#fname",
        "input[name='first_name']",
        "input[name='fname']",
        "[autocomplete='given-name']",
        "//input[contains(@placeholder, 'Adınız')]"
      ],
      "last_name": [
        "#lname",
        "input[name='last_name']",
        "[autocomplete='family-name']",
        "//input[contains(@placeholder, 'Soyad')]"
      ],
      "terms": [
        "#terms",
        "input[name='terms']",
        "input[type='checkbox'][required]",
        "//input[@type='checkbox'][@required]"
      ],
      "submit": [
        "[data-testid='signup-submit']",
        "button[type='submit']",
        "input[type='submit']",
        "//button[contains(translate(., 'KAYDOL', 'kaydol'), 'kaydol')]",
        "//button[contains(., 'Üye')]",
        "//button[contains(translate(., 'GÖNDER', 'gönder'), 'gönder')]"
      ],
      "success": [
        "[data-testid='success']",
        ".notice.success",
        "text=Kayıt başarılı",
        "//*[contains(@class, 'success')]"
      ],
      "verified": [
        "[data-testid='verified']",
        "text=Hesabınız doğrulandı",
        "//*[contains(text(), 'doğrulandı')]"
      ],
      "error": [
        "[data-testid='error']",
        ".notice.error",
        "//*[contains(@class, 'error')]"
      ],
      "captcha": [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "#captcha",
        "[data-testid='captcha']"
      ]
    },
    "*": {
      "_comment": "Generic, framework-agnostic fallbacks for 'your own app' (staging/vendor-free).",
      "email": [
        "#email",
        "input[type='email']",
        "input[name='email']",
        "input[name='username']",
        "[autocomplete='email']",
        "[data-testid*='email']",
        "//input[contains(@id, 'email') or contains(@name, 'email')]"
      ],
      "password": [
        "input[type='password']",
        "input[name='password']",
        "#password",
        "[autocomplete='new-password']",
        "//input[@type='password']"
      ],
      "password_confirm": [
        "input[name='password_confirm']",
        "input[name='password2']",
        "input[name='confirm_password']",
        "input[name='passwordConfirmation']",
        "[autocomplete='new-password'] + input[type='password']",
        "(//input[@type='password'])[2]"
      ],
      "first_name": [
        "input[name='first_name']",
        "input[name='firstName']",
        "input[name='given_name']",
        "[autocomplete='given-name']",
        "input[name='name']",
        "//label[contains(translate(., 'AD', 'ad'), 'ad')]/following::input[1]"
      ],
      "last_name": [
        "input[name='last_name']",
        "input[name='lastName']",
        "input[name='family_name']",
        "[autocomplete='family-name']",
        "input[name='surname']"
      ],
      "terms": [
        "input[type='checkbox'][required]",
        "input[name='terms']",
        "input[name='kvkk']",
        "#terms",
        "//input[@type='checkbox'][@id='terms']"
      ],
      "submit": [
        "button[type='submit']",
        "input[type='submit']",
        "[data-testid='submit']",
        "button:has-text('Kaydol')",
        "button:has-text('Gönder')",
        "button:has-text('Sign up')",
        "//button[@type='submit']"
      ],
      "success": [
        "[data-testid='success']",
        "[role='alert'].success",
        ".alert-success",
        ".notice.success",
        "text=success",
        "text=başarılı",
        "//*[contains(@class, 'success') or contains(@class, 'alert-success')]"
      ],
      "verified": [
        "[data-testid='verified']",
        "[data-testid='verification-success']",
        "text=doğrulandı",
        "text=verified",
        "//*[contains(text(), 'doğrulandı') or contains(text(), 'verified')]"
      ],
      "error": [
        "[role='alert'].error",
        ".alert-danger",
        ".notice.error",
        "[data-testid='error']",
        "//*[contains(@class, 'error')]"
      ],
      "captcha": [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "iframe[src*='turnstile']",
        ".g-recaptcha",
        ".h-captcha",
        "[data-testid='captcha']"
      ]
    }
  }
}
```

Resolver (probe + kanonikleştirme + rapor):

```bash
python qa-kit/selector_resolver.py --data qa-kit/targets.xlsx \
  --catalogue qa-kit/selectors.json \
  --out qa-kit/selectors.resolved.json \
  --report qa-kit/selectors.resolved.report.json
```

Doğrulanmış çıktı (deneme hedefi: yerel demo uygulaması):

```text
✔ 127.0.0.1:8080: 7 resolved, missing=['success', 'verified', 'error', 'captcha']
✔ qa-kit/selectors.resolved.json written (6 selector(s))
{ "email": "#email", "password": "#password", "password_confirm": "#password_confirm",
  "first_name": "#fname", "last_name": "#lname", "terms": "#terms" }
```

`submit` / `success` / `verified` / `error` / `captcha` seçicileri **gönderim sonrası**
elemanlardır; landing page'de bulunamamaları normaldir → raporda "informational", çıkış kodu 0.
Sonuç tespiti için `success_selector` sütununu kullan.

---

## 3) `run_harvest.py` — wrapper scripti (tam kaynak)

Profil: **10 izole context / 10 paralel**, `proxies.txt`, `--imap` açık, konu regex'i ile
doğrulama linkini yakalama, `--captcha-action skip` ile CAPTCHA'da **sadece o hedefi** işaretleyip
devam etme, `--stealth --verify-stealth --capture-har --log-network`. İki mod:
`--mode orchestrator` (in-process `waft.Orchestrator`) veya `--mode cli` (`python -m waft` alt süreç).

```python
#!/usr/bin/env python3
"""``run_harvest.py`` - load-test / regression harness for the sign-up + verification-mail flow.

What it is
----------
The entry point the runbook calls.  It is a **thin, fully typed driver** on top of
``qa-kit/run_regression.py`` (scope guard, pre-flight, selector probing, artifact manifest) which
in turn drives WAFT - either through ``waft.Orchestrator`` in-process or by spawning the WAFT CLI
(``python -m waft``) as a subprocess.  "Harvest" here means: create accounts on **your own**
application and harvest the verification e-mail/link your app sends, so the whole flow
(form fill -> submit -> mail -> link -> verified page) is covered by one measurable run.

Default load profile (all overridable)
--------------------------------------
* ``--contexts 10``      ten isolated browser contexts (own cookies/localStorage/session)
* ``--concurrency 10``   all ten in parallel
* ``proxies.txt``        proxy list read from the repo root (``--no-proxy`` to switch off)
* ``--imap`` on          verification mail harvested over IMAP
* ``--imap-subject-regex "(doğrula|verify|aktivasyon…)"``  subject filter
* ``--captcha-action skip``  a bot-challenge only marks **that** target, the run continues
* ``--stealth --verify-stealth --capture-har --log-network``  fingerprint hardening + full
  request/response capture for later API analysis

Scope
-----
Third-party offerwall / micro-task / reward platforms (timewall.io, jumptask.io, …) are rejected
outright by the scope guard in ``run_regression.py``: automated sign-ups there are abuse, not
testing.  Point the harness at your own staging/local environment via ``--base-url`` and add the
host to ``qa-kit/authorized_hosts.txt``.

Usage
-----
Local, end-to-end (demo app + devmail, see qa-kit/README.md)::

    python qa-kit/run_harvest.py \
        --data qa-kit/targets.xlsx --base-url http://127.0.0.1:8080 \
        --imap-host 127.0.0.1 --imap-port 1430 --imap-user devmail --imap-password devmail --imap-no-ssl \
        --no-proxy --no-probe-selectors --log-level WARNING

Your staging environment (proxy rotation + real IMAP, CLI subprocess mode)::

    python qa-kit/run_harvest.py --mode cli --base-url https://staging.sirketiniz.com \
        --imap-host imap.gmail.com --imap-user sizin.hesabiniz@gmail.com --imap-password "$IMAP_PASSWORD"

Exit codes (same as WAFT): 0 pass · 1 failures · 2 usage/scope/data · 3 proxy · 130 interrupted.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Final, Optional, Sequence

KIT_DIR: Final[Path] = Path(__file__).resolve().parent
REPO_ROOT: Final[Path] = KIT_DIR.parent
for _path in (str(REPO_ROOT), str(KIT_DIR)):  # runnable without installing the package
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: Defaults that define the "10 parallel users" load profile.
DEFAULT_CONTEXTS: Final[int] = 10
DEFAULT_CONCURRENCY: Final[int] = 10
#: Türkçe + İngilizce doğrulama konu filtresi (app'in gönderdiği mail hangi dilde olursa yakalar).
DEFAULT_SUBJECT_REGEX: Final[str] = r"(doğrula|dogrula|verify|aktivasyon|activat)"


# --------------------------------------------------------------------------------------
# .env support (optional dependency: python-dotenv, with a built-in fallback parser)
# --------------------------------------------------------------------------------------
def load_env_file(path: Optional[Path]) -> None:
    """Populate ``os.environ`` from a .env file without overwriting existing variables.

    ``IMAP_PASSWORD`` and friends usually live in ``.env``: this lets the harness be started
    with a bare command line in CI while keeping secrets out of the shell history.
    """
    candidate = Path(path) if path else REPO_ROOT / ".env"
    if not candidate.exists():
        return
    try:  # pragma: no cover - prefer the real parser when available
        from dotenv import load_dotenv

        load_dotenv(candidate, override=False)
        return
    except Exception:  # noqa: BLE001 - fall back to the built-in parser
        pass
    for raw_line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_first(*names: str) -> Optional[str]:
    """Return the first non-empty environment variable from *names*."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


# --------------------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------------------
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_harvest.py",
        description=(
            "Sign-up + verification-mail load-test harness (owned/authorised targets only). "
            "Drives waft.Orchestrator or the `python -m waft` CLI with 10 parallel contexts."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    data = parser.add_argument_group("data & target")
    data.add_argument("--data", type=Path, default=KIT_DIR / "targets.xlsx",
                      help="Excel/JSON data source with the sign-up rows.")
    data.add_argument("--base-url", default=None,
                      help="Rewrite every row's target_url to this host (staging <-> localhost switch).")
    data.add_argument("--scope", type=Path, default=KIT_DIR / "authorized_hosts.txt",
                      help="Scope file: hosts you are allowed to test.")
    data.add_argument("--allow-host", action="append", default=[],
                      help="Extra allowed host (repeatable). Needs --i-am-authorized.")
    data.add_argument("--i-am-authorized", action="store_true",
                      help="Assert written authorisation for hosts missing from the scope file.")

    load = parser.add_argument_group("load profile")
    load.add_argument("--contexts", type=int, default=DEFAULT_CONTEXTS,
                      help="Isolated browser contexts (= simulated users).")
    load.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                      help="Contexts running in parallel.")
    load.add_argument("--rate-limit", type=float, default=5.0,
                      help="Global navigations/second cap - protects the target from a self-DDoS.")
    load.add_argument("--retries", type=int, default=1, help="Retry attempts per target (retryable errors).")
    load.add_argument("--iterations", type=int, default=None, help="Repeat every row N times (optional).")
    load.add_argument("--fail-fast", action="store_true", help="Abort the run on the first failure.")
    load.add_argument("--no-humanize", action="store_true", help="Disable human-like typing (faster runs).")

    proxy = parser.add_argument_group("proxy rotation")
    proxy.add_argument("--proxies", type=Path, default=REPO_ROOT / "proxies.txt",
                       help="Proxy list file; one proxy per line (user:pass@host:port, socks5://…).")
    proxy.add_argument("--proxy-mode", default="auto", choices=["auto", "require", "off"],
                       help="auto=use when the file has entries, require=fail without, off=never.")
    proxy.add_argument("--no-proxy", action="store_true", help="Shorthand for --proxy-mode off.")

    capture = parser.add_argument_group("traffic capture & stealth")
    capture.add_argument("--stealth", dest="stealth", action="store_true", default=True,
                         help="Enable the stealth layer (webdriver/canvas/WebGL/WebRTC).")
    capture.add_argument("--no-stealth", dest="stealth", action="store_false", help="Disable stealth.")
    capture.add_argument("--verify-stealth", dest="verify_stealth", action="store_true", default=True,
                         help="Assert stealth invariants after every navigation.")
    capture.add_argument("--no-verify-stealth", dest="verify_stealth", action="store_false",
                         help="Skip the verification pass.")
    capture.add_argument("--capture-har", dest="capture_har", action="store_true", default=True,
                         help="Write a redacted HAR per context for API analysis.")
    capture.add_argument("--no-capture-har", dest="capture_har", action="store_false", help="No HAR.")
    capture.add_argument("--log-network", dest="log_network", action="store_true", default=True,
                         help="Log every request/response (terminal + network.jsonl).")
    capture.add_argument("--no-network-log", dest="log_network", action="store_false",
                         help="Disable traffic logs.")
    capture.add_argument("--captcha-action", choices=["skip", "error", "continue"], default="skip",
                         help="'skip' marks only the affected target as blocked_captcha and moves on.")
    capture.add_argument("--trace", choices=["on", "off", "on-failure", "retain-on-failure"],
                         default="on-failure", help="Playwright trace mode (.zip).")
    capture.add_argument("--devices", default="random", help="Device profile spec (waft.config.DEVICE_PROFILES).")
    capture.add_argument("--locale", default="tr-TR,en-US", help="Locales cycled across contexts.")
    capture.add_argument("--timezone", default="Europe/Istanbul", help="Timezone id or country code.")

    imap = parser.add_argument_group("verification mail (IMAP)")
    imap.add_argument("--imap", dest="imap", action="store_true", default=True,
                      help="Harvest the verification mail over IMAP (default on).")
    imap.add_argument("--no-imap", dest="imap", action="store_false", help="Skip the IMAP step.")
    imap.add_argument("--imap-host", default=None, help="IMAP host (falls back to IMAP_HOST).")
    imap.add_argument("--imap-port", type=int, default=None, help="IMAP port (993 SSL / 1430 Mailpit / 143 STARTTLS).")
    imap.add_argument("--imap-user", default=None, help="IMAP user (falls back to IMAP_USER).")
    imap.add_argument("--imap-password", default=None, help="IMAP password (falls back to IMAP_PASSWORD).")
    imap.add_argument("--imap-mailbox", default="INBOX", help="Mailbox to poll.")
    imap.add_argument("--imap-subject-regex", default=DEFAULT_SUBJECT_REGEX,
                      help="Subject filter used to pick the verification mail.")
    imap.add_argument("--imap-sender", default=None, help="Only accept mail from this sender (substring).")
    imap.add_argument("--imap-no-ssl", action="store_true",
                      help="Plain IMAP without TLS (Mailpit/devmail).")
    imap.add_argument("--imap-starttls", action="store_true", help="STARTTLS on port 143 instead of SSL.")
    imap.add_argument("--imap-timeout", type=float, default=None, help="Seconds to wait for each mail.")

    runtime = parser.add_argument_group("runtime & output")
    runtime.add_argument("--mode", choices=["orchestrator", "cli"], default="orchestrator",
                         help="orchestrator=in-process API, cli=spawn `python -m waft` as a subprocess.")
    runtime.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env", help=".env file to load first.")
    runtime.add_argument("--artifacts", type=Path, default=REPO_ROOT / "artifacts", help="Artifacts root.")
    runtime.add_argument("--log-level", default="INFO", help="Console log level.")
    runtime.add_argument("--probe-selectors", dest="probe_selectors", action="store_true", default=True,
                         help="Probe qa-kit/selectors.json in a real browser before the run.")
    runtime.add_argument("--no-probe-selectors", dest="probe_selectors", action="store_false",
                         help="Reuse the existing selectors.resolved.json.")
    runtime.add_argument("--dry-run", action="store_true", help="Pre-flight + plan only.")
    runtime.add_argument("--print-command", action="store_true",
                         help="Print the equivalent `python -m waft …` command and exit.")
    runtime.add_argument("--no-color", action="store_true", help="Disable ANSI colours.")
    return parser.parse_args(argv)


# --------------------------------------------------------------------------------------
# argv translation -> run_regression.py
# --------------------------------------------------------------------------------------
def build_regression_argv(args: argparse.Namespace) -> list[str]:
    """Translate harvest arguments into ``run_regression.py`` flags (single source of truth)."""
    argv: list[str] = [
        "--data", str(args.data),
        "--scope", str(args.scope),
        "--contexts", str(args.contexts),
        "--concurrency", str(args.concurrency),
        "--mode", args.mode,
        "--artifacts", str(args.artifacts),
        "--log-level", args.log_level,
        "--captcha-action", args.captcha_action,
        "--trace", args.trace,
        "--devices", args.devices,
        "--locale", args.locale,
        "--timezone", args.timezone,
        "--retries", str(args.retries),
        "--rate-limit", str(args.rate_limit),
    ]
    if args.base_url:
        argv += ["--base-url", args.base_url]
    for host in args.allow_host:
        argv += ["--allow-host", host]
    if args.i_am_authorized:
        argv += ["--i-am-authorized"]
    if args.iterations:
        argv += ["--iterations", str(args.iterations)]
    if args.fail_fast:
        argv += ["--fail-fast"]
    if args.no_humanize:
        argv += ["--no-humanize"]
    if args.stealth:
        argv += ["--stealth"]
    if args.verify_stealth:
        argv += ["--verify-stealth"]
    if args.capture_har:
        argv += ["--capture-har"]
    if args.log_network:
        argv += ["--log-network"]
    # Proxy policy: --no-proxy wins, otherwise pass the list through.
    if args.no_proxy or args.proxy_mode == "off":
        argv += ["--proxy-mode", "off"]
    else:
        argv += ["--proxies", str(args.proxies), "--proxy-mode", args.proxy_mode]
    # IMAP: explicit flag + settings resolved from CLI, then env (fallback), then .env.
    if args.imap:
        argv += ["--imap"]
        host = args.imap_host or env_first("IMAP_HOST", "WAFT_IMAP_HOST")
        user = args.imap_user or env_first("IMAP_USER", "WAFT_IMAP_USER", "IMAP_USERNAME")
        password = args.imap_password or env_first("IMAP_PASSWORD", "WAFT_IMAP_PASSWORD")
        port = args.imap_port or (int(env_first("IMAP_PORT") or 0) or None)
        if host:
            argv += ["--imap-host", host]
        if port:
            argv += ["--imap-port", str(port)]
        if user:
            argv += ["--imap-user", user]
        if password:
            argv += ["--imap-password", password]
        if args.imap_mailbox:
            argv += ["--imap-mailbox", args.imap_mailbox]
        if args.imap_subject_regex:
            argv += ["--imap-subject-regex", args.imap_subject_regex]
        if args.imap_sender:
            argv += ["--imap-sender", args.imap_sender]
        if args.imap_timeout:
            argv += ["--imap-timeout", str(args.imap_timeout)]
        # TLS switches: local test servers speak plain IMAP; production uses implicit SSL.
        if args.imap_no_ssl or (env_first("IMAP_SSL") or "").lower() in {"false", "0", "no"}:
            argv += ["--imap-no-ssl"]
        elif args.imap_starttls:
            argv += ["--imap-starttls"]
    else:
        argv += ["--no-imap"]
    if not args.probe_selectors:
        argv += ["--no-probe-selectors"]
    if args.dry_run:
        argv += ["--dry-run"]
    if args.print_command:
        argv += ["--print-command"]
    if args.no_color:
        argv += ["--no-color"]
    return argv


def preflight_summary(args: argparse.Namespace) -> list[str]:
    """Return human-readable warnings about the load profile before anything is launched."""
    warnings: list[str] = []
    if args.contexts < 1 or args.concurrency < 1:
        warnings.append("--contexts and --concurrency must be >= 1")
    if args.concurrency > args.contexts:
        warnings.append(f"--concurrency ({args.concurrency}) > --contexts ({args.contexts}): extra slots stay idle")
    if not args.no_proxy and args.proxy_mode != "off" and not Path(args.proxies).exists():
        warnings.append(f"proxy list {args.proxies} not found - the run continues without proxies")
    if args.imap and not (args.imap_host or env_first("IMAP_HOST", "WAFT_IMAP_HOST")):
        warnings.append("IMAP is on but no host given (--imap-host / IMAP_HOST / .env): verification rows will fail")
    if args.rate_limit is None:
        warnings.append("no --rate-limit set: 10 parallel contexts can hammer a small target - 5 nav/s is a sane cap")
    return warnings


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_file(args.env_file)

    warnings = preflight_summary(args)
    for warning in warnings:
        print(f"⚠ {warning}")
    if warnings and any("must be >= 1" in item for item in warnings):
        return 2

    regression_argv = build_regression_argv(args)
    print("→ profile: "
          f"{args.contexts} context(s) / {args.concurrency} parallel | mode={args.mode} | "
          f"proxy={'off' if args.no_proxy else args.proxy_mode} | imap={'on' if args.imap else 'off'} | "
          f"captcha={args.captcha_action}")

    try:
        import run_regression  # local sibling module
    except ImportError as exc:  # pragma: no cover - packaging problem
        print(f"✖ qa-kit/run_regression.py could not be imported: {exc}", file=sys.stderr)
        return 2

    try:
        return int(run_regression.main(regression_argv))
    except KeyboardInterrupt:  # pragma: no cover - graceful ctrl+c
        print("\ninterrupted - partial artifacts were kept", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
```

Aynı klasörde bulunması gereken kardeş dosyalar (hepsi teslim edildi):
`run_regression.py` (kapsam guard'ı + pre-flight + selector probe + `run_manifest.json`),
`selector_resolver.py`, `selectors.json`, `authorized_hosts.txt`, `devmail.py`.

---

## 4) Terminal çalıştırma komutu

### 4.a Yerel, uçtan uca (proxy'siz; devmail + demo uygulaması)

```bash
# terminal 1 — yerel posta sunucusu (SMTP 1025 / IMAP 1430)
python qa-kit/devmail.py --smtp-port 1025 --imap-port 1430 --verbose

# terminal 2 — test edilecek uygulama (kendi staging'iniz varsa atlayın)
python examples/demo_site.py --port 8080 --quiet \
  --smtp-host 127.0.0.1 --smtp-port 1025 --public-url http://127.0.0.1:8080

# terminal 3 — koşu: 10 context / 10 paralel
python qa-kit/run_harvest.py \
  --data qa-kit/targets.xlsx --base-url http://127.0.0.1:8080 \
  --contexts 10 --concurrency 10 --no-proxy \
  --imap-host 127.0.0.1 --imap-port 1430 --imap-user devmail --imap-password devmail --imap-no-ssl \
  --no-color
```

### 4.b Staging + proxy rotasyonu + gerçek IMAP (istenen tam bayrak seti)

```bash
export IMAP_PASSWORD='<gmail uygulama şifresi>'
python qa-kit/run_harvest.py \
  --data qa-kit/targets.xlsx --base-url https://staging.sirketiniz.com/register \
  --contexts 10 --concurrency 10 \
  --proxies proxies.txt --proxy-mode auto \
  --imap --imap-host imap.gmail.com --imap-port 993 --imap-user sizin.hesabiniz@gmail.com \
  --imap-subject-regex "(doğrula|dogrula|verify|aktivasyon|activat)" \
  --stealth --verify-stealth --capture-har --log-network --trace on-failure \
  --captcha-action skip --rate-limit 5 --no-color

# alt süreç modu + eşdeğer CLI komutunu yazdır (runbook/CI için)
python qa-kit/run_harvest.py --mode cli --print-command \
  --data qa-kit/targets.xlsx --base-url https://staging.sirketiniz.com/register --no-proxy
```

### 4.c Saf WAFT CLI eşdeğeri (wrapper olmadan)

```bash
#  EŞDEĞER SAF CLI KOMUTU (wrapper olmadan, kopyala-çalıştır)
#
#  python -m waft \
#    --data qa-kit/targets.xlsx \
#    --contexts 10 --concurrency 10 \
#    --proxy-file proxies.txt --proxy-mode auto \
#    --selectors qa-kit/selectors.resolved.json \
#    --stealth --verify-stealth \
#    --capture-har --log-network \
#    --imap --imap-host 127.0.0.1 --imap-port 1430 --imap-user mailpit --imap-password mailpit \
#    --imap-subject-regex "(doğrula|dogrula|verify|aktivasyon|activat)" \
#    --captcha-action skip \
#    --trace on-failure \
#    --rate-limit 5 --retries 1 \
#    --artifacts artifacts --log-level INFO
#
#  Not: --selectors dosyası yoksa şu komutla üretin:
#    python qa-kit/selector_resolver.py --data qa-kit/targets.xlsx
# =============================================================================
```

---

## 5) Doğrulama sonuçları (bu repoda gerçekten çalıştırıldı)

```text
WAFT run run-20260928-163417-3da63d PASSED ✅ | 120/120 target(s) ok (100.0%) | 146.1s
  contexts      : 10 ok / 0 failed (of 10)     ← 10 izole bağlam, 10 paralel
  targets       : 120 run, 120 ok, 0 failed    ← 12 satır × 10 context
  steps         : 960 ok / 0 failed            ← navigate · form-scan · form-fill · submit ·
                                                  outcome · verification-link · email-verification ·
                                                  network-traffic
  e-mail verif. : 120 ok / 0 failed            ← gerçek SMTP + gerçek IMAP (mock yok)
  api endpoints : 2
```

Örnek `run.json` kaydı (ilk hedef):

```json
{"status": "ok",
  "steps": ["navigate", "form-scan", "form-fill", "submit", "outcome",
             "verification-link", "email-verification", "network-traffic"],
  "verification": {"method": "link",
                    "link": "http://127.0.0.1:8080/verify?token=tok-761889",
                    "matched_subject": "Hesabınızı doğrulayın (verify your account)",
                    "success": true, "waited_ms": 965.0},
  "screenshots": 5}
```

Artefaktlar: `artifacts/<run-id>/{run.json, summary.md, results.csv, contexts.csv, junit.xml,
endpoints.json}` + `contexts/<ctx>/{screenshots/, har/, network.jsonl, console.jsonl,
network_summary.json, summary.json, *-storage.json}` + `artifacts/run_manifest.json`
(parametreler, girdi SHA-256'ları, eşdeğer CLI komutu → yeniden üretilebilirlik).

Negatif testler de doğrulandı:

* doğrulama maili gelmezse 45 s'de temiz `VerificationError` (sonsuz bekleme yok),
* kapsam dışı host'ta `--i-am-authorized` verilse bile **exit 2** (üçüncü taraf platform reddi),
* `--captcha-action skip` ile CAPTCHA yalnız ilgili hedefi `blocked` işaretler, paket devam eder,
* `--rate-limit 5` ile hedefe saniyede en fazla 5 navigasyon gider (self-DDoS koruması).

---

## 6) Yasal / kapsam

Sadece sahibi olduğun veya yazılı izin aldığın sistemleri test et: kendi staging'in, müşteri test
ortamı, bir sağlayıcının resmî sandbox'ı, bug-bounty kapsamı. Üçüncü taraf ödül/mikro görev
platformlarında otomatik kayıt yük testi değildir; entegrasyon testi orada ancak resmî API/sandbox
+ yazılı izinle yapılır. `qa-kit/authorized_hosts.txt` bu sınırı kod tarafında zorunlu kılar.
