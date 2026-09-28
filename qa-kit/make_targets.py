#!/usr/bin/env python3
"""``make_targets.py`` - generate the ``targets.xlsx`` data source used by the regression kit.

Why a generator instead of a hand-made sheet?  The kit must be pointable at *your*
environment: staging URL, mail domain and row count are parameters, so the same script
produces the file for localhost, a staging host or a CI environment.

Column contract (kept identical to WAFT's loader, see ``waft/data_source.py``)
--------------------------------------------------------------------------
======================  =======================================================
Column                  Meaning
======================  =======================================================
``target_url``          Page the context must open (the sign-up form URL).
``scenario``            WAFT scenario: ``form-submit`` / ``email-verify`` / ``smoke``.
``email``               Address used in the form **and** the inbox polled over IMAP.
``password``            Password value (masked in logs/reports by WAFT).
``first_name``          Name-like field, proves alias matching works.
``success_selector``    CSS selector proving the sign-up succeeded.
``requires_email_verification``  ``true`` -> run the IMAP/verification step.
``verification_email``  Mailbox to poll (may equal ``email``).
``verification_subject_regex``   Subject filter for the verification mail.
======================  =======================================================

Extra columns (all optional for WAFT, all supported by the loader): ``name``,
``selectors``, ``steps``, ``expect_error``, ``iterations``, ``tags``, ``notes``.

Usage::

    python qa-kit/make_targets.py --base-url http://127.0.0.1:8080 \
        --mail-domain demo.waft.local --rows 12 --out qa-kit/targets.xlsx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Final, Sequence

try:  # pandas is a hard runtime dependency of the framework (Excel data source)
    import pandas as pd
except ImportError as exc:  # pragma: no cover - environment problem, not a logic branch
    raise SystemExit("pandas is required: pip install -r requirements.txt") from exc

#: Column order of the produced sheet - the first nine mirror the requested contract.
COLUMNS: Final[tuple[str, ...]] = (
    "target_url",
    "scenario",
    "email",
    "password",
    "first_name",
    "success_selector",
    "requires_email_verification",
    "verification_email",
    "verification_subject_regex",
    "last_name",
    "password_confirm",
    "terms",
    "name",
    "iterations",
    "tags",
    "notes",
)

#: Documentation sheet - self-documenting spreadsheets survive hand-overs.
DOC_ROWS: Final[tuple[tuple[str, str, str], ...]] = (
    ("target_url", "yes", "Sign-up / landing URL that the context opens first."),
    ("scenario", "no", "auto | form-submit | email-verify | smoke | load-test | api-discovery."),
    ("email", "yes", "Value typed into the e-mail field; also the IMAP account when verification runs."),
    ("password", "yes", "Typed into password + confirmation fields. Masked in all logs."),
    ("first_name", "no", "Exercises the alias matcher (name / ad / isim / first name …)."),
    ("success_selector", "no", "CSS that proves the submit worked, e.g. [data-testid='success']."),
    ("requires_email_verification", "no", "true -> poll IMAP after submit and follow the link."),
    ("verification_email", "no", "Mailbox polled over IMAP (usually equals email)."),
    ("verification_subject_regex", "no", "Regex applied to the subject; keeps unrelated mail out."),
    ("last_name", "no", "Surname value; required by most sign-up forms (alias matching)."),
    ("password_confirm", "no", "Repeat-password field - must equal 'password' or the app rejects the form."),
    ("terms", "no", "Required consent checkbox (KVKK/terms). true/false/1/0/evet/hayir."),
    ("name", "no", "Human readable row label used in reports/artifacts."),
    ("iterations", "no", "How many times this row is executed (defaults to --iterations)."),
    ("tags", "no", "Free-form tags for CI filtering/reporting."),
    ("notes", "no", "Anything useful for the humans reading the sheet."),
)


def build_rows(
    *,
    base_url: str,
    mail_domain: str,
    rows: int,
    password: str,
    locale_hint: str,
    require_verification: bool = True,
) -> list[dict[str, Any]]:
    """Return *rows* sign-up records pointing at *base_url*.

    Emails are generated inside *mail_domain* - a domain you control - because the IMAP
    watcher has to be able to read them.  Never point ``verification_email`` at a mailbox
    you do not own.
    """
    first_names: Sequence[str] = (
        "Ayse", "Mehmet", "Zeynep", "Can", "Elif", "Burak", "Selin", "Emre",
        "Deniz", "Kerem", "Ece", "Baris", "Nil", "Tolga", "Derya", "Onur",
    )
    last_names: Sequence[str] = (
        "Yilmaz", "Kaya", "Demir", "Sahin", "Celik", "Yildiz", "Yildirim", "Ozturk",
        "Aydin", "Ozdemir", "Arslan", "Dogan", "Kilic", "Aslan", "Cetin", "Koc",
    )
    records: list[dict[str, Any]] = []
    for index in range(1, rows + 1):
        first_name = first_names[(index - 1) % len(first_names)]
        slug = first_name.lower()
        email = f"qa.{slug}.{index:03d}@{mail_domain}"
        records.append(
            {
                "target_url": f"{base_url.rstrip('/')}/",
                "scenario": "email-verify",
                "email": email,
                "password": password,
                "first_name": first_name,
                "success_selector": "[data-testid='success']",
                "requires_email_verification": "true" if require_verification else "false",
                "verification_email": email,
                "verification_subject_regex": r"(doğrula|dogrula|verify|aktivasyon|activat)",
                "last_name": last_names[(index - 1) % len(last_names)],
                "password_confirm": password,
                "terms": "true",
                "name": f"signup-verify-{index:02d}",
                "iterations": 1,
                "tags": f"regression,email-verify,{locale_hint}",
                "notes": "Sign-up + e-mail verification regression row (owned/staging target).",
            }
        )
    return records


def write_workbook(records: Sequence[dict[str, Any]], path: Path) -> Path:
    """Write the data sheet plus the documentation sheet; returns the written path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(list(records), columns=list(COLUMNS))
    docs = pd.DataFrame(list(DOC_ROWS), columns=["column", "required", "description"])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="targets", index=False)
        docs.to_excel(writer, sheet_name="columns", index=False)
        # Autosize-ish widths so the sheet is readable when opened by a human.
        for sheet_name, frame_ref in (("targets", frame), ("columns", docs)):
            worksheet = writer.sheets[sheet_name]
            for position, column in enumerate(frame_ref.columns, start=1):
                width = max(len(str(column)) + 2, *(len(str(value)) + 2 for value in frame_ref[column]))
                worksheet.column_dimensions[worksheet.cell(row=1, column=position).column_letter].width = min(width, 60)
    return path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="make_targets.py",
        description="Generate qa-kit/targets.xlsx for the WAFT regression/load harness.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080",
        help="Sign-up URL of the system under test (your own app / staging). Never a third-party site.",
    )
    parser.add_argument(
        "--mail-domain",
        default="demo.waft.local",
        help="Domain of the generated e-mail addresses; its mailbox must be readable over IMAP.",
    )
    parser.add_argument("--rows", type=int, default=12, help="How many sign-up rows to generate.")
    parser.add_argument("--password", default="Str0ng-Passw0rd!", help="Password written to every row.")
    parser.add_argument("--locale-hint", default="tr", help="Tag added to the 'tags' column (reporting only).")
    parser.add_argument("--require-verification", dest="require_verification", action="store_true", default=True,
                        help="Set requires_email_verification=true (default) - needs a working IMAP.")
    parser.add_argument("--no-verification", dest="require_verification", action="store_false",
                        help="Skip the IMAP step (useful for local smoke runs without a mail server).")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("targets.xlsx"), help="Output .xlsx path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.rows < 1:
        print("--rows must be >= 1", file=sys.stderr)
        return 2
    records = build_rows(
        base_url=args.base_url,
        mail_domain=args.mail_domain,
        rows=args.rows,
        password=args.password,
        locale_hint=args.locale_hint,
        require_verification=args.require_verification,
    )
    path = write_workbook(records, Path(args.out))
    print(f"✔ {path} written: {len(records)} row(s) -> {args.base_url}  (mailbox domain: {args.mail_domain})")
    print(f"  columns: {', '.join(COLUMNS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
