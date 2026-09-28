"""Configuration model: CLI arguments + ``.env`` + JSON profile file.

Priority (highest wins):

    1. explicit CLI flags
    2. environment variables / ``.env`` file
    3. ``--profile`` JSON file
    4. built-in defaults

Every timeout is expressed in **milliseconds** (Playwright convention) unless the field
name ends with ``_s`` (seconds), which is used by the IMAP/retry layers.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional, Union

from .errors import ConfigError
from .models import DeviceProfile, Geolocation
from .utils import (
    DEFAULT_TIMEZONE,
    deep_merge,
    import_optional,
    json_dumps,
    parse_bool,
    parse_float,
    parse_int,
    read_json,
    split_csv,
)

__all__ = [
    "Config",
    "DEVICE_PROFILES",
    "TIMEZONE_MAP",
    "SCENARIO_ALIASES",
    "load_dotenv_file",
    "resolve_device_profiles",
    "resolve_geolocation",
    "resolve_timezone",
    "build_arg_parser",
    "config_from_args",
    "EXPECTED_ROWS",
]


# --------------------------------------------------------------------------------------
# Static lookup tables
# --------------------------------------------------------------------------------------

#: Coherent Chrome/Windows/macOS desktop + Android mobile fingerprints. The stealth layer
#: must never mix, for example, a Windows UA with ``navigator.platform == 'Linux'``, so the
#: whole bundle is selected as one unit.
DEVICE_PROFILES: dict[str, DeviceProfile] = {
    "chrome-win-1366x768": DeviceProfile(
        name="chrome-win-1366x768",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1366, "height": 768},
        screen={"width": 1366, "height": 768},
        platform="Win32",
        locale="en-US",
        device_scale_factor=1.0,
        hardware_concurrency=8,
        device_memory=8,
        color_depth=24,
        pixel_ratio=1.0,
        webgl_vendor="Google Inc. (NVIDIA)",
        webgl_renderer=(
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
    ),
    "chrome-win-1920x1080": DeviceProfile(
        name="chrome-win-1920x1080",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 941},
        screen={"width": 1920, "height": 1080},
        platform="Win32",
        locale="tr-TR",
        device_scale_factor=1.0,
        hardware_concurrency=12,
        device_memory=16,
        webgl_vendor="Google Inc. (NVIDIA)",
        webgl_renderer=(
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
    ),
    "chrome-mac-1440x900": DeviceProfile(
        name="chrome-mac-1440x900",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 812},
        screen={"width": 1440, "height": 900},
        platform="MacIntel",
        locale="en-GB",
        device_scale_factor=2.0,
        hardware_concurrency=10,
        device_memory=16,
        pixel_ratio=2.0,
        webgl_vendor="Google Inc. (Apple)",
        webgl_renderer="ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)",
    ),
    "chrome-linux-1600x900": DeviceProfile(
        name="chrome-linux-1600x900",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1600, "height": 800},
        screen={"width": 1600, "height": 900},
        platform="Linux x86_64",
        locale="en-US",
        device_scale_factor=1.0,
        hardware_concurrency=6,
        device_memory=8,
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer="ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)",
    ),
    "edge-win-1536x864": DeviceProfile(
        name="edge-win-1536x864",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        ),
        viewport={"width": 1536, "height": 754},
        screen={"width": 1536, "height": 864},
        platform="Win32",
        locale="tr-TR",
        device_scale_factor=1.25,
        hardware_concurrency=8,
        device_memory=8,
        pixel_ratio=1.25,
        webgl_vendor="Google Inc. (AMD)",
        webgl_renderer="ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    ),
    "android-pixel7": DeviceProfile(
        name="android-pixel7",
        user_agent=(
            "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
        ),
        viewport={"width": 412, "height": 915},
        screen={"width": 412, "height": 915},
        platform="Linux armv8l",
        vendor="Google Inc.",
        locale="tr-TR",
        device_scale_factor=2.625,
        is_mobile=True,
        has_touch=True,
        hardware_concurrency=8,
        device_memory=8,
        pixel_ratio=2.625,
        max_touch_points=5,
        webgl_vendor="Qualcomm",
        webgl_renderer="Adreno (TM) 730",
    ),
    "android-galaxy-s23": DeviceProfile(
        name="android-galaxy-s23",
        user_agent=(
            "Mozilla/5.0 (Linux; Android 13; SM-S911B) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
        ),
        viewport={"width": 360, "height": 800},
        screen={"width": 360, "height": 800},
        platform="Linux armv8l",
        vendor="Google Inc.",
        locale="tr-TR",
        device_scale_factor=3.0,
        is_mobile=True,
        has_touch=True,
        hardware_concurrency=8,
        device_memory=8,
        pixel_ratio=3.0,
        max_touch_points=5,
        webgl_vendor="Qualcomm",
        webgl_renderer="Adreno (TM) 740",
    ),
}

#: IANA timezone per country code - keeps proxy geo, locale and clock coherent.
TIMEZONE_MAP: dict[str, str] = {
    "TR": "Europe/Istanbul",
    "US": "America/New_York",
    "DE": "Europe/Berlin",
    "NL": "Europe/Amsterdam",
    "GB": "Europe/London",
    "FR": "Europe/Paris",
    "ES": "Europe/Madrid",
    "IT": "Europe/Rome",
    "PL": "Europe/Warsaw",
    "RU": "Europe/Moscow",
    "UA": "Europe/Kyiv",
    "IN": "Asia/Kolkata",
    "SG": "Asia/Singapore",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "CN": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong",
    "AE": "Asia/Dubai",
    "IL": "Asia/Jerusalem",
    "BR": "America/Sao_Paulo",
    "AR": "America/Argentina/Buenos_Aires",
    "CA": "America/Toronto",
    "MX": "America/Mexico_City",
    "AU": "Australia/Sydney",
    "ZA": "Africa/Johannesburg",
    "EG": "Africa/Cairo",
    "ID": "Asia/Jakarta",
    "VN": "Asia/Ho_Chi_Minh",
    "TH": "Asia/Bangkok",
    "SE": "Europe/Stockholm",
    "CH": "Europe/Zurich",
    "AT": "Europe/Vienna",
    "BE": "Europe/Brussels",
    "PT": "Europe/Lisbon",
    "GR": "Europe/Athens",
    "CZ": "Europe/Prague",
    "RO": "Europe/Bucharest",
    "AZ": "Asia/Baku",
    "KZ": "Asia/Almaty",
    "SA": "Asia/Riyadh",
    "IR": "Asia/Tehran",
    "PK": "Asia/Karachi",
    "BD": "Asia/Dhaka",
    "NG": "Africa/Lagos",
    "KE": "Africa/Nairobi",
}

SCENARIO_ALIASES: dict[str, str] = {
    "form": "form-submit",
    "form_fill": "form-submit",
    "formfill": "form-submit",
    "form-submit": "form-submit",
    "load": "load-test",
    "loadtest": "load-test",
    "load-test": "load-test",
    "smoke": "smoke",
    "health": "smoke",
    "verify": "email-verify",
    "email": "email-verify",
    "email-verify": "email-verify",
    "api": "api-discovery",
    "discover": "api-discovery",
    "api-discovery": "api-discovery",
    "full": "full-journey",
    "journey": "full-journey",
    "full-journey": "full-journey",
}

#: Keys recognised in the *data source* (Excel) file - the schema is documented in README.
EXPECTED_ROWS = "rows"

ENV_PREFIX = "WAFT_"


def load_dotenv_file(path: Union[str, Path, None], *, override: bool = False, required: bool = False) -> dict[str, str]:
    """Load a ``.env`` file into ``os.environ``.

    Uses ``python-dotenv`` when available, otherwise falls back to a small built-in parser
    supporting ``KEY=value``, ``export KEY=value``, ``#`` comments and quoted values.
    Returns a dict of the values that were loaded.
    """
    if not path:
        return {}
    target = Path(path).expanduser()
    if not target.exists():
        if required:
            raise ConfigError(f"Env file not found: {target}")
        return {}

    dotenv = import_optional("dotenv", "dotenv_values")
    if dotenv is not None:
        values = {k: v for k, v in dotenv(str(target)).items() if v is not None}
    else:  # pragma: no cover - exercised only without python-dotenv
        values = {}
        with target.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key.lower().startswith("export "):
                    key = key[7:].strip()
                value = value.strip().strip("'\"")
                values[key] = value

    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = str(value)
    return values


def env_bool(key: str, default: bool = False) -> bool:
    return parse_bool(os.environ.get(f"{ENV_PREFIX}{key}") or os.environ.get(key), default=default)


def env_int(key: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.environ.get(f"{ENV_PREFIX}{key}") or os.environ.get(key)
    return parse_int(raw, default=default)


def env_float(key: str, default: Optional[float] = None) -> Optional[float]:
    raw = os.environ.get(f"{ENV_PREFIX}{key}") or os.environ.get(key)
    return parse_float(raw, default=default)


def env_str(key: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(f"{ENV_PREFIX}{key}") or os.environ.get(key)
    return value if value not in (None, "") else default


def setup_playwright_env() -> None:
    """Prepare environment variables consumed by the Playwright driver.

    * ``PLAYWRIGHT_BROWSERS_PATH`` is respected as-is when the user sets it.
    * When the browsers live inside the current workspace (typical for sandboxes/CI),
      the executable path is exported so ``playwright install`` output is reused.
    """
    browsers_path = Path.home() / ".cache" / "ms-playwright"
    if browsers_path.is_dir() and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers_path))
    # Chromium's sandbox needs kernel privileges that containers usually lack.
    if not env_bool("ALLOW_CHROMIUM_SANDBOX", default=False):
        os.environ.setdefault("WAFT_ALLOW_CHROMIUM_SANDBOX", "0")


def resolve_device_profiles(spec: str, count: int) -> list[DeviceProfile]:
    """Expand a ``--devices`` spec into *count* concrete device profiles.

    Accepted forms::

        random                     # any profile per context (deterministic fallback)
        chrome-win-1920x1080       # a single profile reused for every context
        chrome-win-1920x1080,android-pixel7
    """
    names = split_csv(spec)
    if not names or names[0].lower() in {"random", "auto", "mix"}:
        names = list(DEVICE_PROFILES.keys())
    resolved: list[DeviceProfile] = []
    for name in names:
        key = name.strip().lower()
        if key not in DEVICE_PROFILES:
            available = ", ".join(sorted(DEVICE_PROFILES))
            raise ConfigError(f"Unknown device profile '{name}'. Available: {available}")
        resolved.append(DEVICE_PROFILES[key])
    if not resolved:
        resolved = list(DEVICE_PROFILES.values())
    return [resolved[i % len(resolved)] for i in range(max(1, count))]


def resolve_timezone(spec: Optional[str], country: Optional[str] = None) -> str:
    """Resolve a timezone from an explicit value, a country code or the default."""
    if spec:
        candidate = spec.strip()
        if candidate.upper() in {"TR", "US", "DE"}:  # country code passed by accident
            return TIMEZONE_MAP.get(candidate.upper(), DEFAULT_TIMEZONE)
        return candidate
    if country:
        mapped = TIMEZONE_MAP.get(country.strip().upper())
        if mapped:
            return mapped
    return env_str("DEFAULT_TIMEZONE", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE


def resolve_geolocation(
    explicit: Optional[str],
    country: Optional[str] = None,
    *,
    city_lookup: Optional[dict[str, tuple[float, float]]] = None,
) -> Optional[Geolocation]:
    """Resolve geolocation from ``"lat,lon[,accuracy]"`` or a country code."""
    if explicit:
        parts = split_csv(explicit)
        if len(parts) >= 2:
            try:
                lat = float(parts[0])
                lon = float(parts[1])
                accuracy = float(parts[2]) if len(parts) > 2 else 50.0
                return Geolocation(latitude=lat, longitude=lon, accuracy=accuracy)
            except ValueError as exc:
                raise ConfigError(f"Invalid --geolocation value '{explicit}': {exc}") from exc
    table = city_lookup or {
        "TR": (41.0082, 28.9784),  # Istanbul
        "US": (40.7128, -74.0060),
        "DE": (52.5200, 13.4050),
        "NL": (52.3676, 4.9041),
        "GB": (51.5074, -0.1278),
        "FR": (48.8566, 2.3522),
        "RU": (55.7558, 37.6173),
        "UA": (50.4501, 30.5234),
        "IN": (19.0760, 72.8777),
        "SG": (1.3521, 103.8198),
        "JP": (35.6762, 139.6503),
        "BR": (-23.5505, -46.6333),
        "AE": (25.2048, 55.2708),
        "IT": (41.9028, 12.4964),
        "ES": (40.4168, -3.7038),
    }
    if country:
        coords = table.get(country.strip().upper())
        if coords:
            return Geolocation(latitude=coords[0], longitude=coords[1], accuracy=100.0)
    return None


def _slug_country(tag: str) -> str:
    """Extract the country part of a locale tag (``tr-TR`` → ``TR``)."""
    parts = str(tag).replace("_", "-").split("-")
    if len(parts) >= 2:
        return parts[1].upper()
    if len(parts) == 1 and len(parts[0]) == 2:
        return parts[0].upper()
    return parts[0].upper() if parts else "US"


# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------


@dataclass
class Config:
    """Complete run configuration - the single object passed across the framework."""

    # ------------------------------------------------------------------ sources
    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    data_file: Optional[Path] = None
    sheet_name: Optional[str] = None
    url_column: str = "target_url"
    proxy_file: Optional[Path] = None
    env_file: Optional[Path] = None
    profile_file: Optional[Path] = None
    config_file: Optional[Path] = None

    # ------------------------------------------------------------------ run shape
    contexts: int = 10
    concurrency: Optional[int] = None
    iterations: int = 1
    max_targets_per_context: Optional[int] = None
    context_restart_pages: int = 25
    seed: Optional[int] = None
    rate_limit: Optional[float] = None
    shuffle_targets: bool = False
    scenario: str = "auto"
    dry_run: bool = False

    # ------------------------------------------------------------------ browser
    browser: str = "chromium"
    headless: bool = True
    slow_mo_ms: int = 0
    browser_timeout_ms: int = 15_000
    navigation_timeout_ms: int = 45_000
    default_timeout_ms: int = 20_000
    fallback_timeout_ms: int = 7_500
    action_pause_ms: int = 120
    humanize: bool = True
    humanize_min_ms: int = 8
    humanize_max_ms: int = 30
    humanize_typing_budget_ms: int = 900
    humanize_max_chars: int = 18
    block_resource_types: list[str] = field(default_factory=list)
    block_hosts: list[str] = field(default_factory=list)
    block_blank_hosts: bool = True
    device_scale_factor: Optional[float] = None
    allow_chromium_sandbox: bool = False
    browser_args: list[str] = field(default_factory=list)
    ignore_https_errors: bool = True
    chromium_channel: Optional[str] = None
    firefox_executable: Optional[str] = None
    webkit_executable: Optional[str] = None
    channel_executable: Optional[str] = None

    # ------------------------------------------------------------------ stealth
    stealth: bool = True
    canvas_noise: bool = True
    webgl_spoof: bool = True
    audio_noise: bool = True
    webrtc_block: bool = True
    permission_hardening: bool = True
    chrome_runtime_evasion: bool = False
    stealth_overrides: dict[str, Any] = field(default_factory=dict)
    verify_stealth: bool = False

    # ------------------------------------------------------------------ identity
    devices_spec: str = "random"
    locales_spec: str = "tr-TR,en-US"
    timezones_spec: str = "Europe/Istanbul"
    geolocation_spec: Optional[str] = None
    coordinate_country: Optional[str] = None
    user_agents_file: Optional[Path] = None
    user_agents: list[str] = field(default_factory=list)
    extra_headers: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ proxies
    proxy_mode: str = "auto"  # off | require | auto
    proxy_health_check: bool = True
    proxy_cycle: bool = True
    proxy_max_failures: int = 3
    proxy_cooldown_s: float = 120.0
    proxy_health_timeout_ms: int = 12_000
    proxy_health_url: str = "https://api.ipify.org?format=json"
    proxy_geo_lookup: bool = True
    proxy_fail_closed: bool = False

    # ------------------------------------------------------------------ network
    log_network: bool = True
    log_network_headers: bool = False
    log_request_body: bool = False
    log_response_body: bool = False
    log_response_body_max_chars: int = 2048
    log_console: bool = True
    log_dialog_events: bool = True
    log_api_candidates: bool = True
    capture_har: bool = False
    har_mode: str = "minimal"
    har_content: str = "embed"
    ignore_network_hosts: list[str] = field(default_factory=list)
    sensitive_headers: list[str] = field(default_factory=list)
    redact: bool = True
    track_request_bytes: bool = True

    # ------------------------------------------------------------------ forms
    submit_default: bool = True
    submit_selector: Optional[str] = None
    submit_button_text: Optional[str] = None
    selectors_file: Optional[Path] = None
    selectors: dict[str, str] = field(default_factory=dict)
    field_aliases: dict[str, list[str]] = field(default_factory=dict)
    field_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    fill_checkboxes: bool = True
    fill_selects: bool = True
    clear_before_fill: bool = True
    field_match_threshold: float = 45.0
    detect_captcha: bool = True
    captcha_action: str = "error"  # error | skip | continue
    success_url_regex: Optional[str] = None
    success_selector: Optional[str] = None
    error_selector: Optional[str] = None
    success_text: list[str] = field(default_factory=list)
    failure_text: list[str] = field(default_factory=list)
    outcome_timeout_ms: int = 20_000
    save_storage_state: bool = True
    storage_state_dir: Optional[Path] = None

    # ------------------------------------------------------------------ steps / scenario
    steps_file: Optional[Path] = None
    default_steps: list[dict[str, Any]] = field(default_factory=list)
    scenario_pause_ms: int = 0

    # ------------------------------------------------------------------ IMAP
    imap_enabled: bool = False
    imap_host: Optional[str] = None
    imap_port: int = 993
    imap_ssl: bool = True
    imap_starttls: bool = False
    imap_username: Optional[str] = None
    imap_password: Optional[str] = None
    imap_mailbox: str = "INBOX"
    imap_timeout_s: float = 120.0
    imap_poll_interval_s: float = 5.0
    imap_search_days: int = 2
    imap_allow_unseen_only: bool = True
    imap_mark_seen: bool = False
    imap_mark_processed: bool = False
    imap_link_regex: str = (
        r"https?://[^\s\"'<>)]*(?:verify|confirm|activate|activation|account|token|auth)"
        r"[^\s\"'<>)]*"
    )
    imap_otp_regex: str = r"(?<!\d)(\d{4,8})(?!\d)"
    imap_subject_regex: Optional[str] = None
    imap_sender_filter: Optional[str] = None
    imap_link_must_match_target: bool = True
    imap_max_links: int = 25
    imap_http_check: bool = False

    # ------------------------------------------------------------------ retries / artifacts
    retries: int = 1
    retry_backoff_s: float = 2.0
    retry_backoff_max_s: float = 30.0
    retry_only_retryable: bool = True
    artifacts_dir: Path = field(default_factory=lambda: Path("artifacts"))
    screenshots: bool = True
    screenshot_full_page: bool = True
    screenshot_on_success: bool = True
    screenshot_on_failure: bool = True
    screenshot_quality: Optional[int] = None
    trace_mode: str = "on-failure"  # on | off | on-failure | retain-on-failure
    trace_screenshots: bool = True
    trace_snapshots: bool = True
    trace_sources: bool = True
    save_html_on_failure: bool = True
    save_network_log: bool = True
    failure_bundle: bool = True
    zip_bundles: bool = True
    max_artifacts_mb: int = 2048
    keep_hars: bool = True
    junit_xml: bool = True
    summary_json: bool = True
    csv_report: bool = True
    log_file: Optional[Path] = None

    # ------------------------------------------------------------------ misc
    log_level: str = "INFO"
    quiet: bool = False
    no_color: bool = False
    fail_fast: bool = False
    stop_on_error_rate: Optional[float] = None
    per_context_lock: bool = True
    exit_on_sigint: bool = True

    # ------------------------------------------------------------------ derived (filled by validate)
    resolved_concurrency: int = 1
    device_profiles: list[DeviceProfile] = field(default_factory=list)
    config_digest: dict[str, Any] = field(default_factory=dict)

    # ================================================================== helpers
    @property
    def artifact_root(self) -> Path:
        return Path(self.artifacts_dir).expanduser()

    def context_dir(self, run_id: str, context_id: str) -> Path:
        return self.artifact_root / run_id / "contexts" / context_id

    def run_dir(self, run_id: str) -> Path:
        return self.artifact_root / run_id

    # ================================================================== loading
    @classmethod
    def from_sources(
        cls,
        args: Optional[argparse.Namespace] = None,
        *,
        env_file: Optional[Union[str, Path]] = None,
        config_file: Optional[Union[str, Path]] = None,
    ) -> "Config":
        """Build a config from CLI namespace → env → profile/config file → defaults."""
        if env_file is None and args is not None:
            env_file = getattr(args, "env", None) or getattr(args, "env_file", None)
        cli_env = env_file
        if cli_env:
            load_dotenv_file(cli_env, required=True)

        # Implicit .env discovery in the working directory.
        for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
            if candidate.exists():
                load_dotenv_file(candidate)
                break

        cfg = cls()
        cfg.env_file = Path(cli_env).expanduser() if cli_env else None

        file_layers: list[dict[str, Any]] = []
        profile_path = config_file or (getattr(args, "profile", None) if args else None) or env_str("PROFILE_FILE")
        if profile_path:
            cfg.profile_file = Path(profile_path).expanduser()
            payload = read_json(cfg.profile_file, default=None)
            if payload is None:
                raise ConfigError(f"Profile file not found: {cfg.profile_file}")
            if not isinstance(payload, dict):
                raise ConfigError(f"Profile file must contain a JSON object: {cfg.profile_file}")
            file_layers.append(payload)

        external_config = env_str("CONFIG_FILE")
        if external_config:
            cfg.config_file = Path(external_config).expanduser()
            payload = read_json(cfg.config_file, default=None)
            if not isinstance(payload, dict):
                raise ConfigError(f"Config file must contain a JSON object: {cfg.config_file}")
            file_layers.append(payload)

        if file_layers:
            merged: dict[str, Any] = {}
            for layer in file_layers:
                deep_merge(merged, layer)
            cfg.apply_dict(merged)

        cfg.apply_env()
        if args is not None:
            cfg.apply_cli(args)
        cfg.validate()
        return cfg

    # ------------------------------------------------------------------ layers
    _STR_FIELDS = {
        "sheet_name",
        "url_column",
        "scenario",
        "browser",
        "devices_spec",
        "locales_spec",
        "timezones_spec",
        "geolocation_spec",
        "coordinate_country",
        "proxy_mode",
        "log_level",
        "captcha_action",
        "trace_mode",
        "har_mode",
        "har_content",
        "imap_host",
        "imap_username",
        "imap_password",
        "imap_mailbox",
        "imap_link_regex",
        "imap_otp_regex",
        "imap_subject_regex",
        "imap_sender_filter",
        "success_url_regex",
        "success_selector",
        "error_selector",
        "submit_selector",
        "submit_button_text",
        "proxy_health_url",
        "chromium_channel",
        "channel_executable",
        "firefox_executable",
        "webkit_executable",
    }
    _PATH_FIELDS = {
        "data_file",
        "proxy_file",
        "env_file",
        "profile_file",
        "config_file",
        "selectors_file",
        "steps_file",
        "user_agents_file",
        "storage_state_dir",
        "artifacts_dir",
        "log_file",
    }
    _BOOL_FIELDS = {
        "shuffle_targets",
        "dry_run",
        "headless",
        "humanize",
        "block_blank_hosts",
        "allow_chromium_sandbox",
        "ignore_https_errors",
        "stealth",
        "canvas_noise",
        "webgl_spoof",
        "audio_noise",
        "webrtc_block",
        "permission_hardening",
        "chrome_runtime_evasion",
        "verify_stealth",
        "proxy_health_check",
        "proxy_cycle",
        "proxy_geo_lookup",
        "proxy_fail_closed",
        "log_network",
        "log_network_headers",
        "log_request_body",
        "log_response_body",
        "log_console",
        "log_dialog_events",
        "log_api_candidates",
        "capture_har",
        "redact",
        "track_request_bytes",
        "submit_default",
        "fill_checkboxes",
        "fill_selects",
        "clear_before_fill",
        "detect_captcha",
        "save_storage_state",
        "imap_enabled",
        "imap_ssl",
        "imap_starttls",
        "imap_allow_unseen_only",
        "imap_mark_seen",
        "imap_mark_processed",
        "imap_link_must_match_target",
        "imap_http_check",
        "retry_only_retryable",
        "screenshots",
        "screenshot_full_page",
        "screenshot_on_success",
        "screenshot_on_failure",
        "trace_screenshots",
        "trace_snapshots",
        "trace_sources",
        "save_html_on_failure",
        "save_network_log",
        "failure_bundle",
        "zip_bundles",
        "keep_hars",
        "junit_xml",
        "summary_json",
        "csv_report",
        "quiet",
        "no_color",
        "fail_fast",
        "per_context_lock",
        "exit_on_sigint",
    }
    _INT_FIELDS = {
        "contexts",
        "concurrency",
        "iterations",
        "max_targets_per_context",
        "context_restart_pages",
        "seed",
        "slow_mo_ms",
        "browser_timeout_ms",
        "navigation_timeout_ms",
        "default_timeout_ms",
        "fallback_timeout_ms",
        "action_pause_ms",
        "humanize_min_ms",
        "humanize_max_ms",
        "humanize_typing_budget_ms",
        "humanize_max_chars",
        "proxy_max_failures",
        "proxy_health_timeout_ms",
        "log_response_body_max_chars",
        "outcome_timeout_ms",
        "scenario_pause_ms",
        "imap_port",
        "imap_search_days",
        "imap_max_links",
        "retries",
        "screenshot_quality",
        "max_artifacts_mb",
    }
    _FLOAT_FIELDS = {
        "rate_limit",
        "field_match_threshold",
        "proxy_cooldown_s",
        "retry_backoff_s",
        "retry_backoff_max_s",
        "imap_timeout_s",
        "imap_poll_interval_s",
        "stop_on_error_rate",
    }
    _LIST_FIELDS = {
        "block_resource_types",
        "block_hosts",
        "browser_args",
        "user_agents",
        "ignore_network_hosts",
        "sensitive_headers",
        "success_text",
        "failure_text",
        "default_steps",
    }
    _DICT_FIELDS = {
        "stealth_overrides",
        "extra_headers",
        "selectors",
        "field_aliases",
        "field_types",
    }

    def apply_dict(self, payload: dict[str, Any], *, source: str = "profile") -> None:
        """Apply a mapping of ``field -> value`` with type coercion and validation."""
        valid = {f.name for f in fields(self)}
        for key, value in payload.items():
            if key not in valid:
                raise ConfigError(f"Unknown configuration key '{key}' in {source}")
            if key in {"artifacts_dir", "log_file"} and isinstance(value, str):
                value = Path(value.format(run_id="{run_id}", ts="{ts}")).expanduser()
            setattr(self, key, value)

    def apply_env(self) -> None:
        """Overlay environment variables (``WAFT_<FIELD>`` or bare ``<FIELD>``)."""
        for name in self._STR_FIELDS:
            value = env_str(name.upper())
            if value is not None:
                setattr(self, name, value)
        for name in self._PATH_FIELDS:
            value = env_str(name.upper())
            if value:
                setattr(self, name, Path(value).expanduser())
        for name in self._BOOL_FIELDS:
            raw = os.environ.get(f"{ENV_PREFIX}{name.upper()}")
            if raw is None:
                raw = os.environ.get(name.upper())
            if raw is not None:
                setattr(self, name, parse_bool(raw, default=getattr(self, name)))
        for name in self._INT_FIELDS:
            raw = os.environ.get(f"{ENV_PREFIX}{name.upper()}")
            if raw is not None:
                parsed = parse_int(raw, default=None)
                if parsed is not None:
                    setattr(self, name, parsed)
        for name in self._FLOAT_FIELDS:
            raw = os.environ.get(f"{ENV_PREFIX}{name.upper()}")
            if raw is not None:
                parsed = parse_float(raw, default=None)
                if parsed is not None:
                    setattr(self, name, parsed)
        for name in self._LIST_FIELDS:
            raw = env_str(name.upper())
            if raw:
                setattr(self, name, split_csv(raw))
        for name in self._DICT_FIELDS:
            raw = env_str(name.upper())
            if raw:
                payload = read_json(raw, default=None) if Path(raw).suffix == ".json" else None
                if payload is None:
                    payload = _parse_kv_pairs(raw)
                if isinstance(payload, dict):
                    setattr(self, name, payload)

    def apply_cli(self, args: argparse.Namespace) -> None:
        """Overlay explicitly provided CLI arguments (only non-``None`` values win)."""
        mapping = {
            "data": "data_file",
            "proxy": "proxy_file",
            "env": "env_file",
            "profile": "profile_file",
            "contexts": "contexts",
            "concurrency": "concurrency",
            "iterations": "iterations",
            "url_column": "url_column",
            "sheet": "sheet_name",
            "browser": "browser",
            "timezone": "timezones_spec",
            "locale": "locales_spec",
            "devices": "devices_spec",
            "user_agents": "user_agents_file",
            "geolocation": "geolocation_spec",
            "proxy_mode": "proxy_mode",
            "proxy_health_url": "proxy_health_url",
            "scenario": "scenario",
            "selectors": "selectors_file",
            "steps": "steps_file",
            "artifacts": "artifacts_dir",
            "log_file": "log_file",
            "trace": "trace_mode",
            "imap_host": "imap_host",
            "imap_port": "imap_port",
            "imap_user": "imap_username",
            "imap_password": "imap_password",
            "imap_mailbox": "imap_mailbox",
            "imap_timeout": "imap_timeout_s",
            "imap_link_regex": "imap_link_regex",
            "imap_otp_regex": "imap_otp_regex",
            "imap_subject_regex": "imap_subject_regex",
            "imap_sender": "imap_sender_filter",
            "proxy_cooldown": "proxy_cooldown_s",
            "rate_limit": "rate_limit",
            "retries": "retries",
            "retry_backoff": "retry_backoff_s",
            "outcome_timeout": "outcome_timeout_ms",
            "field_match_threshold": "field_match_threshold",
            "humanize_typing_budget_ms": "humanize_typing_budget_ms",
        }
        for attr, field_name in mapping.items():
            value = getattr(args, attr, None)
            if value is None or value == [] or value == {}:
                continue
            setattr(self, field_name, value)

        if getattr(args, "headless", None):
            self.headless = True
        if getattr(args, "headful", False):
            self.headless = False
        if getattr(args, "headed", False):
            self.headless = False
        if getattr(args, "stealth", None) is not None:
            self.stealth = bool(args.stealth)
        if getattr(args, "no_canvas_noise", False):
            self.canvas_noise = False
        if getattr(args, "no_webgl_spoof", False):
            self.webgl_spoof = False
        if getattr(args, "no_audio_noise", False):
            self.audio_noise = False
        if getattr(args, "allow_webrtc", False):
            self.webrtc_block = False
        if getattr(args, "no_humanize", False):
            self.humanize = False
        if getattr(args, "no_network_log", False):
            self.log_network = False
        if getattr(args, "log_headers", False):
            self.log_network_headers = True
        if getattr(args, "log_bodies", False):
            self.log_request_body = True
            self.log_response_body = True
        if getattr(args, "verbose", 0) >= 2:
            self.log_network_headers = True
        if getattr(args, "capture_har", False):
            self.capture_har = True
        if getattr(args, "save_storage_state", False):
            self.save_storage_state = True
        if getattr(args, "no_storage_state", False):
            self.save_storage_state = False
        if getattr(args, "proxy_cycle", None) is not None:
            self.proxy_cycle = bool(args.proxy_cycle)
        if getattr(args, "no_redact", False):
            self.redact = False
        if getattr(args, "no_screenshots", False):
            self.screenshots = False
            self.screenshot_on_success = False
            self.screenshot_on_failure = False
        if getattr(args, "no_trace", False):
            self.trace_mode = "off"
        if getattr(args, "no_imap", False):
            self.imap_enabled = False
        if getattr(args, "imap", False):
            self.imap_enabled = True
        if getattr(args, "no_fail_fast", False):
            self.fail_fast = False
        if getattr(args, "fail_fast", False):
            self.fail_fast = True
        if getattr(args, "allow_chromium_sandbox", None):
            self.allow_chromium_sandbox = True
        if getattr(args, "block_resource_types", None):
            self.block_resource_types = split_csv(args.block_resource_types)
        if getattr(args, "block_hosts", None):
            self.block_hosts = split_csv(args.block_hosts)
        if getattr(args, "header", None):
            self.extra_headers.update(_parse_kv_pairs(",".join(args.header)))
        if getattr(args, "browser_arg", None):
            self.browser_args.extend(args.browser_arg)
        if getattr(args, "verify_stealth", False):
            self.verify_stealth = True
        if getattr(args, "quiet", False):
            self.quiet = True
        if getattr(args, "no_color", False):
            self.no_color = True
        if getattr(args, "max_targets_per_context", None):
            self.max_targets_per_context = args.max_targets_per_context
        if getattr(args, "context_restart_pages", None):
            self.context_restart_pages = args.context_restart_pages
        if getattr(args, "shuffle", False):
            self.shuffle_targets = True
        if getattr(args, "seed", None) is not None:
            self.seed = args.seed
        if getattr(args, "dry_run", False):
            self.dry_run = True
        if getattr(args, "imap_timeout", None) is not None:
            self.imap_timeout_s = float(args.imap_timeout)
        verbose = getattr(args, "verbose", 0) or 0
        if verbose == 1:
            self.log_level = "DEBUG"
        elif verbose >= 2:
            self.log_level = "DEBUG"
        if getattr(args, "debug", False):
            self.log_level = "DEBUG"
        if getattr(args, "log_level", None):
            self.log_level = str(args.log_level).upper()

    # ------------------------------------------------------------------ validate
    def validate(self) -> "Config":
        """Normalise + sanity-check the configuration; raises :class:`ConfigError`."""
        if self.contexts < 1:
            raise ConfigError("--contexts must be >= 1")
        if self.concurrency is not None and self.concurrency < 1:
            raise ConfigError("--concurrency must be >= 1")
        if self.iterations < 1:
            raise ConfigError("--iterations must be >= 1")
        if self.retries < 0:
            raise ConfigError("--retries must be >= 0")

        self.browser = (self.browser or "chromium").lower()
        if self.browser not in {"chromium", "chrome", "msedge", "firefox", "webkit"}:
            raise ConfigError("--browser must be one of chromium|firefox|webkit")
        if self.proxy_mode not in {"off", "auto", "require"}:
            raise ConfigError("--proxy-mode must be one of off|auto|require")
        if self.trace_mode not in {"on", "off", "on-failure", "retain-on-failure"}:
            raise ConfigError("--trace must be one of on|off|on-failure|retain-on-failure")
        if self.captcha_action not in {"error", "skip", "continue"}:
            raise ConfigError("--captcha-action must be one of error|skip|continue")
        if self.imap_ssl and self.imap_starttls:
            raise ConfigError("--imap-starttls cannot be combined with implicit SSL")

        if self.slow_mo_ms < 0:
            raise ConfigError("--slow-mo must be >= 0")
        if self.humanize_min_ms > self.humanize_max_ms:
            self.humanize_min_ms, self.humanize_max_ms = self.humanize_max_ms, self.humanize_min_ms
        if self.outcome_timeout_ms <= 0:
            self.outcome_timeout_ms = 20_000
        if self.navigation_timeout_ms <= 0:
            self.navigation_timeout_ms = 45_000
        if self.default_timeout_ms <= 0:
            self.default_timeout_ms = 20_000

        # Scenario alias resolution.
        key = str(self.scenario or "auto").lower().strip()
        if key == "auto":
            self.scenario = "auto"
        else:
            self.scenario = SCENARIO_ALIASES.get(key, key)
            if self.scenario not in {"auto", "form-submit", "load-test", "smoke", "email-verify", "api-discovery", "full-journey"}:
                raise ConfigError(
                    "Unknown scenario '%s'. Use auto|load-test|smoke|form-submit|email-verify|api-discovery|full-journey" % key
                )

        # Artifacts/log paths.
        self.artifacts_dir = Path(self.artifacts_dir).expanduser()
        if self.log_file:
            self.log_file = Path(str(self.log_file).format(run_id="{run_id}", ts="{ts}")).expanduser()

        # Derived values.
        self.resolved_concurrency = max(1, min(self.concurrency or min(self.contexts, 8), self.contexts))
        if self.concurrency is None:
            self.concurrency = self.resolved_concurrency

        if self.imap_host and not self.imap_username:
            self.imap_username = env_str("IMAP_USER") or self.imap_username
        if self.imap_username and not self.imap_password:
            self.imap_password = env_str("IMAP_PASSWORD")
        if self.imap_enabled and not (self.imap_host and self.imap_username and self.imap_password):
            raise ConfigError(
                "IMAP is enabled but incomplete: set --imap-host/--imap-user/--imap-password "
                "or the WAFT_IMAP_* / IMAP_* environment variables"
            )

        if self.data_file:
            self.data_file = Path(self.data_file).expanduser()
        if self.proxy_file:
            self.proxy_file = Path(self.proxy_file).expanduser()
        if self.selectors_file:
            self.selectors_file = Path(self.selectors_file).expanduser()
            payload = read_json(self.selectors_file, default={}) or {}
            merged = dict(payload.get("selectors", payload)) if isinstance(payload, dict) else {}
            self.selectors = {**merged, **self.selectors}
            if isinstance(payload, dict):
                if payload.get("field_aliases"):
                    self.field_aliases = {**payload["field_aliases"], **self.field_aliases}
                if payload.get("field_types"):
                    self.field_types = {**payload["field_types"], **self.field_types}
        if self.steps_file:
            self.steps_file = Path(self.steps_file).expanduser()
            payload = read_json(self.steps_file, default=None)
            if payload is None:
                raise ConfigError(f"Steps file not found: {self.steps_file}")
            if isinstance(payload, dict):
                self.default_steps = payload.get("steps", []) or self.default_steps
                if payload.get("field_aliases"):
                    self.field_aliases = {**payload["field_aliases"], **self.field_aliases}
                if payload.get("selectors"):
                    self.selectors = {**payload["selectors"], **self.selectors}
            elif isinstance(payload, list):
                self.default_steps = payload

        if self.storage_state_dir:
            self.storage_state_dir = Path(self.storage_state_dir).expanduser()

        # Guard against accidentally hammering production with an open-ended rate.
        if self.rate_limit is not None and self.rate_limit <= 0:
            self.rate_limit = None

        self.device_profiles = resolve_device_profiles(self.devices_spec, max(1, self.contexts))
        if self.seed is not None:
            import random

            random.seed(self.seed)

        self._fallback_headers()
        self.config_digest = self.digest()
        return self

    def _fallback_headers(self) -> None:
        """Merge custom headers coming from the selectors/profile layers."""
        locale = split_csv(self.locales_spec)[0] if split_csv(self.locales_spec) else "en-US"
        defaults = {
            "Accept-Language": locale,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        }
        for key, value in defaults.items():
            self.extra_headers.setdefault(key, value)

    # ------------------------------------------------------------------ misc
    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        """Serialise the config (secrets masked by default) for the run manifest."""
        payload = asdict(self)
        payload.pop("device_profiles", None)
        payload = {k: (str(v) if isinstance(v, Path) else v) for k, v in payload.items()}
        if redact:
            for key in ("imap_password", "imap_username"):
                if payload.get(key):
                    payload[key] = "***"
            if payload.get("extra_headers"):
                payload["extra_headers"] = {
                    k: ("***" if k.lower() in {"authorization", "cookie", "x-api-key"} else v)
                    for k, v in payload["extra_headers"].items()
                }
        return payload

    def digest(self) -> dict[str, Any]:
        """Deterministic hash of the meaningful (secret-free) config parts."""
        meaningful = self.to_dict(redact=True)
        for transient in ("config_digest", "resolved_concurrency"):
            meaningful.pop(transient, None)
        blob = json_dumps(meaningful, sort_keys=True)
        return {
            "sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
            "fields": len(meaningful),
            "browser": self.browser,
            "contexts": self.contexts,
            "concurrency": self.resolved_concurrency,
            "iterations": self.iterations,
            "scenario": self.scenario,
            "imap": bool(self.imap_enabled),
            "stealth": bool(self.stealth),
        }

    def check_environment(self) -> list[str]:
        """Return human-readable warnings about the runtime environment."""
        warnings: list[str] = []
        if not self.allow_chromium_sandbox:
            os.environ.setdefault("WAFT_ALLOW_CHROMIUM_SANDBOX", "0")
        if self.browser in {"chromium", "chrome", "msedge"}:
            exe_names = {
                "chromium": ("chrome", "chromium", "chrome-headless-shell"),
                "chrome": ("chrome", "google-chrome"),
                "msedge": ("msedge", "microsoft-edge"),
            }.get(self.browser, ("chrome",))
            if not _find_browser_binary(exe_names):
                warnings.append(
                    "Playwright browser binaries were not found - run: python -m playwright install "
                    f"{'--with-deps ' if os.name != 'nt' else ''}{self.browser}"
                )
        if self.trace_mode != "off":
            pass  # tracing ships with the driver; nothing else to check
        if self.contexts > 25:
            warnings.append(
                f"{self.contexts} contexts requested: on a machine without swap this may exhaust RAM "
                "(each Chromium context costs roughly 60-120 MB). Consider --concurrency."
            )
        return warnings


def _find_browser_binary(names: tuple[str, ...]) -> bool:
    """Check Playwright's browser cache (and PATH) for a usable Chromium binary."""
    cache_roots = []
    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_path and env_path not in {"0"}:
        cache_roots.append(Path(env_path))
    cache_roots.append(Path.home() / ".cache" / "ms-playwright")
    for root in cache_roots:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            if not any(name in entry.name for name in ("chromium", "chrome", "firefox", "webkit")):
                continue
            for binary_name in names:
                for candidate in entry.rglob(binary_name):
                    if candidate.is_file():
                        return True
    return any(shutil.which(name) for name in names)


