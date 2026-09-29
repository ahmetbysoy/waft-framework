#!/usr/bin/env python3
"""``run_proxy_test.py`` - drives a browser through the local reverse proxy and measures it.

What it proves
--------------
The browser only ever talks to ``http://127.0.0.1:8080`` (the local reverse proxy) while the
upstream given by ``REAL_TARGET_URL`` receives the traffic **through** that hop. The artefacts then
answer the question the whole kit exists for: *what does my WAF/application see and do when the
request arrives through a proxy?*

The driver executes, in order:

1. reads ``REAL_TARGET_URL`` (missing → exit 2, nothing is launched);
2. validates the upstream against the scope file **and** the compiled-in third-party block-list
   (identical rules to ``local_proxy_server.py``, so an off-scope upstream cannot be smuggled in);
3. spawns ``local_proxy_server.py`` in the background (``--no-spawn`` = "it is already running"),
   waits for its readiness line, streaming its log into the artefact directory;
4. launches Chromium and points it at the **proxy** only;
5. navigates the target rows, fills the form through the selector fallback chains, submits,
   listens on ``page.on("request")`` / ``page.on("response")``;
6. cross-checks the two logs (browser side vs proxy side) and writes ``proxy_test_results.json``;
7. shuts the browser down and stops the proxy it started (also on error/interrupt).

Deliberate non-goals
--------------------
* **No fingerprint spoofing.** WAF proxy-chain questions are answered by the forwarding headers,
  not by pretending to be another browser; the driver uses a stock Chromium.
* **No third-party hosts.** ``REAL_TARGET_URL`` must be an allowed host; the block-list check runs
  in *both* processes and cannot be disabled.
* **No CAPTCHA solving.** A visible CAPTCHA is logged as ``CAPTCHA_DETECTED``, the target is marked
  skipped and the run continues (``--fail-on-captcha`` turns it into a failure).

Usage
-----
Full local end-to-end (bundled sandbox as the origin behind the proxy)::

    python3 examples/offerwall_sandbox.py --port 8090 &        # the "origin"
    export REAL_TARGET_URL=http://127.0.0.1:8090
    python3 qa-kit/loadtest/run_proxy_test.py                  # spawns the proxy on :8080

Your own staging behind the proxy (host added to ``qa-kit/authorized_hosts.txt``)::

    export REAL_TARGET_URL=https://staging.sirketiniz.com
    python3 qa-kit/loadtest/run_proxy_test.py --xff-client 203.0.113.7

Validation only (no browser, no proxy)::

    python3 qa-kit/loadtest/run_proxy_test.py --check

Exit codes: ``0`` pass | ``1`` check/assertion failure | ``2`` usage/scope/config error | ``130``
interrupt.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Optional, Sequence
from urllib.parse import urlsplit

# --- make the package + kit siblings importable when run as a plain script ---------------------
_HERE: Final[Path] = Path(__file__).resolve().parent
_KIT: Final[Path] = _HERE.parent
_REPO: Final[Path] = _KIT.parent
_OFFERWALL: Final[Path] = _KIT / "offerwall"
for _path in (str(_REPO), str(_HERE), str(_KIT), str(_OFFERWALL)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: ``waft.*`` is imported defensively on purpose: the scope gate must still refuse an off-scope
#: upstream on a machine where the browser layer's dependencies are not installed yet (fresh CI
#: runner, new laptop). A missing playwright must never turn "refused" into a traceback.
_WAFT_IMPORT_ERROR: Optional[str] = None
try:  # pragma: no cover - environment dependent
    from waft.utils import ensure_dir, human_ms, new_run_id, now_iso, truncate, write_json, write_jsonl
except ModuleNotFoundError as _exc:  # pragma: no cover - environment dependent
    _WAFT_IMPORT_ERROR = str(_exc)
    import datetime as _dt
    import json as _json
    import uuid as _uuid

    def ensure_dir(path: Any) -> Path:
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def human_ms(value: Any) -> str:
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            return "0 ms"
        return f"{number:.0f} ms" if number < 1000 else f"{number / 1000:.2f} s"

    def new_run_id(prefix: str = "run") -> str:
        return f"{prefix}-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{_uuid.uuid4().hex[:6]}"

    def now_iso() -> str:
        return _dt.datetime.now().astimezone().isoformat(timespec="seconds")

    def truncate(value: Any, limit: int = 200, suffix: str = "…") -> str:
        text = "" if value is None else str(value)
        return text if len(text) <= limit else text[: max(0, limit - len(suffix))] + suffix

    def write_json(path: Any, payload: Any, *, indent: int = 2) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_json.dumps(payload, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")
        return target

    def write_jsonl(path: Any, records: Any, *, append: bool = False) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a" if append else "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(_json.dumps(record, ensure_ascii=False) + "\n")
        return target

logger = logging.getLogger("waft.proxy.test")

#: Playwright is optional at import time: ``--check`` must work on a machine without browsers.
_PLAYWRIGHT_IMPORT_ERROR: Optional[str] = None
try:  # pragma: no cover - environment dependent
    from playwright.async_api import (  # type: ignore[import-not-found]
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )
except ModuleNotFoundError as _exc:  # pragma: no cover - environment dependent
    _PLAYWRIGHT_IMPORT_ERROR = str(_exc)
    PlaywrightTimeoutError = TimeoutError  # type: ignore[assignment,misc]


def _load_sibling(name: str, filename: str) -> Any:
    """Import a sibling script by path (they are the single source of truth for their logic)."""
    path = _HERE / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


proxy_server = _load_sibling("waft_local_proxy_server", "local_proxy_server.py")
loadtest_runner = _load_sibling("waft_loadtest_runner", "run_load_test.py")

STATUS_OK: Final[str] = "ok"
STATUS_FAILED: Final[str] = "failed"
STATUS_SKIPPED_CAPTCHA: Final[str] = "captcha_detected"

READY_MARKER: Final[str] = '"event": "listening"'


class ProxyTestError(RuntimeError):
    """Fatal, user-facing problem (usage/scope/configuration) → exit code 2."""


# ======================================================================================
# logging
# ======================================================================================
def _configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(levelname)-7s | %(message)s")


# ======================================================================================
# the spawned proxy process
# ======================================================================================
@dataclass
class ProxyProcess:
    """Handle for the background ``local_proxy_server.py`` (spawned by this driver)."""

    process: subprocess.Popen[str]
    stdout_lines: list[str] = field(default_factory=list)
    ready: bool = False
    ready_info: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _reader(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            with self._lock:
                self.stdout_lines.append(line.rstrip("\n"))
                if not self.ready and READY_MARKER in line:
                    self.ready = True
                    with_ = re.search(r"\{.*\}", line)
                    if with_:
                        try:
                            self.ready_info = json.loads(with_.group(0))
                        except json.JSONDecodeError:
                            self.ready_info = {}

    def wait_ready(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if self.ready:
                    return True
            if self.process.poll() is not None:
                return False
            time.sleep(0.05)
        return False

    def stop(self) -> None:
        """SIGTERM → wait → SIGKILL. Never raises: called from ``finally`` blocks."""
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception as exc:  # noqa: BLE001
                logger.debug("proxy terminate failed: %s", exc)
            try:
                self.process.wait(timeout=10)
            except Exception:  # noqa: BLE001
                with suppress_all():
                    self.process.kill()

    def tail(self, limit: int = 12) -> str:
        with self._lock:
            return "\n".join(self.stdout_lines[-limit:])


class suppress_all:
    """Tiny helper: swallow *every* exception from a cleanup call."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: Any) -> bool:
        return True


