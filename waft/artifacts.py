"""Artifact handling: screenshots, traces, HTML dumps, HAR files and .zip failure bundles.

Layout produced for every run::

    artifacts/<run_id>/
        run.json                     # final summary (reporting.py writes it too)
        junit.xml, results.csv       # machine readable reports
        logs/waft.log                # rotating, full detail
        contexts/ctx-01-4f2a/
            screenshots/00_initial-20260928-141530.png
            traces/ctx-01-4f2a-13c9f1.zip          # Playwright trace
            html/after-submit.html
            har/network.har
            network.jsonl / console.jsonl
            summary.json                          # per-context result
            failure-bundle.zip                    # only when something failed

Every write is defensive: artifact IO must never take a run down, so failures are logged
and swallowed (except in ``--strict-artifacts`` style debugging where the caller inspects
the returned value).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from .config import Config
from .logging_setup import get_logger
from .models import ContextResult, RunSummary, TargetRunResult
from .utils import ensure_dir, human_bytes, json_dumps, now_iso, safe_filename, slugify, ts_slug, truncate, write_json

__all__ = ["ArtifactManager", "ArtifactRecord"]

logger = get_logger("waft.artifacts")


@dataclass
class ArtifactRecord:
    """Bookkeeping entry for a produced file."""

    context_id: str
    kind: str  # screenshot | trace | html | har | network | console | bundle | state | other
    path: str
    bytes: int = 0
    created_at: str = field(default_factory=now_iso)
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "kind": self.kind,
            "path": self.path,
            "relative_path": self.path,
            "bytes": self.bytes,
            "size_human": human_bytes(self.bytes),
            "created_at": self.created_at,
            "label": self.label,
        }


class ArtifactManager:
    """Creates and tracks every artifact the framework produces."""

    def __init__(self, config: Config, run_id: str) -> None:
        self.config = config
        self.run_id = run_id
        self.root = ensure_dir(config.artifact_root / run_id)
        self.contexts_root = ensure_dir(self.root / "contexts")
        self.logs_root = ensure_dir(self.root / "logs")
        self.records: list[ArtifactRecord] = []
        self.bytes_written = 0
        self.screenshots_taken = 0
        self.traces_written = 0
        self.bundles_written = 0
        self._counters: dict[str, int] = {}
        self._disk_limit_bytes = max(0, int(config.max_artifacts_mb)) * 1024 * 1024
        self._disk_warned = False

    # ------------------------------------------------------------------ paths
    def context_dir(self, context_id: str, *, sub: Optional[str] = None) -> Path:
        directory = ensure_dir(self.contexts_root / context_id)
        if sub:
            directory = ensure_dir(directory / sub)
        return directory

    def next_index(self, context_id: str, prefix: str) -> int:
        key = f"{context_id}:{prefix}"
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    def screenshot_path(self, context_id: str, label: str) -> Path:
        index = self.next_index(context_id, "screenshot")
        name = f"{index:02d}_{slugify(label, max_length=48)}_{ts_slug()}.png"
        return self.context_dir(context_id, sub="screenshots") / name

    # ------------------------------------------------------------------ disk guard
    def _budget_ok(self, kind: str) -> bool:
        if self._disk_limit_bytes and self.bytes_written > self._disk_limit_bytes:
            if not self._disk_warned:
                logger.warning(
                    "Artifact budget of %s exceeded - skipping further %s artifacts",
                    human_bytes(self._disk_limit_bytes),
                    kind,
                )
                self._disk_warned = True
            return False
        return True

    def _track(self, context_id: str, kind: str, path: Path, label: str = "") -> ArtifactRecord:
        try:
            size = path.stat().st_size if path.exists() else 0
        except OSError:
            size = 0
        self.bytes_written += size
        record = ArtifactRecord(context_id=context_id, kind=kind, path=str(path), bytes=size, label=label)
        self.records.append(record)
        logger.debug("Artifact %-10s %-60s (%s)", kind, path.name, human_bytes(size))
        return record

    # ------------------------------------------------------------------ screenshots
    async def screenshot(
        self,
        page: Any,
        context_id: str,
        label: str,
        *,
        full_page: Optional[bool] = None,
        on_failure: bool = False,
    ) -> Optional[str]:
        """Capture a screenshot; returns its path (or ``None`` when skipped/failed)."""
        if not self.config.screenshots:
            return None
        if on_failure and not self.config.screenshot_on_failure:
            return None
        if not on_failure and not self.config.screenshot_on_success:
            return None
        if not self._budget_ok("screenshot"):
            return None
        if page is None:
            return None

        path = self.screenshot_path(context_id, label)
        try:
            await page.screenshot(
                path=str(path),
                full_page=self.config.screenshot_full_page if full_page is None else full_page,
                animations="disabled",
                caret="hide",
                timeout=min(15_000, max(5_000, self.config.default_timeout_ms)),
            )
            self.screenshots_taken += 1
            self._track(context_id, "screenshot", path, label=label)
            logger.debug("📸 %s/%s", context_id, path.name)
            return str(path)
        except Exception as exc:  # noqa: BLE001 - screenshots are best effort
            logger.debug("Screenshot failed for %s (%s): %s", context_id, label, truncate(str(exc), 160))
            return None

    async def screenshot_bytes(self, page: Any, *, full_page: bool = False) -> Optional[bytes]:
        """Capture a screenshot into memory (used for HTML/e-mail reports)."""
        try:
            return await page.screenshot(full_page=full_page, animations="disabled", caret="hide")
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ HTML
    async def save_html(self, page: Any, context_id: str, label: str) -> Optional[str]:
        """Persist the current DOM for offline debugging."""
        if not self.config.screenshot_on_failure and not self.config.save_html_on_failure:
            return None
        if not self._budget_ok("html"):
            return None
        try:
            content = await page.content()
        except Exception as exc:  # noqa: BLE001
            logger.debug("HTML dump failed for %s: %s", context_id, exc)
            return None
        path = self.context_dir(context_id, sub="html") / f"{slugify(label, max_length=48)}.html"
        try:
            path.write_text(content, encoding="utf-8")
            self._track(context_id, "html", path, label=label)
            return str(path)
        except OSError as exc:  # pragma: no cover - filesystem issue
            logger.debug("Could not write HTML dump %s: %s", path, exc)
            return None

    def save_text(self, context_id: str, label: str, content: str, *, sub: str = "debug", extension: str = ".txt") -> Optional[str]:
        """Write arbitrary text (pipeline dumps, JS evaluation results …)."""
        if not self._budget_ok("text"):
            return None
        path = self.context_dir(context_id, sub=sub) / f"{safe_filename(label, max_length=60)}{extension}"
        try:
            path.write_text(content, encoding="utf-8")
            self._track(context_id, "other", path, label=label)
            return str(path)
        except OSError as exc:  # pragma: no cover
            logger.debug("Could not write %s: %s", path, exc)
            return None

    def save_json(self, context_id: str, label: str, payload: Any, *, sub: str = "debug") -> Optional[str]:
        """Write a JSON artifact (never raises for serialisation issues)."""
        path = self.context_dir(context_id, sub=sub) / f"{safe_filename(label, max_length=60)}.json"
        try:
            write_json(path, payload)
            self._track(context_id, "other", path, label=label)
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not write JSON artifact %s: %s", path, exc)
            return None

    # ------------------------------------------------------------------ tracing
    async def start_trace(self, context: Any, context_id: str) -> bool:
        """Start Playwright tracing for a context (mode dependent)."""
        if self.config.trace_mode == "off":
            return False
        try:
            await context.tracing.start(
                screenshots=self.config.trace_screenshots,
                snapshots=self.config.trace_snapshots,
                sources=self.config.trace_sources,
                title=f"WAFT {self.run_id} / {context_id}",
            )
            logger.debug("[%s] Tracing started (mode=%s)", context_id, self.config.trace_mode)
            return True
        except Exception as exc:  # noqa: BLE001 - tracing is optional
            logger.warning("[%s] Could not start tracing: %s", context_id, exc)
            return False

    async def stop_trace(self, context: Any, context_id: str, *, discard: bool = False) -> Optional[str]:
        """Stop tracing; keep the file only when required by the trace mode."""
        if self.config.trace_mode == "off":
            return None
        should_save = not discard and self.config.trace_mode in {"on", "on-failure", "retain-on-failure"}
        path: Optional[Path] = None
        if should_save and self._budget_ok("trace"):
            path = self.context_dir(context_id, sub="traces") / f"{context_id}-{ts_slug()}.zip"
        try:
            if path is None:
                await context.tracing.stop()
                logger.debug("[%s] Trace discarded (mode=%s, discard=%s)", context_id, self.config.trace_mode, discard)
                return None
            await context.tracing.stop(path=str(path))
            self.traces_written += 1
            self._track(context_id, "trace", path, label="playwright-trace")
            logger.info("[%s] 🧭 Trace saved: %s", context_id, path.name)
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Could not stop/save trace: %s", context_id, exc)
            return None

    async def trace_chunk(self, context: Any, context_id: str, label: str) -> Optional[str]:
        """Optional helper: save a rolling trace chunk without stopping the recording."""
        try:
            if self.config.trace_mode == "off" or not hasattr(context.tracing, "start_chunk"):
                return None
            path = self.context_dir(context_id, sub="traces") / f"{context_id}-{slugify(label, max_length=24)}-{ts_slug()}.zip"
            await context.tracing.start_chunk(title=label)
            await context.tracing.stop_chunk(path=str(path))
            self._track(context_id, "trace", path, label=label)
            return str(path)
        except Exception as exc:  # noqa: BLE001 - not available in older Playwright versions
            logger.debug("[%s] trace chunk failed: %s", context_id, exc)
            return None

    # ------------------------------------------------------------------ storage state
    async def save_storage_state(self, context: Any, context_id: str) -> Optional[str]:
        """Persist cookies + localStorage so the session can be replayed later."""
        if not self.config.save_storage_state:
            return None
        directory = self.config.storage_state_dir or (self.contexts_root / context_id)
        ensure_dir(directory)
        path = Path(directory) / f"{context_id}-storage.json"
        try:
            await context.storage_state(path=str(path))
            self._track(context_id, "state", path, label="storage-state")
            logger.info("[%s] 💾 Storage state saved (%s)", context_id, path.name)
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] Could not save storage state: %s", context_id, exc)
            return None

    # ------------------------------------------------------------------ bundles
    def write_context_summary(self, result: ContextResult) -> Optional[str]:
        """Write ``contexts/<id>/summary.json``."""
        try:
            path = self.context_dir(result.context_id) / "summary.json"
            write_json(path, result.to_dict())
            self._track(result.context_id, "other", path, label="context-summary")
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not write context summary: %s", exc)
            return None

    def create_failure_bundle(
        self,
        result: ContextResult,
        *,
        extra_files: Optional[Sequence[Any]] = None,
        include_trace: bool = True,
    ) -> Optional[str]:
        """Zip everything relevant for a failing context into one downloadable archive."""
        if not self.config.failure_bundle:
            return None
        if result.ok and not any(not run.ok for run in result.runs):
            return None

        context_dir = self.context_dir(result.context_id)
        bundle_path = context_dir / f"{result.context_id}-failure-bundle.zip"
        try:
            files: list[Path] = []
            for candidate in context_dir.rglob("*"):
                if not candidate.is_file():
                    continue
                if candidate.name == bundle_path.name:
                    continue
                if candidate.suffix == ".zip" and candidate.name.startswith(result.context_id) and "trace" in candidate.parent.name and not include_trace:
                    continue
                files.append(candidate)
            for extra in extra_files or []:
                extra_path = Path(extra)
                if extra_path.exists() and extra_path.is_file():
                    files.append(extra_path)

            manifest = {
                "run_id": self.run_id,
                "context_id": result.context_id,
                "status": result.status,
                "error": result.error,
                "created_at": now_iso(),
                "files": [str(p.relative_to(context_dir)) if context_dir in p.parents else str(p) for p in files],
                "summary": result.to_dict(),
            }
            with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json_dumps(manifest, indent=2))
                for path in files:
                    try:
                        arcname = str(path.relative_to(context_dir))
                    except ValueError:
                        arcname = f"external/{path.name}"
                    archive.write(path, arcname=arcname)
            self.bundles_written += 1
            self._track(result.context_id, "bundle", bundle_path, label="failure-bundle")
            logger.info(
                "[%s] 📦 Failure bundle created: %s (%s, %d file(s))",
                result.context_id,
                bundle_path.name,
                human_bytes(bundle_path.stat().st_size),
                len(files),
            )
            return str(bundle_path)
        except Exception as exc:  # noqa: BLE001 - bundling must not mask the real error
            logger.warning("[%s] Could not create failure bundle: %s", result.context_id, exc)
            return None

    # ------------------------------------------------------------------ HAR
    def har_path(self, context_id: str) -> Optional[Path]:
        """Path handed to ``browser.new_context(record_har_path=…)``."""
        if not self.config.capture_har:
            return None
        return self.context_dir(context_id, sub="har") / "network.har"

    def redact_har(self, path: Optional[Path]) -> Optional[str]:
        """Strip cookies/authorization headers from a recorded HAR (privacy first)."""
        if path is None or not Path(path).exists():
            return None
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not read HAR %s: %s", path, exc)
            return None
        if not self.config.redact:
            self._track(Path(path).parent.parent.name, "har", Path(path), label="har")
            return str(path)

        sensitive = {
            "authorization",
            "cookie",
            "set-cookie",
            "x-api-key",
            "x-auth-token",
            "x-csrf-token",
            "proxy-authorization",
        } | {key.lower() for key in self.config.sensitive_headers}

        def scrub_headers(headers: Any) -> None:
            if not isinstance(headers, list):
                return
            for header in headers:
                name = str(header.get("name", "")).lower()
                if name in sensitive:
                    header["value"] = "***REDACTED***"

        try:
            for entry in payload.get("log", {}).get("entries", []):
                request = entry.get("request", {})
                response = entry.get("response", {})
                scrub_headers(request.get("headers"))
                scrub_headers(response.get("headers"))
                scrub_headers(response.get("cookies"))
                scrub_headers(request.get("cookies"))
                for collection in ("queryString", "postData"):
                    data = request.get(collection, [])
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and any(
                                token in str(item.get("name", "")).lower()
                                for token in ("password", "passwd", "token", "secret", "otp", "pin", "code")
                            ):
                                item["value"] = "***REDACTED***"
                    elif isinstance(data, dict) and data:
                        for key in list(data.keys()):
                            if any(token in key.lower() for token in ("password", "token", "secret", "otp", "pin")):
                                data[key] = "***REDACTED***"
            Path(path).write_text(json_dumps(payload, indent=1), encoding="utf-8")
            self._track("har", "har", Path(path), label="har-redacted")
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("HAR redaction failed for %s: %s", path, exc)
            return str(path)

    # ------------------------------------------------------------------ run level
    def cleanup_partial(self) -> None:
        """Remove empty per-context directories (keeps the artifact tree tidy)."""
        try:
            for directory in self.contexts_root.iterdir():
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
        except OSError:  # pragma: no cover - best effort
            pass

    def index(self) -> dict[str, Any]:
        """Return a manifest of every artifact produced during the run."""
        by_kind: dict[str, int] = {}
        by_context: dict[str, int] = {}
        total = 0
        for record in self.records:
            total += record.bytes
            by_kind[record.kind] = by_kind.get(record.kind, 0) + 1
            by_context[record.context_id] = by_context.get(record.context_id, 0) + 1
        return {
            "run_id": self.run_id,
            "root": str(self.root),
            "count": len(self.records),
            "total_bytes": total,
            "total_human": human_bytes(total),
            "by_kind": by_kind,
            "by_context": by_context,
            "screenshots": self.screenshots_taken,
            "traces": self.traces_written,
            "bundles": self.bundles_written,
            "disk_limit_human": human_bytes(self._disk_limit_bytes) if self._disk_limit_bytes else "unlimited",
            "records": [record.to_dict() for record in self.records][:2000],
        }

    def write_index(self) -> Optional[str]:
        """Persist ``artifacts/<run_id>/artifact-index.json``."""
        try:
            path = self.root / "artifact-index.json"
            write_json(path, self.index())
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not write artifact index: %s", exc)
            return None

    def write_run_summary(self, summary: RunSummary) -> Optional[str]:
        """Persist ``artifacts/<run_id>/run.json`` (also done by reporting.py)."""
        try:
            path = self.root / "run.json"
            write_json(path, summary.to_dict())
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not write run summary: %s", exc)
            return None

    def directory_size(self) -> int:
        total = 0
        for candidate in self.root.rglob("*"):
            if candidate.is_file():
                try:
                    total += candidate.stat().st_size
                except OSError:
                    continue
        return total

    def copy_into(self, run_dir: Path, files: Sequence[Any]) -> list[str]:
        """Copy loose files (e.g. HARS) into the run directory (used by reporting)."""
        copied: list[str] = []
        for entry in files:
            source = Path(entry)
            if not source.exists():
                continue
            target = run_dir / source.name
            try:
                shutil.copy2(source, target)
                copied.append(str(target))
            except OSError as exc:  # pragma: no cover
                logger.debug("Could not copy %s → %s: %s", source, target, exc)
        return copied

    async def wait_for_io(self) -> None:
        """Yield briefly so the event loop (and the OS) can flush pending artifact writes."""
        await asyncio.sleep(0.05)


def bundle_path_for(artifacts: ArtifactManager, result: ContextResult) -> Optional[str]:
    """Convenience wrapper: create a bundle and return its path."""
    return artifacts.create_failure_bundle(result)


def summary_paths(artifacts: ArtifactManager, results: Sequence[TargetRunResult]) -> dict[str, Any]:
    """Small helper used by reporters to map context ids to artifact files."""
    payload: dict[str, Any] = {}
    for run in results:
        payload.setdefault(run.context_id, {"screenshots": [], "trace": None, "html": None})
        payload[run.context_id]["screenshots"].extend(run.screenshots)
        payload[run.context_id]["trace"] = run.trace_path or payload[run.context_id]["trace"]
        payload[run.context_id]["html"] = run.html_dump or payload[run.context_id]["html"]
    return payload


__all__ += ["bundle_path_for", "summary_paths"]
