#!/usr/bin/env python3
"""``run_offerwall.py`` - account-pinned load-test / regression driver for a sign-up + survey flow.

What it does, in order
----------------------
1. **credentials.json** is read with :mod:`account_pool` (typed, de-duplicated, masked logging).
2. **targets_offerwall.xlsx** is loaded through WAFT's own data loader; every row keeps the
   ``{email}`` / ``{password}`` / ``{verification_email}`` placeholders.
3. **One account per context**: context *i* gets ``pool.accounts[i % len(pool)]`` and runs its own
   copy of the row set with the placeholders resolved - so 10 contexts + 10 accounts is a 1:1
   mapping and no account is used by two contexts at the same time.
4. **WAFT Config** is built from the requested profile: ``contexts=10``, ``concurrency=5``,
   ``stealth``, ``verify_stealth``, ``proxy_file='proxies.txt'``, ``imap`` with a TR/EN subject
   regex and 180 s timeout, ``capture_har``, ``log_network``, ``captcha_action='skip'``,
   ``trace='on-failure'``, ``artifacts='artifacts'``, ``retries=2``, ``rate_limit=2.0``.
5. ``AccountPinnedOrchestrator`` (a thin ``Orchestrator`` subclass) runs the contexts; its exit
   code is returned unchanged.
6. **endpoints.json** (plus ``run.json``) is read back and summarised on the terminal: discovered
   API endpoints, status codes, request counts, CAPTCHA-blocked targets, verification results.

Scope guard
-----------
The target URLs must belong to you or to an environment you are authorised to test (staging,
pre-prod, partner sandbox, local mock). ``run_regression.py``'s scope file/block-list is reused
here: third-party offerwall / micro-task / reward platforms (timewall.io, jumptask.io, …) are
rejected outright - automated sign-ups there are abuse, not load testing.

Usage
-----
Local end-to-end against the bundled sandbox (see qa-kit/README.md)::

    python qa-kit/offerwall/run_offerwall.py \
        --credentials qa-kit/offerwall/credentials.sandbox.json \
        --targets qa-kit/offerwall/targets_offerwall.xlsx \
        --orderwall-sandbox-off  # (example flags below)

    python qa-kit/offerwall/run_offerwall.py \
        --credentials qa-kit/offerwall/credentials.sandbox.json \
        --targets qa-kit/offerwall/targets_offerwall.xlsx \
        --imap-host 127.0.0.1 --imap-port 1430 --imap-ssl off \
        --proxy-mode off --contexts 10 --concurrency 5

Your own staging + Gmail app passwords::

    python qa-kit/offerwall/run_offerwall.py \
        --credentials credentials.json \
        --targets qa-kit/offerwall/targets_offerwall.xlsx \
        --base-url https://staging.sirketiniz.com --proxy-mode auto

Exit codes: 0 pass | 1 target failures | 2 usage/scope/credential error | 3 proxy | 130 interrupt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Final, Optional, Sequence

# --- make the package + sibling modules importable when run as a plain script --------------
_HERE: Final[Path] = Path(__file__).resolve().parent
_REPO: Final[Path] = _HERE.parent.parent
for _path in (str(_REPO), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from account_pool import Account, AccountPool, AccountPoolError  # noqa: E402  (local sibling)

from waft.config import Config  # noqa: E402
from waft.data_source import DataLoader  # noqa: E402
from waft.models import (  # noqa: E402
    STATUS_FAILED,
    STATUS_OK,
    ContextResult,
    TargetRow,
)
from waft.orchestrator import Orchestrator  # noqa: E402
from waft.utils import human_ms, mask_secret, truncate  # noqa: E402
from waft.engine import ContextEngine, ContextEngineFactory  # noqa: E402

logger = logging.getLogger("waft.offerwall")

#: Fields in the sheet that hold account placeholders, and how they are substituted.
PLACEHOLDER_FIELDS: Final[tuple[str, ...]] = ("email", "password", "verification_email")

DEFAULT_SUBJECT_REGEX: Final[str] = r"(doğrula|dogrula|verify|confirm|aktivasyon|activate)"


# ======================================================================================
# placeholder resolution
# ======================================================================================
def resolve_account_placeholders(text: Optional[str], account: Account) -> Optional[str]:
    """Replace ``{email}`` / ``{password}`` / ``{account_email}`` in *text* with account values."""
    if text is None:
        return None
    replacements = {
        "{email}": account.email,
        "{account_email}": account.email,
        "{password}": account.password,
        "{account_password}": account.password,
        "{{email}}": account.email,
        "{{password}}": account.password,
    }
    result = text
    for token, value in replacements.items():
        result = result.replace(token, value)
    return result


def bind_rows_to_account(template_rows: Sequence[TargetRow], account: Account, *, iteration_offset: int) -> list[TargetRow]:
    """Return a copy of *template_rows* with every account placeholder resolved.

    ``TargetRow`` is a dataclass, so :func:`dataclasses.replace` keeps every field we do not
    touch (selectors, steps, timeouts, tags …) exactly as the sheet defined it.  ``index`` is
    shifted per account so rows stay unique inside ``results.csv``/``run.json``.
    """
    bound: list[TargetRow] = []
    for position, row in enumerate(template_rows):
        form_data = {
            key: resolve_account_placeholders(str(value), account) if isinstance(value, str) else value
            for key, value in row.form_data.items()
        }
        wants_verification = bool(row.requires_email_verification) and row.scenario == "email-verify"
        bound.append(
            replace(
                row,
                index=iteration_offset + position + 1,
                form_data=form_data,
                # Defensive: a stray verification_email on a non-verification row would make WAFT
                # poll IMAP for a mail that never arrives.
                verification_email=(
                    resolve_account_placeholders(row.verification_email, account) if wants_verification else None
                ),
                verification_subject_regex=row.verification_subject_regex if wants_verification else None,
                name=(row.name or f"row-{row.index}") + f"@ctx-account-{account.email.split('@')[0]}",
                metadata={
                    **row.metadata,
                    "account_email": account.email,
                    "account_note": account.note,
                    "template_row_index": row.index,
                },
            )
        )
    return bound


# ======================================================================================
# orchestrator with per-context account pinning
# ======================================================================================
class AccountPinnedOrchestrator(Orchestrator):
    """``Orchestrator`` that gives every context **its own** account-bound row list.

    WAFT's stock behaviour runs the full row set inside *every* context.  For an account-pool
    test we need the opposite: context *i* must drive account *i* only.  The override below
    swaps ``engine.run_rows(self.rows)`` for ``engine.run_rows(self._rows_by_context[index])``
    and falls back to the stock behaviour (with a loud warning) if the parent signature ever
    changes - so an upgrade cannot silently corrupt the load profile.
    """

    VERSION: Final[str] = "1.0.0"

    def __init__(
        self,
        config: Config,
        *,
        pool: AccountPool,
        rows_by_context: Sequence[Sequence[TargetRow]],
    ) -> None:
        super().__init__(config)
        self.pool = pool
        self.rows_by_context: list[list[TargetRow]] = [list(rows) for rows in rows_by_context]
        self.account_binding_ok: bool = True
        self._checked_parent_signature: bool = False

    # ------------------------------------------------------------------ helpers
    def rows_for(self, index: int) -> list[TargetRow]:
        """Rows bound to context *index* (falls back to the global row set)."""
        if not self.rows_by_context:
            return list(self.rows)
        return self.rows_by_context[index % len(self.rows_by_context)]

    def prepare(self) -> None:  # type: ignore[override]
        """Run the stock preparation, then expose the *flattened* plan for reporting."""
        super().prepare()
        flat: list[TargetRow] = [row for rows in self.rows_by_context for row in rows]
        if flat:
            # Reporting/totals must reflect what is really executed (contexts x rows), not the
            # template sheet.
            self.rows = flat
        logger.info(
            "account pinning: %d context(s) x %d account(s) x %d template row(s) = %d planned target(s)",
            max(len(self.rows_by_context), 1),
            len(self.pool),
            len(self.rows_by_context[0]) if self.rows_by_context else 0,
            len(flat) or len(self.rows),
        )

    # ------------------------------------------------------------------ override
    async def _run_context(  # type: ignore[override]
        self,
        index: int,
        engine_factory: ContextEngineFactory,
        semaphore: asyncio.Semaphore,
    ) -> ContextResult:
        """Copy of the parent routine with the per-context row list injected."""
        import inspect

        parent = super()._run_context
        if not self._checked_parent_signature:
            self._checked_parent_signature = True
            try:
                parameters = list(inspect.signature(parent).parameters)
                if parameters[:3] != ["index", "engine_factory", "semaphore"]:
                    self.account_binding_ok = False
                    logger.error(
                        "Orchestrator._run_context signature changed (%s) - falling back to shared rows",
                        parameters,
                    )
            except (TypeError, ValueError):  # pragma: no cover - introspectable in practice
                self.account_binding_ok = False
                logger.error("could not introspect Orchestrator._run_context - falling back to shared rows")

        if not self.account_binding_ok:
            return await parent(index, engine_factory, semaphore)

        profile = self.profiles[index]
        rows = self.rows_for(index)
        account = self.pool.for_context(index) if len(self.pool) else None
        async with semaphore:
            context_result: Optional[ContextResult] = None
            engine: Optional[ContextEngine] = None
            from waft.utils import Stopwatch, now_iso  # local import: mirrors the parent

            stopwatch = Stopwatch()
            stopwatch.__enter__()
            started_iso = now_iso()
            try:
                if account is not None:
                    logger.info(
                        "[%s] account pinned: %s (app password %s) - %d row(s)",
                        profile.context_id,
                        account.email,
                        account.masked_password,
                        len(rows),
                    )
                proxy = await self.proxy_pool.acquire(context_id=profile.context_id) if self.proxy_pool else None
                engine = await engine_factory.create(profile, proxy)
                self._engines.append(engine)
                await engine.start()
                if account is not None:
                    engine.comment = f"account:{account.email}"  # type: ignore[attr-defined]
                await engine.run_rows(rows)
                failed_runs = [run for run in engine.runs if not run.ok]
                blocked = [
                    run for run in engine.runs
                    if "captcha" in (run.error or "").lower() or run.metadata.get("captcha_expected")
                ]
                if blocked:
                    logger.warning(
                        "[%s] %d target(s) blocked_captcha (policy=%s) - continuing with the rest",
                        profile.context_id,
                        len(blocked),
                        self.config.captcha_action,
                    )
                context_result = engine.build_context_result(
                    status=STATUS_FAILED if failed_runs else STATUS_OK,
                    error=(f"{len(failed_runs)} of {len(engine.runs)} target(s) failed" if failed_runs else None),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one context must never kill the run
                logger.error(
                    "[%s] context failed: %s: %s", profile.context_id, type(exc).__name__, truncate(str(exc), 300)
                )
                if engine is not None:
                    context_result = engine.build_context_result(
                        status=STATUS_FAILED, error=f"{type(exc).__name__}: {exc}"
                    )
                    context_result.error_type = type(exc).__name__
            finally:
                if engine is not None:
                    try:
                        failed = context_result is None or not context_result.ok
                        trace_path = await engine.close(failed=failed)
                        if context_result is not None and trace_path:
                            context_result.trace_path = trace_path
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("[%s] engine shutdown issue: %s", profile.context_id, exc)
                stopwatch.stop()

            if context_result is None:
                context_result = ContextResult(
                    context_id=profile.context_id, index=index, status=STATUS_FAILED,
                    error="context produced no result",
                )
            context_result.started_at = started_iso
            context_result.duration_ms = round(stopwatch.elapsed_ms, 1)
            context_result.finished_at = context_result.finished_at or now_iso()

            if context_result.ok:
                self.stats.contexts_ok += 1
            else:
                self.stats.contexts_failed += 1
            self._context_results.append(context_result)

            if self.artifacts is not None:
                try:
                    self.artifacts.write_context_summary(context_result)
                    if not context_result.ok:
                        context_result.failure_bundle = self.artifacts.create_failure_bundle(context_result)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[%s] artifact finalisation failed: %s", profile.context_id, exc)

            logger.info(
                "[%s] finished: %d target(s), %d ok, %d failed in %s",
                profile.context_id,
                len(context_result.runs),
                sum(1 for run in context_result.runs if run.ok),
                sum(1 for run in context_result.runs if not run.ok),
                human_ms(context_result.duration_ms),
            )
            return context_result


# ======================================================================================
# config construction
# ======================================================================================
def build_config(args: argparse.Namespace, pool: AccountPool) -> Config:
    """Build and validate the WAFT :class:`Config` for the offerwall profile."""
    imap_host, imap_port = pool.first_imap_settings()
    config = Config(
        data_file=Path(args.targets),
        contexts=args.contexts,
        concurrency=args.concurrency,
        scenario="auto",
        # --- stealth ------------------------------------------------------------------
        stealth=True,
        verify_stealth=True,
        humanize=not args.no_humanize,
        # --- proxy --------------------------------------------------------------------
        proxy_file=Path(args.proxies) if args.proxies and args.proxy_mode != "off" else None,
        proxy_mode=args.proxy_mode,
        # --- network ------------------------------------------------------------------
        capture_har=True,
        log_network=True,
        # --- forms / captcha ----------------------------------------------------------
        captcha_action="skip",
        selectors_file=Path(args.selectors) if args.selectors and Path(args.selectors).exists() else None,
        # --- IMAP verification --------------------------------------------------------
        imap_enabled=bool(args.imap_host or args.imap_ssl == "off"),
        imap_host=args.imap_host or imap_host,
        imap_port=int(args.imap_port or imap_port),
        imap_ssl=(args.imap_ssl != "off"),
        imap_username=args.imap_user or pool.for_context(0).email,
        imap_password=args.imap_password or pool.for_context(0).password,
        imap_subject_regex=args.imap_subject_regex,
        imap_timeout_s=float(args.imap_timeout),
        # --- artifacts / retries ------------------------------------------------------
        artifacts_dir=Path(args.artifacts),
        trace_mode="on-failure",
        retries=args.retries,
        retry_only_retryable=False,  # keep retrying deterministic verification failures too
        rate_limit=args.rate_limit,
        save_storage_state=True,
        log_level=args.log_level,
    )
    config.validate()
    return config


# ======================================================================================
# results summary
# ======================================================================================
def summarise_endpoints(artifacts_dir: Path, run_id: Optional[str] = None) -> dict[str, Any]:
    """Read ``endpoints.json`` (and ``run.json``) and print a terminal summary.

    Returns a dictionary so callers/tests can assert on it.  Never raises: a missing or
    unreadable file yields ``{"available": False, "reason": …}``.
    """
    summary: dict[str, Any] = {"available": False, "run_id": run_id}
    try:
        candidates = sorted(
            (path for path in artifacts_dir.glob("run-*/endpoints.json") if path.parent.is_dir()),
            key=lambda path: path.stat().st_mtime,
        )
        if not candidates:
            summary["reason"] = f"no endpoints.json under {artifacts_dir}"
            return summary
        endpoints_path = candidates[-1]
        payload = json.loads(endpoints_path.read_text(encoding="utf-8"))
        run_dir = endpoints_path.parent
        if run_id and run_dir.name != run_id:
            # Fall back to the requested run directory when several runs are present.
            explicit = run_dir.parent / run_id / "endpoints.json"
            if explicit.exists():
                endpoints_path, payload, run_dir = explicit, json.loads(explicit.read_text(encoding="utf-8")), explicit.parent

        summary.update({"available": True, "run_id": run_dir.name, "path": str(endpoints_path)})
        endpoints = payload.get("endpoints") or []
        summary["endpoint_count"] = len(endpoints)
        summary["endpoints"] = [
            {
                "method": item.get("method"),
                "host": item.get("host"),
                "path": item.get("path"),
                "statuses": item.get("statuses"),
                "count": item.get("count"),
                "query_keys": item.get("query_keys"),
            }
            for item in endpoints[:25]
        ]

        run_json = run_dir / "run.json"
        if run_json.exists():
            run_payload = json.loads(run_json.read_text(encoding="utf-8"))
            totals = run_payload.get("totals") or {}
            summary["totals"] = totals
            contexts = run_payload.get("contexts") or []
            blocked: list[dict[str, Any]] = []
            verified_ok = verified_failed = 0
            for context in contexts:
                for run in context.get("runs") or []:
                    error = str(run.get("error") or "")
                    if "captcha" in error.lower():
                        blocked.append(
                            {
                                "context": context.get("context_id"),
                                "target": run.get("target_url"),
                                "status": run.get("status"),
                                "error": truncate(error, 120),
                            }
                        )
                    verification = run.get("verification") or {}
                    if verification:
                        if verification.get("success"):
                            verified_ok += 1
                        else:
                            verified_failed += 1
            summary["captcha_blocked"] = blocked
            summary["verifications_ok"] = verified_ok
            summary["verifications_failed"] = verified_failed
        return summary
    except Exception as exc:  # noqa: BLE001 - reporting must never break the exit code
        summary["reason"] = f"{type(exc).__name__}: {exc}"
        return summary


def print_endpoint_summary(summary: dict[str, Any]) -> None:
    """Pretty-print :func:`summarise_endpoints` output."""
    print("\n" + "=" * 96)
    if not summary.get("available"):
        print(f"API endpoint özeti yok: {summary.get('reason')}")
        print("=" * 96)
        return
    print(f"API endpoint özeti — {summary.get('run_id')}")
    print(f"  dosya            : {summary.get('path')}")
    print(f"  keşfedilen uç nokta: {summary.get('endpoint_count', 0)}")
    for item in summary.get("endpoints", []):
        statuses = ",".join(str(code) for code in (item.get("statuses") or []))
        print(
            f"    {str(item.get('method')):6s} {item.get('host')}{item.get('path')} "
            f"[status {statuses or '?'}, {item.get('count')} kez]"
        )
    totals = summary.get("totals") or {}
    if totals:
        print(
            f"  hedefler         : {totals.get('targets_run', 0)} koşu, {totals.get('targets_ok', 0)} ok, "
            f"{totals.get('targets_failed', 0)} fail (%{totals.get('success_rate_pct', 0)})"
        )
    print(f"  e-posta doğrulama: {summary.get('verifications_ok', 0)} ok / {summary.get('verifications_failed', 0)} fail")
    blocked = summary.get("captcha_blocked") or []
    print(f"  blocked_captcha  : {len(blocked)} hedef")
    for item in blocked[:10]:
        print(f"    • [{item.get('context')}] {item.get('target')} → {item.get('error')}")
    print("=" * 96)


# ======================================================================================
# CLI
# ======================================================================================
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_offerwall.py",
        description=(
            "Offerwall/survey sign-up flow: account-pinned load & regression harness "
            "(owned/authorised targets only)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--credentials", type=Path, default=_HERE / "credentials.json",
                        help="Account pool: credentials.json (or email:password text file).")
    parser.add_argument("--targets", type=Path, default=_HERE / "targets_offerwall.xlsx",
                        help="Target sheet with {email}/{password} placeholders.")
    parser.add_argument("--base-url", default=None,
                        help="Rewrite every target_url to this host (staging <-> sandbox switch).")
    parser.add_argument("--contexts", type=int, default=10, help="Isolated browser contexts (= accounts).")
    parser.add_argument("--concurrency", type=int, default=5, help="Contexts running in parallel.")
    parser.add_argument("--rate-limit", type=float, default=2.0, help="Global navigations/second cap.")
    parser.add_argument("--retries", type=int, default=2, help="Retry attempts per target.")
    parser.add_argument("--proxies", type=Path, default=_REPO / "proxies.txt", help="Proxy list file.")
    parser.add_argument("--proxy-mode", default="auto", choices=["auto", "require", "off"], help="Proxy policy.")
    parser.add_argument("--selectors", type=Path, default=_HERE / "selectors.resolved.json",
                        help="Resolved selector map (produced by selector_resolver.py).")
    parser.add_argument("--imap-host", default=None, help="IMAP host (default: first account's imap_host).")
    parser.add_argument("--imap-port", type=int, default=None, help="IMAP port (default: first account's imap_port).")
    parser.add_argument("--imap-user", default=None, help="IMAP user (default: first account's e-mail).")
    parser.add_argument("--imap-password", default=None, help="IMAP password (default: first account's app password).")
    parser.add_argument("--imap-ssl", choices=["on", "off"], default="on", help="Implicit TLS (993) or plain IMAP.")
    parser.add_argument("--imap-subject-regex", default=DEFAULT_SUBJECT_REGEX, help="Verification mail subject filter.")
    parser.add_argument("--imap-timeout", type=float, default=180.0, help="Seconds to wait for each mail.")
    parser.add_argument("--artifacts", type=Path, default=_REPO / "artifacts", help="Artifacts root.")
    parser.add_argument("--log-level", default="INFO", help="Console log level.")
    parser.add_argument("--no-humanize", action="store_true", help="Disable human-like typing (faster).")
    parser.add_argument("--dry-run", action="store_true", help="Plan only: prepare, print, exit.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colours.")
    return parser.parse_args(argv)


def configure_logging(level: str, *, color: bool) -> None:
    """Console + file logging for the wrapper itself (WAFT configures its own on top)."""
    try:
        from waft.logging_setup import setup_logging

        setup_logging(level=level, color=color)
        return
    except Exception:  # noqa: BLE001 - fall back to stdlib
        logging.basicConfig(level=getattr(logging, str(level).upper(), logging.INFO),
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def load_template_rows(config: Config) -> list[TargetRow]:
    """Load the target sheet through WAFT's loader (placeholders stay untouched)."""
    loader = DataLoader(config)
    rows = loader.load()
    if not rows:
        raise SystemExit(f"✖ {config.data_file} produced zero runnable rows")
    placeholders = [
        row.name or f"row-{row.index}"
        for row in rows
        if any(str(row.form_data.get(field, "")).startswith("{") for field in PLACEHOLDER_FIELDS)
    ]
    logger.info(
        "target sheet: %d row(s), %d with account placeholders (%s)",
        len(rows),
        len(placeholders),
        ", ".join(placeholders[:6]) or "none",
    )
    return rows


