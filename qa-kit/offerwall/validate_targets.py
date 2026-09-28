#!/usr/bin/env python3
"""``validate_targets.py`` - pre-flight validation for any target sheet (no browser needed).

Why this exists
---------------
A hand-written generator produced a sheet that *looked* fine but contained two traps:

* ``submit_button_text: "Kayıt Ol,Register,Sign Up"`` - WAFT passes this value verbatim into
  ``button:has-text("...")`` and does **not** split on commas, so the candidate can never match.
  The run still succeeds (WAFT's built-in label list covers "-kayıt ol-/register/sign up"), which
  is exactly why the mistake goes unnoticed: the field is dead, silently.
* targets on third-party platforms - refused at run time by the scope gate, but only *after*
  you have configured everything else.

This tool reports both **before** a browser is ever launched, plus the whole family of sheet
mistakes this kit has already been bitten by: verification fields on rows that must not wait for
mail, verification rows with no address, duplicated rows, unknown placeholders, unbalanced
selectors and identical identities across accounts.

Usage::

    python3 qa-kit/offerwall/validate_targets.py --targets qa-kit/offerwall/targets_offerwall.xlsx
    python3 qa-kit/offerwall/validate_targets.py --targets /tmp/mine.xlsx --json

Exit codes: 0 no errors · 1 errors found · 2 file/usage problem.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final, Iterable, Optional, Sequence

_HERE: Final[Path] = Path(__file__).resolve().parent
_REPO: Final[Path] = _HERE.parent.parent
for _path in (str(_REPO), str(_HERE), str(_HERE.parent)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pandas as pd  # noqa: E402

from run_offerwall import (  # noqa: E402  (single source of truth for the scope rules)
    BLOCKED_THIRD_PARTY_SUFFIXES,
    load_scope,
    scope_host_candidates,
)

TRUTHY: Final[frozenset[str]] = frozenset({"true", "1", "yes", "y", "evet", "on", "x"})
PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(r"\{([a-zA-Z_][\w.{}]*)\}")
KNOWN_PLACEHOLDERS: Final[frozenset[str]] = frozenset({"email", "password"})
VERIFICATION_COLUMNS: Final[tuple[str, ...]] = (
    "verification_email",
    "verification_subject_regex",
    "verification_link_regex",
    "verification_otp_field",
)
REGISTER_KEYS: Final[tuple[str, ...]] = ("email", "password")


@dataclass(frozen=True, slots=True)
class Issue:
    """One validation finding."""

    severity: str  # "error" | "warning"
    row: str
    field: str
    message: str


def normalize_header(name: Any) -> str:
    """WAFT-compatible header normalisation (lower, snake_case, ASCII-safe punctuation)."""
    text = str(name or "").strip().lower()
    text = re.sub(r"[\s\-./]+", "_", text)
    text = re.sub(r"[^0-9a-z_ğüşıöç]", "", text)
    return re.sub(r"_+", "_", text).strip("_")


def is_truthy(value: Any) -> bool:
    return str(value).strip().lower() in TRUTHY


def bad_selector(value: str) -> Optional[str]:
    """Return a reason when a selector string looks unbalanced, else ``None``."""
    text = value.strip()
    if not text:
        return "boş selector"
    for opener, closer, label in (("(", ")", "parantez"), ("[", "]", "köşeli parantez"), ("{", "}", "süslü parantez")):
        if text.count(opener) != text.count(closer):
            return f"dengesiz {label}"
    for quote in ("'", '"'):
        if text.count(quote) % 2:
            return f"kapanmamış {quote} tırnağı"
    if text.count("(") != text.count(")") and ":" in text:  # :has-text( / :nth-of-type(
        return "dengesiz parantez"
    return None


def validate_sheet(path: Path) -> list[Issue]:
    """Validate every sheet of the workbook; returns all findings."""
    issues: list[Issue] = []
    issues_by_sheet_notes: list[tuple[str, int]] = []
    try:
        sheets = pd.read_excel(path, sheet_name=None)
    except Exception as exc:  # noqa: BLE001 - a broken workbook is a usage problem
        return [Issue("error", "-", "<workbook>", f"okunamadı: {type(exc).__name__}: {exc}")]

    for sheet_name, frame in sheets.items():
        if frame.empty:
            issues.append(Issue("warning", "-", sheet_name, "boş sayfa atlandı"))
            continue
        columns = {normalize_header(column): column for column in frame.columns}
        if "target_url" not in columns:
            # The kit's workbook ships a second, human-readable "columns" sheet; WAFT's loader
            # ignores it, so must we - otherwise a documentation sheet looks like 25 broken rows.
            issues_by_sheet_notes.append((sheet_name, len(frame)))
            continue
        for required in ("scenario",):
            if required not in columns:
                issues.append(Issue("error", "-", sheet_name, f"zorunlu kolon eksik: {required}"))

        seen_rows: dict[tuple[str, str], str] = {}
        identities: set[tuple[str, str]] = set()

        for position, (_, raw_row) in enumerate(frame.iterrows(), start=1):
            row = {key: ("" if pd.isna(raw_row[column]) else str(raw_row[column])) for key, column in columns.items()}
            name = row.get("name") or f"satır-{position}"
            url = row.get("target_url", "").strip()
            scenario = row.get("scenario", "").strip().lower()

            if not url:
                issues.append(Issue("error", name, "target_url", "URL boş"))
            elif not url.startswith(("http://", "https://")):
                issues.append(Issue("warning", name, "target_url", f"http(s) ile başlamıyor: {url}"))

            # ---- scope ---------------------------------------------------------------
            for candidate in scope_host_candidates(url)[:1]:
                if any(
                    candidate == suffix or candidate.endswith("." + suffix)
                    for suffix in BLOCKED_THIRD_PARTY_SUFFIXES
                ):
                    issues.append(
                        Issue(
                            "error",
                            name,
                            "target_url",
                            f"3. parti offerwall/mikro görev platformu: {candidate} — kit bunu koşu anında "
                            f"reddeder (--i-am-authorized dahil). Kendi staging'iniz için --base-url kullanın.",
                        )
                    )

            # ---- submit_button_text: single label, NOT a comma list -------------------
            submit_text = row.get("submit_button_text", "").strip()
            if "," in submit_text:
                issues.append(
                    Issue(
                        "error",
                        name,
                        "submit_button_text",
                        f"virgüllü liste: {submit_text!r} → WAFT bunu tek etiket olarak "
                        f"button:has-text(\"{submit_text}\") biçiminde kullanır ve ASLA eşleşmez "
                        f"(sessiz ölü alan). Tek etiket verin; yedekleri selectors json'da tutun — "
                        f"WAFT zaten 'kayıt ol', 'register', 'sign up' gibi etiketleri yerleşik olarak deniyor.",
                    )
                )
            elif "(" in submit_text:
                reason = bad_selector(submit_text)
                if reason:
                    issues.append(Issue("warning", name, "submit_button_text", reason))

            # ---- selectors -----------------------------------------------------------
            for key, value in row.items():
                if not key.endswith(("_selector",)) or key == "submit_button_text" or not value.strip():
                    continue
                reason = bad_selector(value)
                if reason:
                    issues.append(Issue("warning", name, key, f"şüpheli selector ({reason}): {value!r}"))
                elif "," in value:
                    issues.append(
                        Issue(
                            "warning",
                            name,
                            key,
                            "virgüllü CSS listesi — Playwright kabul eder, ancak WAFT seçici "
                            "kataloğunda alan başına TEK çözüm bekler; kalıcı çözüm için "
                            "selectors json'a alın.",
                        )
                    )

            # ---- verification columns -------------------------------------------------
            requires = is_truthy(row.get("requires_email_verification", ""))
            verification_email = row.get("verification_email", "").strip()
            if requires and not verification_email:
                issues.append(Issue("error", name, "verification_email", "doğrulama bekleyen satırda adres yok"))
            if verification_email and not requires:
                issues.append(
                    Issue(
                        "error",
                        name,
                        "verification_email",
                        "requires_email_verification kapalıyken adres verilmiş → satır mail bekler "
                        "(koşu-1'de tüm satırlar bu yüzden başarısız oldu)",
                    )
                )
            if scenario == "email-verify" and not requires:
                issues.append(Issue("warning", name, "scenario", "scenario=email-verify ama doğrulama kapalı"))
            for key in VERIFICATION_COLUMNS:
                if row.get(key, "").strip() and not (requires or scenario == "email-verify"):
                    issues.append(Issue("warning", name, key, "doğrulama alanı ama senaryo doğrulama değil"))

            # ---- placeholders ---------------------------------------------------------
            for key, value in row.items():
                if not value:
                    continue
                if any(token in value for token in ("{email}", "{password}")):
                    continue
                for token in PLACEHOLDER_RE.findall(value):
                    if token not in KNOWN_PLACEHOLDERS:
                        issues.append(
                            Issue("warning", name, key, f"bilinmeyen yer tutucu {{{token}}} — doldurulmaz")
                        )

            # ---- register rows should carry the confirm field --------------------------
            is_survey = "survey" in f"{name} {url}".lower() or "anket" in f"{name} {url}".lower()
            if row.get("password", "").strip() and not is_survey and not row.get("password_confirm", "").strip():
                issues.append(
                    Issue(
                        "warning",
                        name,
                        "password_confirm",
                        "parola tekrarı kolonu yok veya boş; form bu alanı istiyorsa kayıt hiç POST olmaz "
                        "(daha önce birebir bu hata yaşandı)",
                    )
                )

            # ---- timing ---------------------------------------------------------------
            wait = row.get("wait_after_submit_ms", "").strip()
            if wait:
                try:
                    if float(wait) <= 0:
                        issues.append(Issue("warning", name, "wait_after_submit_ms", "0 veya negatif"))
                except ValueError:
                    issues.append(Issue("warning", name, "wait_after_submit_ms", f"sayı değil: {wait!r}"))

            # ---- duplicates & identity diversity --------------------------------------
            key = (url, scenario)
            if url:
                if key in seen_rows:
                    issues.append(
                        Issue("warning", name, "target_url", f"aynı URL+senaryo tekrar ediyor (önceki: {seen_rows[key]})")
                    )
                else:
                    seen_rows[key] = name
            if row.get("first_name") or row.get("last_name"):
                identities.add((row.get("first_name", ""), row.get("last_name", "")))

        if len(frame) > 1 and len(identities) == 1:
            first, last = next(iter(identities))
            issues.append(
                Issue(
                    "warning",
                    "-",
                    sheet_name,
                    f"{len(frame)} satır aynı kimliği kullanıyor ({first} {last}) — çoklu hesap "
                    f"senaryosunda farklı isimler beklenir",
                )
            )
    for sheet_name, row_count in issues_by_sheet_notes:
        issues.append(
            Issue("warning", "-", sheet_name, f"veri sayfası değil (target_url kolonu yok, {row_count} satır) — atlandı")
        )
    return issues


def render(issues: Iterable[Issue], *, targets: Path) -> str:
    items = list(issues)
    errors = [item for item in items if item.severity == "error"]
    warnings = [item for item in items if item.severity == "warning"]
    lines = [f"→ {targets}: {len(errors)} hata, {len(warnings)} uyarı"]
    for item in errors:
        lines.append(f"  ✖ [{item.row}] {item.field}: {item.message}")
    for item in warnings:
        lines.append(f"  ⚠ [{item.row}] {item.field}: {item.message}")
    if not items:
        lines.append("  ✔ sorun bulunmadı")
    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="validate_targets.py",
        description="Hedef sayfasını (xlsx/json/csv) tarayıcı açmadan doğrular: kapsam + bilinen tuzaklar.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--targets", type=Path, default=_HERE / "targets_offerwall.xlsx")
    parser.add_argument("--scope", type=Path, default=_HERE.parent / "authorized_hosts.txt")
    parser.add_argument("--allow-host", action="append", default=[])
    parser.add_argument("--i-am-authorized", action="store_true")
    parser.add_argument("--json", action="store_true", help="Bulguları JSON olarak yaz.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not Path(args.targets).exists():
        print(f"✖ bulunamadı: {args.targets}", file=sys.stderr)
        return 2

    issues = validate_sheet(Path(args.targets))

    # scope allow-list check runs on the same rules the runner enforces
    try:
        scope = load_scope(Path(args.scope), args.allow_host)
        frame_sheets = pd.read_excel(args.targets, sheet_name=None)
        for sheet_name, frame in frame_sheets.items():
            columns = [column for column in frame.columns if normalize_header(column) == "target_url"]
            if not columns:
                continue
            for position, value in enumerate(frame[columns[0]], start=1):
                url = "" if pd.isna(value) else str(value)
                candidates = scope_host_candidates(url)
                if not candidates or any(
                    candidate == suffix or candidate.endswith("." + suffix)
                    for candidate in candidates
                    for suffix in BLOCKED_THIRD_PARTY_SUFFIXES
                ):
                    continue  # blocked case already reported above with a better message
                if not any(scope.allows(candidate) for candidate in candidates):
                    issues.append(
                        Issue(
                            "error",
                            f"satır-{position}",
                            "target_url",
                            f"kapsam dosyasında yok: {candidates[0]} — qa-kit/authorized_hosts.txt "
                            f"içine ekleyin (kendi ortamınız) ya da --allow-host + --i-am-authorized verin",
                        )
                    )
    except Exception as exc:  # noqa: BLE001 - scope file problems must not hide sheet findings
        issues.append(Issue("warning", "-", "<scope>", f"kapsam kontrolü atlandı: {type(exc).__name__}: {exc}"))

    if args.json:
        print(json.dumps([asdict(item) for item in issues], ensure_ascii=False, indent=2))
    else:
        print(render(issues, targets=Path(args.targets)))

    return 1 if any(item.severity == "error" for item in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
