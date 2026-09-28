"""Proxy parsing, rotation and health checking.

Sources, in priority order:

1. ``--proxy-file`` CLI argument (``proxies.txt``),
2. ``WAFT_PROXY_FILE`` / ``PROXY_FILE`` environment variables,
3. ``WAFT_PROXIES`` / ``PROXIES`` / ``PROXY_LIST`` inline lists (``;`` separated),
4. ``proxy`` column inside the data source (per-row override),
5. ``WAFT_PROXY_SERVER`` / ``PROXY_SERVER`` single-proxy variables.

Supported formats inside files/lists::

    host:port
    host:port:user:pass
    user:pass@host:port
    http://user:pass@host:port
    socks5://user:pass@host:1080
    socks5h://host:1080            # remote DNS resolution
    1.2.3.4:8080 user pass         # whitespace separated
    # comments and blank lines are ignored

Rotation policies::

    least-used (default)  -> the least used, healthy, non-cooling proxy
    round-robin           -> strict cyclic order (``--proxy-cycle``)
    sticky                -> one proxy per context for the whole run (default behaviour)

Health checking runs concurrently (``--no-proxy-health-check`` disables it) and can be done
either through a real Playwright request (default, catches auth failures) or a plain TCP
connect probe (``mode="tcp"``, ~1000x cheaper, used automatically as a fallback).
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union
from urllib.parse import unquote, urlsplit

from .config import Config, env_str
from .errors import ProxyError, ProxyUnavailable
from .logging_setup import get_logger
from .models import ProxySpec
from .utils import Stopwatch, mask_secret, parse_bool, read_text, split_csv, truncate

__all__ = ["ProxyPool", "parse_proxy_line", "parse_proxy_lines", "normalize_proxy_server", "ProxyHealth"]

logger = get_logger("waft.proxy")

PROXY_FILE_ENV_KEYS = ("WAFT_PROXY_FILE", "PROXY_FILE", "PROXY_LIST_FILE")
PROXY_LIST_ENV_KEYS = ("WAFT_PROXIES", "PROXIES", "PROXY_LIST", "PROXY_SERVERS")
PROXY_SINGLE_ENV_KEYS = ("WAFT_PROXY_SERVER", "PROXY_SERVER", "HTTPS_PROXY", "HTTP_PROXY")
SUPPORTED_SCHEMES = ("http", "https", "socks5", "socks5h", "socks4", "socks4a")
_SCHEME_RE = re.compile(r"^(?P<scheme>[a-z0-9]+)://(?P<rest>.+)$", re.I)
_HOSTPORT_RE = re.compile(r"^(?P<host>\[[0-9a-fA-F:]+\]|[^:\[\]\s]+):(?P<port>\d{1,5})$")


@dataclass
class ProxyHealth:
    """Outcome of one proxy probe."""

    proxy: ProxySpec
    ok: bool
    latency_ms: Optional[float] = None
    status: Optional[int] = None
    ip: Optional[str] = None
    country: Optional[str] = None
    error: Optional[str] = None
    method: str = "browser"

    def to_dict(self) -> dict[str, Any]:
        return {
            "proxy": self.proxy.masked(),
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "status": self.status,
            "exit_ip": self.ip,
            "country": self.country,
            "error": self.error,
            "method": self.method,
        }


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------


def normalize_proxy_server(scheme: str, host: str, port: Union[str, int]) -> str:
    """Return a canonical ``scheme://host:port`` string."""
    scheme = (scheme or "http").lower()
    if scheme not in SUPPORTED_SCHEMES:
        scheme = "http"
    host = host.strip()
    if ":" in host and not host.startswith("["):  # raw IPv6
        host = f"[{host}]"
    return f"{scheme}://{host}:{int(port)}"