def rewrite_base_url(args: argparse.Namespace) -> Optional[Path]:
    """Point every row at *args.base_url* by rewriting the sheet into the artifacts directory."""
    if not args.base_url:
        return None
    from urllib.parse import urlsplit

    import pandas as pd

    base = str(args.base_url).rstrip("/")
    sheets = pd.read_excel(args.targets, sheet_name=None)
    out = Path(args.artifacts) / "targets_offerwall.base_url.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for sheet_name, sheet in sheets.items():
            if "target_url" in sheet.columns:
                sheet = sheet.copy()
                sheet["target_url"] = [
                    base + (urlsplit(str(value)).path or "/") if str(value).startswith("http") else f"{base}/{value}"
                    for value in sheet["target_url"]
                ]
            sheet.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    logger.info("target URLs rewritten to %s → %s", base, out)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level, color=not args.no_color)

    # --- 1) account pool -----------------------------------------------------------------
    try:
        pool = AccountPool.from_file(args.credentials)
        warnings = pool.validate(require_enabled=True)
    except AccountPoolError as exc:
        print(f"✖ credential problem: {exc}", file=sys.stderr)
        return 2
    print(pool.describe())
    for warning in warnings:
        print(f"⚠ {warning}")
    if not pool.enabled():
        print("✖ no enabled accounts in the pool", file=sys.stderr)
        return 2

    rewritten = rewrite_base_url(args)
    if rewritten is not None:
        args.targets = rewritten

    # --- 2) config + template rows -------------------------------------------------------
    try:
        config = build_config(args, pool)
        if args.dry_run:
            config.dry_run = True
        template_rows = load_template_rows(config)
    except Exception as exc:  # noqa: BLE001 - configuration/data problems are usage errors
        print(f"✖ configuration error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    # --- 3) bind rows to accounts --------------------------------------------------------
    rows_by_context: list[list[TargetRow]] = []
    offset = 0
    for index in range(config.contexts):
        account = pool.for_context(index)
        bound = bind_rows_to_account(template_rows, account, iteration_offset=offset)
        offset += len(template_rows)
        rows_by_context.append(bound)

    orchestrator = AccountPinnedOrchestrator(config, pool=pool, rows_by_context=rows_by_context)
    print(
        f"→ {config.contexts} context(s) / {config.concurrency} parallel | "
        f"{len(template_rows)} template row(s) x {len(pool)} account(s) = "
        f"{sum(len(rows) for rows in rows_by_context)} target run(s) | "
        f"proxy={args.proxy_mode} | imap={'on' if config.imap_enabled else 'off'} | captcha=skip"
    )
    print(f"→ run id: {orchestrator.run_id} | artifacts: {config.artifacts_dir}")

    # --- 4) run --------------------------------------------------------------------------
    try:
        exit_code = int(asyncio.run(orchestrator.run()))
    except KeyboardInterrupt:
        print("\n✖ interrupted - partial artifacts were kept", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort guard, always leaves a message
        logger.exception("orchestrator crashed: %s", exc)
        return 1

    # --- 5) endpoint summary -------------------------------------------------------------
    summary = summarise_endpoints(Path(config.artifacts_dir), run_id=orchestrator.run_id)
    print_endpoint_summary(summary)

    # --- 6) manifest (reproducibility) ---------------------------------------------------
    try:
        manifest_path = Path(config.artifacts_dir) / "offerwall_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "run_id": orchestrator.run_id,
                    "exit_code": exit_code,
                    "credentials_file": str(args.credentials),
                    "accounts": [account.to_dict(mask=True) for account in pool],
                    "targets_file": str(args.targets),
                    "contexts": config.contexts,
                    "concurrency": config.concurrency,
                    "rate_limit": config.rate_limit,
                    "retries": config.retries,
                    "proxy_mode": args.proxy_mode,
                    "imap": {
                        "enabled": config.imap_enabled,
                        "host": config.imap_host,
                        "port": config.imap_port,
                        "user": config.imap_username,
                        "password": mask_secret(config.imap_password or ""),
                        "subject_regex": config.imap_subject_regex,
                        "timeout_s": config.imap_timeout_s,
                    },
                    "captcha_action": config.captcha_action,
                    "har": config.capture_har,
                    "network_log": config.log_network,
                    "stealth": config.stealth,
                    "verify_stealth": config.verify_stealth,
                    "account_binding": orchestrator.account_binding_ok,
                    "endpoint_summary": {key: value for key, value in summary.items() if key != "endpoints"},
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"→ manifest: {manifest_path}")
    except Exception as exc:  # noqa: BLE001 - manifest is nice-to-have
        logger.warning("manifest could not be written: %s", exc)

    print(f"→ exit code: {exit_code} ({'PASSED' if exit_code == 0 else 'see artifacts'})")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
