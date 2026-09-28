"""Logging setup: rich console output (when available) + rotating file logs.

A single call to :func:`setup_logging` configures the root logger; every module then uses
:func:`get_logger`. :class:`ContextAdapter` prefixes messages with the context id so that
10+ interleaved virtual users stay readable in one terminal.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any, Optional, Union

from .utils import import_optional

__all__ = [
    "LOG_LEVELS",
    "setup_logging",
    "get_logger",
    "ContextAdapter",
    "context_logger",
    "NetworkStreamFormatter",
    "DEFAULT_FORMAT",
    "DEFAULT_DATEFMT",
]

DEFAULT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-24s | %(message)s"
DEFAULT_DATEFMT = "%H:%M:%S"
CONTEXT_FORMAT = "%(asctime)s | %(levelname)-7s | %(context_id)-12s | %(name)-22s | %(message)s"

LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET")

_configured = False


class ContextAdapter(logging.LoggerAdapter):
    """LoggerAdapter injecting ``context_id``/``row`` fields into every record."""

    def process(self, msg: Any, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("context_id", self.extra.get("context_id", "-"))
        for key, value in self.extra.items():
            extra.setdefault(key, value)
        prefix_parts = [str(extra.get("context_id", "-"))]
        if extra.get("row") is not None:
            prefix_parts.append(f"row{extra['row']}")
        if extra.get("iteration") is not None:
            prefix_parts.append(f"it{extra['iteration']}")
        prefix = "[" + " ".join(prefix_parts) + "]"
        return f"{prefix} {msg}", kwargs


def context_logger(logger: Union[logging.Logger, "ContextAdapter"], context_id: str, **extra: Any) -> ContextAdapter:
    """Return a :class:`ContextAdapter` bound to *context_id* and extra fields."""
    base = logger.logger if isinstance(logger, ContextAdapter) else logger
    payload = {"context_id": context_id, **extra}
    return ContextAdapter(base, payload)


class NetworkStreamFormatter(logging.Formatter):
    """Compact single-line formatter used for the request/response traffic stream."""

    COLORS = {
        "DEBUG": "\033[90m",
        "INFO": "\033[36m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[41m",
    }
    RESET = "\033[0m"

    def __init__(self, *, color: bool = True, show_context: bool = True) -> None:
        super().__init__(DEFAULT_FORMAT, DEFAULT_DATEFMT)
        self.color = color and sys.stderr.isatty()
        self.show_context = show_context

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib signature
        record.context_id = getattr(record, "context_id", "-")
        rendered = super().format(record)
        if self.color:
            color = self.COLORS.get(record.levelname, "")
            return f"{color}{rendered}{self.RESET}"
        return rendered


def _has_rich() -> bool:
    return import_optional("rich", "logging") is not None


def setup_logging(
    level: Union[str, int] = "INFO",
    *,
    log_file: Optional[Union[str, Path]] = None,
    use_rich: bool = True,
    force_color: Optional[bool] = None,
    quiet_console: bool = False,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    force_reconfigure: bool = False,
) -> logging.Logger:
    """Configure the root logger for console + optional rotating file output.

    Parameters
    ----------
    level:
        Log level name or numeric level.
    log_file:
        When given, a :class:`~logging.handlers.RotatingFileHandler` is attached writing
        full-detail lines (no colour, timestamps with date).
    use_rich:
        Prefer ``rich.logging.RichHandler`` when the package is importable.
    quiet_console:
        Only attach the file handler (useful when the CLI renders its own progress UI).
    force_reconfigure:
        Re-apply handlers even if already configured (used by tests).
    """
    global _configured
    root = logging.getLogger()

    if _configured and not force_reconfigure:
        root.setLevel(level if isinstance(level, int) else logging.getLevelName(str(level).upper()))
        return root

    if isinstance(level, str):
        numeric_level = logging.getLevelName(level.upper())
        if not isinstance(numeric_level, int):
            numeric_level = logging.INFO
    else:
        numeric_level = int(level)

    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    root.setLevel(numeric_level)

    if not quiet_console:
        rich_logging = import_optional("rich.logging", "RichHandler") if use_rich else None
        if rich_logging is not None:
            console = None
            rich_console_mod = import_optional("rich.console", "Console")
            if rich_console_mod is not None and force_color is not None:
                console = rich_console_mod(force_terminal=force_color)
            handler = rich_logging(
                level=numeric_level,
                console=console,
                rich_tracebacks=True,
                tracebacks_suppress=[],
                markup=False,
                show_path=False,
                log_time_format=DEFAULT_DATEFMT,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
        else:
            handler = logging.StreamHandler(stream=sys.stderr)
            handler.setFormatter(NetworkStreamFormatter(color=force_color if force_color is not None else True))
        handler.setLevel(numeric_level)
        root.addHandler(handler)

    if log_file:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8", delay=True
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(logging.Formatter(DEFAULT_FORMAT, "%Y-%m-%d %H:%M:%S"))
        root.addHandler(file_handler)

    # Third-party noise control.
    for noisy, noisy_level in {
        "asyncio": logging.WARNING,
        "urllib3": logging.WARNING,
        "python_multipart": logging.WARNING,
        "charset_normalizer": logging.WARNING,
        "PIL": logging.WARNING,
        "matplotlib": logging.WARNING,
    }.items():
        logging.getLogger(noisy).setLevel(noisy_level)

    _configured = True
    root.debug("Logging configured (pid=%s, level=%s)", os.getpid(), numeric_level)
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger (thin wrapper to keep import sites tidy)."""
    return logging.getLogger(name)
