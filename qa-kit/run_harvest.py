#!/usr/bin/env python3
"""``run_harvest.py`` - load-test / regression harness for the sign-up + verification-mail flow.

What it is
----------
The entry point the runbook calls.  It is a **thin, fully typed driver** on top of
``qa-kit/run_regression.py`` (scope guard, pre-flight, selector probing, artifact manifest) which
in turn drives WAFT - either through ``waft.Orchestrator`` in-process or by spawning the WAFT CLI
(``python -m waft``) as a subprocess.  "Harvest" here means: create accounts on **your own**
application and harvest the verification e-mail/link your app sends, so the whole flow
(form fill -> submit -> mail -> link -> verified page) is covered by one measurable run.

Default load profile (all overridable)
--------------------------------------
* ``--contexts 10``      ten isolated browser contexts (own cookies/localStorage/session)
* ``--concurrency 10``   all ten in parallel
* ``proxies.txt``        proxy list read from the repo root (``--no-proxy`` to switch off)
* ``--imap`` on          verification mail harvested over IMAP
* ``--imap-subject-regex "(doğrula|verify|aktivasyon…)"``  subject filter
* ``--captcha-action skip``  a bot-challenge only marks **that** target, the run continues
* ``--stealth --verify-stealth --capture-har --log-network``  fingerprint hardening + full
  request/response capture for later API analysis

Scope
-----
Third-party offerwall / micro-task / reward platforms (timewall.io, jumptask.io, …) are rejected
outright by the scope guard in ``run_regression.py``: automated sign-ups there are abuse, not
testing.  Point the harness at your own staging/local environment via ``--base-url`` and add the
host to ``qa-kit/authorized_hosts.txt``.

Usage
-----
Local, end-to-end (demo app + devmail, see qa-kit/README.md)::

    python qa-kit/run_harvest.py \
        --data qa-kit/targets.xlsx --base-url http://127.0.0.1:8080 \
        --imap-host 127.0.0.1 --imap-port 1430 --imap-user devmail --imap-password devmail --imap-no-ssl \
        --no-proxy --no-probe-selectors --log-level WARNING

Your staging environment (proxy rotation + real IMAP, CLI subprocess mode)::

    python qa-kit/run_harvest.py --mode cli --base-url https://staging.sirketiniz.com \
        --imap-host imap.gmail.com --imap-user sizin.hesabiniz@gmail.com --imap-password "$IMAP_PASSWORD"

Exit codes (same as WAFT): 0 pass · 1 failures · 2 usage/scope/data · 3 proxy · 130 interrupted.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Final, Optional, Sequence

KIT_DIR: Final[Path] = Path(__file__).resolve().parent
REPO_ROOT: Final[Path] = KIT_DIR.parent
for _path in (str(REPO_ROOT), str(KIT_DIR)):  # runnable without installing the package
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: Defaults that define the "10 parallel users" load profile.
DEFAULT_CONTEXTS: Final[int] = 10
DEFAULT_CONCURRENCY: Final[int] = 10
#: Türkçe + İngilizce doğrulama konu filtresi (app'in gönderdiği mail hangi dilde olursa yakalar).
DEFAULT_SUBJECT_REGEX: Final[str] = r"(doğrula|dogrula|verify|aktivasyon|activat)"


# --------------------------------------------------------------------------------------
# .env support (optional dependency: python-dotenv, with a built-in fallback parser)
# --------------------------------------------------------------------------------------
def load_env_file(path: Optional[Path]) -> None:
    """Populate ``os.environ`` from a .env file without overwriting existing variables.

    ``IMAP_PASSWORD`` and friends usually live in ``.env``: this lets the harness be started
    with a bare command line in CI while keeping secrets out of the shell history.
    """
    candidate = Path(path) if path else REPO_ROOT / ".env"
    if not candidate.exists():
        return
    try:  # pragma: no cover - prefer the real parser when available
        from dotenv import load_dotenv

        load_dotenv(candidate, override=False)
        return
    except Exception:  # noqa: BLE001 - fall back to the built-in parser
        pass
    for raw_line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_first(*names: str) -> Optional[str]:
    """Return the first non-empty environment variable from *names*."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


