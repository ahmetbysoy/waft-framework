#!/usr/bin/env python3
"""``local_proxy_server.py`` - local reverse proxy for WAF proxy-chain testing.

What this is (and what it is not)
---------------------------------
This is a **reverse proxy**: a client talks to ``http://127.0.0.1:8080`` and every request is
forwarded, unchanged, to the single upstream given by ``REAL_TARGET_URL``. It is the standard way
to measure how your own WAF/gateway treats requests that arrive **through** a proxy hop
(``X-Forwarded-For`` / ``X-Forwarded-Proto`` / ``X-Forwarded-Host`` / ``Via`` handling, rule
matching on the forwarded client address, redirect rewriting, hop-by-hop header stripping).

It is **not** an interception/proxy-rotation tool: it does not hide who you are, it does not spoof
fingerprints, and it never forwards to a third party. The upstream is validated against the same
scope file the rest of this kit uses (``qa-kit/authorized_hosts.txt``) plus a hard block-list of
third-party offerwall / micro-task platforms; an off-scope upstream makes this process **refuse to
start** (exit 2). "Point it at localhost and forward somewhere else" is exactly the pattern that
turns a test harness into an abuse tool, so it is blocked by construction, not by documentation.

Behaviour
---------
* Binds **127.0.0.1** by default (``--listen-host`` to override; you do not want this on 0.0.0.0).
* Forwards all methods (GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS) with headers, cookies, query
  string and body passed through verbatim, minus RFC 7230 hop-by-hop headers.
* Adds (or appends to) the forwarding headers so your WAF has a proxy chain to inspect:
  ``X-Forwarded-For`` (appended, standard behaviour), ``X-Forwarded-Proto``, ``X-Forwarded-Host``,
  ``X-Real-IP`` and ``Via``. ``--xff-client`` substitutes a fixed value for ``X-Forwarded-For`` -
  that is how you test a WAF rule such as "never trust XFF from an untrusted hop".
* Streams the upstream response back verbatim (status, headers, body, multiple ``Set-Cookie``).
  ``Location`` headers that point at the upstream origin are rewritten to the proxy origin
  (``--no-rewrite-location`` disables it) so the browser keeps talking to the proxy.
* Writes one JSON line per forwarded request to ``--log-jsonl`` (method, path, status, duration,
  the forwarding headers it sent, request/response sizes). That file is the measurement.
* Prints a single readiness line ``{"event": "listening", ...}`` on stdout - the QA driver (and
  ``--wait-for-ready`` in CI) waits for exactly that.

Usage
-----
Local end-to-end (bundled sandbox as the "origin" behind the proxy)::

    export REAL_TARGET_URL=http://127.0.0.1:8090
    python3 qa-kit/loadtest/local_proxy_server.py --listen-port 8080

Your own staging behind the proxy::

    export REAL_TARGET_URL=https://staging.sirketiniz.com     # host must be in the scope file
    python3 qa-kit/loadtest/local_proxy_server.py --log-jsonl artifacts/proxy/proxy_requests.jsonl

Driven automatically (spawn + browser) by ``run_proxy_test.py``.

Exit codes: ``0`` clean shutdown | ``2`` usage / scope / configuration error | ``130`` interrupt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import socket
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Final, Iterable, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

# --- make the package + kit siblings importable when run as a plain script ---------------------
_HERE: Final[Path] = Path(__file__).resolve().parent
_KIT: Final[Path] = _HERE.parent
_REPO: Final[Path] = _KIT.parent
_OFFERWALL: Final[Path] = _KIT / "offerwall"
for _path in (str(_REPO), str(_HERE), str(_KIT), str(_OFFERWALL)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from run_regression import (  # noqa: E402  (single source of truth for the scope gate)
    BLOCKED_THIRD_PARTY_SUFFIXES,
    SCOPE_HELP,
    Scope,
    host_of,
)

logger = logging.getLogger("waft.proxy")

#: aiohttp is imported defensively so a missing dependency is a sentence, not a traceback.
_AIOHTTP_IMPORT_ERROR: Optional[str] = None
aiohttp: Any = None
web: Any = None
try:  # pragma: no cover - environment dependent
    import aiohttp as _aiohttp
    from aiohttp import web as _web

    aiohttp, web = _aiohttp, _web
except ModuleNotFoundError as _exc:  # pragma: no cover - environment dependent
    _AIOHTTP_IMPORT_ERROR = str(_exc)


# ======================================================================================
# hop-by-hop headers (RFC 7230 §6.1) + the ones we must recompute ourselves
# ======================================================================================
HOP_BY_HOP: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

#: Headers we never copy verbatim: Host is replaced by the upstream host, Content-Length is
#: recomputed from the body we actually send/receive.
NEVER_FORWARD: Final[frozenset[str]] = frozenset({"host", "content-length"})

#: Forwarding headers the proxy is allowed to write.
FORWARDED_HEADERS: Final[tuple[str, ...]] = (
    "X-Forwarded-For",
    "X-Forwarded-Proto",
    "X-Forwarded-Host",
    "X-Real-IP",
    "Via",
)


class ProxyError(RuntimeError):
    """Fatal, user-facing configuration/scope problem (exit code 2)."""


# ======================================================================================
# header plumbing (pure functions - unit tested without a browser or a socket)
# ======================================================================================
def strip_hop_by_hop(headers: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop hop-by-hop headers, plus every field named in a ``Connection:`` header.

    ``Connection: X-Custom-Hop`` means *that* field is also hop-by-hop; a proxy that forwards it
    leaks per-connection semantics upstream and some WAFs reject the request outright.
    """
    items = [(str(key), str(value)) for key, value in headers]
    connection_tokens: set[str] = set()
    for key, value in items:
        if key.lower() == "connection":
            connection_tokens.update(token.strip().lower() for token in value.split(",") if token.strip())
    drop = HOP_BY_HOP | connection_tokens
    return [(key, value) for key, value in items if key.lower() not in drop]


