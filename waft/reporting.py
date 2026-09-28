"""Report generation: JSON, CSV, JUnit XML, Markdown and a rich console summary.

All reports land inside ``artifacts/<run_id>/``:

===========================  =========================================================
File                         Content
===========================  =========================================================
``run.json``                 Full machine-readable summary (contexts + runs + steps).
``results.csv``              One row per target execution (ideal for spreadsheets/BI).
``contexts.csv``             One row per browser context (proxy, device, timings).
``junit.xml``                CI-friendly report (one testsuite per context).
``failures.json``            Only the failing runs, with artifact paths.
``endpoints.json``           Deduplicated API discovery results.
``summary.md``               Human readable Markdown report (great for PRs/tickets).
===========================  =========================================================
"""

from __future__ import annotations

import csv
import html
import io
import json
import platform
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

from .artifacts import ArtifactManager
from .config import Config
from .logging_setup import get_logger
from .models import ContextResult, RunSummary, STATUS_FAILED, STATUS_OK, TargetRunResult
from .utils import human_ms, import_optional, json_dumps, truncate, write_json

__all__ = ["Reporter", "render_summary_table", "render_markdown"]

logger = get_logger("waft.reporting")


class Reporter:
    """Writes every report format and prints the console summary."""

    def __init__(self, config: Config, artifacts: ArtifactManager, run_id: str) -> None:
        self.config = config
        self.artifacts = artifacts
        self.run_id = run_id
        self.root = artifacts.root
        self._rich = import_optional("rich.console", "Console")
        self._rich_table = import_optional("rich.table", "Table")

    # ------------------------------------------------------------------ public
    def write_all(self, summary: RunSummary) -> dict[str, Optional[str]]:
        """Write every enabled report; returns a mapping of report name → path."""
        written: dict[str, Optional[str]] = {}
        if self.config.summary_json:
            written["run.json"] = self._write_run_json(summary)
            written["failures.json"] = self._write_failures(summary)
            written["endpoints.json"] = self._write_endpoints(summary)
        if self.config.csv_report:
            written["results.csv"] = self._write_results_csv(summary)
            written["contexts.csv"] = self._write_contexts_csv(summary)
        if self.config.junit_xml:
            written["junit.xml"] = self._write_junit(summary)
        written["summary.md"] = self._write_markdown(summary)
        return {key: value for key, value in written.items() if value}

    # ------------------------------------------------------------------ JSON
    def _write_run_json(self, summary: RunSummary) -> Optional[str]:
        try:
            path = self.root / "run.json"
            write_json(path, summary.to_dict())
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not write run.json: %s", exc)
            return None

    def _write_failures(self, summary: RunSummary) -> Optional[str]:
        try:
            path = self.root / "failures.json"
            write_json(
                path,
                {
                    "run_id": summary.run_id,
                    "count": len(summary.failures),
                    "failures": summary.failures,
                },
            )
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not write failures.json: %s", exc)
            return None

    def _write_endpoints(self, summary: RunSummary) -> Optional[str]:
        """Deduplicated API endpoints discovered across all contexts."""
        try:
            aggregated: dict[str, dict[str, Any]] = {}
            for context in summary.contexts:
                for run in context.runs:
                    for endpoint in (run.network_metrics or {}).get("endpoints", []) or []:
                        key = f"{endpoint.get('method')} {endpoint.get('host')}{endpoint.get('path')}"
                        entry = aggregated.setdefault(
                            key,
                            {
                                "method": endpoint.get("method"),
                                "host": endpoint.get("host"),
                                "path": endpoint.get("path"),
                                "query_keys": endpoint.get("query_keys", []),
                                "response_keys": endpoint.get("response_keys", []),
                                "statuses": [],
                                "seen_in_contexts": [],
                                "count": 0,
                            },
                        )
                        entry["count"] += endpoint.get("count", 0) + 1
                        for status in endpoint.get("statuses", []):
                            if status not in entry["statuses"]:
                                entry["statuses"].append(status)
                        if context.context_id not in entry["seen_in_contexts"]:
                            entry["seen_in_contexts"].append(context.context_id)
                        if not entry["response_keys"] and endpoint.get("response_keys"):
                            entry["response_keys"] = endpoint["response_keys"]
            path = self.root / "endpoints.json"
            write_json(
                path,
                {
                    "run_id": summary.run_id,
                    "count": len(aggregated),
                    "endpoints": [aggregated[key] for key in sorted(aggregated)],
                },
            )
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not write endpoints.json: %s", exc)
            return None

    # ------------------------------------------------------------------ CSV
    def _write_results_csv(self, summary: RunSummary) -> Optional[str]:
        path = self.root / "results.csv"
        columns = [
            "context_id",
            "row_index",
            "row_name",
            "iteration",
            "attempt",
            "status",
            "scenario",
            "target_url",
            "final_url",
            "final_title",
            "duration_ms",
            "steps_ok",
            "steps_failed",
            "fields_filled",
            "fields_unmatched",
            "verification_method",
            "verification_ok",
            "requests",
            "responses",
            "failed_requests",
            "console_errors",
            "page_errors",
            "http_4xx_5xx",
            "bytes_in",
            "api_endpoints",
            "proxy",
            "device",
            "error_type",
            "error",
            "trace_path",
            "screenshot",
        ]
        try:
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                for context in summary.contexts:
                    for run in context.runs:
                        metrics = run.network_metrics or {}
                        fill = run.fill_report.to_dict() if run.fill_report else {}
                        statuses = metrics.get("by_status", {}) or {}
                        http_errors = sum(count for status, count in statuses.items() if str(status).startswith(("4", "5")))
                        writer.writerow(
                            {
                                "context_id": context.context_id,
                                "row_index": run.row_index,
                                "row_name": run.metadata.get("row_name") or "",
                                "iteration": run.iteration,
                                "attempt": run.attempt,
                                "status": run.status,
                                "scenario": run.scenario,
                                "target_url": run.target_url,
                                "final_url": run.final_url or "",
                                "final_title": truncate(run.final_title or "", 120),
                                "duration_ms": run.duration_ms,
                                "steps_ok": sum(1 for step in run.steps if step.status == STATUS_OK),
                                "steps_failed": sum(1 for step in run.steps if step.status == STATUS_FAILED),
                                "fields_filled": fill.get("total_filled", 0),
                                "fields_unmatched": ";".join(run.fill_report.unmatched) if run.fill_report else "",
                                "verification_method": run.verification.method if run.verification else "",
                                "verification_ok": bool(run.verification.success) if run.verification else "",
                                "requests": metrics.get("requests", 0),
                                "responses": metrics.get("responses", 0),
                                "failed_requests": metrics.get("failures", 0),
                                "console_errors": metrics.get("console_errors", 0),
                                "page_errors": metrics.get("page_errors", 0),
                                "http_4xx_5xx": http_errors,
                                "bytes_in": metrics.get("bytes_in", 0),
                                "api_endpoints": len(run.api_endpoints or []),
                                "proxy": run.proxy or "",
                                "device": run.device or "",
                                "error_type": run.error_type or "",
                                "error": truncate(run.error or "", 400),
                                "trace_path": run.trace_path or "",
                                "screenshot": (run.screenshots or [""])[-1],
                            }
                        )
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not write results.csv: %s", exc)
            return None

    def _write_contexts_csv(self, summary: RunSummary) -> Optional[str]:
        path = self.root / "contexts.csv"
        columns = [
            "context_id",
            "index",
            "status",
            "proxy",
            "device",
            "locale",
            "timezone_id",
            "targets",
            "ok",
            "failed",
            "duration_ms",
            "requests",
            "bytes_in",
            "trace_path",
            "failure_bundle",
            "error",
        ]
        try:
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                for context in summary.contexts:
                    requests = sum((run.network_metrics or {}).get("requests", 0) for run in context.runs)
                    bytes_in = sum((run.network_metrics or {}).get("bytes_in", 0) for run in context.runs)
                    writer.writerow(
                        {
                            "context_id": context.context_id,
                            "index": context.index,
                            "status": context.status,
                            "proxy": context.proxy or "",
                            "device": context.device or "",
                            "locale": context.locale or "",
                            "timezone_id": context.timezone_id or "",
                            "targets": len(context.runs),
                            "ok": sum(1 for run in context.runs if run.ok),
                            "failed": sum(1 for run in context.runs if not run.ok),
                            "duration_ms": context.duration_ms,
                            "requests": requests,
                            "bytes_in": bytes_in,
                            "trace_path": context.trace_path or "",
                            "failure_bundle": context.failure_bundle or "",
                            "error": truncate(context.error or "", 300),
                        }
                    )
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not write contexts.csv: %s", exc)
            return None

    # ------------------------------------------------------------------ JUnit
    def _write_junit(self, summary: RunSummary) -> Optional[str]:
        """CI-friendly JUnit XML (works with Jenkins/GitLab/GitHub Actions)."""
        try:
            suites = ET.Element("testsuites", attrib={"name": "WAFT", "time": f"{summary.duration_ms / 1000:.3f}"})
            for context in summary.contexts:
                suite_time = context.duration_ms / 1000.0
                suite = ET.SubElement(
                    suites,
                    "testsuite",
                    attrib={
                        "name": context.context_id,
                        "tests": str(len(context.runs)),
                        "failures": str(sum(1 for run in context.runs if not run.ok)),
                        "errors": "1" if context.error else "0",
                        "skipped": str(sum(1 for run in context.runs if run.status == "skipped")),
                        "time": f"{suite_time:.3f}",
                        "timestamp": context.started_at,
                        "hostname": platform.node(),
                        "properties": json.dumps(
                            {
                                "proxy": context.proxy,
                                "device": context.device,
                                "locale": context.locale,
                                "timezone": context.timezone_id,
                            }
                        ),
                    },
                )
                if context.error:
                    ET.SubElement(suite, "error", attrib={"type": context.error_type or "ContextError", "message": context.error})
                for run in context.runs:
                    case = ET.SubElement(
                        suite,
                        "testcase",
                        attrib={
                            "classname": f"waft.{context.context_id}",
                            "name": f"row{run.row_index}-{run.metadata.get('row_name') or 'target'}-it{run.iteration}",
                            "time": f"{run.duration_ms / 1000:.3f}",
                        },
                    )
                    if not run.ok:
                        failure = ET.SubElement(
                            case,
                            "failure",
                            attrib={"type": run.error_type or "Failure", "message": truncate(run.error or "failed", 400)},
                        )
                        failure.text = self._failure_text(run, context)
                    elif run.status == "skipped":
                        ET.SubElement(case, "skipped", attrib={"message": "skipped"})
                    stdout = ET.SubElement(case, "system-out")
                    stdout.text = self._case_stdout(run)
            tree = ET.ElementTree(suites)
            ET.indent(tree, space="  ") if hasattr(ET, "indent") else None
            path = self.root / "junit.xml"
            tree.write(path, encoding="utf-8", xml_declaration=True)
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not write junit.xml: %s", exc)
            return None

    @staticmethod
    def _failure_text(run: TargetRunResult, context: ContextResult) -> str:
        failed_step = run.failed_step()
        lines = [
            f"url: {run.target_url}",
            f"final_url: {run.final_url}",
            f"scenario: {run.scenario}",
            f"attempts: {run.attempt}",
            f"error: {run.error}",
        ]
        if failed_step is not None:
            lines.append(f"failed_step: {failed_step.name} ({failed_step.error})")
        lines.append(f"trace: {run.trace_path or context.trace_path or '-'}")
        lines.append(f"screenshots: {', '.join(run.screenshots) or '-'}")
        lines.append(f"bundle: {context.failure_bundle or '-'}")
        return "\n".join(lines)

    @staticmethod
    def _case_stdout(run: TargetRunResult) -> str:
        metrics = run.network_metrics or {}
        payload = {
            "steps": [{"name": step.name, "status": step.status, "duration_ms": step.duration_ms} for step in run.steps],
            "fill": (run.fill_report.to_dict() if run.fill_report else None),
            "verification": (run.verification.to_dict() if run.verification else None),
            "network": {
                "requests": metrics.get("requests"),
                "responses": metrics.get("responses"),
                "failures": metrics.get("failures"),
                "bytes_in": metrics.get("bytes_in"),
                "api_endpoints": metrics.get("api_endpoints"),
            },
            "api_endpoints": run.api_endpoints,
        }
        return json_dumps(payload, indent=2)

    # ------------------------------------------------------------------ Markdown
    def _write_markdown(self, summary: RunSummary) -> Optional[str]:
        try:
            path = self.root / "summary.md"
            path.write_text(render_markdown(summary), encoding="utf-8")
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not write summary.md: %s", exc)
            return None

    # ------------------------------------------------------------------ console
    def print_summary(self, summary: RunSummary) -> None:
        """Print the end-of-run summary (rich if available, plain text otherwise)."""
        totals = summary.totals
        status_word = "PASSED ✅" if summary.exit_code == 0 else "FAILED ❌"
        headline = (
            f"WAFT run {summary.run_id} {status_word} | "
            f"{totals.targets_ok}/{totals.targets_run} target(s) ok ({totals.success_rate}%) | "
            f"{summary.duration_ms / 1000:.1f}s | artifacts: {summary.artifacts_dir}"
        )

        if self._rich is not None and self._rich_table is not None and not self.config.no_color:
            console = self._rich()
            table = self._rich_table(title="WAFT run summary", show_lines=False, header_style="bold cyan")
            table.add_column("Metric", style="bold")
            table.add_column("Value", justify="right")
            table.add_column("Metric", style="bold")
            table.add_column("Value", justify="right")
            left = [
                ("Contexts", f"{totals.contexts_ok} ok / {totals.contexts_failed} failed"),
                ("Targets run", str(totals.targets_run)),
                ("Targets ok", str(totals.targets_ok)),
                ("Targets failed", str(totals.targets_failed)),
                ("Steps ok/failed", f"{totals.steps_ok} / {totals.steps_failed}"),
            ]
            right = [
                ("Retries", str(self.config.config_digest.get("retries", 0))),
                ("E-mail verifications", f"{totals.verifications_ok} ok / {totals.verifications_failed} failed"),
                ("API endpoints", str(len(summary.api_endpoints))),
                ("HTTP status mix", ", ".join(f"{k}:{v}" for k, v in list((self.config.config_digest.get("http_status_mix") or {}).items())[:5])),
                ("Duration", human_ms(summary.duration_ms)),
            ]
            for index in range(max(len(left), len(right))):
                row = []
                row.extend(left[index] if index < len(left) else ("", ""))
                row.extend(right[index] if index < len(right) else ("", ""))
                table.add_row(*[str(cell) for cell in row])
            console.print(table)
            console.print(f"[bold]{html.escape(headline)}[/bold]")
            if summary.failures:
                failures = self._rich_table(title=f"Failures ({len(summary.failures)})", show_lines=False)
                failures.add_column("Context")
                failures.add_column("Row")
                failures.add_column("URL", overflow="fold")
                failures.add_column("Error", overflow="fold")
                for failure in summary.failures[:20]:
                    failures.add_row(
                        str(failure.get("context_id")),
                        str(failure.get("row_index")),
                        truncate(str(failure.get("url")), 60),
                        truncate(str(failure.get("error")), 90),
                    )
                console.print(failures)
            return

        print("")
        print("=" * 100)
        print(headline)
        print("=" * 100)
        print(f"  contexts          : {totals.contexts_ok} ok / {totals.contexts_failed} failed (of {totals.contexts})")
        print(f"  targets           : {totals.targets_run} run, {totals.targets_ok} ok, {totals.targets_failed} failed, {totals.success_rate}% success")
        print(f"  steps             : {totals.steps_ok} ok / {totals.steps_failed} failed")
        print(f"  retries           : {self.config.config_digest.get('retries', 0)}")
        print(f"  e-mail verif.     : {totals.verifications_ok} ok / {totals.verifications_failed} failed")
        print(f"  api endpoints     : {len(summary.api_endpoints)}")
        print(f"  duration          : {human_ms(summary.duration_ms)}")
        print(f"  artifacts         : {summary.artifacts_dir}")
        if summary.failures:
            print("-" * 100)
            print("  first failures:")
            for failure in summary.failures[:10]:
                print(
                    f"    • [{failure.get('context_id')}] row {failure.get('row_index')} "
                    f"{truncate(str(failure.get('url')), 60)} → {truncate(str(failure.get('error')), 90)}"
                )
        print("=" * 100)


