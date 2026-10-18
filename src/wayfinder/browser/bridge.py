"""Playwright browser bridge. Observations are evidence. Receipts are not success.

Playwright's sync API is bound to the thread that started it. FastAPI runs
sync handlers on a thread pool, so every Playwright call is marshalled onto
one dedicated worker thread.
"""

from __future__ import annotations

import functools
import http.server
import os
import queue
import threading
from pathlib import Path
from typing import Any, Callable

from wayfinder.browser.families import (
    PUBLIC_URL,
    TARGETS,
    PageObservation,
    failure_observation,
    observe_from_dom,
)
from wayfinder.models import ExecResult

PAGES = Path(__file__).resolve().parents[3] / "apps" / "pages"

OBSERVE_JS = """
() => {
  const clean = (value, limit) => (value || "").trim().replace(/\\s+/g, " ").slice(0, limit);
  const textOf = (el) => clean(el && (el.innerText || el.getAttribute("aria-label") || ""), 400);
  const main = document.querySelector("main") || document.body;
  const labelFor = (el) => {
    if (el.id) {
      const label = document.querySelector(`label[for="${el.id}"]`);
      if (label) return textOf(label);
    }
    return el.getAttribute("aria-label") || el.name || el.id || "field";
  };
  const isHidden = (el) => el.checkVisibility ? !el.checkVisibility() : Boolean(el.closest("[hidden]"));
  const sensitive = /password|passwd|card|cvv|cvc|ssn|secret|token|iban/i;
  const details = [...main.querySelectorAll("details")].map((el, index) => {
    const answerNode = [...el.children].find((node) => node.tagName !== "SUMMARY");
    return {
      index,
      summary: textOf(el.querySelector("summary")),
      answer: textOf(answerNode),
      open: Boolean(el.open),
      id: el.id || ""
    };
  });
  const links = [...main.querySelectorAll("a[href]")].map((el, index) => ({
    index,
    name: textOf(el),
    href: el.getAttribute("href") || "",
    path: el.pathname || "",
    same_origin: el.origin === location.origin,
    context: el.closest("li") ? clean(el.closest("li").innerText, 200) : "",
    hidden: isHidden(el),
    id: el.id || ""
  }));
  const buttons = [...main.querySelectorAll("button")].map((el, index) => ({
    index,
    name: textOf(el),
    type: (el.getAttribute("type") || "submit").toLowerCase(),
    disabled: Boolean(el.disabled),
    id: el.id || ""
  }));
  const fields = [...main.querySelectorAll("input:not([type='hidden']), textarea, select")].map((el, index) => {
    const type = (el.getAttribute("type") || el.tagName).toLowerCase();
    const name = labelFor(el);
    const secret = type === "password" || sensitive.test(name) || sensitive.test(el.name || "");
    return {
      index,
      name,
      type,
      value: secret ? "" : (el.value || ""),
      sensitive: secret,
      placeholder: secret ? "" : (el.getAttribute("placeholder") || ""),
      id: el.id || ""
    };
  });
  const headings = [...main.querySelectorAll("h1, h2, h3")].map((el, index) => ({
    index,
    level: el.tagName.toLowerCase(),
    text: textOf(el)
  }));
  const status = textOf(main.querySelector("[role='status']"));
  return {
    title: document.title,
    h1: textOf(main.querySelector("h1")) || document.title,
    purpose: textOf(main.querySelector("p")) || textOf(main.querySelector("h1")) || document.title,
    excerpt: textOf(main),
    fulltext: (main.innerText || "").replace(/[ \t]+/g, " ").slice(0, 6000),
    headings, details, links, buttons, fields, status,
    html: document.documentElement.outerHTML
  };
}
"""

CALL_TIMEOUT_S = 45
MAX_LIVE_PAGES = 4
FIELD_SELECTOR = "input:not([type='hidden']), textarea, select"


class BrowserUnavailable(RuntimeError):
    """Playwright or Chromium is not installed."""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