def build_forward_headers(
    incoming: Iterable[tuple[str, str]],
    *,
    client_ip: str,
    proxy_scheme: str,
    xff_client: Optional[str] = None,
) -> dict[str, str]:
    """Return the header dict to send upstream (single-valued view, last wins).

    The client's own ``X-Forwarded-*`` values are preserved and appended to (never replaced), which
    is what a real proxy chain does and what your WAF has to cope with.
    """
    kept = strip_hop_by_hop(incoming)
    headers: dict[str, str] = {}
    for key, value in kept:
        if key.lower() in NEVER_FORWARD:
            continue
        headers[key] = value

    original_host = next((value for key, value in kept if key.lower() == "host"), "")
    existing_xff = next((value for key, value in kept if key.lower() == "x-forwarded-for"), "")

    if xff_client is not None:
        # Explicit override: this is how you test "our WAF must not trust this header".
        headers["X-Forwarded-For"] = xff_client
    elif existing_xff:
        headers["X-Forwarded-For"] = f"{existing_xff}, {client_ip}"
    else:
        headers["X-Forwarded-For"] = client_ip

    headers.setdefault("X-Forwarded-Proto", proxy_scheme)
    if original_host:
        headers["X-Forwarded-Host"] = original_host
    headers["X-Real-IP"] = client_ip
    via = headers.get("Via")
    headers["Via"] = f"{via}, 1.1 waft-local-proxy" if via else "1.1 waft-local-proxy"
    return headers


def join_upstream_url(upstream: str, path: str, query_string: str) -> str:
    """Build the upstream URL from the proxy request path/query (upstream path prefix honoured).

    ``upstream=http://127.0.0.1:8090/base`` + ``path=/register`` → ``http://127.0.0.1:8090/base/register``;
    the proxy request path is used **as received** (no normalisation), so path-traversal style probe
    payloads reach your WAF exactly as the attacker sent them.
    """
    parts = urlsplit(upstream)
    prefix = parts.path.rstrip("/")
    target_path = path if path.startswith("/") else "/" + path
    combined = (prefix + target_path) or "/"
    return urlunsplit((parts.scheme, parts.netloc, combined, query_string, ""))


def rewrite_location(location: str, upstream: str, proxy_origin: str) -> str:
    """Rewrite an upstream-absolute ``Location`` to point back at the proxy (browser stays local).

    Redirects to a *different* host are left untouched on purpose: that is a real behavioural signal
    about your application (and silently rewriting it would hide a misconfiguration from you).
    """
    if not location:
        return location
    if location.startswith("/"):
        return location
    up = urlsplit(upstream)
    loc = urlsplit(location)
    if loc.netloc and loc.netloc.lower() == up.netloc.lower():
        return urlunsplit((urlsplit(proxy_origin).scheme, urlsplit(proxy_origin).netloc, loc.path, loc.query, loc.fragment))
    return location


def describe_forwarding(headers: dict[str, str]) -> dict[str, str]:
    """The subset of headers worth recording as the *measurement* (proxy chain visibility)."""
    return {key: headers[key] for key in FORWARDED_HEADERS if key in headers}


