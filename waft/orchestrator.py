"""Run orchestration: browser lifecycle, context scheduling, signals, aggregation.

``Orchestrator.run()`` is the single entry point used by the CLI. Responsibilities:

1.  Validate configuration and the runtime environment (``--print-config``, ``--dry-run``).
2.  Load targets (Excel/JSON/CSV) and proxies, run proxy health checks.
3.  Launch **one** browser engine and create *N* isolated contexts (bounded by
    ``--concurrency`` so 25 contexts do not exhaust RAM on a laptop).
4.  Execute every target row inside every context (with ``--iterations``), collecting
    :class:`~waft.models.TargetRunResult` objects.
5.  React to Ctrl+C / SIGTERM gracefully: stop scheduling new work, close browsers, write
    the reports that are already available.
6.  Aggregate metrics, print a console summary and write ``run.json``, ``results.csv``,
    ``junit.xml``, ``artifact-index.json`` and the per-context failure bundles.
"""

from __future__ import annotations

import asyncio
import os
import platform
import signal
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from .artifacts import ArtifactManager
from .config import Config, setup_playwright_env
from .data_source import DataLoader
from .engine import ContextEngine, ContextEngineFactory, EngineHooks, profile_from_config
from .errors import ConfigError, WaftError
from .imap_client import ImapClient, ImapSettings
from .logging_setup import get_logger, setup_logging
from .models import (
    ContextProfile,
    ContextResult,
    RunSummary,
    RunTotals,
    STATUS_FAILED,
    STATUS_OK,
    TargetRow,
    TargetRunResult,
)
from .network_monitor import NetworkMonitor  # noqa: F401 - re-exported for convenience
from .proxy_manager import ProxyPool, summarize_health
from .reporting import Reporter
from .stealth import StealthLayer
from .utils import (
    RateLimiter,
    Redactor,
    Stopwatch,
    human_ms,
    new_run_id,
    now_iso,
    truncate,
)

__all__ = ["Orchestrator", "RunStats", "run_from_config", "launch_kwargs"]

logger = get_logger("waft.orchestrator")


# --------------------------------------------------------------------------------------
# Run statistics
# --------------------------------------------------------------------------------------


@dataclass
class RunStats:
    """Live counters updated while contexts execute."""

    started_at: str = field(default_factory=now_iso)
    targets_planned: int = 0
    targets_started: int = 0
    targets_ok: int = 0
    targets_failed: int = 0
    targets_skipped: int = 0
    steps_ok: int = 0
    steps_failed: int = 0
    contexts_ok: int = 0
    contexts_failed: int = 0
    retries: int = 0
    verifications_ok: int = 0
    verifications_failed: int = 0
    api_endpoints: set[str] = field(default_factory=set)
    status_counter: Counter = field(default_factory=Counter)
    error_counter: Counter = field(default_factory=Counter)
    stop_requested: bool = False

    def record_run(self, result: TargetRunResult) -> None:
        """Update counters for one finished target (synchronous: called from the event loop)."""
        if True:
            self.targets_started += 1
            if result.attempt > 1:
                self.retries += result.attempt - 1
            if result.ok:
                self.targets_ok += 1
            elif result.status == "skipped":
                self.targets_skipped += 1
            else:
                self.targets_failed += 1
            for step in result.steps:
                if step.status == STATUS_OK:
                    self.steps_ok += 1
                elif step.status == STATUS_FAILED:
                    self.steps_failed += 1
            if result.verification is not None and result.verification.attempted:
                if result.verification.success:
                    self.verifications_ok += 1
                else:
                    self.verifications_failed += 1
            self.api_endpoints.update(result.api_endpoints or [])
            for status, count in (result.network_metrics.get("by_status") or {}).items():
                self.status_counter[str(status)] += int(count)
            if result.error:
                self.error_counter[result.error_type or "Error"] += 1

    def to_totals(self) -> RunTotals:
        return RunTotals(
            targets_planned=self.targets_planned,
            targets_run=self.targets_started,
            targets_ok=self.targets_ok,
            targets_failed=self.targets_failed,
            targets_skipped=self.targets_skipped,
            steps_ok=self.steps_ok,
            steps_failed=self.steps_failed,
            retries=self.retries,
            contexts_ok=self.contexts_ok,
            contexts_failed=self.contexts_failed,
            verifications_ok=self.verifications_ok,
            verifications_failed=self.verifications_failed,
        )


