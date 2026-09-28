"""Framework version metadata."""

from __future__ import annotations

__all__ = ["__version__", "VERSION_INFO", "FRAMEWORK_NAME"]

__version__ = "1.0.0"
VERSION_INFO = tuple(int(part) for part in __version__.split("."))
FRAMEWORK_NAME = "WAFT - Web Automation & Form-Test Framework"