class _Worker:
    """Run callables on one daemon thread so Playwright never changes threads."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="wayfinder-browser", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            fn, args, kwargs, box, done = self._queue.get()
            try:
                box["value"] = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread
                box["error"] = exc
            finally:
                done.set()

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if threading.current_thread() is self._thread:
            return fn(*args, **kwargs)
        box: dict[str, Any] = {}
        done = threading.Event()
        self._queue.put((fn, args, kwargs, box, done))
        if not done.wait(CALL_TIMEOUT_S):
            raise TimeoutError("the browser did not answer in time")
        if "error" in box:
            raise box["error"]
        return box.get("value")


def public_url() -> str:
    return os.environ.get("WAYFINDER_PUBLIC_URL", PUBLIC_URL)


class BrowserBridge:
    def __init__(self) -> None:
        self._worker = _Worker()
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._playwright = None
        self._browser = None
        self._pages: dict[str, object] = {}
        self._failures: dict[str, PageObservation] = {}
        self._versions: dict[str, int] = {}
        self._trail: dict[str, list[str]] = {}
        self.adapter = "playwright"

    # ---- public API: every call runs on the worker thread ----

    def close(self) -> None:
        self._worker.call(self._close)

    def reset_pages(self) -> None:
        self._worker.call(self._reset_pages)

    def open(self, session_id: str, target: str) -> PageObservation:
        return self._worker.call(self._open, session_id, target)

    def observe(self, session_id: str) -> PageObservation:
        return self._worker.call(self._observe, session_id)

    def back(self, session_id: str) -> bool:
        return self._worker.call(self._back, session_id)

    def act(self, session_id: str, recipe: str, faults: set[str]) -> tuple[ExecResult, PageObservation]:
        return self._worker.call(self._act, session_id, recipe, faults)

    def forget(self, session_id: str) -> None:
        self._worker.call(self._forget, session_id)

    def current_url(self, session_id: str) -> str:
        return self._worker.call(self._current_url, session_id)

    # ---- worker-thread implementation ----

    def _close(self) -> None:
        self._reset_pages()
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def _reset_pages(self) -> None:
        for page in self._pages.values():
            try:
                page.context.close()
            except Exception:
                continue
        self._pages.clear()
        self._failures.clear()
        self._versions.clear()
        self._trail.clear()

    def _forget(self, session_id: str) -> None:
        page = self._pages.pop(session_id, None)
        self._failures.pop(session_id, None)
        self._versions.pop(session_id, None)
        self._trail.pop(session_id, None)
        if page is not None:
            try:
                page.context.close()
            except Exception:
                pass

    def _current_url(self, session_id: str) -> str:
        page = self._pages.get(session_id)
        return page.url if page is not None else ""

    def _open(self, session_id: str, target: str) -> PageObservation:
        if target not in TARGETS and target != "public":
            raise KeyError(target)
        page = self._page(session_id)
        self._failures.pop(session_id, None)
        self._versions.pop(session_id, None)
        self._trail.pop(session_id, None)
        if target == "public":
            url = public_url()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=8000)
            except Exception:
                observation = failure_observation(
                    url,
                    "The public page could not be read. No actions were invented. "
                    "A local FAQ, search, or form page can still be opened through the browser.",
                )
                self._failures[session_id] = observation
                return observation
        else:
            page.goto(self._local(target), wait_until="domcontentloaded", timeout=10000)
        return self._observe(session_id)

    def _observe(self, session_id: str) -> PageObservation:
        failed = self._failures.get(session_id)
        if failed is not None:
            return failed
        page = self._pages.get(session_id)
        if page is None:
            return failure_observation("about:blank", "No browser page is open.")
        raw = page.evaluate(OBSERVE_JS)
        version = self._versions.get(session_id, 0) + 1
        self._versions[session_id] = version
        trail = self._trail.setdefault(session_id, [])
        if not trail or trail[-1] != page.url:
            trail.append(page.url)
        return observe_from_dom(raw, page.url, version=version)

    def _back(self, session_id: str) -> bool:
        if session_id in self._failures:
            return False
        page = self._pages.get(session_id)
        if page is None:
            return False
        trail = self._trail.get(session_id, [])
        if len(trail) < 2:
            # The browser's own history may hold about:blank. Do not walk onto it.
            return False
        before = page.url
        try:
            page.go_back(wait_until="domcontentloaded", timeout=3000)
        except Exception:
            return False
        if page.url == before or page.url == "about:blank":
            return False
        trail.pop()
        return True

    def _act(self, session_id: str, recipe: str, faults: set[str]) -> tuple[ExecResult, PageObservation]:
        before = self._observe(session_id)
        if session_id in self._failures or recipe in {"read", "topics", "compare", "answer", "fields"}:
            return ExecResult(status="unchanged", changed=False), before
        if "selector_failure" in faults:
            return ExecResult(status="rejected", error_class="selector_not_found", changed=False), before
        if "verification_mismatch" in faults:
            # Claim a write without touching the page. The verifier must not accept it.
            return ExecResult(status="applied", changed=False), before
        page = self._pages[session_id]
        try:
            self._run(page, recipe)
            page.wait_for_timeout(40)
        except Exception:
            after = self._observe(session_id)
            return ExecResult(status="rejected", error_class="selector_not_found", changed=False), after
        after = self._observe(session_id)
        return ExecResult(status="applied", changed=after.stamp != before.stamp), after

    def _root(self, page):
        main = page.locator("main")
        return main.first if main.count() else page.locator("body")

    def _run(self, page, recipe: str) -> None:
        kind, _, rest = recipe.partition(":")
        root = self._root(page)
        if kind == "activate":
            root.locator("details").nth(int(rest)).locator("summary").first.click(timeout=3000)
            return
        if kind == "follow":
            root.locator("a[href]").nth(int(rest)).click(timeout=3000)
            page.wait_for_load_state("domcontentloaded")
            return
        if kind in {"search", "fill"}:
            index, _, value = rest.partition("=")
            field = root.locator(FIELD_SELECTOR).nth(int(index))
            field.fill(value, timeout=3000)
            if kind == "search":
                field.press("Enter")
            return
        if kind in {"filter", "submit"}:
            root.locator("button").nth(int(rest)).click(timeout=3000)
            return
        raise RuntimeError(f"unknown recipe {recipe}")

    def _page(self, session_id: str):
        existing = self._pages.get(session_id)
        if existing is not None:
            return existing
        browser = self._chromium()
        while len(self._pages) >= MAX_LIVE_PAGES:
            oldest = next(iter(self._pages))
            self._forget(oldest)
        context = browser.new_context()
        page = context.new_page()
        self._pages[session_id] = page
        return page

    def _chromium(self):
        if self._browser is not None:
            return self._browser
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable("playwright is not installed") from exc
        self._ensure_server()
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        except Exception as exc:
            raise BrowserUnavailable("Chromium could not be started") from exc
        return self._browser

    def _ensure_server(self) -> None:
        if self._server is not None:
            return
        handler = functools.partial(_QuietHandler, directory=str(PAGES))
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _local(self, target: str) -> str:
        self._ensure_server()
        assert self._server is not None
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/{TARGETS[target]}"
