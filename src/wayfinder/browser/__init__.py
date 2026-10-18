"""Live browser boundary. Harbor Line does not pass through this package."""

from wayfinder.browser.bridge import BrowserBridge, BrowserUnavailable

__all__ = ["BrowserBridge", "BrowserUnavailable"]