def _parse_kv_pairs(raw: str) -> dict[str, str]:
    """Parse ``k1=v1;k2=v2`` / ``k1=v1,k2=v2`` CLI values into a dict."""
    payload: dict[str, str] = {}
    for token in str(raw).replace(";", ",").split(","):
        token = token.strip()
        if not token or "=" not in token:
            continue
        key, _, value = token.partition("=")
        payload[key.strip()] = value.strip()
    return payload


# --------------------------------------------------------------------------------------
# CLI parser
# --------------------------------------------------------------------------------------

PROG = "waft"


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the ``argparse`` CLI (documented in README.md and ``--help``)."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "WAFT - Web Automation & Form-Test Framework.\n"
            "Multi-context (isolated browser contexts) + stealth + proxy rotation + form filling + "
            "network logging + IMAP verification, driven by an Excel/JSON data source."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m waft --data data/targets.xlsx --proxy-file proxies.txt --contexts 10\n"
            "  python -m waft --data data/targets.json --contexts 4 --concurrency 2 --headful --verbose\n"
            "  python -m waft --scenario load-test --data data/targets.xlsx --contexts 25 --iterations 3\n"
            "  python -m waft --dry-run --data data/targets.xlsx --contexts 12 --print-config\n"
        ),
    )

    src = parser.add_argument_group("data & configuration sources")
    src.add_argument("-d", "--data", dest="data", type=Path, help="Excel (.xlsx/.xls) or JSON/CSV file with target URL + form data")
    src.add_argument("-p", "--proxy-file", dest="proxy", type=Path, help="Proxy list file (one per line, supports user:pass@host:port, socks5://…)")
    src.add_argument("--env-file", dest="env", type=Path, help="Explicit .env file to load (defaults to ./.env)")
    src.add_argument("--profile", dest="profile", type=Path, help="JSON profile file with configuration overrides")
    src.add_argument("--url-column", dest="url_column", type=str, help="Column name holding the target URL")
    src.add_argument("--sheet", "--sheet-name", dest="sheet", type=str, help="Excel sheet name (default: first sheet)")
    src.add_argument("--urls", dest="urls", type=str, help="Comma-separated URL list used when no data file is given")

    shape = parser.add_argument_group("run shape")
    shape.add_argument("-c", "--contexts", dest="contexts", type=int, help="Number of isolated browser contexts to create")
    shape.add_argument("-j", "--concurrency", dest="concurrency", type=int, help="How many contexts may run in parallel")
    shape.add_argument("-i", "--iterations", dest="iterations", type=int, help="How many times every target row is executed")
    shape.add_argument("--max-targets-per-context", dest="max_targets_per_context", type=int, help="Only run the first N targets inside each context")
    shape.add_argument("--context-restart-pages", dest="context_restart_pages", type=int, help="Recycle a context after N pages to bound memory usage")
    shape.add_argument("--shuffle", dest="shuffle", action="store_true", help="Shuffle target order per context (breaks login rate-limit patterns)")
    shape.add_argument("--seed", dest="seed", type=int, help="Random seed for deterministic shuffling/jitter")
    shape.add_argument("--rate-limit", dest="rate_limit", type=float, help="Global cap of navigations per second (token bucket)")
    shape.add_argument("--scenario", dest="scenario", type=str, help="auto|load-test|smoke|form-submit|email-verify|api-discovery|full-journey")
    shape.add_argument("--dry-run", dest="dry_run", action="store_true", help="Plan the run (contexts, proxies, rows) without launching a browser")

    browser = parser.add_argument_group("browser")
    browser.add_argument("--browser", dest="browser", choices=["chromium", "chrome", "msedge", "firefox", "webkit"], help="Browser engine")
    browser.add_argument("--headless", dest="headless", action="store_true", default=None, help="Force headless mode (default)")
    browser.add_argument("--headful", dest="headful", action="store_true", help="Run with a visible window (alias: --headed)")
    browser.add_argument("--headed", dest="headed", action="store_true", help="Alias for --headful")
    browser.add_argument("--slow-mo", dest="slow_mo", type=int, help="Playwright slow_mo in milliseconds")
    browser.add_argument("--timeout", dest="default_timeout", type=int, help="Default element timeout (ms)")
    browser.add_argument("--navigation-timeout", dest="navigation_timeout", type=int, help="Navigation timeout (ms)")
    browser.add_argument("--outcome-timeout", dest="outcome_timeout", type=int, help="Success/error detection timeout after submit (ms)")
    browser.add_argument("--action-pause", dest="action_pause", type=int, help="Artificial pause between actions (ms)")
    browser.add_argument("--no-humanize", dest="no_humanize", action="store_true", help="Disable human-like typing/mouse jitter")
    browser.add_argument(
        "--typing-budget",
        dest="humanize_typing_budget_ms",
        type=int,
        help="Maximum time (ms) spent typing one field with human-like delay",
    )
    browser.add_argument("--allow-chromium-sandbox", dest="allow_chromium_sandbox", action="store_true", help="Do not pass --no-sandbox")
    browser.add_argument("--channel", dest="chromium_channel", type=str, help="Use an installed Chrome channel, e.g. chrome|msedge")
    browser.add_argument("--browser-arg", dest="browser_arg", action="append", help="Extra Chromium flag (repeatable)")
    browser.add_argument("--block-resource-types", dest="block_resource_types", type=str, help="Comma list, e.g. image,media,font")
    browser.add_argument("--block-hosts", dest="block_hosts", type=str, help="Comma list of hosts/patterns to abort")
    browser.add_argument("--capture-har", dest="capture_har", action="store_true", help="Record a HAR per context (secrets are redacted afterwards)")

    stealth = parser.add_argument_group("stealth / anti-detection")
    stealth.add_argument("--stealth", dest="stealth", action="store_true", default=None, help="Force-enable the stealth layer")
    stealth.add_argument("--no-stealth", dest="stealth", action="store_false", help="Disable the stealth layer")
    stealth.add_argument("--no-canvas-noise", dest="no_canvas_noise", action="store_true", help="Disable canvas fingerprint perturbation")
    stealth.add_argument("--no-webgl-spoof", dest="no_webgl_spoof", action="store_true", help="Disable WebGL vendor/renderer spoofing")
    stealth.add_argument("--no-audio-noise", dest="no_audio_noise", action="store_true", help="Disable AudioContext fingerprint noise")
    stealth.add_argument("--allow-webrtc", dest="allow_webrtc", action="store_true", help="Do not block WebRTC (it can leak the real IP)")
    stealth.add_argument("--verify-stealth", dest="verify_stealth", action="store_true", help="Assert stealth invariants after each navigation")
    stealth.add_argument("--devices", dest="devices", type=str, help="Device profiles: random or a comma list (see waft.config.DEVICE_PROFILES)")
    stealth.add_argument("--locale", dest="locale", type=str, help="Comma list of locales cycled across contexts")
    stealth.add_argument("--timezone", dest="timezone", type=str, help="Time zone id or country code (TR/US/…)")
    stealth.add_argument("--geolocation", dest="geolocation", type=str, help="lat,lon[,accuracy] or a country code")
    stealth.add_argument("--user-agents", dest="user_agents", type=Path, help="File with one user-agent per line (overrides device profiles)")
    stealth.add_argument("--header", dest="header", action="append", help="Extra request header 'Name: value' (repeatable)")

    proxy = parser.add_argument_group("proxy rotation")
    proxy.add_argument("--proxy-mode", dest="proxy_mode", choices=["off", "auto", "require"], help="off=never, auto=when file given, require=error if unusable")
    proxy.add_argument("--no-proxy-health-check", dest="no_proxy_health_check", action="store_true", help="Skip pre-flight proxy reachability checks")
    proxy.add_argument("--proxy-health-url", dest="proxy_health_url", type=str, help="URL used for proxy health checks")
    proxy.add_argument("--proxy-cooldown", dest="proxy_cooldown", type=float, help="Cooldown seconds before a failing proxy is reused")
    proxy.add_argument("--proxy-cycle", dest="proxy_cycle", action="store_true", default=None, help="Rotate proxies round-robin in list order (default)")
    proxy.add_argument("--no-proxy-cycle", dest="proxy_cycle", action="store_false", help="Pick the least-used healthy proxy instead of round-robin")
    proxy.add_argument("--proxy-fail-closed", dest="proxy_fail_closed", action="store_true", help="Abort when a proxy is required but unavailable")

    traffic = parser.add_argument_group("network traffic logging")
    traffic.add_argument("--log-network", dest="log_network", action="store_true", default=None, help="Force-enable request/response logging")
    traffic.add_argument("--no-network-log", dest="no_network_log", action="store_true", help="Disable request/response logging")
    traffic.add_argument("--log-headers", dest="log_headers", action="store_true", help="Include request/response headers in the log")
    traffic.add_argument("--log-bodies", dest="log_bodies", action="store_true", help="Include (redacted, truncated) request/response bodies")
    traffic.add_argument("--ignore-network-host", dest="ignore_network_hosts", action="append", help="Host to exclude from traffic logs (repeatable)")

    form = parser.add_argument_group("forms & selectors")
    form.add_argument("--selectors", dest="selectors", type=Path, help="JSON file with CSS selector overrides per field")
    form.add_argument("--steps", dest="steps", type=Path, help="JSON file describing a declarative step workflow")
    form.add_argument("--no-submit", dest="no_submit", action="store_true", help="Fill forms but never press submit")
    form.add_argument(
        "--field-match-threshold",
        dest="field_match_threshold",
        type=float,
        help="Minimum confidence score (0-100) required to fill a field heuristically",
    )
    form.add_argument("--captcha-action", dest="captcha_action", choices=["error", "skip", "continue"], help="What to do when a CAPTCHA is detected")

    imap = parser.add_argument_group("IMAP verification")
    imap.add_argument("--imap", dest="imap", action="store_true", help="Force-enable IMAP email verification")
    imap.add_argument("--no-imap", dest="no_imap", action="store_true", help="Disable IMAP even if a row asks for it")
    imap.add_argument("--imap-host", dest="imap_host", type=str, help="IMAP server hostname")
    imap.add_argument("--imap-port", dest="imap_port", type=int, help="IMAP port (993 SSL / 143 STARTTLS)")
    imap.add_argument("--imap-user", dest="imap_user", type=str, help="IMAP username (usually the email address)")
    imap.add_argument("--imap-password", dest="imap_password", type=str, help="IMAP password / app password")
    imap.add_argument("--imap-mailbox", dest="imap_mailbox", type=str, help="Mailbox to search")
    imap.add_argument("--imap-timeout", dest="imap_timeout", type=float, help="Seconds to wait for the verification email")
    imap.add_argument("--imap-link-regex", dest="imap_link_regex", type=str, help="Regex used to extract the verification link")
    imap.add_argument("--imap-otp-regex", dest="imap_otp_regex", type=str, help="Regex used to extract the OTP code")
    imap.add_argument("--imap-subject-regex", dest="imap_subject_regex", type=str, help="Subject filter regex")
    imap.add_argument("--imap-sender", dest="imap_sender", type=str, help="From-address filter (substring or regex)")

    out = parser.add_argument_group("artifacts & output")
    out.add_argument("-o", "--artifacts", dest="artifacts", type=Path, help="Artifacts root directory")
    out.add_argument("--log-file", dest="log_file", type=Path, help="Rotating log file path")
    out.add_argument("--log-level", dest="log_level", choices=list(LOG_LEVEL_HELP), help="Console log level")
    out.add_argument("--trace", dest="trace", choices=["on", "off", "on-failure", "retain-on-failure"], help="Playwright tracing mode")
    out.add_argument("--no-trace", dest="no_trace", action="store_true", help="Disable tracing entirely")
    out.add_argument("--no-screenshots", dest="no_screenshots", action="store_true", help="Disable all screenshots")
    out.add_argument("--save-storage-state", dest="save_storage_state", action="store_true", help="Persist cookies/localStorage per context")
    out.add_argument("--no-storage-state", dest="no_storage_state", action="store_true", help="Do not persist storage state")
    out.add_argument("--bundle", dest="bundle", action="store_true", help="Zip each failing context into one archive")
    out.add_argument("--no-redact", dest="no_redact", action="store_true", help="Do NOT mask secrets in logs/HARs (only for trusted local debugging)")

    runtime = parser.add_argument_group("runtime behaviour")
    runtime.add_argument("--retries", dest="retries", type=int, help="Retry attempts per target on transient errors")
    runtime.add_argument("--retry-backoff", dest="retry_backoff", type=float, help="Initial retry backoff in seconds")
    runtime.add_argument("--fail-fast", dest="fail_fast", action="store_true", help="Stop the whole run on the first failing target")
    runtime.add_argument("--no-fail-fast", dest="no_fail_fast", action="store_true", help="Never abort early (default)")
    runtime.add_argument("--print-config", dest="print_config", action="store_true", help="Print the effective configuration and exit")
    runtime.add_argument("--dry-run-only", dest="dry_run_only", action="store_true", help="Alias for --dry-run")
    runtime.add_argument("-v", "--verbose", dest="verbose", action="count", default=0, help="-v: debug logs, -vv: + headers/bodies")
    runtime.add_argument("--debug", dest="debug", action="store_true", help="Alias for -vv")
    runtime.add_argument("--quiet", dest="quiet", action="store_true", help="Only warnings and errors on the console")
    runtime.add_argument("--no-color", dest="no_color", action="store_true", help="Disable ANSI colours in logs")
    return parser


LOG_LEVEL_HELP = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def config_from_args(args: argparse.Namespace) -> Config:
    """Convenience wrapper used by ``__main__`` and tests."""
    cfg = Config.from_sources(args)
    if getattr(args, "no_submit", False):
        cfg.submit_default = False
    if getattr(args, "urls", None):
        # Synthesised data source: build a temporary JSON file so the loader stays uniform.
        urls = split_csv(args.urls)
        if not urls:
            raise ConfigError("--urls was given but contained no usable URL")
        data = [{"target_url": url} for url in urls]
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="waft-urls-")) / "urls.json"
        tmp.write_text(json_dumps(data, indent=2), encoding="utf-8")
        cfg.data_file = tmp
    return cfg
