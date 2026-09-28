#!/usr/bin/env python3
"""``postback_receiver.py`` - S2S postback endpoint + durability / load self-test.

What this is for
----------------
Offerwall / CPA integrations live or die on the **server-to-server postback**: the network (or
your own offerwall) calls *your* endpoint to report a conversion. That endpoint has to survive
ten concurrent realities at once: duplicate retries, replayed requests, forged signatures,
out-of-window timestamps, malformed payloads and plain load.

This module is a production-shaped receiver plus a self-test that proves it behaves:

* ``GET  /postback?uid=…&offer_id=…&status=…&payout=…&ts=…&sig=…``  (the classic GET postback)
* ``POST /postback``                                              (JSON body, same signature)
* ``GET  /health``                                                 (liveness + counters)
* ``GET  /metrics``                                                (counters + p50/p95/p99 latency)

Durability rules enforced
-------------------------
1. **HMAC-SHA256 signature** over ``uid|offer_id|status|payout|ts`` (constant-time compare) →
   ``401`` when it does not match.
2. **Replay window** (``--replay-window``, default 300 s) → ``410`` when ``ts`` is too old/future.
3. **Idempotency**: a ``uid`` may be accepted exactly once → later ones return ``409`` *duplicate*
   (they are counted, never double-credited).
4. **Malformed payloads** (missing ``uid`` / unparsable body) → ``400``.
5. Every decision is appended to an optional JSONL audit log, so a disputed conversion can be
   replayed offline.

Self-test (``--selftest``)
--------------------------
Starts the receiver on an ephemeral port and drives it with ``--workers`` concurrent clients:

======================  ===========================================================
Scenario                Expected result
======================  ===========================================================
``--requests`` unique   all ``202 accepted``
same uids replayed       ``409`` for each (idempotency holds)
wrong signature           ``401`` for each
stale timestamp (ts-1h)   ``410`` for each
missing ``uid``           ``400`` for each
======================  ===========================================================

It then asserts the counters, prints a throughput/latency report and exits non-zero if anything
deviated - i.e. it is usable as a CI gate for *your* postback endpoint.

Usage
-----
Serve for real (point a network sandbox at it)::

    POSTBACK_SECRET=... python qa-kit/offerwall/postback_receiver.py --serve --port 8095

Run the durability gate in CI (10 workers, 400 conversions + attack traffic)::

    python qa-kit/offerwall/postback_receiver.py --selftest --workers 10 --requests 400

Exit codes: 0 every assertion held · 1 assertion failed · 2 usage/bind error.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final, Mapping, Optional, Sequence

logger = logging.getLogger("waft.offerwall.postback")

#: Signature is computed over exactly these fields, in this order (documented for partners).
SIGNED_FIELDS: Final[tuple[str, ...]] = ("uid", "offer_id", "status", "payout", "ts")

STATUS_ACCEPTED: Final[int] = 202
STATUS_MALFORMED: Final[int] = 400
STATUS_UNAUTHORIZED: Final[int] = 401
STATUS_DUPLICATE: Final[int] = 409
STATUS_EXPIRED: Final[int] = 410


# --------------------------------------------------------------------------------------
# signing helpers (shared by the receiver and the self-test client)
# --------------------------------------------------------------------------------------
def canonical_payload(fields: Mapping[str, Any]) -> str:
    """Return the canonical signing string ``uid=…|offer_id=…|status=…|payout=…|ts=…``."""
    return "|".join(f"{name}={fields.get(name, '')}" for name in SIGNED_FIELDS)


def sign_postback(secret: str, fields: Mapping[str, Any]) -> str:
    """Return the hex HMAC-SHA256 signature for *fields* (what a network would send)."""
    return hmac.new(secret.encode("utf-8"), canonical_payload(fields).encode("utf-8"), hashlib.sha256).hexdigest()


def signature_matches(secret: str, fields: Mapping[str, Any], provided: str) -> bool:
    """Constant-time comparison, tolerant of a ``sha256=`` prefix."""
    expected = sign_postback(secret, fields)
    candidate = (provided or "").strip()
    if candidate.lower().startswith("sha256="):
        candidate = candidate.split("=", 1)[1]
    return hmac.compare_digest(expected, candidate)


# --------------------------------------------------------------------------------------
# counters
# --------------------------------------------------------------------------------------
@dataclass(slots=True)
class Counters:
    """Thread-safe-enough counters for a single-process receiver (guarded by the server lock)."""

    received: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected_signature: int = 0
    rejected_expired: int = 0
    rejected_malformed: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def percentiles(self) -> dict[str, float]:
        if not self.latencies_ms:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
        ordered = sorted(self.latencies_ms)
        return {
            "p50": round(statistics.median(ordered), 2),
            "p95": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2),
            "p99": round(ordered[max(0, int(len(ordered) * 0.99) - 1)], 2),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "received": self.received,
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "rejected_signature": self.rejected_signature,
            "rejected_expired": self.rejected_expired,
            "rejected_malformed": self.rejected_malformed,
            **self.percentiles(),
        }


# --------------------------------------------------------------------------------------
# receiver
# --------------------------------------------------------------------------------------
class PostbackHandler(BaseHTTPRequestHandler):
    """HTTP handler implementing the durability rules described in the module docstring."""

    server_version = "WAFTPostback/1.0"

    # ------------------------------------------------------------------ plumbing
    def _json(self, payload: Mapping[str, Any], status: int) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802 - stdlib naming
        if getattr(self.server, "quiet", True):
            return
        logger.info("%s %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------------ routing
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        if parsed.path == "/postback":
            self._handle_postback(query)
        elif parsed.path == "/health":
            counters: Counters = self.server.counters  # type: ignore[attr-defined]
            self._json({"status": "ok", **counters.snapshot()}, 200)
        elif parsed.path == "/metrics":
            counters = self.server.counters  # type: ignore[attr-defined]
            self._json({"uptime_s": round(time.monotonic() - self.server.started_at, 2), **counters.snapshot()}, 200)
        else:
            self._json({"error": "not_found", "path": parsed.path}, 404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/postback":
            self._json({"error": "not_found", "path": parsed.path}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        payload: dict[str, Any] = {}
        try:
            if raw.strip().startswith("{"):
                parsed_body = json.loads(raw)
                if isinstance(parsed_body, dict):
                    payload = {str(key): value for key, value in parsed_body.items()}
            else:
                payload = {key: values[0] for key, values in urllib.parse.parse_qs(raw).items()}
        except (json.JSONDecodeError, ValueError) as exc:
            self._reject("malformed", message=f"body could not be parsed: {exc}")
            return
        header_signature = self.headers.get("X-WAFT-Signature")
        if header_signature and "sig" not in payload:
            payload["sig"] = header_signature
        self._handle_postback(payload)

    # ------------------------------------------------------------------ core logic
    def _handle_postback(self, fields: Mapping[str, Any]) -> None:
        server: Any = self.server
        counters: Counters = server.counters
        started = time.perf_counter()

        with server.lock:
            counters.received += 1

        uid = str(fields.get("uid") or "").strip()
        if not uid:
            self._reject("malformed", message="uid is required")
            return

        provided = str(fields.get("sig") or "")
        if not signature_matches(server.secret, fields, provided):
            self._reject("signature", message="signature mismatch")
            return

        try:
            timestamp = int(str(fields.get("ts") or "0"))
        except ValueError:
            self._reject("malformed", message="ts must be an integer (unix seconds)")
            return
        if abs(time.time() - timestamp) > server.replay_window:
            self._reject("expired", message=f"ts outside ±{int(server.replay_window)}s replay window")
            return

        with server.lock:
            if uid in server.seen_uids:
                counters.duplicates += 1
                duplicate = True
            else:
                server.seen_uids[uid] = dict(fields)
                counters.accepted += 1
                duplicate = False
            counters.latencies_ms.append((time.perf_counter() - started) * 1000.0)
            if len(counters.latencies_ms) > 50_000:  # bound memory on long runs
                del counters.latencies_ms[:25_000]

        if server.audit_path:
            try:
                with server.audit_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {"at": time.time(), "decision": "duplicate" if duplicate else "accepted", "fields": dict(fields)},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except OSError as exc:  # noqa: BLE001 - audit must never break the endpoint
                logger.debug("audit log write failed: %s", exc)

        if duplicate:
            self._json({"status": "duplicate", "uid": uid, "credited": False}, STATUS_DUPLICATE)
        else:
            self._json({"status": "accepted", "uid": uid, "credited": True}, STATUS_ACCEPTED)

    def _reject(self, kind: str, *, message: str) -> None:
        server: Any = self.server
        counters: Counters = server.counters
        mapping = {
            "malformed": ("rejected_malformed", STATUS_MALFORMED),
            "signature": ("rejected_signature", STATUS_UNAUTHORIZED),
            "expired": ("rejected_expired", STATUS_EXPIRED),
        }
        attribute, status = mapping[kind]
        with server.lock:
            setattr(counters, attribute, getattr(counters, attribute) + 1)
        self._json({"status": "rejected", "reason": kind, "message": message}, status)


class PostbackServer(ThreadingHTTPServer):
    """Threaded receiver with shared state (counters, uid ledger, secret, replay window)."""

    allow_reuse_address = True
    daemon_threads = True
    # ThreadingHTTPServer defaults to a listen backlog of 5; with 10 concurrent postback senders
    # the accept queue overflows and Linux re-sends the SYN after ~1 s - visible as a p99 outlier
    # in the load test (measured: p99 1.01 s). 128 is a sane production value.
    request_queue_size = 128

    def __init__(self, address: tuple[str, int], *, secret: str, replay_window: float, audit_path: Optional[Path], quiet: bool) -> None:
        super().__init__(address, PostbackHandler)
        self.secret = secret
        self.replay_window = replay_window
        self.counters = Counters()
        self.seen_uids: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.audit_path = audit_path
        self.quiet = quiet
        self.started_at = time.monotonic()


# --------------------------------------------------------------------------------------
# self-test client
# --------------------------------------------------------------------------------------
@dataclass(slots=True)
class ProbeResult:
    """One HTTP call result from the load generator."""

    status: int
    latency_ms: float
    expected: int
    label: str

    @property
    def ok(self) -> bool:
        return self.status == self.expected


def _request(url: str, params: Mapping[str, Any], *, timeout: float) -> tuple[int, float]:
    """Fire one GET postback; return ``(status_code, latency_ms)`` (never raises)."""
    query = urllib.parse.urlencode({key: str(value) for key, value in params.items()})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(f"{url}?{query}", timeout=timeout) as response:
            response.read()
            return int(response.status), (time.perf_counter() - started) * 1000.0
    except urllib.error.HTTPError as exc:
        exc.read()
        return int(exc.code), (time.perf_counter() - started) * 1000.0
    except Exception as exc:  # noqa: BLE001 - network hiccups are reported as status 0
        logger.debug("request failed: %s", exc)
        return 0, (time.perf_counter() - started) * 1000.0


def run_selftest(args: argparse.Namespace) -> int:
    """Start the receiver on an ephemeral port and prove every durability rule holds."""
    secret = args.secret or os.environ.get("POSTBACK_SECRET") or "selftest-secret"
    audit = Path(args.audit) if args.audit else None

    server = PostbackServer(
        ("127.0.0.1", args.port or 0),
        secret=secret,
        replay_window=args.replay_window,
        audit_path=audit,
        quiet=True,
    )
    host, port = server.server_address[:2]
    endpoint = f"http://{host}:{port}/postback"
    thread = threading.Thread(target=server.serve_forever, name="postback-receiver", daemon=True)
    thread.start()
    print(f"→ receiver up: {endpoint}  (secret={'<given>' if args.secret else '<env/default>'}, replay window ±{int(args.replay_window)}s)")

    now = int(time.time())
    unique: list[dict[str, Any]] = []
    for worker in range(args.workers):
        for index in range(max(1, args.requests // args.workers)):
            fields = {
                "uid": f"uid-{worker:02d}-{index:05d}",
                "offer_id": f"offer-{index % 7 + 1}",
                "status": "completed",
                "payout": f"{0.10 + (index % 5) * 0.05:.2f}",
                "ts": now,
            }
            fields["sig"] = sign_postback(secret, fields)
            unique.append(fields)

    # Two strictly separated phases: replayed uids are only fired *after* the unique phase has
    # finished, otherwise an original and its duplicate race and either may win - the receiver
    # would still be correct, but the test could not attribute the 202/409 outcomes (observed:
    # 400 accepted + 20 duplicates, yet labels mismatched). Deterministic beats clever.
    phase_unique: list[tuple[str, dict[str, Any], int]] = [
        (f"valid #{position}", fields, STATUS_ACCEPTED) for position, fields in enumerate(unique)
    ]
    stale_ts = now - int(args.replay_window) - 600
    phase_hostile: list[tuple[str, dict[str, Any], int]] = [
        ("duplicate", dict(fields), STATUS_DUPLICATE) for fields in unique[:20]
    ]
    phase_hostile += [
        ("bad signature", {**{k: v for k, v in fields.items() if k != "sig"}, "sig": "0" * 64}, STATUS_UNAUTHORIZED)
        for fields in unique[:10]
    ]
    phase_hostile += [
        (
            "stale ts",
            {
                **{k: v for k, v in fields.items() if k not in {"ts", "sig"}},
                "ts": stale_ts,
                "sig": sign_postback(
                    secret,
                    {**{k: v for k, v in fields.items() if k not in {"ts", "sig"}}, "ts": stale_ts},
                ),
            },
            STATUS_EXPIRED,
        )
        for fields in unique[10:20]
    ]
    phase_hostile += [
        ("missing uid", {"offer_id": "offer-1", "status": "completed", "payout": "0.10", "ts": now}, STATUS_MALFORMED),
    ]

    print(
        f"→ faz 1: {len(phase_unique)} benzersiz dönüşüm, {args.workers} paralel istemci\n"
        f"→ faz 2: {len(phase_hostile)} saldırı/yeniden gönderim "
        f"(duplicate + sahte imza + süresi geçmiş + bozuk payload)"
    )
    # Warm-up: the very first request pays thread-pool/TCP setup cost (measured ~1s p99 outlier);
    # it is fired before the clock starts, is not a scenario result, and is excluded from the
    # assertions via the counter baseline taken right after it.
    _request(endpoint, {"uid": "warmup", "offer_id": "0", "status": "warmup", "payout": "0", "ts": now, "sig": ""}, timeout=args.timeout)
    baseline = server.counters.snapshot()

    started = time.perf_counter()
    results: list[ProbeResult] = []
    for phase_name, plan in (("unique", phase_unique), ("hostile", phase_hostile)):
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(_request, endpoint, fields, timeout=args.timeout) for _label, fields, _expect in plan]
            for (label, _fields, expected), future in zip(plan, futures):
                status, latency = future.result()
                results.append(ProbeResult(status=status, latency_ms=latency, expected=expected, label=label))
        logger.debug("phase %s finished", phase_name)
    elapsed = time.perf_counter() - started

    failures = [result for result in results if not result.ok]
    counters = server.counters.snapshot()
    latencies = [result.latency_ms for result in results] or [0.0]
    ordered = sorted(latencies)
    self_test_report = {
        "requests": len(results),
        "workers": args.workers,
        "elapsed_s": round(elapsed, 2),
        "throughput_rps": round(len(results) / elapsed, 1) if elapsed else 0.0,
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2),
        "p99_ms": round(ordered[max(0, int(len(ordered) * 0.99) - 1)], 2),
    }

    print("\n  senaryo sonuçları")
    for label in ("valid", "duplicate", "bad signature", "stale ts", "missing uid"):
        subset = [result for result in results if result.label.startswith(label)]
        expected_status = subset[0].expected if subset else 0
        observed = sorted({result.status for result in subset})
        verdict = "OK " if subset and all(result.ok for result in subset) else "FAIL"
        print(f"    {verdict} {label:14s} n={len(subset):4d}  beklenen={expected_status}  gözlenen={observed}")

    print("\n  receiver metrikleri")
    for key, value in counters.items():
        print(f"    {key:22s}: {value}")

    print("\n  yük profili")
    for key, value in self_test_report.items():
        print(f"    {key:22s}: {value}")

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)

    # Assertions run on deltas measured from the post-warm-up baseline, so the (deliberately
    # rejected) warm-up request can never skew the expected numbers.
    additive = ("received", "accepted", "duplicates", "rejected_signature", "rejected_expired", "rejected_malformed")
    delta = {key: int(counters[key]) - int(baseline[key]) for key in additive}
    print("\n  bu koşunun sayaç farkı (warm-up hariç)")
    for key in additive:
        print(f"    {key:22s}: {delta[key]}")

    assertions = [
        (not failures, f"{len(failures)} request(s) returned an unexpected status: "
                       + ", ".join(f"{item.label}→{item.status}" for item in failures[:5])),
        (delta["accepted"] == len(unique), f"expected {len(unique)} accepted, got {delta['accepted']}"),
        (delta["duplicates"] == 20, f"expected 20 duplicates, got {delta['duplicates']}"),
        (delta["rejected_signature"] == 10, f"expected 10 forged signatures rejected, got {delta['rejected_signature']}"),
        (delta["rejected_expired"] == 10, f"expected 10 stale requests rejected, got {delta['rejected_expired']}"),
        (delta["rejected_malformed"] >= 1, "malformed payloads were not rejected"),
        (delta["received"] == delta["accepted"] + delta["duplicates"]
         + delta["rejected_signature"] + delta["rejected_expired"] + delta["rejected_malformed"],
         "counter bookkeeping does not add up"),
    ]
    broken = [message for ok, message in assertions if not ok]
    print("\n" + "=" * 88)
    if broken:
        for message in broken:
            print(f"✖ FAIL: {message}")
        print("=" * 88)
        return 1
    print(
        f"✔ postback receiver durability gate PASSED — {delta['accepted']} kabul, "
        f"{delta['duplicates']} duplicate, {delta['rejected_signature']} sahte imza, "
        f"{delta['rejected_expired']} süresi geçmiş, {delta['rejected_malformed']} bozuk payload; "
        f"{self_test_report['throughput_rps']} rps, p50 {self_test_report['p50_ms']} ms, "
        f"p95 {self_test_report['p95_ms']} ms, p99 {self_test_report['p99_ms']} ms "
        f"({args.workers} paralel istemci)"
    )
    print("=" * 88)
    return 0


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="postback_receiver.py",
        description="S2S postback endpoint (HMAC + replay window + idempotency) and its durability self-test.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--serve", action="store_true", help="Run the receiver and stay up (real traffic).")
    mode.add_argument("--selftest", action="store_true", help="Run the load/durability gate and exit.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (--serve).")
    parser.add_argument("--port", type=int, default=0, help="Port (0 = ephemeral; --serve default 8095).")
    parser.add_argument("--secret", default=None, help="HMAC shared secret (falls back to POSTBACK_SECRET).")
    parser.add_argument("--replay-window", type=float, default=300.0, help="Accepted |now - ts| in seconds.")
    parser.add_argument("--audit", default=None, help="Append-only JSONL audit log path.")
    parser.add_argument("--workers", type=int, default=10, help="Concurrent clients in --selftest.")
    parser.add_argument("--requests", type=int, default=400, help="Unique conversions in --selftest.")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout (s).")
    parser.add_argument("--quiet", action="store_true", help="Do not log every request.")
    parser.add_argument("--log-level", default="WARNING", help="Log level for the receiver itself.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.selftest or not args.serve:
        return run_selftest(args)

    secret = args.secret or os.environ.get("POSTBACK_SECRET")
    if not secret:
        print("✖ --serve needs a shared secret (--secret or POSTBACK_SECRET)", file=sys.stderr)
        return 2
    port = args.port or 8095
    try:
        server = PostbackServer(
            (args.host, port),
            secret=secret,
            replay_window=args.replay_window,
            audit_path=Path(args.audit) if args.audit else None,
            quiet=args.quiet,
        )
    except OSError as exc:
        print(f"✖ could not bind {args.host}:{port}: {exc}", file=sys.stderr)
        return 2
    print(f"postback receiver listening on http://{args.host}:{port}/postback  (Ctrl+C to stop)")
    print(f"  signing fields : {'|'.join(SIGNED_FIELDS)}")
    print(f"  replay window  : ±{int(args.replay_window)}s | idempotency: uid accepted once")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down…")
    finally:
        counters = server.counters.snapshot()
        server.server_close()
        print(f"  final counters : {json.dumps(counters, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
