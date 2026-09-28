"""Network / console traffic monitor built on ``page.on("request")`` & friends.

Responsibilities
----------------
*   Log every outbound request, response, failed request, console message, page error and
    JS dialog to the terminal (requirement #5) with a stable, greppable format.
*   Persist the full traffic to ``network.jsonl`` / ``console.jsonl`` inside the context
    artifact directory so API discovery survives the terminal scrollback.
*   Aggregate metrics: per-status counters, per-host counters, transfer sizes, slowest
    requests and a deduplicated **API endpoint discovery** list used in the run summary.
*   Redact credentials, tokens, e-mails and card numbers before anything is written.

The monitor is attached per context (and re-attached to every new page/tab) by the
:class:`~waft.engine.ContextEngine`.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .config import Config
from .logging_setup import get_logger
from .models import StepResult
from .utils import Redactor, ensure_dir, human_bytes, human_ms, json_dumps, now_iso, truncate, write_jsonl

__all__ = ["NetworkMonitor", "NetworkEvent", "TrafficStats", "API_CANDIDATE_RE"]

logger = get_logger("waft.network")

#: Heuristics deciding whether an XHR/fetch endpoint is worth surfacing as an API candidate.
API_CANDIDATE_RE = re.compile(
    r"(/api/|/api$|/ajax/|/graphql|/rest/|/v\d+/|/rpc/|/services?/|/gql|\.json(\?|$)|/auth/|/login|/token)",
    re.I,
)

#: Response content types whose bodies are worth reading for API discovery.
BODY_CONTENT_TYPES = ("application/json", "text/json", "application/xml", "text/xml", "application/graphql")

#: Static resource types that are skipped from the body-capture path (they are big).
STATIC_RESOURCE_TYPES = frozenset({"image", "font", "media", "stylesheet", "manifest", "other"})


@dataclass
class NetworkEvent:
    """Normalised representation of one request/response pair (or console entry)."""

    kind: str  # request | response | failure | console | pageerror | dialog | download
    context_id: str
    url: str = ""
    method: str = ""
    status: Optional[int] = None
    resource_type: str = ""
    started_at: str = field(default_factory=now_iso)
    duration_ms: Optional[float] = None
    request_headers: dict[str, str] = field(default_factory=dict)
    response_headers: dict[str, str] = field(default_factory=dict)
    post_data: Optional[str] = None
    response_body: Optional[str] = None
    size_in: Optional[int] = None
    size_out: Optional[int] = None
    failure: Optional[str] = None
    level: Optional[str] = None
    text: Optional[str] = None
    api_candidate: bool = False
    page_url: Optional[str] = None
    frame: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, {}, "", [])}


@dataclass
class TrafficStats:
    """Aggregated counters exposed to the run summary."""

    requests: int = 0
    responses: int = 0
    failures: int = 0
    console_errors: int = 0
    console_warnings: int = 0
    page_errors: int = 0
    dialogs: int = 0
    downloads: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    by_status: Counter = field(default_factory=Counter)
    by_method: Counter = field(default_factory=Counter)
    by_resource_type: Counter = field(default_factory=Counter)
    by_host: Counter = field(default_factory=Counter)
    by_status_class: Counter = field(default_factory=Counter)
    slowest: list[tuple[float, str]] = field(default_factory=list)
    api_candidates: set = field(default_factory=set)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "responses": self.responses,
            "failures": self.failures,
            "console_errors": self.console_errors,
            "console_warnings": self.console_warnings,
            "page_errors": self.page_errors,
            "dialogs": self.dialogs,
            "downloads": self.downloads,
            "bytes_in": self.bytes_in,
            "bytes_in_human": human_bytes(self.bytes_in),
            "bytes_out": self.bytes_out,
            "by_status": dict(self.by_status.most_common(20)),
            "by_method": dict(self.by_method.most_common()),
            "by_resource_type": dict(self.by_resource_type.most_common()),
            "by_status_class": dict(self.by_status_class.most_common()),
            "top_hosts": [{"host": host, "count": count} for host, count in self.by_host.most_common(15)],
            "slowest": [{"url": truncate(url, 160), "duration_ms": round(ms, 1)} for ms, url in self.slowest[:10]],
            "api_endpoints": sorted(self.api_candidates),
            "error_samples": self.errors[:25],
        }


class NetworkMonitor:
    """Attaches Playwright event listeners and turns them into logs + metrics."""

    def __init__(
        self,
        config: Config,
        context_id: str,
        *,
        artifact_dir: Optional[Path] = None,
        page_label: str = "main",
    ) -> None:
        self.config = config
        self.context_id = context_id
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None
        self.page_label = page_label

        self.redactor = Redactor(enabled=config.redact, extra_header_keys=config.sensitive_headers)
        self.stats = TrafficStats()
        self.events: list[NetworkEvent] = []
        self.endpoints: dict[str, dict[str, Any]] = {}
        self._request_started: dict[str, float] = {}
        self._request_meta: dict[str, tuple[str, str, str]] = {}
        self._pending_tasks: set[asyncio.Task] = set()
        self._attached_pages: set[int] = set()
        self._disabled = not config.log_network and not (config.save_network_log or config.log_api_candidates)

        self._network_log_path: Optional[Path] = None
        self._console_log_path: Optional[Path] = None
        if artifact_dir is not None and (config.save_network_log or config.log_console):
            directory = ensure_dir(Path(artifact_dir))
            self._network_log_path = directory / "network.jsonl"
            self._console_log_path = directory / "console.jsonl"

        self.on_api_candidate: Optional[Callable[[dict[str, Any]], Any]] = None

    # ------------------------------------------------------------------ attach
    async def attach(self, context: Any, page: Any) -> None:
        """Wire listeners onto *page* and onto every future page of *context*."""
        await self.attach_page(page)
        try:
            context.on("page", lambda new_page: self._schedule(self.attach_page(new_page)))
        except Exception as exc:  # noqa: BLE001 - older drivers
            logger.debug("[%s] could not subscribe to context 'page' events: %s", self.context_id, exc)

    async def attach_page(self, page: Any) -> None:
        """Attach all handlers to a single page (idempotent)."""
        key = id(page)
        if key in self._attached_pages:
            return
        self._attached_pages.add(key)

        def _closed(*_args: Any, _page=page) -> None:
            self._attached_pages.discard(id(_page))

        page.on("request", lambda request: self._schedule(self.on_request(request, page)))
        page.on("response", lambda response: self._schedule(self.on_response(response, page)))
        page.on("requestfailed", lambda request: self._schedule(self.on_request_failed(request, page)))
        page.on("console", lambda message: self._schedule(self.on_console(message, page)))
        page.on("pageerror", lambda error: self._schedule(self.on_page_error(error, page)))
        page.on("dialog", lambda dialog: self._schedule(self.on_dialog(dialog, page)))
        page.on("download", lambda download: self._schedule(self.on_download(download, page)))
        page.on("close", _closed)
        logger.debug("[%s] Network monitor attached to page %s:%s", self.context_id, self.page_label, key)

    # ------------------------------------------------------------------ handlers
    async def on_request(self, request: Any, page: Any) -> None:
        try:
            url = request.url
            if not self._should_track(url):
                return
            key = self._request_key(request)
            self._request_started[key] = time.perf_counter()
            method = request.method
            resource_type = request.resource_type
            self._request_meta[key] = (url, method, resource_type)

            self.stats.requests += 1
            self.stats.by_method[method] += 1
            self.stats.by_resource_type[resource_type] += 1
            self.stats.by_host[self._host(url)] += 1

            post_data: Optional[str] = None
            if self.config.log_request_body and method in {"POST", "PUT", "PATCH", "DELETE"}:
                try:
                    post_data = request.post_data
                except Exception:  # noqa: BLE001 - body may be gone
                    post_data = None
            size_out = None
            if post_data and self.config.track_request_bytes:
                size_out = len(post_data.encode("utf-8"))
                self.stats.bytes_out += size_out

            headers = self.redactor.headers(dict(request.headers)) if self.config.log_network_headers else {}
            event = NetworkEvent(
                kind="request",
                context_id=self.context_id,
                url=url,
                method=method,
                resource_type=resource_type,
                request_headers=headers,
                post_data=self.redactor.text(post_data, limit=2048) if post_data else None,
                size_out=size_out,
                page_url=self._page_url(page),
                frame=self._frame_url(request),
            )
            self._record(event)

            if self.config.log_network:
                extra = ""
                if event.post_data:
                    extra = f" body={truncate(event.post_data, 160)}"
                logger.info("→ %-6s %s%s", method, truncate(self._url_for_log(url), 160), extra)
            if self.config.log_api_candidates and self._is_api_candidate(url, resource_type, method):
                self._register_candidate(url, method, resource_type, event)
        except Exception as exc:  # noqa: BLE001 - monitoring must never break the flow
            logger.debug("[%s] on_request handler error: %s", self.context_id, exc)

    async def on_response(self, response: Any, page: Any) -> None:
        try:
            request = response.request
            url = response.url
            if not self._should_track(url):
                return
            key = self._request_key(request)
            started = self._request_started.pop(key, None)
            duration_ms = (time.perf_counter() - started) * 1000 if started else None
            status = response.status
            method = request.method
            resource_type = request.resource_type

            self.stats.responses += 1
            self.stats.by_status[status] += 1
            self.stats.by_status_class[f"{status // 100}xx"] += 1
            if duration_ms is not None:
                self.stats.slowest.append((duration_ms, url))
                self.stats.slowest.sort(reverse=True)
                del self.stats.slowest[15:]

            size_in: Optional[int] = None
            response_body: Optional[str] = None
            content_type = ""
            try:
                headers = dict(await response.all_headers())
            except Exception:  # noqa: BLE001 - headers unavailable after navigation
                headers = {}
            content_type = (headers.get("content-type") or "").lower()
            if self.config.track_request_bytes:
                length = headers.get("content-length")
                if length and str(length).isdigit():
                    size_in = int(length)
                    self.stats.bytes_in += size_in

            if self.config.log_response_body and self._body_is_interesting(content_type, resource_type, status):
                response_body = await self._read_body(response)
                if size_in is None and response_body is not None:
                    size_in = len(response_body.encode("utf-8"))
                    self.stats.bytes_in += size_in

            event = NetworkEvent(
                kind="response",
                context_id=self.context_id,
                url=url,
                method=method,
                status=status,
                resource_type=resource_type,
                duration_ms=round(duration_ms, 1) if duration_ms is not None else None,
                response_headers=self.redactor.headers(headers) if self.config.log_network_headers else {},
                response_body=response_body,
                size_in=size_in,
                page_url=self._page_url(page),
                api_candidate=self._is_api_candidate(url, resource_type, method) and status < 400,
            )
            self._record(event)

            if status >= 400:
                self.stats.errors.append(
                    {
                        "url": truncate(self._url_for_log(url), 200),
                        "status": status,
                        "method": method,
                        "resource_type": resource_type,
                        "page": self._page_url(page),
                    }
                )

            if self.config.log_network:
                level = self._status_level(status)
                log = logger.warning if level == "warning" else (logger.error if level == "error" else logger.info)
                marker = "  ⚠" if level == "warning" else ("  ✖" if level == "error" else "")
                body_hint = f" body={truncate(response_body, 200)}" if response_body and level != "info" else ""
                log(
                    "← %-6s %s %s%s%s",
                    method,
                    status,
                    truncate(self._url_for_log(url), 150),
                    f" ({human_ms(duration_ms)}, {human_bytes(size_in)})" if duration_ms is not None else "",
                    marker + body_hint,
                )

            if event.api_candidate:
                self._register_candidate(url, method, resource_type, event, status=status, body=response_body)
        except Exception as exc:  # noqa: BLE001 - keep the run alive
            logger.debug("[%s] on_response handler error: %s", self.context_id, exc)

    async def on_request_failed(self, request: Any, page: Any) -> None:
        try:
            url = request.url
            if not self._should_track(url):
                return
            failure = ""
            try:
                failure = request.failure or ""
            except Exception:  # noqa: BLE001
                failure = ""
            self.stats.failures += 1
            self.stats.errors.append(
                {"url": truncate(self._url_for_log(url), 200), "failure": failure, "method": request.method}
            )
            event = NetworkEvent(
                kind="failure",
                context_id=self.context_id,
                url=url,
                method=request.method,
                resource_type=request.resource_type,
                failure=failure or "unknown",
                page_url=self._page_url(page),
            )
            self._record(event)
            if self.config.log_network:
                logger.warning(
                    "✖ FAILED %-5s %s → %s", request.method, truncate(self._url_for_log(url), 150), failure or "unknown"
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] on_request_failed handler error: %s", self.context_id, exc)

    async def on_console(self, message: Any, page: Any) -> None:
        try:
            level = message.type
            text = self.redactor.text(message.text, limit=2000)
            location = None
            try:
                location = message.location
            except Exception:  # noqa: BLE001
                location = None

            if level in {"error", "warning"}:
                if level == "error":
                    self.stats.console_errors += 1
                else:
                    self.stats.console_warnings += 1
                self.stats.errors.append({"console": text, "level": level, "page": self._page_url(page)})

            event = NetworkEvent(
                kind="console",
                context_id=self.context_id,
                level=level,
                text=text,
                url=(location or {}).get("url") if isinstance(location, dict) else None,
                page_url=self._page_url(page),
            )
            self._record(event)
            if self.config.log_console and level in {"error", "warning", "debug"}:
                log = logger.error if level == "error" else (logger.warning if level == "warning" else logger.debug)
                log("⌨ console.%s %s", level, truncate(text, 180))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] on_console handler error: %s", self.context_id, exc)

    async def on_page_error(self, error: Any, page: Any) -> None:
        try:
            self.stats.page_errors += 1
            text = self.redactor.text(str(error), limit=2000)
            self.stats.errors.append({"pageerror": text, "page": self._page_url(page)})
            self._record(
                NetworkEvent(kind="pageerror", context_id=self.context_id, text=text, page_url=self._page_url(page))
            )
            logger.error("💥 pageerror %s", truncate(text, 220))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] on_page_error handler error: %s", self.context_id, exc)

    async def on_dialog(self, dialog: Any, page: Any) -> None:
        try:
            self.stats.dialogs += 1
            text = self.redactor.text(getattr(dialog, "message", ""), limit=500)
            self._record(
                NetworkEvent(
                    kind="dialog",
                    context_id=self.context_id,
                    text=text,
                    level=getattr(dialog, "type", "dialog"),
                    page_url=self._page_url(page),
                )
            )
            if self.config.log_dialog_events:
                logger.info("🗨 dialog.%s %s", getattr(dialog, "type", "dialog"), truncate(text, 150))
            # JS dialogs stall navigation; accept them unless the caller handles it.
            try:
                await dialog.accept()
            except Exception:  # noqa: BLE001 - may already be handled
                pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] on_dialog handler error: %s", self.context_id, exc)

    async def on_download(self, download: Any, page: Any) -> None:
        try:
            self.stats.downloads += 1
            suggested = ""
            try:
                suggested = download.suggested_filename
            except Exception:  # noqa: BLE001
                suggested = ""
            self._record(
                NetworkEvent(
                    kind="download",
                    context_id=self.context_id,
                    url=truncate(getattr(download, "url", ""), 300),
                    text=suggested,
                    page_url=self._page_url(page),
                )
            )
            logger.info("⬇ download: %s", suggested or "unnamed")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] on_download handler error: %s", self.context_id, exc)

    # ------------------------------------------------------------------ API discovery
    def _register_candidate(
        self,
        url: str,
        method: str,
        resource_type: str,
        event: NetworkEvent,
        *,
        status: Optional[int] = None,
        body: Optional[str] = None,
    ) -> None:
        """Record a discovered endpoint: method + path template + query keys."""
        parsed = urlsplit(url)
        key = f"{method.upper()} {parsed.netloc}{parsed.path}"
        entry = self.endpoints.get(key)
        record: dict[str, Any] = {
            "method": method.upper(),
            "host": parsed.netloc,
            "path": parsed.path,
            "query_keys": sorted({k for k, _ in parse_qsl(parsed.query, keep_blank_values=True)}),
            "resource_type": resource_type,
            "sample_url": self._url_for_log(url),
            "statuses": [],
            "count": 0,
            "last_seen": now_iso(),
        }
        if status is not None:
            record["statuses"] = [status]
        if body:
            try:
                payload = json.loads(body)
                if isinstance(payload, dict):
                    record["response_keys"] = sorted(list(payload.keys()))[:30]
                elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
                    record["response_keys"] = sorted(list(payload[0].keys()))[:30]
            except (json.JSONDecodeError, TypeError):
                pass

        if entry is None:
            self.endpoints[key] = record
            self.stats.api_candidates.add(key)
            logger.info(
                "[API] discovered %s %s%s",
                method.upper(),
                parsed.netloc,
                parsed.path or "/",
            )
            if self.on_api_candidate is not None:
                try:
                    self.on_api_candidate(record)
                except Exception:  # noqa: BLE001 - callback must not break monitoring
                    logger.debug("[%s] api candidate callback failed", self.context_id)
        else:
            entry["count"] += 1
            entry["last_seen"] = now_iso()
            if status is not None and status not in entry["statuses"]:
                entry["statuses"].append(status)
        event.api_candidate = True

    # ------------------------------------------------------------------ lifecycle
    async def drain(self, timeout: float = 5.0) -> None:
        """Wait for pending event handlers (bounded) - call before closing the context."""
        if not self._pending_tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*list(self._pending_tasks), return_exceptions=True), timeout=timeout
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.debug("[%s] network handlers still pending after %.1fs", self.context_id, timeout)

    def flush(self) -> dict[str, Any]:
        """Persist buffered events/metrics and return the context traffic summary."""
        if self._network_log_path is not None:
            try:
                write_jsonl(self._network_log_path, [event.to_dict() for event in self.events])
            except Exception as exc:  # noqa: BLE001 - artifact IO is best effort
                logger.warning("[%s] could not write %s: %s", self.context_id, self._network_log_path, exc)
        if self._console_log_path is not None:
            console_events = [e.to_dict() for e in self.events if e.kind in {"console", "pageerror", "dialog"}]
            try:
                write_jsonl(self._console_log_path, console_events)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] could not write %s: %s", self.context_id, self._console_log_path, exc)

        summary = self.summary()
        if self._network_log_path is not None:
            try:
                meta_path = self._network_log_path.with_name("network_summary.json")
                meta_path.write_text(json_dumps(summary, indent=2) + "\n", encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                logger.debug("[%s] could not write network summary: %s", self.context_id, exc)
        return summary

    def summary(self) -> dict[str, Any]:
        """Return the traffic statistics + discovered endpoints."""
        payload = self.stats.to_dict()
        payload["context_id"] = self.context_id
        payload["endpoints"] = sorted(
            self.endpoints.values(), key=lambda item: (-item.get("count", 0), item["path"])
        )[:200]
        payload["generated_at"] = now_iso()
        return payload

    def step_result(self, name: str = "network-traffic") -> StepResult:
        """Represent the traffic summary as a :class:`StepResult` for reports."""
        stats = self.stats
        return StepResult(
            name=name,
            status="ok",
            details={
                "requests": stats.requests,
                "responses": stats.responses,
                "failures": stats.failures,
                "console_errors": stats.console_errors,
                "page_errors": stats.page_errors,
                "bytes_in_human": human_bytes(stats.bytes_in),
                "http_4xx_5xx": sum(count for status, count in stats.by_status.items() if status >= 400),
                "api_endpoints": len(stats.api_candidates),
            },
        )

    # ------------------------------------------------------------------ helpers
    def _schedule(self, coroutine: Awaitable[Any]) -> None:
        """Run a handler without blocking Playwright's event dispatching."""
        try:
            task = asyncio.create_task(coroutine)  # type: ignore[arg-type]
        except RuntimeError:  # pragma: no cover - no running loop (context teardown)
            return
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def _record(self, event: NetworkEvent) -> None:
        self.events.append(event)

    def _should_track(self, url: str) -> bool:
        if not url:
            return False
        if url.startswith(("data:", "blob:", "about:", "chrome-extension:", "chrome:", "devtools:")):
            return False
        host = self._host(url)
        for pattern in self.config.ignore_network_hosts:
            if pattern and pattern in host:
                return False
        return True

    @staticmethod
    def _request_key(request: Any) -> str:
        try:
            return f"{id(request)}"
        except Exception:  # pragma: no cover - defensive
            return str(request)

    @staticmethod
    def _host(url: str) -> str:
        try:
            return urlsplit(url).netloc or "unknown"
        except Exception:  # pragma: no cover - defensive
            return "unknown"

    @staticmethod
    def _frame_url(request: Any) -> Optional[str]:
        try:
            frame = request.frame
            return frame.url if frame else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _page_url(page: Any) -> Optional[str]:
        try:
            return page.url
        except Exception:  # noqa: BLE001
            return None

    def _url_for_log(self, url: str) -> str:
        """Strip credentials from a URL and redact sensitive query values."""
        try:
            parsed = urlsplit(url)
        except Exception:  # pragma: no cover - defensive
            return url
        if parsed.username or parsed.password:
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            netloc = f"***:***@{host}"
        else:
            netloc = parsed.netloc
        query = parsed.query
        if self.config.redact and query:
            query = self.redactor.text(query, limit=400)
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))

    def _is_api_candidate(self, url: str, resource_type: str, method: str) -> bool:
        if resource_type in {"xhr", "fetch"}:
            return True
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            return True
        try:
            path = urlsplit(url).path
        except Exception:  # pragma: no cover - defensive
            return False
        return bool(API_CANDIDATE_RE.search(path))

    @staticmethod
    def _status_level(status: int) -> str:
        if status >= 500:
            return "error"
        if status >= 400:
            return "warning"
        return "info"

    @staticmethod
    def _body_is_interesting(content_type: str, resource_type: str, status: int) -> bool:
        if status >= 400:
            return True  # error payloads carry the most useful API details
        if resource_type in STATIC_RESOURCE_TYPES:
            return any(token in content_type for token in BODY_CONTENT_TYPES)
        return any(token in content_type for token in BODY_CONTENT_TYPES) or "javascript" in content_type

    async def _read_body(self, response: Any, timeout: float = 5.0) -> Optional[str]:
        """Read a bounded, redacted response body (never raises)."""
        try:
            body = await asyncio.wait_for(response.text(), timeout=timeout)
        except Exception:  # noqa: BLE001 - binary/streamed/closed bodies
            return None
        if body is None:
            return None
        content_type = ""
        try:
            content_type = (await response.all_headers()).get("content-type", "")
        except Exception:  # noqa: BLE001
            pass
        if "json" in content_type.lower():
            try:
                body = json_dumps(json.loads(body))
            except Exception:  # noqa: BLE001 - keep raw text when invalid JSON
                pass
        return self.redactor.text(body, limit=self.config.log_response_body_max_chars)

    async def close(self) -> None:
        """Drain pending handlers and flush artifacts."""
        await self.drain()
        for task in list(self._pending_tasks):
            task.cancel()
        await asyncio.gather(*list(self._pending_tasks), return_exceptions=True)
        self._pending_tasks.clear()
