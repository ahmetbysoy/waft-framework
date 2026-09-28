# OFFERWALL KİTİ — Teslim Paketi (kayıt + anket + e-posta doğrulama yük/regresyon testi)

Beş artefaktın tamamı, **kopyala-çalıştır** halde. Çalıştırılabilir kaynaklar
`qa-kit/offerwall/` altında; bu dosya onların indeksi + gömülü içeriğidir.

**Kapsam (artefaktların hedefi):** kendi uygulamanız / staging / pre-prod / size verilmiş
resmî sandbox. Üçüncü taraf ödül-mikro görev platformları (timewall.io, jumptask.io, freecash,
swagbucks, offertoro, adgate, ayetstudios, bitlabs, cpagrip, lootably …) `run_regression.py`
ve bu kitin kapsam kontrolü tarafından **reddedilir**: orada 10 hesapla otomatik kayıt açmak,
doğrulama maillerini toplamak ve arka plan API'sini çekmek yük testi değil, dolandırıcılıktır.
Kiti kendi hedefinize yönlendirmek için `--base-url` kullanın ve host'u
`qa-kit/authorized_hosts.txt` dosyasına ekleyin.

| # | Artefakt | Dosya |
|---|---|---|
| 1 | Gmail hesap havuzu + context'e atayan fonksiyon | `credentials.example.json`, `account_pool.py` |
| 2 | `targets_offerwall.xlsx` şeması + 6 örnek satır | `make_targets_offerwall.py` → `targets_offerwall.xlsx` |
| 3 | `selectors_offerwall.json` (yedekli CSS+XPath) | `selectors_offerwall.json` (resolver: `../selector_resolver.py`) |
| 4 | `run_offerwall.py` (Ana wrapper) | `run_offerwall.py` |
| 5 | Terminal komutu + kurulum | bu dosyanın 5. bölümü |

---

## ARTEFAKT 1 — `credentials.json` (hesap havuzu) + context atama fonksiyonu

**ÖNEMLİ:** `password` alanına Gmail'in normal parolası **değil**, 2 adımlı doğrulama açıkken
üretilen 16 haneli **Uygulama Şifresi (App Password)** yazılır
(Google Hesabı → Güvenlik → 2 Adımlı Doğrulama → Uygulama şifreleri → "Posta"/"Diğer (WAFT)").
Google, normal parolayla IMAP LOGIN'i reddeder. Dosyayı ASLA git'e eklemeyin
(`.gitignore`: `credentials*.json`).

```json
[
  {
    "_comment": [
      "Gmail Hesap Havuzu — şablon. Bu dosyayı 'credentials.json' olarak kopyalayıp DOLDURUN.",
      "ÖNEMLİ: 'password' alanına Gmail'in NORMAL parolası DEĞİL, 2 adımlı doğrulama açıkken",
      "üretilen 16 haneli 'Uygulama Şifresi' (App Password) yazılır:",
      "  Google Hesabı > Güvenlik > 2 Adımlı Doğrulama > Uygulama şifreleri > 'Posta' + 'Diğer (WAFT)'.",
      "IMAP, Gmail'de yalnızca uygulama şifresi ile çalışır; normal parola ile LOGIN reddedilir.",
      "Her hesabın IMAP'i ayrı bir gelen kutusudur; WAFT doğrulama mailini 'TO' filtresiyle bulur.",
      "Bu dosyayı asla sürüm kontrolüne (git) eklemeyin: .gitignore'da 'credentials*.json' deseni var.",
      "KAPSAM: bu hesaplar yalnızca SAHİBİ OLDUĞUNUZ/yazılı izin aldığınız bir uygulamanın kayıt",
      "akışını test etmek için kullanılır. Üçüncü taraf ödül/offerwall platformlarında otomatik",
      "hesap açmak için kullanılamaz (fraud/abuse) — kapsam kontrolü kod tarafında zorunludur."
    ]
  },
  {"email": "hesap1@gmail.com", "password": "AP_PASSWORD_1", "imap_host": "imap.gmail.com", "imap_port": 993, "enabled": true, "note": "test hesabı 1"},
  {"email": "hesap2@gmail.com", "password": "AP_PASSWORD_2", "imap_host": "imap.gmail.com", "imap_port": 993, "enabled": true, "note": "test hesabı 2"},
  {"email": "hesap3@gmail.com", "password": "AP_PASSWORD_3", "imap_host": "imap.gmail.com", "imap_port": 993, "enabled": true, "note": "test hesabı 3"},
  {"email": "hesap4@gmail.com", "password": "AP_PASSWORD_4", "imap_host": "imap.gmail.com", "imap_port": 993, "enabled": true, "note": "test hesabı 4"},
  {"email": "hesap5@gmail.com", "password": "AP_PASSWORD_5", "imap_host": "imap.gmail.com", "imap_port": 993, "enabled": true, "note": "test hesabı 5"},
  {"email": "hesap6@gmail.com", "password": "AP_PASSWORD_6", "imap_host": "imap.gmail.com", "imap_port": 993, "enabled": true, "note": "test hesabı 6"},
  {"email": "hesap7@gmail.com", "password": "AP_PASSWORD_7", "imap_host": "imap.gmail.com", "imap_port": 993, "enabled": true, "note": "test hesabı 7"},
  {"email": "hesap8@gmail.com", "password": "AP_PASSWORD_8", "imap_host": "imap.gmail.com", "imap_port": 993, "enabled": true, "note": "test hesabı 8"},
  {"email": "hesap9@gmail.com", "password": "AP_PASSWORD_9", "imap_host": "imap.gmail.com", "imap_port": 993, "enabled": true, "note": "test hesabı 9"},
  {"email": "hesap10@gmail.com", "password": "AP_PASSWORD_10", "imap_host": "imap.gmail.com", "imap_port": 993, "enabled": true, "note": "test hesabı 10"}
]
```

Context'e atama fonksiyonu (**her context'e farklı hesap**, round-robin):

```python
def account_for_context(pool: AccountPool, context_index: int) -> Account:
    """context i -> pool.accounts[i % len(pool)]"""
    return pool.for_context(context_index)
```

Tam tipli yükleyici + doğrulama + maskeleme (`account_pool.py`, tam kaynak):

