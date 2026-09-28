#!/usr/bin/env python3
"""``selector_resolver.py`` - turn the fallback catalogue into a WAFT-native selector map.

WAFT accepts explicit field overrides through ``--selectors <flat.json>``
(``{data_key: css_or_xpath}``, loaded into ``Config.selectors`` and merged into every
``TargetRow.selectors``).  Writing that file by hand is brittle: a selector that used to
work silently stops matching after a redesign.

This resolver removes the guess-work:

1. read the fallback chains from ``selectors.json`` (per ``host:port`` / host / ``*``),
2. open the target page once per host in a real Chromium context (headless by default),
3. probe each candidate in order - CSS **and** XPath - and keep the first one that matches
   a visible, editable element,
4. rewrite the winner into a canonical form WAFT can resolve against its field
   fingerprint (``#id``, ``[name='…']``, ``[id='…']``, ``[placeholder='…']``, ``[type='…']``),
5. emit ``selectors.resolved.json`` (feed it to ``--selectors``) plus a human-readable
   report that shows exactly which fallback won and which candidates are dead.

Typical use::

    python qa-kit/selector_resolver.py \
        --data qa-kit/targets.xlsx \
        --catalogue qa-kit/selectors.json \
        --out qa-kit/selectors.resolved.json \
        --report qa-kit/selectors.resolved.report.json

Exit codes: ``0`` every required field resolved, ``1`` at least one field unresolved,
``2`` usage/environment error (missing data file, no browsers, page unreachable).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Iterable, Mapping, Optional, Sequence

ROOT_DIR: Final[Path] = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:  # runnable as "python qa-kit/selector_resolver.py"
    sys.path.insert(0, str(ROOT_DIR))

try:
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright
except ImportError as exc:  # pragma: no cover - environment problem
    raise SystemExit(
        "playwright is required: pip install -r requirements.txt && python -m playwright install chromium"
    ) from exc

#: Fields that must resolve before the run is considered healthy (others are informational).
#: ``submit``/``success``/``error``/``captcha`` are not "fill" targets - they are used for
#: clicking and outcome detection, so a miss there is a warning, not a failure.
REQUIRED_FIELDS: Final[tuple[str, ...]] = ("email", "password")
INFORMATIONAL_FIELDS: Final[tuple[str, ...]] = ("submit", "success", "verified", "error", "captcha")

#: JS helper: canonical selector for an element, in the shape WAFT's resolver understands.
CANONICAL_JS: Final[str] = """
(element) => {
  const esc = (value) => String(value).replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'");
  const tag = (element.tagName || '').toLowerCase();
  if (element.id) return `#${element.id}`;
  const name = element.getAttribute('name');
  if (name) return `${tag}[name='${esc(name)}']`;
  const testId = element.getAttribute('data-testid');
  if (testId) return `[data-testid='${esc(testId)}']`;
  const placeholder = element.getAttribute('placeholder');
  if (placeholder) return `${tag}[placeholder='${esc(placeholder)}']`;
  const type = element.getAttribute('type');
  if (type) return `${tag}[type='${esc(type)}']`;
  const href = element.getAttribute('href');
  if (href) return `${tag}[href='${esc(href)}']`;
  return tag;
}
"""

#: JS helper: stamp the probed element so we can prove the canonical selector points at *it*.
#: Without this the round-trip check only proved "the canonical matches SOMETHING" - a candidate
#: for the second of two anonymous password inputs canonicalises to ``input[type=\'password\']``
#: and matched the FIRST input, i.e. the confirm field silently resolved to the password field.
MARK_JS: Final[str] = """
(element, token) => {
  element.setAttribute('data-waft-probe', token);
  return element.getAttribute('data-waft-probe');
}
"""

#: JS helper: does the element the canonical selector resolves to carry our token?
HAS_MARK_JS: Final[str] = """
(element, token) => element.getAttribute('data-waft-probe') === token
"""

#: JS helper: remove the probe stamp (never leave test residue in the page).
UNMARK_JS: Final[str] = """
(element) => { element.removeAttribute('data-waft-probe'); return true; }
"""

#: A canonical selector that is nothing but a tag name (``iframe``, ``button``) carries no
#: specificity: on a page with two iframes it resolves to the first one - measured: a captcha
#: chain ``iframe[src*='recaptcha']`` canonicalised to ``iframe`` and locked onto the ad frame.
BARE_TAG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9]*$")

#: Attribute-free XPath starts (``(//…``, ``//…``) - WAFT cannot consume XPath, so when the
#: canonical form is also bare there is nothing specific left to hand over.
X_PATH_RE: Final[re.Pattern[str]] = re.compile(r"^\(?//")


def prefer_specific(canonical: str, candidate: str) -> str:
    """Return a canonical selector that keeps *some* specificity.

    ``CANONICAL_JS`` falls back to the bare tag name when the element exposes no id/name/
    data-testid/placeholder/type/href. Handing a bare tag to WAFT means "first element of this
    tag on the page", which is a wrong-element bug waiting to happen. So:

    * canonical already specific → unchanged;
    * canonical bare + candidate is a CSS selector that pins something → use the candidate;
    * canonical bare + candidate is XPath → nothing specific survives, return the bare tag and
      let the caller reject it (reason: not specific enough).
    """
    canonical = (canonical or "").strip()
    candidate = (candidate or "").strip()
    if not BARE_TAG_RE.match(canonical):
        return canonical
    if candidate and not X_PATH_RE.match(candidate) and not BARE_TAG_RE.match(candidate):
        return candidate
    return canonical


#: JS helper: is the element visible and fillable/clickable?
EDITABLE_JS: Final[str] = """
(element) => {
  const style = window.getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  const visible = style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  const disabled = element.disabled === true || element.getAttribute('aria-disabled') === 'true';
  const readOnly = element.readOnly === true;
  return { visible, disabled, readOnly };
}
"""


# --------------------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------------------
@dataclass(slots=True)
class CandidateOutcome:
    """Result of probing one candidate selector for one field."""

    field_name: str
    selector: str
    matched: bool
    canonical: Optional[str] = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HostResolution:
    """Everything learned about one host during the probe."""

    host: str
    page_url: str
    resolved: dict[str, str] = field(default_factory=dict)
    outcomes: list[CandidateOutcome] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def required_missing(self) -> list[str]:
        return [name for name in self.missing if name in REQUIRED_FIELDS]

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "page_url": self.page_url,
            "resolved": self.resolved,
            "missing": self.missing,
            "required_missing": self.required_missing,
            "error": self.error,
            "candidates": [outcome.to_dict() for outcome in self.outcomes],
        }


# --------------------------------------------------------------------------------------
# catalogue handling
# --------------------------------------------------------------------------------------
@dataclass(slots=True)
class Catalogue:
    """Parsed ``selectors.json`` catalogue."""

    per_host: dict[str, dict[str, list[str]]]
    defaults: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Catalogue":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        hosts_raw: Mapping[str, Any] = raw.get("hosts") or {}
        per_host: dict[str, dict[str, list[str]]] = {}
        for host, fields in hosts_raw.items():
            clean: dict[str, list[str]] = {}
            for key, value in (fields or {}).items():
                if key.startswith("_"):
                    continue
                if isinstance(value, str):
                    clean[key] = [value]
                elif isinstance(value, Iterable):
                    clean[key] = [str(item) for item in value if str(item).strip()]
            per_host[str(host).lower()] = clean
        if not per_host:
            raise ValueError(f"{path}: no usable 'hosts' block found")
        return cls(per_host=per_host, defaults=dict(raw.get("defaults") or {}))

    def chains_for(self, host_port: str, host_only: str) -> dict[str, list[str]]:
        """Merge the most specific block with the generic ``*`` block (host wins)."""
        generic = dict(self.per_host.get("*", {}))
        for key in (host_only.lower(), host_port.lower()):
            block = self.per_host.get(key)
            if block:
                generic.update(block)
        return generic


# --------------------------------------------------------------------------------------
# probing
# --------------------------------------------------------------------------------------
async def probe_chain(
    page: Page,
    field_name: str,
    candidates: Sequence[str],
    *,
    probe_timeout_ms: int,
    require_visible: bool,
) -> tuple[Optional[str], list[CandidateOutcome]]:
    """Probe *candidates* in order; return the first canonical match plus every outcome."""
    outcomes: list[CandidateOutcome] = []
    for selector in candidates:
        outcome = CandidateOutcome(field_name=field_name, selector=selector, matched=False)
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                outcome.reason = "no element"
            else:
                state = await locator.evaluate(EDITABLE_JS)
                if require_visible and not state.get("visible"):
                    outcome.reason = "element not visible"
                elif state.get("disabled"):
                    outcome.reason = "element disabled"
                elif state.get("readOnly") and field_name not in {"submit", "success", "verified", "error"}:
                    outcome.reason = "element read-only"
                else:
                    token = f"waft-probe-{field_name}-{uuid.uuid4().hex[:8]}"
                    try:
                        await locator.evaluate(MARK_JS, token)
                        canonical = prefer_specific(await locator.evaluate(CANONICAL_JS), selector)
                        # Verify the canonical form resolves *to this very element* - WAFT resolves
                        # it later on its own, so "it matches something" is not good enough.
                        if await page.locator(canonical).first.count() == 0:
                            outcome.reason = "canonical selector did not round-trip"
                        elif BARE_TAG_RE.match(canonical.strip()):
                            outcome.reason = "canonical selector not specific (bare tag)"
                        elif await page.locator(canonical).first.evaluate(HAS_MARK_JS, token):
                            outcome.matched = True
                            outcome.canonical = canonical
                            outcome.reason = "matched"
                            outcomes.append(outcome)
                            return canonical, outcomes
                        else:
                            outcome.reason = "canonical selector points at a different element"
                    finally:
                        try:
                            await locator.evaluate(UNMARK_JS)
                        except Exception:  # noqa: BLE001 - page may have navigated away
                            pass
        except Exception as exc:  # noqa: BLE001 - a bad selector must never abort the probe
            outcome.reason = f"error: {type(exc).__name__}: {exc}"
        outcomes.append(outcome)
        await asyncio.sleep(0.02)  # keep the page responsive while sweeping many selectors
    return None, outcomes


async def resolve_host(
    browser: Browser,
    *,
    host: str,
    page_url: str,
    chains: Mapping[str, Sequence[str]],
    probe_timeout_ms: int,
    require_visible: bool,
    navigation_timeout_ms: int,
    user_agent: Optional[str] = None,
) -> HostResolution:
    """Open *page_url* and resolve every field chain for *host*."""
    resolution = HostResolution(host=host, page_url=page_url)
    context: Optional[BrowserContext] = None
    try:
        context = await browser.new_context(user_agent=user_agent, ignore_https_errors=True)
        page = await context.new_page()
        page.set_default_timeout(probe_timeout_ms)
        await page.goto(page_url, wait_until="domcontentloaded", timeout=navigation_timeout_ms)
        await page.wait_for_load_state("networkidle", timeout=min(navigation_timeout_ms, 8000))
    except Exception as exc:  # noqa: BLE001 - report, never raise
        resolution.error = f"{type(exc).__name__}: {exc}"
        if context is not None:
            await context.close()
        return resolution

    try:
        for field_name, candidates in chains.items():
            canonical, outcomes = await probe_chain(
                page,
                field_name,
                candidates,
                probe_timeout_ms=probe_timeout_ms,
                require_visible=require_visible,
            )
            resolution.outcomes.extend(outcomes)
            if canonical:
                resolution.resolved[field_name] = canonical
            else:
                resolution.missing.append(field_name)
    finally:
        await context.close()
    return resolution


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def hosts_from_data_file(data_file: Path, url_column: Optional[str]) -> dict[str, str]:
    """Return ``{host:port -> first URL}`` for every host found in the data source.

    Uses the framework's own loader so the resolver sees exactly the rows WAFT will run
    (Türkçe başlık normalizasyonu, ``|`` fan-out, empty-row tolerance included).
    """
    from waft.config import Config
    from waft.data_source import DataLoader

    config = Config(data_file=Path(data_file), url_column=url_column)
    config.validate()
    loader = DataLoader(config)
    rows = loader.load()
    mapping: dict[str, str] = {}
    for row in rows:
        try:
            from urllib.parse import urlsplit

            split = urlsplit(row.target_url)
        except Exception:  # noqa: BLE001
            continue
        host = (split.netloc or "").lower()
        if host and host not in mapping:
            mapping[host] = row.target_url
    return mapping


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")


def build_flat_map(resolutions: Sequence[HostResolution], *, include_informational: bool) -> dict[str, str]:
    """Merge per-host results into one flat WAFT selector map (field -> selector)."""
    flat: dict[str, str] = {}
    for resolution in resolutions:
        for name, selector in resolution.resolved.items():
            if not include_informational and name in INFORMATIONAL_FIELDS:
                continue
            flat.setdefault(name, selector)
    return flat


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="selector_resolver.py",
        description="Probe qa-kit/selectors.json fallback chains and emit selectors.resolved.json.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--data", type=Path, default=Path(__file__).with_name("targets.xlsx"),
                        help="Data source (Excel/JSON/CSV) whose target URLs are probed.")
    parser.add_argument("--url-column", type=str, default=None, help="Override the URL column name.")
    parser.add_argument("--catalogue", type=Path, default=Path(__file__).with_name("selectors.json"),
                        help="Fallback selector catalogue.")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("selectors.resolved.json"),
                        help="Where the WAFT-native selector map is written.")
    parser.add_argument("--report", type=Path, default=Path(__file__).with_name("selectors.resolved.report.json"),
                        help="Detailed probe report (which fallback won, which are dead).")
    parser.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"],
                        help="Browser engine used for probing.")
    parser.add_argument("--user-agent", default=None, help="Optional UA override for the probe.")
    parser.add_argument("--probe-timeout", type=int, default=10_000, help="Per-selector timeout (ms).")
    parser.add_argument("--navigation-timeout", type=int, default=30_000, help="Navigation timeout (ms).")
    parser.add_argument("--headful", action="store_true", help="Show the probing window (debugging).")
    parser.add_argument("--allow-invisible", action="store_true", help="Accept hidden elements (not recommended).")
    parser.add_argument("--include-informational", action="store_true",
                        help="Also write submit/success/error/captcha winners into the resolved map.")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    catalogue = Catalogue.load(args.catalogue)
    targets = hosts_from_data_file(args.data, args.url_column)
    if not targets:
        print(f"✖ no usable target URLs found in {args.data}", file=sys.stderr)
        return 2

    print(f"→ probing {len(targets)} host(s) from {args.data}")
    resolutions: list[HostResolution] = []
    async with async_playwright() as playwright:
        engine = getattr(playwright, args.browser)
        browser = await engine.launch(headless=not args.headful, args=["--no-sandbox"])
        try:
            for host, url in targets.items():
                host_only = host.split(":")[0]
                chains = catalogue.chains_for(host, host_only)
                if not chains:
                    print(f"  ! {host}: no selector chains configured (only '*' block is used)")
                resolution = await resolve_host(
                    browser,
                    host=host,
                    page_url=url,
                    chains=chains,
                    probe_timeout_ms=args.probe_timeout,
                    require_visible=not args.allow_invisible,
                    navigation_timeout_ms=args.navigation_timeout,
                    user_agent=args.user_agent,
                )
                resolutions.append(resolution)
                if resolution.error:
                    print(f"  ✖ {host}: {resolution.error}")
                else:
                    print(
                        f"  ✔ {host}: {len(resolution.resolved)} resolved, "
                        f"missing={resolution.missing or 'none'}"
                    )
        finally:
            await browser.close()

    flat = build_flat_map(resolutions, include_informational=args.include_informational)
    write_json(args.out, flat)
    write_json(
        args.report,
        {
            "catalogue": str(args.catalogue),
            "data": str(args.data),
            "resolved_file": str(args.out),
            "hosts": [resolution.to_dict() for resolution in resolutions],
            "flat_map": flat,
        },
    )
    print(f"✔ {args.out} written ({len(flat)} selector(s))")
    print(f"✔ {args.report} written")

    failures = [item for item in resolutions if item.error or item.required_missing]
    if failures:
        for item in failures:
            print(f"✖ {item.host}: {item.error or 'missing required fields: ' + ', '.join(item.required_missing)}",
                  file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:  # pragma: no cover
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
