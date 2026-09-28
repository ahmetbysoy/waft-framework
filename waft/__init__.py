"""WAFT — Web Automation & Form-Test Framework.

A production-oriented Playwright framework for running **N isolated browser contexts in
parallel**, each with its own proxy, user-agent, timezone, locale and stealth fingerprint,
filling forms from Excel/JSON data, watching network traffic, and completing e-mail (IMAP)
verification flows.

Quick start::

    pip install -r requirements.txt
    python -m playwright install --with-deps chromium

    # 10 isolated contexts, proxies from proxies.txt, data from targets.xlsx
    python -m waft --data data/targets.xlsx --proxy-file proxies.txt --contexts 10

Programmatic use::

    import asyncio
    from waft import Config, Orchestrator

    config = Config(data_file="data/targets.xlsx", contexts=5, headless=True)
    config.validate()
    asyncio.run(Orchestrator(config).run())
"""

from __future__ import annotations

from .artifacts import ArtifactManager
from .config import Config, DEVICE_PROFILES, build_arg_parser, config_from_args
from .data_source import DataLoader, InlineRows
from .engine import ContextEngine, ContextEngineFactory, EngineHooks, profile_from_config
from .errors import (
    BlockedByEdgeError,
    BrowserError,
    CaptchaDetectedError,
    ConfigError,
    DataSourceError,
    FormAutomationError,
    ImapError,
    ImapTimeoutError,
    NavigationError,
    ProxyError,
    ProxyUnavailable,
    StealthError,
    VerificationError,
    WaftError,
    WorkflowError,
    is_retryable,
)
from .forms import FormFiller
from .imap_client import ImapClient, ImapSettings
from .logging_setup import context_logger, get_logger, setup_logging
from .models import (
    ContextProfile,
    ContextResult,
    FillReport,
    ProxySpec,
    RunSummary,
    RunTotals,
    TargetRow,
    TargetRunResult,
    VerificationResult,
)
from .network_monitor import NetworkMonitor
from .orchestrator import Orchestrator, run, run_from_config
from .proxy_manager import ProxyPool, parse_proxy_line, parse_proxy_lines
from .reporting import Reporter
from .stealth import StealthLayer
from .utils import Redactor, RateLimiter, interpolate, retry_async
from .version import FRAMEWORK_NAME, __version__

__all__ = [
    # version
    "__version__",
    "FRAMEWORK_NAME",
    # configuration & bootstrap
    "Config",
    "DEVICE_PROFILES",
    "build_arg_parser",
    "config_from_args",
    "setup_logging",
    "get_logger",
    "context_logger",
    # data
    "DataLoader",
    "InlineRows",
    # orchestration
    "Orchestrator",
    "run",
    "run_from_config",
    "EngineHooks",
    "ArtifactManager",
    "Reporter",
    # browser
    "ContextEngine",
    "ContextEngineFactory",
    "profile_from_config",
    "StealthLayer",
    "FormFiller",
    "NetworkMonitor",
    "ProxyPool",
    "parse_proxy_line",
    "parse_proxy_lines",
    "ImapClient",
    "ImapSettings",
    # models
    "TargetRow",
    "TargetRunResult",
    "ContextProfile",
    "ContextResult",
    "FillReport",
    "VerificationResult",
    "RunSummary",
    "RunTotals",
    "ProxySpec",
    # utils
    "Redactor",
    "RateLimiter",
    "interpolate",
    "retry_async",
    "is_retryable",
    # errors
    "WaftError",
    "ConfigError",
    "DataSourceError",
    "ProxyError",
    "ProxyUnavailable",
    "BrowserError",
    "NavigationError",
    "BlockedByEdgeError",
    "StealthError",
    "FormAutomationError",
    "CaptchaDetectedError",
    "ImapError",
    "ImapTimeoutError",
    "VerificationError",
    "WorkflowError",
]
