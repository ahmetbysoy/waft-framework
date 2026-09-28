"""Contract tests for the offerwall kit — they encode the review findings as executable checks.

These tests exist because a hand-written wrapper (the "kendi wrapper taslağı") had **eight**
defects that all looked plausible in review: wrong module/attribute names, wrong ``Config`` field
names, a wrong ``Orchestrator`` signature, a masked-in-place password that was then used to fill
the form, a ``form_data`` dict truncated to six keys, verification fields set on every row, and an
``endpoints.json`` read from the wrong path/shape.

None of those are design opinions - they are contract facts about this repository, so they are
asserted here. No browser and no network are required.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "qa-kit" / "offerwall"
for extra in (ROOT, KIT, ROOT / "qa-kit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import run_offerwall as kit  # noqa: E402
import selector_resolver as resolver  # noqa: E402
from waft import Config  # noqa: E402
from waft.models import TargetRow  # noqa: E402
from waft.utils import mask_secret  # noqa: E402


# --------------------------------------------------------------------------------------
# 1. the names the draft got wrong really do not exist (guards against re-introducing them)
# --------------------------------------------------------------------------------------
def test_draft_module_attributes_do_not_exist() -> None:
    import waft.data_source as data_source
    import waft.reporting as reporting

    assert not hasattr(data_source, "load_data"), "draft used waft.data_source.load_data"
    assert not hasattr(reporting, "generate_summary"), "draft used waft.reporting.generate_summary"
    assert hasattr(data_source, "DataLoader")
    assert hasattr(reporting, "Reporter")


def test_loader_returns_typed_rows_not_a_dataframe(tmp_path: Path) -> None:
    """``DataLoader(config).load()`` yields ``list[TargetRow]`` - ``.iterrows()`` would fail."""
    sheet = KIT / "targets_offerwall.xlsx"
    config = Config(data_file=sheet, contexts=1, concurrency=1, dry_run=True)
    rows = kit.DataLoader(config).load()
    assert rows and all(isinstance(row, TargetRow) for row in rows)
    assert not hasattr(rows, "iterrows")


def test_orchestrator_takes_only_config() -> None:
    import inspect

    params = list(inspect.signature(kit.Orchestrator.__init__).parameters)
    assert params[:3] == ["self", "config"], f"Orchestrator signature changed: {params}"
    assert "targets" not in params, "rows come from config.data_file, not a second argument"


# --------------------------------------------------------------------------------------
# 2. Config field names (the draft's kwargs would raise TypeError)
# --------------------------------------------------------------------------------------
def test_draft_config_keywords_are_rejected() -> None:
    for bad_kwargs in (
        {"imap": True},
        {"imap_timeout": 180},
        {"trace": "on-failure"},
        {"artifacts": "artifacts"},
    ):
        with pytest.raises(TypeError):
            Config(**bad_kwargs)  # type: ignore[arg-type]


def test_real_config_field_names_are_accepted() -> None:
    names = {field.name for field in dataclasses.fields(Config)}
    assert {"imap_enabled", "imap_timeout_s", "trace_mode", "artifacts_dir"} <= names


# --------------------------------------------------------------------------------------
# 3. masking must never touch the value that fills the form
# --------------------------------------------------------------------------------------
def test_masking_is_display_only_and_never_mutates_the_secret() -> None:
    pool = kit.AccountPool.from_file(KIT / "credentials.sandbox.json")
    account = pool.for_context(0)
    real_password = account.password

    masked = mask_secret(real_password)
    assert masked != real_password
    assert "***" in masked
    # the pool object is untouched: this is exactly what the draft's in-place masking broke
    assert pool.for_context(0).password == real_password


def test_sandbox_pool_gives_each_context_a_distinct_account() -> None:
    pool = kit.AccountPool.from_file(KIT / "credentials.sandbox.json")
    emails = [pool.for_context(index).email for index in range(len(pool))]
    assert len(set(emails)) == len(emails) == 10


# --------------------------------------------------------------------------------------
# 4. binding: full form data preserved, verification only on email-verify rows
# --------------------------------------------------------------------------------------
def test_binding_preserves_every_form_field_and_scopes_verification() -> None:
    pool = kit.AccountPool.from_file(KIT / "credentials.sandbox.json")
    config = Config(data_file=KIT / "targets_offerwall.xlsx", contexts=1, concurrency=1, dry_run=True)
    templates = kit.DataLoader(config).load()

    bound = kit.bind_rows_to_account(templates, pool.for_context(0), iteration_offset=0)
    assert len(bound) == len(templates)

    register = next(row for row in bound if "email-verify" in (row.name or ""))
    assert register.form_data.get("password_confirm"), "required field dropped during binding"
    assert register.form_data.get("country") and "terms" in register.form_data
    assert "{email}" not in json.dumps(register.form_data), "placeholder left unresolved"
    assert register.verification_email == pool.for_context(0).email

    survey = next(row for row in bound if "survey" in (row.name or ""))
    assert not survey.requires_email_verification
    assert not survey.verification_email, "survey rows must not wait for a mail"


# --------------------------------------------------------------------------------------
# 5. endpoints.json: real path and real shape
# --------------------------------------------------------------------------------------
def test_endpoints_payload_shape_and_reader(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-test"
    run_dir.mkdir()
    payload = {
        "endpoints": [
            {
                "method": "POST",
                "host": "127.0.0.1:8090",
                "path": "/register",
                "statuses": [200],
                "count": 360,
            }
        ]
    }
    (run_dir / "endpoints.json").write_text(json.dumps(payload), encoding="utf-8")

    summary = kit.summarise_endpoints(tmp_path, run_id="run-test")
    assert summary["available"] is True
    assert summary["endpoint_count"] == 1
    assert summary["endpoints"][0]["statuses"] == [200]

    # a bare list (what the draft assumed) carries no "endpoints" key
    assert isinstance(payload, dict) and "endpoints" in payload


# --------------------------------------------------------------------------------------
# 6. the scope gate is enforced, not documented
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("host", "authorized"),
    [
        ("timewall.io", False),
        ("timewall.io", True),
        ("sub.timewall.io", True),
        ("jumptask.io", True),
        ("example.com", False),
    ],
)
def test_scope_gate_refuses(host: str, authorized: bool) -> None:
    row = TargetRow(index=0, target_url=f"https://{host}/register", form_data={}, raw={})
    scope = kit.load_scope(ROOT / "qa-kit" / "authorized_hosts.txt", [])
    with pytest.raises(kit.ScopeViolation):
        kit.enforce_scope([row], scope, i_am_authorized=authorized)


def test_scope_gate_allows_sandbox_and_own_staging() -> None:
    rows = [
        TargetRow(index=0, target_url="http://127.0.0.1:8090/register", form_data={}, raw={}),
        TargetRow(index=1, target_url="http://localhost:8091/register", form_data={}, raw={}),
        TargetRow(index=2, target_url="http://demo.waft.local/register", form_data={}, raw={}),
    ]
    scope = kit.load_scope(ROOT / "qa-kit" / "authorized_hosts.txt", [])
    blocked, unauthorized = kit.enforce_scope(rows, scope, i_am_authorized=False)
    assert blocked == [] and unauthorized == []


def test_scope_host_candidates_normalises_port_and_ipv6() -> None:
    """The bug that would have broken the documented one-command sandbox run."""
    assert kit.scope_host_candidates("http://127.0.0.1:8090/register") == ["127.0.0.1:8090", "127.0.0.1"]
    assert kit.scope_host_candidates("http://[::1]:8090/x") == ["[::1]:8090", "::1"]
    assert kit.scope_host_candidates("https://staging.example.com/x") == ["staging.example.com"]


# --------------------------------------------------------------------------------------
# 7. the driver under test builds a valid Config
# --------------------------------------------------------------------------------------
def test_min_driver_builds_valid_config() -> None:
    import run_offerwall_min as driver

    args = driver.parse_args([])  # sandbox defaults
    pool = kit.AccountPool.from_file(args.credentials)
    config = driver.build_config(args, pool)
    assert config.capture_har is True and config.log_network is True
    assert config.captcha_action == "skip" and config.stealth is True
    assert config.trace_mode == "on-failure" and config.artifacts_dir == args.artifacts
    assert config.contexts == 10 and config.concurrency == 5


# --------------------------------------------------------------------------------------
# 8. resolver canonicalisation: specificity must survive (measured wrong-element bug)
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("canonical", "candidate", "expected"),
    [
        # canonical is already specific -> untouched
        ("#register-email", "input[name='email']", "#register-email"),
        # canonical degenerated to a bare tag, candidate keeps the specificity -> candidate wins
        ("iframe", "iframe[src*='recaptcha']", "iframe[src*='recaptcha']"),
        ("button", "button[data-testid='register-submit']", "button[data-testid='register-submit']"),
        # candidate is XPath: nothing WAFT-consumable survives -> stay bare (caller rejects it)
        ("input", "(//input[@type='password'])[2]", "input"),
        ("iframe", "//iframe[@title='captcha']", "iframe"),
        # both bare -> nothing to prefer
        ("button", "button", "button"),
    ],
)
def test_prefer_specific_keeps_specificity(canonical: str, candidate: str, expected: str) -> None:
    assert resolver.prefer_specific(canonical, candidate) == expected


def test_bare_tag_and_xpath_detection() -> None:
    assert resolver.BARE_TAG_RE.match("iframe") and resolver.BARE_TAG_RE.match("button")
    assert not resolver.BARE_TAG_RE.match("iframe[src*='recaptcha']")
    assert resolver.X_PATH_RE.match("(//input[@type='password'])[2]")
    assert resolver.X_PATH_RE.match("//iframe[@title='captcha']")
    assert not resolver.X_PATH_RE.match("input[name='email']")


def test_resolver_identity_check_is_wired_in() -> None:
    """The fix must be *in* probe_chain, not merely available as a helper."""
    import inspect

    source = inspect.getsource(resolver.probe_chain)
    assert "MARK_JS" in source and "HAS_MARK_JS" in source and "UNMARK_JS" in source
    assert "points at a different element" in source
    assert "prefer_specific" in source


def test_user_draft_catalogue_loads_and_maps_kit_keys() -> None:
    """The pasted catalogue was translated to kit keys without changing the chains."""
    catalogue = resolver.Catalogue.load(KIT / "selectors_user_draft.json")
    chains = catalogue.chains_for("127.0.0.1:8090", "127.0.0.1")
    assert set(chains) == {"email", "password", "password_confirm", "first_name", "last_name", "submit", "captcha"}
    assert chains["submit"][0] == "button[type='submit']"
    assert chains["captcha"][0] == "iframe[src*='recaptcha']"

    # the gap this catalogue has on the bundled sandbox: no country/terms/success/error keys
    kit_chains = resolver.Catalogue.load(KIT / "selectors_offerwall.json").chains_for("127.0.0.1:8090", "127.0.0.1")
    assert {"country", "terms", "success", "error"} <= set(kit_chains)
    assert not ({"country", "terms", "success", "error"} & set(chains))
