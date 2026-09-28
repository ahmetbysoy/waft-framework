"""Small, dependency-free helpers shared by every WAFT module.

Contents
--------
* ID/timestamp helpers            -> ``new_run_id``, ``now_iso``, ``ts_slug``
* Placeholder interpolation       -> ``interpolate``, ``interpolate_deep``
* Async primitives               -> ``retry_async``, ``RateLimiter``, ``Stopwatch``
* Secret redaction               -> ``Redactor``, ``mask_secret``
* Filesystem / JSON IO           -> ``ensure_dir``, ``write_json``, ``write_jsonl``, ``read_json``
* Misc parsing                   -> ``parse_bool``, ``parse_duration``, ``parse_size``, ``to_jsonable``
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import inspect
import json
import os
import random
import re
import secrets
import string
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Optional, TypeVar, Union

__all__ = [
    "DEFAULT_TIMEZONE",
    "new_run_id",
    "new_context_id",
    "now_iso",
    "ts_slug",
    "slugify",
    "safe_filename",
    "normalize_ws",
    "truncate",
    "interpolate",
    "interpolate_deep",
    "retry_async",
    "RateLimiter",
    "Stopwatch",
    "Redactor",
    "mask_secret",
    "ensure_dir",
    "write_text",
    "read_text",
    "write_json",
    "write_jsonl",
    "read_json",
    "deep_merge",
    "to_jsonable",
    "json_dumps",
    "parse_bool",
    "parse_duration",
    "parse_size",
    "parse_int",
    "parse_float",
    "split_csv",
    "is_selector_key",
    "looks_like_selector",
    "chunked",
    "unique_preserving_order",
    "random_choice_seeded",
    "import_optional",
    "call_with_supported_kwargs",
    "safe_close",
    "human_bytes",
    "human_ms",
]

DEFAULT_TIMEZONE = "Europe/Istanbul"

T = TypeVar("T")

# --------------------------------------------------------------------------------------
# IDs / timestamps
# --------------------------------------------------------------------------------------


def new_run_id(prefix: str = "run") -> str:
    """Return a sortable, human readable run identifier, e.g. ``run-20260928-141530-a1b2c3``."""
    return f"{prefix}-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def new_context_id(index: int, prefix: str = "ctx") -> str:
    """Return a deterministic-yet-unique context identifier, e.g. ``ctx-03-7f91``."""
    return f"{prefix}-{index:02d}-{secrets.token_hex(2)}"


def now_iso() -> str:
    """Local-time ISO-8601 timestamp with second resolution (safe for filenames/logs)."""
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def ts_slug() -> str:
    """Filesystem-safe timestamp, e.g. ``20260928-141530``."""
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


# --------------------------------------------------------------------------------------
# String helpers
# --------------------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")
_TURKISH_MAP = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "ğ": "g",
        "Ğ": "g",
        "ş": "s",
        "Ş": "s",
        "ç": "c",
        "Ç": "c",
        "ö": "o",
        "Ö": "o",
        "ü": "u",
        "Ü": "u",
    }
)


def slugify(value: str, *, max_length: int = 80, fallback: str = "unnamed") -> str:
    """ASCII-fold, lower-case and dash-separate *value* (Turkish characters included)."""
    if value is None:
        return fallback
    text = str(value).translate(_TURKISH_MAP)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = _SLUG_RE.sub("-", text.lower()).strip("-")
    return text[:max_length] or fallback


def safe_filename(value: str, *, max_length: int = 120, fallback: str = "unnamed") -> str:
    """Turn arbitrary text (URLs, subjects) into a safe file name keeping its extension."""
    if not value:
        return fallback
    text = str(value).translate(_TURKISH_MAP)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._")
    return text[:max_length] or fallback


def normalize_ws(value: str) -> str:
    """Collapse whitespace runs and strip the result."""
    return _WS_RE.sub(" ", str(value or "")).strip()


def truncate(value: str, limit: int = 200, suffix: str = "…") -> str:
    """Truncate *value* to *limit* characters appending *suffix* when shortened."""
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def chunked(sequence: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    """Yield consecutive slices of *sequence* with length *size*."""
    for start in range(0, len(sequence), size):
        yield sequence[start : start + size]


def unique_preserving_order(items: Iterable[T]) -> list[T]:
    """Deduplicate *items* keeping first-seen order (values must be hashable)."""
    seen: set = set()
    out: list[T] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def random_choice_seeded(seed: int, options: Sequence[T]) -> T:
    """Deterministically pick one element of *options* for a given *seed*."""
    if not options:
        raise ValueError("options must not be empty")
    rng = random.Random(seed)
    return options[rng.randrange(len(options))]


def human_bytes(num: Union[int, float, None]) -> str:
    """Render a byte count as ``1.4 MB``."""
    if not num:
        return "0 B"
    step = 1024.0
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= step
    return f"{value:.1f} PB"


def human_ms(value: Union[int, float, None]) -> str:
    """Render a millisecond duration as ``420 ms`` / ``3.2 s``."""
    if value is None:
        return "n/a"
    value = float(value)
    if value < 1000:
        return f"{value:.0f} ms"
    return f"{value / 1000.0:.2f} s"


# --------------------------------------------------------------------------------------
# Placeholder interpolation
# --------------------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]*))?\}")
_ALNUM = string.ascii_lowercase + string.digits


def _render_placeholder(name: str, arg: Optional[str], mapping: Mapping[str, Any]) -> Optional[str]:
    """Resolve a single ``{name:arg}`` placeholder. Returns ``None`` when unknown."""
    name = name.lower()
    if name in mapping and arg is None:
        return str(mapping[name])

    if name in {"run_id", "context_id", "row_index", "iteration", "index", "email", "user"} and name in mapping:
        return str(mapping[name])

    if name in {"uuid", "guid"}:
        length = int(arg) if arg else 32
        return uuid.uuid4().hex[:length]

    if name in {"rand", "random", "token"}:
        length = int(arg) if arg else 8
        return "".join(secrets.choice(_ALNUM) for _ in range(length))

    if name in {"digits", "number", "num"}:
        length = int(arg) if arg else 6
        return "".join(secrets.choice(string.digits) for _ in range(length))

    if name == "letters":
        length = int(arg) if arg else 6
        return "".join(secrets.choice(string.ascii_lowercase) for _ in range(length))

    if name == "epoch":
        return str(int(time.time()))

    if name == "epoch_ms":
        return str(int(time.time() * 1000))

    if name in {"now", "datetime"}:
        fmt = arg or "%Y-%m-%d %H:%M:%S"
        return _dt.datetime.now().strftime(fmt)

    if name == "date":
        fmt = arg or "%Y-%m-%d"
        return _dt.datetime.now().strftime(fmt)

    if name == "time":
        fmt = arg or "%H%M%S"
        return _dt.datetime.now().strftime(fmt)

    if name == "year":
        return str(_dt.datetime.now().year)

    if name == "env":
        if not arg:
            return None
        return os.environ.get(arg, "")

    if name in {"slug", "safe"}:
        if arg is None:
            return None
        return slugify(arg) if name == "slug" else safe_filename(arg)

    return None


def interpolate(template: Any, mapping: Mapping[str, Any], *, keep_unknown: bool = True) -> Any:
    """Replace ``{placeholders}`` inside *template*.

    Supported placeholders (examples)::

        {run_id} {context_id} {row_index} {iteration}
        {uuid} {uuid:8} {rand:12} {digits:6} {letters:8}
        {epoch} {epoch_ms} {now:%H:%M:%S} {date:%Y%m%d} {time:%H%M%S} {year}
        {env:HOME} {slug:Some Text}

    Unknown placeholders are preserved verbatim when *keep_unknown* is true.
    """
    if not isinstance(template, str) or "{" not in template:
        return template

    def _sub(match: re.Match[str]) -> str:
        replacement = _render_placeholder(match.group(1), match.group(2), mapping)
        if replacement is None:
            return match.group(0) if keep_unknown else ""
        return replacement

    return _PLACEHOLDER_RE.sub(_sub, template)


def interpolate_deep(value: Any, mapping: Mapping[str, Any], *, keep_unknown: bool = True) -> Any:
    """Recursively interpolate strings inside dicts/lists/tuples."""
    if isinstance(value, str):
        return interpolate(value, mapping, keep_unknown=keep_unknown)
    if isinstance(value, Mapping):
        return {k: interpolate_deep(v, mapping, keep_unknown=keep_unknown) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_deep(v, mapping, keep_unknown=keep_unknown) for v in value]
    if isinstance(value, tuple):
        return tuple(interpolate_deep(v, mapping, keep_unknown=keep_unknown) for v in value)
    return value


# --------------------------------------------------------------------------------------
# Async helpers
# --------------------------------------------------------------------------------------


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    jitter: float = 0.25,
    retry_on: Union[tuple[type[BaseException], ...], Callable[[BaseException], bool]] = (Exception,),
    on_retry: Optional[Callable[[int, BaseException, float], Any]] = None,
    label: str = "operation",
) -> T:
    """Await ``func()`` retrying transient failures with exponential backoff + jitter.

    ``retry_on`` may be a tuple of exception classes or a predicate receiving the
    exception and returning ``True`` when another attempt should be made.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    def _should_retry(exc: BaseException) -> bool:
        if isinstance(retry_on, tuple):
            return isinstance(exc, retry_on)
        return bool(retry_on(exc))

    last_exc: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return await func()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - re-raised below when not retryable
            last_exc = exc
            if attempt >= attempts or not _should_retry(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, max(0.0, delay * jitter))
            if on_retry is not None:
                result = on_retry(attempt, exc, delay)
                if inspect.isawaitable(result):
                    await result
            await asyncio.sleep(delay)
    assert last_exc is not None  # pragma: no cover - defensive
    raise last_exc


