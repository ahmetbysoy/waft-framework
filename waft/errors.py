"""Centralised exception hierarchy for the WAFT framework.

Every error raised by the framework derives from :class:`WaftError`, which makes it
trivial for callers (workers, orchestrator, CLI) to distinguish framework errors from
unexpected Python errors and to decide whether a retry makes sense.
"""

from __future__ import annotations

from typing import Optional

__all__ = [
    "WaftError",
    "ConfigError",
    "DataSourceError",
    "ProxyError",
    "ProxyUnavailable",
    "BrowserError",
    "ContextClosedError",
    "StealthError",
    "NavigationError",
    "TargetUnreachableError",
    "BlockedByEdgeError",
    "FormAutomationError",
    "FieldResolutionError",
    "FieldFillError",
    "SubmitError",
    "OutcomeTimeoutError",
    "CaptchaDetectedError",
    "ImapError",
    "ImapAuthError",
    "ImapTimeoutError",
    "VerificationError",
    "ArtifactError",
    "WorkflowError",
    "retryable",
    "is_retryable",
]


class WaftError(Exception):
    """Base class for every error raised by the framework."""

    #: Subclasses may flip this to ``True`` to mark an error as transient.
    retryable: bool = False

    def __init__(self, message: str, *, details: Optional[dict] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:  # pragma: no cover - trivial
        if not self.details:
            return self.message
        rendered = ", ".join(f"{k}={v!r}" for k, v in self.details.items())
        return f"{self.message} ({rendered})"


class ConfigError(WaftError):
    """Invalid or inconsistent configuration (CLI, .env, data files)."""


class DataSourceError(WaftError):
    """The target/form data source could not be read or is malformed."""


class ProxyError(WaftError):
    """Proxy parsing/health error."""


class ProxyUnavailable(ProxyError):
    """No healthy proxy was available when one was required."""

    retryable = True


class BrowserError(WaftError):
    """Browser engine could not be launched or driven."""


class ContextClosedError(BrowserError):
    """Operation attempted on a browser context that was already closed."""

    retryable = True


class StealthError(WaftError):
    """Stealth layer could not be applied or a consistency check failed."""


class NavigationError(WaftError):
    """Navigating to the target URL failed."""

    retryable = True


class TargetUnreachableError(NavigationError):
    """DNS/connect/timeout level failure while reaching the target."""

    retryable = True


class BlockedByEdgeError(NavigationError):
    """The target answered with a bot-management / WAF challenge page (403/429/503)."""

    retryable = True

    def __init__(self, message: str, *, status: Optional[int] = None, vendor: Optional[str] = None) -> None:
        super().__init__(message, details={"status": status, "vendor": vendor})
        self.status = status
        self.vendor = vendor


class FormAutomationError(WaftError):
    """Base class for form scanning/filling/submission errors."""


class FieldResolutionError(FormAutomationError):
    """A form field could not be located for a given data key."""


class FieldFillError(FormAutomationError):
    """A located field could not be filled/checked/selected."""


class SubmitError(FormAutomationError):
    """The form could not be submitted."""

    retryable = True


class OutcomeTimeoutError(FormAutomationError):
    """Submission did not produce the expected success/error outcome in time."""

    retryable = True


class CaptchaDetectedError(FormAutomationError):
    """A CAPTCHA/Turnstile/hCaptcha widget was detected; automated solving is out of scope."""


class ImapError(WaftError):
    """Generic IMAP problem (connect, select, fetch)."""

    retryable = True


class ImapAuthError(ImapError):
    """IMAP authentication failed - retrying will not help."""

    retryable = False


class ImapTimeoutError(ImapError):
    """No matching message arrived inside the configured window."""

    retryable = False


class VerificationError(WaftError):
    """Email/OTP verification flow failed."""

    retryable = True


class ArtifactError(WaftError):
    """Screenshot/trace/bundle could not be produced."""


class WorkflowError(WaftError):
    """A scenario step definition was invalid or failed."""


def retryable(exc_type: type) -> type:
    """Class decorator that marks an exception type as retryable."""
    exc_type.retryable = True
    return exc_type


def is_retryable(exc: BaseException) -> bool:
    """Return ``True`` when *exc* is a transient framework error."""
    if isinstance(exc, WaftError):
        return bool(exc.retryable)
    # Playwright timeouts / navigation aborts are inherently transient for our purposes.
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    if isinstance(exc, PlaywrightTimeoutError):
        return True
    if isinstance(exc, PlaywrightError):
        message = str(exc).lower()
        transient_markers = (
            "target closed",
            "browser has been closed",
            "net::err_",
            "connection reset",
            "connection refused",
            "timeout",
            "econnreset",
            "socket hang up",
        )
        return any(marker in message for marker in transient_markers)
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    return False