# --------------------------------------------------------------------------------------
# Launch helpers
# --------------------------------------------------------------------------------------


def launch_kwargs(config: Config) -> dict[str, Any]:
    """Build ``browser_type.launch(**kwargs)`` arguments (container/CI friendly)."""
    args: list[str] = []
    if config.browser in {"chromium", "chrome", "msedge"}:
        if not config.allow_chromium_sandbox:
            args += ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        args += [
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-notifications",
            "--disable-infobars",
            "--metrics-recording-only",
            "--password-store=basic",
            "--use-mock-keychain",
            "--hide-crash-restore-bubble",
            "--lang=en-US",
            f"--window-size={ (config.device_profiles[0].viewport if config.device_profiles else {'width':1366,'height':768})['width'] },"
            f"{(config.device_profiles[0].viewport if config.device_profiles else {'height':768})['height'] + 90}",
        ]
        args += list(config.browser_args or [])
    kwargs: dict[str, Any] = {
        "headless": config.headless,
        "args": args,
        "slow_mo": config.slow_mo_ms or None,
        "timeout": max(30_000, config.browser_timeout_ms),
    }
    if config.chromium_channel and config.browser in {"chromium", "chrome", "msedge"}:
        kwargs["channel"] = config.chromium_channel
    if config.browser == "firefox" and config.firefox_executable:
        kwargs["executable_path"] = config.firefox_executable
    if config.browser == "webkit" and config.webkit_executable:
        kwargs["executable_path"] = config.webkit_executable
    kwargs["env"] = {**os.environ, "WAFT_RUN": "1"}
    return {key: value for key, value in kwargs.items() if value is not None}


# --------------------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------------------