def spawn_proxy(args: argparse.Namespace, *, run_dir: Path, upstream: str) -> ProxyProcess:
    """Start ``local_proxy_server.py`` and wait for its readiness line."""
    command = [
        sys.executable,
        str(_HERE / "local_proxy_server.py"),
        "--upstream",
        upstream,
        "--listen-host",
        args.listen_host,
        "--listen-port",
        str(args.listen_port),
        "--scope",
        str(args.scope),
        "--timeout",
        str(args.timeout),
        "--log-jsonl",
        str(args.proxy_log),
        "--log-level",
        args.log_level,
    ]
    if args.i_am_authorized:
        command.append("--i-am-authorized")
    if args.xff_client:
        command.extend(["--xff-client", args.xff_client])

    logger.info("proxy başlatılıyor: %s", " ".join(command[:6]) + " …")
    logs_dir = ensure_dir(run_dir / "proxy")
    stderr_path = logs_dir / "proxy.stderr.log"
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=stderr_path.open("w", encoding="utf-8"),
            text=True,
            bufsize=1,
            cwd=str(_REPO),
        )
    except OSError as exc:
        raise ProxyTestError(f"proxy process could not start: {exc}") from exc

    handle = ProxyProcess(process=process)
    threading.Thread(target=handle._reader, name="proxy-stdout", daemon=True).start()
    if not handle.wait_ready(float(args.startup_timeout)):
        code = process.poll()
        detail = handle.tail()
        handle.stop()
        raise ProxyTestError(
            f"proxy {args.startup_timeout}s içinde ayağa kalkmadı (exit={code}).\n"
            f"  proxy çıktısı: {truncate(detail, 400) or '(boş)'}\n"
            f"  stderr: {stderr_path}"
        )
    logger.info(
        "proxy hazır: %s → %s",
        handle.ready_info.get("listen", args.proxy_url),
        handle.ready_info.get("upstream", upstream),
    )
    return handle


def wait_for_existing_proxy(host: str, port: int, *, timeout_s: float) -> bool:
    """``--no-spawn``: wait until something is listening on host:port."""
    import socket

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.1)
    return False