# ======================================================================================
# scope gate (identical rules to the rest of the kit)
# ======================================================================================
def validate_upstream(upstream: str, scope: Scope, *, i_am_authorized: bool) -> str:
    """Return the normalised upstream URL, or raise :class:`ProxyError` (hard block-list first)."""
    text = (upstream or "").strip()
    if not text:
        raise ProxyError(
            "REAL_TARGET_URL is empty. This proxy has no hard-coded destination by design:\n"
            "    export REAL_TARGET_URL=http://127.0.0.1:8090      # local mock\n"
            "    export REAL_TARGET_URL=https://staging.sirketiniz.com   # your own staging"
        )
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ProxyError(f"REAL_TARGET_URL must be an absolute http(s) URL (got {text!r})")

    host = parts.netloc.lower()
    bare = host.split(":")[0]
    blocked = [suffix for suffix in BLOCKED_THIRD_PARTY_SUFFIXES if bare == suffix or bare.endswith("." + suffix)]
    if blocked:
        raise ProxyError(
            "refusing to forward to third-party offerwall / micro-task platform(s): "
            + host
            + " — automating sign-ups there is abuse, not testing this proxy's behaviour.\n"
            "This block-list is compiled in and cannot be disabled by any flag."
        )
    if not scope.allows(host) and not scope.allows(bare) and not i_am_authorized:
        raise ProxyError(f"upstream host not covered by the scope file: {host} — {SCOPE_HELP}")
    if not scope.allows(host) and not scope.allows(bare):
        logger.warning(
            "upstream %s is outside the scope file; allowed because --i-am-authorized was given", host
        )
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