class Orchestrator:
    """Coordinates a complete run."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.run_id = new_run_id()
        self.stats = RunStats()
        self.rows: list[TargetRow] = []
        self.profiles: list[ContextProfile] = []
        self.proxy_pool: Optional[ProxyPool] = None
        self.artifacts: Optional[ArtifactManager] = None
        self.redactor = Redactor(enabled=config.redact, extra_header_keys=config.sensitive_headers)
        self.reporter: Optional[Reporter] = None
        self.rate_limiter = RateLimiter(config.rate_limit, burst=max(1.0, (config.rate_limit or 1.0) * 2))
        self._stop_event = asyncio.Event()
        self._browser: Any = None
        self._playwright: Any = None
        self._imap: Optional[ImapClient] = None
        self._engines: list[ContextEngine] = []
        self._context_results: list[ContextResult] = []
        self._load_report: Optional[dict[str, Any]] = None
        self._signal_handlers_installed = False

    # ==============================================================================
    # setup
    # ==============================================================================
    def prepare(self) -> None:
        """Load data, proxies and profiles; create the artifact tree. No browser yet."""
        config = self.config
        setup_playwright_env()

        warnings = config.check_environment()
        for warning in warnings:
            logger.warning("%s", warning)

        self.artifacts = ArtifactManager(config, self.run_id)
        self.reporter = Reporter(config, self.artifacts, self.run_id)
        log_path = (
            Path(str(config.log_file).replace("{run_id}", self.run_id).replace("{ts}", self.run_id))
            if config.log_file
            else (self.artifacts.logs_root / "waft.log")
        )
        setup_logging(
            config.log_level,
            log_file=log_path,
            force_reconfigure=True,
            quiet_console=config.quiet,
        )

        loader = DataLoader(config)
        self.rows = loader.load()
        self._load_report = loader.report.to_dict()
        if not self.rows:
            raise ConfigError("The data source produced zero runnable rows")

        self.proxy_pool = ProxyPool(config)
        self.proxy_pool.load()

        targets_per_context = len(self.rows) * max(1, config.iterations)
        self.stats.targets_planned = targets_per_context * config.contexts

        device_names = [device.name for device in config.device_profiles]
        self.profiles = []
        for index in range(config.contexts):
            profile = profile_from_config(config, index, target_urls=[row.target_url for row in self.rows])
            if config.storage_state_dir:
                profile.storage_state_path = str(Path(config.storage_state_dir) / f"{profile.context_id}-storage.json")
            self.profiles.append(profile)

        logger.info(
            "Plan: %d context(s) × %d target(s) × %d iteration(s) = %d target run(s), concurrency=%d",
            config.contexts,
            len(self.rows),
            config.iterations,
            self.stats.targets_planned,
            config.resolved_concurrency,
        )
        logger.info("Devices in rotation: %s", ", ".join(dict.fromkeys(device_names)) or "default")
        logger.info(
            "Locales: %s | Timezones: %s",
            ", ".join(sorted({profile.locale or "?" for profile in self.profiles})),
            ", ".join(sorted({profile.timezone_id or "?" for profile in self.profiles})),
        )

    async def health_check_proxies(self) -> list[dict[str, Any]]:
        """Run proxy health probes (Playwright based when possible)."""
        assert self.proxy_pool is not None
        if not self.proxy_pool.enabled or not self.config.proxy_health_check:
            return []
        if self._playwright is None:
            self._playwright = await self._start_playwright()
        results = await self.proxy_pool.health_check_all(playwright=self._playwright)
        return summarize_health(results)

    async def _start_playwright(self) -> Any:
        from playwright.async_api import async_playwright

        if self._playwright is None:
            self._playwright = await async_playwright().start()
        return self._playwright

    async def _launch_browser(self) -> Any:
        playwright = await self._start_playwright()
        browser_type = getattr(playwright, self.config.browser if self.config.browser in {"chromium", "firefox", "webkit"} else "chromium")
        kwargs = launch_kwargs(self.config)
        logger.info(
            "Launching %s (headless=%s, slow_mo=%s ms, args=%d)",
            self.config.browser,
            self.config.headless,
            self.config.slow_mo_ms,
            len(kwargs.get("args", [])),
        )
        try:
            self._browser = await browser_type.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001 - enrich the error message
            message = str(exc)
            if "Executable doesn't exist" in message or "playwright install" in message:
                raise ConfigError(
                    "Playwright browser binaries are missing. Run: python -m playwright install --with-deps "
                    f"{self.config.browser}"
                ) from exc
            if "error while loading shared libraries" in message:
                raise ConfigError(
                    "System libraries for Chromium are missing. Run: python -m playwright install-deps chromium"
                ) from exc
            raise
        logger.info("Browser ready: %s", self._browser.version)
        return self._browser

    def _install_signal_handlers(self) -> None:
        """Graceful Ctrl+C/SIGTERM handling (also inside containers)."""
        if self._signal_handlers_installed or not self.config.exit_on_sigint:
            return
        loop = asyncio.get_running_loop()

        def _handle(signame: str) -> None:
            if self.stats.stop_requested:
                logger.error("Second %s received - exiting immediately", signame)
                raise SystemExit(130)
            self.stats.stop_requested = True
            logger.warning(
                "%s received - finishing the current target(s) and shutting down gracefully "
                "(press again to abort immediately)",
                signame,
            )
            self._stop_event.set()

        for signame in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, signame, None)
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, _handle, signame)
            except (NotImplementedError, RuntimeError):  # pragma: no cover - Windows
                try:
                    signal.signal(sig, lambda *_args, name=signame: _handle(name))
                except Exception:  # noqa: BLE001
                    pass
        self._signal_handlers_installed = True

    # ==============================================================================
    # execution
    # ==============================================================================
    async def _build_imap(self) -> Optional[ImapClient]:
        """Create the shared IMAP client when the run needs it."""
        config = self.config
        needs_imap = config.imap_enabled or any(row.requires_email_verification for row in self.rows)
        if not needs_imap:
            return None
        if not (config.imap_host and config.imap_username and config.imap_password):
            if any(row.requires_email_verification for row in self.rows):
                logger.warning(
                    "Some rows require e-mail verification but IMAP credentials are missing - those steps will be skipped"
                )
            return None
        settings = ImapSettings.from_config(config)
        client = ImapClient(settings)
        try:
            info = await client.check()
            logger.info(
                "IMAP ready: %s@%s:%s mailbox=%s (%s message(s), %s unseen)",
                settings.username,
                settings.host,
                settings.port,
                settings.mailbox,
                info.get("messages"),
                info.get("unseen"),
            )
        except Exception as exc:  # noqa: BLE001 - verification is optional
            logger.warning("IMAP pre-flight check failed (%s); verification steps will report errors", exc)
        return client

    async def _run_context(self, index: int, engine_factory: ContextEngineFactory, semaphore: asyncio.Semaphore) -> ContextResult:
        """Create, run and tear down one context."""
        profile = self.profiles[index]
        async with semaphore:
            result: Optional[ContextResult] = None
            engine: Optional[ContextEngine] = None
            stopwatch = Stopwatch()
            stopwatch.__enter__()
            started_iso = now_iso()
            try:
                proxy = await self.proxy_pool.acquire(context_id=profile.context_id) if self.proxy_pool else None
                if proxy is not None and proxy.username:
                    logger.debug("[%s] proxy credentials present (masked)", profile.context_id)
                engine = await engine_factory.create(profile, proxy)
                self._engines.append(engine)
                await engine.start()
                await engine.run_rows(self.rows)
                failed_runs = [run for run in engine.runs if not run.ok]
                result = engine.build_context_result(
                    status=STATUS_FAILED if failed_runs else STATUS_OK,
                    error=(
                        f"{len(failed_runs)} of {len(engine.runs)} target(s) failed"
                        if failed_runs
                        else None
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - every context failure is reported, never fatal
                logger.error("[%s] context failed: %s: %s", profile.context_id, type(exc).__name__, truncate(str(exc), 300))
                if engine is not None:
                    result = engine.build_context_result(status=STATUS_FAILED, error=f"{type(exc).__name__}: {exc}")
                    result.error_type = type(exc).__name__
            finally:
                if engine is not None:
                    try:
                        trace_path = await engine.close(failed=(result is None or not result.ok))
                        if result is not None and trace_path:
                            result.trace_path = trace_path
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("[%s] engine shutdown issue: %s", profile.context_id, exc)
                stopwatch.stop()

            if result is None:
                result = ContextResult(context_id=profile.context_id, index=index, status=STATUS_FAILED, error="context produced no result")
            result.started_at = started_iso
            result.duration_ms = round(stopwatch.elapsed_ms, 1)
            result.finished_at = result.finished_at or now_iso()

            if result.ok:
                self.stats.contexts_ok += 1
            else:
                self.stats.contexts_failed += 1
            self._context_results.append(result)

            # Per-context artifacts: summary JSON + failure bundle.
            if self.artifacts is not None:
                try:
                    self.artifacts.write_context_summary(result)
                    if not result.ok:
                        result.failure_bundle = self.artifacts.create_failure_bundle(result)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[%s] artifact finalisation failed: %s", profile.context_id, exc)

            logger.info(
                "[%s] finished: %d target(s), %d ok, %d failed in %s%s",
                profile.context_id,
                len(result.runs),
                sum(1 for run in result.runs if run.ok),
                sum(1 for run in result.runs if not run.ok),
                human_ms(result.duration_ms),
                " ❌" if not result.ok else " ✅",
            )
            return result

    async def run(self) -> int:
        """Execute the whole run and return the process exit code (0 = success)."""
        config = self.config
        self.prepare()
        assert self.artifacts is not None and self.reporter is not None and self.proxy_pool is not None

        if config.dry_run:
            return self.print_plan()

        logger.info("━" * 100)
        logger.info("WAFT run %s starting | data=%s | proxies=%s", self.run_id, config.data_file, config.proxy_file or "-")
        logger.info("━" * 100)

        self._install_signal_handlers()
        started = time.perf_counter()
        proxy_health: list[dict[str, Any]] = []
        try:
            browser = await self._launch_browser()
            proxy_health = await self.health_check_proxies()
            self._imap = await self._build_imap()

            stealth = StealthLayer(config)
            if stealth.library_available:
                logger.info("Anti-detection: playwright-stealth + WAFT fingerprint layer")
            else:
                logger.info("Anti-detection: WAFT built-in layer (install playwright-stealth for extended evasions)")

            hooks = EngineHooks(
                rate_limiter=self.rate_limiter,
                should_stop=lambda: self.stats.stop_requested,
                on_target_end=self._on_target_end,
                on_api_candidate=self._on_api_candidate,
            )
            factory = ContextEngineFactory(
                config,
                browser,
                self.proxy_pool,
                self.artifacts,
                stealth,
                imap_client=self._imap,
                hooks=hooks,
                redactor=self.redactor,
            )

            semaphore = asyncio.Semaphore(max(1, config.resolved_concurrency))
            tasks = [
                asyncio.create_task(self._run_context(index, factory, semaphore), name=f"ctx-{index:02d}")
                for index in range(config.contexts)
            ]
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

            for index, outcome in enumerate(results):
                if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                    logger.error("Context %d raised: %r", index, outcome)

        except KeyboardInterrupt:  # pragma: no cover - handled through signals normally
            logger.warning("Interrupted by the user")
            self.stats.stop_requested = True
        except WaftError as exc:
            logger.error("Run aborted: %s", exc)
            self._finish(stopwatch_seconds=time.perf_counter() - started, proxy_health=proxy_health, fatal=True)
            raise
        finally:
            await self._shutdown_browser()
            if self._imap is not None:
                try:
                    await self._imap.close_async()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("IMAP close failed: %s", exc)
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._playwright = None

        summary = self._finish(stopwatch_seconds=time.perf_counter() - started, proxy_health=proxy_health)
        return summary.exit_code

    async def _shutdown_browser(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
                logger.debug("Browser closed")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Browser close failed: %s", exc)
            self._browser = None

    # ==============================================================================
    # hooks / aggregation
    # ==============================================================================
    def _on_target_end(self, context_id: str, result: TargetRunResult) -> None:
        """Update live statistics and honour rate-based abort policies."""
        self.stats.record_run(result)
        if self.config.stop_on_error_rate is not None and self.stats.targets_started >= 10:
            rate = 100.0 * self.stats.targets_failed / max(1, self.stats.targets_started)
            if rate >= self.config.stop_on_error_rate and not self.stats.stop_requested:
                logger.error(
                    "Failure rate %.1f%% ≥ --stop-on-error-rate %.1f%% → stopping the run",
                    rate,
                    self.config.stop_on_error_rate,
                )
                self.stats.stop_requested = True
                self._stop_event.set()
        del context_id

    def _on_api_candidate(self, context_id: str, record: dict[str, Any]) -> None:
        self.stats.api_endpoints.add(f"{record.get('method')} {record.get('host')}{record.get('path')}")
        del context_id

    # ==============================================================================
    # reporting
    # ==============================================================================
    def _finish(self, *, stopwatch_seconds: float, proxy_health: Sequence[dict[str, Any]], fatal: bool = False) -> RunSummary:
        """Aggregate everything into a :class:`RunSummary`, write reports, print the table."""
        assert self.artifacts is not None and self.reporter is not None
        config = self.config
        totals = self.stats.to_totals()
        totals.contexts = config.contexts
        if not totals.contexts_ok and not totals.contexts_failed:
            totals.contexts_ok = sum(1 for ctx in self._context_results if ctx.ok)
            totals.contexts_failed = len(self._context_results) - totals.contexts_ok

        summary = RunSummary(
            run_id=self.run_id,
            started_at=self.stats.started_at,
            finished_at=now_iso(),
            duration_ms=round(stopwatch_seconds * 1000, 1),
            status=STATUS_OK if (totals.targets_failed == 0 and not self.stats.stop_requested and not fatal) else STATUS_FAILED,
            interrupted=self.stats.stop_requested,
            artifacts_dir=str(self.artifacts.root),
            data_source=str(config.data_file) if config.data_file else None,
            proxy_source=self.proxy_pool.source if self.proxy_pool else None,
            browser=config.browser,
            headless=config.headless,
            concurrency=config.resolved_concurrency,
            context_count=config.contexts,
            iterations=config.iterations,
            totals=totals,
            contexts=list(self._context_results),
            api_endpoints=sorted(self.stats.api_endpoints)[:500],
            config_digest=dict(config.config_digest),
            notes=[],
        )

        for context in self._context_results:
            for run in context.runs:
                if run.ok:
                    continue
                summary.failures.append(
                    {
                        "context_id": context.context_id,
                        "row_index": run.row_index,
                        "name": run.metadata.get("row_name"),
                        "url": self.redactor.text(run.target_url, limit=300),
                        "error": run.error,
                        "error_type": run.error_type,
                        "attempts": run.metadata.get("attempts"),
                        "screenshot": (run.screenshots or [None])[-1],
                        "trace": run.trace_path,
                        "bundle": context.failure_bundle,
                        "duration_ms": run.duration_ms,
                    }
                )

        # Extra diagnostics stored on the summary (kept out of the dataclass fields).
        summary.config_digest.update(
            {
                "load_report": self._load_report,
                "proxy_health": list(proxy_health),
                "proxy_stats": self.proxy_pool.stats() if self.proxy_pool else {},
                "stealth": {"enabled": config.stealth, "canvas": config.canvas_noise, "webgl": config.webgl_spoof},
                "environment": {
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "cpu_count": os.cpu_count(),
                },
                "retries": self.stats.retries,
                "http_status_mix": dict(self.stats.status_counter),
                "error_mix": dict(self.stats.error_counter),
                "verifications": {
                    "ok": self.stats.verifications_ok,
                    "failed": self.stats.verifications_failed,
                },
                "blocked_requests": self._block_stats(),
            }
        )

        exit_code = 0 if (totals.targets_failed == 0 and not self.stats.stop_requested and not fatal) else 1
        if totals.targets_run == 0:
            exit_code = 1
            summary.notes.append("No target was executed")
        summary.exit_code = exit_code

        try:
            written = self.reporter.write_all(summary)
            summary.notes.append(f"Reports: {', '.join(sorted({Path(path).name for path in written.values() if path}))}")
        except Exception as exc:  # noqa: BLE001 - reporting must not mask the run outcome
            logger.error("Report generation failed: %s", exc)

        try:
            index_path = self.artifacts.write_index()
            if index_path:
                logger.debug("Artifact index: %s", index_path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Artifact index failed: %s", exc)

        self.reporter.print_summary(summary)
        return summary

    def _block_stats(self) -> dict[str, int]:
        totals = Counter()
        for engine in self._engines:
            for key, value in engine._block_stats.items():  # noqa: SLF001 - internal metric by design
                totals[key] += int(value)
        return dict(totals)

    # ==============================================================================
    # dry run / config print
    # ==============================================================================
    def print_plan(self) -> int:
        """Render the execution plan without launching any browser (``--dry-run``)."""
        assert self.proxy_pool is not None
        config = self.config
        logger.info("=" * 96)
        logger.info("DRY RUN - no browser will be launched")
        logger.info("=" * 96)
        logger.info("Run id           : %s", self.run_id)
        logger.info("Data source      : %s", config.data_file)
        logger.info("Target rows      : %d", len(self.rows))
        logger.info("Iterations       : %d", config.iterations)
        logger.info("Contexts         : %d (concurrency %d)", config.contexts, config.resolved_concurrency)
        logger.info("Planned runs     : %d target executions", self.stats.targets_planned)
        logger.info("Browser          : %s (headless=%s)", config.browser, config.headless)
        logger.info("Scenario         : %s", config.scenario)
        logger.info("Artifacts        : %s", self.artifacts.root if self.artifacts else "-")
        logger.info("Proxies          : %d (%s)", len(self.proxy_pool.proxies), self.proxy_pool.source or "none")
        for spec in self.proxy_pool.proxies[:12]:
            logger.info("    • %s", spec.masked())
        if len(self.proxy_pool.proxies) > 12:
            logger.info("    • … %d more", len(self.proxy_pool.proxies) - 12)
        logger.info("Devices          :")
        for profile in self.profiles[:12]:
            logger.info(
                "    • %-14s %-9s %-16s %s",
                profile.context_id,
                (profile.device.name if profile.device else "default")[:14],
                profile.locale,
                profile.timezone_id,
            )
        if len(self.profiles) > 12:
            logger.info("    • … %d more context(s)", len(self.profiles) - 12)
        logger.info("Targets preview  :")
        logger.info("%s", DataLoader.describe_rows(self.rows, limit=12))
        if config.imap_enabled or any(row.requires_email_verification for row in self.rows):
            logger.info(
                "IMAP verification: %s@%s:%s mailbox=%s (timeout %ss)",
                config.imap_username,
                config.imap_host,
                config.imap_port,
                config.imap_mailbox,
                config.imap_timeout_s,
            )
        logger.info("=" * 96)
        return 0

    def print_config(self) -> int:
        """Print the effective configuration as JSON."""
        import json as _json

        print(_json.dumps(self.config.to_dict(redact=True), indent=2, sort_keys=True, default=str))
        return 0


async def run_from_config(config: Config) -> int:
    """Async convenience wrapper used by the CLI and by tests."""
    orchestrator = Orchestrator(config)
    if getattr(config, "print_config", False):
        return orchestrator.print_config()
    return await orchestrator.run()


def run(config: Config) -> int:
    """Synchronous entry point (``python -m waft`` uses this)."""
    try:
        return asyncio.run(run_from_config(config))
    except KeyboardInterrupt:  # pragma: no cover - user pressed Ctrl+C during shutdown
        logger.warning("Aborted by the user")
        return 130