class RateLimiter:
    """Async token-bucket limiter used to keep load-test traffic humane & realistic.

    ``None`` rate disables throttling entirely (``acquire`` becomes a no-op).
    """

    def __init__(self, rate_per_second: Optional[float], burst: Optional[float] = None) -> None:
        self.rate = float(rate_per_second) if rate_per_second else None
        self.burst = float(burst) if burst else (self.rate or 1.0)
        self._tokens = self.burst
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        if self.rate is None:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated
                self._updated = now
                self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                await asyncio.sleep(max(deficit / self.rate, 0.001))

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"RateLimiter(rate={self.rate}, burst={self.burst})"


class Stopwatch:
    """Context-manager stopwatch producing millisecond durations.

    ``elapsed_ms`` is *live* while the stopwatch runs and frozen by ``__exit__``/``stop()``
    - callers may read it inside the measured block (progress logs, timeouts) or afterwards
    (``step.duration_ms``) and always get a sensible number.  Before the first ``__enter__``
    it reports ``0.0``.
    """

    __slots__ = ("_started", "_frozen_ms", "started_at")

    def __init__(self) -> None:
        self._started = 0.0
        self._frozen_ms: Optional[float] = None
        self.started_at = now_iso()

    @property
    def elapsed_ms(self) -> float:
        """Milliseconds since ``__enter__`` (frozen once the block finished)."""
        if self._frozen_ms is not None:
            return self._frozen_ms
        if not self._started:
            return 0.0
        return (time.perf_counter() - self._started) * 1000.0

    @property
    def running(self) -> bool:
        """True while the stopwatch is measuring and has not been frozen yet."""
        return self._frozen_ms is None and bool(self._started)

    def __enter__(self) -> "Stopwatch":
        self._started = time.perf_counter()
        self._frozen_ms = None
        self.started_at = now_iso()
        return self

    def __exit__(self, *_exc: object) -> bool:
        self._frozen_ms = self.elapsed_ms
        return False

    def stop(self) -> float:
        """Freeze and return the elapsed milliseconds (idempotent)."""
        self._frozen_ms = self.elapsed_ms
        return self._frozen_ms