# ======================================================================================
# the proxy application
# ======================================================================================
class LocalReverseProxy:
    """The aiohttp application, the forward logic and the JSONL measurement log."""

    def __init__(
        self,
        *,
        upstream: str,
        listen_host: str,
        listen_port: int,
        log_jsonl: Optional[Path],
        rewrite_redirects: bool,
        xff_client: Optional[str],
        timeout_s: float,
        max_body_bytes: int,
        strip_cookie_domain: bool,
        disable_compression_passthrough: bool,
    ) -> None:
        self.upstream = upstream
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.log_jsonl = Path(log_jsonl) if log_jsonl else None
        self.rewrite_redirects = rewrite_redirects
        self.xff_client = xff_client
        self.timeout_s = timeout_s
        self.max_body_bytes = max_body_bytes
        self.strip_cookie_domain = strip_cookie_domain
        self.disable_compression_passthrough = disable_compression_passthrough
        self.proxy_origin = f"http://{listen_host}:{listen_port}"
        self.stats: dict[str, Any] = {"forwarded": 0, "errors": 0, "started_at": time.time()}
        self._session: Optional[Any] = None
        self._log_lock = asyncio.Lock()
        self._shutdown = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle
    async def _make_session(self) -> Any:
        timeout = aiohttp.ClientTimeout(total=self.timeout_s, connect=min(10.0, self.timeout_s))
        # auto_decompress=False keeps the body byte-identical to what the origin sent; a proxy that
        # silently gunzips would change Content-Encoding/Content-Length and confuse WAF measurements.
        return aiohttp.ClientSession(timeout=timeout, auto_decompress=False, trust_env=False)

    async def on_startup(self, app: Any) -> None:
        self._session = await self._make_session()
        logger.info("forwarding %s → %s", self.proxy_origin, self.upstream)

    async def on_cleanup(self, app: Any) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------ logging
    async def _record(self, record: dict[str, Any]) -> None:
        if self.log_jsonl is None:
            return
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        async with self._log_lock:
            try:
                self.log_jsonl.parent.mkdir(parents=True, exist_ok=True)
                with self.log_jsonl.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as exc:  # pragma: no cover - disk problems must not break forwarding
                logger.warning("could not write %s: %s", self.log_jsonl, exc)

    # ------------------------------------------------------------------ forward
    async def handle(self, request: Any) -> Any:
        started = time.perf_counter()
        target = join_upstream_url(self.upstream, request.path, request.query_string)
        client_ip = self._client_ip(request)
        forward_headers = build_forward_headers(
            request.headers.items(),
            client_ip=client_ip,
            proxy_scheme=request.scheme or "http",
            xff_client=self.xff_client,
        )

        body = b""
        if request.can_read_body:
            try:
                body = await request.read()
            except Exception as exc:  # noqa: BLE001 - a broken client body is a 400, not a crash
                self.stats["errors"] += 1
                await self._record(
                    {
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "type": "proxy_error",
                        "method": request.method,
                        "path": request.path,
                        "error": f"client body unreadable: {type(exc).__name__}",
                    }
                )
                return web.Response(status=400, text="proxy: client body unreadable\n")
            if len(body) > self.max_body_bytes:
                self.stats["errors"] += 1
                logger.warning("request body of %d bytes exceeds --max-body-mb; refusing", len(body))
                await self._record(
                    {
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "type": "proxy_error",
                        "method": request.method,
                        "path": request.path,
                        "error": "request body too large",
                        "body_bytes": len(body),
                    }
                )
                return web.Response(status=413, text="proxy: request body too large\n")

        assert self._session is not None, "session must exist (on_startup)"
        try:
            async with self._session.request(
                request.method,
                target,
                headers=forward_headers,
                data=body if body else None,
                allow_redirects=False,
            ) as upstream_response:
                payload = await upstream_response.read()
                status = upstream_response.status
                response_headers = strip_hop_by_hop(upstream_response.headers.items())
        except asyncio.TimeoutError:
            self.stats["errors"] += 1
            await self._finish(request, target, client_ip, forward_headers, started, status=None, error="timeout")
            return web.Response(status=504, text="proxy: upstream timeout\n")
        except aiohttp.ClientError as exc:
            self.stats["errors"] += 1
            await self._finish(
                request, target, client_ip, forward_headers, started, status=None, error=f"{type(exc).__name__}"
            )
            return web.Response(status=502, text=f"proxy: upstream unreachable ({type(exc).__name__})\n")

        self.stats["forwarded"] += 1
        await self._finish(
            request,
            target,
            client_ip,
            forward_headers,
            started,
            status=status,
            response_bytes=len(payload),
            request_bytes=len(body),
        )

        out = web.StreamResponse(status=status)
        for key, value in response_headers:
            lowered = key.lower()
            if lowered == "content-length":
                continue  # recomputed from the body we are about to send
            if lowered == "location" and self.rewrite_redirects:
                value = rewrite_location(value, self.upstream, self.proxy_origin)
            if lowered == "set-cookie" and self.strip_cookie_domain:
                value = re.sub(r";\s*Domain=[^;]*", "", value, flags=re.I)
            if lowered == "content-encoding" and self.disable_compression_passthrough:
                continue
            out.headers[key] = value
        out.headers["Content-Length"] = str(len(payload))
        out.headers["X-Waft-Proxy"] = "local-reverse-proxy"
        await out.prepare(request)
        if payload:
            await out.write(payload)
        await out.write_eof()
        return out

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _client_ip(request: Any) -> str:
        peername = request.transport.get_extra_info("peername") if request.transport else None
        if isinstance(peername, (tuple, list)) and peername:
            return str(peername[0])
        return "127.0.0.1"

    async def _finish(
        self,
        request: Any,
        target: str,
        client_ip: str,
        forward_headers: dict[str, str],
        started: float,
        *,
        status: Optional[int],
        error: Optional[str] = None,
        request_bytes: int = 0,
        response_bytes: int = 0,
    ) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "type": "forward",
            "client": client_ip,
            "method": request.method,
            "path": request.path,
            "query": request.query_string,
            "upstream_url": target,
            "upstream_host": host_of(target),
            "status": status,
            "error": error,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "request_bytes": request_bytes,
            "response_bytes": response_bytes,
            "forwarded_headers": describe_forwarding(forward_headers),
        }
        await self._record(record)
        logger.debug(
            "%s %s → %s (%s) %.1f ms",
            request.method,
            request.path,
            status if status is not None else "ERROR",
            error or "ok",
            record["duration_ms"],
        )

    # ------------------------------------------------------------------ app
    def build_app(self) -> Any:
        app = web.Application(client_max_size=self.max_body_bytes)
        app.on_startup.append(self.on_startup)
        app.on_cleanup.append(self.on_cleanup)
        app.router.add_route("*", "/{tail:.*}", self.handle)
        return app


