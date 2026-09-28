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