# --------------------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------------------


def mask_secret(value: Optional[str], *, keep: int = 3, mask: str = "***") -> str:
    """Mask credentials while keeping a short, recognisable prefix.

    ``user:password@host`` style blobs are masked in-place so proxies/log lines remain
    debuggable without leaking secrets.
    """
    if not value:
        return ""
    text = str(value)
    # user:pass@host  ->  use***:***@host
    if "@" in text and ":" in text.split("@", 1)[0]:
        creds, host = text.split("@", 1)
        user, _, _password = creds.partition(":")
        return f"{user[:keep]}{mask}:{mask}@{host}"
    if len(text) <= keep:
        return mask
    return f"{text[:keep]}{mask}({len(text)} chars)"


class Redactor:
    """Best-effort scrubber for headers/bodies written into artifacts and logs."""

    SENSITIVE_HEADERS = frozenset(
        {
            "authorization",
            "proxy-authorization",
            "cookie",
            "set-cookie",
            "x-api-key",
            "x-auth-token",
            "x-csrf-token",
            "x-xsrf-token",
            "api-key",
            "apikey",
            "x-amz-security-token",
            "x-goog-api-key",
        }
    )

    SENSITIVE_FIELD_HINTS = (
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "apikey",
        "api_key",
        "access_key",
        "refresh_token",
        "session",
        "otp",
        "cvv",
        "credit",
        "card_number",
        "ssn",
        "tc_kimlik",
    )

    PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}"), r"\1<REDACTED>"),
        (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "<JWT-REDACTED>"),
        (re.compile(r"(?i)(password|passwd|pwd)=([^&\s]+)"), r"\1=<REDACTED>"),
        (re.compile(r"(?i)(\"(?:password|passwd|pwd|token|secret|api_?key)\"\s*:\s*\")[^\"]*(\")"), r"\1<REDACTED>\2"),
        (re.compile(r"\b\d{13,19}\b"), "<CARD-REDACTED>"),
        (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "<EMAIL-REDACTED>"),
        (re.compile(r"\b1[1-9]\d{7}[0-9A-Z]{2}\b"), "<TCKN-REDACTED>"),
        (re.compile(r"\bbb-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<UUID-TOKEN>"),
    )

    def __init__(self, enabled: bool = True, extra_header_keys: Iterable[str] = ()) -> None:
        self.enabled = enabled
        self.extra_header_keys = {k.lower() for k in extra_header_keys}

    def _is_sensitive_header(self, name: str) -> bool:
        lowered = name.lower()
        return lowered in self.SENSITIVE_HEADERS or lowered in self.extra_header_keys

    def header(self, name: str, value: Optional[str]) -> str:
        """Redact a single header value when it is considered sensitive."""
        if not self.enabled or value is None:
            return "<none>"
        if self._is_sensitive_header(name):
            return mask_secret(value)
        return self.text(value)

    def headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        """Return a copy of *headers* with sensitive values masked."""
        if not headers:
            return {}
        return {k: self.header(k, v) for k, v in headers.items()}

    def text(self, value: Optional[str], *, limit: int = 4000) -> str:
        """Scrub free-form text (bodies, urls, error messages)."""
        if value is None:
            return ""
        text = str(value)
        if not self.enabled:
            return truncate(text, limit)
        for pattern, replacement in self.PATTERNS:
            text = pattern.sub(replacement, text)
        return truncate(text, limit)

    def is_sensitive_field(self, key: str) -> bool:
        """Return ``True`` when a form-data key looks like a credential."""
        lowered = str(key).lower()
        return any(hint in lowered for hint in self.SENSITIVE_FIELD_HINTS)


