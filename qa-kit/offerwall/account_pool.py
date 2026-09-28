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
