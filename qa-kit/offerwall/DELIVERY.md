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
| + | **API endpoint dayanıklılığı** (S2S postback alıcısı + yük/replay/imza kapısı) | `postback_receiver.py` (6. bölüm) |
| + | Tek komutla kurulum + ateşleme + sorun giderme | `RUNBOOK.md`, `sandbox_up.sh` (5.6) |
| + | Bu dokümanı kaynaklardan yeniden üreten builder | `build_delivery.py` (`--check` ile drift denetimi) |
| + | Minimal tek dosya sürücü (programatik API örneği, kapsam kapılı) | `run_offerwall_min.py` (RUNBOOK §10) |
| + | Kit sözleşme testleri (17 test; taslak wrapper'ın 8 hatasını kilitler) | `tests/test_offerwall_kit_contracts.py` |
| + | Hedef sayfası ön doğrulama (kapsam + bilinen tuzaklar; koşuya otomatik bağlı) | `validate_targets.py` (RUNBOOK §12) |
| + | Kimlik dosyası onarımı + katı havuz doğrulaması (Markdown/URL artıkları, yer tutucu parola) | `import_credentials.py`, `account_pool.py` (RUNBOOK §13) |

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
import re
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Iterator, Optional, Sequence

logger = logging.getLogger("waft.offerwall.accounts")

#: Placeholder values that mean "you forgot to fill the file in".
#: Strict-ish address shape. A missing ``@`` was not enough: pasting credentials out of a chat
#: turns ``hesap1@gmail.com`` into ``[hesap1@gmail.com](mailto:hesap1@gmail.com)``, which contains
#: an ``@`` - the pool accepted it silently, the browser typed the whole Markdown link into the
#: sign-up form and IMAP then tried to connect to ``[imap.gmail.com](http://imap.gmail.com)``.
EMAIL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?)+$")

#: Hostname or IPv4 literal - no scheme, no brackets, no Markdown leftovers.
HOST_RE: Final[re.Pattern[str]] = re.compile(r"^(?:(?:[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?)\.)+[A-Za-z]{2,}$|^\d{1,3}(\.\d{1,3}){3}$")

#: Substrings that betray a Markdown link / URL paste rather than a value.
PASTE_ARTIFACT_MARKERS: Final[tuple[str, ...]] = ("](", "mailto:", "http://", "https://", "`", "<", ">")

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


#: Substrings that betray a not-yet-filled password. ``"16_haneli_uygulama_sifresi_buraya"`` is not
#: in the exact set above, yet shipping it would make every IMAP login fail with an auth error
#: that looks like a Google problem. Substring matching is deliberately broad: this is a warning
#: in the pool and a blocking problem in the importer, never a silent pass.
PLACEHOLDER_PASSWORD_MARKERS: Final[tuple[str, ...]] = (
    "buraya",
    "uygulama_sifresi",
    "uygulama-sifresi",
    "app_password",
    "app-password",
    "your_",
    "senin_",
    "example",
    "xxxx",
    "****",
    "<",
    ">",
)


