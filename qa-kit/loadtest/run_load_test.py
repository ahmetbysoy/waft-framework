#!/usr/bin/env python3
"""``run_load_test.py`` - your own sign-up flow under N concurrent browser contexts.

What it measures
----------------
Real browsers (Playwright/Chromium) behind isolated contexts: 10 contexts by default, one test
account per context, form fill + submit + e-mail verification. The point is to observe **your
own** staging system: how it behaves with 10 concurrent users and how your WAF sees that
traffic. It is a functional/load probe, not an attack tool: no CAPTCHA solving, no bypass
logic, no third-party hosts.

Hard rules enforced by this script
----------------------------------
1. **No hard-coded third-party URL.** Every target URL comes from ``TARGET_URL`` (environment
   variable) plus the relative path in ``test_targets.json``. If ``TARGET_URL`` is missing the
   script stops with exit code 2 before a browser is launched.
2. **Scope gate.** The resolved hosts are checked against ``qa-kit/authorized_hosts.txt`` and a
   hard block-list of third-party offerwall / micro-task platforms; an unlisted host stops the
   run (override only with ``--i-am-authorized``, for environments you own).
3. **CAPTCHA is detected, never solved.** When a CAPTCHA marker is visible the step is logged as
   ``CAPTCHA_DETECTED``, no exception is raised, that context is closed safely and the run
   continues (``--fail-on-captcha`` turns it into a failure if you prefer a red build).

Artefacts produced per run (``artifacts/loadtest/<run_id>/``)
------------------------------------------------------------
* ``network_log.jsonl``  - every request/response: method, url, status, duration (required for
                           API contract verification).
* ``endpoints.json``     - method+path aggregation of the above (schema/pact sanity check).
* ``steps.jsonl``        - per-step journal: which selector matched, what was filled, timings.
* ``screenshots/ctx-XX/``- one PNG per step (``--screenshots on|off``).
* ``summary.json``       - machine-readable run summary (per context + totals).
* ``report.txt``         - the same summary the terminal shows.

Usage
-----
Own staging (real accounts with app passwords)::

    export TARGET_URL=https://staging.sirketiniz.com
    python3 qa-kit/loadtest/run_load_test.py \\
        --credentials credentials_pool.local.json \\
        --targets qa-kit/loadtest/test_targets.json \\
        --contexts 10 --concurrency 10 --imap-ssl auto

Bundled local sandbox (bundled mock server + devmail, zero credentials needed)::

    bash qa-kit/offerwall/sandbox_up.sh
    python3 qa-kit/loadtest/run_load_test.py --sandbox

Validation only, no browser (CI friendly)::

    python3 qa-kit/loadtest/run_load_test.py --sandbox --check

Exit codes: ``0`` pass (CAPTCHA skips do not fail the run) | ``1`` target/verification failure |
``2`` usage, scope, credential or configuration error | ``130`` interrupted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable, Literal, Optional, Sequence

# --- make the package + sibling kit modules importable when run as a plain script -------------
_HERE: Final[Path] = Path(__file__).resolve().parent
_KIT: Final[Path] = _HERE.parent
_REPO: Final[Path] = _KIT.parent
#: ``account_pool.py`` (validators) lives in the offerwall kit; it is imported, not copied, so
#: both kits keep exactly one definition of "what a valid test account looks like".
_OFFERWALL: Final[Path] = _KIT / "offerwall"
for _path in (str(_REPO), str(_HERE), str(_KIT), str(_OFFERWALL)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: Two *independent* import groups, because they fail for different reasons:
#:
#: * the **kit siblings** (``account_pool``, ``run_regression``) are pure stdlib - the scope gate,
#:   the e-mail/password validators and the selector/target loaders must keep working on a machine
#:   where the browser layer is not installed yet (fresh CI runner, new laptop). "Refused" must
#:   never degrade into a traceback.
#: * the **waft** package pulls the Playwright driver in through ``waft/__init__``, so only this
#:   group is allowed to be missing - and only until the run actually needs a browser.
_KIT_IMPORT_ERROR: Optional[str] = None
_WAFT_IMPORT_ERROR: Optional[str] = None
CAPTCHA_SELECTORS: tuple[str, ...] = ()

try:  # pragma: no cover - environment dependent
    from account_pool import (  # noqa: E402  (qa-kit/offerwall sibling - validators reused)
        EMAIL_RE,
        HOST_RE,
        PASTE_ARTIFACT_MARKERS,
        looks_like_placeholder,
    )
    from run_regression import (  # noqa: E402  (single source of truth for the scope gate)
        BLOCKED_THIRD_PARTY_SUFFIXES,
        SCOPE_HELP,
        Scope,
        host_of,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
    _KIT_IMPORT_ERROR = str(exc)
    EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$|^\d{1,3}(\.\d{1,3}){3}$")
    PASTE_ARTIFACT_MARKERS = ("`", "<", ">")
    BLOCKED_THIRD_PARTY_SUFFIXES = ()

    def looks_like_placeholder(password: str) -> bool:
        return not (password or "").strip()

    class Scope:  # minimal stand-in: allow nothing unless a pattern matches
        def __init__(self, patterns: tuple[str, ...] = ()) -> None:
            self.patterns = patterns

        def allows(self, host: str) -> bool:
            host = (host or "").lower()
            return any(
                host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:]))
                for pattern in self.patterns
            )

        @classmethod
        def load(cls, path: Any, extra: Sequence[str]) -> "Scope":
            patterns = [item.strip().lower() for item in extra if item.strip()]
            if path and Path(path).exists():
                for line in Path(path).read_text(encoding="utf-8").splitlines():
                    cleaned = line.split("#", 1)[0].strip().lower()
                    if cleaned:
                        patterns.append(cleaned)
            return cls(tuple(dict.fromkeys(patterns)))

    def host_of(url: str) -> str:
        try:
            from urllib.parse import urlsplit as _urlsplit

            return (_urlsplit(url).netloc or "").lower()
        except Exception:  # noqa: BLE001
            return ""

    SCOPE_HELP = "add the host to qa-kit/authorized_hosts.txt (or pass --i-am-authorized)."

try:  # pragma: no cover - environment dependent
    from waft.config import Config, resolve_device_profiles  # noqa: E402
    from waft.forms import CAPTCHA_SELECTORS  # noqa: E402
    from waft.imap_client import ImapClient, ImapSettings  # noqa: E402
    from waft.logging_setup import setup_logging  # noqa: E402
    from waft.models import ContextProfile  # noqa: E402
    from waft.stealth import build_init_script  # noqa: E402
    from waft.utils import (  # noqa: E402
        ensure_dir,
        human_ms,
        mask_secret,
        new_context_id,
        new_run_id,
        now_iso,
        read_json,
        truncate,
        write_json,
        write_jsonl,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
    _WAFT_IMPORT_ERROR = str(exc)
    import datetime as _dt
    import json as _json
    import uuid as _uuid

    Config = None  # type: ignore[assignment]
    resolve_device_profiles = None  # type: ignore[assignment]
    ImapClient = None  # type: ignore[assignment]
    ImapSettings = None  # type: ignore[assignment]
    setup_logging = None  # type: ignore[assignment]
    ContextProfile = None  # type: ignore[assignment]
    build_init_script = None  # type: ignore[assignment]

    def ensure_dir(path: Any) -> Path:
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def human_ms(value: Any) -> str:
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            return "0 ms"
        return f"{number:.0f} ms" if number < 1000 else f"{number / 1000:.2f} s"

    def mask_secret(value: Any, *, keep: int = 3, mask: str = "***") -> str:
        text = "" if value is None else str(value)
        if not text:
            return ""
        head = text[:keep] if len(text) > 6 else ""
        return f"{head}{mask}({len(text)} chars)"

    def new_run_id(prefix: str = "run") -> str:
        return f"{prefix}-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{_uuid.uuid4().hex[:6]}"

    def new_context_id(index: int, prefix: str = "ctx") -> str:
        return f"{prefix}-{index:02d}-{_uuid.uuid4().hex[:4]}"

    def now_iso() -> str:
        return _dt.datetime.now().astimezone().isoformat(timespec="seconds")

    def truncate(value: Any, limit: int = 200, suffix: str = "…") -> str:
        text = "" if value is None else str(value)
        return text if len(text) <= limit else text[: max(0, limit - len(suffix))] + suffix

    def read_json(path: Any, *, default: Any = None) -> Any:
        try:
            return _json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def write_json(path: Any, payload: Any, *, indent: int = 2) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_json.dumps(payload, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")
        return target

    def write_jsonl(path: Any, records: Any, *, append: bool = False) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a" if append else "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(_json.dumps(record, ensure_ascii=False) + "\n")
        return target

logger = logging.getLogger("waft.loadtest")

#: Playwright is imported defensively: the error a user sees when it is missing must be a
#: sentence, not a stack trace from the middle of this module.
_PLAYWRIGHT_IMPORT_ERROR: Optional[str] = None
try:  # pragma: no cover - import side effect
    from playwright.async_api import (  # type: ignore[import-not-found]
        Browser,
        BrowserContext,
        Page,
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )
except ModuleNotFoundError as _exc:  # pragma: no cover - environment dependent
    _PLAYWRIGHT_IMPORT_ERROR = str(_exc)
    Browser = BrowserContext = Page = Any  # type: ignore[assignment,misc]
    PlaywrightTimeoutError = TimeoutError  # type: ignore[assignment,misc]

# ======================================================================================
# constants
# ======================================================================================
STATUS_OK: Final[str] = "ok"
STATUS_FAILED: Final[str] = "failed"
STATUS_CAPTCHA: Final[str] = "captcha_detected"
STATUS_VERIFICATION_FAILED: Final[str] = "verification_failed"
STATUS_ERROR: Final[str] = "error"

TargetStatus = Literal["ok", "failed", "captcha_detected", "verification_failed", "error"]

#: Placeholders resolved per context (``{ctx}`` = context index, ``{run}`` = run id).
CONTEXT_PLACEHOLDERS: Final[tuple[str, ...]] = ("{ctx}", "{context}", "{run}", "{run_id}")

#: Query parameters whose *values* are masked in ``network_log.jsonl``.
SENSITIVE_QUERY_KEYS: Final[tuple[str, ...]] = (
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "otp",
    "code",
    "secret",
    "api_key",
    "apikey",
    "session",
    "auth",
)

#: IMAP host inference - only used when ``imap_host`` is absent from the pool file.
IMAP_PRESETS: Final[dict[str, tuple[str, int, bool]]] = {
    "gmail.com": ("imap.gmail.com", 993, True),
    "googlemail.com": ("imap.gmail.com", 993, True),
    "outlook.com": ("outlook.office365.com", 993, True),
    "hotmail.com": ("outlook.office365.com", 993, True),
    "live.com": ("outlook.office365.com", 993, True),
    "yandex.com": ("imap.yandex.com", 993, True),
    "yandex.ru": ("imap.yandex.com", 993, True),
    "demo.waft.local": ("127.0.0.1", 1430, False),
    "waft.local": ("127.0.0.1", 1430, False),
}

#: Timezone rotation - a context whose timezone contradicts its locale is a fingerprint, so the
#: value is derived from the device locale unless the caller overrides it.
TIMEZONE_FALLBACKS: Final[tuple[str, ...]] = (
    "Europe/Istanbul",
    "Europe/Berlin",
    "Europe/London",
    "America/New_York",
    "America/Los_Angeles",
)


class LoadTestError(RuntimeError):
    """Any fatal, user-facing problem in this driver (usage, scope, credentials, config)."""


# ======================================================================================
# ARTEFAKT 1 - credentials pool
# ======================================================================================
@dataclass(frozen=True, slots=True)
class TestAccount:
    """One test account, exactly as ``credentials_pool.json`` describes it."""

    index: int
    email: str
    app_password: str
    imap_host: str
    imap_port: int
    imap_ssl: Optional[bool] = None
    enabled: bool = True
    note: str = ""
    placeholder_password: bool = False

    def masked_password(self) -> str:
        """``abc***(16 hane)`` - the only representation that may reach a log or a report."""
        return mask_secret(self.app_password, keep=3)

    def ssl_flag(self, default: bool) -> bool:
        """Per-account ``imap_ssl`` when present, otherwise the run-wide default."""
        return default if self.imap_ssl is None else bool(self.imap_ssl)

    def describe(self) -> str:
        return (
            f"{self.email} | imap {self.imap_host}:{self.imap_port} "
            f"| ssl={'default' if self.imap_ssl is None else self.imap_ssl} "
            f"| pass {self.masked_password()}"
        )

    def to_dict(self, *, mask: bool = True) -> dict[str, Any]:
        return {
            "index": self.index,
            "email": self.email,
            "app_password": self.masked_password() if mask else self.app_password,
            "imap_host": self.imap_host,
            "imap_port": self.imap_port,
            "imap_ssl": self.imap_ssl,
            "enabled": self.enabled,
            "note": self.note,
            "placeholder_password": self.placeholder_password,
        }


def repair_app_password(raw: Any) -> str:
    """Clean up the two ways an app password normally arrives: pasted Markdown and spaces.

    Gmail shows app passwords as four groups of four characters (``abcd efgh ijkl mnop``) and
    people copy them from a mail/markdown document, so quotes, backticks, ``mailto:`` prefixes,
    line breaks, tabs and the grouping spaces are stripped here. Nothing is guessed: an
    unrepairable value is returned as-is and flagged by :func:`looks_like_placeholder`.
    """
    text = "" if raw is None else str(raw)
    text = text.replace("\u00a0", " ")
    for marker in PASTE_ARTIFACT_MARKERS:
        text = text.replace(marker, "")
    text = text.strip().strip("\"'`").strip()
    if re.fullmatch(r"(?:[A-Za-z0-9]{4}[ \-]){3}[A-Za-z0-9]{4}", text):
        text = re.sub(r"[ \-]", "", text)
    return text.strip()


def load_credentials_pool(
    path: Path,
    *,
    allow_placeholders: bool = False,
) -> tuple[list[TestAccount], list[str]]:
    """Read ``credentials_pool.json`` and return ``(enabled_accounts, warnings)``.

    The loader validates instead of trusting: e-mail shape, IMAP host shape, port range and
    "is this still the template?" are all checked. Placeholder passwords are allowed only while
    they are *warnings* (you may be running the sandbox profile); if **every** enabled account
    still carries a placeholder the run cannot do IMAP verification, so it is refused here with
    an actionable message rather than 10 identical login failures later.
    """
    source = Path(path).expanduser()
    if not source.exists():
        raise LoadTestError(
            f"credential pool not found: {source}\n"
            f"→ template: qa-kit/loadtest/credentials_pool.json | local sandbox: "
            f"qa-kit/loadtest/credentials_pool.sandbox.json"
        )
    try:
        payload: Any = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoadTestError(f"{source}: invalid JSON ({exc})") from exc
    except OSError as exc:
        raise LoadTestError(f"{source}: unreadable ({exc})") from exc

    if isinstance(payload, dict):
        entries = payload.get("accounts") or payload.get("credentials") or [payload]
    elif isinstance(payload, list):
        entries = payload
    else:  # pragma: no cover - defensive
        raise LoadTestError(f"{source}: expected a JSON array (or an object with 'accounts')")

    accounts: list[TestAccount] = []
    warnings: list[str] = []
    seen: dict[str, int] = {}

    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            warnings.append(f"{source.name}: entry #{position} ignored (not an object)")
            continue
        if not entry.get("email"):
            continue  # documentation-only entry ("_comment" blocks)

        email = str(entry.get("email") or "").strip()
        if not EMAIL_RE.match(email):
            raise LoadTestError(
                f"{source}: entry #{position} has an invalid e-mail address ({truncate(email, 60)!r}). "
                f"Fix the address; the pool is validated before any browser starts."
            )
        key = email.lower()
        if key in seen:
            warnings.append(f"{source.name}: duplicate account {email} ignored (first entry wins)")
            continue
        seen[key] = position

        raw_password = entry.get("app_password", entry.get("password"))
        app_password = repair_app_password(raw_password)
        is_placeholder = looks_like_placeholder(app_password)

        domain = email.rsplit("@", 1)[-1].lower()
        preset = IMAP_PRESETS.get(domain)
        host_raw = str(entry.get("imap_host") or "").strip()
        inferred_ssl: Optional[bool] = None
        if host_raw:
            default_port = 993
        elif preset is not None:
            host_raw, default_port, preset_ssl = preset
            inferred_ssl = preset_ssl
            warnings.append(f"{email}: imap_host missing → inferred {host_raw} from the domain")
        else:
            raise LoadTestError(
                f"{source}: {email} has no 'imap_host'. Add the IMAP server explicitly "
                f"(e.g. \"imap_host\": \"imap.gmail.com\", \"imap_port\": 993)."
            )

        if not HOST_RE.match(host_raw):
            raise LoadTestError(
                f"{source}: {email} has an invalid imap_host ({truncate(host_raw, 60)!r}); "
                f"expected a hostname or IPv4 literal, without scheme or port."
            )

        try:
            imap_port = int(entry.get("imap_port") or default_port)
        except (TypeError, ValueError) as exc:
            raise LoadTestError(f"{source}: {email} has a non-numeric imap_port") from exc
        if not 1 <= imap_port <= 65535:
            raise LoadTestError(f"{source}: {email} has an out-of-range imap_port ({imap_port})")

        ssl_raw = entry.get("imap_ssl")
        imap_ssl = inferred_ssl if ssl_raw is None else bool(ssl_raw)
        if imap_ssl is True and imap_port == 1430:
            warnings.append(f"{email}: imap_ssl=true on port {imap_port} - sandboxes are plaintext")
        elif imap_ssl is None and imap_port == 1430:
            imap_ssl = False

        accounts.append(
            TestAccount(
                index=len(accounts),
                email=email,
                app_password=app_password,
                imap_host=host_raw,
                imap_port=imap_port,
                imap_ssl=imap_ssl,
                enabled=bool(entry.get("enabled", True)),
                note=str(entry.get("note") or ""),
                placeholder_password=is_placeholder,
            )
        )

    enabled = [account for account in accounts if account.enabled]
    disabled = len(accounts) - len(enabled)
    if not enabled:
        raise LoadTestError(f"{source}: no enabled accounts (the pool needs at least one)")

    placeholders = [account.email for account in enabled if account.placeholder_password]
    if placeholders and not allow_placeholders:
        raise LoadTestError(
            f"{source}: {len(placeholders)} account(s) still use the template app password "
            f"(e.g. {placeholders[0]}).\n"
            f"→ real run: copy the template and paste 16-character app passwords.\n"
            f"→ sandbox: use qa-kit/loadtest/credentials_pool.sandbox.json (or --sandbox).\n"
            f"→ form-filling only (IMAP will fail loudly): pass --allow-placeholders."
        )
    for email in placeholders:
        warnings.append(f"{email}: template app password - IMAP verification will fail for it")
    if disabled:
        warnings.append(f"{disabled} account(s) skipped because enabled=false")

    logger.info("credential pool: %d enabled account(s) from %s", len(enabled), source.name)
    return enabled, warnings


def assign_account(
    accounts: Sequence[TestAccount],
    context_index: int,
    *,
    allow_reuse: bool = False,
) -> TestAccount:
    """Return the account owned by ``context_index`` (context 0 → account 0, …).

    Uniqueness is the whole point of a per-context account pool: two contexts sharing one
    mailbox would race for the same verification e-mail and the second one would time out.
    With fewer accounts than contexts this raises unless ``allow_reuse`` is set explicitly.
    """
    if not accounts:
        raise LoadTestError("cannot assign an account from an empty pool")
    if context_index < len(accounts):
        return accounts[context_index]
    if allow_reuse:
        reused = accounts[context_index % len(accounts)]
        logger.warning(
            "context %d reuses account %s (--allow-account-reuse): verification mails may race",
            context_index,
            reused.email,
        )
        return reused
    raise LoadTestError(
        f"context {context_index} has no dedicated account: pool holds {len(accounts)} account(s) "
        f"for {context_index + 1} context(s). Add accounts to credentials_pool.json, lower "
        f"--contexts, or accept the risk with --allow-account-reuse."
    )


def resolve_imap_ssl(mode: str, account: TestAccount) -> bool:
    """Decide TLS for one account: explicit account value → explicit flag → port heuristic.

    ``--imap-ssl auto`` means "trust the pool": the account's own ``imap_ssl`` wins, otherwise a
    port of 143/1430 is treated as plaintext and everything else as TLS. Guessing the other way
    round would silently make a Gmail run fail with an authentication error that looks like a
    Google problem.
    """
    if account.imap_ssl is not None:
        return bool(account.imap_ssl)
    if mode == "on":
        return True
    if mode == "off":
        return False
    return account.imap_port not in {143, 1430}


# ======================================================================================
# ARTEFAKT 2 - targets
# ======================================================================================
@dataclass(frozen=True, slots=True)
class TargetSpec:
    """One row of ``test_targets.json`` after ``{TARGET_URL}`` expansion."""

    name: str
    target_url: str
    scenario: str
    form_fields: dict[str, str]
    submit_selector: Optional[str] = None
    success_selector: Optional[str] = None
    error_selector: Optional[str] = None
    success_url_regex: Optional[str] = None
    requires_email_verification: bool = False
    verification_subject_regex: Optional[str] = None
    verification_link_regex: Optional[str] = None
    captcha_selector: Optional[str] = None
    wait_after_submit_ms: int = 1500
    tags: tuple[str, ...] = ()

    def context_url(self, *, context_index: int, run_id: str) -> str:
        """Resolve ``{ctx}``/``{run}`` placeholders for one context."""
        url = self.target_url
        url = url.replace("{run_id}", run_id).replace("{run}", run_id)
        url = url.replace("{context}", str(context_index)).replace("{ctx}", str(context_index))
        return url

    def context_fields(self, account: TestAccount, *, context_index: int, run_id: str) -> dict[str, str]:
        """Resolve account + context placeholders inside every form value."""
        mapping = {
            "{email}": account.email,
            "{account_email}": account.email,
            "{{email}}": account.email,
            "{password}": account.app_password,
            "{app_password}": account.app_password,
            "{account_password}": account.app_password,
            "{{password}}": account.app_password,
            "{ctx}": str(context_index),
            "{context}": str(context_index),
            "{run}": run_id,
            "{run_id}": run_id,
        }
        resolved: dict[str, str] = {}
        for key, value in self.form_fields.items():
            text = value
            for token, replacement in mapping.items():
                text = text.replace(token, replacement)
            resolved[key] = text
        return resolved

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "target_url": self.target_url,
            "scenario": self.scenario,
            "form_fields": dict(self.form_fields),
            "submit_selector": self.submit_selector,
            "success_selector": self.success_selector,
            "error_selector": self.error_selector,
            "success_url_regex": self.success_url_regex,
            "requires_email_verification": self.requires_email_verification,
            "verification_subject_regex": self.verification_subject_regex,
            "verification_link_regex": self.verification_link_regex,
            "captcha_selector": self.captcha_selector,
            "wait_after_submit_ms": self.wait_after_submit_ms,
            "tags": list(self.tags),
        }


def _cell_text(value: Any) -> str:
    """JSON scalar → text, the way a form field value has to be a string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "on" if value else ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return _cell_text(value).strip().lower() in {"1", "true", "yes", "y", "evet", "on", "aktif", "enabled"}