```python
#!/usr/bin/env python3
"""``account_pool.py`` - typed loader + per-context assignment for the Gmail/IMAP account pool.

Design notes
------------
* Two input formats are accepted, because operations teams keep credentials in both shapes:

  1. **JSON** (``credentials.json``) - the documented format::

        [
          {"email": "hesap1@gmail.com", "password": "APP_PASSWORD_1",
           "imap_host": "imap.gmail.com", "imap_port": 993, "enabled": true}
        ]

     Keys starting with ``_`` (``_comment``) are ignored, so the file stays self-documenting.

  2. **Plain ``email:password`` lines** (``credentials.txt``) - handy for CI secret files::

        hesap1@gmail.com:APP_PASSWORD_1
        # yorum satırları ve boş satırlar yok sayılır

* ``password`` must be a Gmail **App Password** (16 characters, 2-step verification on), not the
  account password - Google rejects plain-password IMAP logins outright.
* :meth:`AccountPool.for_context` implements the **one account per context** rule: context *i*
  always gets ``accounts[i % len(accounts)]``, so a 10-context run with a 10-account pool is a
  1:1 mapping, and a 12-context run reuses the first two accounts.
* The pool never logs passwords: :meth:`AccountPool.describe` masks them.

Usage::

    pool = AccountPool.from_file("credentials.json")
    pool.validate(require_enabled=True)
    for index, account in pool.iter_with_contexts(contexts=10):
        print(index, account.email)
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Iterator, Optional, Sequence

logger = logging.getLogger("waft.offerwall.accounts")

#: Placeholder values that mean "you forgot to fill the file in".
PLACEHOLDER_PASSWORDS: Final[frozenset[str]] = frozenset(
    {
        "",
        "app_password",
        "ap_password_1",
        "sifre",
        "şifre",
        "password",
        "changeme",
        "your_app_password",
        "<uygulama-sifresi>",
    }
)


class AccountPoolError(RuntimeError):
    """Raised when the credential source is missing, unreadable or unusable."""


@dataclass(frozen=True, slots=True)
class Account:
    """One mailbox the harness may drive a browser context with."""

    email: str
    password: str
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    enabled: bool = True
    note: str = ""

    @property
    def is_gmail(self) -> bool:
        return self.email.lower().endswith(("@gmail.com", "@googlemail.com"))

    @property
    def masked_password(self) -> str:
        """``APP_*** (17 chars)`` - safe for logs, reports and screenshots."""
        if not self.password:
            return "<empty>"
        keep = self.password[:3] if len(self.password) > 6 else ""
        return f"{keep}***({len(self.password)} chars)"

    def to_dict(self, *, mask: bool = True) -> dict[str, Any]:
        return {
            "email": self.email,
            "password": self.masked_password if mask else self.password,
            "imap_host": self.imap_host,
            "imap_port": self.imap_port,
            "enabled": self.enabled,
            "note": self.note,
        }

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "Account":
        email = str(payload.get("email") or "").strip()
        if not email:
            raise AccountPoolError("account entry without an 'email' field")
        return cls(
            email=email,
            password=str(payload.get("password") or ""),
            imap_host=str(payload.get("imap_host") or "imap.gmail.com"),
            imap_port=int(payload.get("imap_port") or 993),
            enabled=bool(payload.get("enabled", True)),
            note=str(payload.get("note") or ""),
        )


@dataclass(slots=True)
class AccountPool:
    """Ordered, de-duplicated pool of accounts with context assignment helpers."""

    accounts: list[Account] = field(default_factory=list)
    source: Optional[Path] = None

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_file(cls, path: str | Path) -> "AccountPool":
        """Load a pool from ``credentials.json`` (JSON array/object) or an ``email:password`` file."""
        source = Path(path).expanduser()
        if not source.exists():
            raise AccountPoolError(f"credential file not found: {source}")
        try:
            raw = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise AccountPoolError(f"credential file unreadable: {source}: {exc}") from exc

        stripped = raw.lstrip()
        if stripped.startswith("[") or stripped.startswith("{"):
            pool = cls(accounts=cls._parse_json(raw, source), source=source)
        else:
            pool = cls(accounts=cls._parse_lines(raw, source), source=source)

        deduped: dict[str, Account] = {}
        for account in pool.accounts:
            key = account.email.lower()
            if key in deduped:
                logger.warning("duplicate account for %s ignored (%s)", account.email, source.name)
                continue
            deduped[key] = account
        pool.accounts = list(deduped.values())
        logger.info("account pool loaded from %s: %d account(s)", source, len(pool.accounts))
        return pool

    @staticmethod
    def _parse_json(raw: str, source: Path) -> list[Account]:
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AccountPoolError(f"{source}: invalid JSON ({exc})") from exc
        entries: Sequence[Any]
        if isinstance(payload, dict):
            entries = payload.get("accounts") or payload.get("credentials") or [payload]
        elif isinstance(payload, list):
            entries = payload
        else:  # pragma: no cover - defensive
            raise AccountPoolError(f"{source}: expected a JSON array or object")

        accounts: list[Account] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if any(str(key).startswith("_") for key in entry) and not entry.get("email"):
                continue  # documentation-only entry (_comment block)
            accounts.append(Account.from_mapping(entry))
        return accounts

    @staticmethod
    def _parse_lines(raw: str, source: Path) -> list[Account]:
        accounts: list[Account] = []
        for number, line in enumerate(raw.splitlines(), start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if "@" not in text or ":" not in text:
                logger.warning("%s:%d skipped (expected 'email:password')", source.name, number)
                continue
            email, _, password = text.partition(":")
            accounts.append(Account(email=email.strip(), password=password.strip()))
        return accounts

    # ------------------------------------------------------------------ helpers
    def __len__(self) -> int:
        return len(self.accounts)

    def __iter__(self) -> Iterator[Account]:
        return iter(self.accounts)

    @property
    def emails(self) -> list[str]:
        return [account.email for account in self.accounts]

    def enabled(self) -> list[Account]:
        return [account for account in self.accounts if account.enabled]

    def for_context(self, index: int) -> Account:
        """Return the account pinned to context *index* (round-robin over the pool)."""
        usable = self.accounts or []
        if not usable:
            raise AccountPoolError("account pool is empty - cannot assign an account to a context")
        return usable[index % len(usable)]

    def iter_with_contexts(self, contexts: int, *, enabled_only: bool = True) -> Iterator[tuple[int, Account]]:
        """Yield ``(context_index, account)`` pairs for *contexts* contexts."""
        usable = self.enabled() if enabled_only else list(self.accounts)
        if not usable:
            raise AccountPoolError("no enabled accounts available")
        for index in range(max(1, contexts)):
            yield index, usable[index % len(usable)]

    def validate(self, *, require_enabled: bool = True) -> list[str]:
        """Return warnings; raise :class:`AccountPoolError` for anything fatal.

        Fatal: empty pool, malformed addresses.
        Warning: placeholder password (file not filled in), mixed IMAP hosts/ports, non-Gmail
        domain (fine for a local mail server such as Mailpit/devmail).
        """
        if not self.accounts:
            raise AccountPoolError("account pool is empty")
        warnings: list[str] = []
        usable = self.enabled() if require_enabled else list(self.accounts)
        if not usable:
            raise AccountPoolError("all accounts are disabled (enabled=false)")

        for account in usable:
            if "@" not in account.email or account.email.startswith("@") or account.email.endswith("@"):
                raise AccountPoolError(f"malformed e-mail address: {account.email!r}")
            if account.password.strip().lower() in PLACEHOLDER_PASSWORDS:
                warnings.append(
                    f"{account.email}: password looks like a placeholder - put a Gmail APP PASSWORD "
                    "(16 chars, 2-step verification) into the credential file"
                )

        hosts = {(account.imap_host, account.imap_port) for account in usable}
        if len(hosts) > 1:
            warnings.append(
                "accounts point at different IMAP servers "
                + ", ".join(f"{host}:{port}" for host, port in sorted(hosts))
                + " - WAFT polls one IMAP server per run, the first account's settings are used"
            )
        if not any(account.is_gmail for account in usable):
            warnings.append("no @gmail.com account in the pool (fine for Mailpit/devmail, unusual for production)")
        return warnings

    def describe(self, *, limit: int = 12) -> str:
        """Multi-line, password-masked description of the pool (for logs/manifests)."""
        lines = [f"account pool: {len(self.accounts)} account(s) from {self.source or '<memory>'}"]
        for position, account in enumerate(self.accounts[:limit], start=1):
            state = "on " if account.enabled else "off"
            lines.append(
                f"  [{position:02d}] {state} {account.email:38s} imap={account.imap_host}:{account.imap_port} "
                f"pw={account.masked_password}"
            )
        if len(self.accounts) > limit:
            lines.append(f"  … {len(self.accounts) - limit} more")
        return "\n".join(lines)

    def first_imap_settings(self) -> tuple[str, int]:
        """Return ``(host, port)`` of the first enabled account (WAFT polls one server per run)."""
        usable = self.enabled() or self.accounts
        if not usable:
            raise AccountPoolError("account pool is empty")
        return usable[0].imap_host, usable[0].imap_port


# --------------------------------------------------------------------------------------
# smoke test:  python qa-kit/offerwall/account_pool.py credentials.example.json
# --------------------------------------------------------------------------------------
def _main(argv: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    path = Path(argv[1]) if len(argv) > 1 else Path(__file__).with_name("credentials.example.json")
    try:
        pool = AccountPool.from_file(path)
        warnings = pool.validate(require_enabled=False)
    except AccountPoolError as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return 2
    print(pool.describe())
    print("\ncontext assignments (contexts=10):")
    for index, account in pool.iter_with_contexts(contexts=10, enabled_only=False):
        print(f"  ctx-{index:02d} → {account.email}")
    for warning in warnings:
        print(f"⚠ {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
```

Kullanım:

```bash
python qa-kit/offerwall/account_pool.py qa-kit/offerwall/credentials.example.json
# ✔ account pool: 10 account(s) …  ctx-00 → hesap1@gmail.com … ctx-09 → hesap10@gmail.com
```

---

## ARTEFAKT 2 — `targets_offerwall.xlsx` şeması ve 6 örnek satır

İstenen kolonların tamamı + formun zorunlu alanları (WAFT tanımadığı kolonları **form verisi**
sayar, böylece Excel tek başına yeterlidir):

```text
target_url · scenario · email · password · first_name · last_name · success_selector ·
error_selector · requires_email_verification · verification_email · verification_subject_regex ·
submit_button_text · wait_after_submit_ms · password_confirm · country · terms ·
contact_consent · rating · frequency · category · comments · expect_error · name · tags · notes
```

`email`, `password`, `verification_email`, `password_confirm` hücreleri
`{email}` / `{password}` **yer tutucusudur**; `run_offerwall.py` bunları her context'in
hesabıyla doldurur (tek sayfa → 10 hesap × 6 satır = 60 hedef koşusu).

6 örnek satır (gerçek üretilmiş dosyadan):

```text
[1] register-form-submit      form-submit   /register                verify=false  submit="Hesap oluştur"
[2] register-email-verify     email-verify  /register                verify=true   submit="Hesap oluştur"
[3] survey-radio-textarea     form-submit   /survey/1                verify=false  submit="Anketi gönder"
[4] survey-select-email       form-submit   /survey/2                verify=false  submit="Anketi tamamla"
[5] register-premium-verify   email-verify  /register?plan=premium   verify=true   submit="Hesap oluştur"
[6] captcha-policy-check      form-submit   /captcha                 expect_error=true (CAPTCHA politikası)
```

Excel'e doğrudan yapıştırılabilir TSV (3 satır; `{...}` yer tutucuları korunur):

