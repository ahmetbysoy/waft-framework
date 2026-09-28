#!/usr/bin/env python3
"""``run_regression.py`` - load-test / regression driver for the sign-up + e-mail verification flow.

What it does
------------
1. **Scope guard** - reads ``authorized_hosts.txt`` (plus any ``--allow-host``) and refuses to
   run against anything that is not explicitly yours: staging, pre-prod, a local mock or a
   partner sandbox you are allowed to use. Well-known third-party offerwall / micro-task
   platforms are always rejected - testing them with automated sign-ups is abuse, and their
   own sandbox/API plus written permission is the only correct route.
2. **Pre-flight** - checks the data file, proxy list, Playwright browsers, free disk space and
   IMAP configuration *before* a single context is opened, so a 10-context run never dies
   half-way because of a typo.
3. **Selector resolution** (optional, on by default when the browser is available) - runs
   :mod:`selector_resolver` so the run uses verified selectors instead of dead ones.
4. **Execution** - either drives ``waft.Orchestrator`` in-process (default) or shells out to
   the WAFT CLI (``--mode cli``), with ``--contexts``/``--concurrency`` contexts in parallel,
   proxies from ``proxies.txt``, IMAP verification enabled and ``--captcha-action skip`` so a
   bot-challenge only fails *that target* (logged as skipped/blocked) instead of killing the run.
5. **Manifest** - writes ``run_manifest.json`` next to the artifacts: parameters, scope,
   input file hashes, resolved selectors and the equivalent CLI command (reproducibility).

Exit codes mirror the framework: ``0`` all targets passed, ``1`` failures, ``2`` usage/scope
error, ``3`` proxy requirement unmet, ``130`` interrupted.

Examples
--------
Local (demo app + Mailpit)::

    python qa-kit/run_regression.py --base-url http://127.0.0.1:8080 --contexts 10 --concurrency 10

Staging (own environment, proxies + IMAP)::

    python qa-kit/run_regression.py --contexts 10 --concurrency 10 \\
        --proxies proxies.txt --imap --mode cli
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Optional, Sequence
from urllib.parse import urlsplit

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
KIT_DIR: Final[Path] = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:  # allow "python qa-kit/run_regression.py" without installing
    sys.path.insert(0, str(REPO_ROOT))

#: Hosts this kit will always refuse, even with ``--i-am-authorized``.
#: These are commercial offerwall / micro-task / reward platforms: automated account creation
#: there is fraud (not load testing). Integrate through the platform's official API or a
#: sandbox they grant you in writing, then add *that* host to ``authorized_hosts.txt``.
BLOCKED_THIRD_PARTY_SUFFIXES: Final[tuple[str, ...]] = (
    "timewall.io",
    "jumptask.io",
    "freecash.com",
    "swagbucks.com",
    "offertoro.com",
    "adgate.online",
    "ayetstudios.com",
    "bitlabs.ai",
    "cpagrip.com",
    "lootably.com",
)

MIN_FREE_DISK_MB: Final[int] = 2048
SCOPE_HELP: Final[str] = (
    "Add the host to qa-kit/authorized_hosts.txt (one host or *.suffix per line) - or pass "
    "--allow-host <host> together with --i-am-authorized to assert you own / are allowed to test it."
)


# --------------------------------------------------------------------------------------
# small typed helpers
# --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Scope:
    """Host allow-list loaded from a scope file plus CLI extras."""

    patterns: tuple[str, ...]

    def allows(self, host: str) -> bool:
        host = (host or "").strip().lower()
        if not host:
            return False
        if host in self.patterns:
            return True
        for pattern in self.patterns:
            if pattern.startswith("*.") and (host == pattern[2:] or host.endswith(pattern[1:])):
                return True
            if pattern == "*":
                return True
        return False

    @classmethod
    def load(cls, path: Optional[Path], extra: Sequence[str]) -> "Scope":
        patterns: list[str] = [item.strip().lower() for item in extra if item.strip()]
        if path and Path(path).exists():
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip().lower()
                if line:
                    patterns.append(line)
        return cls(patterns=tuple(dict.fromkeys(patterns)))


@dataclass(slots=True)
class PreflightReport:
    """Everything the driver learned before touching the browser."""

    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hosts: dict[str, str] = field(default_factory=dict)
    unauthorized: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    rows: int = 0

    def error(self, message: str) -> None:
        self.errors.append(message)
        self.ok = False

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def file_sha256(path: Path, *, chunk: int = 1 << 20) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).netloc or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def sha_or_none(path: Optional[Path]) -> Optional[str]:
    return file_sha256(Path(path)) if path else None


# --------------------------------------------------------------------------------------
# pre-flight
# --------------------------------------------------------------------------------------
def preflight(config_args: argparse.Namespace, scope: Scope) -> PreflightReport:
    """Validate data, scope, proxies, browsers, disk and IMAP before launching anything."""
    report = PreflightReport()

    # --- data source -------------------------------------------------------------------
    data_path = Path(config_args.data)
    if not data_path.exists():
        report.error(f"data file not found: {data_path}")
        return report
    try:
        from waft.config import Config
        from waft.data_source import DataLoader

        loader_config = Config(data_file=data_path, url_column=config_args.url_column)
        loader_config.validate()
        rows = DataLoader(loader_config).load()
    except Exception as exc:  # noqa: BLE001
        report.error(f"data file could not be parsed: {type(exc).__name__}: {exc}")
        return report
    report.rows = len(rows)
    if not rows:
        report.error(f"{data_path} contains no usable rows")
        return report

    for row in rows:
        url = (row.target_url or "").strip()
        host = host_of(url)
        if not host:
            report.warn(f"row {row.index}: target_url is empty or unparsable ({url!r})")
            continue
        report.hosts.setdefault(host, url)

    # --- scope guard -------------------------------------------------------------------
    host_only_map = {host: host.split(":")[0] for host in report.hosts}
    report.blocked = sorted(
        host for host, bare in host_only_map.items()
        if any(bare == suffix or bare.endswith("." + suffix) for suffix in BLOCKED_THIRD_PARTY_SUFFIXES)
    )
    report.unauthorized = sorted(
        host for host in report.hosts
        if host not in report.blocked and not scope.allows(host) and not scope.allows(host_only_map[host])
    )
    if report.blocked:
        report.error(
            "third-party offerwall / micro-task platform(s) are out of scope for this kit: "
            + ", ".join(report.blocked)
            + " — automated sign-ups there are abuse, not testing. Use the platform's official "
              "sandbox/API with written permission instead."
        )
    if report.unauthorized and not config_args.i_am_authorized:
        report.error("host(s) not covered by the scope file: " + ", ".join(report.unauthorized) + " — " + SCOPE_HELP)
    elif report.unauthorized:
        report.warn(
            "running against host(s) outside qa-kit/authorized_hosts.txt because --i-am-authorized "
            "was given: " + ", ".join(report.unauthorized)
        )

    # --- proxies -----------------------------------------------------------------------
    proxies_path = Path(config_args.proxies) if config_args.proxies else None
    if proxies_path and not proxies_path.exists() and config_args.proxy_mode != "off":
        report.warn(f"proxy file {proxies_path} not found - continuing without proxies (proxy-mode=auto)")
    if proxies_path and proxies_path.exists():
        usable = [
            line for line in proxies_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not usable:
            report.warn(f"{proxies_path} contains no active proxy lines (all commented/blank)")

    # --- browsers ----------------------------------------------------------------------
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:  # noqa: BLE001
        report.error("playwright is not importable: pip install -r requirements.txt")
    else:
        try:
            from waft.config import Config

            for warning in Config().check_environment():
                report.warn(warning)
        except Exception:  # noqa: BLE001 - environment check is best-effort
            pass

    # --- disk --------------------------------------------------------------------------
    try:
        free_mb = shutil.disk_usage(REPO_ROOT).free // (1024 * 1024)
        if free_mb < MIN_FREE_DISK_MB:
            report.warn(f"only {free_mb} MB free disk space - artifacts can be large (screenshots/HAR/traces)")
    except Exception:  # noqa: BLE001
        pass

    # --- IMAP --------------------------------------------------------------------------
    if config_args.imap:
        missing = [
            name for name, value in (
                ("--imap-host (IMAP_HOST)", config_args.imap_host or os.environ.get("IMAP_HOST")),
                ("--imap-user (IMAP_USER)", config_args.imap_user or os.environ.get("IMAP_USER")),
                ("--imap-password (IMAP_PASSWORD)", config_args.imap_password or os.environ.get("IMAP_PASSWORD")),
            )
            if not value
        ]
        if missing:
            report.warn(
                "IMAP verification requested but settings are incomplete ("
                + ", ".join(missing)
                + "); rows with requires_email_verification will fail their verification step"
            )

    # --- contexts vs. machine ----------------------------------------------------------
    if config_args.contexts > 25:
        report.warn(f"{config_args.contexts} contexts on one machine is aggressive - keep --concurrency bounded")
    if config_args.concurrency > config_args.contexts:
        report.warn(f"--concurrency ({config_args.concurrency}) > --contexts ({config_args.contexts}); extra slots idle")
    return report


# --------------------------------------------------------------------------------------
# selector resolution
# --------------------------------------------------------------------------------------
def resolve_selectors(args: argparse.Namespace) -> tuple[Optional[Path], list[str]]:
    """Run the resolver (best effort); return the resolved file path and warnings."""
    warnings: list[str] = []
    catalogue = Path(args.selectors_catalogue)
    if not catalogue.exists():
        return None, [f"selector catalogue {catalogue} not found - falling back to WAFT heuristics"]
    try:
        from selector_resolver import main as resolver_main  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - running from repo root
        sys.path.insert(0, str(KIT_DIR))
        try:
            from selector_resolver import main as resolver_main  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            return None, [f"selector_resolver could not be imported: {exc}"]

    out = Path(args.selectors_out)
    code = resolver_main(
        [
            "--data", str(args.data),
            "--catalogue", str(catalogue),
            "--out", str(out),
            "--report", str(Path(args.selectors_report)),
            "--browser", args.browser,
        ]
    )
    if code != 0:
        warnings.append(f"selector resolver exited with code {code} - falling back to heuristics where needed")
    return (out if out.exists() else None), warnings


# --------------------------------------------------------------------------------------
# config + execution
# --------------------------------------------------------------------------------------
def build_config(args: argparse.Namespace, resolved_selectors: Optional[Path]) -> Any:
    """Translate CLI arguments into a validated :class:`waft.config.Config`."""
    # Reuse the framework's own CLI parsing so nothing drifts between the kit and `python -m waft`.
    from waft.config import build_arg_parser, config_from_args

    argv: list[str] = ["--data", str(args.data), "--contexts", str(args.contexts), "--concurrency", str(args.concurrency)]
    if resolved_selectors is not None:
        argv += ["--selectors", str(resolved_selectors)]
    if args.proxies:
        argv += ["--proxy-file", str(args.proxies), "--proxy-mode", args.proxy_mode]
    else:
        argv += ["--proxy-mode", "off"]
    if args.imap:
        argv += ["--imap"]
        for flag, value in (
            ("--imap-host", args.imap_host), ("--imap-user", args.imap_user),
            ("--imap-password", args.imap_password), ("--imap-mailbox", args.imap_mailbox),
            ("--imap-subject-regex", args.imap_subject_regex), ("--imap-sender", args.imap_sender),
        ):
            if value:
                argv += [flag, str(value)]
        if args.imap_port:
            argv += ["--imap-port", str(args.imap_port)]
        if args.imap_timeout:
            argv += ["--imap-timeout", str(args.imap_timeout)]
    if args.stealth:
        argv += ["--stealth"]
    if args.verify_stealth:
        argv += ["--verify-stealth"]
    if args.capture_har:
        argv += ["--capture-har"]
    if args.log_network:
        argv += ["--log-network"]
    argv += ["--captcha-action", args.captcha_action]
    argv += ["--artifacts", str(args.artifacts)]
    argv += ["--log-level", args.log_level]
    if args.devices:
        argv += ["--devices", args.devices]
    if args.locale:
        argv += ["--locale", args.locale]
    if args.timezone:
        argv += ["--timezone", args.timezone]
    if args.retries is not None:
        argv += ["--retries", str(args.retries)]
    if args.rate_limit is not None:
        argv += ["--rate-limit", str(args.rate_limit)]
    if args.trace:
        argv += ["--trace", args.trace]
    if args.no_humanize:
        argv += ["--no-humanize"]
    if args.quiet:
        argv += ["--quiet"]
    if args.no_color:
        argv += ["--no-color"]
    argv += ["--fail-fast"] if args.fail_fast else ["--no-fail-fast"]

    parsed = build_arg_parser().parse_args(argv)
    config = config_from_args(parsed)

    # TLS switches have no CLI flag in the core (they are .env/JSON driven), so set them here:
    # Mailpit/MailHog style test servers speak plain IMAP -> --imap-no-ssl, STARTTLS -> --imap-starttls.
    if args.imap and args.imap_no_ssl and not args.imap_starttls:
        config.imap_ssl = False
        config.imap_starttls = False
    if args.imap and args.imap_starttls:
        config.imap_ssl = False
        config.imap_starttls = True

    config.validate()
    return config


def equivalent_cli_command(args: argparse.Namespace, resolved_selectors: Optional[Path]) -> str:
    """Human-readable ``python -m waft …`` equivalent of this invocation (for runbooks/CI)."""
    parts: list[str] = ["python -m waft", "--data", str(args.data)]
    parts += ["--contexts", str(args.contexts), "--concurrency", str(args.concurrency)]
    if args.proxies and args.proxy_mode != "off":
        parts += ["--proxy-file", str(args.proxies), "--proxy-mode", args.proxy_mode]
    else:
        parts += ["--proxy-mode", "off"]
    if resolved_selectors:
        parts += ["--selectors", str(resolved_selectors)]
    if args.stealth:
        parts += ["--stealth"]
    if args.verify_stealth:
        parts += ["--verify-stealth"]
    if args.capture_har:
        parts += ["--capture-har"]
    if args.log_network:
        parts += ["--log-network"]
    parts += ["--captcha-action", args.captcha_action]
    if args.imap:
        parts += ["--imap"]
        if args.imap_subject_regex:
            parts += ["--imap-subject-regex", f"'{args.imap_subject_regex}'"]
    parts += ["--artifacts", str(args.artifacts), "--log-level", args.log_level]
    if args.trace:
        parts += ["--trace", args.trace]
    if args.rate_limit is not None:
        parts += ["--rate-limit", str(args.rate_limit)]
    if args.no_color:
        parts += ["--no-color"]
    return " ".join(parts)


def write_manifest(
    args: argparse.Namespace,
    *,
    artifacts_root: Path,
    resolved_selectors: Optional[Path],
    scope: Scope,
    report: PreflightReport,
    run_id: Optional[str],
    exit_code: int,
    started: float,
) -> Path:
    """Write ``run_manifest.json`` - the reproducibility note for this execution."""
    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kit": "qa-kit/run_regression.py",
        "mode": args.mode,
        "run_id": run_id,
        "exit_code": exit_code,
        "duration_s": round(time.perf_counter() - started, 2),
        "parameters": {
            "data": str(args.data),
            "contexts": args.contexts,
            "concurrency": args.concurrency,
            "proxies": str(args.proxies) if args.proxies else None,
            "proxy_mode": args.proxy_mode if args.proxies else "off",
            "imap": args.imap,
            "captcha_action": args.captcha_action,
            "stealth": args.stealth,
            "verify_stealth": args.verify_stealth,
            "capture_har": args.capture_har,
            "log_network": args.log_network,
            "browser": args.browser,
            "trace": args.trace,
        },
        "scope": {
            "file": str(args.scope),
            "patterns": list(scope.patterns),
            "hosts_tested": sorted(report.hosts),
            "unauthorized_override": report.unauthorized if args.i_am_authorized else [],
            "blocked_third_party": report.blocked,
        },
        "inputs": {
            "data_sha256": sha_or_none(Path(args.data)),
            "proxy_sha256": sha_or_none(Path(args.proxies)) if args.proxies else None,
            "selectors_catalogue_sha256": sha_or_none(Path(args.selectors_catalogue)),
            "selectors_resolved": str(resolved_selectors) if resolved_selectors else None,
        },
        "rows": report.rows,
        "warnings": report.warnings,
        "equivalent_cli": equivalent_cli_command(args, resolved_selectors),
    }
    path = Path(artifacts_root) / "run_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def run_via_orchestrator(config: Any) -> tuple[int, Optional[str], Path]:
    """Drive ``waft.Orchestrator`` in-process; returns (exit_code, run_id, artifacts_root)."""
    from waft.orchestrator import Orchestrator

    orchestrator = Orchestrator(config)
    exit_code = asyncio.run(orchestrator.run())
    run_id: Optional[str] = getattr(orchestrator, "run_id", None)
    artifacts = getattr(orchestrator, "artifacts", None)
    root = Path(getattr(artifacts, "root", config.artifacts_dir))
    return int(exit_code), run_id, root


def run_via_cli(args: argparse.Namespace, resolved_selectors: Optional[Path]) -> tuple[int, Optional[str], Path]:
    """Shell out to the WAFT CLI (``python -m waft``) - the subprocess variant."""
    command = equivalent_cli_command(args, resolved_selectors)
    print(f"→ {command}")
    completed = subprocess.run(command, shell=True, cwd=str(REPO_ROOT), check=False)  # noqa: S602 - fixed, self-built command
    # Fingerprint the newest run directory so the manifest still points at the right artifacts.
    root = Path(args.artifacts)
    run_id: Optional[str] = None
    if root.exists():
        candidates = sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime)
        if candidates and (candidates[-1] / "run.json").exists():
            run_id = candidates[-1].name
    return int(completed.returncode), run_id, root


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_regression.py",
        description="WAFT regression/load driver for the sign-up + e-mail verification flow (owned/authorised targets only).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    data = parser.add_argument_group("data & scope")
    data.add_argument("--data", type=Path, default=KIT_DIR / "targets.xlsx", help="Excel/JSON data source.")
    data.add_argument("--url-column", default=None, help="Override the URL column name.")
    data.add_argument("--scope", type=Path, default=KIT_DIR / "authorized_hosts.txt", help="Scope file (allow-list).")
    data.add_argument("--allow-host", action="append", default=[], help="Extra allowed host (repeatable).")
    data.add_argument("--i-am-authorized", action="store_true",
                      help="Assert written authorisation for hosts missing from the scope file (use with --allow-host).")
    data.add_argument("--base-url", default=None,
                      help="Rewrite every target URL to this host/base (quick switch staging <-> localhost).")

    shape = parser.add_argument_group("run shape")
    shape.add_argument("--contexts", type=int, default=10, help="Isolated browser contexts.")
    shape.add_argument("--concurrency", type=int, default=10, help="Contexts running in parallel.")
    shape.add_argument("--rate-limit", type=float, default=None, help="Global navigations/second cap (protects the target).")
    shape.add_argument("--retries", type=int, default=1, help="Retry attempts per target for retryable errors.")
    shape.add_argument("--fail-fast", action="store_true", help="Stop the whole run on the first failure.")

    browser = parser.add_argument_group("browser & stealth")
    browser.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"], help="Engine.")
    browser.add_argument("--stealth", dest="stealth", action="store_true", default=True, help="Enable the stealth layer.")
    browser.add_argument("--no-stealth", dest="stealth", action="store_false", help="Disable the stealth layer.")
    browser.add_argument("--verify-stealth", action="store_true", help="Assert stealth invariants after each navigation.")
    browser.add_argument("--devices", default="random", help="Device profile spec (see waft.config.DEVICE_PROFILES).")
    browser.add_argument("--locale", default="tr-TR,en-US", help="Locales cycled over contexts.")
    browser.add_argument("--timezone", default="Europe/Istanbul", help="Timezone id or country code.")
    browser.add_argument("--no-humanize", action="store_true", help="Disable human-like typing (much faster).")

    proxy = parser.add_argument_group("proxy rotation")
    proxy.add_argument("--proxies", type=Path, default=REPO_ROOT / "proxies.txt", help="Proxy list file.")
    proxy.add_argument("--proxy-mode", default="auto", choices=["auto", "require", "off"], help="Proxy policy.")

    traffic = parser.add_argument_group("network capture")
    traffic.add_argument("--log-network", dest="log_network", action="store_true", default=True,
                         help="Log every request/response to terminal + JSONL.")
    traffic.add_argument("--no-network-log", dest="log_network", action="store_false", help="Disable traffic logging.")
    traffic.add_argument("--capture-har", dest="capture_har", action="store_true", default=True,
                         help="Write a redacted HAR per context.")
    traffic.add_argument("--no-capture-har", dest="capture_har", action="store_false", help="Disable HAR capture.")

    selectors = parser.add_argument_group("selectors")
    selectors.add_argument("--selectors-catalogue", type=Path, default=KIT_DIR / "selectors.json",
                           help="Fallback selector catalogue.")
    selectors.add_argument("--selectors-out", type=Path, default=KIT_DIR / "selectors.resolved.json",
                           help="Resolved WAFT selector map (produced automatically).")
    selectors.add_argument("--selectors-report", type=Path, default=KIT_DIR / "selectors.resolved.report.json",
                           help="Probe report.")
    selectors.add_argument("--probe-selectors", dest="probe_selectors", action="store_true", default=True,
                           help="Probe the catalogue in a real browser before the run.")
    selectors.add_argument("--no-probe-selectors", dest="probe_selectors", action="store_false",
                           help="Reuse the existing selectors.resolved.json without probing.")

    imap = parser.add_argument_group("IMAP verification")
    imap.add_argument("--imap", dest="imap", action="store_true", default=False, help="Enable the IMAP watcher.")
    imap.add_argument("--imap-host", default=os.environ.get("IMAP_HOST"), help="IMAP host (or IMAP_HOST env).")
    imap.add_argument("--imap-port", type=int, default=None, help="IMAP port (Mailpit: 1430, Gmail: 993).")
    imap.add_argument("--imap-no-ssl", action="store_true", help="Plain IMAP without TLS (Mailpit/MailHog on 1430).")
    imap.add_argument("--imap-starttls", action="store_true", help="Use STARTTLS on 143 instead of implicit SSL.")
    imap.add_argument("--imap-user", default=os.environ.get("IMAP_USER"), help="IMAP user (or IMAP_USER env).")
    imap.add_argument("--imap-password", default=os.environ.get("IMAP_PASSWORD"), help="IMAP password (or IMAP_PASSWORD env).")
    imap.add_argument("--imap-mailbox", default="INBOX", help="Mailbox to poll.")
    imap.add_argument("--imap-timeout", type=float, default=None, help="Seconds to wait for each verification mail.")
    imap.add_argument("--imap-subject-regex", default=r"(doğrula|dogrula|verify|aktivasyon|activat)",
                      help="Subject filter for verification mails.")
    imap.add_argument("--imap-sender", default=None, help="Only accept mail from this sender (substring).")

    runtime = parser.add_argument_group("runtime & output")
    runtime.add_argument("--mode", choices=["orchestrator", "cli"], default="orchestrator",
                         help="Run in-process (Orchestrator) or shell out to `python -m waft`.")
    runtime.add_argument("--artifacts", type=Path, default=REPO_ROOT / "artifacts", help="Artifacts root.")
    runtime.add_argument("--captcha-action", choices=["skip", "error", "continue"], default="skip",
                         help="CAPTCHA policy: 'skip' marks only that target as blocked.")
    runtime.add_argument("--trace", choices=["on", "off", "on-failure", "retain-on-failure"], default="on-failure",
                         help="Playwright trace mode (zipped).")
    runtime.add_argument("--log-level", default="INFO", help="Console log level.")
    runtime.add_argument("--dry-run", action="store_true", help="Pre-flight + plan only; no browser run.")
    runtime.add_argument("--print-command", action="store_true", help="Print the equivalent WAFT CLI command and exit.")
    runtime.add_argument("--quiet", action="store_true", help="Less console output.")
    runtime.add_argument("--no-color", action="store_true", help="Disable ANSI colours.")
    return parser.parse_args(argv)


def rewrite_base_url(args: argparse.Namespace) -> Optional[Path]:
    """Point every row at *args.base_url* by rewriting 'target_url' in a temp copy of the sheet."""
    if not args.base_url:
        return None
    import pandas as pd

    base = args.base_url.rstrip("/")
    frame = pd.read_excel(args.data, sheet_name=None)
    rewritten: dict[str, Any] = {}
    for sheet_name, sheet in frame.items():
        if "target_url" in sheet.columns:
            sheet = sheet.copy()
            # Keep the path part of each existing URL, swap scheme+host.
            sheet["target_url"] = [
                base + (urlsplit(str(value)).path or "/") if str(value).startswith("http") else f"{base}/{value}"
                for value in sheet["target_url"]
            ]
        rewritten[sheet_name] = sheet
    tmp = Path(args.artifacts) / f"targets.base_url.{int(time.time())}.xlsx"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
        for sheet_name, sheet in rewritten.items():
            sheet.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    print(f"→ target URLs rewritten to {base} ({tmp})")
    return tmp


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()

    rewritten = rewrite_base_url(args)
    if rewritten is not None:
        args.data = rewritten

    scope = Scope.load(args.scope, args.allow_host)
    report = preflight(args, scope)

    for warning in report.warnings:
        print(f"⚠ {warning}")
    if not report.ok:
        for error in report.errors:
            print(f"✖ {error}", file=sys.stderr)
        return 2

    if args.print_command:
        print(equivalent_cli_command(args, None))
        return 0

    resolved = Path(args.selectors_out)
    warnings: list[str] = []
    if args.probe_selectors:
        resolved_path, warnings = resolve_selectors(args)
        for warning in warnings:
            print(f"⚠ {warning}")
        print(f"→ selectors: {resolved_path if resolved_path else 'heuristics only'}")
    elif not resolved.exists():
        print("⚠ --no-probe-selectors given but no resolved file exists; WAFT will use its heuristics")
        resolved_path = None
    else:
        resolved_path = resolved
        print(f"→ selectors: reusing {resolved_path}")

    print(
        f"→ {report.rows} row(s) | {args.contexts} contexts | concurrency {args.concurrency} | "
        f"hosts: {', '.join(sorted(report.hosts))} | captcha: {args.captcha_action}"
    )
    if args.dry_run:
        config = build_config(args, resolved_path)
        config.dry_run = True
        exit_code, run_id, artifacts_root = run_via_orchestrator(config)
        print("→ dry run finished (no browser was launched for the scenarios)")
    else:
        config = build_config(args, resolved_path)
        if args.mode == "cli":
            exit_code, run_id, artifacts_root = run_via_cli(args, resolved_path)
        else:
            exit_code, run_id, artifacts_root = run_via_orchestrator(config)

    manifest = write_manifest(
        args,
        artifacts_root=artifacts_root,
        resolved_selectors=resolved_path,
        scope=scope,
        report=report,
        run_id=run_id,
        exit_code=exit_code,
        started=started,
    )
    print(f"→ manifest: {manifest}")
    print(f"→ exit code: {exit_code} ({'PASSED' if exit_code == 0 else 'see artifacts for details'})")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
