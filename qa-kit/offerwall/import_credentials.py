#!/usr/bin/env python3
"""``import_credentials.py`` - repair and validate a credential pool before a run.

The problem it solves
---------------------
Credentials copied out of a chat or a note arrive mangled. A real example (pasted verbatim into
the kit):

.. code-block:: json

    [{"email": "[hesap1@gmail.com](mailto:hesap1@gmail.com)",
      "imap_host": "[imap.gmail.com](http://imap.gmail.com)", "imap_port": 993, ...}]

The JSON is valid and contains an ``@``, so the old loader accepted it: the browser filled the
**whole Markdown link** into the sign-up form and IMAP tried to reach
``[imap.gmail.com](http://imap.gmail.com)``. Both failures surfaced much later, looking like a
site problem. :class:`~account_pool.AccountPool` now refuses such values outright; this tool
repairs them and tells you exactly what it changed.

What it does
------------
* unwraps Markdown links ``[text](target)`` (falls back to the target when the text is empty),
* strips ``mailto:``, ``http(s)://``, angle brackets, backticks and surrounding quotes,
* removes Markdown escapes (``\\_``, ``\\*``) and **all internal whitespace**,
* coerces the port to an int and checks it is in range,
* validates addresses/hosts with the same patterns the pool enforces (single source of truth),
* de-duplicates by lower-cased address, keeping the first occurrence,
* flags placeholder passwords (``16_haneli_uygulama_sifresi_buraya`` and friends),
* warns when there are fewer accounts than contexts (accounts get reused round-robin, so two
  accounts do **not** give ten isolated identities),
* writes the result with mode ``0600`` and prints a **masked** report.

Usage::

    # see what would change, write nothing
    python3 qa-kit/offerwall/import_credentials.py --in pasted.json --check

    # repair + write credentials.json (0600) and validate the pool
    python3 qa-kit/offerwall/import_credentials.py --in pasted.json --out credentials.json

    # enforce a real production pool
    python3 qa-kit/offerwall/import_credentials.py --in pasted.json --out credentials.json \\
        --require-gmail --min-accounts 10

Exit codes: 0 clean · 1 problems remain (see report) · 2 usage / unreadable input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Optional, Sequence

_HERE: Final[Path] = Path(__file__).resolve().parent
_REPO: Final[Path] = _HERE.parent.parent
for _path in (str(_REPO), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from account_pool import (  # noqa: E402  (single source of truth for the rules)
    EMAIL_RE,
    HOST_RE,
    PASTE_ARTIFACT_MARKERS,
    Account,
    looks_like_placeholder,
)

#: ``[label](target)`` - the Markdown wrapper that started all of this.
MARKDOWN_LINK_RE: Final[re.Pattern[str]] = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<target>[^)]*)\)")
#: ``<mailto:x@y>`` style autolinks.
AUTOLINK_RE: Final[re.Pattern[str]] = re.compile(r"<(?P<value>[^>]+)>")
SCHEME_RE: Final[re.Pattern[str]] = re.compile(r"^(?:mailto:|imaps?://|https?://)", re.IGNORECASE)
ESCAPE_RE: Final[re.Pattern[str]] = re.compile(r"\\([\\`*_{}\[\]()#+.!-])")


def clean_text(value: Any) -> str:
    """Normalise one pasted scalar into the bare value it was meant to be."""
    text = "" if value is None else str(value)
    previous = None
    while text != previous:  # links can nest (`[a](b)` inside a label)
        previous = text
        text = MARKDOWN_LINK_RE.sub(lambda match: match.group("label") or match.group("target"), text)
    text = AUTOLINK_RE.sub(lambda match: match.group("value"), text)
    text = ESCAPE_RE.sub(r"\1", text)
    text = SCHEME_RE.sub("", text.strip())
    text = text.replace("`", "").strip()
    text = text.strip("\"'")            # quotes
    text = text.strip(",;")              # list separators left over from a JSON-ish paste
    text = text.strip().strip("\"'").strip(",;").strip()
    text = re.sub(r"[\s\u00a0]+", "", text)  # "hesap 1@gmail.com" -> "hesap1@gmail.com"
    return text


def clean_port(value: Any, *, default: int = 993) -> tuple[int, Optional[str]]:
    """Coerce a pasted port into an int; returns ``(port, problem)``."""
    if value in (None, ""):
        return default, None
    text = clean_text(value)
    try:
        port = int(float(text))
    except (TypeError, ValueError):
        return default, f"port okunamadı: {value!r}"
    if not 1 <= port <= 65535:
        return default, f"port aralık dışı: {port}"
    return port, None


@dataclass(slots=True)
class RepairReport:
    """Per-account outcome of the import."""

    accounts: list[Account] = field(default_factory=list)
    repairs: dict[str, list[str]] = field(default_factory=dict)
    problems: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def import_pool(payload: Any, *, require_gmail: bool = False, allow_placeholders: bool = False) -> RepairReport:
    """Repair *payload* (a parsed JSON array/object) into a validated account list."""
    report = RepairReport()
    raw_accounts: list[Any]
    if isinstance(payload, dict):
        raw_accounts = list(payload.get("accounts") or [])
    elif isinstance(payload, list):
        raw_accounts = list(payload)
    else:
        report.problems["<file>"] = [f"beklenen JSON dizi ya da {{'accounts': [...]}}, gelen: {type(payload).__name__}"]
        return report

    seen: dict[str, str] = {}
    for position, item in enumerate(raw_accounts, start=1):
        label = f"#{position}"
        if not isinstance(item, dict):
            report.skipped.append(f"{label}: nesne değil ({type(item).__name__})")
            continue
        if not item.get("email") and any(str(key).startswith("_") or str(key) == "_comment" for key in item):
            report.skipped.append(f"{label}: açıklama girdisi (_comment) - hesap değil")
            continue

        raw_email = item.get("email")
        email = clean_text(raw_email)
        label = email or label
        changes: list[str] = []
        issues: list[str] = []

        if raw_email is not None and clean_text(raw_email) != str(raw_email):
            changes.append(f"email temizlendi: {str(raw_email)[:48]!r} → {email!r}")
        if email and any(marker in email for marker in PASTE_ARTIFACT_MARKERS):
            issues.append(f"e-posta hâlâ yapıştırma artığı içeriyor: {email!r}")
        if not EMAIL_RE.match(email):
            issues.append(f"geçersiz e-posta: {email!r}")
        if require_gmail and not email.lower().endswith(("@gmail.com", "@googlemail.com")):
            issues.append(f"Gmail adresi bekleniyordu: {email!r}")

        password = str(item.get("password") or "")
        if password != password.strip():
            changes.append("parola çevresindeki boşluk temizlendi")
            password = password.strip()
        if not password:
            issues.append("parola boş")
        elif looks_like_placeholder(password):
            message = (
                f"parola yer tutucu: {password[:32]!r} — Gmail'de 2 adımlı doğrulamayı açıp "
                f"16 karakterlik Uygulama Şifresi üretin"
            )
            if allow_placeholders:
                report.warnings.append(f"{email}: {message} (--allow-placeholders)")
            else:
                issues.append(message)

        raw_host = item.get("imap_host", "imap.gmail.com")
        host = clean_text(raw_host)
        if clean_text(raw_host) != str(raw_host):
            changes.append(f"imap_host temizlendi: {str(raw_host)[:48]!r} → {host!r}")
        if not HOST_RE.match(host):
            issues.append(f"geçersiz IMAP host: {host!r} (şemasız, çıplak hostname beklenir)")
        if email and email.lower().endswith(("@gmail.com", "@googlemail.com")) and host not in {
            "imap.gmail.com",
        }:
            report.warnings.append(f"{email}: Gmail adresi ama IMAP host {host!r} (imap.gmail.com beklenirdi)")

        port, port_problem = clean_port(item.get("imap_port"))
        if port_problem:
            issues.append(port_problem)
        elif str(item.get("imap_port", "")).strip() not in {str(port), ""}:
            changes.append(f"imap_port düzeltildi → {port}")

        enabled = item.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in {"1", "true", "yes", "on", "evet"}

        if issues:
            report.problems[label] = issues
        key = email.lower()
        if key in seen:
            report.duplicates.append(f"{label} (ilk kayıt: {seen[key]})")
        else:
            seen[key] = label
            report.accounts.append(
                Account(
                    email=email,
                    password=password,
                    imap_host=host,
                    imap_port=port,
                    enabled=bool(enabled),
                    note=str(item.get("note") or ""),
                )
            )
        if changes:
            report.repairs[label] = changes

    return report


def render(report: RepairReport, *, source: Path, target: Optional[Path], contexts: int, require_gmail: bool) -> str:
    lines: list[str] = [f"→ {source}: {len(report.accounts)} hesap" + (f" → {target}" if target else " (yazılmadı)")]
    for label, changes in report.repairs.items():
        for change in changes:
            lines.append(f"  🔧 [{label}] {change}")
    for label, issues in report.problems.items():
        for issue in issues:
            lines.append(f"  ✖ [{label}] {issue}")
    for duplicate in report.duplicates:
        lines.append(f"  ⚠ yinelenen hesap atlandı: {duplicate}")
    for skipped in report.skipped:
        lines.append(f"  ⚠ atlandı: {skipped}")
    for warning in report.warnings:
        lines.append(f"  ⚠ {warning}")

    if len(report.accounts) < contexts:
        lines.append(
            f"  ⚠ {len(report.accounts)} hesap, {contexts} context → hesaplar sırayla tekrar kullanılacak "
            f"(ctx-{len(report.accounts):02d} yine ilk hesaba döner). 'Her context'e ayrı kimlik' "
            f"iddiası {len(report.accounts)} hesapla geçerli DEĞİL; {contexts} ayrı hesap gerekir."
        )
    if require_gmail and report.accounts:
        lines.append(f"  ✔ tüm hesaplar Gmail: {len(report.accounts)}/{len(report.accounts)}")
    lines.append("  " + ("✔ havuz kullanıma hazır" if report.ok else "✖ düzeltilmesi gereken sorunlar var"))
    return "\n".join(lines)


def write_pool(path: Path, accounts: Sequence[Account]) -> None:
    """Write ``credentials.json`` with owner-only permissions."""
    payload = [
        {
            "email": account.email,
            "password": account.password,
            "imap_host": account.imap_host,
            "imap_port": account.imap_port,
            "enabled": account.enabled,
            **({"note": account.note} if account.note else {}),
        }
        for account in accounts
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - filesystems without POSIX modes
        pass


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="import_credentials.py",
        description="Yapıştırılmış/bozuk kimlik dosyasını onarır, doğrular ve credentials.json yazar.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--in", dest="source", type=Path, required=True, help="Kaynak JSON (bozuk olabilir).")
    parser.add_argument("--out", dest="target", type=Path, default=None, help="Yazılacak credentials.json.")
    parser.add_argument("--check", action="store_true", help="Hiçbir şey yazma, yalnızca raporla.")
    parser.add_argument("--min-accounts", type=int, default=1, help="En az bu kadar hesap gerekli.")
    parser.add_argument("--contexts", type=int, default=10, help="Karşılaştırılacak context sayısı (uyarı için).")
    parser.add_argument("--require-gmail", action="store_true", help="Yalnızca Gmail adreslerini kabul et.")
    parser.add_argument("--json", action="store_true", help="Raporu JSON olarak yaz (parolalar maskeli).")
    parser.add_argument("--allow-placeholders", action="store_true",
                        help="Yer tutucu parolaları hata değil uyarı say (şablon dosyaları için).")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    source = Path(args.source)
    if not source.exists():
        print(f"✖ bulunamadı: {source}", file=sys.stderr)
        return 2
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"✖ {source} okunamadı/ayrıştırılamadı: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    report = import_pool(payload, require_gmail=args.require_gmail, allow_placeholders=args.allow_placeholders)
    target = None if args.check else (args.target or source.with_name("credentials.json"))

    if args.json:
        print(
            json.dumps(
                {
                    "source": str(source),
                    "target": str(target) if target else None,
                    "accounts": [account.to_dict(mask=True) for account in report.accounts],
                    "repairs": report.repairs,
                    "problems": report.problems,
                    "warnings": report.warnings,
                    "duplicates": report.duplicates,
                    "skipped": report.skipped,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render(report, source=source, target=target, contexts=args.contexts, require_gmail=args.require_gmail))

    enough = len(report.accounts) >= max(1, args.min_accounts)
    if not enough:
        print(f"  ✖ yalnızca {len(report.accounts)} hesap var, --min-accounts {args.min_accounts}", file=sys.stderr)

    if target is not None and report.ok and enough:
        write_pool(target, report.accounts)
        print(f"  ✔ yazıldı: {target} (0600)")

    return 0 if (report.ok and enough) else 1


if __name__ == "__main__":
    raise SystemExit(main())
