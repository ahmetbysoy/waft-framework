"""Tests for the credential importer and the pool's strict validation.

Background: a credential file pasted out of a chat arrived as

    {"email": "[hesap1@gmail.com](mailto:hesap1@gmail.com)",
     "imap_host": "[imap.gmail.com](http://imap.gmail.com)"}

The old pool check was ``"@" in email``, so it accepted the value, the browser typed the whole
Markdown link into the sign-up form and IMAP dialled the bracketed string. The pool now refuses
such values and ``import_credentials.py`` repairs them. Both behaviours are pinned here.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "qa-kit" / "offerwall"
for extra in (ROOT, KIT, ROOT / "qa-kit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import account_pool as pool_module  # noqa: E402
import import_credentials as importer  # noqa: E402

PASTED = [
    {
        "email": "[hesap1@gmail.com](mailto:hesap1@gmail.com)",
        "password": "16_haneli_uygulama_sifresi_buraya",
        "imap_host": "[imap.gmail.com](http://imap.gmail.com)",
        "imap_port": 993,
    },
    {
        "email": "[hesap2@gmail.com](mailto:hesap2@gmail.com)",
        "password": "16_haneli_uygulama_sifresi_buraya",
        "imap_host": "[imap.gmail.com](http://imap.gmail.com)",
        "imap_port": 993,
    },
]


# --------------------------------------------------------------------------------------
# cleaning primitives
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[hesap1@gmail.com](mailto:hesap1@gmail.com)", "hesap1@gmail.com"),
        ("[imap.gmail.com](http://imap.gmail.com)", "imap.gmail.com"),
        ("<hesap1@gmail.com>", "hesap1@gmail.com"),
        ("mailto:hesap1@gmail.com", "hesap1@gmail.com"),
        ("`hesap1@gmail.com`", "hesap1@gmail.com"),
        ('"hesap1@gmail.com"', "hesap1@gmail.com"),
        ("hesap\\_1@gmail.com", "hesap_1@gmail.com"),
        ("hesap 1@gmail.com", "hesap1@gmail.com"),
        ("hesap1@gmail.com,", "hesap1@gmail.com"),
        ("", ""),
        (None, ""),
    ],
)
def test_clean_text_normalises_paste_artifacts(raw: object, expected: str) -> None:
    assert importer.clean_text(raw) == expected


def test_clean_port_variants() -> None:
    assert importer.clean_port("993") == (993, None)
    assert importer.clean_port(" 993 ") == (993, None)
    assert importer.clean_port(None) == (993, None)
    assert importer.clean_port(0)[1] is not None
    assert importer.clean_port(70000)[1] is not None
    assert importer.clean_port("doksan")[1] is not None


# --------------------------------------------------------------------------------------
# the pasted file that started this
# --------------------------------------------------------------------------------------
def test_pasted_file_is_repaired_but_placeholders_remain_a_problem() -> None:
    report = importer.import_pool(PASTED)
    assert [account.email for account in report.accounts] == ["hesap1@gmail.com", "hesap2@gmail.com"]
    assert all(account.imap_host == "imap.gmail.com" for account in report.accounts)
    assert report.repairs["hesap1@gmail.com"], "the repair must be reported, not silently applied"
    assert not report.ok, "placeholder passwords must block"
    assert any("yer tutucu" in issue for issues in report.problems.values() for issue in issues)


def test_allow_placeholders_turns_them_into_warnings() -> None:
    report = importer.import_pool(PASTED, allow_placeholders=True)
    assert report.ok
    assert any("yer tutucu" in warning for warning in report.warnings)


def test_pool_refuses_the_mangled_file_and_names_the_fix(tmp_path: Path) -> None:
    broken = tmp_path / "pasted.json"
    broken.write_text(json.dumps(PASTED), encoding="utf-8")
    loaded = pool_module.AccountPool.from_file(broken)  # loading is lazy: it does not validate
    with pytest.raises(pool_module.AccountPoolError) as excinfo:
        loaded.validate(require_enabled=True)
    message = str(excinfo.value)
    assert "pasted Markdown/URL" in message
    assert "import_credentials.py" in message


def test_pool_refuses_mangled_imap_host_alone() -> None:
    account = pool_module.Account(email="ok@example.com", password="secretpass", imap_host="[imap.gmail.com](http://x)")
    pool = pool_module.AccountPool(accounts=[account])
    with pytest.raises(pool_module.AccountPoolError, match="IMAP host"):
        pool.validate()


def test_pool_still_accepts_legitimate_pools() -> None:
    pool = pool_module.AccountPool.from_file(KIT / "credentials.sandbox.json")
    warnings = pool.validate(require_enabled=True)
    assert len(pool.accounts) == 10
    # the only warning here is the (expected) "no @gmail.com account" note for the local mailbox
    assert not any("placeholder" in warning for warning in warnings)
    assert not any("malformed" in warning or "Markdown" in warning for warning in warnings)


# --------------------------------------------------------------------------------------
# placeholder heuristics
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "password",
    ["", "sifre", "app_password", "AP_PASSWORD_1", "16_haneli_uygulama_sifresi_buraya", "<uygulama-sifresi>", "your_real_one"],
)
def test_placeholder_detection(password: str) -> None:
    assert pool_module.looks_like_placeholder(password) is True


@pytest.mark.parametrize("password", ["devmail", "abcd efgh ijkl mnop", "Xk7$mQ2p"])
def test_real_passwords_are_not_placeholders(password: str) -> None:
    assert pool_module.looks_like_placeholder(password) is False


# --------------------------------------------------------------------------------------
# structure handling
# --------------------------------------------------------------------------------------
def test_comment_entry_is_skipped_and_template_imports() -> None:
    payload = json.loads((KIT / "credentials.example.json").read_text(encoding="utf-8"))
    report = importer.import_pool(payload, allow_placeholders=True)
    assert len(report.accounts) == 10
    assert any("açıklama girdisi" in item for item in report.skipped)


def test_object_payload_and_duplicates(tmp_path: Path) -> None:
    payload = {"accounts": [{"email": "a@example.com", "password": "abcdefgh"}, {"email": "A@example.com", "password": "abcdefgh"}]}
    report = importer.import_pool(payload, allow_placeholders=True)
    assert [account.email for account in report.accounts] == ["a@example.com"]
    assert report.duplicates and "A@example.com" in report.duplicates[0]


def test_require_gmail_rejects_other_domains() -> None:
    report = importer.import_pool([{"email": "qa@demo.waft.local", "password": "devmail"}], require_gmail=True)
    assert not report.ok
    assert any("Gmail" in issue for issues in report.problems.values() for issue in issues)


def test_enabled_flag_is_coerced() -> None:
    report = importer.import_pool(
        [{"email": "a@example.com", "password": "abcdefgh", "enabled": "false"}], allow_placeholders=True
    )
    assert report.accounts[0].enabled is False


# --------------------------------------------------------------------------------------
# writing the repaired pool
# --------------------------------------------------------------------------------------
def test_written_pool_is_0600_and_reloadable(tmp_path: Path) -> None:
    target = tmp_path / "credentials.json"
    importer.write_pool(target, importer.import_pool(PASTED, allow_placeholders=True).accounts)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    reloaded = pool_module.AccountPool.from_file(target)
    assert [account.email for account in reloaded.accounts] == ["hesap1@gmail.com", "hesap2@gmail.com"]
    assert reloaded.for_context(0).imap_host == "imap.gmail.com"


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "pasted.json"
    source.write_text(json.dumps(PASTED), encoding="utf-8")
    target = tmp_path / "credentials.json"

    assert importer.main(["--in", str(source), "--out", str(target), "--check"]) == 1
    assert not target.exists(), "--check must never write"
    assert importer.main(["--in", str(source), "--out", str(target), "--allow-placeholders"]) == 0
    assert target.exists()
    assert importer.main(["--in", str(tmp_path / "yok.json"), "--check"]) == 2
    assert "hesap" in capsys.readouterr().out