def load_targets(path: Path, *, target_url: Optional[str]) -> tuple[list[TargetSpec], list[str]]:
    """Read ``test_targets.json`` and expand ``{TARGET_URL}`` from the environment.

    ``target_url`` is the value of the ``TARGET_URL`` environment variable (or ``--target-url``).
    A target that is relative, or that still contains ``{TARGET_URL}``, needs it; when it is
    missing the caller has already stopped with exit code 2, and this function double-checks so
    the invariant holds even when used as a library.
    """
    source = Path(path).expanduser()
    if not source.exists():
        raise LoadTestError(f"target file not found: {source}")
    try:
        payload: Any = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoadTestError(f"{source}: invalid JSON ({exc})") from exc

    if isinstance(payload, dict):
        raw_targets = payload.get("targets") or payload.get("rows") or [payload]
    elif isinstance(payload, list):
        raw_targets = payload
    else:  # pragma: no cover - defensive
        raise LoadTestError(f"{source}: expected a JSON array or an object with 'targets'")

    specs: list[TargetSpec] = []
    warnings: list[str] = []
    for position, raw in enumerate(raw_targets):
        # "targets" entries may legitimately carry "_comment" keys; a pure comment object is skipped.
        if not isinstance(raw, dict):
            warnings.append(f"{source.name}: target #{position} ignored (not an object)")
            continue
        if not raw.get("target_url"):
            continue

        url = _cell_text(raw.get("target_url")).strip()
        if "{TARGET_URL}" in url or url.startswith("/"):
            if not target_url:
                raise LoadTestError(
                    f"{source}: target #{position} needs TARGET_URL but the environment variable "
                    f"is empty. Export it first: export TARGET_URL=https://staging.sirketiniz.com"
                )
            url = url.replace("{TARGET_URL}", target_url.rstrip("/"))
            if not re.match(r"^https?://", url, re.I):
                url = target_url.rstrip("/") + "/" + url.lstrip("/")
        if not re.match(r"^https?://", url, re.I):
            raise LoadTestError(
                f"{source}: target #{position} URL is not absolute and TARGET_URL is not usable "
                f"({truncate(url, 80)!r})"
            )

        scenario = _cell_text(raw.get("scenario")).strip() or "auto"
        fields_raw = raw.get("form_fields") or raw.get("form_data") or {}
        if not isinstance(fields_raw, dict):
            raise LoadTestError(f"{source}: target #{position} 'form_fields' must be an object")
        form_fields = {str(key): _cell_text(value) for key, value in fields_raw.items()}

        requires_verification = _truthy(raw.get("requires_email_verification"))
        if requires_verification and scenario != "email-verify":
            warnings.append(
                f"{source.name}: {raw.get('name') or position} declares e-mail verification but "
                f"scenario={scenario!r}; verification is only honoured for scenario='email-verify'"
            )
            requires_verification = False

        try:
            wait_ms = int(raw.get("wait_after_submit_ms") or 1500)
        except (TypeError, ValueError) as exc:
            raise LoadTestError(f"{source}: target #{position} has a non-numeric wait_after_submit_ms") from exc

        tags_raw = raw.get("tags") or []
        if isinstance(tags_raw, str):
            tags = tuple(part.strip() for part in tags_raw.split(",") if part.strip())
        elif isinstance(tags_raw, (list, tuple)):
            tags = tuple(_cell_text(tag).strip() for tag in tags_raw if _cell_text(tag).strip())
        else:  # pragma: no cover - defensive
            tags = ()

        specs.append(
            TargetSpec(
                name=_cell_text(raw.get("name")).strip() or f"target-{len(specs) + 1}",
                target_url=url,
                scenario=scenario,
                form_fields=form_fields,
                submit_selector=_cell_text(raw.get("submit_selector")).strip() or None,
                success_selector=_cell_text(raw.get("success_selector")).strip() or None,
                error_selector=_cell_text(raw.get("error_selector")).strip() or None,
                success_url_regex=_cell_text(raw.get("success_url_regex")).strip() or None,
                requires_email_verification=requires_verification,
                verification_subject_regex=_cell_text(raw.get("verification_subject_regex")).strip() or None,
                verification_link_regex=_cell_text(raw.get("verification_link_regex")).strip() or None,
                captcha_selector=_cell_text(raw.get("captcha_selector")).strip() or None,
                wait_after_submit_ms=max(0, wait_ms),
                tags=tags,
            )
        )

    if not specs:
        raise LoadTestError(f"{source}: no usable target rows (each row needs 'target_url')")
    logger.info("targets: %d row(s) from %s", len(specs), source.name)
    return specs, warnings