# ======================================================================================
# browser side-recording
# ======================================================================================
@dataclass(slots=True)
class BrowserEvent:
    """One request/response pair seen by the browser."""

    method: str
    url: str
    path: str
    status: Optional[int]
    resource_type: str
    duration_ms: Optional[float]
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "path": self.path,
            "status": self.status,
            "resource_type": self.resource_type,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


@dataclass(slots=True)
class TargetResult:
    name: str
    url: str
    status: str
    detail: str = ""
    duration_ms: float = 0.0
    selectors_used: dict[str, str] = field(default_factory=dict)
    screenshot: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "status": self.status,
            "detail": self.detail,
            "duration_ms": round(self.duration_ms, 2),
            "selectors_used": dict(self.selectors_used),
            "screenshot": self.screenshot,
        }


class ProxyTestRunner:
    """Playwright driver: proxy-only navigation, form filling, network capture, cross-checks."""

    def __init__(
        self,
        *,
        args: argparse.Namespace,
        targets: Sequence[Any],
        catalogue: Any,
        proxy_origin: str,
        upstream: str,
        run_id: str,
        run_dir: Path,
    ) -> None:
        self.args = args
        self.targets = list(targets)
        self.catalogue = catalogue
        self.proxy_origin = proxy_origin.rstrip("/")
        self.upstream = upstream
        self.upstream_host = urlsplit(upstream).netloc.lower()
        self.run_id = run_id
        self.run_dir = ensure_dir(run_dir)
        self.screenshot_dir = ensure_dir(self.run_dir / "screenshots")
        self.events: list[BrowserEvent] = []
        self.results: list[TargetResult] = []
        self.steps: list[dict[str, Any]] = []
        self._started: dict[tuple[str, str, str], float] = {}
        self.proxy_records: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ helpers
    def _log_step(self, step: str, **payload: Any) -> None:
        self.steps.append({"ts": now_iso(), "run_id": self.run_id, "step": step, **payload})
        logger.debug("%s %s", step, truncate(json.dumps(payload, ensure_ascii=False), 200))

    @staticmethod
    def _as_locator(page: Any, selector: str) -> Any:
        text = selector.strip()
        if text.startswith("xpath="):
            return page.locator(text)
        if text.startswith(("//", "..", "(")):
            return page.locator(f"xpath={text}")
        return page.locator(text)

    async def _first_visible(self, page: Any, chain: Sequence[str], *, timeout_ms: Optional[int] = None) -> tuple[Optional[Any], Optional[str]]:
        budget = timeout_ms if timeout_ms is not None else int(self.catalogue.probe_timeout_ms)
        for selector in chain:
            try:
                locator = self._as_locator(page, selector).first
                if await locator.count() == 0:
                    continue
                if await locator.is_visible(timeout=budget):
                    return locator, selector
            except PlaywrightTimeoutError:
                continue
            except Exception as exc:  # noqa: BLE001 - a bad selector must not abort the chain
                logger.debug("selector %r failed: %s", selector, exc)
                continue
        return None, None

    async def _screenshot(self, page: Any, label: str) -> Optional[str]:
        if not self.args.screenshots:
            return None
        path = self.screenshot_dir / f"{len(self.steps):03d}-{re.sub(r'[^A-Za-z0-9_.-]+', '-', label)[:40]}.png"
        try:
            await page.screenshot(path=str(path))
            return str(path.relative_to(self.run_dir))
        except Exception as exc:  # noqa: BLE001
            logger.debug("screenshot failed: %s", exc)
            return None

    # ------------------------------------------------------------------ capture
    def _attach_capture(self, page: Any) -> None:
        def request_key(request: Any) -> tuple[str, str, str]:
            return (request.method, request.url, str(id(request)))

        def on_request(request: Any) -> None:
            self._started[request_key(request)] = time.perf_counter()

        def on_response(response: Any) -> None:
            request = response.request
            started = self._started.pop(request_key(request), None)
            url = response.url
            self.events.append(
                BrowserEvent(
                    method=request.method,
                    url=url,
                    path=urlsplit(url).path or "/",
                    status=int(response.status),
                    resource_type=request.resource_type,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2) if started else None,
                )
            )

        def on_failed(request: Any) -> None:
            self._started.pop(request_key(request), None)
            failure = getattr(request, "failure", None)
            message = ""
            if isinstance(failure, dict):
                message = str(failure.get("errorText") or "")
            elif failure is not None:
                message = str(failure)
            self.events.append(
                BrowserEvent(
                    method=request.method,
                    url=request.url,
                    path=urlsplit(request.url).path or "/",
                    status=None,
                    resource_type=request.resource_type,
                    duration_ms=None,
                    error=truncate(message, 160),
                )
            )

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfailed", on_failed)

    # ------------------------------------------------------------------ identities
    def identity_for(self, index: int) -> tuple[str, str]:
        """Per-context test identity used to resolve ``{email}`` / ``{password}``.

        Distinct identities per context are deliberate: a WAF sees several "different users"
        rather than the same payload repeated, and a unique local part keeps the origin's own
        deduplication logic out of the way. No real mailbox is involved - this kit does not do
        e-mail verification (that is ``run_load_test.py``'s job, with a real credential pool).
        """
        email = self.args.account_email or f"proxy-ctx{index:02d}-{self.run_id}@{self.args.identity_domain}"
        return email, self.args.account_password

    def resolve_fields(self, target: Any, *, index: int) -> dict[str, str]:
        """Resolve placeholders in ``form_fields`` (``{email}``/``{password}``/``{ctx}``/``{run}``)."""
        email, password = self.identity_for(index)
        mapping = {
            "{email}": email,
            "{{email}}": email,
            "{account_email}": email,
            "{password}": password,
            "{{password}}": password,
            "{app_password}": password,
            "{account_password}": password,
            "{ctx}": str(index),
            "{context}": str(index),
            "{run}": self.run_id,
            "{run_id}": self.run_id,
        }
        resolved: dict[str, str] = {}
        for key, value in target.form_fields.items():
            text = str(value)
            for token, replacement in mapping.items():
                text = text.replace(token, replacement)
            resolved[key] = text
        return resolved

    # ------------------------------------------------------------------ form work
    async def _detect_captcha(self, page: Any, target: Any) -> list[str]:
        markers: list[str] = []
        chains: list[str] = []
        if getattr(target, "captcha_selector", None):
            chains.append(target.captcha_selector)
        if self.catalogue.known("captcha_frame"):
            chains.extend(self.catalogue.chain("captcha_frame"))
        chains.extend(loadtest_runner.CAPTCHA_SELECTORS)
        seen: set[str] = set()
        for selector in chains:
            if selector in seen:
                continue
            seen.add(selector)
            try:
                locator = self._as_locator(page, selector).first
                if await locator.count() and await locator.is_visible(timeout=750):
                    markers.append(selector)
            except Exception:  # noqa: BLE001 - probing never raises
                continue
        return markers

    async def _fill_and_submit(self, page: Any, target: Any, *, index: int) -> TargetResult:
        started = time.perf_counter()
        url = self.proxy_origin + (urlsplit(target.target_url).path or "/")
        result = TargetResult(name=target.name, url=url, status=STATUS_OK)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=int(self.args.navigation_timeout))
            self._log_step("navigated", target=target.name, url=url)
            result.screenshot = await self._screenshot(page, f"landing-{target.name}")

            markers = await self._detect_captcha(page, target)
            if markers:
                result.status = STATUS_SKIPPED_CAPTCHA
                result.detail = f"CAPTCHA visible ({markers[0]}) — skipped without exception"
                self._log_step("CAPTCHA_DETECTED", target=target.name, markers=markers)
                return result

            fields = self.resolve_fields(target, index=index)
            for field_name, value in fields.items():
                key = self.catalogue.resolve_field_key(field_name)
                if key is None:
                    self._log_step("field_skipped", field=field_name, reason="no selector chain")
                    continue
                locator, selector = await self._first_visible(page, self.catalogue.chain(key))
                if locator is None or selector is None:
                    self._log_step("field_unmatched", field=field_name)
                    continue
                kind = self.catalogue.kind_of(key)
                try:
                    if kind == "check":
                        if str(value).strip().lower() not in {"", "0", "false", "off"}:
                            await locator.check(timeout=int(self.args.action_timeout))
                    elif kind == "select":
                        try:
                            await locator.select_option(value=str(value), timeout=int(self.args.action_timeout))
                        except Exception:  # noqa: BLE001
                            await locator.select_option(label=str(value), timeout=int(self.args.action_timeout))
                    else:
                        await locator.fill(str(value), timeout=int(self.args.action_timeout))
                except Exception as exc:  # noqa: BLE001 - one field never kills the target
                    self._log_step("field_failed", field=field_name, selector=selector, error=truncate(str(exc), 160))
                    continue
                result.selectors_used[field_name] = selector
                display = loadtest_runner.mask_secret(str(value), keep=3) if "password" in field_name.lower() else str(value)
                self._log_step("field_filled", field=field_name, selector=selector, kind=kind, value=display)

            if fields:
                locator, selector = await self._first_visible(
                    page,
                    ([target.submit_selector] if getattr(target, "submit_selector", None) else [])
                    + list(self.catalogue.chain("submit_button")),
                )
                if locator is None:
                    result.status = STATUS_FAILED
                    result.detail = "submit control not found"
                    return result
                await locator.click(timeout=int(self.args.action_timeout))
                result.selectors_used["submit"] = selector or ""
                self._log_step("submitted", target=target.name, selector=selector)
                await asyncio.sleep(max(0, int(target.wait_after_submit_ms)) / 1000)

                failure = await self._await_outcome(page, target)
                if failure:
                    result.status = STATUS_FAILED
                    result.detail = failure
            return result
        except PlaywrightTimeoutError as exc:
            result.status = STATUS_FAILED
            result.detail = f"timeout: {truncate(str(exc), 160)}"
            return result
        except Exception as exc:  # noqa: BLE001
            result.status = STATUS_FAILED
            result.detail = f"{type(exc).__name__}: {truncate(str(exc), 160)}"
            return result
        finally:
            result.duration_ms = (time.perf_counter() - started) * 1000
            result.screenshot = result.screenshot or await self._screenshot(page, f"done-{target.name}")

    async def _await_outcome(self, page: Any, target: Any) -> Optional[str]:
        """Return an error string when the form reported a failure, else ``None``."""
        chains: list[str] = []
        if getattr(target, "error_selector", None):
            chains.append(target.error_selector)
        if self.catalogue.known("error"):
            chains.extend(self.catalogue.chain("error"))
        for selector in chains:
            try:
                locator = self._as_locator(page, selector).first
                if await locator.count() and await locator.is_visible(timeout=600):
                    text = ""
                    with suppress_all():
                        text = (await locator.inner_text(timeout=600)).strip()
                    return f"error element visible ({selector}): {truncate(text, 120)}"
            except Exception:  # noqa: BLE001
                continue

        markers = await self._detect_captcha(page, target)
        if markers:
            return f"CAPTCHA visible after submit ({markers[0]})"

        success_chains: list[str] = []
        if getattr(target, "success_selector", None):
            success_chains.append(target.success_selector)
        if self.catalogue.known("success"):
            success_chains.extend(self.catalogue.chain("success"))
        for selector in success_chains:
            try:
                locator = self._as_locator(page, selector).first
                if await locator.count() and await locator.is_visible(timeout=int(self.args.success_timeout)):
                    self._log_step("success_detected", target=target.name, selector=selector)
                    return None
            except Exception:  # noqa: BLE001
                continue
        if getattr(target, "success_url_regex", None):
            with suppress_all():
                if re.search(target.success_url_regex, page.url):
                    return None
        return "no success marker after submit"

    # ------------------------------------------------------------------ run
    async def run(self) -> int:
        assert _PLAYWRIGHT_IMPORT_ERROR is None, "playwright must be importable before run()"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=not self.args.headful, args=list(self.args.browser_args)
            )
            try:
                for index in range(max(1, int(self.args.contexts))):
                    context = await browser.new_context(ignore_https_errors=self.args.ignore_https_errors)
                    page = await context.new_page()
                    page.set_default_timeout(int(self.args.action_timeout))
                    self._attach_capture(page)
                    self._log_step("context_ready", index=index, proxy=self.proxy_origin)
                    try:
                        for target in self.targets:
                            self.results.append(await self._fill_and_submit(page, target, index=index))
                    finally:
                        with suppress_all():
                            await context.close()
            finally:
                with suppress_all():
                    await browser.close()
        self._read_proxy_log()
        return self.exit_code()

    def _read_proxy_log(self) -> None:
        """Load the proxy's JSONL measurement log (written by the spawned/other process)."""
        path = Path(self.args.proxy_log)
        if not path.exists():
            logger.warning("proxy log bulunamadı: %s", path)
            return
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.debug("proxy log satırı atlandı: %s", truncate(line, 120))
        self.proxy_records = records

    # ------------------------------------------------------------------ checks
    def checks(self) -> dict[str, Any]:
        """Cross-checks between what the browser did and what the proxy forwarded."""
        forwards = [record for record in self.proxy_records if record.get("type") == "forward"]
        browser_responses = [event for event in self.events if event.status is not None]

        off_proxy = [event.url for event in self.events if not event.url.startswith(self.proxy_origin)]
        direct_upstream = [event.url for event in self.events if urlsplit(event.url).netloc.lower() == self.upstream_host]
        missing_xff = [
            record for record in forwards
            if not (record.get("forwarded_headers") or {}).get("X-Forwarded-For")
        ]

        browser_keys: dict[tuple[str, str], list[int]] = {}
        for event in browser_responses:
            browser_keys.setdefault((event.method, event.path), []).append(int(event.status or 0))
        proxy_keys: dict[tuple[str, str], list[int]] = {}
        for record in forwards:
            if record.get("status") is None:
                continue
            proxy_keys.setdefault((str(record.get("method")), str(record.get("path"))), []).append(int(record["status"]))

        parity_mismatches: list[dict[str, Any]] = []
        for key, statuses in browser_keys.items():
            forwarded = proxy_keys.get(key)
            if forwarded is None:
                parity_mismatches.append({"method": key[0], "path": key[1], "reason": "not forwarded", "browser": statuses})
                continue
            if sorted(statuses) != sorted(forwarded):
                parity_mismatches.append(
                    {"method": key[0], "path": key[1], "reason": "status mismatch", "browser": statuses, "proxy": forwarded}
                )

        return {
            "browser_talked_only_to_proxy": not off_proxy,
            "off_proxy_urls": off_proxy[:10],
            "browser_never_contacted_upstream_directly": not direct_upstream,
            "direct_upstream_urls": direct_upstream[:10],
            "proxy_forwarded_requests": len(forwards),
            "proxy_errors": sum(1 for record in self.proxy_records if record.get("type") == "proxy_error"),
            "xff_present_on_every_forward": bool(forwards) and not missing_xff,
            "xff_values": sorted({(r.get("forwarded_headers") or {}).get("X-Forwarded-For", "") for r in forwards})[:5],
            "status_parity": not parity_mismatches,
            "status_parity_mismatches": parity_mismatches[:10],
            "upstream_host": self.upstream_host,
            "proxy_origin": self.proxy_origin,
        }

    def exit_code(self) -> int:
        checks = self.checks()
        failed_targets = [result for result in self.results if result.status == STATUS_FAILED]
        if self.args.fail_on_captcha and any(result.status == STATUS_SKIPPED_CAPTCHA for result in self.results):
            return 1
        hard_failures = (
            failed_targets
            or not checks["browser_talked_only_to_proxy"]
            or not checks["browser_never_contacted_upstream_directly"]
            or not checks["xff_present_on_every_forward"]
            or not checks["status_parity"]
            or checks["proxy_errors"] > 0
        )
        return 1 if hard_failures else 0

    # ------------------------------------------------------------------ artefacts
    def write_results(self) -> Path:
        checks = self.checks()
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "generated_at": now_iso(),
            "proxy": {
                "origin": self.proxy_origin,
                "upstream": self.upstream,
                "upstream_host": self.upstream_host,
                "log_jsonl": str(self.args.proxy_log),
                "xff_client": self.args.xff_client,
            },
            "targets": [target.to_dict() for target in self.targets],
            "identities": [
                {"context": index, "email": self.identity_for(index)[0], "password": loadtest_runner.mask_secret(self.identity_for(index)[1])}
                for index in range(max(1, int(self.args.contexts)))
            ],
            "results": [result.to_dict() for result in self.results],
            "network": [event.to_dict() for event in self.events],
            "proxy_forwarded_requests": [
                {key: value for key, value in record.items() if key != "type"}
                for record in self.proxy_records
                if record.get("type") == "forward"
            ],
            "checks": checks,
            "summary": {
                "targets": len(self.results),
                "targets_ok": sum(1 for result in self.results if result.status == STATUS_OK),
                "targets_failed": sum(1 for result in self.results if result.status == STATUS_FAILED),
                "targets_captcha_skipped": sum(1 for result in self.results if result.status == STATUS_SKIPPED_CAPTCHA),
                "browser_requests": len(self.events),
                "proxy_forwards": checks["proxy_forwarded_requests"],
                "duration_ms": round(sum(result.duration_ms for result in self.results), 2),
            },
        }
        path = write_json(self.run_dir / "proxy_test_results.json", payload)
        return Path(path)

    def render_report(self) -> str:
        checks = self.checks()
        summary = {
            "targets": len(self.results),
            "ok": sum(1 for result in self.results if result.status == STATUS_OK),
            "failed": sum(1 for result in self.results if result.status == STATUS_FAILED),
            "captcha": sum(1 for result in self.results if result.status == STATUS_SKIPPED_CAPTCHA),
        }
        lines: list[str] = ["=" * 78]
        lines.append(f"PROXY ZİNCİRİ TESTİ — {self.run_id}")
        lines.append("=" * 78)
        lines.append(f"Tarayıcı → proxy      : {self.proxy_origin}")
        lines.append(f"Proxy → upstream      : {self.upstream}   (XFF: {self.args.xff_client or 'istemci IP'})")
        lines.append(f"Hedefler              : {summary['targets']} (ok {summary['ok']} | hata {summary['failed']} | captcha {summary['captcha']})")
        lines.append(f"Tarayıcı isteği       : {len(self.events)} | proxy'ye iletilen: {checks['proxy_forwarded_requests']}")
        lines.append("")
        lines.append("Kontroller:")
        marks = {True: "✔", False: "✖"}
        lines.append(f"  {marks[checks['browser_talked_only_to_proxy']]} tarayıcı YALNIZCA proxy ile konuştu")
        lines.append(f"  {marks[checks['browser_never_contacted_upstream_directly']]} upstream'e doğrudan gidilmedi")
        lines.append(f"  {marks[checks['xff_present_on_every_forward']]} her iletilen istekte X-Forwarded-For var")
        lines.append(f"  {marks[checks['status_parity']]} durum kodları tarayıcı ↔ proxy tarafında aynı")
        if checks["xff_values"]:
            lines.append(f"  → XFF değerleri: {', '.join(checks['xff_values'])}")
        for mismatch in checks["status_parity_mismatches"]:
            lines.append(f"    ! {mismatch}")
        lines.append("")
        lines.append("Hedef sonuçları:")
        for result in self.results:
            lines.append(f"  • {result.name:<16} {result.status:<18} {human_ms(result.duration_ms):>9}  {truncate(result.detail, 70)}")
        lines.append("")
        lines.append(f"Artefaktlar: {self.run_dir}")
        lines.append("  proxy_test_results.json (network + checks) | proxy/proxy_requests.jsonl | steps.jsonl")
        lines.append("=" * 78)
        return "\n".join(lines)


