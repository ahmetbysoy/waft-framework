"""Command line interface entry point (``python -m waft`` / ``waft`` console script).

Exit codes
----------
===========  ==========================================================
0            every target passed
1            at least one target failed (or nothing was executed)
2            configuration/usage error (bad data file, unknown device …)
3            proxy requirements could not be satisfied (``--proxy-mode require``)
130          interrupted by the user (Ctrl+C / SIGTERM)
===========  ==========================================================
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional, Sequence

from .config import Config, build_arg_parser, config_from_args
from .errors import ConfigError, ProxyError, ProxyUnavailable, WaftError
from .logging_setup import get_logger, setup_logging
from .orchestrator import Orchestrator
from .version import FRAMEWORK_NAME, __version__

__all__ = ["main", "parse_args", "build_config"]

logger = get_logger("waft.cli")

BANNER = r"""
 __      __ _    ______ _____
 \ \    / // \  |  ____|_   _|
  \ \  / // _ \ | |__    | |
   \ \/ // ___ \|  __|   | |
    \  //_/   \_\_|      |_|      Web Automation & Form-Test Framework
"""


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments (thin wrapper so tests can inject argv)."""
    parser = build_arg_parser()
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__} - {FRAMEWORK_NAME}")
    args = parser.parse_args(argv)
    if getattr(args, "dry_run_only", False):
        args.dry_run = True
    return args


def build_config(argv: Optional[Sequence[str]] = None) -> tuple[Config, argparse.Namespace]:
    """Build a validated :class:`~waft.config.Config` from the command line."""
    args = parse_args(argv)
    config = config_from_args(args)
    return config, args


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Program entry point: parse → validate → run → exit code."""
    try:
        config, args = build_config(argv)
    except ConfigError as exc:
        print(f"[WAFT] configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - unexpected but must not traceback for users
        print(f"[WAFT] could not start: {exc}", file=sys.stderr)
        return 2

    setup_logging(
        config.log_level,
        log_file=config.log_file,
        force_color=False if config.no_color else None,
        quiet_console=config.quiet,
        force_reconfigure=True,
    )
    if not config.quiet and sys.stderr.isatty():
        print(BANNER, file=sys.stderr)

    if args.print_config:
        import json

        print(json.dumps(config.to_dict(redact=True), indent=2, sort_keys=True, default=str))
        return 0

    orchestrator = Orchestrator(config)
    try:
        return asyncio.run(orchestrator.run())
    except ProxyUnavailable as exc:
        logger.error("Proxy requirement could not be met: %s", exc)
        return 3
    except ProxyError as exc:
        logger.error("Proxy error: %s", exc)
        return 3
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2
    except WaftError as exc:
        logger.error("Run failed: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort guard with a clear message
        logger.exception("Unexpected failure: %s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover - module executed directly
    raise SystemExit(main())