def parse_proxy_line(
    line: str,
    *,
    default_scheme: str = "http",
    source: str = "file",
    label_prefix: str = "",
    counter: int = 0,
) -> Optional[ProxySpec]:
    """Parse a single proxy line into a :class:`ProxySpec` (``None`` when unusable)."""
    text = (line or "").strip()
    if not text or text.startswith("#") or text.startswith("//"):
        return None
    # Strip inline comments and stray bullets from copy/pasted lists.
    text = re.split(r"\s+#", text, maxsplit=1)[0].strip().strip("-•*").strip()
    if not text:
        return None

    scheme = default_scheme
    rest = text
    match = _SCHEME_RE.match(text)
    if match:
        scheme = match.group("scheme").lower()
        rest = match.group("rest")

    username = password = None
    bypass = None
    if "@" in rest:
        credentials, _, rest = rest.rpartition("@")
        if ":" in credentials:
            username, _, password = credentials.partition(":")
        else:
            username = credentials or None
        username = unquote(username).strip() if username else None
        password = unquote(password).strip() if password else None

    # Semicolon separated variant: scheme://host:port;user;pass
    if ";" in rest:
        parts = [p.strip() for p in rest.split(";") if p.strip()]
        rest = parts[0]
        if len(parts) >= 2 and not username:
            username = parts[1]
        if len(parts) >= 3 and not password:
            password = parts[2]

    host = port = None
    hp_match = _HOSTPORT_RE.match(rest.strip())
    if hp_match:
        host, port = hp_match.group("host"), hp_match.group("port")
    else:
        # "host:port user pass" or "host port user pass" style
        tokens = rest.replace(":", " ").split()
        tokens = [t for t in tokens if t]
        if len(tokens) >= 2 and tokens[1].isdigit():
            host, port = tokens[0], tokens[1]
            if len(tokens) >= 3 and not username:
                username = tokens[2]
            if len(tokens) >= 4 and not password:
                password = tokens[3]
        elif rest.strip() and _looks_like_hostname(rest.strip()):
            host = rest.strip()
            port = "443" if scheme in {"https"} else ("1080" if scheme.startswith("socks") else "80")

    if not host or not port:
        logger.debug("Skipping unparsable proxy line: %s", truncate(text, 80))
        return None

    try:
        port_int = int(port)
    except ValueError:
        logger.debug("Skipping proxy with invalid port: %s", truncate(text, 80))
        return None
    if not (0 < port_int < 65536):
        logger.debug("Skipping proxy with out-of-range port: %s", truncate(text, 80))
        return None

    server = normalize_proxy_server(scheme, host, port_int)
    label = f"{label_prefix}{counter}" if label_prefix else f"proxy-{counter}"
    return ProxySpec(
        server=server,
        username=username,
        password=password,
        scheme=scheme,
        source=source,
        label=label,
        bypass=bypass,
    )


def _looks_like_hostname(value: str) -> bool:
    """Reject free text (``garbage-line``) while accepting IPs, FQDNs and localhost."""
    text = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", text):
        return False
    return "." in text or text.lower() == "localhost"


def parse_proxy_lines(lines: Iterable[str], *, default_scheme: str = "http", source: str = "file") -> list[ProxySpec]:
    """Parse many lines, deduplicating by ``scheme://user@host:port``."""
    seen: set[str] = set()
    parsed: list[ProxySpec] = []
    for raw in lines:
        spec = parse_proxy_line(raw, default_scheme=default_scheme, source=source, label_prefix="")
        if spec is None:
            continue
        key = f"{spec.scheme}://{spec.username or ''}@{spec.host}:{spec.port}"
        if key in seen:
            continue
        seen.add(key)
        spec.label = f"{spec.source}-{len(parsed) + 1:02d}"
        parsed.append(spec)
    return parsed


def _spec_from_mapping(payload: dict[str, Any], source: str = "env") -> Optional[ProxySpec]:
    """Build a :class:`ProxySpec` from a JSON-style mapping."""
    server = payload.get("server") or payload.get("url") or payload.get("proxy")
    username = payload.get("username") or payload.get("user")
    password = payload.get("password") or payload.get("pass")
    scheme = payload.get("scheme") or "http"
    host = payload.get("host")
    port = payload.get("port")
    if not server and host and port:
        server = normalize_proxy_server(str(scheme), str(host), int(port))
    if not server:
        return None
    text = str(server)
    if not _SCHEME_RE.match(text):
        text = f"{scheme}://{text}" if "://" not in text else text
    if not username and "@" in text:
        spec = parse_proxy_line(text, source=source)
        return spec
    parsed = urlsplit(text)
    return ProxySpec(
        server=normalize_proxy_server(parsed.scheme or scheme, parsed.hostname or "", parsed.port or 8080),
        username=str(username) if username else (unquote(parsed.username) if parsed.username else None),
        password=str(password) if password else (unquote(parsed.password) if parsed.password else None),
        scheme=(parsed.scheme or scheme).lower(),
        source=source,
    )


