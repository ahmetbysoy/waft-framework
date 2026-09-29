"""Unit tests for the local reverse-proxy kit (``qa-kit/loadtest/local_proxy_server.py``).

Everything pinned here is a *policy* decision, not an implementation detail:

* the forwarding headers a WAF will actually see (append semantics, override, ``Via``);
* hop-by-hop stripping (including fields named by ``Connection:``);
* ``Location`` rewriting so the browser cannot be bounced out of the proxy;
* the scope gate: third-party platforms refused **even with** ``--i-am-authorized``.

No sockets, no browser, no aiohttp server: the pure functions are called directly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOADTEST_DIR = ROOT / "qa-kit" / "loadtest"


def _load_proxy_module() -> Any:
    spec = importlib.util.spec_from_file_location("proxy_server_under_test", LOADTEST_DIR / "local_proxy_server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["proxy_server_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def proxy() -> Any:
    return _load_proxy_module()


def _scope(proxy: Any, *patterns: str) -> Any:
    return proxy.Scope(patterns=tuple(patterns))


# ------------------------------------------------------------------------------- scope gate
def test_validate_upstream_accepts_loopback_and_normalises_trailing_slash(proxy: Any) -> None:
    scope = _scope(proxy, "127.0.0.1", "localhost", "*.sirketiniz.com")
    assert proxy.validate_upstream("http://127.0.0.1:8090/", scope, i_am_authorized=False) == "http://127.0.0.1:8090"
    assert (
        proxy.validate_upstream("https://staging.sirketiniz.com/", scope, i_am_authorized=False)
        == "https://staging.sirketiniz.com"
    )


def test_validate_upstream_refuses_third_party_even_when_authorized(proxy: Any) -> None:
    scope = _scope(proxy, "*")  # even a wide-open scope file must not unlock the block-list
    with pytest.raises(proxy.ProxyError, match="third-party offerwall"):
        proxy.validate_upstream("https://timewall.io/register", scope, i_am_authorized=True)
    with pytest.raises(proxy.ProxyError, match="third-party offerwall"):
        proxy.validate_upstream("https://app.jumptask.io", scope, i_am_authorized=True)
    with pytest.raises(proxy.ProxyError, match="third-party offerwall"):
        proxy.validate_upstream("https://cdn.timewall.io", scope, i_am_authorized=True)


def test_validate_upstream_requires_scope_or_explicit_authorization(proxy: Any) -> None:
    scope = _scope(proxy, "127.0.0.1")
    with pytest.raises(proxy.ProxyError, match="not covered by the scope file"):
        proxy.validate_upstream("https://staging.example-corp.com", scope, i_am_authorized=False)
    assert (
        proxy.validate_upstream("https://staging.example-corp.com", scope, i_am_authorized=True)
        == "https://staging.example-corp.com"
    )


def test_validate_upstream_rejects_empty_and_relative(proxy: Any) -> None:
    scope = _scope(proxy, "127.0.0.1")
    with pytest.raises(proxy.ProxyError, match="REAL_TARGET_URL is empty"):
        proxy.validate_upstream("", scope, i_am_authorized=False)
    with pytest.raises(proxy.ProxyError, match="absolute http"):
        proxy.validate_upstream("/register", scope, i_am_authorized=False)


# ---------------------------------------------------------------------------------- headers
def test_strip_hop_by_hop_removes_rfc7230_and_connection_named_fields(proxy: Any) -> None:
    headers = [
        ("Host", "127.0.0.1:8080"),
        ("Connection", "keep-alive, X-Custom-Hop"),
        ("X-Custom-Hop", "secret"),
        ("Keep-Alive", "timeout=5"),
        ("Transfer-Encoding", "chunked"),
        ("Content-Type", "application/x-www-form-urlencoded"),
        ("Cookie", "sid=abc"),
    ]
    kept = dict(proxy.strip_hop_by_hop(headers))
    assert "Connection" not in kept and "X-Custom-Hop" not in kept
    assert "Keep-Alive" not in kept and "Transfer-Encoding" not in kept
    assert kept["Cookie"] == "sid=abc" and kept["Content-Type"] == "application/x-www-form-urlencoded"


def test_build_forward_headers_appends_xff_and_adds_chain_headers(proxy: Any) -> None:
    headers = proxy.build_forward_headers(
        [("Host", "127.0.0.1:8080"), ("Cookie", "sid=abc"), ("X-Forwarded-For", "198.51.100.9")],
        client_ip="127.0.0.1",
        proxy_scheme="http",
    )
    assert headers["X-Forwarded-For"] == "198.51.100.9, 127.0.0.1"  # append, never replace
    assert headers["X-Forwarded-Proto"] == "http"
    assert headers["X-Forwarded-Host"] == "127.0.0.1:8080"
    assert headers["X-Real-IP"] == "127.0.0.1"
    assert headers["Via"] == "1.1 waft-local-proxy"
    assert headers["Cookie"] == "sid=abc"
    assert "Host" not in headers  # upstream host is set by the client session


def test_build_forward_headers_override_mode_replaces_xff(proxy: Any) -> None:
    headers = proxy.build_forward_headers(
        [("X-Forwarded-For", "198.51.100.9")],
        client_ip="127.0.0.1",
        proxy_scheme="http",
        xff_client="203.0.113.7",
    )
    assert headers["X-Forwarded-For"] == "203.0.113.7"


def test_build_forward_headers_keeps_existing_via_chain(proxy: Any) -> None:
    headers = proxy.build_forward_headers(
        [("Via", "1.1 other-proxy")], client_ip="10.0.0.5", proxy_scheme="https"
    )
    assert headers["Via"] == "1.1 other-proxy, 1.1 waft-local-proxy"
    assert headers["X-Forwarded-Proto"] == "https"


def test_describe_forwarding_only_reports_chain_headers(proxy: Any) -> None:
    described = proxy.describe_forwarding(
        {"X-Forwarded-For": "127.0.0.1", "Via": "1.1 waft-local-proxy", "Cookie": "sid=abc", "User-Agent": "x"}
    )
    assert described == {"X-Forwarded-For": "127.0.0.1", "Via": "1.1 waft-local-proxy"}


# ------------------------------------------------------------------------------- URL joining
def test_join_upstream_url_honours_prefix_and_query(proxy: Any) -> None:
    assert (
        proxy.join_upstream_url("http://127.0.0.1:8090", "/register", "a=1&b=2")
        == "http://127.0.0.1:8090/register?a=1&b=2"
    )
    assert proxy.join_upstream_url("http://127.0.0.1:8090/base/", "/register", "") == "http://127.0.0.1:8090/base/register"


def test_join_upstream_url_preserves_probe_like_paths_verbatim(proxy: Any) -> None:
    """Path traversal / double-slash payloads must reach the WAF exactly as sent (no normalisation)."""
    assert (
        proxy.join_upstream_url("http://127.0.0.1:8090", "/../etc/passwd", "")
        == "http://127.0.0.1:8090/../etc/passwd"
    )
    assert proxy.join_upstream_url("http://127.0.0.1:8090", "//admin//", "") == "http://127.0.0.1:8090//admin//"


# ---------------------------------------------------------------------------- location rewrite
def test_rewrite_location_maps_upstream_origin_back_to_proxy(proxy: Any) -> None:
    upstream, proxy_origin = "http://127.0.0.1:8090", "http://127.0.0.1:8080"
    assert (
        proxy.rewrite_location("http://127.0.0.1:8090/verify?token=abc", upstream, proxy_origin)
        == "http://127.0.0.1:8080/verify?token=abc"
    )
    assert proxy.rewrite_location("/verify?token=abc", upstream, proxy_origin) == "/verify?token=abc"
    # A redirect to a *different* host is a real behavioural signal - never silently rewritten.
    assert (
        proxy.rewrite_location("https://baska-sirket.com/x", upstream, proxy_origin) == "https://baska-sirket.com/x"
    )


def test_rewrite_location_case_insensitive_host(proxy: Any) -> None:
    rewritten = proxy.rewrite_location("http://STAGING.SIRKETINIZ.COM/x", "https://staging.sirketiniz.com", "http://127.0.0.1:8080")
    assert rewritten == "http://127.0.0.1:8080/x"


# ------------------------------------------------------------------------------------- misc
def test_proxy_cli_defaults_are_loopback(proxy: Any) -> None:
    args = proxy.build_parser().parse_args([])
    assert args.listen_host == "127.0.0.1" and args.listen_port == 8080
    assert args.rewrite_redirects is True and args.xff_client is None
    assert args.check is False


def test_run_proxy_test_cli_defaults_point_at_the_kit(proxy: Any) -> None:
    driver = importlib.util.spec_from_file_location("proxy_driver_under_test", LOADTEST_DIR / "run_proxy_test.py")
    assert driver is not None and driver.loader is not None
    module = importlib.util.module_from_spec(driver)
    sys.modules["proxy_driver_under_test"] = module
    driver.loader.exec_module(module)

    args = module.build_parser().parse_args([])
    assert args.proxy_url == "http://127.0.0.1:8080"
    assert args.targets.name == "test_targets.sandbox.json"
    assert args.spawn is True and args.contexts == 1
    module.resolve_proxy_endpoint(args)
    assert (args.listen_host, args.listen_port) == ("127.0.0.1", 8080)

    with pytest.raises(Exception, match="loopback"):
        module.resolve_proxy_endpoint(module.build_parser().parse_args(["--proxy-url", "https://example.com/"]))

@pytest.fixture(scope="module")
def driver() -> Any:
    """The QA driver, imported once (mirrors how the CLI imports it)."""
    spec = importlib.util.spec_from_file_location("proxy_driver_gate_test", LOADTEST_DIR / "run_proxy_test.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["proxy_driver_gate_test"] = module
    spec.loader.exec_module(module)
    return module


def test_driver_main_refuses_third_party_upstream(driver: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The exact commands from the "point it at timewall" plan must exit 2, flag or no flag."""
    monkeypatch.setenv("REAL_TARGET_URL", "https://timewall.io/register")
    assert driver.main(["--check", "--artifacts", str(tmp_path)]) == 2
    assert driver.main(["--check", "--i-am-authorized", "--artifacts", str(tmp_path)]) == 2


def test_driver_main_rejects_markdown_wrapped_url(driver: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REAL_TARGET_URL", "[https://timewall.io](https://timewall.io)")
    assert driver.main(["--check", "--artifacts", str(tmp_path)]) == 2
    monkeypatch.delenv("REAL_TARGET_URL", raising=False)
    assert driver.main(["--check", "--artifacts", str(tmp_path)]) == 2  # missing env var


def test_driver_main_accepts_loopback_upstream(driver: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REAL_TARGET_URL", "http://127.0.0.1:8090")
    assert driver.main(["--check", "--artifacts", str(tmp_path), "--targets", str(LOADTEST_DIR / "test_targets.sandbox.json")]) == 0
