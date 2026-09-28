"""Unit tests: configuration, data sources, proxies, utilities, reporting (no browser)."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waft.config import Config, DEVICE_PROFILES, build_arg_parser, config_from_args, resolve_timezone
from waft.data_source import DataLoader, InlineRows, normalize_header
from waft.errors import ConfigError, DataSourceError, is_retryable
from waft.models import RunTotals
from waft.proxy_manager import ProxyPool, parse_proxy_line, parse_proxy_lines
from waft.utils import (
    Redactor,
    interpolate,
    interpolate_deep,
    mask_secret,
    parse_bool,
    parse_duration,
    parse_int,
    retry_async,
    slugify,
)


# --------------------------------------------------------------------------------------
# utils
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template, expected",
    [
        ("{run_id}", "run-1"),
        ("user+{digits:4}@example.com", None),  # digits are random but present
        ("{date:%Y}", datetime.now().strftime("%Y")),
        ("{unknown_key}", "{unknown_key}"),
        ("no placeholders", "no placeholders"),
    ],
)
def test_interpolate(template: str, expected):
    result = interpolate(template, {"run_id": "run-1"})
    if expected is None:
        assert result.startswith("user+") and result.endswith("@example.com")
        assert len(result) == len("user+") + 4 + len("@example.com")
    else:
        assert result == expected


def test_interpolate_deep_and_slugify():
    payload = {"url": "https://x.test/{context_id}", "nested": ["{row_index}"], "n": 5}
    resolved = interpolate_deep(payload, {"context_id": "ctx-01", "row_index": 3})
    assert resolved == {"url": "https://x.test/ctx-01", "nested": ["3"], "n": 5}
    assert slugify("Hedef Şifre Alanı / Test #1") == "hedef-sifre-alani-test-1"
    assert slugify("") == "unnamed"


def test_parsers():
    assert parse_bool("evet") is True
    assert parse_bool("HAYIR") is False
    assert parse_bool(None, default=True) is True
    assert parse_int("1_000") == 1000
    assert parse_int("1.9") == 1
    assert parse_duration("500ms") == 0.5
    assert parse_duration("2m") == 120.0
    assert parse_duration(3) == 3.0


def test_mask_secret_and_redactor():
    assert mask_secret("user:sup3rsecret@host:8080").startswith("use***")
    assert "sup3rsecret" not in mask_secret("user:sup3rsecret@host:8080")
    redactor = Redactor()
    assert "***" in redactor.header("Authorization", "Bearer abcdefghijklmnopqrstuvwxyz")
    scrubbed = redactor.text('{"password": "hunter2"} contact: a@b.com 4111111111111111')
    assert "hunter2" not in scrubbed
    assert "a@b.com" not in scrubbed
    assert "4111111111111111" not in scrubbed
    assert redactor.is_sensitive_field("user_password") is True
    assert redactor.is_sensitive_field("company") is False


def test_retry_async_retries_and_gives_up():
    attempts = {"count": 0}

    async def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("boom")
        return "ok"

    assert asyncio.run(retry_async(flaky, attempts=3, base_delay=0.01, jitter=0)) == "ok"
    assert attempts["count"] == 3

    async def always_fails():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        asyncio.run(retry_async(always_fails, attempts=2, base_delay=0.01, jitter=0))


def test_is_retryable_classification():
    from waft.errors import FieldResolutionError, NavigationError, TargetUnreachableError

    assert is_retryable(TargetUnreachableError("timeout")) is True
    assert is_retryable(NavigationError("boom")) is True
    assert is_retryable(FieldResolutionError("missing field")) is False
    assert is_retryable(ValueError("bad")) is False


# --------------------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------------------


def test_config_defaults_and_digest(tmp_path):
    config = Config(contexts=4, artifacts_dir=tmp_path)
    config.validate()
    assert config.resolved_concurrency >= 1
    assert len(config.config_digest["sha256"]) == 64
    assert config.device_profiles and len(config.device_profiles) == 4
    assert config.extra_headers.get("Accept-Language")


def test_config_rejects_invalid_values(tmp_path):
    with pytest.raises(ConfigError):
        Config(contexts=0, artifacts_dir=tmp_path).validate()
    with pytest.raises(ConfigError):
        Config(browser="netscape", artifacts_dir=tmp_path).validate()
    with pytest.raises(ConfigError):
        Config(devices_spec="ipad-air-1999", artifacts_dir=tmp_path).validate()
    with pytest.raises(ConfigError):
        Config(imap_enabled=True, artifacts_dir=tmp_path).validate()


def test_config_from_cli_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WAFT_CONTEXTS", "3")
    monkeypatch.setenv("WAFT_LOG_LEVEL", "DEBUG")
    args = build_arg_parser().parse_args(
        [
            "--data",
            str(tmp_path / "rows.json"),
            "--contexts",
            "7",
            "--proxy-mode",
            "off",
            "--devices",
            "chrome-win-1920x1080",
            "--no-humanize",
            "--artifacts",
            str(tmp_path / "art"),
        ]
    )
    (tmp_path / "rows.json").write_text('[{"target_url": "https://example.com"}]', encoding="utf-8")
    config = config_from_args(args)
    assert config.contexts == 7  # CLI beats env
    assert config.log_level == "DEBUG"  # env applied
    assert config.humanize is False
    assert config.device_profiles[0].name == "chrome-win-1920x1080"
    assert Path(config.artifacts_dir) == tmp_path / "art"


def test_config_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("WAFT_SCRENSHOT_TEST=1\nIMAP_HOST=imap.example.com\n", encoding="utf-8")
    data = tmp_path / "rows.json"
    data.write_text('[{"target_url": "https://example.com"}]', encoding="utf-8")
    config = Config.from_sources(build_arg_parser().parse_args(["--env-file", str(env_file), "--data", str(data)]))
    assert config.imap_host == "imap.example.com"


def test_resolve_timezone():
    assert resolve_timezone("TR") == "Europe/Istanbul"
    assert resolve_timezone(None, "DE") == "Europe/Berlin"
    assert resolve_timezone("America/Sao_Paulo") == "America/Sao_Paulo"
    assert resolve_timezone(None) == "Europe/Istanbul"


def test_device_profiles_are_coherent():
    for name, device in DEVICE_PROFILES.items():
        assert device.user_agent, name
        assert device.viewport["width"] > 0 and device.viewport["height"] > 0, name
        assert "@" not in device.user_agent, name
        if device.is_mobile:
            assert device.has_touch and device.max_touch_points > 0, name


# --------------------------------------------------------------------------------------
# data sources
# --------------------------------------------------------------------------------------


def test_normalize_header():
    assert normalize_header("Hedef URL") == "hedef_url"
    assert normalize_header("E-Posta") == "e_posta"
    assert normalize_header("First Name ") == "first_name"
    assert normalize_header("") == "column"


def test_inline_rows_build_target_rows(base_config):
    rows = InlineRows(
        [
            {
                "name": "job-1",
                "Hedef URL": "example.com/form",
                "E-Posta": "a@b.com",
                "first_name": "Ada",
                "terms": "evet",
                "iterations": 2,
                "expect_error": "true",
            }
        ]
    ).load(base_config)
    assert len(rows) == 1
    row = rows[0]
    assert row.target_url == "https://example.com/form"  # scheme added
    assert row.form_data["e_posta"] == "a@b.com"
    assert row.form_data["first_name"] == "Ada"
    assert row.form_data["terms"] == "evet"
    assert row.iterations == 2
    assert row.metadata["expect_error"] is True
    assert "e_posta" in row.form_data


def test_data_loader_json_file(tmp_path, base_config):
    payload = {
        "rows": [
            {"target_url": "https://a.test", "email": "x@y.z"},
            {"target_url": "https://b.test", "email": "q@w.z", "steps": [{"action": "goto", "value": "https://b.test"}]},
            {"target_url": "https://c.test", "active": "false"},
        ]
    }
    path = tmp_path / "rows.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    config = base_config
    config.data_file = path
    loader = DataLoader(config)
    rows = loader.load()
    assert len(rows) == 2  # inactive row dropped
    assert rows[1].steps and rows[1].steps[0].action == "goto"
    assert loader.report.expanded_rows == 2


def test_data_loader_multivalue_and_steps_shorthand(tmp_path, base_config):
    path = tmp_path / "rows.json"
    path.write_text(
        json.dumps(
            [
                {
                    "target_url": "https://x.test",
                    "email": "a@x.test|b@x.test",
                    "steps": "fill #email: {email} | click text=Kaydol | expect_url /welcome",
                }
            ]
        ),
        encoding="utf-8",
    )
    base_config.data_file = path
    loader = DataLoader(base_config)
    rows = loader.load()
    assert len(rows) == 2
    assert {row.form_data["email"] for row in rows} == {"a@x.test", "b@x.test"}
    actions = [step.action for step in rows[0].steps]
    assert actions == ["fill", "click", "expect_url"]
    assert rows[0].steps[0].target == "#email"


def test_data_loader_excel_roundtrip(tmp_path, base_config):
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        [
            {"name": "row-1", "target_url": "https://excel.test", "email": "excel@test.com", "terms": True, "iterations": 3},
            {"name": "row-2", "target_url": "https://excel2.test", "email": "second@test.com", "expect_error": "true"},
        ]
    )
    path = tmp_path / "targets.xlsx"
    frame.to_excel(path, index=False, sheet_name="targets")
    base_config.data_file = path
    rows = DataLoader(base_config).load()
    assert [row.target_url for row in rows] == ["https://excel.test", "https://excel2.test"]
    assert rows[0].form_data["email"] == "excel@test.com"
    assert rows[0].form_data["terms"] == "true"
    assert rows[0].iterations == 3
    assert rows[1].metadata["expect_error"] is True


def test_data_loader_missing_url_column(tmp_path, base_config):
    path = tmp_path / "rows.json"
    path.write_text(json.dumps([{"email": "a@b.com"}]), encoding="utf-8")
    base_config.data_file = path
    with pytest.raises(DataSourceError):
        DataLoader(base_config).load()


# --------------------------------------------------------------------------------------
# proxies
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, expected_server, expected_user, expected_pass",
    [
        ("1.2.3.4:8080", "http://1.2.3.4:8080", None, None),
        ("user:secret@1.2.3.4:8080", "http://1.2.3.4:8080", "user", "secret"),
        ("socks5://user:secret@1.2.3.4:1080", "socks5://1.2.3.4:1080", "user", "secret"),
        ("http://1.2.3.4:3128", "http://1.2.3.4:3128", None, None),
        ("1.2.3.4:8080:john:pw", "http://1.2.3.4:8080", "john", "pw"),
        ("1.2.3.4:8080 john pw", "http://1.2.3.4:8080", "john", "pw"),
    ],
)
def test_parse_proxy_line(line, expected_server, expected_user, expected_pass):
    spec = parse_proxy_line(line)
    assert spec is not None
    assert spec.server == expected_server
    assert spec.username == expected_user
    assert spec.password == expected_pass


def test_parse_proxy_lines_dedup_and_comments():
    specs = parse_proxy_lines(
        [
            "# comment",
            "",
            "1.2.3.4:8080",
            "1.2.3.4:8080",
            "  ",
            "user:pass@5.6.7.8:8080  # trailing comment",
            "garbage-line-without-port",
        ]
    )
    assert len(specs) == 2
    assert specs[0].label.endswith("01")
    assert specs[1].username == "user"


def test_proxy_spec_masking_and_playwright_payload():
    spec = parse_proxy_line("user:sup3rsecret@1.2.3.4:8080")
    assert spec is not None
    assert "sup3rsecret" not in spec.masked()
    assert spec.masked().startswith("http://use***")
    payload = spec.to_playwright()
    assert payload["server"] == "http://1.2.3.4:8080"
    assert payload["username"] == "user" and payload["password"] == "sup3rsecret"
    assert spec.key == "http://1.2.3.4:8080"


def test_proxy_pool_rotation_and_failure_bookkeeping(tmp_path, base_config, monkeypatch):
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text("1.1.1.1:8080\nuser:pass@2.2.2.2:8080\nsocks5://3.3.3.3:1080\n", encoding="utf-8")
    base_config.proxy_file = proxy_file
    base_config.proxy_mode = "auto"
    base_config.proxy_cycle = True
    base_config.proxy_health_check = False
    pool = ProxyPool(base_config)
    pool.load()
    assert len(pool) == 3

    async def acquire_all():
        first = await pool.acquire(context_id="ctx-01")
        second = await pool.acquire(context_id="ctx-01")  # sticky → same proxy
        third = await pool.acquire(context_id="ctx-02")
        return first, second, third

    first, second, third = asyncio.run(acquire_all())
    assert first is second
    assert third is not None and third.server != first.server
    pool.report_failure(first, "connection refused")
    assert first.failures == 1 and first.in_cooldown
    stats = pool.stats()
    assert stats["total"] == 3 and stats["failures"] == 1
    assert pool.rows()[0]["label"]


def test_proxy_pool_require_mode_without_proxies(tmp_path):
    config = Config(proxy_mode="require", artifacts_dir=tmp_path)
    with pytest.raises(Exception):
        ProxyPool(config).load()


# --------------------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------------------


def test_report_renderers(tmp_path):
    from waft.artifacts import ArtifactManager
    from waft.models import ContextResult, RunSummary, TargetRunResult
    from waft.reporting import Reporter, render_markdown, render_summary_table

    config = Config(artifacts_dir=tmp_path, junit_xml=True, csv_report=True)
    config.validate()
    artifacts = ArtifactManager(config, "run-test-0001")
    reporter = Reporter(config, artifacts, "run-test-0001")

    run_ok = TargetRunResult(context_id="ctx-00", row_index=1, target_url="https://a.test", status="ok", duration_ms=120.0)
    run_bad = TargetRunResult(
        context_id="ctx-00",
        row_index=2,
        target_url="https://b.test",
        status="failed",
        error="FormAutomationError: rejected",
        error_type="FormAutomationError",
        duration_ms=340.0,
        network_metrics={"requests": 4, "responses": 4, "bytes_in": 2048, "by_status": {"200": 3, "500": 1}},
    )
    context = ContextResult(context_id="ctx-00", index=0, runs=[run_ok, run_bad], duration_ms=460.0)
    summary = RunSummary(
        run_id="run-test-0001",
        started_at="2026-09-28T14:00:00+03:00",
        finished_at="2026-09-28T14:00:05+03:00",
        duration_ms=5000,
        artifacts_dir=str(artifacts.root),
        contexts=[context],
        totals=RunTotals(targets_run=2, targets_ok=1, targets_failed=1, contexts=1, contexts_ok=0, contexts_failed=1),
        failures=[{"context_id": "ctx-00", "row_index": 2, "url": "https://b.test", "error": "rejected"}],
        api_endpoints=["GET a.test/api/v1/users"],
        exit_code=1,
    )
    written = reporter.write_all(summary)
    assert "run.json" in written and Path(written["run.json"]).exists()
    assert Path(written["results.csv"]).exists()
    assert Path(written["junit.xml"]).exists()
    csv_text = Path(written["results.csv"]).read_text(encoding="utf-8-sig")
    assert "ctx-00" in csv_text and "rejected" in csv_text
    junit_text = Path(written["junit.xml"]).read_text(encoding="utf-8")
    assert "<testsuite" in junit_text and "FormAutomationError" in junit_text
    markdown = render_markdown(summary)
    assert "WAFT run report" in markdown and "❌ FAILED" in markdown
    assert "ctx-00" in render_summary_table(summary)
    reporter.print_summary(summary)  # must not raise