# ======================================================================================
# ARTEFAKT 3 - selector catalogue (ordered fallback chains)
# ======================================================================================
@dataclass(frozen=True, slots=True)
class SelectorCatalogue:
    """Ordered fallback chains keyed by field name (``email_input``, ``submit_button``, …)."""

    chains: dict[str, tuple[str, ...]]
    probe_timeout_ms: int = 4000
    source: Optional[Path] = None

    def known(self, key: str) -> bool:
        return key in self.chains

    def chain(self, key: str) -> tuple[str, ...]:
        """The chain for *key*; raises a helpful error when the catalogue has no such field."""
        if key not in self.chains:
            raise LoadTestError(
                f"selectors.json has no chain for {key!r}. Available: {', '.join(sorted(self.chains))}"
            )
        return self.chains[key]

    def resolve_field_key(self, field_name: str) -> Optional[str]:
        """``email`` → ``email_input``, ``terms`` → ``terms_checkbox`` (suffix convention).

        The form field name in ``test_targets.json`` describes *what* the value is; the
        catalogue key describes *how* it is driven (``_input`` / ``_checkbox`` / ``_radio`` /
        ``_select``). Both spellings therefore have to be tried before giving up.
        """
        for candidate in (
            field_name,
            f"{field_name}_input",
            f"{field_name}_checkbox",
            f"{field_name}_radio",
            f"{field_name}_select",
        ):
            if candidate in self.chains:
                return candidate
        return None

    def kind_of(self, key: str) -> Literal["fill", "check", "select", "click", "probe"]:
        """How a chain must be driven, inferred from the field-name suffix."""
        if key.endswith("_checkbox") or key.endswith("_radio"):
            return "check"
        if key.endswith("_select"):
            return "select"
        if key.endswith("_button") or key in {"submit", "submit_button"}:
            return "click"
        if key in {"success", "error", "captcha_frame"}:
            return "probe"
        return "fill"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source) if self.source else None,
            "probe_timeout_ms": self.probe_timeout_ms,
            "fields": {key: list(chain) for key, chain in self.chains.items()},
        }


