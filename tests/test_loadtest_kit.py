"""Unit tests for the multi-context load-test kit (``qa-kit/loadtest``).

These tests never launch a browser: they pin the contract of the four artefacts - the
credential pool, the target schema, the selector catalogue and the driver's pure helpers - so a
regression in validation logic fails here instead of 20 minutes into a staging run.

The driver is imported from its file path because it is a script (``run_load_test.py``), not a
package module; importing it executes only module-level definitions (its ``main()`` is guarded).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOADTEST_DIR = ROOT / "qa-kit" / "loadtest"


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("loadtest_runner", LOADTEST_DIR / "run_load_test.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["loadtest_runner"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def lt() -> Any:
    return _load_runner()


def _write_accounts(tmp_path: Path, accounts: list[dict[str, Any]]) -> Path:
    path = tmp_path / "pool.json"
    path.write_text(json.dumps(accounts), encoding="utf-8")
    return path


def _write_targets(tmp_path: Path, targets: Any) -> Path:
    path = tmp_path / "targets.json"
    payload = targets if isinstance(targets, dict) else {"targets": targets}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --------------------------------------------------------------------------------- load: pool
def test_credentials_pool_repairs_gmail_grouping_and_infers_imap_host(lt: Any, tmp_path: Path) -> None:
    path = _write_accounts(
        tmp_path,
        [
            {
                "email": "qa-user@gmail.com",
                # Gmail shows app passwords as four groups of four; people paste them verbatim.
                "app_password": "abcd efgh ijkl mnop",
                "imap_port": 993,
            }
        ],
    )
    accounts, warnings = lt.load_credentials_pool(path)
    assert len(accounts) == 1
    assert accounts[0].app_password == "abcdefghijklmnop"
    assert accounts[0].imap_host == "imap.gmail.com"
    assert accounts[0].ssl_flag(False) is True
    assert any("inferred" in warning for warning in warnings)


def test_credentials_pool_repairs_markdown_paste(lt: Any, tmp_path: Path) -> None:
    path = _write_accounts(
        tmp_path,
        [{"email": "qa@ornek-domain.com", "app_password": '"abcd efgh ijkl mnop"', "imap_host": "imap.ornek-domain.com"}],
    )
    accounts, _ = lt.load_credentials_pool(path)
    assert accounts[0].app_password == "abcdefghijklmnop"


def test_credentials_pool_does_not_guess_unknown_shape(lt: Any, tmp_path: Path) -> None:
    """Anything that is not the 4-4-4-4 Gmail shape is left alone (repair, never invent)."""
    path = _write_accounts(
        tmp_path,
        [{"email": "qa@ornek-domain.com", "app_password": "abc-defghijkl-mnop", "imap_host": "imap.ornek-domain.com"}],
    )
    accounts, _ = lt.load_credentials_pool(path)
    assert accounts[0].app_password == "abc-defghijkl-mnop"


def test_credentials_pool_refuses_template_placeholders(lt: Any, tmp_path: Path) -> None:
    path = _write_accounts(
        tmp_path,
        [{"email": "qa@ornek-domain.com", "app_password": "16_HANELI_UYGULAMA_SIFRESI_BURAYA_01", "imap_host": "imap.ornek-domain.com"}],
    )
    with pytest.raises(lt.LoadTestError, match="template app password"):
        lt.load_credentials_pool(path)
    accounts, warnings = lt.load_credentials_pool(path, allow_placeholders=True)
    assert len(accounts) == 1 and accounts[0].placeholder_password is True
    assert any("template app password" in warning for warning in warnings)


def test_credentials_pool_rejects_invalid_email_duplicates_and_bad_port(lt: Any, tmp_path: Path) -> None:
    with pytest.raises(lt.LoadTestError, match="invalid e-mail"):
        lt.load_credentials_pool(_write_accounts(tmp_path, [{"email": "not-an-email", "app_password": "x" * 16}]))

    duplicates = _write_accounts(
        tmp_path,
        [
            {"email": "a@ornek-domain.com", "app_password": "abcdefghijklmnop", "imap_host": "imap.ornek-domain.com"},
            {"email": "A@ornek-domain.com", "app_password": "abcdefghijklmnop", "imap_host": "imap.ornek-domain.com"},
        ],
    )
    accounts, warnings = lt.load_credentials_pool(duplicates)
    assert len(accounts) == 1
    assert any("duplicate" in warning for warning in warnings)

    with pytest.raises(lt.LoadTestError, match="out-of-range"):
        lt.load_credentials_pool(
            _write_accounts(tmp_path, [{"email": "a@ornek-domain.com", "app_password": "abcdefghijklmnop", "imap_host": "imap.ornek-domain.com", "imap_port": 70000}])
        )


def test_documentation_only_entries_are_skipped(lt: Any, tmp_path: Path) -> None:
    path = _write_accounts(
        tmp_path,
        [
            {"_comment": ["bu bir açıklama bloğu"]},
            {"email": "a@ornek-domain.com", "app_password": "abcdefghijklmnop", "imap_host": "imap.ornek-domain.com"},
        ],
    )
    accounts, _ = lt.load_credentials_pool(path)
    assert [account.email for account in accounts] == ["a@ornek-domain.com"]


def test_shipped_sandbox_pool_is_valid(lt: Any) -> None:
    accounts, _ = lt.load_credentials_pool(LOADTEST_DIR / "credentials_pool.sandbox.json")
    assert len(accounts) == 10
    assert {account.imap_port for account in accounts} == {1430}
    assert all(account.ssl_flag(True) is False for account in accounts)


# ------------------------------------------------------------------------- load: account binding
def test_assign_account_is_unique_per_context(lt: Any, tmp_path: Path) -> None:
    path = _write_accounts(
        tmp_path,
        [
            {"email": f"qa{i:02d}@ornek-domain.com", "app_password": "abcdefghijklmnop", "imap_host": "imap.ornek-domain.com"}
            for i in range(3)
        ],
    )
    accounts, _ = lt.load_credentials_pool(path)
    assert [lt.assign_account(accounts, index).email for index in range(3)] == [account.email for account in accounts]
    with pytest.raises(lt.LoadTestError, match="no dedicated account"):
        lt.assign_account(accounts, 3)
    reused = lt.assign_account(accounts, 3, allow_reuse=True)
    assert reused is accounts[0]


def test_resolve_imap_ssl_precedence(lt: Any) -> None:
    gmail = lt.TestAccount(index=0, email="a@gmail.com", app_password="x" * 16, imap_host="imap.gmail.com", imap_port=993)
    sandbox = lt.TestAccount(index=0, email="a@demo.waft.local", app_password="devmail", imap_host="127.0.0.1", imap_port=1430, imap_ssl=False)
    assert lt.resolve_imap_ssl("auto", gmail) is True
    assert lt.resolve_imap_ssl("off", gmail) is False
    assert lt.resolve_imap_ssl("on", sandbox) is False  # the account's own value wins
    assert lt.resolve_imap_ssl("auto", lt.TestAccount(index=0, email="a@x.com", app_password="x", imap_host="mail.x.com", imap_port=143)) is False


# ---------------------------------------------------------------------- load: targets + selector
def test_target_url_placeholder_requires_environment(lt: Any, tmp_path: Path) -> None:
    path = _write_targets(tmp_path, [{"name": "kayit", "target_url": "{TARGET_URL}/register", "scenario": "email-verify"}])
    with pytest.raises(lt.LoadTestError, match="TARGET_URL"):
        lt.load_targets(path, target_url=None)
    specs, _ = lt.load_targets(path, target_url="https://staging.sirketiniz.com/")
    assert specs[0].target_url == "https://staging.sirketiniz.com/register"


def test_verification_only_honoured_for_email_verify(lt: Any, tmp_path: Path) -> None:
    path = _write_targets(
        tmp_path,
        [
            {"name": "a", "target_url": "https://s.example-corp.com/register", "scenario": "auto", "requires_email_verification": True},
            {"name": "b", "target_url": "https://s.example-corp.com/register", "scenario": "email-verify", "requires_email_verification": True},
        ],
    )
    specs, warnings = lt.load_targets(path, target_url=None)
    assert specs[0].requires_email_verification is False
    assert specs[1].requires_email_verification is True
    assert any("only honoured" in warning for warning in warnings)


def test_target_placeholders_are_resolved_per_context(lt: Any, tmp_path: Path) -> None:
    path = _write_targets(tmp_path, [{"name": "a", "target_url": "https://s.example-corp.com/{ctx}", "form_fields": {"email": "{email}", "password": "{password}", "note": "{run}"}}])
    specs, _ = lt.load_targets(path, target_url=None)
    account = lt.TestAccount(index=0, email="qa01@ornek-domain.com", app_password="secret16chars", imap_host="imap.ornek-domain.com", imap_port=993)
    assert specs[0].context_url(context_index=3, run_id="run-1") == "https://s.example-corp.com/3"
    fields = specs[0].context_fields(account, context_index=3, run_id="run-1")
    assert fields == {"email": "qa01@ornek-domain.com", "password": "secret16chars", "note": "run-1"}


def test_selector_catalogue_kinds_and_fallbacks(lt: Any) -> None:
    catalogue, warnings = lt.load_selector_catalogue(LOADTEST_DIR / "selectors.json")
    assert len(catalogue.chain("submit_button")) >= 3
    for key in ("email_input", "password_input", "submit_button", "captcha_frame"):
        assert key in catalogue.chains, key
    assert not [warning for warning in warnings if "only 1 fallback" in warning]
    assert catalogue.resolve_field_key("email") == "email_input"
    assert catalogue.resolve_field_key("terms") == "terms_checkbox"
    assert catalogue.resolve_field_key("country") == "country_select"
    assert catalogue.resolve_field_key("nope") is None
    assert catalogue.kind_of("terms_checkbox") == "check"
    assert catalogue.kind_of("country_select") == "select"
    assert catalogue.kind_of("submit_button") == "click"
    assert catalogue.kind_of("success") == "probe"
    assert catalogue.kind_of("first_name_input") == "fill"


def test_selector_catalogue_warns_on_thin_chains(lt: Any, tmp_path: Path) -> None:
    path = tmp_path / "selectors.json"
    path.write_text(json.dumps({"email_input": ["#email", "#mail"], "captcha_frame": ["#c1", "#c2", "#c3"]}), encoding="utf-8")
    catalogue, warnings = lt.load_selector_catalogue(path)
    assert catalogue.chain("email_input") == ("#email", "#mail")
    assert any("at least 3" in warning for warning in warnings)


# ---------------------------------------------------------------------------- pure helpers
def test_parse_proxy_accepts_common_formats(lt: Any) -> None:
    assert lt.parse_proxy("http://user:pass@10.0.0.1:8080") == {
        "server": "http://10.0.0.1:8080",
        "username": "user",
        "password": "pass",
    }
    assert lt.parse_proxy("user:pass@10.0.0.1:8080")["username"] == "user"
    assert lt.parse_proxy("10.0.0.1:8080") == {"server": "http://10.0.0.1:8080"}
    assert lt.parse_proxy("10.0.0.1:8080:user:pass") == {
        "server": "http://10.0.0.1:8080",
        "username": "user",
        "password": "pass",
    }
    assert lt.parse_proxy("socks5://10.0.0.1:1080") == {"server": "socks5://10.0.0.1:1080"}
    assert lt.parse_proxy("# yorum") is None
    assert lt.parse_proxy("bozuk-satir") is None


def test_redact_url_masks_sensitive_query_values(lt: Any) -> None:
    url = "http://127.0.0.1:8090/verify?token=s3cr3t&email=qa%40x.com&code=424242"
    redacted = lt.redact_url(url)
    assert "token=***" in redacted and "code=***" in redacted
    assert "s3cr3t" not in redacted and "424242" not in redacted
    assert "email=qa%40x.com" in redacted  # non-sensitive values stay readable


def test_enforce_scope_blocks_third_party_and_unlisted_hosts(lt: Any) -> None:
    scope = lt.Scope(patterns=("127.0.0.1", "localhost", "*.waft.local", "staging.sirketiniz.com"))
    blocked, unauthorized = lt.enforce_scope(["http://127.0.0.1:8090/register"], scope, i_am_authorized=False)
    assert (blocked, unauthorized) == ([], [])

    with pytest.raises(lt.LoadTestError, match="out of scope"):
        lt.enforce_scope(["https://timewall.io/register"], scope, i_am_authorized=True)

    with pytest.raises(lt.LoadTestError, match="not covered by the scope file"):
        lt.enforce_scope(["https://staging.example-corp.com/register"], scope, i_am_authorized=False)

    _, unauthorized = lt.enforce_scope(["https://staging.example-corp.com/register"], scope, i_am_authorized=True)
    assert unauthorized == ["staging.example-corp.com"]


def test_network_recorder_endpoint_summary(lt: Any, tmp_path: Path) -> None:
    recorder = lt.NetworkRecorder(run_dir=tmp_path)
    recorder.events = [
        {"type": "response", "method": "POST", "path": "/register", "status": 200, "duration_ms": 10.0},
        {"type": "response", "method": "POST", "path": "/register", "status": 422, "duration_ms": 20.0},
        {"type": "requestfailed", "method": "GET", "path": "/api/v1/app-config", "status": None, "duration_ms": None},
    ]
    summary = recorder.endpoint_summary()
    register = next(item for item in summary["endpoints"] if item["path"] == "/register")
    assert register["requests"] == 2
    assert register["statuses"] == {"200": 1, "422": 1}
    assert register["duration_ms"]["p50"] == 20.0
    assert summary["totals"] == {"events": 3, "responses": 2, "failed_requests": 1, "statuses": {"200": 1, "422": 1}}


def test_shipped_sandbox_targets_are_valid(lt: Any) -> None:
    specs, warnings = lt.load_targets(LOADTEST_DIR / "test_targets.sandbox.json", target_url="http://127.0.0.1:8090")
    assert {spec.name for spec in specs} == {"kayit-formu", "captcha-drill"}
    assert specs[0].requires_email_verification is True
    assert specs[0].form_fields["email"] == "{email}"
    assert specs[1].scenario == "captcha-drill"
    assert not warnings


def test_cli_defaults_point_at_the_kit_files(lt: Any) -> None:
    args = lt.build_parser().parse_args([])
    assert args.credentials == LOADTEST_DIR / "credentials_pool.json"
    assert args.targets == LOADTEST_DIR / "test_targets.json"
    assert args.selectors == LOADTEST_DIR / "selectors.json"
    assert args.proxy_mode == "off" and args.fail_on_captcha is False
    sandbox = lt.apply_sandbox_defaults(lt.build_parser().parse_args(["--sandbox"]))
    assert sandbox.credentials.name == "credentials_pool.sandbox.json"
    assert sandbox.targets.name == "test_targets.sandbox.json"
    assert sandbox.imap_ssl == "off"

@pytest.mark.skipif(
    _load_runner().Config is None,
    reason="waft yüklü değil (tarayıcı katmanı): LoadTestRunner Config gerektirir",
)
def test_context_count_never_exceeds_accounts_or_the_flag(lt: Any) -> None:
    """`--contexts 2` must really mean two contexts (regression: it was silently ignored)."""
    from argparse import Namespace

    accounts = [
        lt.TestAccount(index=i, email=f"qa{i}@ornek-domain.com", app_password="x" * 16, imap_host="imap.ornek-domain.com", imap_port=993)
        for i in range(10)
    ]

    def runner(contexts: int) -> Any:
        return lt.LoadTestRunner(
            accounts=accounts,
            targets=[],
            catalogue=lt.SelectorCatalogue(chains={"email_input": ("#a", "#b", "#c")}),
            config=lt.Config(contexts=10),
            args=Namespace(contexts=contexts, allow_account_reuse=False),
            proxies=[],
            run_id="run-test",
            run_dir=Path("/tmp/waft-loadtest-unit"),
        )

    assert runner(2).planned_context_count() == 2
    assert runner(10).planned_context_count() == 10
    assert runner(25).planned_context_count() == 10  # never more contexts than accounts
    assert runner(0).planned_context_count() == 1    # defensive floor