# --------------------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------------------
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_harvest.py",
        description=(
            "Sign-up + verification-mail load-test harness (owned/authorised targets only). "
            "Drives waft.Orchestrator or the `python -m waft` CLI with 10 parallel contexts."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    data = parser.add_argument_group("data & target")
    data.add_argument("--data", type=Path, default=KIT_DIR / "targets.xlsx",
                      help="Excel/JSON data source with the sign-up rows.")
    data.add_argument("--base-url", default=None,
                      help="Rewrite every row's target_url to this host (staging <-> localhost switch).")
    data.add_argument("--scope", type=Path, default=KIT_DIR / "authorized_hosts.txt",
                      help="Scope file: hosts you are allowed to test.")
    data.add_argument("--allow-host", action="append", default=[],
                      help="Extra allowed host (repeatable). Needs --i-am-authorized.")
    data.add_argument("--i-am-authorized", action="store_true",
                      help="Assert written authorisation for hosts missing from the scope file.")

    load = parser.add_argument_group("load profile")
    load.add_argument("--contexts", type=int, default=DEFAULT_CONTEXTS,
                      help="Isolated browser contexts (= simulated users).")
    load.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                      help="Contexts running in parallel.")
    load.add_argument("--rate-limit", type=float, default=5.0,
                      help="Global navigations/second cap - protects the target from a self-DDoS.")
    load.add_argument("--retries", type=int, default=1, help="Retry attempts per target (retryable errors).")
    load.add_argument("--iterations", type=int, default=None, help="Repeat every row N times (optional).")
    load.add_argument("--fail-fast", action="store_true", help="Abort the run on the first failure.")
    load.add_argument("--no-humanize", action="store_true", help="Disable human-like typing (faster runs).")

    proxy = parser.add_argument_group("proxy rotation")
    proxy.add_argument("--proxies", type=Path, default=REPO_ROOT / "proxies.txt",
                       help="Proxy list file; one proxy per line (user:pass@host:port, socks5://…).")
    proxy.add_argument("--proxy-mode", default="auto", choices=["auto", "require", "off"],
                       help="auto=use when the file has entries, require=fail without, off=never.")
    proxy.add_argument("--no-proxy", action="store_true", help="Shorthand for --proxy-mode off.")

    capture = parser.add_argument_group("traffic capture & stealth")
    capture.add_argument("--stealth", dest="stealth", action="store_true", default=True,
                         help="Enable the stealth layer (webdriver/canvas/WebGL/WebRTC).")
    capture.add_argument("--no-stealth", dest="stealth", action="store_false", help="Disable stealth.")
    capture.add_argument("--verify-stealth", dest="verify_stealth", action="store_true", default=True,
                         help="Assert stealth invariants after every navigation.")
    capture.add_argument("--no-verify-stealth", dest="verify_stealth", action="store_false",
                         help="Skip the verification pass.")
    capture.add_argument("--capture-har", dest="capture_har", action="store_true", default=True,
                         help="Write a redacted HAR per context for API analysis.")
    capture.add_argument("--no-capture-har", dest="capture_har", action="store_false", help="No HAR.")
    capture.add_argument("--log-network", dest="log_network", action="store_true", default=True,
                         help="Log every request/response (terminal + network.jsonl).")
    capture.add_argument("--no-network-log", dest="log_network", action="store_false",
                         help="Disable traffic logs.")
    capture.add_argument("--captcha-action", choices=["skip", "error", "continue"], default="skip",
                         help="'skip' marks only the affected target as blocked_captcha and moves on.")
    capture.add_argument("--trace", choices=["on", "off", "on-failure", "retain-on-failure"],
                         default="on-failure", help="Playwright trace mode (.zip).")
    capture.add_argument("--devices", default="random", help="Device profile spec (waft.config.DEVICE_PROFILES).")
    capture.add_argument("--locale", default="tr-TR,en-US", help="Locales cycled across contexts.")
    capture.add_argument("--timezone", default="Europe/Istanbul", help="Timezone id or country code.")

    imap = parser.add_argument_group("verification mail (IMAP)")
    imap.add_argument("--imap", dest="imap", action="store_true", default=True,
                      help="Harvest the verification mail over IMAP (default on).")
    imap.add_argument("--no-imap", dest="imap", action="store_false", help="Skip the IMAP step.")
    imap.add_argument("--imap-host", default=None, help="IMAP host (falls back to IMAP_HOST).")
    imap.add_argument("--imap-port", type=int, default=None, help="IMAP port (993 SSL / 1430 Mailpit / 143 STARTTLS).")
    imap.add_argument("--imap-user", default=None, help="IMAP user (falls back to IMAP_USER).")
    imap.add_argument("--imap-password", default=None, help="IMAP password (falls back to IMAP_PASSWORD).")
    imap.add_argument("--imap-mailbox", default="INBOX", help="Mailbox to poll.")
    imap.add_argument("--imap-subject-regex", default=DEFAULT_SUBJECT_REGEX,
                      help="Subject filter used to pick the verification mail.")
    imap.add_argument("--imap-sender", default=None, help="Only accept mail from this sender (substring).")
    imap.add_argument("--imap-no-ssl", action="store_true",
                      help="Plain IMAP without TLS (Mailpit/devmail).")
    imap.add_argument("--imap-starttls", action="store_true", help="STARTTLS on port 143 instead of SSL.")
    imap.add_argument("--imap-timeout", type=float, default=None, help="Seconds to wait for each mail.")

    runtime = parser.add_argument_group("runtime & output")
    runtime.add_argument("--mode", choices=["orchestrator", "cli"], default="orchestrator",
                         help="orchestrator=in-process API, cli=spawn `python -m waft` as a subprocess.")
    runtime.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env", help=".env file to load first.")
    runtime.add_argument("--artifacts", type=Path, default=REPO_ROOT / "artifacts", help="Artifacts root.")
    runtime.add_argument("--log-level", default="INFO", help="Console log level.")
    runtime.add_argument("--probe-selectors", dest="probe_selectors", action="store_true", default=True,
                         help="Probe qa-kit/selectors.json in a real browser before the run.")
    runtime.add_argument("--no-probe-selectors", dest="probe_selectors", action="store_false",
                         help="Reuse the existing selectors.resolved.json.")
    runtime.add_argument("--dry-run", action="store_true", help="Pre-flight + plan only.")
    runtime.add_argument("--print-command", action="store_true",
                         help="Print the equivalent `python -m waft …` command and exit.")
    runtime.add_argument("--no-color", action="store_true", help="Disable ANSI colours.")
    return parser.parse_args(argv)