def load_selector_catalogue(path: Path) -> tuple[SelectorCatalogue, list[str]]:
    """Read the catalogue, validating that every chain is usable and at least 3 deep."""
    source = Path(path).expanduser()
    if not source.exists():
        raise LoadTestError(f"selector catalogue not found: {source}")
    payload: Any = read_json(source, default=None)
    if payload is None:
        raise LoadTestError(f"{source}: empty or unreadable JSON")

    warnings: list[str] = []
    if isinstance(payload, dict):
        raw_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
        defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {}
    else:  # pragma: no cover - defensive
        raise LoadTestError(f"{source}: expected a JSON object of field → selector chain")

    try:
        probe_timeout = int(defaults.get("probe_timeout_ms") or 4000)
    except (TypeError, ValueError):
        probe_timeout = 4000

    chains: dict[str, tuple[str, ...]] = {}
    for key, value in raw_fields.items():
        if str(key).startswith("_"):
            continue
        if isinstance(value, str):
            entries = [value]
        elif isinstance(value, (list, tuple)):
            entries = [_cell_text(item).strip() for item in value]
        else:
            warnings.append(f"{source.name}: field {key!r} ignored (expected a list of selectors)")
            continue
        entries = [entry for entry in entries if entry]
        if not entries:
            warnings.append(f"{source.name}: field {key!r} ignored (empty chain)")
            continue
        if len(entries) < 3:
            warnings.append(
                f"{source.name}: field {key!r} has only {len(entries)} fallback(s); "
                f"at least 3 keep the run alive when your markup changes"
            )
        chains[str(key)] = tuple(dict.fromkeys(entries))

    if not chains:
        raise LoadTestError(f"{source}: no usable selector chains found")

    catalogue = SelectorCatalogue(chains=chains, probe_timeout_ms=probe_timeout, source=source)
    for required in ("email_input", "password_input", "submit_button", "captcha_frame"):
        if not catalogue.known(required):
            warnings.append(f"{source.name}: recommended chain {required!r} is missing")
    logger.info("selector catalogue: %d field(s) from %s", len(chains), source.name)
    return catalogue, warnings