# --------------------------------------------------------------------------------------
# Renderers (also used by tests and the Markdown report)
# --------------------------------------------------------------------------------------


def render_summary_table(summary: RunSummary) -> str:
    """Plain-text table of per-context results (used in Markdown/console)."""
    lines = [
        f"{'context':<14} {'status':<8} {'device':<22} {'targets':>7} {'ok':>5} {'fail':>5} {'duration':>10}",
        "-" * 90,
    ]
    for context in summary.contexts:
        lines.append(
            f"{context.context_id:<14} {context.status:<8} {(context.device or '-'):<22} "
            f"{len(context.runs):>7} {sum(1 for r in context.runs if r.ok):>5} "
            f"{sum(1 for r in context.runs if not r.ok):>5} {human_ms(context.duration_ms):>10}"
        )
    return "\n".join(lines)


def render_markdown(summary: RunSummary) -> str:
    """Markdown report - handy for pull requests, tickets and CI artifacts."""
    totals = summary.totals
    status = "✅ PASSED" if summary.exit_code == 0 else "❌ FAILED"
    buffer = io.StringIO()
    buffer.write(f"# WAFT run report — {summary.run_id}\n\n")
    buffer.write(f"**Status:** {status}  \n")
    buffer.write(f"**Started:** {summary.started_at}  \n")
    buffer.write(f"**Finished:** {summary.finished_at}  \n")
    buffer.write(f"**Duration:** {human_ms(summary.duration_ms)}  \n")
    buffer.write(f"**Artifacts:** `{summary.artifacts_dir}`  \n")
    buffer.write(f"**Data source:** `{summary.data_source}`  \n")
    buffer.write(f"**Proxy source:** `{summary.proxy_source or 'none'}`  \n")
    buffer.write(f"**Browser:** {summary.browser} (headless={summary.headless}), concurrency {summary.concurrency}  \n\n")

    buffer.write("## Totals\n\n")
    buffer.write("| Metric | Value |\n|---|---|\n")
    for label, value in (
        ("Contexts", f"{totals.contexts_ok} ok / {totals.contexts_failed} failed / {totals.contexts} created"),
        ("Targets run", totals.targets_run),
        ("Targets ok", totals.targets_ok),
        ("Targets failed", totals.targets_failed),
        ("Success rate", f"{totals.success_rate}%"),
        ("Steps ok / failed", f"{totals.steps_ok} / {totals.steps_failed}"),
        ("E-mail verifications", f"{totals.verifications_ok} ok / {totals.verifications_failed} failed"),
        ("API endpoints discovered", len(summary.api_endpoints)),
        ("Retries", summary.config_digest.get("retries", 0)),
    ):
        buffer.write(f"| {label} | {value} |\n")

    buffer.write("\n## Contexts\n\n")
    buffer.write("| Context | Status | Device | Locale | Timezone | Proxy | Targets | ok/fail | Duration |\n")
    buffer.write("|---|---|---|---|---|---|---|---|---|\n")
    for context in summary.contexts:
        ok = sum(1 for run in context.runs if run.ok)
        failed = len(context.runs) - ok
        buffer.write(
            f"| {context.context_id} | {context.status} | {context.device or '-'} | {context.locale or '-'} | "
            f"{context.timezone_id or '-'} | {context.proxy or 'direct'} | {len(context.runs)} | {ok}/{failed} | "
            f"{human_ms(context.duration_ms)} |\n"
        )

    if summary.failures:
        buffer.write(f"\n## Failures ({len(summary.failures)})\n\n")
        buffer.write("| Context | Row | URL | Error | Trace | Bundle |\n|---|---|---|---|---|---|\n")
        for failure in summary.failures[:50]:
            buffer.write(
                f"| {failure.get('context_id')} | {failure.get('row_index')} | "
                f"`{truncate(str(failure.get('url')), 70)}` | {truncate(str(failure.get('error')), 120)} | "
                f"{Path(str(failure.get('trace'))).name if failure.get('trace') else '-'} | "
                f"{Path(str(failure.get('bundle'))).name if failure.get('bundle') else '-'} |\n"
            )

    if summary.api_endpoints:
        buffer.write(f"\n## API endpoints discovered ({len(summary.api_endpoints)})\n\n```\n")
        for endpoint in summary.api_endpoints[:120]:
            buffer.write(f"{endpoint}\n")
        buffer.write("```\n")

    status_mix = summary.config_digest.get("http_status_mix") or {}
    if status_mix:
        buffer.write("\n## HTTP status mix\n\n| Status | Count |\n|---|---|\n")
        for status_code, count in sorted(status_mix.items(), key=lambda item: str(item[0])):
            buffer.write(f"| {status_code} | {count} |\n")

    proxy_health = summary.config_digest.get("proxy_health") or []
    if proxy_health:
        buffer.write("\n## Proxy health\n\n| Proxy | OK | Latency | Exit IP | Error |\n|---|---|---|---|---|\n")
        for health in proxy_health:
            buffer.write(
                f"| `{health.get('proxy')}` | {'✅' if health.get('ok') else '❌'} | "
                f"{health.get('latency_ms') or '-'} ms | {health.get('exit_ip') or '-'} | "
                f"{truncate(str(health.get('error') or ''), 80) or '-'} |\n"
            )

    buffer.write("\n## Configuration digest\n\n```json\n")
    buffer.write(json_dumps({k: v for k, v in summary.config_digest.items() if k != "load_report"}, indent=2))
    buffer.write("\n```\n")
    return buffer.getvalue()


def summary_as_dict(summary: RunSummary) -> dict[str, Any]:
    """Helper for embedding the summary in other systems (Slack, e-mail, PR comments)."""
    return summary.to_dict(with_contexts=False)


def console_line_for(result: TargetRunResult) -> str:
    """One-line console rendering of a target result (used by custom hooks)."""
    icon = "✔" if result.ok else "✘"
    return (
        f"{icon} [{result.context_id}] row {result.row_index} {truncate(result.target_url, 70)} "
        f"({human_ms(result.duration_ms)}) {truncate(result.error or '', 90)}"
    )


__all__ += ["summary_as_dict", "console_line_for"]