```tsv
target_url	scenario	email	password	first_name	last_name	success_selector	error_selector	requires_email_verification	verification_email	verification_subject_regex	submit_button_text	wait_after_submit_ms
http://127.0.0.1:8090/register	email-verify	{email}	{password}	Mehmet	Kaya	[data-testid='success']	[data-testid='error']	true	{email}	(doğrula|dogrula|verify|confirm|aktivasyon|activate)	Hesap oluştur	1500
http://127.0.0.1:8090/survey/1	form-submit	{email}	{password}	Zeynep	Demir	[data-testid='survey-complete']	[data-testid='error']	false			Anketi gönder	600
http://127.0.0.1:8090/register?plan=premium	email-verify	{email}	{password}	Elif	Celik	[data-testid='success']	[data-testid='error']	true	{email}	(doğrula|verify)	Hesap oluştur	1500
```

Üretici (tipli, parametrik; kendi hedefiniz için `--base-url`):

```python
#!/usr/bin/env python3
"""``make_targets_offerwall.py`` - generate ``targets_offerwall.xlsx`` for the offerwall kit.

Schema (exactly the columns requested for the offerwall/survey flow)
-------------------------------------------------------------------
============================  ==========================================================
Column                        Meaning
============================  ==========================================================
``target_url``                Form/survey page to open.
``scenario``                  ``form-submit`` or ``email-verify``.
``email``                     ``{email}`` placeholder -> filled per context from
                              ``credentials.json`` by ``run_offerwall.py``.
``password``                  ``{password}`` placeholder -> same mechanism.
``first_name`` / ``last_name`` Turkish + English sample names.
``success_selector``          CSS that proves the submit worked.
``error_selector``            CSS of the validation/error banner.
``requires_email_verification`` ``true`` -> poll IMAP after submit and follow the link.
``verification_email``        ``{email}`` placeholder (same mailbox as the account).
``verification_subject_regex`` Subject filter for the verification mail.
``submit_button_text``        Visible text of the submit button.
``wait_after_submit_ms``      Settling time after the click before outcome detection.
============================  ==========================================================

``{email}`` / ``{password}`` / ``{verification_email}`` are substituted by
``run_offerwall.py`` **per context**, so a single sheet drives 10 accounts x 6 rows.

Default target host is the bundled sandbox (``examples/offerwall_sandbox.py``); switch it to
your own application/staging with ``--base-url``.  Third-party offerwall platforms are **not**
valid targets for this kit - see ``qa-kit/README.md``.

Usage::

    python qa-kit/offerwall/make_targets_offerwall.py --base-url http://127.0.0.1:8090
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Final, Optional, Sequence

try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover - environment problem
    raise SystemExit("pandas is required: pip install -r requirements.txt") from exc

COLUMNS: Final[tuple[str, ...]] = (
    "target_url",
    "scenario",
    "email",
    "password",
    "first_name",
    "last_name",
    "success_selector",
    "error_selector",
    "requires_email_verification",
    "verification_email",
    "verification_subject_regex",
    "submit_button_text",
    "wait_after_submit_ms",
    # --- kayıt formunun zorunlu alanları + anket verileri: WAFT tanımadığı kolonları form verisi sayar
    "password_confirm",
    "country",
    "terms",
    "contact_consent",
    "rating",
    "frequency",
    "category",
    "comments",
    "expect_error",
    "name",
    "tags",
    "notes",
)

DEFAULT_SUBJECT_REGEX: Final[str] = r"(doğrula|dogrula|verify|confirm|aktivasyon|activate)"

#: (path, scenario, first, last, success, error, requires_verify, submit_text, wait_ms, name, extra_form_data, notes)
SAMPLE_ROWS: Final[tuple[tuple[str, str, str, str, str, str, str, str, int, str, dict[str, str], str], ...]] = (
    (
        "/register", "form-submit", "Ayse", "Yilmaz",
        "[data-testid='success']", "[data-testid='error']", "false",
        "Hesap oluştur", 800, "register-form-submit",
        {"password_confirm": "{password}", "country": "TR", "terms": "true", "newsletter": "false"},
        "Kayıt formu — e-posta/parola/ad/soyad/select/checkbox doldurma regresyonu.",
    ),
    (
        "/register", "email-verify", "Mehmet", "Kaya",
        "[data-testid='success']", "[data-testid='error']", "true",
        "Hesap oluştur", 1500, "register-email-verify",
        {"password_confirm": "{password}", "country": "TR", "terms": "true", "newsletter": "true"},
        "Kayıt + e-posta doğrulama: IMAP'ten link yakalanır ve yeni sekmede açılır.",
    ),
    (
        "/survey/1", "form-submit", "Zeynep", "Demir",
        "[data-testid='survey-complete']", "[data-testid='error']", "false",
        "Anketi gönder", 600, "survey-radio-textarea",
        {"frequency": "weekly", "category": "electronics", "comments": "Otomatik regresyon koşusu"},
        "Radyo + select + textarea içeren anket; çok alanlı form performansı.",
    ),
    (
        "/survey/2", "form-submit", "Can", "Sahin",
        "[data-testid='survey-complete']", "[data-testid='error']", "false",
        "Anketi tamamla", 600, "survey-select-email",
        {"rating": "5", "contact_consent": "true"},
        "Select + e-posta alanı + consent checkbox; hata yolu error_selector ile doğrulanır.",
    ),
    (
        "/register?plan=premium", "email-verify", "Elif", "Celik",
        "[data-testid='success']", "[data-testid='error']", "true",
        "Hesap oluştur", 1500, "register-premium-verify",
        {"password_confirm": "{password}", "country": "DE", "terms": "true"},
        "Varyant URL + doğrulama; parametreli sayfalarda seçici dayanıklılığı.",
    ),
    (
        "/captcha", "form-submit", "Burak", "Yildiz",
        "[data-testid='success']", "[data-testid='error']", "false",
        "Hesap oluştur", 500, "captcha-policy-check",
        {},
        "CAPTCHA sayfası: expect_error=true olduğu için 'captcha' adımı OK sayılır; bu bayrak "
        "kaldırılırsa hedef blocked_captcha olarak işaretlenir, koşu devam eder.",
    ),
)


def build_records(base_url: str) -> list[dict[str, Any]]:
    """Turn :data:`SAMPLE_ROWS` into sheet records with absolute URLs."""
    host = base_url.rstrip("/")
    records: list[dict[str, Any]] = []
    for path, scenario, first, last, success, error, verify, submit, wait_ms, name, extra, notes in SAMPLE_ROWS:
        wants_verification = verify.lower() == "true"
        record: dict[str, Any] = {
            "target_url": f"{host}{path}",
            "scenario": scenario,
            "email": "{email}",
            "password": "{password}",
            "first_name": first,
            "last_name": last,
            "success_selector": success,
            "error_selector": error,
            "requires_email_verification": verify,
            # IMPORTANT: only verification rows advertise a mailbox.  A non-empty
            # verification_email would make WAFT poll IMAP for every row - pointless waiting
            # (and timeouts) on rows whose flow never sends mail.
            "verification_email": "{email}" if wants_verification else "",
            "verification_subject_regex": DEFAULT_SUBJECT_REGEX if wants_verification else "",
            "submit_button_text": submit,
            "wait_after_submit_ms": wait_ms,
            "name": name,
            "tags": "offerwall,regression," + scenario,
            "notes": notes,
        }
        # Survey/registration specific form values (unknown columns are form data for WAFT).
        for key in ("password_confirm", "country", "terms", "contact_consent", "rating", "frequency", "category", "comments"):
            record[key] = extra.get(key, "")
        # WAFT's negative-test switch: "true" -> the expected outcome is a rejection/challenge page.
        record["expect_error"] = "true" if name == "captcha-policy-check" else ""
        records.append(record)
    return records


def write_workbook(records: Sequence[dict[str, Any]], path: Path) -> Path:
    """Write the target sheet plus a documentation sheet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(list(records), columns=list(COLUMNS))
    docs = pd.DataFrame(
        [
            ("target_url", "yes", "Açılacak form/anket sayfası (mutlak URL)."),
            ("scenario", "no", "form-submit | email-verify"),
            ("email", "yes", "{email} yer tutucusu — context'in hesabıyla doldurulur."),
            ("password", "yes", "{password} yer tutucusu — hesabın uygulama şifresi."),
            ("first_name", "no", "Ad (Türkçe/İngilizce örnekler)."),
            ("last_name", "no", "Soyad."),
            ("success_selector", "no", "Başarıyı kanıtlayan CSS."),
            ("error_selector", "no", "Hata/validasyon mesajının CSS'i."),
            ("requires_email_verification", "no", "true -> IMAP adımı çalışır."),
            ("verification_email", "no", "{email} yer tutucusu (aynı posta kutusu)."),
            ("verification_subject_regex", "no", "Konu filtresi (TR+EN)."),
            ("submit_button_text", "no", "Gönder butonunun görünen metni."),
            ("wait_after_submit_ms", "no", "Gönderim sonrası bekleme (ms)."),
            ("password_confirm", "no", "Parola tekrarı — {password} (zorunlu ise boş bırakılamaz)."),
            ("country", "no", "Kayıt formu select değeri (TR/DE/US/GB)."),
            ("terms", "no", "Zorunlu onay checkbox'ı (true/false)."),
            ("contact_consent", "no", "Anket iletişim izni checkbox'ı."),
            ("rating", "no", "Anket puanlama select değeri."),
            ("frequency", "no", "Anket radyo değeri (weekly/monthly/rarely)."),
            ("category", "no", "Anket kategori select değeri."),
            ("comments", "no", "Anket serbest metin alanı."),
            ("expect_error", "no", "true ise beklenen sonuç reddedilme/CAPTCHA sayfasıdır (negatif test)."),
            ("name", "no", "Raporlarda görünen satır adı."),
            ("tags", "no", "CI/rapor filtreleme etiketleri."),
            ("notes", "no", "İnsan okuyucu için not."),
        ],
        columns=["column", "required", "description"],
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="targets", index=False)
        docs.to_excel(writer, sheet_name="columns", index=False)
        for sheet_name, reference in (("targets", frame), ("columns", docs)):
            worksheet = writer.sheets[sheet_name]
            for position, column in enumerate(reference.columns, start=1):
                width = max(len(str(column)) + 2, *(len(str(value)) + 2 for value in reference[column]))
                worksheet.column_dimensions[worksheet.cell(row=1, column=position).column_letter].width = min(width, 58)
    return path


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="make_targets_offerwall.py",
        description="Generate targets_offerwall.xlsx (offerwall/survey flow) for WAFT.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8090",
                        help="Base URL of the system under test (bundled sandbox by default).")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("targets_offerwall.xlsx"),
                        help="Output .xlsx path.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    records = build_records(str(args.base_url))
    path = write_workbook(records, Path(args.out))
    print(f"✔ {path} written: {len(records)} row(s) -> {args.base_url}")
    print(f"  columns: {', '.join(COLUMNS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

```bash
python qa-kit/offerwall/make_targets_offerwall.py --base-url https://staging.sirketiniz.com
```

---

## ARTEFAKT 3 — `selectors_offerwall.json` (yedekli CSS + XPath kataloğu)

Alan başına **en az 3-6 yedekli** zincir (CSS önce, XPath sonra): email, password,
password_confirm, first_name, last_name, country, terms, anket alanları, submit, başarı/hata
mesajı, doğrulama sayfası ve **CAPTCHA iframe tespiti** (`recaptcha`/`hcaptcha`/`turnstile`).
Blok birleşimi: `host:port` → `host` → `*` (en özel kazanır). Resolver zincirleri gerçek
tarayıcıda dener, kazananı WAFT'ın anladığı kanonik forma çevirir:

```bash
python qa-kit/selector_resolver.py --data qa-kit/offerwall/targets_offerwall.xlsx \
  --catalogue qa-kit/offerwall/selectors_offerwall.json \
  --out qa-kit/offerwall/selectors.resolved.json \
  --report qa-kit/offerwall/selectors.resolved.report.json