# ======================================================================================
# CLI
# ======================================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_proxy_test.py",
        description="Drive a browser through the local reverse proxy and verify proxy-chain behaviour.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--real-target-url", default=None,
                        help="upstream origin; defaults to the REAL_TARGET_URL environment variable")
    parser.add_argument("--proxy-url", default="http://127.0.0.1:8080",
                        help="the ONLY origin the browser is allowed to talk to")
    parser.add_argument("--listen-host", default=None, help="proxy bind host (default: from --proxy-url)")
    parser.add_argument("--listen-port", type=int, default=None, help="proxy bind port (default: from --proxy-url)")
    parser.add_argument("--no-spawn", dest="spawn", action="store_false", default=True,
                        help="assume the proxy is already running instead of starting it")
    parser.add_argument("--startup-timeout", type=float, default=20.0, help="seconds to wait for the proxy")
    parser.add_argument("--targets", type=Path, default=_HERE / "test_targets.sandbox.json",
                        help="target schema JSON (paths are appended to the proxy origin)")
    parser.add_argument("--selectors", type=Path, default=_HERE / "selectors.json", help="selector catalogue")
    parser.add_argument("--scope", type=Path, default=_KIT / "authorized_hosts.txt", help="scope file for the upstream")
    parser.add_argument("--i-am-authorized", action="store_true",
                        help="allow an upstream outside the scope file (your own infra only)")
    parser.add_argument("--xff-client", default=None,
                        help="fixed X-Forwarded-For value the proxy should send (WAF rule testing)")
    parser.add_argument("--contexts", type=int, default=1, help="how many isolated browser contexts")
    parser.add_argument("--account-email", default=None,
                        help="e-mail used for {email}; default: proxy-ctx<NN>-<run>@<--identity-domain>")
    parser.add_argument("--account-password", default="ProxyTest-2026!x",
                        help="password used for {password} (masked in logs/reports)")
    parser.add_argument("--identity-domain", default="demo.waft.local",
                        help="domain for the synthesized per-context e-mail addresses")
    parser.add_argument("--timeout", type=float, default=30.0, help="upstream timeout for the proxy (seconds)")
    parser.add_argument("--action-timeout", type=int, default=20_000, help="Playwright action timeout (ms)")
    parser.add_argument("--navigation-timeout", type=int, default=45_000, help="navigation timeout (ms)")
    parser.add_argument("--success-timeout", type=int, default=5_000, help="success marker wait (ms)")
    parser.add_argument("--screenshots", action="store_true", default=True, help="save one PNG per step")
    parser.add_argument("--no-screenshots", dest="screenshots", action="store_false", help="do not save PNGs")
    parser.add_argument("--fail-on-captcha", action="store_true", help="CAPTCHA detection makes the run fail")
    parser.add_argument("--headful", action="store_true", help="visible browser (debugging)")
    parser.add_argument("--browser-arg", action="append", default=[], dest="browser_args",
                        help="extra Chromium argument (repeatable)")
    parser.add_argument("--ignore-https-errors", action="store_true", default=True, help="staging certificates")
    parser.add_argument("--strict-https", dest="ignore_https_errors", action="store_false", help="fail on bad certs")
    parser.add_argument("--proxy-log", type=Path, default=None,
                        help="proxy JSONL log path (default: <artifacts>/<run>/proxy/proxy_requests.jsonl)")
    parser.add_argument("--artifacts", type=Path, default=_REPO / "artifacts" / "proxy", help="artefact root")
    parser.add_argument("--check", action="store_true", help="validate configuration/scope and exit")
    parser.add_argument("--log-level", default="info", help="debug | info | warning | error")
    return parser