# --------------------------------------------------------------------------------------
# Filesystem / JSON IO
# --------------------------------------------------------------------------------------


def ensure_dir(path: Union[str, Path]) -> Path:
    """Create *path* (and parents) if necessary and return it as a :class:`Path`."""
    resolved = Path(path).expanduser()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def write_text(path: Union[str, Path], content: str, *, encoding: str = "utf-8") -> Path:
    """Write text content, creating parent directories."""
    target = Path(path).expanduser()
    ensure_dir(target.parent)
    target.write_text(content, encoding=encoding)
    return target


def read_text(path: Union[str, Path], *, encoding: str = "utf-8", default: Optional[str] = None) -> Optional[str]:
    """Read text content returning *default* when the file does not exist."""
    target = Path(path).expanduser()
    if not target.exists():
        return default
    return target.read_text(encoding=encoding)


def to_jsonable(value: Any) -> Any:
    """Best-effort conversion of arbitrary objects into JSON-serialisable structures."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {k: to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return to_jsonable(value.to_dict())
        except Exception:  # pragma: no cover - defensive
            pass
    if hasattr(value, "__dict__"):
        try:
            return {k: to_jsonable(v) for k, v in vars(value).items() if not k.startswith("_")}
        except Exception:  # pragma: no cover - defensive
            pass
    return str(value)


def json_dumps(value: Any, *, indent: Optional[int] = None, sort_keys: bool = False) -> str:
    """JSON dump that never raises on exotic objects and keeps Unicode readable."""
    return json.dumps(to_jsonable(value), ensure_ascii=False, indent=indent, sort_keys=sort_keys, default=str)


def write_json(path: Union[str, Path], payload: Any, *, indent: int = 2) -> Path:
    """Write *payload* as pretty UTF-8 JSON."""
    return write_text(path, json_dumps(payload, indent=indent) + "\n")


def write_jsonl(path: Union[str, Path], records: Iterable[Any], *, append: bool = False) -> Path:
    """Write records as JSON Lines (one JSON document per line)."""
    target = Path(path).expanduser()
    ensure_dir(target.parent)
    mode = "a" if append else "w"
    with target.open(mode, encoding="utf-8") as handle:
        for record in records:
            handle.write(json_dumps(record) + "\n")
    return target


def read_json(path: Union[str, Path], *, default: Any = None) -> Any:
    """Read JSON from *path*; returns *default* when missing, raises ``ValueError`` when invalid."""
    target = Path(path).expanduser()
    if not target.exists():
        return default
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - config error path
        raise ValueError(f"Invalid JSON in {target}: {exc}") from exc


def deep_merge(base: MutableMapping[str, Any], override: Mapping[str, Any]) -> MutableMapping[str, Any]:
    """Recursively merge *override* into *base* (override wins, dicts merged, lists replaced)."""
    for key, value in override.items():
        if key in base and isinstance(base[key], MutableMapping) and isinstance(value, Mapping):
            deep_merge(base[key], value)  # type: ignore[arg-type]
        else:
            base[key] = value  # type: ignore[assignment]
    return base


# --------------------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------------------


def parse_bool(value: Any, *, default: bool = False) -> bool:
    """Parse ``true/false/1/0/yes/no/on/off/evet/hayir`` into a bool."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on", "enabled", "evet", "aktif", "var"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off", "disabled", "hayir", "hayır", "pasif", "yok"}:
        return False
    return default


