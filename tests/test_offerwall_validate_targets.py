"""Tests for ``qa-kit/offerwall/validate_targets.py`` — the pre-flight sheet validator.

The validator was written in response to a hand-made target sheet that carried two traps the kit
refuses at run time: third-party hosts and a comma-joined ``submit_button_text`` (which WAFT feeds
verbatim into ``button:has-text("...")`` and therefore can never match — a silently dead field).
These tests pin both, plus the false positives the first draft produced (a documentation sheet
being read as 25 broken rows, and survey rows being nagged about ``password_confirm``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "qa-kit" / "offerwall"
for extra in (ROOT, KIT, ROOT / "qa-kit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import validate_targets as validator  # noqa: E402


def _write(path: Path, rows: list[dict[str, object]], sheet: str = "targets") -> Path:
    pd.DataFrame(rows).to_excel(path, index=False, sheet_name=sheet)
    return path


def _messages(issues: list[validator.Issue]) -> str:
    return "\n".join(f"{item.severity}|{item.row}|{item.field}|{item.message}" for item in issues)


def _errors(issues: list[validator.Issue]) -> list[validator.Issue]:
    return [item for item in issues if item.severity == "error"]


# --------------------------------------------------------------------------------------
# the sheet that triggered this tool
# --------------------------------------------------------------------------------------
def test_third_party_sheet_is_refused_with_both_findings(tmp_path: Path) -> None:
    sheet = _write(
        tmp_path / "user.xlsx",
        [
            {
                "target_url": "https://timewall.io/register",
                "scenario": "email-verify",
                "email": "{email}",
                "password": "{password}",
                "password_confirm": "{password}",
                "submit_button_text": "Kayıt Ol,Register,Sign Up",
                "requires_email_verification": True,
                "verification_email": "{email}",
            },
            {
                "target_url": "https://jumptask.io/register",
                "scenario": "email-verify",
                "email": "{email}",
                "password": "{password}",
                "password_confirm": "{password}",
                "submit_button_text": "Kayıt Ol,Register,Sign Up",
                "requires_email_verification": True,
                "verification_email": "{email}",
            },
        ],
    )
    issues = validator.validate_sheet(sheet)
    text = _messages(issues)
    assert len(_errors(issues)) == 4, text
    assert text.count("3. parti offerwall/mikro görev platformu") == 2
    assert text.count("virgüllü liste") == 2
    assert "button:has-text" in text  # the reason must be explicit, not just "invalid"
    # both rows share a verification address requirement, so no verification errors appear
    assert "doğrulama bekleyen satırda adres yok" not in text


def test_validator_exit_code_is_one_on_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sheet = _write(tmp_path / "bad.xlsx", [{"target_url": "https://timewall.io/register", "scenario": "email-verify"}])
    assert validator.main(["--targets", str(sheet)]) == 1
    assert "✖" in capsys.readouterr().out


# --------------------------------------------------------------------------------------
# the kit's own sheet must be clean (no false positives)
# --------------------------------------------------------------------------------------
def test_kit_sheet_has_no_errors() -> None:
    issues = validator.validate_sheet(KIT / "targets_offerwall.xlsx")
    assert _errors(issues) == [], _messages(issues)


def test_documentation_sheet_is_skipped_not_read_as_rows(tmp_path: Path) -> None:
    """The kit workbook ships a human-readable "columns" sheet; it must not look like 25 rows."""
    path = tmp_path / "with_notes.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([{"target_url": "http://127.0.0.1:8090/register", "scenario": "form-submit"}]).to_excel(
            writer, sheet_name="targets", index=False
        )
        pd.DataFrame([{"column": f"col_{i}", "required": True, "description": "x" * 10} for i in range(25)]).to_excel(
            writer, sheet_name="columns", index=False
        )
    issues = validator.validate_sheet(path)
    assert _errors(issues) == [], _messages(issues)
    assert any("veri sayfası değil" in item.message for item in issues)
    assert not any(item.row.startswith("satır-2") for item in issues)


def test_survey_rows_are_not_nagged_about_password_confirm() -> None:
    issues = validator.validate_sheet(KIT / "targets_offerwall.xlsx")
    survey_warnings = [
        item for item in issues if item.field == "password_confirm" and "survey" in item.row.lower()
    ]
    assert survey_warnings == []


# --------------------------------------------------------------------------------------
# the other traps this kit has already been bitten by
# --------------------------------------------------------------------------------------
def test_verification_fields_on_a_non_verifying_row_are_an_error(tmp_path: Path) -> None:
    sheet = _write(
        tmp_path / "verify_leak.xlsx",
        [
            {
                "target_url": "http://127.0.0.1:8090/survey/1",
                "scenario": "form-submit",
                "requires_email_verification": "false",
                "verification_email": "{email}",
            }
        ],
    )
    issues = validator.validate_sheet(sheet)
    text = _messages(issues)
    assert any(item.field == "verification_email" and item.severity == "error" for item in issues), text
    # the ad-based URL must NOT be reported as out of scope: 127.0.0.1 is allow-listed
    assert "kapsam" not in text.lower() or "authorized_hosts" not in text


def test_verifying_row_without_address_is_an_error(tmp_path: Path) -> None:
    sheet = _write(
        tmp_path / "missing_mail.xlsx",
        [{"target_url": "http://127.0.0.1:8090/register", "scenario": "email-verify", "requires_email_verification": "true"}],
    )
    issues = validator.validate_sheet(sheet)
    assert any(item.field == "verification_email" and item.severity == "error" for item in issues)


def test_unbalanced_selector_is_flagged(tmp_path: Path) -> None:
    sheet = _write(
        tmp_path / "broken_selector.xlsx",
        [{"target_url": "http://127.0.0.1:8090/register", "scenario": "form-submit", "success_selector": "div[data-x='1'"}],
    )
    issues = validator.validate_sheet(sheet)
    assert any("şüpheli selector" in item.message for item in issues)


def test_css_list_in_selector_is_only_a_warning(tmp_path: Path) -> None:
    """``.a, .b`` is valid CSS for Playwright - it must not be an error."""
    sheet = _write(
        tmp_path / "css_list.xlsx",
        [
            {
                "target_url": "http://127.0.0.1:8090/register",
                "scenario": "form-submit",
                "success_selector": ".success-message, .alert-success",
            }
        ],
    )
    issues = validator.validate_sheet(sheet)
    assert _errors(issues) == []
    assert any(item.severity == "warning" and "CSS listesi" in item.message for item in issues)


def test_unknown_placeholder_is_flagged(tmp_path: Path) -> None:
    sheet = _write(
        tmp_path / "placeholder.xlsx",
        [{"target_url": "http://127.0.0.1:8090/register", "scenario": "form-submit", "email": "{hesap}"}],
    )
    issues = validator.validate_sheet(sheet)
    assert any("bilinmeyen yer tutucu" in item.message for item in issues)


def test_balanced_submit_button_text_is_accepted(tmp_path: Path) -> None:
    sheet = _write(
        tmp_path / "ok.xlsx",
        [
            {
                "target_url": "http://127.0.0.1:8090/register",
                "scenario": "form-submit",
                "email": "{email}",
                "password": "{password}",
                "password_confirm": "{password}",
                "submit_button_text": "Hesap oluştur",
            }
        ],
    )
    issues = validator.validate_sheet(sheet)
    assert _errors(issues) == [], _messages(issues)