# ======================================================================================
# CLI
# ======================================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local_proxy_server.py",
        description="Local reverse proxy for measuring your own WAF's proxy-chain behaviour.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--upstream", default=None,
                        help="forward target; defaults to the REAL_TARGET_URL environment variable")
    parser.add_argument("--listen-host", default="127.0.0.1", help="bind address (keep it loopback)")
    parser.add_argument("--listen-port", type=int, default=8080, help="bind port")
    parser.add_argument("--scope", type=Path, default=_KIT / "authorized_hosts.txt", help="scope file")
    parser.add_argument("--i-am-authorized", action="store_true",
                        help="allow an upstream that is not in the scope file (your own infra only)")
    parser.add_argument("--log-jsonl", type=Path, default=None, help="append one JSON line per forwarded request")
    parser.add_argument("--no-rewrite-location", dest="rewrite_redirects", action="store_false", default=True,
                        help="pass upstream Location headers through unchanged")
    parser.add_argument("--strip-cookie-domain", action="store_true",
                        help="remove Domain= from Set-Cookie so the browser keeps cookies on the proxy host")
    parser.add_argument("--drop-content-encoding", action="store_true",
                        help="do not forward Content-Encoding (debugging only; changes the body contract)")
    parser.add_argument("--xff-client", default=None,
                        help="fixed X-Forwarded-For value (WAF rule testing: 'do we trust this header?')")
    parser.add_argument("--timeout", type=float, default=30.0, help="upstream timeout (seconds)")
    parser.add_argument("--max-body-mb", type=float, default=32.0, help="maximum request body size (MB)")
    parser.add_argument("--log-level", default="info", help="debug | info | warning | error")
    parser.add_argument("--quiet", action="store_true", help="only log warnings/errors")
    parser.add_argument("--check", action="store_true", help="validate configuration/scope and exit")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(levelname)-7s | %(name)s | %(message)s",
    )

    if _AIOHTTP_IMPORT_ERROR is not None:
        print(
            "✖ aiohttp gerekli: " + _AIOHTTP_IMPORT_ERROR + "\n  → python3 -m pip install aiohttp",
            file=sys.stderr,
        )
        return 2

    upstream_raw = args.upstream if args.upstream is not None else os.environ.get("REAL_TARGET_URL", "")
    scope = Scope.load(args.scope, [])
    try:
        upstream = validate_upstream(upstream_raw, scope, i_am_authorized=bool(args.i_am_authorized))
    except ProxyError as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return 2

    if not 1 <= int(args.listen_port) <= 65535:
        print("✖ --listen-port 1..65535 arasında olmalı", file=sys.stderr)
        return 2

    proxy = LocalReverseProxy(
        upstream=upstream,
        listen_host=args.listen_host,
        listen_port=int(args.listen_port),
        log_jsonl=args.log_jsonl,
        rewrite_redirects=bool(args.rewrite_redirects),
        xff_client=args.xff_client,
        timeout_s=float(args.timeout),
        max_body_bytes=int(float(args.max_body_mb) * 1024 * 1024),
        strip_cookie_domain=bool(args.strip_cookie_domain),
        disable_compression_passthrough=bool(args.drop_content_encoding),
    )

    if args.check:
        print(
            f"→ proxy doğrulaması tamam: {proxy.proxy_origin} → {upstream} | "
            f"rewrite_location={bool(args.rewrite_redirects)} | scope={args.scope.name} | "
            f"xff_client={args.xff_client or '(client ip)'}"
        )
        return 0

    if not _port_free(args.listen_host, int(args.listen_port)):
        print(
            f"✖ {args.listen_host}:{args.listen_port} kullanımda. Proxy zaten çalışıyor olabilir;\n"
            f"  → kapatın ya da --listen-port ile başka port seçin.",
            file=sys.stderr,
        )
        return 2

    # The QA driver (and ``--wait-for-ready`` in CI) waits for exactly this line on stdout; it is
    # printed *after* the bind check so "listening" is never a lie.
    print(
        json.dumps(
            {
                "event": "listening",
                "listen": proxy.proxy_origin,
                "upstream": upstream,
                "log_jsonl": str(args.log_jsonl) if args.log_jsonl else None,
                "rewrite_location": bool(args.rewrite_redirects),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    app = proxy.build_app()
    web.run_app(app, host=args.listen_host, port=int(args.listen_port), print=None, shutdown_timeout=5)
    return 0


def _port_free(host: str, port: int) -> bool:
    """True when nothing is listening on host:port (so we can fail before aiohttp's traceback)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


async def serve_forever(proxy: LocalReverseProxy, *, ready_event: Optional[asyncio.Event] = None) -> None:
    """Programmatic entry point (used by tests): run the app until ``proxy._shutdown`` is set."""
    app = proxy.build_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, proxy.listen_host, proxy.listen_port)
    await site.start()
    if ready_event is not None:
        ready_event.set()
    try:
        await proxy._shutdown.wait()  # noqa: SLF001 - internal by design (same module)
    finally:
        await runner.cleanup()


def install_signal_handlers(proxy: LocalReverseProxy) -> None:
    """Ctrl-C / SIGTERM → graceful aiohttp shutdown (keeps the JSONL log intact)."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):  # pragma: no cover - platform dependent
            loop.add_signal_handler(sig, proxy._shutdown.set)  # noqa: SLF001


if __name__ == "__main__":
    raise SystemExit(main())