# --------------------------------------------------------------------------------------
# Pool
# --------------------------------------------------------------------------------------


class ProxyPool:
    """Thread-safe-ish (event-loop confined) pool with rotation + health bookkeeping."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.proxies: list[ProxySpec] = []
        self.source: Optional[str] = None
        self._cursor = 0
        self._lock = asyncio.Lock()
        self._assignments: dict[str, ProxySpec] = {}
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ loading
    def load(self) -> list[ProxySpec]:
        """Read proxy definitions from the CLI file / env vars and return the pool."""
        specs: list[ProxySpec] = []
        default_scheme = env_str("PROXY_SCHEME", "http") or "http"

        path = self.config.proxy_file
        if path is None:
            env_path = next((env_str(key) for key in PROXY_FILE_ENV_KEYS if env_str(key)), None)
            if env_path:
                path = Path(env_path).expanduser()
        if path is not None:
            path = Path(path).expanduser()
            if not path.exists():
                raise ProxyError(f"Proxy file not found: {path}")
            lines = read_text(path, default="") or ""
            specs = parse_proxy_lines(lines.splitlines(), default_scheme=default_scheme, source=path.name)
            self.source = str(path)
            logger.info("Loaded %d proxy definition(s) from %s", len(specs), path.name)

        if not specs:
            inline = next((env_str(key) for key in PROXY_LIST_ENV_KEYS if env_str(key)), None)
            if inline:
                tokens = [token for token in re.split(r"[;\n]+", inline) if token.strip()]
                specs = parse_proxy_lines(tokens, default_scheme=default_scheme, source="env")
                self.source = "env:WAFT_PROXIES"
                logger.info("Loaded %d proxy definition(s) from environment", len(specs))

        if not specs:
            single = next((env_str(key) for key in PROXY_SINGLE_ENV_KEYS if env_str(key)), None)
            if single:
                spec = parse_proxy_line(single, default_scheme=default_scheme, source="env")
                if spec:
                    specs = [spec]
                    self.source = "env:PROXY_SERVER"

        # Validate the whole list, collecting friendly warnings.
        valid: list[ProxySpec] = []
        for spec in specs:
            if spec.scheme not in SUPPORTED_SCHEMES and "socks" not in spec.scheme:
                self.warnings.append(f"Proxy scheme '{spec.scheme}' is unusual; falling back to http")
                spec.scheme = "http"
                spec.server = normalize_proxy_server("http", spec.host, spec.port)
            valid.append(spec)
        self.proxies = valid

        if not self.proxies and self.config.proxy_mode == "require":
            raise ProxyUnavailable(
                "Proxy mode is 'require' but no proxies were found. Provide --proxy-file or WAFT_PROXIES."
            )
        if not self.proxies:
            logger.info("Running without proxies (proxy-mode=%s)", self.config.proxy_mode)
        return self.proxies

    # ------------------------------------------------------------------ queries
    def __len__(self) -> int:
        return len(self.proxies)

    @property
    def enabled(self) -> bool:
        return bool(self.proxies) and self.config.proxy_mode != "off"

    def healthy(self) -> list[ProxySpec]:
        return [p for p in self.proxies if p.healthy and not p.in_cooldown]

    def stats(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "total": len(self.proxies),
            "healthy": len([p for p in self.proxies if p.healthy]),
            "cooling_down": len([p for p in self.proxies if p.in_cooldown]),
            "used": len([p for p in self.proxies if p.uses]),
            "failures": sum(p.failures for p in self.proxies),
            "mode": self.config.proxy_mode,
            "warnings": list(self.warnings),
        }

    def rows(self) -> list[dict[str, Any]]:
        return [spec.to_dict() for spec in self.proxies]

    # ------------------------------------------------------------------ selection
    async def acquire(
        self,
        *,
        context_id: Optional[str] = None,
        preferred: Optional[Union[str, ProxySpec]] = None,
        sticky: bool = True,
    ) -> Optional[ProxySpec]:
        """Pick a proxy for a context.

        ``preferred`` (a per-row/context value) wins; otherwise the healthiest, least-used
        proxy is chosen. When ``sticky`` is true and *context_id* was already assigned, the
        same proxy is returned again.
        """
        if not self.enabled:
            return None

        async with self._lock:
            if preferred is not None:
                spec = self.resolve(preferred)
                if spec is not None:
                    spec.note_use()
                    return spec
                self.warnings.append(f"Requested proxy '{preferred}' is not in the pool; using rotation")

            if sticky and context_id and context_id in self._assignments:
                spec = self._assignments[context_id]
                if spec.healthy and not spec.in_cooldown:
                    spec.note_use()
                    return spec

            candidates = self.healthy()
            if not candidates:
                if self.config.proxy_fail_closed:
                    raise ProxyUnavailable("All proxies are unhealthy or cooling down (fail-closed mode)")
                logger.warning("No healthy proxy available - running without a proxy for this context")
                return None

            spec = self._choose(candidates)
            spec.note_use()
            if sticky and context_id:
                self._assignments[context_id] = spec
            return spec

    def _choose(self, candidates: Sequence[ProxySpec]) -> ProxySpec:
        if self.config.proxy_cycle:
            spec = candidates[self._cursor % len(candidates)]
            self._cursor += 1
            return spec
        # least-used first, randomised among equally-used entries to spread load
        min_uses = min(p.uses for p in candidates)
        pool = [p for p in candidates if p.uses == min_uses]
        return random.choice(pool) if len(pool) > 1 else pool[0]

    def resolve(self, value: Union[str, ProxySpec, None]) -> Optional[ProxySpec]:
        """Find a spec by its masked/plain string form (used for per-row overrides)."""
        if value is None:
            return None
        if isinstance(value, ProxySpec):
            return value
        text = str(value).strip()
        if not text:
            return None
        # exact masked match
        for spec in self.proxies:
            if text in {spec.masked(), spec.server, f"{spec.host}:{spec.port}"}:
                return spec
        parsed = parse_proxy_line(text, source="inline")
        if parsed is None:
            return None
        for spec in self.proxies:
            if spec.key == parsed.key and (spec.username or "") == (parsed.username or ""):
                return spec
        parsed.label = "inline-override"
        self.proxies.append(parsed)
        logger.info("Added inline proxy override: %s", parsed.masked())
        return parsed

    # ------------------------------------------------------------------ feedback
    def report_success(self, spec: Optional[ProxySpec], *, latency_ms: Optional[float] = None) -> None:
        if spec is None:
            return
        spec.note_success(latency_ms)

    def report_failure(self, spec: Optional[ProxySpec], error: Optional[str] = None) -> None:
        if spec is None:
            return
        spec.note_failure(
            error,
            cooldown_seconds=self.config.proxy_cooldown_s,
            max_failures=self.config.proxy_max_failures,
        )
        logger.warning(
            "Proxy %s failed (%d/%d): %s - cooldown %.0fs",
            spec.masked(),
            spec.failures,
            self.config.proxy_max_failures,
            truncate(error or "unknown", 120),
            self.config.proxy_cooldown_s,
        )

    def release(self, context_id: str, preserve_assignment: bool = False) -> None:
        """Drop (or keep) a context→proxy sticky assignment."""
        if not preserve_assignment:
            self._assignments.pop(context_id, None)

    # ------------------------------------------------------------------ health
    async def health_check_all(
        self,
        *,
        playwright: Any = None,
        mode: str = "auto",
        concurrency: int = 8,
        url: Optional[str] = None,
    ) -> list[ProxyHealth]:
        """Probe every proxy concurrently; returns the list of results.

        ``mode="auto"`` uses Playwright when a driver instance is available (validates
        credentials, TLS and the CONNECT tunnel) and falls back to TCP connect probes.
        """
        if not self.proxies:
            return []
        if not self.config.proxy_health_check:
            logger.info("Proxy health check disabled (--no-proxy-health-check)")
            return []

        url = url or self.config.proxy_health_url
        use_browser = playwright is not None and mode in {"auto", "browser"}
        probe_method = "browser" if use_browser else "tcp"
        logger.info(
            "Checking %d proxy(ies) with %s probe (%s)…",
            len(self.proxies),
            probe_method,
            url,
        )

        semaphore = asyncio.Semaphore(max(1, concurrency))
        timeout_s = max(1.0, self.config.proxy_health_timeout_ms / 1000.0)

        async def _probe(spec: ProxySpec) -> ProxyHealth:
            async with semaphore:
                if use_browser:
                    try:
                        return await self._probe_browser(playwright, spec, url, timeout_s)
                    except Exception as exc:  # noqa: BLE001 - fall back to TCP
                        logger.debug("Browser probe failed for %s (%s); trying TCP", spec.masked(), exc)
                return await self._probe_tcp(spec, timeout_s)

        results = await asyncio.gather(*(_probe(spec) for spec in list(self.proxies)), return_exceptions=True)

        healths: list[ProxyHealth] = []
        for spec, result in zip(list(self.proxies), results):
            if isinstance(result, BaseException):
                health = ProxyHealth(proxy=spec, ok=False, error=f"{type(result).__name__}: {result}")
            else:
                health = result
            if health.ok:
                spec.note_success(health.latency_ms)
            else:
                spec.note_failure(
                    health.error,
                    cooldown_seconds=self.config.proxy_cooldown_s,
                    max_failures=self.config.proxy_max_failures,
                )
            healths.append(health)

        ok_count = sum(1 for h in healths if h.ok)
        logger.info(
            "Proxy health: %d/%d reachable%s",
            ok_count,
            len(healths),
            "" if ok_count == len(healths) else f" (unreachable: {', '.join(h.proxy.masked() for h in healths if not h.ok)})",
        )
        if ok_count == 0 and self.enabled:
            message = "No proxy passed the health check"
            if self.config.proxy_fail_closed or self.config.proxy_mode == "require":
                raise ProxyUnavailable(message + " (fail-closed)")
            self.warnings.append(message)
        return healths

    async def _probe_browser(self, playwright: Any, spec: ProxySpec, url: str, timeout_s: float) -> ProxyHealth:
        """Probe a proxy through a throwaway Chromium context (validates auth + tunnel)."""
        browser_type = playwright.chromium
        browser = None
        context = None
        stopwatch = Stopwatch()
        try:
            launch_args = _chromium_security_args(self.config)
            browser = await browser_type.launch(headless=True, args=launch_args, timeout=int(timeout_s * 1000))
            context = await browser.new_context(
                proxy=spec.to_playwright(),
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                ignore_https_errors=self.config.ignore_https_errors,
            )
            page = await context.new_page()
            response = await page.goto(url, timeout=int(timeout_s * 1000), wait_until="domcontentloaded")
            stopwatch.stop()
            status = response.status if response else None
            body = ""
            if response is not None:
                try:
                    body = (await response.text())[:500]
                except Exception:  # noqa: BLE001 - body may be unavailable
                    body = ""
            ip = self._extract_ip(body)
            country = self._extract_country(body)
            ok = status is not None and 200 <= status < 400
            return ProxyHealth(
                proxy=spec,
                ok=ok,
                latency_ms=round(stopwatch.elapsed_ms, 1),
                status=status,
                ip=ip,
                country=country,
                error=None if ok else f"unexpected status {status}",
                method="browser",
            )
        except Exception as exc:  # noqa: BLE001 - reported through the dataclass
            return ProxyHealth(
                proxy=spec,
                ok=False,
                latency_ms=round(stopwatch.elapsed_ms, 1) or None,
                error=f"{type(exc).__name__}: {truncate(str(exc), 200)}",
                method="browser",
            )
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:  # noqa: BLE001
                    pass
            if browser is not None:
                try:
                    await browser.close()
                except Exception:  # noqa: BLE001
                    pass

    async def _probe_tcp(self, spec: ProxySpec, timeout_s: float) -> ProxyHealth:
        """Cheap TCP connect probe (cannot validate credentials, only reachability)."""
        stopwatch = Stopwatch()
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host=spec.host, port=spec.port), timeout=timeout_s
            )
            stopwatch.stop()
            del reader
            return ProxyHealth(
                proxy=spec,
                ok=True,
                latency_ms=round(stopwatch.elapsed_ms, 1),
                error=None if spec.username else "reachable (credentials not verified)",
                method="tcp",
            )
        except (OSError, asyncio.TimeoutError, socket.gaierror) as exc:
            stopwatch.stop()
            return ProxyHealth(
                proxy=spec,
                ok=False,
                latency_ms=round(stopwatch.elapsed_ms, 1) or None,
                error=f"tcp connect failed: {type(exc).__name__}: {truncate(str(exc), 160)}",
                method="tcp",
            )
        finally:
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------ geo helpers
    @staticmethod
    def _extract_ip(body: str) -> Optional[str]:
        body = (body or "").strip()
        if not body:
            return None
        try:
            payload = json.loads(body)
            if isinstance(payload, dict):
                for key in ("ip", "query", "origin", "client_ip"):
                    if payload.get(key):
                        return str(payload[key]).split(",")[0].strip()
        except json.JSONDecodeError:
            pass
        match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", body)
        return match.group(0) if match else None

    @staticmethod
    def _extract_country(body: str) -> Optional[str]:
        body = (body or "").strip()
        if not body:
            return None
        try:
            payload = json.loads(body)
            if isinstance(payload, dict):
                for key in ("country", "country_code", "countryCode", "cc"):
                    if payload.get(key):
                        return str(payload[key]).upper()
        except json.JSONDecodeError:
            pass
        return None

    # ------------------------------------------------------------------ misc
    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"ProxyPool(size={len(self.proxies)}, source={self.source!r}, mode={self.config.proxy_mode!r})"


def _chromium_security_args(config: Config) -> list[str]:
    """Container-friendly Chromium flags shared by probes and the real run."""
    args = ["--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]
    if not config.allow_chromium_sandbox:
        args += ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
    args += list(config.browser_args or [])
    return args


def summarize_health(healths: Sequence[ProxyHealth]) -> list[dict[str, Any]]:
    """Serialise health results for the run summary (secrets are already masked)."""
    return [health.to_dict() for health in healths]


def env_proxy_list() -> list[str]:
    """Return raw proxy strings from any supported env variable (for diagnostics)."""
    for key in PROXY_LIST_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            return [token.strip() for token in re.split(r"[;\n]+", value) if token.strip()]
    return split_csv(None)


def proxy_spec_from_env(prefix: str = "WAFT_PROXY") -> Optional[ProxySpec]:
    """Build a :class:`ProxySpec` from ``<PREFIX>_HOST/PORT/USER/PASS`` variables."""
    host = env_str(f"{prefix}_HOST")
    port = env_str(f"{prefix}_PORT")
    if not host or not port:
        return None
    return ProxySpec(
        server=normalize_proxy_server(env_str(f"{prefix}_SCHEME", "http") or "http", host, port),
        username=env_str(f"{prefix}_USER"),
        password=env_str(f"{prefix}_PASS"),
        scheme=(env_str(f"{prefix}_SCHEME", "http") or "http").lower(),
        source="env",
        label="env",
    )


def parse_proxy_payload(payload: Any, *, source: str = "inline") -> list[ProxySpec]:
    """Parse JSON/CSV/plain payloads into proxy specs (used by tests and extra sources)."""
    specs: list[ProxySpec] = []
    if isinstance(payload, str):
        return parse_proxy_lines(payload.splitlines() or [payload], source=source)
    if isinstance(payload, dict):
        payload = payload.get("proxies") or payload.get("list") or [payload]
    if isinstance(payload, Iterable):
        for entry in payload:
            if isinstance(entry, dict):
                spec = _spec_from_mapping(entry, source)
                if spec:
                    specs.append(spec)
            elif isinstance(entry, str):
                spec = parse_proxy_line(entry, source=source)
                if spec:
                    specs.append(spec)
    return specs


def proxy_secret_preview(spec: Optional[ProxySpec]) -> str:
    """Short, safe rendering used in logs (never includes the password)."""
    if spec is None:
        return "direct"
    return spec.masked()


def coerce_bool_env(key: str, default: bool = False) -> bool:
    """Small helper used by the CLI when mixing env + flags."""
    return parse_bool(os.environ.get(key), default=default)


def is_masked(value: str) -> bool:
    """Detect an already-masked secret (prevents double masking in reports)."""
    return "***" in str(value)


__all__ += [
    "summarize_health",
    "env_proxy_list",
    "proxy_spec_from_env",
    "parse_proxy_payload",
    "proxy_secret_preview",
    "coerce_bool_env",
    "is_masked",
    "mask_secret",
]
