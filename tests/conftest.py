"""Shared pytest fixtures.

The suite is split in two layers:

*   **Unit tests** (``test_unit_*.py``) — no browser, no network; they always run.
*   **Integration tests** (``test_integration_*.py``) — start the bundled demo web server and
    drive real Chromium contexts; they are skipped automatically when Playwright browser
    binaries are unavailable (``python -m playwright install chromium``).

Run everything::

    pytest -q
    pytest -q -m "not integration"     # unit only
"""

from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "examples"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))


def pytest_addoption(parser: pytest.Parser) -> None:  # pragma: no cover - pytest hook
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Force integration tests even when browsers look unavailable",
    )


def pytest_configure(config: pytest.Config) -> None:  # pragma: no cover - pytest hook
    config.addinivalue_line("markers", "integration: requires a real browser (Playwright binaries)")


def browsers_available() -> bool:
    """True when Playwright is importable *and* a Chromium binary is installed."""
    try:
        import playwright.async_api  # noqa: F401
    except Exception:  # noqa: BLE001
        return False

    from waft.config import _find_browser_binary  # noqa: PLC0415 - private helper, fine for tests

    return _find_browser_binary(("chrome", "chromium", "chrome-headless-shell"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:  # pragma: no cover
    if config.getoption("--run-integration") or browsers_available():
        return
    skip = pytest.mark.skip(reason="Playwright browser binaries not installed")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def demo_port() -> int:
    """Reserve a free TCP port for the demo server."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def demo_server(demo_port: int):
    """Start the WAFT demo web application in a background thread."""
    import threading

    from demo_site import build_server

    server = build_server(demo_port, quiet=True, host="127.0.0.1")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{demo_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def base_config(tmp_path: Path):
    """A minimal validated :class:`waft.config.Config` isolated to ``tmp_path``."""
    from waft.config import Config

    config = Config(
        contexts=1,
        concurrency=1,
        artifacts_dir=tmp_path / "artifacts",
        headless=True,
        proxy_mode="off",
        detect_captcha=True,
        humanize=False,
        save_storage_state=False,
        trace_mode="off",
        capture_har=False,
    )
    config.validate()
    return config


@pytest.fixture
def event_loop():  # pragma: no cover - pytest-asyncio compatibility shim
    """Yield a fresh event loop for async tests (no pytest-asyncio required)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def run_async(coro):
    """Run a coroutine in a dedicated event loop (helper for non-async test functions)."""
    return asyncio.run(coro)
