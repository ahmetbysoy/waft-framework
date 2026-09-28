"""Context engine: one isolated browser context = one virtual user.

Responsibilities
----------------
*   Create a Chromium/Firefox/WebKit context with its own **proxy, user-agent, timezone,
    locale, viewport, geolocation and storage** (requirement #1 & #3).
*   Apply the stealth layer (requirement #2) and verify it on demand.
*   Drive the scenario for every target row: navigation, declarative step workflows,
    automated form filling/submission (requirement #4), outcome detection, screenshots and
    trace handling (requirement #8).
*   Delegate IMAP verification-link handling (requirement #6) to :mod:`waft.imap_client`.
*   Return rich :class:`~waft.models.TargetRunResult` objects that the orchestrator/reporters
    turn into run summaries, CSV and JUnit XML.

A context is *sticky* to its proxy for the whole run but can transparently rebuild itself on
a fresh proxy when the current one fails (``_rotate_proxy``).
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError

from .artifacts import ArtifactManager
from .config import Config
from .data_source import interpolate_row
from .errors import (
    BlockedByEdgeError,
    CaptchaDetectedError,
    ContextClosedError,
    FormAutomationError,
    ImapError,
    ImapTimeoutError,
    NavigationError,
    OutcomeTimeoutError,
    TargetUnreachableError,
    VerificationError,
    WorkflowError,
    is_retryable,
)
from .forms import FormFiller, SELECTOR_TEMPLATES, captcha_action, locate_focus
from .imap_client import ImapClient
from .logging_setup import context_logger, get_logger
from .models import (
    ContextProfile,
    FillReport,
    ProxySpec,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    StepAction,
    StepResult,
    TargetRow,
    TargetRunResult,
    VerificationResult,
)
from .network_monitor import NetworkMonitor
from .proxy_manager import ProxyPool
from .stealth import StealthLayer
from .utils import Redactor, Stopwatch, human_ms, interpolate, normalize_ws, parse_int, truncate

__all__ = ["ContextEngine", "ContextEngineFactory", "SCENARIOS"]

logger = get_logger("waft.engine")

SCENARIOS = ("auto", "load-test", "smoke", "form-submit", "email-verify", "api-discovery", "full-journey")

#: Headers used to detect WAF / bot-management challenge pages.
BLOCK_VENDOR_MARKERS = {
    "cloudflare": ("cf-ray", "cloudflare", "__cf_bm", "cf-mitigated"),
    "akamai": ("akamai", "_abck", "ak_bmsc"),
    "datadome": ("datadome", "dd_cookie"),
    "perimeterx": ("perimeterx", "_px", "px-captcha"),
    "incapsula": ("incap_ses", "visid_incap", "imperva"),
    "distil": ("distil", "x-distil"),
    "sucuri": ("sucuri", "x-sucuri"),
    "aws_waf": ("awselb", "aws-waf", "x-amzn-waf"),
    "f5": ("x-wa-info", "ts01", "big-ip"),
}


@dataclass
class EngineHooks:
    """Callbacks the orchestrator injects to observe engine progress."""

    on_step: Optional[Callable[[str, StepResult], Any]] = None
    on_target_start: Optional[Callable[[str, TargetRow, int], Any]] = None
    on_target_end: Optional[Callable[[str, TargetRunResult], Any]] = None
    on_api_candidate: Optional[Callable[[str, dict[str, Any]], Any]] = None
    should_stop: Optional[Callable[[], bool]] = None
    rate_limiter: Optional[Any] = None  # waft.utils.RateLimiter


class ContextEngine:
    """Owns one browser context and executes target rows inside it."""

    def __init__(
        self,
        config: Config,
        profile: ContextProfile,
        browser: Browser,
        proxy_pool: ProxyPool,
        proxy: Optional[ProxySpec],
        artifacts: ArtifactManager,
        stealth: StealthLayer,
        hooks: Optional[EngineHooks] = None,
        imap_client: Optional[ImapClient] = None,
        redactor: Optional[Redactor] = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self.browser = browser
        self.proxy_pool = proxy_pool
        self.proxy = proxy
        self.artifacts = artifacts
        self.stealth = stealth
        self.hooks = hooks or EngineHooks()
        self.imap_client = imap_client
        self.redactor = redactor or Redactor(enabled=config.redact)

        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.monitor: Optional[NetworkMonitor] = None
        self.filler = FormFiller(config, self.redactor)
        self.log = context_logger(logger, profile.context_id)

        self.pages_served = 0
        self.runs: list[TargetRunResult] = []
        self._trace_started = False
        self._har_path: Optional[Path] = None
        self._extra_headers = dict(config.extra_headers)
        self._closed = False
        self._block_stats = {"blocked_resource_types": 0, "blocked_hosts": 0, "aborted_requests": 0}

    # ==============================================================================
    # lifecycle
    # ==============================================================================
    async def start(self) -> None:
        """Create the browser context, apply stealth and attach the network monitor."""
        stopwatch = Stopwatch()
        stopwatch.__enter__()
        context_options = self._context_options()
        try:
            self.context = await self.browser.new_context(**context_options)
        except Exception as exc:  # noqa: BLE001 - surfaces as a clear framework error
            raise ContextClosedError(
                f"Could not create browser context {self.profile.context_id}: {exc}",
                details={"proxy": self.proxy.masked() if self.proxy else None},
            ) from exc

        self.context.set_default_timeout(self.config.default_timeout_ms)
        self.context.set_default_navigation_timeout(self.config.navigation_timeout_ms)
        try:
            await self.context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });"
            )
        except Exception:  # noqa: BLE001 - the stealth layer covers this too
            pass

        await self._install_routes()
        page = await self.context.new_page()
        self.page = page

        await self.stealth.apply(self.context, self.profile, page=page)
        self.monitor = NetworkMonitor(
            self.config,
            self.profile.context_id,
            artifact_dir=self.artifacts.context_dir(self.profile.context_id),
        )
        if self.hooks.on_api_candidate is not None:
            self.monitor.on_api_candidate = lambda record: self.hooks.on_api_candidate(self.profile.context_id, record)  # type: ignore[misc]
        await self.monitor.attach(self.context, page)

        self._trace_started = await self.artifacts.start_trace(self.context, self.profile.context_id)
        if self.config.capture_har:
            self._har_path = self.artifacts.har_path(self.profile.context_id)

        stopwatch.stop()
        self.log.info(
            "Context ready in %s | ua=%s | tz=%s | locale=%s | viewport=%s | proxy=%s",
            human_ms(stopwatch.elapsed_ms),
            truncate(self.profile.user_agent or "default", 60),
            self.profile.timezone_id,
            self.profile.locale,
            self.profile.viewport,
            self.proxy.masked() if self.proxy else "direct",
        )
        if self.config.verify_stealth and self.page is not None:
            try:
                verification = await self.stealth.verify(self.page, self.profile, strict=False)
                if verification.ok:
                    self.log.info("Stealth self-test passed: %s", verification.checks)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("Stealth self-test could not run: %s", exc)

    def _context_options(self) -> dict[str, Any]:
        """Translate the profile into ``browser.new_context()`` keyword arguments."""
        profile = self.profile
        options: dict[str, Any] = {
            "viewport": profile.viewport or {"width": 1366, "height": 768},
            "screen": profile.screen or profile.viewport or {"width": 1366, "height": 768},
            "user_agent": profile.user_agent,
            "locale": profile.locale,
            "timezone_id": profile.timezone_id,
            "ignore_https_errors": profile.ignore_https_errors,
            "java_script_enabled": profile.java_script_enabled,
            "is_mobile": profile.is_mobile,
            "has_touch": profile.has_touch,
            "device_scale_factor": profile.device_scale_factor or 1.0,
            "color_scheme": profile.color_scheme,
            "reduced_motion": profile.reduced_motion,
            "service_workers": profile.service_workers,
            "accept_downloads": True,
            "extra_http_headers": self._extra_headers or None,
        }
        if profile.credentials:
            options["http_credentials"] = profile.credentials
        if profile.geolocation is not None:
            options["geolocation"] = profile.geolocation.to_playwright()
            options["permissions"] = sorted(set(profile.permissions) | {"geolocation"})
        elif profile.permissions:
            options["permissions"] = list(profile.permissions)
        if self.proxy is not None:
            options["proxy"] = self.proxy.to_playwright()
        if profile.storage_state_path and Path(profile.storage_state_path).exists():
            options["storage_state"] = profile.storage_state_path
        har_path = self.artifacts.har_path(profile.context_id)
        if har_path is not None:
            options["record_har_path"] = str(har_path)
            options["record_har_mode"] = self.config.har_mode
            options["record_har_content"] = self.config.har_content
        # Drop None values so Playwright keeps its own defaults.
        return {key: value for key, value in options.items() if value is not None}

    async def _install_routes(self) -> None:
        """Abort configured resource types/hosts (bandwidth + realism control)."""
        assert self.context is not None
        resource_types = {item.lower() for item in self.config.block_resource_types if item}
        blocked_hosts = [pattern for pattern in self.config.block_hosts if pattern]
        if not resource_types and not blocked_hosts:
            return

        async def handler(route: Any) -> None:
            try:
                request = route.request
                if resource_types and request.resource_type in resource_types:
                    self._block_stats["blocked_resource_types"] += 1
                    await route.abort()
                    return
                host = urlsplit(request.url).netloc.lower()
                for pattern in blocked_hosts:
                    if pattern in host:
                        self._block_stats["blocked_hosts"] += 1
                        await route.abort()
                        return
                await route.continue_()
            except Exception:  # noqa: BLE001 - route may already be handled
                self._block_stats["aborted_requests"] += 1
                try:
                    await route.abort()
                except Exception:  # noqa: BLE001
                    pass

        try:
            await self.context.route("**/*", handler)
            self.log.debug(
                "Routing installed (block resource types=%s, hosts=%s)",
                sorted(resource_types) or "-",
                blocked_hosts or "-",
            )
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Could not install request routing: %s", exc)

    async def close(self, *, failed: bool = False) -> Optional[str]:
        """Close the context, flush traffic logs and stop tracing."""
        if self._closed:
            return None
        self._closed = True

        trace_path: Optional[str] = None
        if self.monitor is not None:
            try:
                await self.monitor.close()
                self.monitor.flush()
            except Exception as exc:  # noqa: BLE001
                self.log.debug("Network monitor shutdown issue: %s", exc)
        if self.context is not None:
            try:
                await self.artifacts.save_storage_state(self.context, self.profile.context_id)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("Storage state save failed: %s", exc)
            try:
                trace_path = await self.artifacts.stop_trace(self.context, self.profile.context_id, discard=not failed)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("Trace finalisation failed: %s", exc)
            try:
                await self.context.close()
            except Exception as exc:  # noqa: BLE001
                self.log.debug("Context close failed: %s", exc)
        if self._har_path is not None:
            try:
                self.artifacts.redact_har(self._har_path)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("HAR redaction failed: %s", exc)
        self.proxy_pool.release(self.profile.context_id, preserve_assignment=True)
        return trace_path

    # ==============================================================================
    # target execution
    # ==============================================================================
    async def run_rows(self, rows: Sequence[TargetRow]) -> list[TargetRunResult]:
        """Execute every row inside this context (with retries and per-row isolation)."""
        results: list[TargetRunResult] = []
        iterations = max(1, self.config.iterations)
        for iteration in range(1, iterations + 1):
            for row in rows:
                if self.hooks.should_stop is not None and self.hooks.should_stop():
                    self.log.warning("Stop requested - aborting remaining %d target(s)", len(rows) - rows.index(row))
                    return results
                prepared = self._prepare_row(row, iteration)
                if prepared is None:
                    continue
                if self.hooks.on_target_start is not None:
                    self.hooks.on_target_start(self.profile.context_id, prepared, iteration)
                result = await self.run_target(prepared, iteration)
                results.append(result)
                self.runs.append(result)
                if self.hooks.on_target_end is not None:
                    self.hooks.on_target_end(self.profile.context_id, result)
                if self.hooks.rate_limiter is not None:
                    try:
                        await self.hooks.rate_limiter.acquire()
                    except Exception:  # noqa: BLE001
                        pass
                if not result.ok and self.config.fail_fast:
                    self.log.error("fail-fast enabled → stopping this context after a failure")
                    return results
                # Recycle the page periodically to bound memory usage (10+ contexts add up).
                self.pages_served += 1
                if self.config.context_restart_pages and self.pages_served % self.config.context_restart_pages == 0:
                    await self._recycle_page()
        return results

    def _prepare_row(self, row: TargetRow, iteration: int) -> Optional[TargetRow]:
        """Resolve placeholders and apply per-context limits before running a row."""
        mapping = {
            "run_id": self.artifacts.run_id,
            "context_id": self.profile.context_id,
            "row_index": row.index,
            "iteration": iteration,
            "index": self.profile.index,
            "email": row.form_data.get("email") or row.form_data.get("e_posta") or "",
            "user": row.form_data.get("username") or row.form_data.get("kullanici_adi") or "",
        }
        try:
            return interpolate_row(row, mapping)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Could not interpolate row %s placeholders: %s", row.index, exc)
            return row

    async def run_target(self, row: TargetRow, iteration: int = 1) -> TargetRunResult:
        """Run one row with retries, screenshots and artifact capture."""
        attempts = max(1, self.config.retries + 1)
        last_exception: Optional[BaseException] = None
        result: Optional[TargetRunResult] = None

        for attempt in range(1, attempts + 1):
            result = TargetRunResult(
                context_id=self.profile.context_id,
                row_index=row.index,
                target_url=row.target_url,
                iteration=iteration,
                attempt=attempt,
                scenario=self._resolve_scenario(row),
                proxy=self.proxy.masked() if self.proxy else None,
                device=self.profile.device.name if self.profile.device else None,
                metadata={"row_name": row.name, "tags": row.tags},
            )
            stopwatch = Stopwatch()
            stopwatch.__enter__()
            try:
                await self._execute(row, result)
                stopwatch.stop()
                result.duration_ms = round(stopwatch.elapsed_ms, 1)
                result.finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
                if result.status == STATUS_OK:
                    self.log.info(
                        "✔ [%s] %s (%.0f ms, %d request(s), %s in)",
                        row.name or row.index,
                        truncate(row.target_url, 90),
                        result.duration_ms,
                        result.network_metrics.get("requests", 0),
                        result.network_metrics.get("bytes_in_human", "0 B"),
                    )
                    return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - converted into a failed result
                stopwatch.stop()
                last_exception = exc
                result.status = STATUS_FAILED
                result.error = f"{type(exc).__name__}: {exc}"
                result.error_type = type(exc).__name__
                result.duration_ms = round(stopwatch.elapsed_ms, 1)
                result.finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
                retryable = is_retryable(exc)
                self.log.error(
                    "✘ [%s] %s failed after %s (%s)%s",
                    row.name or row.index,
                    truncate(row.target_url, 80),
                    human_ms(result.duration_ms),
                    truncate(str(exc), 200),
                    "" if retryable else " [not retryable]",
                )

            # Failure handling: screenshot, HTML, bundle, proxy feedback.
            await self._capture_failure_artifacts(result, row)
            if self.proxy is not None and self._looks_like_proxy_failure(last_exception):
                self.proxy_pool.report_failure(self.proxy, str(last_exception))
                rotated = await self._rotate_proxy()
                if rotated:
                    self.log.warning("Retrying with a different proxy: %s", rotated.masked())
            elif self.proxy is not None and result.status == STATUS_OK:
                self.proxy_pool.report_success(self.proxy)

            should_retry = (
                attempt < attempts
                and last_exception is not None
                and (is_retryable(last_exception) or not self.config.retry_only_retryable)
            )
            if not should_retry:
                break
            delay = min(
                self.config.retry_backoff_max_s,
                self.config.retry_backoff_s * (2 ** (attempt - 1)),
            ) + random.uniform(0, 0.75)
            self.log.warning(
                "Retry %d/%d in %.1fs (%s)", attempt, attempts - 1, delay, type(last_exception).__name__
            )
            await asyncio.sleep(delay)

        assert result is not None
        if last_exception is not None:
            result.metadata["last_error"] = f"{type(last_exception).__name__}: {truncate(str(last_exception), 400)}"
        result.metadata["attempts"] = result.attempt
        return result

    # ------------------------------------------------------------------ scenario dispatch
    def _resolve_scenario(self, row: TargetRow) -> str:
        """Decide which scenario to run for a row."""
        if row.steps:
            return "custom-steps"
        configured = (self.config.scenario or "auto").lower()
        if configured != "auto":
            return configured
        if row.scenario and row.scenario not in {"auto", ""}:
            return row.scenario
        if row.requires_email_verification:
            return "email-verify"
        has_form_data = any(str(value).strip() for value in row.form_data.values())
        if has_form_data:
            return "form-submit"
        return "load-test"

    async def _execute(self, row: TargetRow, result: TargetRunResult) -> None:
        """Scenario dispatcher - every branch records steps into *result*."""
        try:
            await self._execute_scenario(row, result)
        except CaptchaDetectedError as exc:
            if not row.metadata.get("expect_error"):
                raise
            # Negative test: a bot-challenge page was the expected outcome.
            self.log.info("🧪 expected CAPTCHA/challenge detected (negative test)")
            result.metadata["captcha_expected"] = True
            result.steps.append(
                StepResult(name="captcha", status=STATUS_OK, details={"expected": True, "markers": exc.details})
            )
            page = self.page
            if page is not None and not page.is_closed():
                await self._finalize(page, row, result)

    async def _execute_scenario(self, row: TargetRow, result: TargetRunResult) -> None:
        """The actual scenario orchestration (see :meth:`_execute`)."""
        page = await self._ensure_page()
        scenario = result.scenario

        # 1) Always navigate first (except when a custom workflow starts with its own goto).
        first_step_is_goto = bool(row.steps) and row.steps[0].action == "goto"
        if not first_step_is_goto:
            await self._step_navigate(page, row, result)

        # 2) Custom declarative workflow takes over when present.
        if row.steps:
            await self._run_declarative_steps(page, row, result)
        elif scenario == "smoke":
            await self._step_smoke(page, row, result)
        elif scenario == "api-discovery":
            await self._step_api_discovery(page, row, result)
        elif scenario in {"form-submit", "email-verify", "full-journey"}:
            await self._step_form_flow(page, row, result)
        elif scenario in {"load-test"}:
            await self._step_measure(page, row, result)
        else:
            await self._step_form_flow(page, row, result)

        # 3) Verification mail (only when the row asks for it and IMAP is configured).
        if row.requires_email_verification or scenario in {"email-verify"}:
            await self._step_email_verification(page, row, result)

        # 4) Final snapshot + page metadata.
        await self._finalize(page, row, result)

    async def _ensure_page(self) -> Page:
        """Return a healthy page (recreating it if the previous one died)."""
        if self.context is None or self._closed:
            raise ContextClosedError(f"Context {self.profile.context_id} is closed")
        try:
            if self.page is None or self.page.is_closed():
                self.page = await self.context.new_page()
                if self.monitor is not None:
                    await self.monitor.attach_page(self.page)
        except PlaywrightError as exc:
            raise ContextClosedError(f"Could not open a page in {self.profile.context_id}: {exc}") from exc
        return self.page

    # ------------------------------------------------------------------ navigation
    async def _step_navigate(self, page: Page, row: TargetRow, result: TargetRunResult) -> dict[str, Any]:
        """Navigate to the target URL with retries, WAF detection and timing metrics."""
        step = StepResult(name="navigate")
        stopwatch = Stopwatch()
        stopwatch.__enter__()
        details: dict[str, Any] = {}
        response = None

        for attempt in range(1, 4):
            try:
                response = await page.goto(
                    row.target_url,
                    wait_until="domcontentloaded",
                    timeout=self.config.navigation_timeout_ms,
                    referer="https://www.google.com/" if self.config.humanize and attempt == 1 else None,
                )
                break
            except PlaywrightTimeoutError as exc:
                if attempt >= 3:
                    stopwatch.stop()
                    step.status = STATUS_FAILED
                    step.error = f"navigation timeout after {self.config.navigation_timeout_ms} ms"
                    step.error_type = "PlaywrightTimeoutError"
                    step.duration_ms = round(stopwatch.elapsed_ms, 1)
                    result.steps.append(step)
                    raise TargetUnreachableError(f"Timeout while loading {row.target_url}: {exc}") from exc
                self.log.warning(
                    "Navigation timeout (attempt %d/3) for %s - retrying", attempt, truncate(row.target_url, 70)
                )
                await asyncio.sleep(1.5 * attempt)
            except PlaywrightError as exc:
                stopwatch.stop()
                step.status = STATUS_FAILED
                step.error = str(exc)
                step.error_type = type(exc).__name__
                step.duration_ms = round(stopwatch.elapsed_ms, 1)
                result.steps.append(step)
                message = str(exc).lower()
                if any(token in message for token in ("err_proxy", "err_tunnel", "err_socks", "407", "proxy")):
                    raise TargetUnreachableError(f"Proxy connection failed for {row.target_url}: {exc}") from exc
                if any(token in message for token in ("err_name_not_resolved", "err_internet_disconnected", "err_connection")):
                    raise TargetUnreachableError(f"Could not reach {row.target_url}: {exc}") from exc
                raise NavigationError(f"Navigation failed for {row.target_url}: {exc}") from exc

        stopwatch.stop()
        status = response.status if response is not None else None
        details.update(
            {
                "http_status": status,
                "url": page.url,
                "redirected": page.url.rstrip("/") != row.target_url.rstrip("/"),
                "duration_ms": round(stopwatch.elapsed_ms, 1),
            }
        )
        self.log.info(
            "🌐 GET %s → %s (%s)", truncate(row.target_url, 90), status or "n/a", human_ms(stopwatch.elapsed_ms)
        )

        # Bot-management / WAF challenge detection on the response itself.
        vendor = self._detect_block_vendor(response, page.url)
        if vendor and status in {403, 429, 503}:
            step.status = STATUS_FAILED
            step.error = f"blocked by {vendor} (HTTP {status})"
            step.error_type = "BlockedByEdgeError"
            step.duration_ms = round(stopwatch.elapsed_ms, 1)
            step.screenshot = await self.artifacts.screenshot(page, self.profile.context_id, "blocked", on_failure=True)
            result.steps.append(step)
            result.screenshots.append(step.screenshot) if step.screenshot else None
            raise BlockedByEdgeError(
                f"Blocked by {vendor} edge protection (HTTP {status}) on {page.url}", status=status, vendor=vendor
            )

        await self._settle(page)
        step.details = details
        step.duration_ms = round(stopwatch.elapsed_ms, 1)
        step.screenshot = await self.artifacts.screenshot(page, self.profile.context_id, "01-loaded")
        if step.screenshot:
            result.screenshots.append(step.screenshot)
        result.steps.append(step)
        self._notify_step("navigate", step)

        if self.config.verify_stealth:
            await self.stealth.verify(page, self.profile, strict=False)

        captcha = await self.filler.detect_captcha(page)
        if captcha:
            action = captcha_action(self.config, captcha)
            if action == "skip":
                raise CaptchaDetectedError(
                    "CAPTCHA detected on the landing page and --captcha-action=skip",
                    details={"markers": [item["selector"] for item in captcha]},
                )
        return details

    async def _settle(self, page: Page, *, timeout_ms: Optional[int] = None) -> None:
        """Wait for the page to become quiet (bounded, never fatal)."""
        timeout = timeout_ms or min(self.config.navigation_timeout_ms, 15_000)
        try:
            await page.wait_for_load_state("load", timeout=timeout)
        except PlaywrightTimeoutError:
            self.log.debug("'load' state not reached within %d ms (continuing)", timeout)
        except PlaywrightError as exc:
            self.log.debug("wait_for_load_state failed: %s", exc)
        if self.config.humanize:
            await asyncio.sleep(random.uniform(0.15, 0.45))
        else:
            await asyncio.sleep(max(0.0, self.config.action_pause_ms / 1000.0))

    def _detect_block_vendor(self, response: Any, url: str) -> Optional[str]:
        """Identify common WAF/bot-management vendors from headers/cookies/URL."""
        try:
            headers = {str(k).lower(): str(v).lower() for k, v in (response.headers if response else {}).items()}
        except Exception:  # noqa: BLE001
            headers = {}
        haystack = " ".join([url.lower(), " ".join(f"{k}:{v}" for k, v in headers.items())])
        if "captcha" in haystack or "challenge" in url.lower():
            return "challenge-page"
        for vendor, markers in BLOCK_VENDOR_MARKERS.items():
            if any(marker in haystack for marker in markers):
                return vendor
        return None

    # ------------------------------------------------------------------ scenarios
    async def _step_smoke(self, page: Page, row: TargetRow, result: TargetRunResult) -> None:
        """Smoke test: title + HTTP status + no console errors."""
        step = StepResult(name="smoke")
        stopwatch = Stopwatch()
        stopwatch.__enter__()
        title = normalize_ws(await page.title())
        metrics = result.network_metrics
        details = {
            "title": truncate(title, 120),
            "requests": metrics.get("requests", 0),
            "console_errors": metrics.get("console_errors", 0),
        }
        if not title:
            step.status = STATUS_FAILED
            step.error = "page has an empty title (possible soft-404 or blocked response)"
        stopwatch.stop()
        step.details = details
        step.duration_ms = round(stopwatch.elapsed_ms, 1)
        result.steps.append(step)
        result.final_title = title
        if step.status == STATUS_FAILED:
            raise NavigationError(step.error or "smoke check failed")
        self._notify_step("smoke", step)
        self.log.info("🔎 smoke ok: '%s' (%d request(s))", truncate(title, 70), details["requests"])

    async def _step_measure(self, page: Page, row: TargetRow, result: TargetRunResult) -> None:
        """Collect Core Web Vitals-ish timings used by load testing."""
        step = StepResult(name="performance")
        stopwatch = Stopwatch()
        stopwatch.__enter__()
        try:
            timings = await page.evaluate(
                """() => {
                    const nav = performance.getEntriesByType('navigation')[0] || {};
                    const paint = performance.getEntriesByType('paint') || [];
                    const fcp = paint.find((p) => p.name === 'first-contentful-paint');
                    const resources = performance.getEntriesByType('resource') || [];
                    return {
                      domContentLoaded: nav.domContentLoadedEventEnd || 0,
                      loadEvent: nav.loadEventEnd || 0,
                      ttfb: nav.responseStart || 0,
                      transferSize: nav.transferSize || 0,
                      encodedBodySize: nav.encodedBodySize || 0,
                      resourceCount: resources.length,
                      resourceBytes: resources.reduce((sum, r) => sum + (r.transferSize || 0), 0),
                      fcp: fcp ? fcp.startTime : null,
                    };
                }"""
            )
        except Exception as exc:  # noqa: BLE001
            timings = {}
            self.log.debug("performance metrics unavailable: %s", exc)

        stopwatch.stop()
        step.details = {"timings": timings, "measure_duration_ms": round(stopwatch.elapsed_ms, 1)}
        step.duration_ms = round(stopwatch.elapsed_ms, 1)
        result.steps.append(step)
        result.metadata["timings"] = timings
        self._notify_step("performance", step)
        self.log.info(
            "⏱  ttfb=%.0f ms  dom=%.0f ms  load=%.0f ms  resources=%s",
            timings.get("ttfb", 0),
            timings.get("domContentLoaded", 0),
            timings.get("loadEvent", 0),
            timings.get("resourceCount", "n/a"),
        )

    async def _step_api_discovery(self, page: Page, row: TargetRow, result: TargetRunResult) -> None:
        """Wait for XHR/fetch traffic and summarise the endpoints seen on this page."""
        step = StepResult(name="api-discovery")
        stopwatch = Stopwatch()
        stopwatch.__enter__()
        try:
            await page.wait_for_load_state("networkidle", timeout=min(self.config.navigation_timeout_ms, 20_000))
        except PlaywrightTimeoutError:
            self.log.debug("networkidle not reached; using collected traffic so far")
        await self._settle(page, timeout_ms=4000)

        monitor = self.monitor
        endpoints = sorted(monitor.stats.api_candidates) if monitor else []
        apis_from_page = []
        try:
            apis_from_page = await page.evaluate(
                """() => {
                    const found = new Set();
                    const push = (value) => { if (value && typeof value === 'string') found.add(value); };
                    try {
                      const entries = performance.getEntriesByType('resource') || [];
                      entries.forEach((entry) => {
                        if (entry.initiatorType === 'xmlhttprequest' || entry.initiatorType === 'fetch') push(entry.name);
                      });
                    } catch (e) {}
                    try {
                      if (window.performance && performance.getEntriesByType('resource')) {}
                      if (window.axios && window.axios.defaults && window.axios.defaults.baseURL) push(window.axios.defaults.baseURL);
                      if (window.__API_URL__) push(window.__API_URL__);
                      for (const key of Object.keys(window)) {
                        const value = window[key];
                        if (typeof value === 'string' && /^https?:\\/\\//.test(value) && /api|graphql|rest/i.test(value)) push(value);
                      }
                    } catch (e) {}
                    return Array.from(found).slice(0, 50);
                }"""
            )
        except Exception as exc:  # noqa: BLE001
            self.log.debug("API discovery evaluate failed: %s", exc)

        stopwatch.stop()
        details = {
            "endpoints": endpoints[:100],
            "endpoint_count": len(endpoints),
            "page_level_apis": apis_from_page,
        }
        step.details = details
        step.duration_ms = round(stopwatch.elapsed_ms, 1)
        result.steps.append(step)
        result.api_endpoints = endpoints[:100]
        self.log.info("🛰  discovered %d API endpoint(s) on this page", len(endpoints))
        for endpoint in endpoints[:25]:
            self.log.info("    • %s", endpoint)
        self._notify_step("api-discovery", step)

    async def _step_form_flow(self, page: Page, row: TargetRow, result: TargetRunResult) -> None:
        """Fill the form, submit it and interpret the outcome."""
        has_data = any(str(value).strip() for value in row.form_data.values())
        if not has_data:
            self.log.info("No form data for this row → measuring page instead")
            await self._step_measure(page, row, result)
            return

        # Ensure a form is actually present; some SPAs need a click first (see --steps).
        fields = await self.filler.scan(page)
        if not fields:
            paths = ", ".join(SELECTOR_TEMPLATES["submit"][:2])
            self.log.warning(
                "No form fields detected on %s - the form may be behind a login/iframe; use --steps or a "
                "selectors file (hint: '%s')",
                truncate(page.url, 80),
                paths,
            )
            result.steps.append(
                StepResult(
                    name="form-scan",
                    status=STATUS_SKIPPED,
                    details={"reason": "no form fields found", "url": page.url},
                )
            )
            return

        scan_step = StepResult(
            name="form-scan",
            status=STATUS_OK,
            details={"fields": [f.to_dict() for f in fields], "count": len(fields)},
        )
        result.steps.append(scan_step)
        self._notify_step("form-scan", scan_step)

        fill_step = StepResult(name="form-fill")
        stopwatch = Stopwatch()
        stopwatch.__enter__()
        try:
            report: FillReport = await self.filler.fill_form_for_row(page, row)
        except FormAutomationError as exc:
            stopwatch.stop()
            fill_step.status = STATUS_FAILED
            fill_step.error = str(exc)
            fill_step.error_type = type(exc).__name__
            fill_step.duration_ms = round(stopwatch.elapsed_ms, 1)
            result.steps.append(fill_step)
            raise
        stopwatch.stop()
        fill_step.details = report.to_dict()
        fill_step.duration_ms = round(stopwatch.elapsed_ms, 1)
        fill_step.screenshot = await self.artifacts.screenshot(page, self.profile.context_id, "02-filled")
        if fill_step.screenshot:
            result.screenshots.append(fill_step.screenshot)
        result.fill_report = report
        notable = report.total > 0 or not report.unmatched
        fill_step.status = STATUS_OK if notable else STATUS_FAILED
        if not notable:
            fill_step.error = f"no field could be matched: {', '.join(report.unmatched)}"
        result.steps.append(fill_step)
        self._notify_step("form-fill", fill_step)
        if not notable:
            raise FormAutomationError(fill_step.error)

        if row.metadata.get("submit") is False or not self.config.submit_default:
            self.log.info("Submission skipped (submit disabled) - form left filled")
            result.steps.append(StepResult(name="submit", status=STATUS_SKIPPED, details={"reason": "disabled"}))
            return

        submit_step = await self.filler.submit(page, row)
        result.steps.append(submit_step)
        self._notify_step("submit", submit_step)
        await self._settle(page, timeout_ms=min(self.config.outcome_timeout_ms, 8000))
        if self.config.humanize:
            await asyncio.sleep(random.uniform(0.4, 1.1))

        outcome = await self.filler.wait_for_outcome(page, row)
        expect_error = bool(row.metadata.get("expect_error"))
        outcome_status = outcome.get("status")
        if expect_error and outcome_status == "error":
            # Negative test: the server was *supposed* to reject the submission.
            outcome_step_status = STATUS_OK
            outcome["expected_error"] = True
        elif expect_error and outcome_status == "success":
            outcome_step_status = STATUS_FAILED
            outcome["expected_error"] = False
        else:
            outcome_step_status = (
                STATUS_OK if outcome_status == "success" else (STATUS_SKIPPED if outcome_status == "unknown" else STATUS_FAILED)
            )
        outcome_step = StepResult(
            name="outcome",
            status=outcome_step_status,
            details=outcome,
            duration_ms=outcome.get("duration_ms", 0.0),
        )
        outcome_step.screenshot = await self.artifacts.screenshot(page, self.profile.context_id, "03-outcome")
        if outcome_step.screenshot:
            result.screenshots.append(outcome_step.screenshot)
        if outcome.get("status") == "captcha":
            captcha_action(self.config, [{"selector": marker} for marker in outcome.get("markers", [])] or [{"selector": "captcha"}])
        result.steps.append(outcome_step)
        self._notify_step("outcome", outcome_step)

        status = outcome.get("status")
        if status == "error":
            message = outcome.get("message") or outcome.get("reason") or "form rejected by the server"
            if expect_error:
                self.log.info(
                    "🧪 expected rejection received (negative test): %s", truncate(str(message), 160)
                )
                return
            raise FormAutomationError(f"Form submission rejected: {truncate(str(message), 300)}")
        if status == "success" and expect_error:
            raise FormAutomationError(
                "negative test failed: the submission succeeded although an error response was expected"
            )
        if status == "unknown" and self.config.success_url_regex and not self.config.submit_default:
            raise OutcomeTimeoutError(outcome.get("reason", "no outcome detected"))
        if status == "unknown":
            self.log.warning("Outcome unknown for %s - treating as success but flagging it", row.name)

        # Post-submit sanity check: did the page silently reset the form?
        if self.config.detect_captcha:
            captcha = await self.filler.detect_captcha(page)
            if captcha:
                captcha_action(self.config, captcha)

    async def _run_declarative_steps(self, page: Page, row: TargetRow, result: TargetRunResult) -> None:
        """Execute a row's declarative workflow (JSON/Excel ``steps`` column)."""
        for index, action in enumerate(row.steps, start=1):
            step_name = f"step{index}:{action.action}"
            step = StepResult(name=step_name)
            stopwatch = Stopwatch()
            stopwatch.__enter__()
            try:
                detail = await self._run_single_step(page, row, action)
                step.details = detail if isinstance(detail, dict) else {"value": detail}
                step.status = STATUS_OK
            except Exception as exc:  # noqa: BLE001
                step.status = STATUS_SKIPPED if action.optional else STATUS_FAILED
                step.error = f"{type(exc).__name__}: {exc}"
                step.error_type = type(exc).__name__
                stopwatch.stop()
                step.duration_ms = round(stopwatch.elapsed_ms, 1)
                result.steps.append(step)
                self._notify_step(step_name, step)
                if action.optional:
                    self.log.warning("Optional step %s failed: %s", step_name, truncate(str(exc), 160))
                    continue
                raise WorkflowError(f"Step '{step_name}' failed: {exc}") from exc
            stopwatch.stop()
            step.duration_ms = round(stopwatch.elapsed_ms, 1)
            result.steps.append(step)
            self._notify_step(step_name, step)

    async def _run_single_step(self, page: Page, row: TargetRow, action: StepAction) -> Any:
        """Execute one declarative step and return its details."""
        name = (action.action or "").lower()
        target = action.target
        value = action.value
        timeout = action.timeout_ms or self.config.default_timeout_ms

        if name == "goto":
            url = str(value or target or row.target_url)
            response = await page.goto(url, timeout=self.config.navigation_timeout_ms, wait_until="domcontentloaded")
            await self._settle(page)
            return {"status": response.status if response else None, "url": page.url}
        if name == "reload":
            await page.reload(timeout=self.config.navigation_timeout_ms)
            return {"url": page.url}
        if name == "go_back":
            await page.go_back(timeout=self.config.navigation_timeout_ms)
            return {"url": page.url}
        if name in {"fill", "type"}:
            assert target, "fill/type steps need a target selector"
            locator = page.locator(target).first
            text = self._interpolate_value(value, row)
            if name == "type":
                await locate_focus(locator)
                await page.keyboard.type(text)
            else:
                await locator.fill(text, timeout=timeout)
            return {"selector": target, "value": truncate(text, 80)}
        if name in {"click", "dblclick", "hover"}:
            assert target, "click steps need a target selector"
            locator = page.locator(target).first
            if name == "click":
                await locator.click(timeout=timeout)
            elif name == "dblclick":
                await locator.dblclick(timeout=timeout)
            else:
                await locator.hover(timeout=timeout)
            await self._maybe_wait_for_navigation(page)
            return {"selector": target, "url": page.url}
        if name == "press":
            keys = str(value or target or "Enter")
            if target and target not in {"body", "page"}:
                await page.locator(target).first.press(keys, timeout=timeout)
            else:
                await page.keyboard.press(keys)
            await self._maybe_wait_for_navigation(page)
            return {"keys": keys}
        if name in {"check", "uncheck"}:
            assert target, "check steps need a selector"
            locator = page.locator(target).first
            if name == "check":
                await locator.check(timeout=timeout)
            else:
                await locator.uncheck(timeout=timeout)
            return {"selector": target}
        if name in {"select", "select_option"}:
            assert target, "select steps need a selector"
            await page.locator(target).first.select_option(str(value), timeout=timeout)
            return {"selector": target, "value": value}
        if name in {"upload", "set_input_files"}:
            assert target, "upload steps need a selector"
            paths = [str(Path(token.strip()).expanduser()) for token in re.split(r"[;|]", str(value)) if token.strip()]
            await page.locator(target).first.set_input_files(paths, timeout=timeout)
            return {"selector": target, "files": paths}
        if name in {"wait", "sleep"}:
            seconds = float(value) if value is not None else 1.0
            await asyncio.sleep(seconds)
            return {"slept_s": seconds}
        if name == "wait_for_selector":
            assert target, "wait_for_selector needs a target"
            state = action.options.get("state", "visible")
            await page.wait_for_selector(target, state=state, timeout=timeout)
            return {"selector": target, "state": state}
        if name == "wait_for_url":
            pattern = str(value or target)
            await page.wait_for_url(re.compile(pattern) if pattern.startswith("^") else pattern, timeout=timeout)
            return {"url": page.url}
        if name == "wait_for_load_state":
            state = str(value or action.options.get("state") or "load")
            await page.wait_for_load_state(state, timeout=timeout)
            return {"state": state}
        if name == "wait_for_response":
            pattern = str(value or target)
            response = await page.wait_for_response(pattern, timeout=timeout)
            return {"url": response.url, "status": response.status}
        if name in {"expect_text", "assert_text"}:
            expected = str(value)
            locator = page.locator(target).first if target else page.locator("body")
            content = normalize_ws(await locator.inner_text(timeout=timeout))
            assert expected.lower() in content.lower(), f"expected text '{expected}' not found"
            return {"found": expected}
        if name == "expect_visible":
            assert target, "expect_visible needs a selector"
            visible = await page.locator(target).first.is_visible()
            assert visible, f"selector '{target}' is not visible"
            return {"selector": target, "visible": True}
        if name == "expect_url":
            pattern = str(value or target)
            current = page.url
            matched = bool(re.search(pattern, current))
            assert matched, f"url '{current}' does not match '{pattern}'"
            return {"url": current}
        if name == "screenshot":
            label = str(value or target or "step")
            path = await self.artifacts.screenshot(page, self.profile.context_id, label)
            return {"path": path}
        if name == "evaluate":
            options = action.options or {}
            script = target if options.get("source") == "selector" else str(value or target)
            assert script, "evaluate needs a JS expression in target/value"
            result = await page.evaluate(script)
            return {"result": truncate(json.dumps(result, default=str), 400)}
        if name == "scroll":
            amount = int(value or 1200)
            await page.mouse.wheel(0, amount)
            await asyncio.sleep(0.3)
            return {"scrolled": amount}
        if name == "frame_fill":
            frame_selector, _, field_selector = str(target or "").partition("|")
            frame = page.frame_locator(frame_selector)
            await frame.locator(field_selector or "input").first.fill(str(value), timeout=timeout)
            return {"frame": frame_selector, "field": field_selector}
        if name == "frame_click":
            frame_selector, _, field_selector = str(target or "").partition("|")
            await page.frame_locator(frame_selector).locator(field_selector or "button").first.click(timeout=timeout)
            return {"frame": frame_selector, "field": field_selector}
        if name == "new_tab":
            new_page = await page.context.new_page()
            if self.monitor is not None:
                await self.monitor.attach_page(new_page)
            if value:
                await new_page.goto(str(value), timeout=self.config.navigation_timeout_ms)
            return {"opened": len(page.context.pages)}
        if name == "close_tab":
            await page.close()
            return {"closed": True}

        raise WorkflowError(f"Unsupported step action '{action.action}'. Supported: {sorted(StepAction.SUPPORTED)}")

    async def _maybe_wait_for_navigation(self, page: Page) -> None:
        """Give a click the chance to trigger navigation without failing when it doesn't."""
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=3000)
        except PlaywrightTimeoutError:
            pass
        await asyncio.sleep(random.uniform(0.1, 0.3) if self.config.humanize else 0.05)

    def _interpolate_value(self, value: Any, row: TargetRow) -> str:
        """Resolve ``{placeholders}`` and ``{column}`` references inside a step value."""
        if value is None:
            return ""
        text = str(value)
        mapping = {
            **{k: v for k, v in row.form_data.items()},
            "row_index": row.index,
            "row_name": row.name or "",
            "iteration": row.metadata.get("iteration", 1),
        }
        return str(interpolate(text, mapping))

    # ------------------------------------------------------------------ e-mail verification
    async def _step_email_verification(self, page: Page, row: TargetRow, result: TargetRunResult) -> VerificationResult:
        """IMAP: wait for the verification mail, then use its link or OTP."""
        verification = VerificationResult(attempted=True)
        step = StepResult(name="email-verification")
        stopwatch = Stopwatch()
        stopwatch.__enter__()

        if self.imap_client is None:
            verification.error = "IMAP is not configured/disabled"
            verification.method = "skipped"
            step.status = STATUS_SKIPPED
            step.details = {"reason": verification.error}
            stopwatch.stop()
            step.duration_ms = round(stopwatch.elapsed_ms, 1)
            result.steps.append(step)
            result.verification = verification
            self.log.warning("Email verification requested but IMAP is not configured - skipping")
            return verification

        target_email = row.verification_email
        if not target_email and row.verification_email_field:
            target_email = row.form_data.get(row.verification_email_field)
        if not target_email:
            for candidate_key in ("email", "e_posta", "eposta", "mail", "email_address"):
                if row.form_data.get(candidate_key):
                    target_email = row.form_data[candidate_key]
                    break

        since = datetime.now(timezone.utc) - timedelta(seconds=25)
        try:
            extracted = await self.imap_client.wait_for_verification(
                since=since,
                timeout_s=row.verification_timeout_s or self.config.imap_timeout_s,
                target_email=target_email,
                subject_regex=row.verification_subject_regex or self.config.imap_subject_regex,
                sender_filter=row.verification_sender or self.config.imap_sender_filter,
                link_regex=row.verification_link_regex or self.config.imap_link_regex,
                prefer_same_domain=row.target_url if self.config.imap_link_must_match_target else None,
            )
        except ImapTimeoutError as exc:
            verification.error = str(exc)
            verification.method = "timeout"
            stopwatch.stop()
            verification.waited_ms = round(stopwatch.elapsed_ms, 1)
            step.status = STATUS_FAILED
            step.error = verification.error
            step.error_type = "ImapTimeoutError"
            step.duration_ms = verification.waited_ms
            result.steps.append(step)
            result.verification = verification
            raise VerificationError(f"Verification e-mail never arrived: {exc}") from exc
        except ImapError as exc:
            verification.error = str(exc)
            verification.method = "error"
            stopwatch.stop()
            verification.waited_ms = round(stopwatch.elapsed_ms, 1)
            step.status = STATUS_FAILED
            step.error = verification.error
            step.error_type = type(exc).__name__
            step.duration_ms = verification.waited_ms
            result.steps.append(step)
            result.verification = verification
            raise

        verification.matched_subject = extracted.message.subject
        verification.matched_sender = extracted.message.sender
        verification.matched_date = extracted.message.date.isoformat() if extracted.message.date else None
        verification.link = extracted.link
        verification.otp_code = extracted.otp
        verification.method = extracted.method
        verification.details = {"reason": extracted.reason, "candidates": extracted.candidates[:10]}

        try:
            if extracted.link:
                await self._follow_verification_link(page, row, extracted.link, result)
            if extracted.otp:
                await self._apply_otp(page, row, extracted.otp)
            verification.success = True
        except Exception as exc:  # noqa: BLE001
            verification.error = f"{type(exc).__name__}: {exc}"
            stopwatch.stop()
            verification.waited_ms = round(stopwatch.elapsed_ms, 1)
            step.status = STATUS_FAILED
            step.error = verification.error
            step.duration_ms = verification.waited_ms
            result.steps.append(step)
            result.verification = verification
            raise VerificationError(f"Verification step failed: {exc}") from exc

        stopwatch.stop()
        verification.waited_ms = round(stopwatch.elapsed_ms, 1)
        step.status = STATUS_OK
        step.details = verification.to_dict()
        step.duration_ms = verification.waited_ms
        step.screenshot = await self.artifacts.screenshot(page, self.profile.context_id, "04-verified")
        if step.screenshot:
            result.screenshots.append(step.screenshot)
        result.steps.append(step)
        result.verification = verification
        self._notify_step("email-verification", step)
        self.log.info("📧 verification %s accepted '%s'", verification.method, truncate(verification.matched_subject or "", 60))
        return verification

    async def _follow_verification_link(self, page: Page, row: TargetRow, link: str, result: TargetRunResult) -> None:
        """Open the verification link (in a new tab, then adopt it) and settle the page."""
        self.log.info("🔗 Following verification link: %s", truncate(self.redactor.text(link, limit=200), 160))
        new_page = None
        try:
            new_page = await page.context.new_page()
            if self.monitor is not None:
                await self.monitor.attach_page(new_page)
            response = await new_page.goto(link, wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
            status = response.status if response else None
            await self._settle(new_page, timeout_ms=min(self.config.navigation_timeout_ms, 12_000))
            screenshot = await self.artifacts.screenshot(new_page, self.profile.context_id, "04-verification-link")
            if screenshot:
                result.screenshots.append(screenshot)
            result.steps.append(
                StepResult(
                    name="verification-link",
                    status=STATUS_OK if status is None or status < 400 else STATUS_FAILED,
                    details={"url": new_page.url, "status": status},
                )
            )
            # Adopt the tab: subsequent steps (OTP entry, final snapshot) run there.
            if self.page is not None and not self.page.is_closed() and new_page.url != self.page.url:
                try:
                    await self.page.close()
                except Exception:  # noqa: BLE001
                    pass
            self.page = new_page
        except PlaywrightError as exc:
            raise VerificationError(f"Could not open the verification link: {exc}") from exc

    async def _apply_otp(self, page: Page, row: TargetRow, otp: str) -> None:
        """Type the OTP into the verification form on the current page."""
        fields = await self.filler.scan(page)
        otp_field = None
        if row.verification_otp_field:
            otp_field = next(
                (
                    f
                    for f in fields
                    if row.verification_otp_field.lower() in (f.name + f.element_id + f.placeholder + f.label + f.aria_label).lower()
                ),
                None,
            )
        if otp_field is None:
            otp_field = next((f for f in fields if f.kind in {"otp", "text", "number"} and f.visible), None)
        if otp_field is None:
            self.log.warning("OTP received but no input field found on the verification page")
            return
        locator = page.locator(otp_field.selector).first
        await locator.fill(otp, timeout=self.config.default_timeout_ms)
        self.log.info("🔢 OTP entered into %s", otp_field.describe()[:60])
        submit = await self.filler.find_submit(page, row)
        if submit is not None:
            await submit.click(timeout=self.config.default_timeout_ms)
            await self._settle(page, timeout_ms=min(self.config.outcome_timeout_ms, 10_000))

    # ------------------------------------------------------------------ finalisation
    async def _finalize(self, page: Page, row: TargetRow, result: TargetRunResult) -> None:
        """Collect final page metadata, network summary and the success screenshot."""
        try:
            result.final_url = page.url
            result.final_title = normalize_ws(await page.title())
        except Exception:  # noqa: BLE001 - page may be gone
            pass

        monitor = self.monitor
        if monitor is not None:
            result.network_metrics = monitor.summary()
            result.api_endpoints = sorted(monitor.stats.api_candidates)[:200]
            result.network_log_path = str(monitor._network_log_path) if monitor._network_log_path else None
            result.steps.append(monitor.step_result())

        screenshot = await self.artifacts.screenshot(page, self.profile.context_id, "05-final")
        if screenshot:
            result.screenshots.append(screenshot)

        if self.config.save_html_on_failure and result.status != STATUS_OK:
            result.html_dump = await self.artifacts.save_html(page, self.profile.context_id, f"failure-{row.index}")

        result.status = result.status if result.status else STATUS_OK
        result.metadata.setdefault("pages_served", self.pages_served)
        result.metadata.setdefault("block_stats", dict(self._block_stats))
        result.metadata["device_profile"] = self.profile.device.name if self.profile.device else None

    async def _capture_failure_artifacts(self, result: TargetRunResult, row: TargetRow) -> None:
        """Screenshot + HTML dump on failure (called from the retry loop)."""
        page = self.page
        if page is None or page.is_closed():
            return
        try:
            screenshot = await self.artifacts.screenshot(
                page, self.profile.context_id, f"failure-{row.index}-attempt{result.attempt}", on_failure=True
            )
            if screenshot:
                result.screenshots.append(screenshot)
            if self.config.save_html_on_failure:
                result.html_dump = await self.artifacts.save_html(page, self.profile.context_id, f"failure-{row.index}")
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Failure artifact capture failed: %s", exc)

    # ------------------------------------------------------------------ proxy handling
    def _looks_like_proxy_failure(self, exc: Optional[BaseException]) -> bool:
        if exc is None or self.proxy is None:
            return False
        message = str(exc).lower()
        hints = ("err_proxy", "err_tunnel", "err_socks", "proxy", "407", "connection refused", "econnreset", "timeout")
        return any(hint in message for hint in hints)

    async def _rotate_proxy(self) -> Optional[ProxySpec]:
        """Rebuild the context on a fresh proxy (used after proxy-related failures)."""
        if not self.proxy_pool.enabled:
            return None
        old = self.proxy
        self.log.info("Rotating proxy (failed: %s)", old.masked() if old else "direct")
        try:
            await self._rebuild_context(force_new_proxy=True)
        except Exception as exc:  # noqa: BLE001
            self.log.error("Proxy rotation failed: %s", exc)
            return None
        if self.proxy is not None and self.proxy is not old:
            return self.proxy
        return None

    async def _rebuild_context(self, *, force_new_proxy: bool = False) -> None:
        """Close and recreate the context (fresh cookies) keeping the same profile."""
        trace_path = await self.close(failed=True)
        if trace_path:
            self.log.info("Trace saved before context rebuild: %s", Path(trace_path).name)
        if force_new_proxy:
            self.proxy_pool.release(self.profile.context_id, preserve_assignment=False)
        self.proxy = await self.proxy_pool.acquire(context_id=self.profile.context_id, sticky=True)
        self.pages_served = 0
        self._closed = False
        self.context = None
        self.monitor = None
        await self.start()

    async def _recycle_page(self) -> None:
        """Close the current page and open a fresh one (memory hygiene)."""
        try:
            if self.page is not None and not self.page.is_closed():
                await self.page.close()
        except Exception:  # noqa: BLE001
            pass
        self.page = await self._ensure_page()
        if self.monitor is not None:
            await self.monitor.attach_page(self.page)
        self.log.debug("Page recycled (served=%d)", self.pages_served)

    # ------------------------------------------------------------------ misc
    def _notify_step(self, name: str, step: StepResult) -> None:
        if self.hooks.on_step is not None:
            try:
                self.hooks.on_step(self.profile.context_id, step)
            except Exception:  # noqa: BLE001
                self.log.debug("step hook failed")

    def build_context_result(self, status: str = STATUS_OK, error: Optional[str] = None) -> "Any":
        """Assemble a :class:`~waft.models.ContextResult` for reporting."""
        from .models import ContextResult  # local import to avoid a cycle at module import time

        result = ContextResult(
            context_id=self.profile.context_id,
            index=self.profile.index,
            status=status,
            proxy=self.proxy.masked() if self.proxy else None,
            device=self.profile.device.name if self.profile.device else None,
            user_agent=self.profile.user_agent,
            timezone_id=self.profile.timezone_id,
            locale=self.profile.locale,
            storage_state_path=str(self.config.storage_state_dir or self.artifacts.contexts_root / self.profile.context_id)
            if self.config.save_storage_state
            else None,
            runs=list(self.runs),
            error=error,
        )
        result.finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
        return result


class ContextEngineFactory:
    """Builds engines with the shared singletons (browser, pools, artifacts …)."""

    def __init__(
        self,
        config: Config,
        browser: Browser,
        proxy_pool: ProxyPool,
        artifacts: ArtifactManager,
        stealth: StealthLayer,
        *,
        imap_client: Optional[ImapClient] = None,
        hooks: Optional[EngineHooks] = None,
        redactor: Optional[Redactor] = None,
    ) -> None:
        self.config = config
        self.browser = browser
        self.proxy_pool = proxy_pool
        self.artifacts = artifacts
        self.stealth = stealth
        self.imap_client = imap_client
        self.hooks = hooks or EngineHooks()
        self.redactor = redactor or Redactor(enabled=config.redact)

    async def create(self, profile: ContextProfile, proxy: Optional[ProxySpec] = None) -> ContextEngine:
        """Instantiate (but do not start) an engine for *profile*."""
        engine = ContextEngine(
            config=self.config,
            profile=profile,
            browser=self.browser,
            proxy_pool=self.proxy_pool,
            proxy=proxy,
            artifacts=self.artifacts,
            stealth=self.stealth,
            hooks=self.hooks,
            imap_client=self.imap_client,
            redactor=self.redactor,
        )
        return engine


def profile_from_config(config: Config, index: int, *, target_urls: Optional[Sequence[str]] = None) -> ContextProfile:
    """Create a coherent :class:`ContextProfile` for context *index*.

    Device profile, locale, timezone, geolocation and proxy country are aligned so the
    fingerprint never contradicts itself (a Windows UA must not report ``Asia/Tokyo`` when
    the proxy exits in Germany).
    """
    from .config import resolve_geolocation, resolve_timezone
    from .utils import split_csv

    devices = config.device_profiles or []
    device = devices[index % len(devices)] if devices else None
    locales = split_csv(config.locales_spec) or ["tr-TR"]
    locale = locales[index % len(locales)]
    timezone_id = resolve_timezone(config.timezones_spec, locale.split("-")[-1] if "-" in locale else None)
    geolocation = resolve_geolocation(config.geolocation_spec, config.coordinate_country)
    profile = ContextProfile(
        index=index,
        device=device,
        locale=locale,
        timezone_id=timezone_id,
        geolocation=geolocation,
        ignore_https_errors=config.ignore_https_errors,
        seed=parse_int(config.seed, default=0) or (index * 7919 + 13),
        canvas_noise=config.canvas_noise,
        webgl_spoof=config.webgl_spoof,
        audio_noise=config.audio_noise,
        webrtc_block=config.webrtc_block,
        stealth_enabled=config.stealth,
        humanize=config.humanize,
        save_storage_state=config.save_storage_state,
        target_urls=list(target_urls or []),
    )
    if config.device_scale_factor:
        profile.device_scale_factor = config.device_scale_factor
    return profile


def randomize_profile_order(count: int, seed: Optional[int] = None) -> list[int]:
    """Return a shuffled context index order (keeps proxy/device pairing varied)."""
    order = list(range(count))
    rng = random.Random(seed) if seed is not None else random
    rng.shuffle(order)
    return order


__all__ += ["profile_from_config", "randomize_profile_order", "EngineHooks"]