def parse_int(value: Any, *, default: Optional[int] = None) -> Optional[int]:
    """Parse an int tolerating ``1_000``, ``"1000"``, ``"1.0"``, ``""`` and ``None``."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN / inf (spreadsheet gaps)
            return default
        return int(value)
    text = str(value).strip().replace("_", "").replace(" ", "")
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def parse_float(value: Any, *, default: Optional[float] = None) -> Optional[float]:
    """Parse a float tolerating locale-independent numeric strings."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if numeric != numeric or numeric in (float("inf"), float("-inf")):  # NaN / inf
            return default
        return numeric
    text = str(value).strip().replace("_", "").replace(",", ".")
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|m|min|mins|h|hour|hours)?\s*$", re.I)
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(b|kb|kib|mb|mib|gb|gib)?\s*$", re.I)


def parse_duration(value: Any, *, default: float = 0.0, unit: str = "s") -> float:
    """Parse ``"500ms"``, ``"30s"``, ``"5m"``, ``"1h"`` or a plain number into seconds."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        return numeric / 1000.0 if unit == "ms" else numeric
    match = _DURATION_RE.match(str(value))
    if not match:
        return default
    amount = float(match.group(1))
    suffix = (match.group(2) or unit).lower()
    multipliers = {
        "ms": 0.001,
        "s": 1.0,
        "sec": 1.0,
        "secs": 1.0,
        "m": 60.0,
        "min": 60.0,
        "mins": 60.0,
        "h": 3600.0,
        "hour": 3600.0,
        "hours": 3600.0,
    }
    return amount * multipliers.get(suffix, 1.0)


def parse_size(value: Any, *, default: int = 0) -> int:
    """Parse ``"2MB"`` / ``"512KB"`` / ``1048576`` into a byte count."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    match = _SIZE_RE.match(str(value))
    if not match:
        return default
    amount = float(match.group(1))
    suffix = (match.group(2) or "b").lower()
    multipliers = {"b": 1, "kb": 1000, "kib": 1024, "mb": 1_000_000, "mib": 1_048_576, "gb": 1_000_000_000, "gib": 1_073_741_824}
    return int(amount * multipliers.get(suffix, 1))