def resolve_proxy_endpoint(args: argparse.Namespace) -> None:
    """Derive the proxy's bind host/port from ``--proxy-url`` unless given explicitly."""
    parts = urlsplit(args.proxy_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ProxyTestError(f"--proxy-url mutlak bir http(s) URL olmalı (verilen: {args.proxy_url!r})")
    if args.listen_host is None:
        args.listen_host = parts.hostname
    if args.listen_port is None:
        args.listen_port = parts.port or (443 if parts.scheme == "https" else 80)
    if parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ProxyTestError(
            f"--proxy-url loopback olmalı (127.0.0.1/localhost); verilen: {parts.hostname}.\n"
            f"Bu testin amacı tarayıcının SADECE yerel proxy ile konuşmasıdır."
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.log_level)

    # --- 1) REAL_TARGET_URL -----------------------------------------------------------------
    upstream_raw = (args.real_target_url or os.environ.get("REAL_TARGET_URL") or "").strip()
    if not upstream_raw:
        print(
            "✖ REAL_TARGET_URL tanımlı değil. Bu betikte gömülü hiçbir adres yoktur:\n\n"
            "    export REAL_TARGET_URL=http://127.0.0.1:8090            # yerel mock\n"
            "    export REAL_TARGET_URL=https://staging.sirketiniz.com   # kendi staging'iniz",
            file=sys.stderr,
        )
        return 2

    # --- 2) scope gate (compiled-in block-list first, then the scope file) -------------------
    scope = proxy_server.Scope.load(args.scope, [])
    try:
        upstream = proxy_server.validate_upstream(upstream_raw, scope, i_am_authorized=bool(args.i_am_authorized))
        resolve_proxy_endpoint(args)
    except (proxy_server.ProxyError, ProxyTestError) as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return 2
    args.real_target_url = upstream

    # --- 3) artefacts ------------------------------------------------------------------------
    run_id = new_run_id("proxy")
    run_dir = ensure_dir(Path(args.artifacts) / run_id)
    if args.proxy_log is None:
        args.proxy_log = run_dir / "proxy" / "proxy_requests.jsonl"
    args.proxy_log = Path(args.proxy_log)
    args.proxy_log.parent.mkdir(parents=True, exist_ok=True)
    args.proxy_url = f"http://{args.listen_host}:{args.listen_port}"
    if not args.browser_args:
        args.browser_args = ["--no-sandbox", "--disable-dev-shm-usage"]

    # --- 4) targets + selectors ---------------------------------------------------------------
    try:
        targets, target_warnings = loadtest_runner.load_targets(args.targets, target_url=args.proxy_url)
        catalogue, selector_warnings = loadtest_runner.load_selector_catalogue(args.selectors)
    except Exception as exc:  # noqa: BLE001 - configuration problems are usage errors
        print(f"✖ yapılandırma hatası: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    for warning in [*target_warnings, *selector_warnings]:
        print(f"⚠ {warning}")

    print(f"→ upstream (REAL_TARGET_URL): {upstream}")
    print(f"→ proxy (tarayıcının gördüğü tek adres): {args.proxy_url}")
    print(f"→ scope: {len(scope.patterns)} pattern(s) from {args.scope} | blocked_third_party=none")
    for target in targets:
        print(f"→ hedef: {target.name} → {target.target_url}")

    if args.check:
        print(
            f"→ doğrulama tamam: {len(targets)} hedef, {len(catalogue.chains)} seçici zinciri, "
            f"spawn={args.spawn} | tarayıcı açılmadı (--check)"
        )
        return 0

    if _PLAYWRIGHT_IMPORT_ERROR is not None:
        print(
            "✖ Playwright import edilemedi: " + _PLAYWRIGHT_IMPORT_ERROR + "\n"
            "  → python3 -m pip install -r requirements.txt && python3 -m playwright install --with-deps chromium",
            file=sys.stderr,
        )
        return 2

    # --- 5) proxy -----------------------------------------------------------------------------
    proxy_handle: Optional[ProxyProcess] = None
    if args.spawn:
        try:
            proxy_handle = spawn_proxy(args, run_dir=run_dir, upstream=upstream)
        except ProxyTestError as exc:
            print(f"✖ {exc}", file=sys.stderr)
            return 2
    elif not wait_for_existing_proxy(args.listen_host, int(args.listen_port), timeout_s=float(args.startup_timeout)):
        print(
            f"✖ --no-spawn verildi ama {args.listen_host}:{args.listen_port} dinlemiyor.\n"
            f"  → proxy'yi başlatın: python3 qa-kit/loadtest/local_proxy_server.py --listen-port {args.listen_port}",
            file=sys.stderr,
        )
        return 2

    runner = ProxyTestRunner(
        args=args,
        targets=targets,
        catalogue=catalogue,
        proxy_origin=args.proxy_url,
        upstream=upstream,
        run_id=run_id,
        run_dir=run_dir,
    )
    print(f"→ run: {run_id} | artefaktlar: {run_dir}")
    try:
        exit_code = int(asyncio.run(runner.run()))
    except KeyboardInterrupt:
        print("\n✖ kesildi — kısmi artefaktlar korundu", file=sys.stderr)
        exit_code = 130
    except Exception as exc:  # noqa: BLE001 - always explain, never dump a bare traceback
        logger.exception("proxy testi çöktü: %s", exc)
        print(f"✖ koşu çöktü: {type(exc).__name__}: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        if proxy_handle is not None:
            proxy_handle.stop()

    # --- 6) artefacts + report -----------------------------------------------------------------
    try:
        results_path = runner.write_results()
        if runner.steps:
            write_jsonl(run_dir / "steps.jsonl", runner.steps)
    except Exception as exc:  # noqa: BLE001 - artefacts must not mask the exit code
        logger.error("artefakt yazılamadı: %s", exc)
        results_path = run_dir / "proxy_test_results.json"
    print(runner.render_report())
    print(f"→ sonuç dosyası: {results_path}")
    print(f"→ exit code: {exit_code} ({'PASSED ✅' if exit_code == 0 else 'bkz. artifacts ❌'})")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
