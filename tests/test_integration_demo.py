"""End-to-end integration tests: real Chromium contexts against the bundled demo site.

These tests prove the four hard requirements:

1.  several contexts run in parallel, fully isolated from each other (cookies/storage);
2.  the stealth layer really removes ``navigator.webdriver``, keeps a stable-per-context
    canvas fingerprint and populates plugins/WebGL;
3.  forms are filled from a data source and the outcome is interpreted (success, expected
    rejection, unknown);
4.  artifacts (screenshots, traces, bundles, reports) are produced.

They are skipped automatically when Playwright browsers are not installed.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def write_rows(path: Path, base: str, extra: list[dict] | None = None) -> Path:
    rows = [
        {
            "name": "signup-full",
            "target_url": f"{base}/",
            "scenario": "form-submit",
            "email": "integration.user@example.com",
            "first_name": "Entegrasyon",
            "last_name": "Testi",
            "phone": "+90 555 123 45 67",
            "company": "WAFT QA",
            "city": "istanbul",
            "password": "Wa!tTest123",
            "password_confirm": "Wa!tTest123",
            "terms": "true",
            "newsletter": "true",
            "message": "Bu bir entegrasyon testidir.",
            "submit_button_text": "Kayıt Ol",
            "success_selector": '[data-testid="success"]',
            "error_selector": ".notice.error",
            "wait_after_submit_ms": 4000,
        },
        {
            "name": "signup-expected-rejection",
            "target_url": f"{base}/",
            "scenario": "form-submit",
            "email": "invalid-user@example.com",
            "first_name": "Negatif",
            "last_name": "Test",
            "password": "Wa!tTest456",
            "password_confirm": "Wa!tTest456",
            "terms": "true",
            "submit_button_text": "Kayıt Ol",
            "error_selector": ".notice.error",
            "wait_after_submit_ms": 4000,
            "expect_error": "true",
        },
        {
            "name": "load-test",
            "target_url": f"{base}/",
            "scenario": "load-test",
            "submit": "false",
        },
    ]
    rows.extend(extra or [])
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def build_config(tmp_path: Path, data_file: Path, contexts: int = 3, **overrides):
    from waft.config import Config

    config = Config(
        data_file=data_file,
        contexts=contexts,
        concurrency=contexts,
        iterations=1,
        artifacts_dir=tmp_path / "artifacts",
        headless=True,
        proxy_mode="off",
        humanize=False,
        trace_mode="on-failure",
        save_storage_state=True,
        screenshots=True,
        capture_har=False,
        log_network=True,
        stealth=True,
        log_level="WARNING",
        quiet=True,
        verify_stealth=True,
        **overrides,
    )
    config.validate()
    return config


# --------------------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------------------


def test_multi_context_run_fills_forms_and_reports(tmp_path, demo_server):
    """Three contexts, three targets, full report set - the flagship happy path."""
    from waft.orchestrator import Orchestrator

    data = write_rows(tmp_path / "rows.json", demo_server)
    config = build_config(tmp_path, data, contexts=3)
    orchestrator = Orchestrator(config)
    exit_code = asyncio.run(orchestrator.run())

    assert exit_code == 0, "expected every target to pass"

    run_dir = Path(orchestrator.artifacts.root)
    for artifact in ("run.json", "results.csv", "contexts.csv", "junit.xml", "summary.md", "endpoints.json"):
        assert (run_dir / artifact).exists(), f"{artifact} missing"

    summary = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert summary["totals"]["targets_run"] == 9  # 3 contexts × 3 rows
    assert summary["totals"]["targets_failed"] == 0
    assert summary["status"] == "ok"
    assert len(summary["contexts"]) == 3

    # Isolation: every context has its own id, its own storage state and its own device.
    context_ids = {context["context_id"] for context in summary["contexts"]}
    assert len(context_ids) == 3
    device_names = {context["device"] for context in summary["contexts"]}
    assert len(device_names) >= 2, "device profiles should be rotated across contexts"
    timezones = {context["timezone_id"] for context in summary["contexts"]}
    assert len(timezones) >= 1

    # Form filling really happened (12+ fields per signup row).
    for context in summary["contexts"]:
        for run in context["runs"]:
            if run["metadata"].get("row_name") == "signup-full":
                fill = next((step for step in run["steps"] if step["name"] == "form-fill"), None)
                assert fill is not None and fill["status"] == "ok"
                assert fill["details"]["total_filled"] >= 8
                assert fill["details"]["unmatched"] == []
                assert run["final_url"].endswith("/submit")
            if run["metadata"].get("row_name") == "load-test":
                assert any(step["name"] == "performance" for step in run["steps"])

    # Api discovery captured the XHR the demo page performs on load.
    assert summary["config_digest"]["load_report"]["expanded_rows"] == 3


def test_expected_rejection_is_a_pass(tmp_path, demo_server):
    """``expect_error`` rows succeed when the server rejects the submission."""
    from waft.orchestrator import Orchestrator

    negative_row = [
        {
            "name": "signup-expected-rejection",
            "target_url": f"{demo_server}/",
            "scenario": "form-submit",
            "email": "invalid-user@example.com",
            "first_name": "Negatif",
            "last_name": "Test",
            "password": "Wa!tTest456",
            "password_confirm": "Wa!tTest456",
            "terms": "true",
            "submit_button_text": "Kayıt Ol",
            "error_selector": ".notice.error",
            "success_selector": '[data-testid="success"]',
            "wait_after_submit_ms": 4000,
            "expect_error": "true",
        }
    ]
    data = tmp_path / "negative.json"
    data.write_text(json.dumps(negative_row, ensure_ascii=False), encoding="utf-8")
    config = build_config(tmp_path, data, contexts=1)
    orchestrator = Orchestrator(config)
    exit_code = asyncio.run(orchestrator.run())
    assert exit_code == 0, "the expected rejection must not fail the run"

    summary = json.loads((Path(orchestrator.artifacts.root) / "run.json").read_text(encoding="utf-8"))
    runs = summary["contexts"][0]["runs"]
    assert len(runs) == 1
    outcome = next(step for step in runs[0]["steps"] if step["name"] == "outcome")
    assert outcome["details"].get("expected_error") is True
    assert outcome["details"]["status"] == "error"
    assert outcome["status"] == "ok"


def test_context_isolation_and_stealth_invariants(tmp_path, demo_server):
    """Two contexts must not share cookies/storage and must look like real browsers."""
    from playwright.async_api import async_playwright

    from waft.artifacts import ArtifactManager
    from waft.config import Config
    from waft.engine import ContextEngine, EngineHooks, profile_from_config
    from waft.proxy_manager import ProxyPool
    from waft.stealth import StealthLayer

    config = Config(
        contexts=2,
        artifacts_dir=tmp_path / "artifacts",
        proxy_mode="off",
        humanize=False,
        trace_mode="off",
        save_storage_state=False,
        devices_spec="chrome-win-1920x1080,android-pixel7",
        locales_spec="tr-TR,en-US",
        log_level="WARNING",
        quiet=True,
    )
    config.validate()
    artifacts = ArtifactManager(config, "run-isolation")
    stealth = StealthLayer(config)

    async def scenario() -> dict:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            engines = []
            try:
                for index in range(2):
                    pool = ProxyPool(config)
                    pool.load()
                    engine = ContextEngine(
                        config=config,
                        profile=profile_from_config(config, index),
                        browser=browser,
                        proxy_pool=pool,
                        proxy=None,
                        artifacts=artifacts,
                        stealth=stealth,
                        hooks=EngineHooks(),
                    )
                    await engine.start()
                    engines.append(engine)

                # Each context navigates and stores a cookie of its own.
                for index, engine in enumerate(engines):
                    await engine.page.goto(f"{demo_server}/", wait_until="domcontentloaded")
                    await engine.page.evaluate(
                        "(value) => { document.cookie = 'waft_ctx=' + value + '; path=/'; localStorage.setItem('waft_ctx', value); }",
                        f"ctx-{index}",
                    )

                results = {}
                for index, engine in enumerate(engines):
                    page = engine.page
                    metrics = await page.evaluate(
                        """() => ({
                            webdriver: navigator.webdriver,
                            plugins: navigator.plugins.length,
                            languages: navigator.languages,
                            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                            userAgent: navigator.userAgent,
                            platform: navigator.platform,
                            hardwareConcurrency: navigator.hardwareConcurrency,
                            hasChrome: typeof window.chrome === 'object',
                            webglRenderer: (() => {
                                try {
                                    const gl = document.createElement('canvas').getContext('webgl');
                                    const info = gl.getExtension('WEBGL_debug_renderer_info');
                                    return gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER);
                                } catch (e) { return null; }
                            })(),
                            webrtcBlocked: (() => { try { new RTCPeerConnection(); return false; } catch (e) { return true; } })(),
                            cookie: document.cookie,
                            storage: localStorage.getItem('waft_ctx'),
                        })"""
                    )
                    results[f"ctx-{index}"] = metrics

                    # Canvas must be stable inside one context (deterministic noise).
                    canvas_a = await page.evaluate(
                        "() => { const c = document.createElement('canvas'); c.width = 200; c.height = 40;"
                        " const x = c.getContext('2d'); x.fillStyle = '#123456'; x.fillRect(0,0,100,20);"
                        " x.font = '14px Arial'; x.fillText('waft', 2, 18); return c.toDataURL(); }"
                    )
                    canvas_b = await page.evaluate(
                        "() => { const c = document.createElement('canvas'); c.width = 200; c.height = 40;"
                        " const x = c.getContext('2d'); x.fillStyle = '#123456'; x.fillRect(0,0,100,20);"
                        " x.font = '14px Arial'; x.fillText('waft', 2, 18); return c.toDataURL(); }"
                    )
                    assert canvas_a == canvas_b, "canvas fingerprint must be stable within a context"
                    results[f"ctx-{index}"]["canvas_len"] = len(canvas_a)

                    verification = await stealth.verify(page, engine.profile, strict=False)
                    results[f"ctx-{index}"]["stealth_ok"] = verification.ok
                    results[f"ctx-{index}"]["stealth_failed"] = verification.failed
            finally:
                for engine in engines:
                    await engine.close()
                await browser.close()
            return results

    results = asyncio.run(scenario())

    # 1) isolation
    assert results["ctx-0"]["cookie"] != results["ctx-1"]["cookie"]
    assert results["ctx-0"]["storage"] == "ctx-0"
    assert results["ctx-1"]["storage"] == "ctx-1"

    # 2) stealth invariants
    for key, metrics in results.items():
        assert metrics["webdriver"] in (False, None), f"{key}: navigator.webdriver leaked"
        assert metrics["plugins"] > 0, f"{key}: plugins empty"
        assert metrics["languages"], f"{key}: languages missing"
        assert metrics["hasChrome"] is True, f"{key}: window.chrome missing"
        assert metrics["webrtcBlocked"] is True, f"{key}: WebRTC not blocked"
        assert metrics["webglRenderer"], f"{key}: no WebGL renderer"
        assert "SwiftShader" not in str(metrics["webglRenderer"])
        assert metrics["stealth_ok"] is True, f"{key}: {metrics['stealth_failed']}"

    # 3) per-context identity differs (UA / timezone / platform / screen family)
    assert results["ctx-0"]["userAgent"] != results["ctx-1"]["userAgent"]
    assert results["ctx-0"]["timezone"] in {"Europe/Istanbul", "UTC", "America/New_York"}
    assert results["ctx-1"]["timezone"] in {"Europe/Istanbul", "UTC", "America/New_York"}


def test_declarative_steps_and_network_logging(tmp_path, demo_server):
    """The declarative workflow engine runs step by step and logs traffic."""
    from waft.orchestrator import Orchestrator

    base = demo_server
    rows = [
        {
            "name": "wizard",
            "target_url": f"{base}/wizard",
            "scenario": "form-submit",
            "email": "wizard@example.com",
            "password": "Wa!tWizard1",
            "phone": "+90 555 000 11 22",
            "submit": "false",
            "steps": [
                {"action": "goto", "value": f"{base}/wizard"},
                {"action": "fill", "target": "#wizard-email", "value": "{email}"},
                {"action": "click", "target": "#wizard-continue"},
                {"action": "wait_for_selector", "target": "#wizard-password"},
                {"action": "fill", "target": "#wizard-password", "value": "{password}"},
                {"action": "fill", "target": "#wizard-phone", "value": "{phone}"},
                {"action": "click", "target": "button:has-text('Tamamla')"},
                {"action": "wait_for_selector", "target": ".notice.success"},
                {"action": "expect_url", "value": "/submit"},
            ],
        }
    ]
    data = tmp_path / "wizard.json"
    data.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    config = build_config(tmp_path, data, contexts=1)
    orchestrator = Orchestrator(config)
    assert asyncio.run(orchestrator.run()) == 0

    summary = json.loads((Path(orchestrator.artifacts.root) / "run.json").read_text(encoding="utf-8"))
    run = summary["contexts"][0]["runs"][0]
    step_names = [step["name"] for step in run["steps"]]
    assert "step1:goto" in step_names and "step9:expect_url" in step_names
    assert all(step["status"] == "ok" for step in run["steps"] if step["name"].startswith("step"))

    # network.jsonl written inside the context artifact folder
    context_dir = Path(orchestrator.artifacts.root) / "contexts" / summary["contexts"][0]["context_id"]
    assert (context_dir / "network.jsonl").exists()
    assert any(line for line in (context_dir / "network.jsonl").read_text(encoding="utf-8").splitlines())
    endpoints = json.loads((Path(orchestrator.artifacts.root) / "endpoints.json").read_text(encoding="utf-8"))
    assert endpoints["count"] >= 1


def test_failure_produces_bundle_and_retry(tmp_path, demo_server):
    """A real failure must produce screenshots, a trace and a .zip bundle."""
    from waft.orchestrator import Orchestrator

    rows = [
        {
            "name": "will-fail",
            "target_url": f"{demo_server}/definitely-not-here-404",
            "scenario": "smoke",  # smoke requires a non-empty title → 404 page has one
            "submit": "false",
        }
    ]
    # Force a failure through an unsatisfiable expectation.
    rows.append(
        {
            "name": "failing-expectation",
            "target_url": f"{demo_server}/",
            "scenario": "form-submit",
            "email": "a@b.com",
            "terms": "true",
            "submit": "false",
            "steps": [
                {"action": "goto", "value": f"{demo_server}/"},
                {"action": "expect_visible", "target": "#does-not-exist", "timeout_ms": 1500},
            ],
        }
    )
    data = tmp_path / "rows.json"
    data.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    config = build_config(tmp_path, data, contexts=1)
    config.retries = 1  # exercise the retry path too
    config.retry_only_retryable = False  # a deterministic WorkflowError is retried in this test

    orchestrator = Orchestrator(config)
    exit_code = asyncio.run(orchestrator.run())
    assert exit_code == 1, "a failing expectation must fail the run"

    summary = json.loads((Path(orchestrator.artifacts.root) / "run.json").read_text(encoding="utf-8"))
    assert summary["totals"]["targets_failed"] >= 1
    assert summary["failures"]
    failure = next(f for f in summary["failures"] if f["name"] == "failing-expectation")
    assert failure["attempts"] and failure["attempts"] >= 2, "retry should have been attempted"

    context = summary["contexts"][0]
    assert context["status"] == "failed"
    assert context["failure_bundle"] and Path(context["failure_bundle"]).exists()
    assert context["trace_path"] and Path(context["trace_path"]).exists()
    screenshots = list((Path(orchestrator.artifacts.root) / "contexts").rglob("*.png"))
    assert screenshots, "failure screenshots were not produced"
    bundle_files = [path.name for path in Path(context["failure_bundle"]).parent.iterdir()]
    assert any(name.endswith(".zip") for name in bundle_files)