def looks_like_placeholder(password: str) -> bool:
    """True when *password* is empty, in the exact placeholder set, or matches a known marker."""
    text = (password or "").strip().lower()
    if text in PLACEHOLDER_PASSWORDS:
        return True
    return any(marker in text for marker in PLACEHOLDER_PASSWORD_MARKERS)


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
            if any(marker in account.email for marker in PASTE_ARTIFACT_MARKERS):
                raise AccountPoolError(
                    f"e-mail looks like pasted Markdown/URL, not an address: {account.email!r} — "
                    f"repair it with: python3 qa-kit/offerwall/import_credentials.py --in <file> --out credentials.json"
                )
            if not EMAIL_RE.match(account.email):
                raise AccountPoolError(f"malformed e-mail address: {account.email!r}")
            if any(marker in account.imap_host for marker in PASTE_ARTIFACT_MARKERS) or not HOST_RE.match(account.imap_host):
                raise AccountPoolError(
                    f"malformed IMAP host for {account.email}: {account.imap_host!r} "
                    f"(a bare hostname is expected, e.g. imap.gmail.com)"
                )
            if not 1 <= int(account.imap_port) <= 65535:
                raise AccountPoolError(f"invalid IMAP port for {account.email}: {account.imap_port}")
            if looks_like_placeholder(account.password):
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
Local end-to-end, one command (bundled sandbox; see qa-kit/offerwall/RUNBOOK.md):::

    python3 qa-kit/offerwall/run_offerwall.py --sandbox

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
_KIT: Final[Path] = _HERE.parent
_REPO: Final[Path] = _HERE.parent.parent
for _path in (str(_REPO), str(_HERE), str(_KIT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from account_pool import Account, AccountPool, AccountPoolError  # noqa: E402  (local sibling)

#: Scope guard is *enforced*, not merely documented: the same allow-list file and the same
#: hard block-list as ``run_regression.py`` (imported below, single source of truth).
from run_regression import (  # noqa: E402  (qa-kit sibling)
    BLOCKED_THIRD_PARTY_SUFFIXES,
    SCOPE_HELP,
    Scope,
    host_of,
)

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
#: Values ``--sandbox`` fills in when the caller did not pass the corresponding flag.
SANDBOX_CREDENTIALS: Final[Path] = _HERE / "credentials.sandbox.json"
SANDBOX_BASE_URL: Final[str] = "http://127.0.0.1:8090"
SANDBOX_IMAP_HOST: Final[str] = "127.0.0.1"
SANDBOX_IMAP_PORT: Final[int] = 1430


def resolve_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Apply the real CLI defaults after ``--sandbox`` has had its say.

    Explicitly passed flags always win: only values left as ``None`` by argparse are filled in
    here, which is why the affected options declare ``default=None`` instead of a literal.
    """
    if args.sandbox:
        if args.credentials is None:
            args.credentials = SANDBOX_CREDENTIALS
        if args.base_url is None:
            args.base_url = SANDBOX_BASE_URL
        if args.proxy_mode is None:
            args.proxy_mode = "off"
        if args.imap_host is None:
            args.imap_host = SANDBOX_IMAP_HOST
        if args.imap_port is None:
            args.imap_port = SANDBOX_IMAP_PORT
        if args.imap_ssl is None:
            args.imap_ssl = "off"
        if args.imap_timeout is None:
            args.imap_timeout = 30.0
    else:
        if args.credentials is None:
            args.credentials = _HERE / "credentials.json"
        if args.proxy_mode is None:
            args.proxy_mode = "auto"
        if args.imap_ssl is None:
            args.imap_ssl = "on"
        if args.imap_timeout is None:
            args.imap_timeout = 180.0
    return args


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_offerwall.py",
        description=(
            "Offerwall/survey sign-up flow: account-pinned load & regression harness "
            "(owned/authorised targets only)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--sandbox", action="store_true",
                        help="Tek komutlu yerel koşu: credentials.sandbox.json + 127.0.0.1:8090 hedefleri "
                             "+ yerel IMAP (127.0.0.1:1430, SSL off) + proxy kapalı. Açıkça verilen "
                             "bayraklar bu varsayılanları her zaman geçersiz kılar.")
    parser.add_argument("--credentials", type=Path, default=None,
                        help="Account pool: credentials.json (or email:password text file). "
                             "(varsayılan: credentials.json; --sandbox ile credentials.sandbox.json)")
    parser.add_argument("--targets", type=Path, default=_HERE / "targets_offerwall.xlsx",
                        help="Target sheet with {email}/{password} placeholders.")
    parser.add_argument("--scope", type=Path, default=_KIT / "authorized_hosts.txt",
                        help="Host allow-list (scope file). Target hosts outside it are refused.")
    parser.add_argument("--allow-host", action="append", default=[],
                        help="Extra allowed host (repeatable); requires --i-am-authorized.")
    parser.add_argument("--i-am-authorized", action="store_true",
                        help="Assert you own / are allowed to test the --allow-host entries. "
                             "Never overrides the third-party block-list.")
    parser.add_argument("--base-url", default=None,
                        help="Rewrite every target_url to this host (staging <-> sandbox switch).")
    parser.add_argument("--contexts", type=int, default=10, help="Isolated browser contexts (= accounts).")
    parser.add_argument("--concurrency", type=int, default=5, help="Contexts running in parallel.")
    parser.add_argument("--rate-limit", type=float, default=2.0, help="Global navigations/second cap.")
    parser.add_argument("--retries", type=int, default=2, help="Retry attempts per target.")
    parser.add_argument("--proxies", type=Path, default=_REPO / "proxies.txt", help="Proxy list file.")
    parser.add_argument("--proxy-mode", default=None, choices=["auto", "require", "off"],
                        help="Proxy policy. (varsayılan: auto; --sandbox ile off)")
    parser.add_argument("--selectors", type=Path, default=_HERE / "selectors.resolved.json",
                        help="Resolved selector map (produced by selector_resolver.py).")
    parser.add_argument("--imap-host", default=None, help="IMAP host (default: first account's imap_host).")
    parser.add_argument("--imap-port", type=int, default=None, help="IMAP port (default: first account's imap_port).")
    parser.add_argument("--imap-user", default=None, help="IMAP user (default: first account's e-mail).")
    parser.add_argument("--imap-password", default=None, help="IMAP password (default: first account's app password).")
    parser.add_argument("--imap-ssl", choices=["on", "off"], default=None,
                        help="Implicit TLS (993) or plain IMAP. (varsayılan: on; --sandbox ile off)")
    parser.add_argument("--imap-subject-regex", default=DEFAULT_SUBJECT_REGEX, help="Verification mail subject filter.")
    parser.add_argument("--imap-timeout", type=float, default=None,
                        help="Seconds to wait for each mail. (varsayılan: 180; --sandbox ile 30)")
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


def load_scope(path: Path, extra_hosts: Sequence[str]) -> Scope:
    """Load the allow-list file (comment/blank tolerant) plus any ``--allow-host`` extras."""
    patterns: list[str] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip().lower()
            if line:
                patterns.append(line)
    except OSError as exc:
        logger.warning("scope file %s could not be read (%s); %d host(s) from --allow-host only",
                       path, exc, len(extra_hosts))
    patterns.extend(str(host).strip().lower() for host in extra_hosts if str(host).strip())
    return Scope(patterns=tuple(dict.fromkeys(patterns)))


def scope_host_candidates(url: str) -> list[str]:
    """Return the host forms a scope file may legitimately mention for *url*.

    ``urlsplit("http://127.0.0.1:8090/x").netloc`` is ``127.0.0.1:8090``, while scope files are
    written with bare hosts (``127.0.0.1``). Comparing only the netloc therefore rejected the
    kit's own sandbox - so both forms are matched, plus the IPv6 bracket form.
    """
    netloc = host_of(url)
    if not netloc:
        return []
    candidates = [netloc]
    if netloc.startswith("["):  # [::1]:8090 -> ::1
        closing = netloc.find("]")
        if closing != -1:
            candidates.append(netloc[1:closing])
    elif ":" in netloc:  # host:port -> host
        candidates.append(netloc.rsplit(":", 1)[0])
    return list(dict.fromkeys(candidates))


def enforce_scope(rows: Sequence[TargetRow], scope: Scope, *, i_am_authorized: bool) -> tuple[list[str], list[str]]:
    """Refuse third-party platforms and hosts missing from the scope file.

    Returns ``(blocked, unauthorized)``. Raises :class:`ScopeViolation` when the run must stop,
    which is the case for every hard-blocked host *and* for out-of-scope hosts unless the caller
    explicitly asserted authorization.
    """
    blocked: list[str] = []
    unauthorized: list[str] = []
    for row in rows:
        candidates = scope_host_candidates(str(row.target_url or ""))
        if not candidates:
            continue
        host = candidates[0]
        if any(
            candidate == suffix or candidate.endswith("." + suffix)
            for candidate in candidates
            for suffix in BLOCKED_THIRD_PARTY_SUFFIXES
        ):
            blocked.append(host)
        elif not any(scope.allows(candidate) for candidate in candidates):
            unauthorized.append(host)
    blocked = sorted(set(blocked))
    unauthorized = sorted(set(unauthorized) - set(blocked))

    if blocked:
        raise ScopeViolation(
            "third-party offerwall / micro-task platform(s) are out of scope for this kit: "
            + ", ".join(blocked)
            + ". Automated sign-ups there are abuse (not load testing); integrate through the "
              "platform's official API or a sandbox they grant you in writing. For your own "
              "staging, pass --base-url or use --sandbox."
        )
    if unauthorized and not i_am_authorized:
        raise ScopeViolation(
            "host(s) not covered by the scope file: " + ", ".join(unauthorized) + " — " + SCOPE_HELP
            + " (or use --sandbox / --base-url for your own environment)"
        )
    if unauthorized:
        logger.warning(
            "running against host(s) outside %s because --i-am-authorized was given: %s",
            "the scope file", ", ".join(unauthorized),
        )
    return blocked, unauthorized


class ScopeViolation(RuntimeError):
    """Raised when a target host is out of scope (hard block-list or missing allow-list entry)."""


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
    args = resolve_defaults(parse_args(argv))
    configure_logging(args.log_level, color=not args.no_color)

    if args.sandbox:
        print(
            "🔒 sandbox modu — yerel mock hedef: " + str(args.base_url)
            + " | posta kutusu: " + str(args.imap_host) + ":" + str(args.imap_port) + " (SSL " + str(args.imap_ssl) + ")"
            + " | proxy: " + str(args.proxy_mode)
        )
        print("   yalnızca bu makinedeki mock sunucu; üçüncü parti platform hedefi yok.")

    # --- 1) account pool -----------------------------------------------------------------
    try:
        pool = AccountPool.from_file(args.credentials)
        warnings = pool.validate(require_enabled=True)
    except AccountPoolError as exc:
        print(f"✖ credential problem: {exc}", file=sys.stderr)
        if not args.sandbox and SANDBOX_CREDENTIALS.exists():
            print("→ ipucu: yerel mock ile tek komutta denemek için:  "
                  "python3 qa-kit/offerwall/run_offerwall.py --sandbox", file=sys.stderr)
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

    # --- 2b) target sheet pre-flight (warn only; scope errors already stopped the run) ----
    try:
        from validate_targets import validate_sheet  # local import: avoids a circular import

        sheet_issues = [item for item in validate_sheet(Path(args.targets)) if item.severity in {"error", "warning"}]
        blocking = [item for item in sheet_issues if item.severity == "error"]
        for item in blocking:
            print(f"⚠ hedef sayfası: [{item.row}] {item.field}: {item.message}")
        print(
            f"→ hedef sayfası doğrulaması: {len(blocking)} hata, "
            f"{len(sheet_issues) - len(blocking)} uyarı "
            f"(ayrıntı: python3 qa-kit/offerwall/validate_targets.py --targets {args.targets})"
        )
    except Exception as exc:  # noqa: BLE001 - pre-flight must never block a run
        logger.debug("target sheet validation skipped: %s", exc)

    # --- 2c) scope gate (hard block-list always wins; runs last so the sheet report is printed first) ------------------------------------
    try:
        scope = load_scope(args.scope, args.allow_host)
        blocked, unauthorized = enforce_scope(template_rows, scope, i_am_authorized=bool(args.i_am_authorized))
    except ScopeViolation as exc:
        print(f"✖ scope: {exc}", file=sys.stderr)
        return 2
    print(
        f"→ scope: {len(scope.patterns)} pattern(s) from {args.scope} | "
        f"blocked_third_party={blocked or 'none'} | out_of_scope_override={unauthorized or 'none'}"
    )



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

### 5.6 Tek komut — tek komutla yerel koşu (hesap dosyası gerekmez)

Senin "ATEŞLEME KOMUTU" adımının bu repodaki karşılığı. İki eşdeğer yol var:

```bash
# A) sunucuları da kendisi kaldırsın (SMTP 1025 / IMAP 1430 / web 8090)
bash qa-kit/offerwall/sandbox_up.sh

# B) sunucular zaten ayaktaysa doğrudan koşu
python qa-kit/offerwall/run_offerwall.py --sandbox
```

`--sandbox` şu varsayılanları uygular: `credentials.sandbox.json`, hedefler `http://127.0.0.1:8090`,
IMAP `127.0.0.1:1430` (SSL off), proxy `off`, IMAP timeout 30 s. **Açıkça verdiğin bayrak her zaman
kazanır** (ör. `--contexts 4 --concurrency 2`).

Gerçek koşu çıktısı (bu repoda):

```text
🔒 sandbox modu — yerel mock hedef: http://127.0.0.1:8090 | posta kutusu: 127.0.0.1:1430 (SSL off) | proxy: off
→ scope: 4 pattern(s) from qa-kit/authorized_hosts.txt | blocked_third_party=none | out_of_scope_override=none
  hedefler         : 60 koşu, 60 ok, 0 fail (%100.0)
  e-posta doğrulama: 20 ok / 0 fail
  blocked_captcha  : 0 hedef
→ exit code: 0 (PASSED)
```

Kurulum ve sorun giderme için tam adımlı sürüm: **`qa-kit/offerwall/RUNBOOK.md`**.

Tam `sandbox_up.sh` kaynağı:

```bash
#!/usr/bin/env bash
# ======================================================================================
# sandbox_up.sh — tek komutla yerel offerwall sandbox'ını ayağa kaldır ve kiti koştur
#
# Ne yapar:
#   1. yerel posta sunucusunu başlatır (devmail: SMTP 1025 / IMAP 1430)   [gerekirse]
#   2. offerwall sandbox'ını başlatır (kayıt + anket + doğrulama + XHR API) [gerekirse]
#   3. portlar açılana kadar bekler (zaman aşımı kontrollü)
#   4. `run_offerwall.py --sandbox` ile 10 context / 5 paralel koşuyu çalıştırır
#   5. çıkışta yalnızca KENDİ başlattığı süreçleri kapatır (var olanlara dokunmaz)
#
# Kullanım:
#   bash qa-kit/offerwall/sandbox_up.sh              # başlat → koştur → temizle
#   bash qa-kit/offerwall/sandbox_up.sh --keep        # sunucuları açık bırak (artefakt incelemesi)
#   CONTEXTS=4 CONCURRENCY=2 bash qa-kit/offerwall/sandbox_up.sh
#
# Çıkış kodları: run_offerwall.py'nin kodu aynen döner (0 PASSED | 1 fail | 2 usage |
#                3 proxy | 130 interrupt); 10 = ön koşul hatası (port/venv/betik yok).
# ======================================================================================
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
PY="${PYTHON:-python3}"

WEB_PORT="${WEB_PORT:-8090}"
SMTP_PORT="${SMTP_PORT:-1025}"
IMAP_PORT="${IMAP_PORT:-1430}"
CONTEXTS="${CONTEXTS:-10}"
CONCURRENCY="${CONCURRENCY:-5}"

KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

LOGDIR="${ROOT}/artifacts/_sandbox_logs"
mkdir -p "${LOGDIR}"
STARTED_PIDS=()

log()  { printf '\033[1m[sandbox_up]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[sandbox_up] HATA:\033[0m %s\n' "$*" >&2; exit 10; }

port_open() {
  # Saf bash ile TCP yoklaması (netcat gerekmez).
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && exec 3>&- && return 0
  return 1
}

wait_port() {
  local port="$1" name="$2" tries=100
  for ((i = 0; i < tries; i++)); do
    if port_open "${port}"; then
      log "${name} hazır (127.0.0.1:${port})"
      return 0
    fi
    sleep 0.2
  done
  return 1
}

cleanup() {
  if [[ "${KEEP}" -eq 1 ]]; then
    log "sunucular açık bırakıldı (--keep). Kapatmak için: kill ${STARTED_PIDS[*]:-}"
    return 0
  fi
  for pid in "${STARTED_PIDS[@]:-}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      log "kapatıldı (pid ${pid})"
    fi
  done
}
trap cleanup EXIT

[[ -f "${HERE}/run_offerwall.py" ]] || fail "run_offerwall.py bulunamadı (${HERE})"
[[ -f "${ROOT}/examples/offerwall_sandbox.py" ]] || fail "examples/offerwall_sandbox.py bulunamadı (${ROOT})"
[[ -f "${ROOT}/qa-kit/devmail.py" ]] || fail "qa-kit/devmail.py bulunamadı (${ROOT})"

# --- 1) posta sunucusu ----------------------------------------------------------------
if port_open "${IMAP_PORT}" && port_open "${SMTP_PORT}"; then
  log "posta sunucusu zaten dinliyor (SMTP ${SMTP_PORT} / IMAP ${IMAP_PORT}) — yeniden başlatılmadı"
else
  log "devmail başlatılıyor (SMTP ${SMTP_PORT} / IMAP ${IMAP_PORT}) → ${LOGDIR}/devmail.log"
  (cd "${ROOT}" && exec "${PY}" qa-kit/devmail.py --smtp-port "${SMTP_PORT}" --imap-port "${IMAP_PORT}") \
    >"${LOGDIR}/devmail.log" 2>&1 &
  STARTED_PIDS+=("$!")
  wait_port "${SMTP_PORT}" "devmail SMTP" || fail "devmail SMTP ${SMTP_PORT} açılmadı; ${LOGDIR}/devmail.log"
  wait_port "${IMAP_PORT}" "devmail IMAP" || fail "devmail IMAP ${IMAP_PORT} açılmadı; ${LOGDIR}/devmail.log"
fi

# --- 2) offerwall sandbox -------------------------------------------------------------
if port_open "${WEB_PORT}"; then
  log "offerwall sandbox zaten dinliyor (127.0.0.1:${WEB_PORT}) — yeniden başlatılmadı"
else
  log "offerwall sandbox başlatılıyor (http://127.0.0.1:${WEB_PORT}) → ${LOGDIR}/sandbox.log"
  (cd "${ROOT}" && exec "${PY}" examples/offerwall_sandbox.py \
      --port "${WEB_PORT}" --quiet --smtp-host 127.0.0.1 --smtp-port "${SMTP_PORT}" \
      --public-url "http://127.0.0.1:${WEB_PORT}") >"${LOGDIR}/sandbox.log" 2>&1 &
  STARTED_PIDS+=("$!")
  wait_port "${WEB_PORT}" "offerwall sandbox" || fail "sandbox ${WEB_PORT} açılmadı; ${LOGDIR}/sandbox.log"
fi

# --- 3) koşu --------------------------------------------------------------------------
log "koşu başlıyor: ${CONTEXTS} context / ${CONCURRENCY} paralel (run_offerwall.py --sandbox)"
set +e
"${PY}" "${HERE}/run_offerwall.py" --sandbox \
  --contexts "${CONTEXTS}" --concurrency "${CONCURRENCY}" "${@:2}"
code=$?
set -e

log "run_offerwall.py çıkış kodu: ${code}"
exit "${code}"
```

---

## 6. EK ARTEFAKT — `postback_receiver.py` (API endpoint dayanıklılığı)

İstediğin "API endpoint'lerinin dayanıklılığını test etme" maddesinin meşru karşılığı: **senin
kendi S2S postback alıcın**. Partner entegrasyonlarında dayanıklılık tam olarak burada sınanır —
imzalı dönüşüm çağrıları yük altında gelir; tekrar denemeler, replay ve sahte imza da beraberinde.
Kaynak: `qa-kit/offerwall/postback_receiver.py` (aşağıda tam gömülü).

Uyguladığı kurallar: HMAC-SHA256 imza (`uid|offer_id|status|payout|ts`, sabit zamanlı karşılaştırma)
→ **401**; ±300 s replay penceresi → **410**; `uid` başına tek kabul (idempotency) → **409**;
bozuk payload → **400**; JSONL denetim günlüğü; `/health` ve `/metrics` (sayaçlar + p50/p95/p99).

```python
#!/usr/bin/env python3
"""``postback_receiver.py`` - S2S postback endpoint + durability / load self-test.

What this is for
----------------
Offerwall / CPA integrations live or die on the **server-to-server postback**: the network (or
your own offerwall) calls *your* endpoint to report a conversion. That endpoint has to survive
ten concurrent realities at once: duplicate retries, replayed requests, forged signatures,
out-of-window timestamps, malformed payloads and plain load.

This module is a production-shaped receiver plus a self-test that proves it behaves:

* ``GET  /postback?uid=…&offer_id=…&status=…&payout=…&ts=…&sig=…``  (the classic GET postback)
* ``POST /postback``                                              (JSON body, same signature)
* ``GET  /health``                                                 (liveness + counters)
* ``GET  /metrics``                                                (counters + p50/p95/p99 latency)

Durability rules enforced
-------------------------
1. **HMAC-SHA256 signature** over ``uid|offer_id|status|payout|ts`` (constant-time compare) →
   ``401`` when it does not match.
2. **Replay window** (``--replay-window``, default 300 s) → ``410`` when ``ts`` is too old/future.
3. **Idempotency**: a ``uid`` may be accepted exactly once → later ones return ``409`` *duplicate*
   (they are counted, never double-credited).
4. **Malformed payloads** (missing ``uid`` / unparsable body) → ``400``.
5. Every decision is appended to an optional JSONL audit log, so a disputed conversion can be
   replayed offline.

Self-test (``--selftest``)
--------------------------
Starts the receiver on an ephemeral port and drives it with ``--workers`` concurrent clients:

======================  ===========================================================
Scenario                Expected result
======================  ===========================================================
``--requests`` unique   all ``202 accepted``
same uids replayed       ``409`` for each (idempotency holds)
wrong signature           ``401`` for each
stale timestamp (ts-1h)   ``410`` for each
missing ``uid``           ``400`` for each
======================  ===========================================================

It then asserts the counters, prints a throughput/latency report and exits non-zero if anything
deviated - i.e. it is usable as a CI gate for *your* postback endpoint.

Usage
-----
Serve for real (point a network sandbox at it)::

    POSTBACK_SECRET=... python qa-kit/offerwall/postback_receiver.py --serve --port 8095

Run the durability gate in CI (10 workers, 400 conversions + attack traffic)::

    python qa-kit/offerwall/postback_receiver.py --selftest --workers 10 --requests 400

Exit codes: 0 every assertion held · 1 assertion failed · 2 usage/bind error.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final, Mapping, Optional, Sequence

logger = logging.getLogger("waft.offerwall.postback")

#: Signature is computed over exactly these fields, in this order (documented for partners).
SIGNED_FIELDS: Final[tuple[str, ...]] = ("uid", "offer_id", "status", "payout", "ts")

STATUS_ACCEPTED: Final[int] = 202
STATUS_MALFORMED: Final[int] = 400
STATUS_UNAUTHORIZED: Final[int] = 401
STATUS_DUPLICATE: Final[int] = 409
STATUS_EXPIRED: Final[int] = 410


# --------------------------------------------------------------------------------------
# signing helpers (shared by the receiver and the self-test client)
# --------------------------------------------------------------------------------------
def canonical_payload(fields: Mapping[str, Any]) -> str:
    """Return the canonical signing string ``uid=…|offer_id=…|status=…|payout=…|ts=…``."""
    return "|".join(f"{name}={fields.get(name, '')}" for name in SIGNED_FIELDS)


def sign_postback(secret: str, fields: Mapping[str, Any]) -> str:
    """Return the hex HMAC-SHA256 signature for *fields* (what a network would send)."""
    return hmac.new(secret.encode("utf-8"), canonical_payload(fields).encode("utf-8"), hashlib.sha256).hexdigest()


def signature_matches(secret: str, fields: Mapping[str, Any], provided: str) -> bool:
    """Constant-time comparison, tolerant of a ``sha256=`` prefix."""
    expected = sign_postback(secret, fields)
    candidate = (provided or "").strip()
    if candidate.lower().startswith("sha256="):
        candidate = candidate.split("=", 1)[1]
    return hmac.compare_digest(expected, candidate)


# --------------------------------------------------------------------------------------
# counters
# --------------------------------------------------------------------------------------
@dataclass(slots=True)
class Counters:
    """Thread-safe-enough counters for a single-process receiver (guarded by the server lock)."""

    received: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected_signature: int = 0
    rejected_expired: int = 0
    rejected_malformed: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def percentiles(self) -> dict[str, float]:
        if not self.latencies_ms:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
        ordered = sorted(self.latencies_ms)
        return {
            "p50": round(statistics.median(ordered), 2),
            "p95": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2),
            "p99": round(ordered[max(0, int(len(ordered) * 0.99) - 1)], 2),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "received": self.received,
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "rejected_signature": self.rejected_signature,
            "rejected_expired": self.rejected_expired,
            "rejected_malformed": self.rejected_malformed,
            **self.percentiles(),
        }


# --------------------------------------------------------------------------------------
# receiver
# --------------------------------------------------------------------------------------
class PostbackHandler(BaseHTTPRequestHandler):
    """HTTP handler implementing the durability rules described in the module docstring."""

    server_version = "WAFTPostback/1.0"

    # ------------------------------------------------------------------ plumbing
    def _json(self, payload: Mapping[str, Any], status: int) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802 - stdlib naming
        if getattr(self.server, "quiet", True):
            return
        logger.info("%s %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------------ routing
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        if parsed.path == "/postback":
            self._handle_postback(query)
        elif parsed.path == "/health":
            counters: Counters = self.server.counters  # type: ignore[attr-defined]
            self._json({"status": "ok", **counters.snapshot()}, 200)
        elif parsed.path == "/metrics":
            counters = self.server.counters  # type: ignore[attr-defined]
            self._json({"uptime_s": round(time.monotonic() - self.server.started_at, 2), **counters.snapshot()}, 200)
        else:
            self._json({"error": "not_found", "path": parsed.path}, 404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/postback":
            self._json({"error": "not_found", "path": parsed.path}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        payload: dict[str, Any] = {}
        try:
            if raw.strip().startswith("{"):
                parsed_body = json.loads(raw)
                if isinstance(parsed_body, dict):
                    payload = {str(key): value for key, value in parsed_body.items()}
            else:
                payload = {key: values[0] for key, values in urllib.parse.parse_qs(raw).items()}
        except (json.JSONDecodeError, ValueError) as exc:
            self._reject("malformed", message=f"body could not be parsed: {exc}")
            return
        header_signature = self.headers.get("X-WAFT-Signature")
        if header_signature and "sig" not in payload:
            payload["sig"] = header_signature
        self._handle_postback(payload)

    # ------------------------------------------------------------------ core logic
    def _handle_postback(self, fields: Mapping[str, Any]) -> None:
        server: Any = self.server
        counters: Counters = server.counters
        started = time.perf_counter()

        with server.lock:
            counters.received += 1

        uid = str(fields.get("uid") or "").strip()
        if not uid:
            self._reject("malformed", message="uid is required")
            return

        provided = str(fields.get("sig") or "")
        if not signature_matches(server.secret, fields, provided):
            self._reject("signature", message="signature mismatch")
            return

        try:
            timestamp = int(str(fields.get("ts") or "0"))
        except ValueError:
            self._reject("malformed", message="ts must be an integer (unix seconds)")
            return
        if abs(time.time() - timestamp) > server.replay_window:
            self._reject("expired", message=f"ts outside ±{int(server.replay_window)}s replay window")
            return

        with server.lock:
            if uid in server.seen_uids:
                counters.duplicates += 1
                duplicate = True
            else:
                server.seen_uids[uid] = dict(fields)
                counters.accepted += 1
                duplicate = False
            counters.latencies_ms.append((time.perf_counter() - started) * 1000.0)
            if len(counters.latencies_ms) > 50_000:  # bound memory on long runs
                del counters.latencies_ms[:25_000]

        if server.audit_path:
            try:
                with server.audit_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {"at": time.time(), "decision": "duplicate" if duplicate else "accepted", "fields": dict(fields)},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except OSError as exc:  # noqa: BLE001 - audit must never break the endpoint
                logger.debug("audit log write failed: %s", exc)

        if duplicate:
            self._json({"status": "duplicate", "uid": uid, "credited": False}, STATUS_DUPLICATE)
        else:
            self._json({"status": "accepted", "uid": uid, "credited": True}, STATUS_ACCEPTED)

    def _reject(self, kind: str, *, message: str) -> None:
        server: Any = self.server
        counters: Counters = server.counters
        mapping = {
            "malformed": ("rejected_malformed", STATUS_MALFORMED),
            "signature": ("rejected_signature", STATUS_UNAUTHORIZED),
            "expired": ("rejected_expired", STATUS_EXPIRED),
        }
        attribute, status = mapping[kind]
        with server.lock:
            setattr(counters, attribute, getattr(counters, attribute) + 1)
        self._json({"status": "rejected", "reason": kind, "message": message}, status)


class PostbackServer(ThreadingHTTPServer):
    """Threaded receiver with shared state (counters, uid ledger, secret, replay window)."""

    allow_reuse_address = True
    daemon_threads = True
    # ThreadingHTTPServer defaults to a listen backlog of 5; with 10 concurrent postback senders
    # the accept queue overflows and Linux re-sends the SYN after ~1 s - visible as a p99 outlier
    # in the load test (measured: p99 1.01 s). 128 is a sane production value.
    request_queue_size = 128

    def __init__(self, address: tuple[str, int], *, secret: str, replay_window: float, audit_path: Optional[Path], quiet: bool) -> None:
        super().__init__(address, PostbackHandler)
        self.secret = secret
        self.replay_window = replay_window
        self.counters = Counters()
        self.seen_uids: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.audit_path = audit_path
        self.quiet = quiet
        self.started_at = time.monotonic()


# --------------------------------------------------------------------------------------
# self-test client
# --------------------------------------------------------------------------------------
@dataclass(slots=True)
class ProbeResult:
    """One HTTP call result from the load generator."""

    status: int
    latency_ms: float
    expected: int
    label: str

    @property
    def ok(self) -> bool:
        return self.status == self.expected


def _request(url: str, params: Mapping[str, Any], *, timeout: float) -> tuple[int, float]:
    """Fire one GET postback; return ``(status_code, latency_ms)`` (never raises)."""
    query = urllib.parse.urlencode({key: str(value) for key, value in params.items()})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(f"{url}?{query}", timeout=timeout) as response:
            response.read()
            return int(response.status), (time.perf_counter() - started) * 1000.0
    except urllib.error.HTTPError as exc:
        exc.read()
        return int(exc.code), (time.perf_counter() - started) * 1000.0
    except Exception as exc:  # noqa: BLE001 - network hiccups are reported as status 0
        logger.debug("request failed: %s", exc)
        return 0, (time.perf_counter() - started) * 1000.0


def run_selftest(args: argparse.Namespace) -> int:
    """Start the receiver on an ephemeral port and prove every durability rule holds."""
    secret = args.secret or os.environ.get("POSTBACK_SECRET") or "selftest-secret"
    audit = Path(args.audit) if args.audit else None

    server = PostbackServer(
        ("127.0.0.1", args.port or 0),
        secret=secret,
        replay_window=args.replay_window,
        audit_path=audit,
        quiet=True,
    )
    host, port = server.server_address[:2]
    endpoint = f"http://{host}:{port}/postback"
    thread = threading.Thread(target=server.serve_forever, name="postback-receiver", daemon=True)
    thread.start()
    print(f"→ receiver up: {endpoint}  (secret={'<given>' if args.secret else '<env/default>'}, replay window ±{int(args.replay_window)}s)")

    now = int(time.time())
    unique: list[dict[str, Any]] = []
    for worker in range(args.workers):
        for index in range(max(1, args.requests // args.workers)):
            fields = {
                "uid": f"uid-{worker:02d}-{index:05d}",
                "offer_id": f"offer-{index % 7 + 1}",
                "status": "completed",
                "payout": f"{0.10 + (index % 5) * 0.05:.2f}",
                "ts": now,
            }
            fields["sig"] = sign_postback(secret, fields)
            unique.append(fields)

    # Two strictly separated phases: replayed uids are only fired *after* the unique phase has
    # finished, otherwise an original and its duplicate race and either may win - the receiver
    # would still be correct, but the test could not attribute the 202/409 outcomes (observed:
    # 400 accepted + 20 duplicates, yet labels mismatched). Deterministic beats clever.
    phase_unique: list[tuple[str, dict[str, Any], int]] = [
        (f"valid #{position}", fields, STATUS_ACCEPTED) for position, fields in enumerate(unique)
    ]
    stale_ts = now - int(args.replay_window) - 600
    phase_hostile: list[tuple[str, dict[str, Any], int]] = [
        ("duplicate", dict(fields), STATUS_DUPLICATE) for fields in unique[:20]
    ]
    phase_hostile += [
        ("bad signature", {**{k: v for k, v in fields.items() if k != "sig"}, "sig": "0" * 64}, STATUS_UNAUTHORIZED)
        for fields in unique[:10]
    ]
    phase_hostile += [
        (
            "stale ts",
            {
                **{k: v for k, v in fields.items() if k not in {"ts", "sig"}},
                "ts": stale_ts,
                "sig": sign_postback(
                    secret,
                    {**{k: v for k, v in fields.items() if k not in {"ts", "sig"}}, "ts": stale_ts},
                ),
            },
            STATUS_EXPIRED,
        )
        for fields in unique[10:20]
    ]
    phase_hostile += [
        ("missing uid", {"offer_id": "offer-1", "status": "completed", "payout": "0.10", "ts": now}, STATUS_MALFORMED),
    ]

    print(
        f"→ faz 1: {len(phase_unique)} benzersiz dönüşüm, {args.workers} paralel istemci\n"
        f"→ faz 2: {len(phase_hostile)} saldırı/yeniden gönderim "
        f"(duplicate + sahte imza + süresi geçmiş + bozuk payload)"
    )
    # Warm-up: the very first request pays thread-pool/TCP setup cost (measured ~1s p99 outlier);
    # it is fired before the clock starts, is not a scenario result, and is excluded from the
    # assertions via the counter baseline taken right after it.
    _request(endpoint, {"uid": "warmup", "offer_id": "0", "status": "warmup", "payout": "0", "ts": now, "sig": ""}, timeout=args.timeout)
    baseline = server.counters.snapshot()

    started = time.perf_counter()
    results: list[ProbeResult] = []
    for phase_name, plan in (("unique", phase_unique), ("hostile", phase_hostile)):
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(_request, endpoint, fields, timeout=args.timeout) for _label, fields, _expect in plan]
            for (label, _fields, expected), future in zip(plan, futures):
                status, latency = future.result()
                results.append(ProbeResult(status=status, latency_ms=latency, expected=expected, label=label))
        logger.debug("phase %s finished", phase_name)
    elapsed = time.perf_counter() - started

    failures = [result for result in results if not result.ok]
    counters = server.counters.snapshot()
    latencies = [result.latency_ms for result in results] or [0.0]
    ordered = sorted(latencies)
    self_test_report = {
        "requests": len(results),
        "workers": args.workers,
        "elapsed_s": round(elapsed, 2),
        "throughput_rps": round(len(results) / elapsed, 1) if elapsed else 0.0,
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2),
        "p99_ms": round(ordered[max(0, int(len(ordered) * 0.99) - 1)], 2),
    }

    print("\n  senaryo sonuçları")
    for label in ("valid", "duplicate", "bad signature", "stale ts", "missing uid"):
        subset = [result for result in results if result.label.startswith(label)]
        expected_status = subset[0].expected if subset else 0
        observed = sorted({result.status for result in subset})
        verdict = "OK " if subset and all(result.ok for result in subset) else "FAIL"
        print(f"    {verdict} {label:14s} n={len(subset):4d}  beklenen={expected_status}  gözlenen={observed}")

    print("\n  receiver metrikleri")
    for key, value in counters.items():
        print(f"    {key:22s}: {value}")

    print("\n  yük profili")
    for key, value in self_test_report.items():
        print(f"    {key:22s}: {value}")

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)

    # Assertions run on deltas measured from the post-warm-up baseline, so the (deliberately
    # rejected) warm-up request can never skew the expected numbers.
    additive = ("received", "accepted", "duplicates", "rejected_signature", "rejected_expired", "rejected_malformed")
    delta = {key: int(counters[key]) - int(baseline[key]) for key in additive}
    print("\n  bu koşunun sayaç farkı (warm-up hariç)")
    for key in additive:
        print(f"    {key:22s}: {delta[key]}")

    assertions = [
        (not failures, f"{len(failures)} request(s) returned an unexpected status: "
                       + ", ".join(f"{item.label}→{item.status}" for item in failures[:5])),
        (delta["accepted"] == len(unique), f"expected {len(unique)} accepted, got {delta['accepted']}"),
        (delta["duplicates"] == 20, f"expected 20 duplicates, got {delta['duplicates']}"),
        (delta["rejected_signature"] == 10, f"expected 10 forged signatures rejected, got {delta['rejected_signature']}"),
        (delta["rejected_expired"] == 10, f"expected 10 stale requests rejected, got {delta['rejected_expired']}"),
        (delta["rejected_malformed"] >= 1, "malformed payloads were not rejected"),
        (delta["received"] == delta["accepted"] + delta["duplicates"]
         + delta["rejected_signature"] + delta["rejected_expired"] + delta["rejected_malformed"],
         "counter bookkeeping does not add up"),
    ]
    broken = [message for ok, message in assertions if not ok]
    print("\n" + "=" * 88)
    if broken:
        for message in broken:
            print(f"✖ FAIL: {message}")
        print("=" * 88)
        return 1
    print(
        f"✔ postback receiver durability gate PASSED — {delta['accepted']} kabul, "
        f"{delta['duplicates']} duplicate, {delta['rejected_signature']} sahte imza, "
        f"{delta['rejected_expired']} süresi geçmiş, {delta['rejected_malformed']} bozuk payload; "
        f"{self_test_report['throughput_rps']} rps, p50 {self_test_report['p50_ms']} ms, "
        f"p95 {self_test_report['p95_ms']} ms, p99 {self_test_report['p99_ms']} ms "
        f"({args.workers} paralel istemci)"
    )
    print("=" * 88)
    return 0


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="postback_receiver.py",
        description="S2S postback endpoint (HMAC + replay window + idempotency) and its durability self-test.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--serve", action="store_true", help="Run the receiver and stay up (real traffic).")
    mode.add_argument("--selftest", action="store_true", help="Run the load/durability gate and exit.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (--serve).")
    parser.add_argument("--port", type=int, default=0, help="Port (0 = ephemeral; --serve default 8095).")
    parser.add_argument("--secret", default=None, help="HMAC shared secret (falls back to POSTBACK_SECRET).")
    parser.add_argument("--replay-window", type=float, default=300.0, help="Accepted |now - ts| in seconds.")
    parser.add_argument("--audit", default=None, help="Append-only JSONL audit log path.")
    parser.add_argument("--workers", type=int, default=10, help="Concurrent clients in --selftest.")
    parser.add_argument("--requests", type=int, default=400, help="Unique conversions in --selftest.")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout (s).")
    parser.add_argument("--quiet", action="store_true", help="Do not log every request.")
    parser.add_argument("--log-level", default="WARNING", help="Log level for the receiver itself.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.selftest or not args.serve:
        return run_selftest(args)

    secret = args.secret or os.environ.get("POSTBACK_SECRET")
    if not secret:
        print("✖ --serve needs a shared secret (--secret or POSTBACK_SECRET)", file=sys.stderr)
        return 2
    port = args.port or 8095
    try:
        server = PostbackServer(
            (args.host, port),
            secret=secret,
            replay_window=args.replay_window,
            audit_path=Path(args.audit) if args.audit else None,
            quiet=args.quiet,
        )
    except OSError as exc:
        print(f"✖ could not bind {args.host}:{port}: {exc}", file=sys.stderr)
        return 2
    print(f"postback receiver listening on http://{args.host}:{port}/postback  (Ctrl+C to stop)")
    print(f"  signing fields : {'|'.join(SIGNED_FIELDS)}")
    print(f"  replay window  : ±{int(args.replay_window)}s | idempotency: uid accepted once")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down…")
    finally:
        counters = server.counters.snapshot()
        server.server_close()
        print(f"  final counters : {json.dumps(counters, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Gerçek koşu (bu repoda, `--workers 10`):

```bash
python qa-kit/offerwall/postback_receiver.py --selftest --workers 10 --requests 500
```

```text
  senaryo sonuçları
    OK  valid          n= 500  beklenen=202  gözlenen=[202]
    OK  duplicate      n=  20  beklenen=409  gözlenen=[409]
    OK  bad signature  n=  10  beklenen=401  gözlenen=[401]
    OK  stale ts       n=  10  beklenen=410  gözlenen=[410]
    OK  missing uid    n=   1  beklenen=400  gözlenen=[400]

✔ postback receiver durability gate PASSED — 500 kabul, 20 duplicate, 10 sahte imza,
  10 süresi geçmiş, 1 bozuk payload; 1107.0 rps, p50 8.58 ms, p95 11.36 ms, p99 12.66 ms
```

CI kapısı olarak kullanılabilir: konfigürasyon bozulduğunda (ör. `--replay-window 0.001`)
assertion'lar düşer ve **exit kodu 1** olur (test edildi); doğru konfigürasyonda **exit 0**.

Bu artefakt yazılırken bulunan ve düzeltilen iki gerçek kusur:

1. **Test tasarımı yarışı:** ilk sürümde aynı `uid` hem "geçerli" hem "duplicate" olarak aynı anda
   ateşleniyordu; hangisi önce varırsa kazanıyordu (sayaçlar doğruydu ama etiketler karışıyordu).
   Artık iki fazlı: önce tüm benzersiz dönüşümler, sonra saldırı/yeniden gönderim trafiği; sayaç
   doğrulaması warm-up sonrası alınan baseline'ın delta'sı üzerinden yapılıyor.
2. **Listen backlog:** `ThreadingHTTPServer`'ın varsayılan `request_queue_size=5` değeri 10 paralel
   göndericide kuyruğu taşırıyor, Linux SYN'i ~1 s sonra tekrar deniyordu → istemci tarafında
   p99 **1015 ms** ve 255 rps. `request_queue_size = 128` ile: **p99 12.7 ms**, **1107 rps**.

Gerçek bir partnerin/ağın postback göndermesini bekliyorsan:

```bash
POSTBACK_SECRET='paylasilan-anahtar' python qa-kit/offerwall/postback_receiver.py \
  --serve --host 0.0.0.0 --port 8095 --audit artifacts/postbacks.jsonl
curl -s localhost:8095/health   | python -m json.tool
curl -s localhost:8095/metrics  | python -m json.tool
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

## RUNBOOK yazılırken yeniden doğrulama (bu tur)

* **Ortam sıfırlandı** (pip paketleri + tarayıcı önbelleği snapshot dışında) → `pip install -r
  requirements.txt`, `python -m playwright install --with-deps chromium` yeniden çalıştırıldı;
  ardından kit yeniden koşuldu.
* `run_offerwall.py --sandbox` → **`run-20260928-184434-a611fb`: 10/10 context, 60/60 hedef,
  370/370 adım, 20/20 e-posta doğrulama, 4 API ucu, exit 0** (~48 s).
* `bash qa-kit/offerwall/sandbox_up.sh` → sunucuları kendisi kaldırdı, aynı sonucu verdi,
  çıkışta yalnızca kendi başlattığı süreçleri kapattı (kapanış port kontrolü ile doğrulandı).
* **Yeni: çalışma anında scope kapısı.** `run_offerwall.py` artık `run_regression.py`'nin
  allow-list'ini ve sert 3. parti block-list'ini *uyguluyor*. Doğrulanan 3 senaryo (§7):
  `timewall.io` → exit 2; `--allow-host timewall.io --i-am-authorized` → **yine exit 2**;
  listede olmayan `example.com` → exit 2.
* Bu kapı yazılırken çıkan gerçek hata: `host_of()` netloc'u port'la döndürdüğü için (`127.0.0.1:8090`)
  scope dosyasındaki çıplak `127.0.0.1` eşleşmiyordu → **kapı, belgelenen tek-komut sandbox koşusunu
  kırardı**. Port/IPv6 normalizasyonu (`scope_host_candidates`) eklendi ve sandbox koşusu yeniden
  yeşil.
* `qa-kit/offerwall/build_delivery.py` eklendi: bu dokümanı gömülü kaynaklardan idempotent biçimde
  üretir (`--check` drift denetimi; iki ardışık koşuda byte-özdeş çıktı doğrulandı).

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