# ======================================================================================
# proxies (optional)
# ======================================================================================
def parse_proxy(line: str) -> Optional[dict[str, str]]:
    """Parse one proxy line into Playwright's ``proxy=`` mapping.

    Accepted forms: ``http://user:pass@host:port``, ``user:pass@host:port``, ``host:port``,
    ``host:port:user:pass``, ``socks5://host:port``. Blank lines and ``#`` comments → ``None``.
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    scheme = "http"
    if "://" in text:
        scheme, _, text = text.partition("://")
        scheme = scheme.strip().lower() or "http"

    username = password = ""
    if "@" in text:
        credentials, _, text = text.rpartition("@")
        username, _, password = credentials.partition(":") if ":" in credentials else (credentials, "", "")

    parts = [part.strip() for part in text.split(":")]
    if len(parts) >= 4 and not username:  # host:port:user:pass (common in Turkish proxy lists)
        host, port, username, password = parts[0], parts[1], parts[2], parts[3]
    elif len(parts) >= 2:
        host, port = parts[0], parts[1]
    else:
        logger.warning("proxy line ignored (expected host:port, got %r)", truncate(line, 60))
        return None

    if not host or not port.isdigit():
        logger.warning("proxy line ignored (unparsable host/port in %r)", truncate(line, 60))
        return None
    proxy: dict[str, str] = {"server": f"{scheme}://{host}:{port}"}
    if username:
        proxy["username"] = username
        proxy["password"] = password
    return proxy


def load_proxies(path: Path) -> list[dict[str, str]]:
    """Read a proxy list file; every unparsable line is reported and skipped."""
    source = Path(path).expanduser()
    if not source.exists():
        raise LoadTestError(f"proxy file not found: {source}")
    proxies: list[dict[str, str]] = []
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = parse_proxy(line)
        if parsed is not None:
            proxies.append(parsed)
    if not proxies:
        raise LoadTestError(f"{source}: no usable proxy entries (expected one host:port per line)")
    logger.info("proxies: %d entr(y/ies) from %s", len(proxies), source.name)
    return proxies


# ======================================================================================
# network recording (requirement f)
# ======================================================================================
def redact_url(url: str, masked_query_keys: Sequence[str] = SENSITIVE_QUERY_KEYS) -> str:
    """Mask sensitive query values while keeping the URL readable for contract checks.

    ``/verify?token=abc&email=x%40y`` → ``/verify?token=***&email=x%40y``. Used both for the
    network log and for anything printed to the terminal, so a verification link can be shown
    without leaking the one-time token that would let somebody else confirm the account.
    """
    if "?" not in url:
        return url
    keys = {key.lower() for key in masked_query_keys}
    head, _, query = url.partition("?")
    parts: list[str] = []
    for chunk in query.split("&"):
        if "=" not in chunk:
            parts.append(chunk)
            continue
        key, _, value = chunk.partition("=")
        parts.append(f"{key}=***" if key.lower() in keys and value else chunk)
    return head + "?" + "&".join(parts)


class NetworkRecorder:
    """``page.on("request")`` / ``page.on("response")`` recorder writing ``network_log.jsonl``.

    Every context shares one recorder so the artefact is a single stream you can grep, and each
    record carries ``context`` to tell the 10 concurrent users apart. Query values of sensitive
    parameters are masked; request bodies are never written to disk.
    """

    def __init__(self, *, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.events: list[dict[str, Any]] = []
        self._started: dict[tuple[str, str, str], float] = {}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _request_key(request: Any) -> tuple[str, str, str]:
        return (getattr(request, "method", ""), getattr(request, "url", ""), str(id(request)))

    def _redact_url(self, url: str) -> str:
        return redact_url(url)

    def _path_of(self, url: str) -> str:
        without_scheme = re.sub(r"^[a-z]+://[^/]+", "", url)
        return without_scheme.split("?")[0] or "/"

    # ------------------------------------------------------------------ listeners
    def attach(self, page: Any, context_id: str) -> None:
        """Register the four listeners on *page*. Never raises: recording is best-effort."""
        try:
            page.on("request", lambda request: self._on_request(request, context_id))
            page.on("response", lambda response: self._on_response(response, context_id))
            page.on("requestfailed", lambda request: self._on_failed(request, context_id))
        except Exception as exc:  # noqa: BLE001 - a page that closed early must not kill the run
            logger.debug("[%s] network recorder could not attach: %s", context_id, exc)

    def _on_request(self, request: Any, context_id: str) -> None:
        try:
            self._started[self._request_key(request)] = time.perf_counter()
        except Exception:  # noqa: BLE001
            pass

    def _on_response(self, response: Any, context_id: str) -> None:
        try:
            request = response.request
            started = self._started.pop(self._request_key(request), None)
            duration_ms = round((time.perf_counter() - started) * 1000, 2) if started else None
            self.events.append(
                {
                    "ts": now_iso(),
                    "context": context_id,
                    "type": "response",
                    "method": request.method,
                    "url": self._redact_url(response.url),
                    "path": self._path_of(response.url),
                    "status": int(response.status),
                    "ok": bool(response.ok),
                    "resource_type": request.resource_type,
                    "duration_ms": duration_ms,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("network response not recorded: %s", exc)

    def _on_failed(self, request: Any, context_id: str) -> None:
        try:
            self._started.pop(self._request_key(request), None)
            failure = getattr(request, "failure", None)
            message = ""
            if isinstance(failure, dict):
                message = str(failure.get("errorText") or "")
            elif failure is not None:
                message = str(failure)
            self.events.append(
                {
                    "ts": now_iso(),
                    "context": context_id,
                    "type": "requestfailed",
                    "method": request.method,
                    "url": self._redact_url(request.url),
                    "path": self._path_of(request.url),
                    "status": None,
                    "ok": False,
                    "resource_type": request.resource_type,
                    "duration_ms": None,
                    "error": truncate(message, 200),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("failed request not recorded: %s", exc)

    # ------------------------------------------------------------------ output
    def flush(self, filename: str = "network_log.jsonl") -> Path:
        """Write every buffered event, sorted by time, and return the path."""
        ordered = sorted(self.events, key=lambda event: (event.get("ts") or "", event.get("url") or ""))
        path = Path(write_jsonl(self.run_dir / filename, ordered))
        logger.info("network log: %d event(s) → %s", len(ordered), path)
        return path

    def endpoint_summary(self) -> dict[str, Any]:
        """``{"endpoints": [...], "totals": {...}}`` - the API-contract view of the traffic."""
        buckets: dict[tuple[str, str], dict[str, Any]] = {}
        for event in self.events:
            if event.get("type") != "response":
                continue
            key = (str(event.get("method")), str(event.get("path")))
            bucket = buckets.setdefault(
                key,
                {"method": key[0], "path": key[1], "requests": 0, "statuses": {}, "duration_ms": []},
            )
            bucket["requests"] += 1
            status = event.get("status")
            status_key = str(status) if status is not None else "no-response"
            bucket["statuses"][status_key] = bucket["statuses"].get(status_key, 0) + 1
            if event.get("duration_ms") is not None:
                bucket["duration_ms"].append(float(event["duration_ms"]))

        endpoints: list[dict[str, Any]] = []
        for bucket in sorted(buckets.values(), key=lambda item: (-item["requests"], item["path"])):
            durations = sorted(bucket.pop("duration_ms"))
            if durations:
                bucket["duration_ms"] = {
                    "min": round(durations[0], 2),
                    "p50": round(durations[len(durations) // 2], 2),
                    "max": round(durations[-1], 2),
                }
            endpoints.append(bucket)

        statuses: dict[str, int] = {}
        for endpoint in endpoints:
            for status, count in endpoint["statuses"].items():
                statuses[status] = statuses.get(status, 0) + int(count)

        return {
            "endpoints": endpoints,
            "totals": {
                "events": len(self.events),
                "responses": sum(endpoint["requests"] for endpoint in endpoints),
                "failed_requests": sum(1 for event in self.events if event.get("type") == "requestfailed"),
                "statuses": statuses,
            },
        }


# ======================================================================================
# outcomes
# ======================================================================================
@dataclass(slots=True)
class TargetOutcome:
    """Result of one target inside one context."""

    name: str
    url: str
    status: TargetStatus
    detail: str = ""
    duration_ms: float = 0.0
    selectors_used: dict[str, str] = field(default_factory=dict)
    unmatched_fields: list[str] = field(default_factory=list)
    verification: Optional[dict[str, Any]] = None
    screenshot: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "status": self.status,
            "detail": self.detail,
            "duration_ms": round(self.duration_ms, 2),
            "selectors_used": dict(self.selectors_used),
            "unmatched_fields": list(self.unmatched_fields),
            "verification": self.verification,
            "screenshot": self.screenshot,
        }


@dataclass(slots=True)
class ContextOutcome:
    """Result of one browser context (= one concurrent user)."""

    context_id: str
    index: int
    account_email: str
    status: str = STATUS_OK
    detail: str = ""
    duration_ms: float = 0.0
    user_agent: str = ""
    proxy: str = ""
    targets: list[TargetOutcome] = field(default_factory=list)
    captcha_hits: int = 0

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.targets:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "index": self.index,
            "account": self.account_email,
            "status": self.status,
            "detail": self.detail,
            "duration_ms": round(self.duration_ms, 2),
            "user_agent": self.user_agent,
            "proxy": self.proxy,
            "captcha_hits": self.captcha_hits,
            "counts": self.counts(),
            "targets": [outcome.to_dict() for outcome in self.targets],
        }


# ======================================================================================
# the runner
# ======================================================================================
class LoadTestRunner:
    """Owns the browser, the contexts, the network recorder and the artefacts of one run."""

    def __init__(
        self,
        *,
        accounts: Sequence[TestAccount],
        targets: Sequence[TargetSpec],
        catalogue: SelectorCatalogue,
        config: Config,
        args: argparse.Namespace,
        proxies: Sequence[dict[str, str]],
        run_id: str,
        run_dir: Path,
    ) -> None:
        self.accounts = list(accounts)
        self.targets = list(targets)
        self.catalogue = catalogue
        self.config = config
        self.args = args
        self.proxies = list(proxies)
        self.run_id = run_id
        self.run_dir = ensure_dir(run_dir)
        self.screenshot_dir = ensure_dir(self.run_dir / "screenshots")
        self.recorder = NetworkRecorder(run_dir=self.run_dir)
        self.steps: list[dict[str, Any]] = []
        self.outcomes: list[ContextOutcome] = []
        self.browser: Optional[Any] = None

    # ------------------------------------------------------------------ small helpers
    def _log_step(self, context_id: str, step: str, **payload: Any) -> None:
        record = {"ts": now_iso(), "run_id": self.run_id, "context": context_id, "step": step, **payload}
        self.steps.append(record)
        logger.debug("[%s] %s %s", context_id, step, truncate(json.dumps(payload, ensure_ascii=False), 200))

    @staticmethod
    def _as_locator(page: Any, selector: str) -> Any:
        """Playwright locator for a CSS/XPath chain entry (XPath is prefixed explicitly)."""
        text = selector.strip()
        if text.startswith("xpath="):
            return page.locator(text)
        if text.startswith(("//", "..", "(")):
            return page.locator(f"xpath={text}")
        return page.locator(text)

    async def _first_visible(self, page: Any, chain: Sequence[str], *, timeout_ms: Optional[int] = None) -> tuple[Optional[Any], Optional[str]]:
        """Probe *chain* in order and return ``(locator, selector)`` for the first visible match."""
        budget = timeout_ms if timeout_ms is not None else self.catalogue.probe_timeout_ms
        for selector in chain:
            try:
                locator = self._as_locator(page, selector).first
                if await locator.count() == 0:
                    continue
                if await locator.is_visible(timeout=budget):
                    return locator, selector
            except PlaywrightTimeoutError:
                continue
            except Exception as exc:  # noqa: BLE001 - a bad selector must not abort the chain
                logger.debug("selector %r failed: %s", selector, exc)
                continue
        return None, None

    async def _screenshot(self, page: Any, context_id: str, label: str) -> Optional[str]:
        """Save a PNG for *label*; never fatal (screenshots are evidence, not logic)."""
        if not self.args.screenshots:
            return None
        target_dir = ensure_dir(self.screenshot_dir / context_id)
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label)[:48] or "step"
        path = target_dir / f"{len(self.steps):03d}-{safe_label}.png"
        try:
            await page.screenshot(path=str(path), full_page=False)
            return str(path.relative_to(self.run_dir))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] screenshot failed for %s: %s", context_id, label, exc)
            return None

    async def _detect_captcha(self, page: Any, target: TargetSpec) -> list[str]:
        """Return the visible CAPTCHA markers (empty list = none). Detection only, never solving."""
        markers: list[str] = []
        chains: list[str] = []
        if target.captcha_selector:
            chains.append(target.captcha_selector)
        if self.catalogue.known("captcha_frame"):
            chains.extend(self.catalogue.chain("captcha_frame"))
        chains.extend(CAPTCHA_SELECTORS)
        seen: set[str] = set()
        for selector in chains:
            if selector in seen:
                continue
            seen.add(selector)
            try:
                locator = self._as_locator(page, selector).first
                if await locator.count() == 0:
                    continue
                if await locator.is_visible(timeout=750):
                    markers.append(selector)
            except Exception:  # noqa: BLE001 - probing must never raise
                continue
        return markers

    # ------------------------------------------------------------------ form work
    async def _fill_form(
        self,
        page: Any,
        target: TargetSpec,
        fields: dict[str, str],
        context_id: str,
    ) -> tuple[dict[str, str], list[str], Optional[TargetOutcome]]:
        """Fill every declared field; returns ``(selectors_used, unmatched, early_outcome)``.

        ``early_outcome`` is set when the CAPTCHA policy fired, in which case the caller stops
        working with this context instead of raising.
        """
        used: dict[str, str] = {}
        unmatched: list[str] = []
        for field_name, value in fields.items():
            key = self.catalogue.resolve_field_key(field_name)
            if key is None:
                unmatched.append(field_name)
                self._log_step(context_id, "field_skipped", field=field_name, reason="no selector chain")
                continue

            markers = await self._detect_captcha(page, target)
            if markers:
                self._log_step(context_id, "CAPTCHA_DETECTED", where=f"before:{field_name}", markers=markers)
                await self._screenshot(page, context_id, "captcha-detected")
                return used, unmatched, TargetOutcome(
                    name=target.name,
                    url=page.url,
                    status=STATUS_CAPTCHA,
                    detail=f"CAPTCHA visible before '{field_name}' ({markers[0]})",
                    selectors_used=used,
                    unmatched_fields=unmatched,
                )

            locator, selector = await self._first_visible(page, self.catalogue.chain(key))
            if locator is None or selector is None:
                unmatched.append(field_name)
                self._log_step(context_id, "field_unmatched", field=field_name, chain=list(self.catalogue.chain(key)))
                continue

            kind = self.catalogue.kind_of(key)
            try:
                if kind == "check":
                    if value.strip().lower() not in {"", "0", "false", "off", "hayir", "hayır", "no"}:
                        await locator.check(timeout=self.config.default_timeout_ms)
                    else:
                        await locator.uncheck(timeout=self.config.default_timeout_ms)
                elif kind == "select":
                    try:
                        await locator.select_option(value=value, timeout=self.config.default_timeout_ms)
                    except Exception:  # noqa: BLE001 - fall back to label matching
                        await locator.select_option(label=value, timeout=self.config.default_timeout_ms)
                else:
                    await locator.fill(value, timeout=self.config.default_timeout_ms)
            except Exception as exc:  # noqa: BLE001 - one bad field must not lose the target
                unmatched.append(field_name)
                self._log_step(context_id, "field_failed", field=field_name, selector=selector, error=truncate(str(exc), 200))
                continue

            used[field_name] = selector
            display = mask_secret(value, keep=3) if "password" in field_name.lower() else truncate(value, 48)
            self._log_step(context_id, "field_filled", field=field_name, selector=selector, kind=kind, value=display)
            await self._screenshot(page, context_id, f"field-{field_name}")
        return used, unmatched, None

    async def _submit(self, page: Any, target: TargetSpec, context_id: str) -> tuple[Optional[str], Optional[str]]:
        """Click the submit control, returning ``(selector_used, error_message)``."""
        chains: list[str] = []
        if target.submit_selector:
            chains.append(target.submit_selector)
        if self.catalogue.known("submit_button"):
            chains.extend(self.catalogue.chain("submit_button"))
        chains.extend(['button[type="submit"]', 'input[type="submit"]'])

        locator, selector = await self._first_visible(page, chains)
        if locator is None or selector is None:
            return None, "submit control not found (add a chain to selectors.json → submit_button)"
        try:
            await locator.click(timeout=self.config.default_timeout_ms)
        except Exception as exc:  # noqa: BLE001
            return selector, f"submit click failed: {truncate(str(exc), 160)}"
        self._log_step(context_id, "submitted", selector=selector)
        await self._screenshot(page, context_id, "submitted")
        return selector, None

    async def _await_outcome(self, page: Any, target: TargetSpec, context_id: str) -> Optional[str]:
        """Return an error message when the form reported a failure, otherwise ``None``.

        The error chain is probed **first** and with a short budget: on a slow staging server the
        success marker can take seconds to appear, and waiting for it first would hide a 4xx/5xx
        error page behind a timeout. A post-submit CAPTCHA is reported as an error here and turned
        into the CAPTCHA policy by the caller.
        """
        error_chains: list[str] = []
        if target.error_selector:
            error_chains.append(target.error_selector)
        if self.catalogue.known("error"):
            error_chains.extend(self.catalogue.chain("error"))
        for selector in error_chains:
            try:
                locator = self._as_locator(page, selector).first
                if await locator.count() == 0:
                    continue
                if await locator.is_visible(timeout=600):
                    text = ""
                    try:
                        text = (await locator.inner_text(timeout=600)).strip()
                    except Exception:  # noqa: BLE001
                        pass
                    return f"error element visible ({selector}): {truncate(text, 120)}"
            except Exception:  # noqa: BLE001
                continue

        markers = await self._detect_captcha(page, target)
        if markers:
            return f"CAPTCHA::{markers[0]}"

        success_chains: list[str] = []
        if target.success_selector:
            success_chains.append(target.success_selector)
        if self.catalogue.known("success"):
            success_chains.extend(self.catalogue.chain("success"))
        budget = max(self.config.default_timeout_ms, 3000)
        for selector in success_chains:
            try:
                locator = self._as_locator(page, selector).first
                if await locator.count() == 0:
                    continue
                if await locator.is_visible(timeout=budget):
                    self._log_step(context_id, "success_detected", selector=selector)
                    await self._screenshot(page, context_id, "success")
                    return None
            except Exception:  # noqa: BLE001
                continue

        if target.success_url_regex:
            try:
                if re.search(target.success_url_regex, page.url):
                    self._log_step(context_id, "success_url", url=page.url)
                    return None
            except re.error as exc:
                return f"success_url_regex is not a valid regex: {exc}"
        return "no success marker after submit (check success_selector / wait_after_submit_ms)"

    async def _verify_email(self, page: Any, target: TargetSpec, account: TestAccount, *, since: datetime, context_id: str) -> dict[str, Any]:
        """Wait for the verification mail, then visit the link it carries."""
        ssl_default = resolve_imap_ssl(self.args.imap_ssl, account)
        settings = ImapSettings(
            host=account.imap_host,
            port=account.imap_port,
            username=account.email,
            password=account.app_password,
            use_ssl=account.ssl_flag(ssl_default),
            timeout_s=float(self.args.imap_timeout),
            poll_interval_s=float(self.args.imap_poll_interval),
            search_days=2,
            unseen_only=True,
            mark_seen=False,
            subject_regex=target.verification_subject_regex or self.args.imap_subject_regex,
            max_links=25,
        )
        settings.validate()

        client = ImapClient(settings)
        try:
            extraction = await client.wait_for_verification(
                since=since,
                timeout_s=float(self.args.imap_timeout),
                target_email=account.email,
                subject_regex=settings.subject_regex,
                link_regex=target.verification_link_regex or None,
                prefer_same_domain=host_of(target.target_url).split(":")[0] or None,
            )
        finally:
            try:
                await client.close_async()
            except Exception as exc:  # noqa: BLE001 - closing must never mask the real result
                logger.debug("[%s] imap close failed: %s", context_id, exc)

        payload = extraction.to_dict()
        if not extraction.found or not extraction.link:
            self._log_step(context_id, "verification_failed", reason=payload.get("reason") or "no link found")
            return payload

        masked_link = redact_url(extraction.link)
        self._log_step(context_id, "verification_link", link=masked_link, otp_length=payload.get("otp_length"))
        try:
            await page.goto(extraction.link, wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
        except Exception as exc:  # noqa: BLE001
            payload["reason"] = f"verification link did not open: {truncate(str(exc), 160)}"
            return payload

        payload["opened_url"] = masked_link
        if self.catalogue.known("success"):
            for selector in self.catalogue.chain("success"):
                try:
                    locator = self._as_locator(page, selector).first
                    if await locator.count() and await locator.is_visible(timeout=1500):
                        payload["verified_marker"] = selector
                        break
                except Exception:  # noqa: BLE001
                    continue
        await self._screenshot(page, context_id, "verified")
        self._log_step(context_id, "verification_ok", link=masked_link, marker=payload.get("verified_marker"))
        return payload

    # ------------------------------------------------------------------ one target
    async def _run_target(
        self,
        page: Any,
        target: TargetSpec,
        account: TestAccount,
        context_id: str,
        context_index: int,
    ) -> TargetOutcome:
        started = time.perf_counter()
        url = target.context_url(context_index=context_index, run_id=self.run_id)
        fields = target.context_fields(account, context_index=context_index, run_id=self.run_id)
        outcome = TargetOutcome(name=target.name, url=url, status=STATUS_OK)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
            self._log_step(context_id, "navigated", url=url, status=target.scenario)
            await self._screenshot(page, context_id, "landing")

            markers = await self._detect_captcha(page, target)
            if markers:
                outcome.status = STATUS_CAPTCHA
                outcome.detail = f"CAPTCHA visible on landing page ({markers[0]})"
                self._log_step(context_id, "CAPTCHA_DETECTED", where="landing", markers=markers)
                await self._screenshot(page, context_id, "captcha-detected")
                return outcome

            if fields:
                used, unmatched, early = await self._fill_form(page, target, fields, context_id)
                outcome.selectors_used.update(used)
                outcome.unmatched_fields.extend(unmatched)
                if early is not None:
                    # CAPTCHA policy fired mid-form: adopt the result and stop working with this
                    # context instead of raising (requirement: detect → log → skip safely).
                    outcome.status = early.status
                    outcome.detail = early.detail
                    return outcome

            submitted_at = datetime.now(timezone.utc)
            submit_selector, submit_error = await self._submit(page, target, context_id)
            if submit_selector:
                outcome.selectors_used["submit"] = submit_selector
            if submit_error:
                outcome.status = STATUS_FAILED
                outcome.detail = submit_error
                return outcome

            if target.wait_after_submit_ms:
                await asyncio.sleep(target.wait_after_submit_ms / 1000)

            failure = await self._await_outcome(page, target, context_id)
            if failure and failure.startswith("CAPTCHA::"):
                outcome.status = STATUS_CAPTCHA
                outcome.detail = failure.replace("CAPTCHA::", "CAPTCHA visible after submit (")
                outcome.detail += ")"
                self._log_step(context_id, "CAPTCHA_DETECTED", where="after-submit", markers=[failure])
                return outcome
            if failure:
                outcome.status = STATUS_FAILED
                outcome.detail = failure
                return outcome

            if target.requires_email_verification:
                verification = await self._verify_email(
                    page, target, account, since=submitted_at, context_id=context_id
                )
                outcome.verification = verification
                if not verification.get("found") or not verification.get("opened_url"):
                    outcome.status = STATUS_VERIFICATION_FAILED
                    outcome.detail = str(verification.get("reason") or "verification link not used")
                    return outcome

            outcome.status = STATUS_OK
            if outcome.unmatched_fields:
                outcome.detail = "filled with gaps: " + ", ".join(outcome.unmatched_fields)
            return outcome
        except PlaywrightTimeoutError as exc:
            outcome.status = STATUS_FAILED
            outcome.detail = f"timeout: {truncate(str(exc), 200)}"
            return outcome
        except Exception as exc:  # noqa: BLE001 - one target never kills the run
            outcome.status = STATUS_ERROR
            outcome.detail = f"{type(exc).__name__}: {truncate(str(exc), 200)}"
            logger.debug("[%s] target %s raised: %s", context_id, target.name, exc, exc_info=True)
            return outcome
        finally:
            outcome.duration_ms = (time.perf_counter() - started) * 1000
            await self._screenshot(page, context_id, f"target-{target.name}-{outcome.status}")

    # ------------------------------------------------------------------ one context
    async def _run_context(self, index: int) -> ContextOutcome:
        """Materialise one isolated context and drive every target through it."""
        account = assign_account(self.accounts, index, allow_reuse=bool(self.args.allow_account_reuse))
        context_id = new_context_id(index)
        outcome = ContextOutcome(context_id=context_id, index=index, account_email=account.email)
        started = time.perf_counter()
        context: Optional[Any] = None
        proxy = self.proxies[index % len(self.proxies)] if self.proxies else None
        outcome.proxy = str(proxy.get("server")) if proxy else "direct"

        # One coherent fingerprint bundle per context: the device profile supplies the
        # User-Agent, viewport, locale and platform, the timezone is derived next to it, and both
        # are baked into the stealth init script injected *before* any page script runs.
        device = self.config.device_profiles[index % max(1, len(self.config.device_profiles))]
        timezone_id = TIMEZONE_FALLBACKS[index % len(TIMEZONE_FALLBACKS)]
        stealth_profile = ContextProfile(
            index=index,
            device=device,
            timezone_id=timezone_id,
            locale=(device.locale if device else None) or "tr-TR",
        )
        stealth_script = build_init_script(stealth_profile, self.config)

        try:
            assert self.browser is not None, "browser must be launched before contexts"
            context = await self.browser.new_context(
                user_agent=stealth_profile.user_agent,
                viewport=stealth_profile.viewport or {"width": 1366, "height": 768},
                locale=stealth_profile.locale,
                timezone_id=stealth_profile.timezone_id,
                ignore_https_errors=self.args.ignore_https_errors,
                proxy=proxy if proxy else None,
            )
            await context.add_init_script(stealth_script)
            outcome.user_agent = truncate(stealth_profile.user_agent or "default", 80)
            page = await context.new_page()
            page.set_default_timeout(self.config.default_timeout_ms)
            self.recorder.attach(page, context_id)

            webdriver_flag = await page.evaluate("() => navigator.webdriver")
            self._log_step(
                context_id,
                "context_ready",
                account=account.email,
                proxy=outcome.proxy,
                timezone=stealth_profile.timezone_id,
                user_agent=outcome.user_agent,
                webdriver=webdriver_flag,
            )
            if webdriver_flag not in (None, False):
                logger.warning(
                    "[%s] navigator.webdriver is still %r - the stealth script did not apply",
                    context_id,
                    webdriver_flag,
                )

            captcha_stop = False
            for target in self.targets:
                result = await self._run_target(page, target, account, context_id, index)
                outcome.targets.append(result)
                if result.status == STATUS_CAPTCHA:
                    outcome.captcha_hits += 1
                    captcha_stop = True
                    break
                if self.args.rate_limit:
                    await asyncio.sleep(float(self.args.rate_limit))

            if captcha_stop:
                outcome.status = STATUS_CAPTCHA
                outcome.detail = "context closed after CAPTCHA_DETECTED (skip policy, no exception raised)"
                self._log_step(context_id, "context_skipped", reason="captcha")
            elif any(item.status in {STATUS_ERROR, STATUS_FAILED, STATUS_VERIFICATION_FAILED} for item in outcome.targets):
                outcome.status = STATUS_FAILED
            else:
                outcome.status = STATUS_OK
            return outcome
        except Exception as exc:  # noqa: BLE001 - a broken context is a result, not a crash
            outcome.status = STATUS_ERROR
            outcome.detail = f"{type(exc).__name__}: {truncate(str(exc), 200)}"
            logger.warning("[%s] context failed: %s", context_id, exc)
            return outcome
        finally:
            outcome.duration_ms = (time.perf_counter() - started) * 1000
            if context is not None:
                try:
                    await context.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[%s] context close failed: %s", context_id, exc)
            self._log_step(context_id, "context_closed", status=outcome.status, duration_ms=round(outcome.duration_ms, 1))

    # ------------------------------------------------------------------ whole run
    def planned_context_count(self) -> int:
        """How many contexts this run will materialise.

        Context *i* owns account *i*, so ``--contexts 2`` means "the first two accounts":
        never more contexts than accounts (each context needs its own mailbox) and never more
        than the caller asked for. Kept as its own method so the rule can be unit tested
        without launching a browser.
        """
        return max(1, min(int(self.args.contexts), len(self.accounts)))

    async def run(self) -> int:
        """Launch the browser, run every context under the concurrency budget, write artefacts."""
        assert _PLAYWRIGHT_IMPORT_ERROR is None, "playwright must be importable before run()"
        context_count = self.planned_context_count()
        concurrency = max(1, min(int(self.args.concurrency), context_count, 50))
        semaphore = asyncio.Semaphore(concurrency)

        async def _worker(index: int) -> ContextOutcome:
            async with semaphore:
                return await self._run_context(index)

        logger.info(
            "starting run %s: %d context(s) of %d account(s), concurrency %d, %d target(s) each, proxy=%s",
            self.run_id,
            context_count,
            len(self.accounts),
            concurrency,
            len(self.targets),
            "file" if self.proxies else "off",
        )
        async with async_playwright() as playwright:
            try:
                self.browser = await playwright.chromium.launch(
                    headless=not self.args.headful,
                    slow_mo=self.args.slow_mo_ms or 0,
                    args=list(self.args.browser_args),
                )
            except Exception as exc:  # noqa: BLE001 - the two classic Chromium setup failures
                message = str(exc)
                if "error while loading shared libraries" in message:
                    raise LoadTestError(
                        "Chromium sistem kütüphaneleri eksik (libnspr4 gibi).\n"
                        "→ sudo python3 -m playwright install-deps chromium"
                    ) from exc
                if "Executable doesn't exist" in message or "playwright install" in message:
                    raise LoadTestError(
                        "Playwright tarayıcı ikilisi yok.\n"
                        "→ python3 -m playwright install --with-deps chromium"
                    ) from exc
                raise
            try:
                self.outcomes = list(await asyncio.gather(*(_worker(index) for index in range(context_count))))
            finally:
                try:
                    await self.browser.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("browser close failed: %s", exc)
                self.browser = None

        self.write_artefacts()
        return self.exit_code()

    # ------------------------------------------------------------------ reporting
    def _totals(self) -> dict[str, Any]:
        target_counts: dict[str, int] = {}
        verification_ok = verification_failed = 0
        for outcome in self.outcomes:
            for item in outcome.targets:
                target_counts[item.status] = target_counts.get(item.status, 0) + 1
                if item.verification is not None:
                    if item.verification.get("found") and item.verification.get("opened_url"):
                        verification_ok += 1
                    else:
                        verification_failed += 1
        return {
            "contexts": len(self.outcomes),
            "contexts_ok": sum(1 for item in self.outcomes if item.status == STATUS_OK),
            "contexts_captcha": sum(1 for item in self.outcomes if item.status == STATUS_CAPTCHA),
            "contexts_failed": sum(1 for item in self.outcomes if item.status in {STATUS_FAILED, STATUS_ERROR}),
            "targets": sum(target_counts.values()),
            "target_status": target_counts,
            "verification_ok": verification_ok,
            "verification_failed": verification_failed,
            "captcha_hits": sum(item.captcha_hits for item in self.outcomes),
            "duration_ms": sum(item.duration_ms for item in self.outcomes),
        }

    def exit_code(self) -> int:
        """``0`` when nothing failed; ``1`` on any failure/CAPTCHA-with-``--fail-on-captcha``."""
        totals = self._totals()
        if totals["target_status"].get(STATUS_FAILED) or totals["target_status"].get(STATUS_ERROR):
            return 1
        if totals["verification_failed"]:
            return 1
        if self.args.fail_on_captcha and totals["captcha_hits"]:
            return 1
        return 0

    def write_artefacts(self) -> None:
        """Persist every artefact of the run, tolerating a missing directory."""
        try:
            endpoints = self.recorder.endpoint_summary()
            self.recorder.flush()
            if self.steps:
                write_jsonl(self.run_dir / "steps.jsonl", self.steps)
            write_json(self.run_dir / "endpoints.json", endpoints)
            summary = {
                "run_id": self.run_id,
                "started_artefacts": str(self.run_dir),
                "target_url": self.args.target_url or "",
                "accounts": [account.to_dict(mask=True) for account in self.accounts],
                "targets": [target.to_dict() for target in self.targets],
                "selectors": self.catalogue.to_dict(),
                "totals": self._totals(),
                "endpoints": endpoints,
                "contexts": [outcome.to_dict() for outcome in self.outcomes],
                "config": {
                    "contexts": len(self.accounts),
                    "concurrency": int(self.args.concurrency),
                    "stealth": True,
                    "imap_ssl": self.args.imap_ssl,
                    "imap_timeout_s": float(self.args.imap_timeout),
                    "proxies": len(self.proxies),
                    "screenshots": bool(self.args.screenshots),
                    "rate_limit_s": float(self.args.rate_limit or 0),
                },
            }
            write_json(self.run_dir / "summary.json", summary)
            report = self.render_report()
            (self.run_dir / "report.txt").write_text(report, encoding="utf-8")
            print(report)
        except Exception as exc:  # noqa: BLE001 - artefacts must not hide the exit code
            logger.error("could not write artefacts: %s", exc)

    def render_report(self) -> str:
        """Terminal report: totals, per-context line, API contract view, artefact paths."""
        totals = self._totals()
        lines: list[str] = []
        lines.append("=" * 78)
        lines.append(f"YÜK TESTİ ÖZETİ — {self.run_id}")
        lines.append("=" * 78)
        lines.append(f"Bağlamlar     : {totals['contexts']} (ok {totals['contexts_ok']} | "
                     f"captcha {totals['contexts_captcha']} | hatalı {totals['contexts_failed']})")
        status_text = ", ".join(f"{key}={value}" for key, value in sorted(totals["target_status"].items())) or "yok"
        lines.append(f"Hedefler      : {totals['targets']} ({status_text})")
        lines.append(f"Doğrulama     : {totals['verification_ok']} ok / {totals['verification_failed']} failed")
        lines.append(f"CAPTCHA       : {totals['captcha_hits']} tespit (skip politikası: hata fırlatılmadı)")
        lines.append(f"Toplam süre   : {human_ms(totals['duration_ms'])} (bağlam süreleri toplamı)")
        lines.append("")
        lines.append("Bağlam başına:")
        lines.append(f"  {'ctx':<12} {'hesap':<28} {'durum':<16} {'süre':>9}  hedefler")
        for outcome in self.outcomes:
            counts = ", ".join(f"{key}={value}" for key, value in sorted(outcome.counts().items())) or "-"
            account = truncate(outcome.account_email, 26)
            lines.append(
                f"  {outcome.context_id:<12} {account:<28} {outcome.status:<16} "
                f"{human_ms(outcome.duration_ms):>9}  {counts}"
            )
            if outcome.detail:
                lines.append(f"      ↳ {truncate(outcome.detail, 110)}")
            for item in outcome.targets:
                if item.status != STATUS_OK or item.detail:
                    lines.append(f"      • {item.name}: {item.status} — {truncate(item.detail, 90)}")
        lines.append("")
        endpoints = self.recorder.endpoint_summary()
        totals_block = endpoints["totals"]
        lines.append(
            f"Ağ trafiği    : {totals_block['responses']} yanıt | "
            f"{totals_block['failed_requests']} başarısız istek | durum kodları "
            f"{', '.join(f'{k}×{v}' for k, v in sorted(totals_block['statuses'].items())) or 'yok'}"
        )
        for endpoint in endpoints["endpoints"][:12]:
            statuses = ", ".join(f"{key}×{value}" for key, value in sorted(endpoint["statuses"].items()))
            timing = endpoint.get("duration_ms")
            timing_text = f" | p50 {timing['p50']} ms" if isinstance(timing, dict) else ""
            lines.append(f"  {endpoint['method']:<5} {endpoint['path']:<38} {endpoint['requests']:>4} istek | {statuses}{timing_text}")
        lines.append("")
        lines.append(f"Artefaktlar   : {self.run_dir}")
        lines.append("  network_log.jsonl (method/url/status) | endpoints.json | steps.jsonl | summary.json")
        if self.args.screenshots:
            lines.append("  screenshots/ (her adım için PNG)")
        lines.append("=" * 78)
        return "\n".join(lines)


# ======================================================================================
# scope gate
# ======================================================================================
def enforce_scope(
    urls: Iterable[str],
    scope: Scope,
    *,
    i_am_authorized: bool,
) -> tuple[list[str], list[str]]:
    """Return ``(blocked_third_party_hosts, unauthorized_hosts)``; raise on a hard block.

    The block-list always wins and cannot be overridden: automated sign-ups on a third-party
    offerwall/micro-task platform are abuse, not load testing. An unlisted host is only tolerated
    with ``--i-am-authorized`` (your own staging that you forgot to add to the scope file).
    """
    hosts: dict[str, str] = {}
    for url in urls:
        host = host_of(url)
        if host:
            hosts.setdefault(host, url)

    bare = {host: host.split(":")[0] for host in hosts}
    blocked = sorted(
        host for host, hostname in bare.items()
        if any(hostname == suffix or hostname.endswith("." + suffix) for suffix in BLOCKED_THIRD_PARTY_SUFFIXES)
    )
    unauthorized = sorted(
        host for host in hosts
        if host not in blocked and not scope.allows(host) and not scope.allows(bare[host])
    )
    if blocked:
        raise LoadTestError(
            "third-party offerwall / micro-task platform(s) are out of scope for this kit: "
            + ", ".join(blocked)
            + " — automated sign-ups there are abuse, not load testing. Test your own staging or a "
              "vendor sandbox you have written permission for."
        )
    if unauthorized and not i_am_authorized:
        raise LoadTestError(
            "host(s) not covered by the scope file: " + ", ".join(unauthorized) + " — " + SCOPE_HELP
        )
    return blocked, unauthorized


# ======================================================================================
# CLI
# ======================================================================================
def build_parser() -> argparse.ArgumentParser:
    """Command line interface (every default is safe: local sandbox, no proxies, no CAPTCHA risk)."""
    parser = argparse.ArgumentParser(
        prog="run_load_test.py",
        description="Multi-context load test for your own staging sign-up flow (Playwright).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--sandbox", action="store_true",
                        help="yerel mock sunucuları kullan (credentials_pool.sandbox.json + test_targets.sandbox.json)")
    parser.add_argument("--credentials", type=Path, default=_HERE / "credentials_pool.json",
                        help="hesap havuzu JSON'u (credentials_pool.json şablonu / .local.json kopyanız)")
    parser.add_argument("--targets", type=Path, default=_HERE / "test_targets.json",
                        help="hedef şeması JSON'u")
    parser.add_argument("--selectors", type=Path, default=_HERE / "selectors.json",
                        help="yedekli seçici kataloğu")
    parser.add_argument("--target-url", default=None,
                        help="TARGET_URL ortam değişkenini geçersiz kılar (aynı kural: kod içinde gömülü adres yok)")
    parser.add_argument("--contexts", type=int, default=None,
                        help="kaç bağlam çalışsın (varsayılan: havuzdaki etkin hesap sayısı)")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="aynı anda kaç bağlam (varsayılan: bağlam sayısı)")
    parser.add_argument("--allow-account-reuse", action="store_true",
                        help="hesap sayısı bağlamdan azsa hesapları sırayla paylaş (doğrulama yarışı riski)")
    parser.add_argument("--allow-placeholders", action="store_true",
                        help="şablon parolalarına izin ver (yalnızca form doldurma testi; IMAP başarısız olur)")
    parser.add_argument("--proxy-mode", choices=("off", "file"), default="off", help="proxy kullanımı")
    parser.add_argument("--proxy-file", type=Path, default=_HERE / "proxies.txt", help="--proxy-mode file için liste")
    parser.add_argument("--scope", type=Path, default=_KIT / "authorized_hosts.txt", help="kapsam (izinli host) dosyası")
    parser.add_argument("--i-am-authorized", action="store_true",
                        help="kapsam dosyasında olmayan ama size ait olan ortamlara izin ver")
    parser.add_argument("--imap-ssl", choices=("auto", "on", "off"), default="auto",
                        help="IMAP TLS: auto = hesap kaydındaki imap_ssl / 993 varsayılanı")
    parser.add_argument("--imap-timeout", type=float, default=120.0, help="doğrulama e-postası bekleme süresi (s)")
    parser.add_argument("--imap-poll-interval", type=float, default=3.0, help="IMAP yeniden deneme aralığı (s)")
    parser.add_argument("--imap-subject-regex", default=r"(doğrula|dogrula|verify|confirm|aktivasyon|activate)",
                        help="konu filtresi (TR/EN)")
    parser.add_argument("--screenshots", action="store_true", default=True, help="her adımda PNG kaydet")
    parser.add_argument("--no-screenshots", dest="screenshots", action="store_false", help="PNG kaydetme")
    parser.add_argument("--fail-on-captcha", action="store_true",
                        help="CAPTCHA tespiti exit kodunu 1 yapsın (varsayılan: skip, exit 0)")
    parser.add_argument("--rate-limit", type=float, default=0.0, help="aynı bağlamda hedefler arası bekleme (s)")
    parser.add_argument("--headful", action="store_true", help="tarayıcıyı görünür çalıştır (debug)")
    parser.add_argument("--slow-mo-ms", type=int, default=0, help="Playwright slow_mo (ms)")
    parser.add_argument("--chromium-sandbox", action="store_true",
                        help="Chromium sandbox'ı açık bırak (varsayılan: --no-sandbox ile kapatılır)")
    parser.add_argument("--browser-arg", action="append", default=[], dest="browser_args",
                        help="ek Chromium argümanı (tekrarlanabilir)")
    parser.add_argument("--ignore-https-errors", action="store_true", default=True, help="staging sertifikaları için")
    parser.add_argument("--strict-https", dest="ignore_https_errors", action="store_false", help="sertifika hatasında dur")
    parser.add_argument("--artifacts", type=Path, default=_REPO / "artifacts" / "loadtest", help="artefakt kök dizini")
    parser.add_argument("--check", action="store_true", help="yalnızca doğrula (tarayıcı açılmaz) ve çık")
    parser.add_argument("--log-level", default="info", help="debug | info | warning | error")
    parser.add_argument("--no-color", action="store_true", help="renksiz çıktı")
    return parser


def apply_sandbox_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Point every input at the bundled local stack; explicitly passed flags always win."""
    if args.sandbox:
        if args.credentials == _HERE / "credentials_pool.json":
            args.credentials = _HERE / "credentials_pool.sandbox.json"
        if args.targets == _HERE / "test_targets.json":
            args.targets = _HERE / "test_targets.sandbox.json"
        if not args.target_url:
            args.target_url = os.environ.get("TARGET_URL") or "http://127.0.0.1:8090"
        if args.imap_ssl == "auto":
            args.imap_ssl = "off"
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point: validate everything, then run. Returns the process exit code."""
    args = apply_sandbox_defaults(build_parser().parse_args(argv))
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s | %(message)s")

    missing = _PLAYWRIGHT_IMPORT_ERROR or _KIT_IMPORT_ERROR or _WAFT_IMPORT_ERROR
    if missing is not None:
        print(
            "✖ Bağımlılık eksik: " + missing + "\n"
            "  → python3 -m pip install -r requirements.txt\n"
            "  → python3 -m playwright install --with-deps chromium",
            file=sys.stderr,
        )
        return 2

    # --- 1) TARGET_URL (rule 1) -----------------------------------------------------------
    target_url = (args.target_url or os.environ.get("TARGET_URL") or "").strip()
    if not target_url:
        print(
            "✖ TARGET_URL tanımlı değil. Bu betikte gömülü hiçbir adres yoktur; hedef yalnızca\n"
            "  ortam değişkeninden gelir:\n\n"
            "    export TARGET_URL=https://staging.sirketiniz.com\n\n"
            "  (yerel mock için: python3 qa-kit/loadtest/run_load_test.py --sandbox)",
            file=sys.stderr,
        )
        return 2
    if not re.match(r"^https?://", target_url, re.I):
        print(f"✖ TARGET_URL şema içermeli (http:// veya https://): {target_url!r}", file=sys.stderr)
        return 2
    args.target_url = target_url.rstrip("/")

    # --- 2) inputs ------------------------------------------------------------------------
    try:
        accounts, pool_warnings = load_credentials_pool(
            args.credentials, allow_placeholders=bool(args.allow_placeholders or args.sandbox)
        )
        targets, target_warnings = load_targets(args.targets, target_url=args.target_url)
        catalogue, selector_warnings = load_selector_catalogue(args.selectors)
        proxies = load_proxies(args.proxy_file) if args.proxy_mode == "file" else []
    except LoadTestError as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - configuration problems are usage errors
        print(f"✖ yapılandırma hatası: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    for warning in [*pool_warnings, *target_warnings, *selector_warnings]:
        print(f"⚠ {warning}")
    for account in accounts[:12]:
        print(f"→ hesap {account.index:02d}: {account.describe()}")
    for target in targets:
        verification = " + e-posta doğrulama" if target.requires_email_verification else ""
        print(f"→ hedef: {target.name} [{target.scenario}]{verification} → {target.target_url}")

    # --- 3) context count / account uniqueness --------------------------------------------
    contexts = int(args.contexts) if args.contexts else len(accounts)
    if contexts < 1:
        print("✖ --contexts en az 1 olmalı", file=sys.stderr)
        return 2
    if contexts > len(accounts) and not args.allow_account_reuse:
        print(
            f"✖ {contexts} bağlam için {len(accounts)} hesap var. Her bağlama BENZERSİZ hesap gerekir\n"
            f"  (aynı posta kutusunu iki bağlam dinlerse doğrulama linki yarışır).\n"
            f"  → havuza hesap ekleyin, --contexts düşürün veya --allow-account-reuse kabul edin.",
            file=sys.stderr,
        )
        return 2
    args.contexts = contexts
    args.concurrency = int(args.concurrency) if args.concurrency else contexts
    if args.concurrency < 1:
        print("✖ --concurrency en az 1 olmalı", file=sys.stderr)
        return 2

    # --- 4) scope gate (hard block-list always wins) ---------------------------------------
    try:
        scope = Scope.load(args.scope, [])
        blocked, unauthorized = enforce_scope(
            [target.target_url for target in targets], scope, i_am_authorized=bool(args.i_am_authorized)
        )
    except LoadTestError as exc:
        print(f"✖ scope: {exc}", file=sys.stderr)
        return 2
    print(
        f"→ scope: {len(scope.patterns)} pattern(s) from {args.scope} | "
        f"blocked_third_party={blocked or 'none'} | out_of_scope_override={unauthorized or 'none'}"
    )

    # --- 5) browser arguments / config ------------------------------------------------------
    browser_args = list(args.browser_args)
    if not args.chromium_sandbox:
        browser_args.extend(["--no-sandbox", "--disable-dev-shm-usage"])
    args.browser_args = browser_args

    config = Config(
        contexts=max(contexts, 1),
        headless=not args.headful,
        stealth=True,
        verify_stealth=True,
        captcha_action="skip",
        devices_spec="random",
        seed=20260928,
        default_timeout_ms=20_000,
        navigation_timeout_ms=45_000,
    )
    config.device_profiles = resolve_device_profiles(config.devices_spec, max(1, contexts))
    args.config = config

    run_id = new_run_id("loadtest")
    try:
        run_dir = ensure_dir(Path(args.artifacts) / run_id)
    except OSError as exc:
        print(f"✖ artefakt dizini oluşturulamadı: {exc}", file=sys.stderr)
        return 2
    # The detailed log goes next to the artefacts; the terminal keeps the readable report.
    setup_logging(args.log_level, log_file=run_dir / "loadtest.log", force_color=False if args.no_color else None)

    if args.check:
        print(f"→ doğrulama tamam: {len(accounts)} hesap, {len(targets)} hedef, "
              f"{len(catalogue.chains)} seçici zinciri, {len(proxies)} proxy | tarayıcı açılmadı (--check)")
        for target in targets:
            print(f"   • {target.name}: {target.context_url(context_index=0, run_id=run_id)}")
        return 0

    runner = LoadTestRunner(
        accounts=accounts,
        targets=targets,
        catalogue=catalogue,
        config=config,
        args=args,
        proxies=proxies,
        run_id=run_id,
        run_dir=run_dir,
    )
    if args.sandbox:
        print(f"🔒 sandbox modu — yerel mock hedef: {args.target_url} | hesaplar: {args.credentials.name}")

    print(
        f"→ {contexts} bağlam / {args.concurrency} paralel | {len(targets)} hedef x {contexts} hesap "
        f"= {len(targets) * contexts} koşu | proxy={'file' if proxies else 'off'} | "
        f"stealth=on | captcha=skip"
    )
    print(f"→ run: {run_id} | artefaktlar: {run_dir}")

    try:
        exit_code = int(asyncio.run(runner.run()))
    except KeyboardInterrupt:
        print("\n✖ kesildi — kısmi artefaktlar korundu", file=sys.stderr)
        return 130
    except LoadTestError as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - last resort, always explains itself
        logger.exception("koşu çöktü: %s", exc)
        print(f"✖ koşu çöktü: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"→ exit code: {exit_code} ({'PASSED ✅' if exit_code == 0 else 'bkz. artifacts ❌'})")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