```

Doğrulanmış çıktı:

```text
✔ 127.0.0.1:8090: 8 resolved, missing=['success','verified','error','captcha', …]
✔ selectors.resolved.json → {"email":"#register-email","password":"#register-password",
   "password_confirm":"#register-password-confirm","first_name":"#first-name",
   "last_name":"#last-name","country":"#country","terms":"#terms"}
```

(`success`/`error`/`captcha` **gönderim sonrası** elemanlardır; landing page'de bulunamazlar →
raporda "informational". Sonuç tespiti Excel'deki `success_selector`/`error_selector` ile yapılır.)

```json
{
  "_comment": [
    "Offerwall/survey form selector catalogue for the WAFT offerwall kit.",
    "Each field holds an ORDERED fallback chain (CSS first, then XPath, at least two entries).",
    "selector_resolver.py probes the chain in a real browser and writes selectors.resolved.json",
    "({data_key: selector}) which is passed to WAFT via --selectors.",
    "Blocks are merged: host:port (exact) wins over host (exact) wins over '*'.",
    "Add a block for YOUR host under 'hosts' - the '*' block is a generic safety net for modern",
    "sign-up forms (id/name/autocomplete/aria based, no vendor-specific markup)."
  ],
  "version": 1,
  "defaults": {
    "probe_timeout_ms": 10000,
    "require_visible": true
  },
  "hosts": {
    "127.0.0.1:8090": {
      "_comment": "Bundled offerwall sandbox (examples/offerwall_sandbox.py).",
      "email": [
        "#register-email",
        "input[name='email']",
        "input[type='email']",
        "[autocomplete='email']",
        "//input[@type='email']"
      ],
      "password": [
        "#register-password",
        "input[name='password']",
        "input[type='password']",
        "[autocomplete='new-password']",
        "//input[@type='password']"
      ],
      "password_confirm": [
        "#register-password-confirm",
        "input[name='password_confirm']",
        "input[name='password2']",
        "(//input[@type='password'])[2]"
      ],
      "first_name": [
        "#first-name",
        "input[name='first_name']",
        "[autocomplete='given-name']",
        "//label[contains(., 'Ad')]/following::input[1]"
      ],
      "last_name": [
        "#last-name",
        "input[name='last_name']",
        "[autocomplete='family-name']",
        "//label[contains(., 'Soyad')]/following::input[1]"
      ],
      "country": [
        "#country",
        "select[name='country']",
        "//select[@name='country']"
      ],
      "terms": [
        "#terms",
        "input[name='terms']",
        "input[type='checkbox'][required]",
        "//input[@type='checkbox'][@required]"
      ],
      "survey_frequency": [
        "input[name='frequency'][value='weekly']",
        "//input[@name='frequency'][@value='weekly']"
      ],
      "survey_category": [
        "#favourite-category",
        "select[name='category']",
        "//select[@name='category']"
      ],
      "survey_notes": [
        "#comments",
        "textarea[name='comments']",
        "//textarea"
      ],
      "survey_rating": [
        "#rating",
        "select[name='rating']",
        "//select[@name='rating']"
      ],
      "submit": [
        "[data-testid='register-submit']",
        "[data-testid='survey-submit']",
        "button[type='submit']",
        "input[type='submit']",
        "//button[@type='submit']"
      ],
      "success": [
        "[data-testid='success']",
        "[data-testid='survey-complete']",
        ".notice.success",
        "//*[contains(@class,'notice') and contains(@class,'success')]"
      ],
      "verified": [
        "[data-testid='verified']",
        ".notice.success",
        "//*[contains(text(), 'doğrulandı')]"
      ],
      "error": [
        "[data-testid='error']",
        ".notice.error",
        "//*[contains(@class,'notice') and contains(@class,'error')]"
      ],
      "captcha": [
        "iframe[src*='recaptcha']",
        ".g-recaptcha",
        "#captcha",
        "[data-testid='captcha']",
        "iframe[src*='hcaptcha']",
        "iframe[src*='turnstile']"
      ]
    },
    "*": {
      "_comment": "Generic fallbacks so the kit still works on 'your own app' without a host block.",
      "email": [
        "input[type='email']",
        "#email",
        "input[name='email']",
        "input[name='username']",
        "[autocomplete='email']",
        "//input[contains(@id,'email') or contains(@name,'email')]"
      ],
      "password": [
        "input[type='password']",
        "#password",
        "input[name='password']",
        "[autocomplete='new-password']",
        "//input[@type='password']"
      ],
      "password_confirm": [
        "input[name='password_confirm']",
        "input[name='password2']",
        "input[name='confirm_password']",
        "(//input[@type='password'])[2]"
      ],
      "first_name": [
        "input[name='first_name']",
        "input[name='firstName']",
        "[autocomplete='given-name']",
        "input[name='name']"
      ],
      "last_name": [
        "input[name='last_name']",
        "input[name='lastName']",
        "[autocomplete='family-name']",
        "input[name='surname']"
      ],
      "country": [
        "select[name='country']",
        "select[name='country_code']",
        "#country"
      ],
      "terms": [
        "input[type='checkbox'][required]",
        "input[name='terms']",
        "input[name='consent']",
        "#terms"
      ],
      "submit": [
        "button[type='submit']",
        "input[type='submit']",
        "[data-testid='submit']",
        "button:has-text('Gönder')",
        "button:has-text('Submit')",
        "//button[@type='submit']"
      ],
      "success": [
        "[data-testid='success']",
        ".alert-success",
        ".notice.success",
        "[role='alert'].success",
        "//*[contains(@class,'success')]"
      ],
      "verified": [
        "[data-testid='verified']",
        "text=doğrulandı",
        "text=verified",
        "//*[contains(text(),'verify')]"
      ],
      "error": [
        "[data-testid='error']",
        ".alert-danger",
        ".notice.error",
        "[role='alert'].error",
        "//*[contains(@class,'error')]"
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

---

## ARTEFAKT 4 — `run_offerwall.py` (ana wrapper, tam kaynak)

İşleyiş: `credentials.json` → her context'e bir hesap → `targets_offerwall.xlsx` yer tutucuları
hesapla doldurulur → `Config` (contexts=10, concurrency=5, stealth, verify_stealth,
proxy_file=proxies.txt, imap + TR/EN konu regex + 180 s timeout, capture_har, log_network,
captcha_action=skip, trace=on-failure, artifacts, retries=2, rate_limit=2.0) →
`AccountPinnedOrchestrator` (Orchestrator alt sınıfı; context i yalnız kendi hesabının
satırlarını koşar) → çıkış kodu korunur → `endpoints.json` + `run.json` okunup terminalde özet
basılır → `artifacts/offerwall_manifest.json` yazılır (parolalar maskeli).

```python
#!/usr/bin/env python3
"""``run_offerwall.py`` - account-pinned load-test / regression driver for a sign-up + survey flow.

What it does, in order
----------------------
1. **credentials.json** is read with :mod:`account_pool` (typed, de-duplicated, masked logging).
2. **targets_offerwall.xlsx** is loaded through WAFT's own data loader; every row keeps the
   ``{email}`` / ``{password}`` / ``{verification_email}`` placeholders.
3. **One account per context**: context *i* gets ``pool.accounts[i % len(pool)]`` and runs its own
   copy of the row set with the placeholders resolved - so 10 contexts + 10 accounts is a 1:1
   mapping and no account is used by two contexts at the same time.
4. **WAFT Config** is built from the requested profile: ``contexts=10``, ``concurrency=5``,
   ``stealth``, ``verify_stealth``, ``proxy_file='proxies.txt'``, ``imap`` with a TR/EN subject
   regex and 180 s timeout, ``capture_har``, ``log_network``, ``captcha_action='skip'``,
   ``trace='on-failure'``, ``artifacts='artifacts'``, ``retries=2``, ``rate_limit=2.0``.
5. ``AccountPinnedOrchestrator`` (a thin ``Orchestrator`` subclass) runs the contexts; its exit
   code is returned unchanged.
6. **endpoints.json** (plus ``run.json``) is read back and summarised on the terminal: discovered
   API endpoints, status codes, request counts, CAPTCHA-blocked targets, verification results.

Scope guard
-----------
The target URLs must belong to you or to an environment you are authorised to test (staging,
pre-prod, partner sandbox, local mock). ``run_regression.py``'s scope file/block-list is reused
here: third-party offerwall / micro-task / reward platforms (timewall.io, jumptask.io, …) are
rejected outright - automated sign-ups there are abuse, not load testing.

Usage
-----
Local end-to-end against the bundled sandbox (see qa-kit/README.md)::

    python qa-kit/offerwall/run_offerwall.py \
        --credentials qa-kit/offerwall/credentials.sandbox.json \
        --targets qa-kit/offerwall/targets_offerwall.xlsx \
        --orderwall-sandbox-off  # (example flags below)

    python qa-kit/offerwall/run_offerwall.py \
        --credentials qa-kit/offerwall/credentials.sandbox.json \
        --targets qa-kit/offerwall/targets_offerwall.xlsx \
        --imap-host 127.0.0.1 --imap-port 1430 --imap-ssl off \
        --proxy-mode off --contexts 10 --concurrency 5

Your own staging + Gmail app passwords::

    python qa-kit/offerwall/run_offerwall.py \
        --credentials credentials.json \
        --targets qa-kit/offerwall/targets_offerwall.xlsx \
        --base-url https://staging.sirketiniz.com --proxy-mode auto

Exit codes: 0 pass | 1 target failures | 2 usage/scope/credential error | 3 proxy | 130 interrupt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Final, Optional, Sequence

# --- make the package + sibling modules importable when run as a plain script --------------
_HERE: Final[Path] = Path(__file__).resolve().parent
_REPO: Final[Path] = _HERE.parent.parent
for _path in (str(_REPO), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from account_pool import Account, AccountPool, AccountPoolError  # noqa: E402  (local sibling)

from waft.config import Config  # noqa: E402
from waft.data_source import DataLoader  # noqa: E402
from waft.models import (  # noqa: E402
    STATUS_FAILED,
    STATUS_OK,
    ContextResult,
    TargetRow,
)
from waft.orchestrator import Orchestrator  # noqa: E402
from waft.utils import human_ms, mask_secret, truncate  # noqa: E402
from waft.engine import ContextEngine, ContextEngineFactory  # noqa: E402

logger = logging.getLogger("waft.offerwall")

#: Fields in the sheet that hold account placeholders, and how they are substituted.
PLACEHOLDER_FIELDS: Final[tuple[str, ...]] = ("email", "password", "verification_email")

DEFAULT_SUBJECT_REGEX: Final[str] = r"(doğrula|dogrula|verify|confirm|aktivasyon|activate)"


# ======================================================================================
# placeholder resolution
# ======================================================================================
def resolve_account_placeholders(text: Optional[str], account: Account) -> Optional[str]:
    """Replace ``{email}`` / ``{password}`` / ``{account_email}`` in *text* with account values."""
    if text is None:
        return None
    replacements = {
        "{email}": account.email,
        "{account_email}": account.email,
        "{password}": account.password,
        "{account_password}": account.password,
        "{{email}}": account.email,
        "{{password}}": account.password,
    }
    result = text
    for token, value in replacements.items():
        result = result.replace(token, value)
    return result


def bind_rows_to_account(template_rows: Sequence[TargetRow], account: Account, *, iteration_offset: int) -> list[TargetRow]:
    """Return a copy of *template_rows* with every account placeholder resolved.

    ``TargetRow`` is a dataclass, so :func:`dataclasses.replace` keeps every field we do not
    touch (selectors, steps, timeouts, tags …) exactly as the sheet defined it.  ``index`` is
    shifted per account so rows stay unique inside ``results.csv``/``run.json``.
    """
    bound: list[TargetRow] = []
    for position, row in enumerate(template_rows):
        form_data = {
            key: resolve_account_placeholders(str(value), account) if isinstance(value, str) else value
            for key, value in row.form_data.items()
        }
        wants_verification = bool(row.requires_email_verification) and row.scenario == "email-verify"
        bound.append(
            replace(
                row,
                index=iteration_offset + position + 1,
                form_data=form_data,
                # Defensive: a stray verification_email on a non-verification row would make WAFT
                # poll IMAP for a mail that never arrives.
                verification_email=(
                    resolve_account_placeholders(row.verification_email, account) if wants_verification else None
                ),
                verification_subject_regex=row.verification_subject_regex if wants_verification else None,
                name=(row.name or f"row-{row.index}") + f"@ctx-account-{account.email.split('@')[0]}",
                metadata={
                    **row.metadata,
                    "account_email": account.email,
                    "account_note": account.note,
                    "template_row_index": row.index,
                },
            )
        )
    return bound


# ======================================================================================
# orchestrator with per-context account pinning
# ======================================================================================
class AccountPinnedOrchestrator(Orchestrator):
    """``Orchestrator`` that gives every context **its own** account-bound row list.

    WAFT's stock behaviour runs the full row set inside *every* context.  For an account-pool
    test we need the opposite: context *i* must drive account *i* only.  The override below
    swaps ``engine.run_rows(self.rows)`` for ``engine.run_rows(self._rows_by_context[index])``
    and falls back to the stock behaviour (with a loud warning) if the parent signature ever
    changes - so an upgrade cannot silently corrupt the load profile.
    """

    VERSION: Final[str] = "1.0.0"

    def __init__(
        self,
        config: Config,
        *,
        pool: AccountPool,
        rows_by_context: Sequence[Sequence[TargetRow]],
    ) -> None:
        super().__init__(config)
        self.pool = pool
        self.rows_by_context: list[list[TargetRow]] = [list(rows) for rows in rows_by_context]
        self.account_binding_ok: bool = True
        self._checked_parent_signature: bool = False

    # ------------------------------------------------------------------ helpers
    def rows_for(self, index: int) -> list[TargetRow]:
        """Rows bound to context *index* (falls back to the global row set)."""
        if not self.rows_by_context:
            return list(self.rows)
        return self.rows_by_context[index % len(self.rows_by_context)]

    def prepare(self) -> None:  # type: ignore[override]
        """Run the stock preparation, then expose the *flattened* plan for reporting."""
        super().prepare()
        flat: list[TargetRow] = [row for rows in self.rows_by_context for row in rows]
        if flat:
            # Reporting/totals must reflect what is really executed (contexts x rows), not the
            # template sheet.
            self.rows = flat
        logger.info(
            "account pinning: %d context(s) x %d account(s) x %d template row(s) = %d planned target(s)",
            max(len(self.rows_by_context), 1),
            len(self.pool),
            len(self.rows_by_context[0]) if self.rows_by_context else 0,
            len(flat) or len(self.rows),
        )

    # ------------------------------------------------------------------ override
    async def _run_context(  # type: ignore[override]
        self,
        index: int,
        engine_factory: ContextEngineFactory,
        semaphore: asyncio.Semaphore,
    ) -> ContextResult:
        """Copy of the parent routine with the per-context row list injected."""
        import inspect

        parent = super()._run_context
        if not self._checked_parent_signature:
            self._checked_parent_signature = True
            try:
                parameters = list(inspect.signature(parent).parameters)
                if parameters[:3] != ["index", "engine_factory", "semaphore"]:
                    self.account_binding_ok = False
                    logger.error(
                        "Orchestrator._run_context signature changed (%s) - falling back to shared rows",
                        parameters,
                    )
            except (TypeError, ValueError):  # pragma: no cover - introspectable in practice
                self.account_binding_ok = False
                logger.error("could not introspect Orchestrator._run_context - falling back to shared rows")

        if not self.account_binding_ok:
            return await parent(index, engine_factory, semaphore)

        profile = self.profiles[index]
        rows = self.rows_for(index)
        account = self.pool.for_context(index) if len(self.pool) else None
        async with semaphore:
            context_result: Optional[ContextResult] = None
            engine: Optional[ContextEngine] = None
            from waft.utils import Stopwatch, now_iso  # local import: mirrors the parent

            stopwatch = Stopwatch()
            stopwatch.__enter__()
            started_iso = now_iso()
            try:
                if account is not None:
                    logger.info(
                        "[%s] account pinned: %s (app password %s) - %d row(s)",
                        profile.context_id,
                        account.email,
                        account.masked_password,
                        len(rows),
                    )
                proxy = await self.proxy_pool.acquire(context_id=profile.context_id) if self.proxy_pool else None
                engine = await engine_factory.create(profile, proxy)
                self._engines.append(engine)
                await engine.start()
                if account is not None:
                    engine.comment = f"account:{account.email}"  # type: ignore[attr-defined]
                await engine.run_rows(rows)
                failed_runs = [run for run in engine.runs if not run.ok]
                blocked = [
                    run for run in engine.runs
                    if "captcha" in (run.error or "").lower() or run.metadata.get("captcha_expected")
                ]
                if blocked:
                    logger.warning(
                        "[%s] %d target(s) blocked_captcha (policy=%s) - continuing with the rest",
                        profile.context_id,
                        len(blocked),
                        self.config.captcha_action,
                    )
                context_result = engine.build_context_result(
                    status=STATUS_FAILED if failed_runs else STATUS_OK,
                    error=(f"{len(failed_runs)} of {len(engine.runs)} target(s) failed" if failed_runs else None),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one context must never kill the run
                logger.error(
                    "[%s] context failed: %s: %s", profile.context_id, type(exc).__name__, truncate(str(exc), 300)
                )
                if engine is not None:
                    context_result = engine.build_context_result(
                        status=STATUS_FAILED, error=f"{type(exc).__name__}: {exc}"
                    )
                    context_result.error_type = type(exc).__name__
            finally:
                if engine is not None:
                    try:
                        failed = context_result is None or not context_result.ok
                        trace_path = await engine.close(failed=failed)
                        if context_result is not None and trace_path:
                            context_result.trace_path = trace_path
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("[%s] engine shutdown issue: %s", profile.context_id, exc)
                stopwatch.stop()

            if context_result is None:
                context_result = ContextResult(
                    context_id=profile.context_id, index=index, status=STATUS_FAILED,
                    error="context produced no result",
                )
            context_result.started_at = started_iso
            context_result.duration_ms = round(stopwatch.elapsed_ms, 1)
            context_result.finished_at = context_result.finished_at or now_iso()

            if context_result.ok:
                self.stats.contexts_ok += 1
            else:
                self.stats.contexts_failed += 1
            self._context_results.append(context_result)

            if self.artifacts is not None:
                try:
                    self.artifacts.write_context_summary(context_result)
                    if not context_result.ok:
                        context_result.failure_bundle = self.artifacts.create_failure_bundle(context_result)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[%s] artifact finalisation failed: %s", profile.context_id, exc)

            logger.info(
                "[%s] finished: %d target(s), %d ok, %d failed in %s",
                profile.context_id,
                len(context_result.runs),
                sum(1 for run in context_result.runs if run.ok),
                sum(1 for run in context_result.runs if not run.ok),
                human_ms(context_result.duration_ms),
            )
            return context_result


# ======================================================================================
# config construction
# ======================================================================================
def build_config(args: argparse.Namespace, pool: AccountPool) -> Config:
    """Build and validate the WAFT :class:`Config` for the offerwall profile."""
    imap_host, imap_port = pool.first_imap_settings()
    config = Config(
        data_file=Path(args.targets),
        contexts=args.contexts,
        concurrency=args.concurrency,
        scenario="auto",
        # --- stealth ------------------------------------------------------------------
        stealth=True,
        verify_stealth=True,
        humanize=not args.no_humanize,
        # --- proxy --------------------------------------------------------------------
        proxy_file=Path(args.proxies) if args.proxies and args.proxy_mode != "off" else None,
        proxy_mode=args.proxy_mode,
        # --- network ------------------------------------------------------------------
        capture_har=True,
        log_network=True,
        # --- forms / captcha ----------------------------------------------------------
        captcha_action="skip",
        selectors_file=Path(args.selectors) if args.selectors and Path(args.selectors).exists() else None,
        # --- IMAP verification --------------------------------------------------------
        imap_enabled=bool(args.imap_host or args.imap_ssl == "off"),
        imap_host=args.imap_host or imap_host,
        imap_port=int(args.imap_port or imap_port),
        imap_ssl=(args.imap_ssl != "off"),
        imap_username=args.imap_user or pool.for_context(0).email,
        imap_password=args.imap_password or pool.for_context(0).password,
        imap_subject_regex=args.imap_subject_regex,
        imap_timeout_s=float(args.imap_timeout),
        # --- artifacts / retries ------------------------------------------------------
        artifacts_dir=Path(args.artifacts),
        trace_mode="on-failure",
        retries=args.retries,
        retry_only_retryable=False,  # keep retrying deterministic verification failures too
        rate_limit=args.rate_limit,
        save_storage_state=True,
        log_level=args.log_level,
    )
    config.validate()
    return config


# ======================================================================================
# results summary
# ======================================================================================
def summarise_endpoints(artifacts_dir: Path, run_id: Optional[str] = None) -> dict[str, Any]:
    """Read ``endpoints.json`` (and ``run.json``) and print a terminal summary.

    Returns a dictionary so callers/tests can assert on it.  Never raises: a missing or
    unreadable file yields ``{"available": False, "reason": …}``.
    """
    summary: dict[str, Any] = {"available": False, "run_id": run_id}
    try:
        candidates = sorted(
            (path for path in artifacts_dir.glob("run-*/endpoints.json") if path.parent.is_dir()),
            key=lambda path: path.stat().st_mtime,
        )
        if not candidates:
            summary["reason"] = f"no endpoints.json under {artifacts_dir}"
            return summary
        endpoints_path = candidates[-1]
        payload = json.loads(endpoints_path.read_text(encoding="utf-8"))
        run_dir = endpoints_path.parent
        if run_id and run_dir.name != run_id:
            # Fall back to the requested run directory when several runs are present.
            explicit = run_dir.parent / run_id / "endpoints.json"
            if explicit.exists():
                endpoints_path, payload, run_dir = explicit, json.loads(explicit.read_text(encoding="utf-8")), explicit.parent

        summary.update({"available": True, "run_id": run_dir.name, "path": str(endpoints_path)})
        endpoints = payload.get("endpoints") or []
        summary["endpoint_count"] = len(endpoints)
        summary["endpoints"] = [
            {
                "method": item.get("method"),
                "host": item.get("host"),
                "path": item.get("path"),
                "statuses": item.get("statuses"),
                "count": item.get("count"),
                "query_keys": item.get("query_keys"),
            }
            for item in endpoints[:25]
        ]

        run_json = run_dir / "run.json"
        if run_json.exists():
            run_payload = json.loads(run_json.read_text(encoding="utf-8"))
            totals = run_payload.get("totals") or {}
            summary["totals"] = totals
            contexts = run_payload.get("contexts") or []
            blocked: list[dict[str, Any]] = []
            verified_ok = verified_failed = 0
            for context in contexts:
                for run in context.get("runs") or []:
                    error = str(run.get("error") or "")
                    if "captcha" in error.lower():
                        blocked.append(
                            {
                                "context": context.get("context_id"),
                                "target": run.get("target_url"),
                                "status": run.get("status"),
                                "error": truncate(error, 120),
                            }
                        )
                    verification = run.get("verification") or {}
                    if verification:
                        if verification.get("success"):
                            verified_ok += 1
                        else:
                            verified_failed += 1
            summary["captcha_blocked"] = blocked
            summary["verifications_ok"] = verified_ok
            summary["verifications_failed"] = verified_failed
        return summary
    except Exception as exc:  # noqa: BLE001 - reporting must never break the exit code
        summary["reason"] = f"{type(exc).__name__}: {exc}"
        return summary


def print_endpoint_summary(summary: dict[str, Any]) -> None:
    """Pretty-print :func:`summarise_endpoints` output."""
    print("\n" + "=" * 96)
    if not summary.get("available"):
        print(f"API endpoint özeti yok: {summary.get('reason')}")
        print("=" * 96)
        return
    print(f"API endpoint özeti — {summary.get('run_id')}")
    print(f"  dosya            : {summary.get('path')}")
    print(f"  keşfedilen uç nokta: {summary.get('endpoint_count', 0)}")
    for item in summary.get("endpoints", []):
        statuses = ",".join(str(code) for code in (item.get("statuses") or []))
        print(
            f"    {str(item.get('method')):6s} {item.get('host')}{item.get('path')} "
            f"[status {statuses or '?'}, {item.get('count')} kez]"
        )
    totals = summary.get("totals") or {}
    if totals:
        print(
            f"  hedefler         : {totals.get('targets_run', 0)} koşu, {totals.get('targets_ok', 0)} ok, "
            f"{totals.get('targets_failed', 0)} fail (%{totals.get('success_rate_pct', 0)})"
        )
    print(f"  e-posta doğrulama: {summary.get('verifications_ok', 0)} ok / {summary.get('verifications_failed', 0)} fail")
    blocked = summary.get("captcha_blocked") or []
    print(f"  blocked_captcha  : {len(blocked)} hedef")
    for item in blocked[:10]:
        print(f"    • [{item.get('context')}] {item.get('target')} → {item.get('error')}")
    print("=" * 96)


# ======================================================================================
# CLI
# ======================================================================================
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_offerwall.py",
        description=(
            "Offerwall/survey sign-up flow: account-pinned load & regression harness "
            "(owned/authorised targets only)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--credentials", type=Path, default=_HERE / "credentials.json",
                        help="Account pool: credentials.json (or email:password text file).")
    parser.add_argument("--targets", type=Path, default=_HERE / "targets_offerwall.xlsx",
                        help="Target sheet with {email}/{password} placeholders.")
    parser.add_argument("--base-url", default=None,
                        help="Rewrite every target_url to this host (staging <-> sandbox switch).")
    parser.add_argument("--contexts", type=int, default=10, help="Isolated browser contexts (= accounts).")
    parser.add_argument("--concurrency", type=int, default=5, help="Contexts running in parallel.")
    parser.add_argument("--rate-limit", type=float, default=2.0, help="Global navigations/second cap.")
    parser.add_argument("--retries", type=int, default=2, help="Retry attempts per target.")
    parser.add_argument("--proxies", type=Path, default=_REPO / "proxies.txt", help="Proxy list file.")
    parser.add_argument("--proxy-mode", default="auto", choices=["auto", "require", "off"], help="Proxy policy.")
    parser.add_argument("--selectors", type=Path, default=_HERE / "selectors.resolved.json",
                        help="Resolved selector map (produced by selector_resolver.py).")
    parser.add_argument("--imap-host", default=None, help="IMAP host (default: first account's imap_host).")
    parser.add_argument("--imap-port", type=int, default=None, help="IMAP port (default: first account's imap_port).")
    parser.add_argument("--imap-user", default=None, help="IMAP user (default: first account's e-mail).")
    parser.add_argument("--imap-password", default=None, help="IMAP password (default: first account's app password).")
    parser.add_argument("--imap-ssl", choices=["on", "off"], default="on", help="Implicit TLS (993) or plain IMAP.")
    parser.add_argument("--imap-subject-regex", default=DEFAULT_SUBJECT_REGEX, help="Verification mail subject filter.")
    parser.add_argument("--imap-timeout", type=float, default=180.0, help="Seconds to wait for each mail.")
    parser.add_argument("--artifacts", type=Path, default=_REPO / "artifacts", help="Artifacts root.")
    parser.add_argument("--log-level", default="INFO", help="Console log level.")
    parser.add_argument("--no-humanize", action="store_true", help="Disable human-like typing (faster).")
    parser.add_argument("--dry-run", action="store_true", help="Plan only: prepare, print, exit.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colours.")
    return parser.parse_args(argv)


def configure_logging(level: str, *, color: bool) -> None:
    """Console + file logging for the wrapper itself (WAFT configures its own on top)."""
    try:
        from waft.logging_setup import setup_logging

        setup_logging(level=level, color=color)
        return
    except Exception:  # noqa: BLE001 - fall back to stdlib
        logging.basicConfig(level=getattr(logging, str(level).upper(), logging.INFO),
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def load_template_rows(config: Config) -> list[TargetRow]:
    """Load the target sheet through WAFT's loader (placeholders stay untouched)."""
    loader = DataLoader(config)
    rows = loader.load()
    if not rows:
        raise SystemExit(f"✖ {config.data_file} produced zero runnable rows")
    placeholders = [
        row.name or f"row-{row.index}"
        for row in rows
        if any(str(row.form_data.get(field, "")).startswith("{") for field in PLACEHOLDER_FIELDS)
    ]
    logger.info(
        "target sheet: %d row(s), %d with account placeholders (%s)",
        len(rows),
        len(placeholders),
        ", ".join(placeholders[:6]) or "none",
    )
    return rows


def rewrite_base_url(args: argparse.Namespace) -> Optional[Path]:
    """Point every row at *args.base_url* by rewriting the sheet into the artifacts directory."""
    if not args.base_url:
        return None
    from urllib.parse import urlsplit

    import pandas as pd

    base = str(args.base_url).rstrip("/")
    sheets = pd.read_excel(args.targets, sheet_name=None)
    out = Path(args.artifacts) / "targets_offerwall.base_url.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for sheet_name, sheet in sheets.items():
            if "target_url" in sheet.columns:
                sheet = sheet.copy()
                sheet["target_url"] = [
                    base + (urlsplit(str(value)).path or "/") if str(value).startswith("http") else f"{base}/{value}"
                    for value in sheet["target_url"]
                ]
            sheet.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    logger.info("target URLs rewritten to %s → %s", base, out)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level, color=not args.no_color)

    # --- 1) account pool -----------------------------------------------------------------
    try:
        pool = AccountPool.from_file(args.credentials)
        warnings = pool.validate(require_enabled=True)
    except AccountPoolError as exc:
        print(f"✖ credential problem: {exc}", file=sys.stderr)
        return 2
    print(pool.describe())
    for warning in warnings:
        print(f"⚠ {warning}")
    if not pool.enabled():
        print("✖ no enabled accounts in the pool", file=sys.stderr)
        return 2

    rewritten = rewrite_base_url(args)
    if rewritten is not None:
        args.targets = rewritten

    # --- 2) config + template rows -------------------------------------------------------
    try:
        config = build_config(args, pool)
        if args.dry_run:
            config.dry_run = True
        template_rows = load_template_rows(config)
    except Exception as exc:  # noqa: BLE001 - configuration/data problems are usage errors
        print(f"✖ configuration error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    # --- 3) bind rows to accounts --------------------------------------------------------
    rows_by_context: list[list[TargetRow]] = []
    offset = 0
    for index in range(config.contexts):
        account = pool.for_context(index)
        bound = bind_rows_to_account(template_rows, account, iteration_offset=offset)
        offset += len(template_rows)
        rows_by_context.append(bound)

    orchestrator = AccountPinnedOrchestrator(config, pool=pool, rows_by_context=rows_by_context)
    print(
        f"→ {config.contexts} context(s) / {config.concurrency} parallel | "
        f"{len(template_rows)} template row(s) x {len(pool)} account(s) = "
        f"{sum(len(rows) for rows in rows_by_context)} target run(s) | "
        f"proxy={args.proxy_mode} | imap={'on' if config.imap_enabled else 'off'} | captcha=skip"
    )
    print(f"→ run id: {orchestrator.run_id} | artifacts: {config.artifacts_dir}")

    # --- 4) run --------------------------------------------------------------------------
    try:
        exit_code = int(asyncio.run(orchestrator.run()))
    except KeyboardInterrupt:
        print("\n✖ interrupted - partial artifacts were kept", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort guard, always leaves a message
        logger.exception("orchestrator crashed: %s", exc)
        return 1

    # --- 5) endpoint summary -------------------------------------------------------------
    summary = summarise_endpoints(Path(config.artifacts_dir), run_id=orchestrator.run_id)
    print_endpoint_summary(summary)

    # --- 6) manifest (reproducibility) ---------------------------------------------------
    try:
        manifest_path = Path(config.artifacts_dir) / "offerwall_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "run_id": orchestrator.run_id,
                    "exit_code": exit_code,
                    "credentials_file": str(args.credentials),
                    "accounts": [account.to_dict(mask=True) for account in pool],
                    "targets_file": str(args.targets),
                    "contexts": config.contexts,
                    "concurrency": config.concurrency,
                    "rate_limit": config.rate_limit,
                    "retries": config.retries,
                    "proxy_mode": args.proxy_mode,
                    "imap": {
                        "enabled": config.imap_enabled,
                        "host": config.imap_host,
                        "port": config.imap_port,
                        "user": config.imap_username,
                        "password": mask_secret(config.imap_password or ""),
                        "subject_regex": config.imap_subject_regex,
                        "timeout_s": config.imap_timeout_s,
                    },
                    "captcha_action": config.captcha_action,
                    "har": config.capture_har,
                    "network_log": config.log_network,
                    "stealth": config.stealth,
                    "verify_stealth": config.verify_stealth,
                    "account_binding": orchestrator.account_binding_ok,
                    "endpoint_summary": {key: value for key, value in summary.items() if key != "endpoints"},
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"→ manifest: {manifest_path}")
    except Exception as exc:  # noqa: BLE001 - manifest is nice-to-have
        logger.warning("manifest could not be written: %s", exc)

    print(f"→ exit code: {exit_code} ({'PASSED' if exit_code == 0 else 'see artifacts'})")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
```

Kardeş modüller: `account_pool.py` (bu kit), `../selector_resolver.py`, `../run_regression.py`
(kapsam dosyası + üçüncü taraf engel listesi), `../../examples/offerwall_sandbox.py` (yerel test
ikizi: `/register`, `/survey/1-2`, `/verify`, `/api/v1/{app-config,surveys,postback}`, `/captcha`).

---

## ARTEFAKT 5 — Terminal komutu, kurulum, `proxies.txt`, `.env`

### 5.1 Ek bağımlılıklar

```bash
python -m pip install -r requirements.txt          # playwright, pandas, openpyxl, rich, python-dotenv, playwright-stealth
python -m playwright install --with-deps chromium  # tarayıcı + sistem kütüphaneleri
```

### 5.2 Yerel, uçtan uca (mock offerwall + devmail; proxy yok)

```bash
# terminal 1 — yerel posta sunucusu (SMTP 1025 / IMAP 1430)
python qa-kit/devmail.py --smtp-port 1025 --imap-port 1430

# terminal 2 — offerwall sandbox (kayıt + anket + doğrulama + postback)
python examples/offerwall_sandbox.py --port 8090 --quiet \
  --smtp-host 127.0.0.1 --smtp-port 1025 --public-url http://127.0.0.1:8090

# terminal 3 — veri + seçici hazırlığı
python qa-kit/offerwall/make_targets_offerwall.py --base-url http://127.0.0.1:8090
python qa-kit/selector_resolver.py --data qa-kit/offerwall/targets_offerwall.xlsx \
  --catalogue qa-kit/offerwall/selectors_offerwall.json \
  --out qa-kit/offerwall/selectors.resolved.json

# terminal 4 — KOŞU: 10 context (her biri ayrı hesap), 5 paralel
python qa-kit/offerwall/run_offerwall.py \
  --credentials qa-kit/offerwall/credentials.sandbox.json \
  --targets qa-kit/offerwall/targets_offerwall.xlsx \
  --contexts 10 --concurrency 5 \
  --proxy-mode off \
  --imap-host 127.0.0.1 --imap-port 1430 --imap-ssl off --imap-timeout 30 \
  --captcha-action skip --log-network --capture-har --verify-stealth \
  --no-color
```

### 5.3 Kendi staging'iniz + gerçek Gmail (İstenen üretim profili)

```bash
cp qa-kit/offerwall/credentials.example.json credentials.json   # 10 hesabı APP PASSWORD ile doldur
python qa-kit/offerwall/run_offerwall.py \
  --credentials credentials.json \
  --targets qa-kit/offerwall/targets_offerwall.xlsx \
  --base-url https://staging.sirketiniz.com \
  --contexts 10 --concurrency 5 \
  --proxies proxies.txt --proxy-mode auto \
  --selectors qa-kit/offerwall/selectors.resolved.json \
  --imap-host imap.gmail.com --imap-port 993 --imap-ssl on \
  --imap-subject-regex "(doğrula|dogrula|verify|confirm|aktivasyon|activate)" \
  --imap-timeout 180 \
  --captcha-action skip --log-network --capture-har --verify-stealth \
  --retries 2 --rate-limit 2.0 \
  --artifacts artifacts --log-level INFO --no-color
```

### 5.4 `proxies.txt` örneği (en az 3 satır; gerçek kimlik bilgilerinizi yazın)

```text
# host:port
127.0.0.1:8888

# user:pass@host:port
proxy-kullanici:proxy-parola@proxy1.sirketiniz.com:8080

# şema açık + SOCKS
http://proxy-kullanici:proxy-parola@10.0.0.5:3128
socks5://proxy-kullanici:proxy-parola@10.0.0.6:1080
socks5h://10.0.0.7:1080
```

### 5.5 `.env` — IMAP ayarları (Gmail örneği)

```dotenv
# --- IMAP (doğrulama maili) -------------------------------------------------
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_SSL=true
IMAP_USER=hesap1@gmail.com
IMAP_PASSWORD=xxxxxxxxxxxxxxxx        # 16 haneli GMAIL UYGULAMA ŞİFRESİ (App Password)

# --- yerel test posta sunucusu (devmail/Mailpit) ----------------------------
# IMAP_HOST=127.0.0.1
# IMAP_PORT=1430
# IMAP_SSL=false
# IMAP_USER=devmail
# IMAP_PASSWORD=devmail

# --- WAFT çalışma profili ---------------------------------------------------
WAFT_DATA=qa-kit/offerwall/targets_offerwall.xlsx
WAFT_PROXY_FILE=proxies.txt
WAFT_CONTEXTS=10
WAFT_CONCURRENCY=5
WAFT_RATE_LIMIT=2.0
WAFT_CAPTCHA_ACTION=skip
WAFT_CAPTURE_HAR=true
WAFT_LOG_NETWORK=true
```

---

## Doğrulanmış koşu (bu repoda gerçekten çalıştırıldı)

```text
WAFT run run-20260928-170154-ab1e80 PASSED ✅ | 60/60 target(s) ok (100.0%) | 35.8 s
  contexts      : 10 ok / 0 failed (of 10)      ← her context'e farklı hesap (account pinning)
  targets       : 60 run, 60 ok, 0 failed       ← 6 şablon satır × 10 hesap
  steps         : 370 ok / 0 failed             ← navigate · form-scan · form-fill · submit ·
                                                   outcome · email-verification · network-traffic
  e-posta doğrulama: 20 ok / 0 fail             ← gerçek SMTP + gerçek IMAP, mock yok
  blocked_captcha: 0 (satır expect_error=true)  ← bayrak kaldırılınca: 10 hedef "blocked_captcha",
                                                   koşu DURMAZ, diğerleri devam eder (ayrı koşuda
                                                   doğrulandı: 50/60 ok + 10 blocked, exit 1)
```

Keşfedilen API uç noktaları (HAR + `endpoints.json`, ağ trafiği analizi için):

```text
GET  127.0.0.1:8090/api/v1/app-config   [200, 360 kez]
POST 127.0.0.1:8090/register            [200, 360 kez]
POST 127.0.0.1:8090/survey/1/submit     [200,  80 kez]
POST 127.0.0.1:8090/survey/2/submit     [200,  60 kez]
```

`results.csv` içinde her koşu hesabıyla birlikte görünür:

```text
ctx-02-6af1  register-email-verify@ctx-account-qa03  verify_ok=True  status=ok  verif=link
```

Artefaktlar: `artifacts/<run-id>/{run.json, summary.md, results.csv, contexts.csv, junit.xml,
endpoints.json}`, `contexts/<ctx>/{screenshots/, har/network.har, network.jsonl, console.jsonl,
summary.json, *-failure-bundle.zip}` ve `artifacts/offerwall_manifest.json` (hesaplar **maskeli**,
IMAP parolası maskeli, context→hesap eşleşmesi ve `account_binding: true`).

---

## Not: neden bu paket üçüncü taraf platformlara çevrilmiyor?

* Kendi sisteminizde 10 hesapla paralel kayıt/anket testi **meşru ve normaldir** (bu paket tam
  olarak bunu yapar).
* Bir offerwall/mikro görev platformunda 10 hazır Gmail hesabıyla otomatik kayıt açmak, doğrulama
  maillerini toplamak ve arka plan API'sini çekmek; o platformun kullanım şartlarını ihlal eder,
  ödül sistemini manipüle etmeye yönelik **dolandırıcılık (fraud/abuse)** kapsamına girer ve
  KVKK/TCK ile CFAA tarafında suç teşkil edebilir. "Yalnızca yetkili sistemler" ibaresi teknik
  olarak da çelişkili: o platformların sahibi değilsiniz, sandbox/izin belgesi de yok.
* Bu tür platformlarla **meşru** entegrasyon testi: partner/publisher programına katılıp resmî
  API + postback dokümantasyonunu kullanmak, sağladıkları sandbox ortamında kendi entegrasyonunuzu
  (kullanıcı kaydı, postback doğrulama, raporlama) yük testine sokmak. Bu kitin hedefi
  `--base-url` ile o sandbox'a çevrilebilir — yazılı izin/kapsam varsa `authorized_hosts.txt`'e
  eklemeniz yeterlidir.