# --------------------------------------------------------------------------------------
# argv translation -> run_regression.py
# --------------------------------------------------------------------------------------
def build_regression_argv(args: argparse.Namespace) -> list[str]:
    """Translate harvest arguments into ``run_regression.py`` flags (single source of truth)."""
    argv: list[str] = [
        "--data", str(args.data),
        "--scope", str(args.scope),
        "--contexts", str(args.contexts),
        "--concurrency", str(args.concurrency),
        "--mode", args.mode,
        "--artifacts", str(args.artifacts),
        "--log-level", args.log_level,
        "--captcha-action", args.captcha_action,
        "--trace", args.trace,
        "--devices", args.devices,
        "--locale", args.locale,
        "--timezone", args.timezone,
        "--retries", str(args.retries),
        "--rate-limit", str(args.rate_limit),
    ]
    if args.base_url:
        argv += ["--base-url", args.base_url]
    for host in args.allow_host:
        argv += ["--allow-host", host]
    if args.i_am_authorized:
        argv += ["--i-am-authorized"]
    if args.iterations:
        argv += ["--iterations", str(args.iterations)]
    if args.fail_fast:
        argv += ["--fail-fast"]
    if args.no_humanize:
        argv += ["--no-humanize"]
    if args.stealth:
        argv += ["--stealth"]
    if args.verify_stealth:
        argv += ["--verify-stealth"]
    if args.capture_har:
        argv += ["--capture-har"]
    if args.log_network:
        argv += ["--log-network"]
    # Proxy policy: --no-proxy wins, otherwise pass the list through.
    if args.no_proxy or args.proxy_mode == "off":
        argv += ["--proxy-mode", "off"]
    else:
        argv += ["--proxies", str(args.proxies), "--proxy-mode", args.proxy_mode]
    # IMAP: explicit flag + settings resolved from CLI, then env (fallback), then .env.
    if args.imap:
        argv += ["--imap"]
        host = args.imap_host or env_first("IMAP_HOST", "WAFT_IMAP_HOST")
        user = args.imap_user or env_first("IMAP_USER", "WAFT_IMAP_USER", "IMAP_USERNAME")
        password = args.imap_password or env_first("IMAP_PASSWORD", "WAFT_IMAP_PASSWORD")
        port = args.imap_port or (int(env_first("IMAP_PORT") or 0) or None)
        if host:
            argv += ["--imap-host", host]
        if port:
            argv += ["--imap-port", str(port)]
        if user:
            argv += ["--imap-user", user]
        if password:
            argv += ["--imap-password", password]
        if args.imap_mailbox:
            argv += ["--imap-mailbox", args.imap_mailbox]
        if args.imap_subject_regex:
            argv += ["--imap-subject-regex", args.imap_subject_regex]
        if args.imap_sender:
            argv += ["--imap-sender", args.imap_sender]
        if args.imap_timeout:
            argv += ["--imap-timeout", str(args.imap_timeout)]
        # TLS switches: local test servers speak plain IMAP; production uses implicit SSL.
        if args.imap_no_ssl or (env_first("IMAP_SSL") or "").lower() in {"false", "0", "no"}:
            argv += ["--imap-no-ssl"]
        elif args.imap_starttls:
            argv += ["--imap-starttls"]
    else:
        argv += ["--no-imap"]
    if not args.probe_selectors:
        argv += ["--no-probe-selectors"]
    if args.dry_run:
        argv += ["--dry-run"]
    if args.print_command:
        argv += ["--print-command"]
    if args.no_color:
        argv += ["--no-color"]
    return argv


