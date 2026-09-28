"""Typed data containers shared across the framework.

These models are deliberately plain dataclasses (no third-party validation layer) so the
framework stays installable in constrained CI environments, while still giving static
type checkers and IDEs full information about every field.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .utils import mask_secret, new_context_id, now_iso, to_jsonable

__all__ = [
    "ProxySpec",
    "DeviceProfile",
    "Geolocation",
    "ContextProfile",
    "StepAction",
    "StepResult",
    "TargetRow",
    "FillReport",
    "VerificationResult",
    "TargetRunResult",
    "ContextResult",
    "RunTotals",
    "RunSummary",
    "STATUS_OK",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
    "STATUS_FLAKY",
]

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_FLAKY = "flaky"


# --------------------------------------------------------------------------------------
# Proxy / device / context
# --------------------------------------------------------------------------------------


@dataclass
class ProxySpec:
    """A single proxy endpoint plus lightweight health bookkeeping.

    ``server`` is always in Playwright form (``scheme://host:port``).
    """

    server: str
    username: Optional[str] = None
    password: Optional[str] = None
    scheme: str = "http"
    source: str = "unknown"
    label: str = ""
    bypass: Optional[str] = None

    healthy: bool = True
    uses: int = 0
    failures: int = 0
    last_used: float = 0.0
    last_error: Optional[str] = None
    latency_ms: Optional[float] = None
    cooldown_until: float = 0.0

    # ---------------------------------------------------------------- serialisation
    def to_playwright(self) -> dict[str, Any]:
        """Return the dict expected by ``browser.new_context(proxy=...)``."""
        payload: dict[str, Any] = {"server": self.server}
        if self.username:
            payload["username"] = self.username
        if self.password:
            payload["password"] = self.password
        if self.bypass:
            payload["bypass"] = self.bypass
        return payload

    def to_dict(self) -> dict[str, Any]:
        data = {
            "server": self.server,
            "username": self.username,
            "scheme": self.scheme,
            "source": self.source,
            "label": self.label or self.masked(),
            "healthy": self.healthy,
            "uses": self.uses,
            "failures": self.failures,
            "last_used": self.last_used or None,
            "last_error": self.last_error,
            "latency_ms": self.latency_ms,
            "cooldown_until": self.cooldown_until or None,
        }
        return to_jsonable(data)

    # ------------------------------------------------------------------ behaviour
    @property
    def key(self) -> str:
        """Stable identity used for engine-pooling and metrics."""
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def host(self) -> str:
        return self.server.split("://", 1)[-1].rsplit(":", 1)[0].strip("[]")

    @property
    def port(self) -> int:
        tail = self.server.split("://", 1)[-1].rsplit(":", 1)
        try:
            return int(tail[1])
        except (IndexError, ValueError):
            return 443 if self.scheme in {"https"} else (1080 if self.scheme == "socks5" else 80)

    @property
    def credentials(self) -> Optional[str]:
        if not self.username:
            return None
        return f"{self.username}:{self.password or ''}"

    def masked(self) -> str:
        """Human readable, secret-free rendering used in logs and summaries."""
        if self.username:
            return f"{self.scheme}://{mask_secret(self.credentials)}@{self.host}:{self.port}"
        return self.server

    @property
    def in_cooldown(self) -> bool:
        return self.cooldown_until > time.time()

    def note_use(self) -> None:
        self.uses += 1
        self.last_used = time.time()

    def note_success(self, latency_ms: Optional[float] = None) -> None:
        self.failures = 0
        self.healthy = True
        self.last_error = None
        if latency_ms is not None:
            self.latency_ms = latency_ms

    def note_failure(self, error: Optional[str] = None, *, cooldown_seconds: float = 0.0, max_failures: int = 3) -> None:
        self.failures += 1
        self.last_error = (error or "unknown error")[:400]
        if cooldown_seconds > 0:
            self.cooldown_until = time.time() + cooldown_seconds
        if self.failures >= max_failures:
            self.healthy = False

    def reset(self) -> None:
        self.healthy = True
        self.failures = 0
        self.last_error = None
        self.cooldown_until = 0.0


@dataclass
class Geolocation:
    """GPS coordinates (±accuracy) injected into a browser context."""

    latitude: float
    longitude: float
    accuracy: float = 50.0

    def to_playwright(self) -> dict[str, float]:
        return {"latitude": self.latitude, "longitude": self.longitude, "accuracy": self.accuracy}

    def to_dict(self) -> dict[str, Any]:
        return self.to_playwright()


@dataclass
class DeviceProfile:
    """A *coherent* fingerprint bundle (the parts users cannot override per run)."""

    name: str
    user_agent: str
    viewport: dict[str, int]
    screen: dict[str, int]
    platform: str = "Win32"
    vendor: str = "Google Inc."
    locale: str = "en-US"
    device_scale_factor: float = 1.0
    is_mobile: bool = False
    has_touch: bool = False
    hardware_concurrency: int = 8
    device_memory: int = 8
    webgl_vendor: str = "Google Inc. (NVIDIA)"
    webgl_renderer: str = "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0, D3D11)"
    color_depth: int = 24
    pixel_ratio: float = 1.0
    max_touch_points: int = 0
    brands: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable({**self.__dict__})


@dataclass
class ContextProfile:
    """Everything needed to materialise one isolated browser context."""

    index: int
    context_id: str = ""
    device: Optional[DeviceProfile] = None
    proxy: Optional[ProxySpec] = None
    user_agent: Optional[str] = None
    locale: Optional[str] = None
    timezone_id: Optional[str] = None
    viewport: Optional[dict[str, int]] = None
    screen: Optional[dict[str, int]] = None
    geolocation: Optional[Geolocation] = None
    permissions: list[str] = field(default_factory=list)
    extra_headers: dict[str, str] = field(default_factory=dict)
    color_scheme: str = "light"
    reduced_motion: str = "no-preference"
    is_mobile: bool = False
    has_touch: bool = False
    device_scale_factor: float = 1.0
    ignore_https_errors: bool = True
    java_script_enabled: bool = True
    service_workers: str = "allow"
    storage_state_path: Optional[str] = None
    save_storage_state: bool = True
    seed: int = 0
    webrtc_block: bool = True
    canvas_noise: bool = True
    webgl_spoof: bool = True
    audio_noise: bool = True
    humanize: bool = True
    stealth_enabled: bool = True
    credentials: Optional[dict[str, str]] = None
    target_urls: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.context_id:
            self.context_id = new_context_id(self.index)
        if self.device is not None:
            self.user_agent = self.user_agent or self.device.user_agent
            self.viewport = self.viewport or dict(self.device.viewport)
            self.screen = self.screen or dict(self.device.screen)
            self.locale = self.locale or self.device.locale
            self.is_mobile = self.is_mobile or self.device.is_mobile
            self.has_touch = self.has_touch or self.device.has_touch
            self.device_scale_factor = self.device_scale_factor or self.device.device_scale_factor
        self.locale = self.locale or "en-US"
        if self.canvas_noise and not self.seed:
            self.seed = abs(hash(self.context_id)) % (2**31)

    @property
    def label(self) -> str:
        """Short label used in log prefixes (``ctx-03``)."""
        return self.context_id.split("-")[0] + "-" + self.context_id.split("-")[1] if "-" in self.context_id else self.context_id

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(
            {
                "context_id": self.context_id,
                "index": self.index,
                "device": self.device.name if self.device else None,
                "user_agent": self.user_agent,
                "locale": self.locale,
                "timezone_id": self.timezone_id,
                "viewport": self.viewport,
                "geolocation": self.geolocation.to_dict() if self.geolocation else None,
                "proxy": self.proxy.masked() if self.proxy else None,
                "seed": self.seed,
                "stealth_enabled": self.stealth_enabled,
                "canvas_noise": self.canvas_noise,
                "webgl_spoof": self.webgl_spoof,
                "webrtc_block": self.webrtc_block,
                "storage_state_path": self.storage_state_path,
            }
        )


# --------------------------------------------------------------------------------------
# Scenario definition
# --------------------------------------------------------------------------------------


@dataclass
class StepAction:
    """One declarative workflow step (``goto``, ``fill``, ``click``, ``wait_for_url`` …)."""

    action: str
    target: Optional[str] = None
    value: Any = None
    timeout_ms: Optional[int] = None
    optional: bool = False
    description: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    SUPPORTED = frozenset(
        {
            "goto",
            "fill",
            "type",
            "click",
            "dblclick",
            "hover",
            "press",
            "check",
            "uncheck",
            "select",
            "upload",
            "wait",
            "sleep",
            "wait_for_selector",
            "wait_for_url",
            "wait_for_load_state",
            "wait_for_response",
            "expect_text",
            "expect_visible",
            "expect_url",
            "screenshot",
            "evaluate",
            "scroll",
            "frame_fill",
            "frame_click",
            "new_tab",
            "close_tab",
            "reload",
            "go_back",
            "set_input_files",
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self.__dict__)


@dataclass
class StepResult:
    """Outcome of a single workflow step (also used for non-declarative phases)."""

    name: str
    status: str = STATUS_OK
    started_at: str = field(default_factory=now_iso)
    duration_ms: float = 0.0
    error: Optional[str] = None
    error_type: Optional[str] = None
    screenshot: Optional[str] = None
    html_dump: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self.__dict__)


# --------------------------------------------------------------------------------------
# Targets / form payloads
# --------------------------------------------------------------------------------------


@dataclass
class TargetRow:
    """One row of the target data source: where to go and what to type."""

    index: int
    target_url: str
    form_data: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    name: Optional[str] = None
    scenario: str = "default"
    selectors: dict[str, str] = field(default_factory=dict)
    steps: list[StepAction] = field(default_factory=list)
    submit_selector: Optional[str] = None
    success_url_regex: Optional[str] = None
    success_selector: Optional[str] = None
    error_selector: Optional[str] = None
    submit_button_text: Optional[str] = None
    wait_after_submit_ms: int = 0
    requires_email_verification: bool = False
    verification_email: Optional[str] = None
    verification_email_field: Optional[str] = None
    verification_link_regex: Optional[str] = None
    verification_subject_regex: Optional[str] = None
    verification_sender: Optional[str] = None
    verification_otp_field: Optional[str] = None
    verification_timeout_s: Optional[float] = None
    resend_selector: Optional[str] = None
    proxy: Optional[str] = None
    iterations: int = 1
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = to_jsonable(self.__dict__)
        payload["form_data_keys"] = sorted(self.form_data.keys())
        return payload


@dataclass
class FillReport:
    """What the form filler actually did (attached to step details & summaries)."""

    filled: dict[str, str] = field(default_factory=dict)
    checked: dict[str, str] = field(default_factory=dict)
    selected: dict[str, str] = field(default_factory=dict)
    uploaded: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
    fields_seen: int = 0
    duration_ms: float = 0.0

    @property
    def total(self) -> int:
        return len(self.filled) + len(self.checked) + len(self.selected) + len(self.uploaded)

    def to_dict(self) -> dict[str, Any]:
        payload = to_jsonable(self.__dict__)
        payload["total_filled"] = self.total
        return payload


@dataclass
class VerificationResult:
    """Result of the IMAP → link/OTP verification flow."""

    attempted: bool = False
    success: bool = False
    method: Optional[str] = None  # "link" | "otp" | "skipped"
    matched_subject: Optional[str] = None
    matched_sender: Optional[str] = None
    matched_date: Optional[str] = None
    link: Optional[str] = None
    otp_code: Optional[str] = None
    waited_ms: float = 0.0
    attempts: int = 0
    error: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self.__dict__)


# --------------------------------------------------------------------------------------
# Run results
# --------------------------------------------------------------------------------------


@dataclass
class TargetRunResult:
    """Result of executing one target row once inside one browser context."""

    context_id: str
    row_index: int
    target_url: str
    iteration: int = 1
    attempt: int = 1
    status: str = STATUS_OK
    scenario: str = "default"
    started_at: str = field(default_factory=now_iso)
    finished_at: Optional[str] = None
    duration_ms: float = 0.0
    error: Optional[str] = None
    error_type: Optional[str] = None
    steps: list[StepResult] = field(default_factory=list)
    fill_report: Optional[FillReport] = None
    verification: Optional[VerificationResult] = None
    screenshots: list[str] = field(default_factory=list)
    trace_path: Optional[str] = None
    html_dump: Optional[str] = None
    network_log_path: Optional[str] = None
    network_metrics: dict[str, Any] = field(default_factory=dict)
    api_endpoints: list[str] = field(default_factory=list)
    final_url: Optional[str] = None
    final_title: Optional[str] = None
    proxy: Optional[str] = None
    device: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def failed_step(self) -> Optional[StepResult]:
        for step in self.steps:
            if step.status == STATUS_FAILED:
                return step
        return None

    def to_dict(self) -> dict[str, Any]:
        payload = to_jsonable(self.__dict__)
        payload["ok"] = self.ok
        payload["step_names"] = [s.name for s in self.steps]
        return payload


@dataclass
class ContextResult:
    """Aggregate result of one browser context (a virtual user)."""

    context_id: str
    index: int
    status: str = STATUS_OK
    started_at: str = field(default_factory=now_iso)
    finished_at: Optional[str] = None
    duration_ms: float = 0.0
    proxy: Optional[str] = None
    device: Optional[str] = None
    user_agent: Optional[str] = None
    timezone_id: Optional[str] = None
    locale: Optional[str] = None
    storage_state_path: Optional[str] = None
    trace_path: Optional[str] = None
    failure_bundle: Optional[str] = None
    runs: list[TargetRunResult] = field(default_factory=list)
    error: Optional[str] = None
    error_type: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK and all(run.ok for run in self.runs)

    def to_dict(self) -> dict[str, Any]:
        payload = to_jsonable(
            {
                "context_id": self.context_id,
                "index": self.index,
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "duration_ms": self.duration_ms,
                "proxy": self.proxy,
                "device": self.device,
                "user_agent": self.user_agent,
                "timezone_id": self.timezone_id,
                "locale": self.locale,
                "storage_state_path": self.storage_state_path,
                "trace_path": self.trace_path,
                "failure_bundle": self.failure_bundle,
                "error": self.error,
                "error_type": self.error_type,
            }
        )
        payload["runs"] = [run.to_dict() for run in self.runs]
        payload["ok"] = self.ok
        return payload


@dataclass
class RunTotals:
    """Aggregated KPI numbers for a whole run."""

    contexts: int = 0
    contexts_ok: int = 0
    contexts_failed: int = 0
    targets_planned: int = 0
    targets_run: int = 0
    targets_ok: int = 0
    targets_failed: int = 0
    targets_skipped: int = 0
    steps_ok: int = 0
    steps_failed: int = 0
    retries: int = 0
    requests: int = 0
    responses: int = 0
    failed_requests: int = 0
    console_errors: int = 0
    page_errors: int = 0
    bytes_in: int = 0
    verifications_ok: int = 0
    verifications_failed: int = 0

    @property
    def success_rate(self) -> float:
        if not self.targets_run:
            return 0.0
        return round(100.0 * self.targets_ok / self.targets_run, 2)

    def to_dict(self) -> dict[str, Any]:
        payload = to_jsonable(self.__dict__)
        payload["success_rate_pct"] = self.success_rate
        return payload


@dataclass
class RunSummary:
    """Top-level artifact written at the end of every run."""

    run_id: str
    started_at: str
    finished_at: Optional[str] = None
    duration_ms: float = 0.0
    status: str = STATUS_OK
    interrupted: bool = False
    exit_code: int = 0
    artifacts_dir: str = ""
    data_source: Optional[str] = None
    proxy_source: Optional[str] = None
    browser: str = "chromium"
    headless: bool = True
    concurrency: int = 1
    context_count: int = 0
    iterations: int = 1
    totals: RunTotals = field(default_factory=RunTotals)
    contexts: list[ContextResult] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    api_endpoints: list[str] = field(default_factory=list)
    config_digest: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK and self.exit_code == 0

    def to_dict(self, *, with_contexts: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "interrupted": self.interrupted,
            "exit_code": self.exit_code,
            "artifacts_dir": self.artifacts_dir,
            "data_source": self.data_source,
            "proxy_source": self.proxy_source,
            "browser": self.browser,
            "headless": self.headless,
            "concurrency": self.concurrency,
            "context_count": self.context_count,
            "iterations": self.iterations,
            "totals": self.totals.to_dict(),
            "failures": self.failures,
            "api_endpoints": self.api_endpoints,
            "config_digest": self.config_digest,
            "notes": self.notes,
            "ok": self.ok,
        }
        if with_contexts:
            payload["contexts"] = [ctx.to_dict() for ctx in self.contexts]
        return payload