def split_csv(value: Any) -> list[str]:
    """Split a comma/semicolon separated CLI value into trimmed, non-empty tokens."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for entry in value:
            items.extend(split_csv(entry))
        return items
    text = str(value).replace(";", ",")
    return [token.strip() for token in text.split(",") if token.strip()]


_SELECTOR_PREFIXES = ("#", ".", "[", "//", "text=", "role=", ">>", "xpath=", "css=", "id=", "data-testid=", "name=")
_SELECTOR_HINTS = (" > ", " + ", "=", ":nth", "[data-", "aria-label")


def looks_like_selector(value: str) -> bool:
    """Heuristic: does *value* look like a Playwright/CSS selector rather than a data key?"""
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith(_SELECTOR_PREFIXES):
        return True
    if any(hint in text for hint in _SELECTOR_HINTS):
        return True
    return False


def is_selector_key(key: str) -> bool:
    """Alias kept for readability at call sites dealing with form override maps."""
    return looks_like_selector(key)


# --------------------------------------------------------------------------------------
# Optional imports / defensive calls
# --------------------------------------------------------------------------------------


def import_optional(module_name: str, attribute: Optional[str] = None) -> Any:
    """Import an optional dependency, returning ``None`` when unavailable."""
    try:
        module = __import__(module_name, fromlist=["*"])
    except Exception:  # noqa: BLE001 - optional dependency
        return None
    if attribute:
        return getattr(module, attribute, None)
    return module


def call_with_supported_kwargs(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Call *func* dropping kwargs it does not accept (version-drift safety net)."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):  # pragma: no cover - builtins
        return func(*args, **kwargs)
    accepts_var_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
    if accepts_var_kwargs:
        return func(*args, **kwargs)
    allowed = {name for name, p in signature.parameters.items() if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    return func(*args, **filtered)


async def safe_close(obj: Any, *method_names: str, label: str = "resource") -> Optional[Exception]:
    """Await the first available close-ish coroutine on *obj*, swallowing errors.

    Returns the swallowed exception (if any) so callers may log it at debug level.
    """
    if obj is None:
        return None
    candidates = method_names or ("close", "aclose", "disconnect", "stop")
    for name in candidates:
        method = getattr(obj, name, None)
        if method is None or not callable(method):
            continue
        try:
            result = method()
            if inspect.isawaitable(result):
                await result
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - shutdown must never explode
            return exc
    return None