def preflight_summary(args: argparse.Namespace) -> list[str]:
    """Return human-readable warnings about the load profile before anything is launched."""
    warnings: list[str] = []
    if args.contexts < 1 or args.concurrency < 1:
        warnings.append("--contexts and --concurrency must be >= 1")
    if args.concurrency > args.contexts:
        warnings.append(f"--concurrency ({args.concurrency}) > --contexts ({args.contexts}): extra slots stay idle")
    if not args.no_proxy and args.proxy_mode != "off" and not Path(args.proxies).exists():
        warnings.append(f"proxy list {args.proxies} not found - the run continues without proxies")
    if args.imap and not (args.imap_host or env_first("IMAP_HOST", "WAFT_IMAP_HOST")):
        warnings.append("IMAP is on but no host given (--imap-host / IMAP_HOST / .env): verification rows will fail")
    if args.rate_limit is None:
        warnings.append("no --rate-limit set: 10 parallel contexts can hammer a small target - 5 nav/s is a sane cap")
    return warnings


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_file(args.env_file)

    warnings = preflight_summary(args)
    for warning in warnings:
        print(f"⚠ {warning}")
    if warnings and any("must be >= 1" in item for item in warnings):
        return 2

    regression_argv = build_regression_argv(args)
    print("→ profile: "
          f"{args.contexts} context(s) / {args.concurrency} parallel | mode={args.mode} | "
          f"proxy={'off' if args.no_proxy else args.proxy_mode} | imap={'on' if args.imap else 'off'} | "
          f"captcha={args.captcha_action}")

    try:
        import run_regression  # local sibling module
    except ImportError as exc:  # pragma: no cover - packaging problem
        print(f"✖ qa-kit/run_regression.py could not be imported: {exc}", file=sys.stderr)
        return 2

    try:
        return int(run_regression.main(regression_argv))
    except KeyboardInterrupt:  # pragma: no cover - graceful ctrl+c
        print("\ninterrupted - partial artifacts were kept", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
