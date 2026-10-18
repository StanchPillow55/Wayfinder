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
import time
from pathlib import Path
from typing import Any, Callable

from wayfinder.browser.families import (
    PUBLIC_URL,
    TARGETS,
    PageObservation,
    blocked_observation,
    detect_blocker,
    failure_observation,
    normalize_url,
    observe_from_dom,
    unreachable_message,
)
from wayfinder.models import ExecResult

PAGES = Path(__file__).resolve().parents[3] / "apps" / "pages"

# One definition of "which element is number N" serves both the read and the act, so the
# index in a recipe always points at the element that was described. Nothing is stamped
# onto the page.
COLLECT_JS = r"""
const wfScope = () => document.querySelector("main, [role='main']") || document.body;
const wfChromeSel = "nav, header, footer, aside, [role='navigation'], [role='banner'], [role='contentinfo'], [role='complementary']";
const wfInChrome = (el) => Boolean(el.closest(wfChromeSel));
const wfHidden = (el) => el.checkVisibility ? !el.checkVisibility() : Boolean(el.closest("[hidden]"));
const wfSearchSel = "input[type='search'], input[role='searchbox'], [role='search'] input:not([type='hidden']), search input:not([type='hidden'])";
const wfScoped = (main, sel) => [...main.querySelectorAll(sel)].filter((el) => main !== document.body || !wfInChrome(el));
const wfDisclosures = (main) => {
  const native = [...main.querySelectorAll("details")];
  const aria = [...main.querySelectorAll("button[aria-expanded][aria-controls], [role='button'][aria-expanded][aria-controls]")]
    .filter((el) => !wfInChrome(el) && document.getElementById(el.getAttribute("aria-controls")));
  return [...native, ...aria];
};
const wfFields = (main) => {
  const inside = wfScoped(main, "input:not([type='hidden']), textarea, select");
  const found = [...inside];
  for (const el of document.querySelectorAll(wfSearchSel)) if (!found.includes(el)) found.push(el);
  return found;
};
const wfButtons = (main) => wfScoped(main, "button, input[type='submit'], input[type='button']");
const wfLinks = (main) => [...main.querySelectorAll("a[href]")];
const wfPick = (kind, index) => {
  const main = wfScope();
  let el = null;
  if (kind === "disclosure") {
    el = wfDisclosures(main)[index];
    if (el && el.tagName === "DETAILS") el = el.querySelector("summary") || el;
  } else if (kind === "field") el = wfFields(main)[index];
  else if (kind === "button") el = wfButtons(main)[index];
  else if (kind === "link") el = wfLinks(main)[index];
  return el || null;
};
"""

OBSERVE_JS = "() => {" + COLLECT_JS + r"""
  const clean = (value, limit) => (value || "").trim().replace(/\s+/g, " ").slice(0, limit);
  const textOf = (el) => clean(el && (el.innerText || el.getAttribute("aria-label") || el.value || ""), 400);
  const main = wfScope();
  const labelFor = (el) => {
    if (el.id) {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label) return textOf(label);
    }
    const wrapping = el.closest("label");
    if (wrapping && textOf(wrapping)) return textOf(wrapping);
    return el.getAttribute("aria-label") || el.getAttribute("title") || el.name || el.id || "field";
  };
  // Accessibility repair (read-only). A control with no accessible name may get a suggested one
  // from nearby visible text. The suggestion is marked inferred and the live page is not changed.
  const accName = (el) => textOf(el) || clean(el.getAttribute("title"), 120)
    || clean((el.querySelector && el.querySelector("img[alt]") || {}).alt, 120)
    || clean(el.getAttribute("aria-labelledby") ? [...el.getAttribute("aria-labelledby").split(/\s+/)].map((id) => textOf(document.getElementById(id))).join(" ") : "", 120);
  const ownText = (el) => clean([...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" "), 80);
  const nearby = (el) => {
    const hint = (value) => clean(value, 60).replace(/[:\s]+$/, "");
    let node = el.parentElement;
    for (let depth = 0; node && depth < 3 && node !== document.body; depth += 1, node = node.parentElement) {
      if (!/^(LI|TR|TD|P|FIELDSET|LABEL|DIV|SECTION|ARTICLE|SPAN)$/.test(node.tagName)) continue;
      const clone = node.cloneNode(true);
      clone.querySelectorAll("a, button, input, select, textarea, svg, script, style").forEach((x) => x.remove());
      const text = clean(clone.innerText || clone.textContent, 140);
      if (text && text.length <= 100) return { name: hint(text), source: "nearby text" };
    }
    let prev = el.previousSibling;
    while (prev) {
      const text = prev.nodeType === 3 ? clean(prev.textContent, 60) : (prev.nodeType === 1 && !/^(INPUT|SELECT|TEXTAREA|BUTTON|A)$/.test(prev.tagName) ? clean(prev.innerText, 60) : "");
      if (text) return { name: hint(text), source: "nearby text" };
      prev = prev.previousSibling;
    }
    const block = el.closest("section, article, li, form, fieldset");
    const head = block && block.querySelector("h1, h2, h3, h4");
    if (head && textOf(head)) return { name: hint(textOf(head)), source: "nearby heading" };
    return null;
  };
  const humanPath = (href) => {
    const tail = (href || "").split("#")[0].split("?")[0].replace(/\/+$/, "").split("/").pop() || "";
    return clean(decodeURIComponent(tail).replace(/\.[a-z0-9]+$/i, "").replace(/[-_]+/g, " "), 60);
  };
  const sensitive = /password|passwd|passcode|card|cvv|cvc|ssn|secret|token|iban|routing.?number/i;
  const sensitiveAuto = /^(cc-|current-password|new-password|one-time-code)/i;
  const disclosures = wfDisclosures(main).map((el, index) => {
    if (el.tagName === "DETAILS") {
      const answerNode = [...el.children].find((node) => node.tagName !== "SUMMARY");
      return {
        index, kind: "details", summary: textOf(el.querySelector("summary")), answer: textOf(answerNode),
        open: Boolean(el.open), id: el.id || "", hidden: wfHidden(el)
      };
    }
    const panel = document.getElementById(el.getAttribute("aria-controls"));
    return {
      index, kind: "aria", summary: textOf(el), answer: panel && el.getAttribute("aria-expanded") === "true" ? textOf(panel) : "",
      open: el.getAttribute("aria-expanded") === "true", id: el.id || "", hidden: wfHidden(el)
    };
  });
  const links = wfLinks(main).map((el, index) => {
    let name = accName(el);
    let inferred = null;
    if (!name) {
      inferred = nearby(el) || (humanPath(el.getAttribute("href")) ? { name: humanPath(el.getAttribute("href")), source: "address" } : null);
      name = inferred ? inferred.name : "";
    }
    return {
    index,
    name,
    inferred: Boolean(inferred),
    name_source: inferred ? inferred.source : "",
    href: el.getAttribute("href") || "",
    path: el.pathname || "",
    abs: el.href || "",
    same_origin: el.origin === location.origin,
    context: el.closest("li") ? clean(el.closest("li").innerText, 200) : "",
    region: wfInChrome(el) ? "chrome" : "content",
    download: el.hasAttribute("download"),
    hidden: wfHidden(el),
    id: el.id || ""
  };});
  const buttons = wfButtons(main).map((el, index) => {
    let name = accName(el);
    const inferred = name ? null : nearby(el);
    if (inferred) name = inferred.name;
    return {
    index,
    name,
    inferred: Boolean(inferred),
    name_source: inferred ? inferred.source : "",
    type: (el.getAttribute("type") || (el.form ? "submit" : "button")).toLowerCase(),
    disabled: Boolean(el.disabled),
    hidden: wfHidden(el),
    id: el.id || ""
  };});
  const fields = wfFields(main).map((el, index) => {
    const tag = el.tagName.toLowerCase();
    let type = (el.getAttribute("type") || (tag === "input" ? "text" : tag)).toLowerCase();
    let name = labelFor(el);
    const labelled = Boolean((el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) || el.closest("label") || el.getAttribute("aria-label") || el.getAttribute("title"));
    let inferred = null;
    if (!labelled) {
      inferred = nearby(el);
      if (inferred) name = inferred.name;
    }
    const hay = `${el.name || ""} ${el.id || ""} ${el.getAttribute("placeholder") || ""} ${el.getAttribute("aria-label") || ""}`;
    const secret = type === "password" || sensitive.test(name) || sensitive.test(el.name || "") || sensitive.test(el.id || "")
      || sensitiveAuto.test(el.getAttribute("autocomplete") || "");
    const searchLike = type === "search" || el.getAttribute("role") === "searchbox"
      || ((tag === "input" || tag === "textarea") && ["text", "search", "textarea"].includes(type) && !secret
        && (Boolean(el.closest("[role='search'], search")) || /search/i.test(hay) || el.name === "q"));
    if (searchLike && !secret) type = "search";
    let value = secret ? "" : (el.value || "");
    let options = [];
    if (tag === "select") {
      options = [...el.options].slice(0, 40).map((option) => ({
        label: clean(option.label || option.textContent, 80), value: option.value, disabled: option.disabled, selected: option.selected
      }));
      const chosen = el.selectedOptions && el.selectedOptions[0];
      value = secret ? "" : (chosen && chosen.value !== "" ? clean(chosen.label || chosen.textContent, 80) : "");
    }
    return {
      index, name, type, value, options,
      inferred: Boolean(inferred),
      name_source: inferred ? inferred.source : "",
      sensitive: secret,
      field_name: el.name || "",
      region: wfInChrome(el) ? "chrome" : "content",
      placeholder: secret ? "" : (el.getAttribute("placeholder") || ""),
      hidden: wfHidden(el),
      id: el.id || ""
    };
  });
  const headings = [...main.querySelectorAll("h1, h2, h3")].filter((el) => !wfHidden(el)).map((el, index) => ({
    index,
    level: el.tagName.toLowerCase(),
    text: textOf(el)
  }));
  const roleOf = (el) => el.getAttribute("role") || ({ HEADER: "banner", NAV: "navigation", MAIN: "main", ASIDE: "complementary", FOOTER: "contentinfo", FORM: "form", SEARCH: "search", SECTION: "region" })[el.tagName] || el.tagName.toLowerCase();
  const landmarks = [...document.querySelectorAll("header, nav, main, aside, footer, form, search, [role='banner'], [role='navigation'], [role='main'], [role='search'], [role='complementary'], [role='contentinfo'], [role='form'], section[aria-label], [role='region'][aria-label]")]
    .filter((el) => !wfHidden(el)).slice(0, 14).map((el) => {
      const labelled = clean(el.getAttribute("aria-label"), 60) || textOf(document.getElementById(el.getAttribute("aria-labelledby") || "")) || textOf(el.querySelector("h1, h2, h3"));
      return { role: roleOf(el), label: clean(labelled, 60) };
    });
  const items = [...main.querySelectorAll("[data-wf-item]")].filter((el) => !wfHidden(el)).slice(0, 30).map((el, index) => ({
    index,
    name: clean(el.getAttribute("data-name") || textOf(el.querySelector("h1, h2, h3, h4, a, strong")), 80),
    text: textOf(el).slice(0, 240)
  }));
  const mirror = clean((main.closest("[data-wf-mirror]") || main.querySelector("[data-wf-mirror]") || document.body).getAttribute("data-wf-mirror"), 20);
  const status = textOf(main.querySelector("[role='status']"));
  const bodyText = (document.body.innerText || "").slice(0, 4000);
  const challenge = /verify (that )?you are (a )?human|are you a robot|checking your browser|unusual traffic from your|complete the security check|press (and|&) hold|needs to review the security of your connection/i;
  const widgetSel = "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='challenges.cloudflare.com'], .g-recaptcha, .h-captcha, .cf-turnstile";
  const widget = [...document.querySelectorAll(widgetSel)].some((el) => !wfHidden(el));
  const thin = bodyText.length < 1500;
  const signals = {
    captcha_widget: widget,
    challenge: thin && (challenge.test(bodyText) || /^(just a moment|attention required|are you a robot)/i.test(document.title) || widget)
  };
  return {
    title: document.title,
    h1: textOf(main.querySelector("h1")) || document.title,
    purpose: textOf(main.querySelector("p")) || textOf(main.querySelector("h1")) || document.title,
    excerpt: textOf(main),
    fulltext: (main.innerText || "").replace(/[ \t]+/g, " ").slice(0, 6000),
    headings, details: disclosures, links, buttons, fields, status, signals, landmarks, items, mirror,
    html: document.documentElement.outerHTML
  };
}
"""

ELEMENT_JS = "(arg) => {" + COLLECT_JS + "return wfPick(arg.kind, arg.index);}"

CALL_TIMEOUT_S = 45
MAX_LIVE_PAGES = 4
OPEN_TIMEOUT_MS = 15000
SETTLE_STEP_MS = 100
WRITE_RECIPES = {"activate", "follow", "search", "fill", "choose", "filter", "submit"}
READ_RECIPES = {"read", "topics", "compare", "answer", "fields"}


def change_timeout_ms() -> int:
    """How long to keep re-reading after an action before saying nothing changed."""
    try:
        return max(0, int(os.environ.get("WAYFINDER_CHANGE_TIMEOUT_MS", "1500")))
    except ValueError:
        return 1500


def _reason(exc: BaseException) -> str:
    text = str(exc)
    if "ERR_NAME_NOT_RESOLVED" in text:
        return "the address was not found"
    if "ERR_CONNECTION_REFUSED" in text:
        return "the connection was refused"
    if "ERR_UNSAFE_PORT" in text:
        return "the browser blocks that port"
    if "ERR_CERT" in text or "SSL" in text:
        return "its security certificate was not accepted"
    if "Timeout" in text or "ERR_TIMED_OUT" in text:
        return "it took too long to answer"
    if "Download is starting" in text:
        return "it is a file download, not a web page"
    return "the browser could not load it"


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
        self._status: dict[str, int] = {}
        self._ctype: dict[str, str] = {}
        self._focus: dict[str, int] = {}
        self._submitted: dict[str, str] = {}
        self._queried: dict[str, tuple[str, str]] = {}
        self.adapter = "playwright"

    # ---- public API: every call runs on the worker thread ----

    def close(self) -> None:
        self._worker.call(self._close)

    def reset_pages(self) -> None:
        self._worker.call(self._reset_pages)

    def open(self, session_id: str, target: str, url: str | None = None) -> PageObservation:
        return self._worker.call(self._open, session_id, target, url)

    def observe(self, session_id: str) -> PageObservation:
        return self._worker.call(self._observe, session_id)

    def back(self, session_id: str) -> bool:
        return self._worker.call(self._back, session_id)

    def act(self, session_id: str, recipe: str, faults: set[str]) -> tuple[ExecResult, PageObservation]:
        return self._worker.call(self._act, session_id, recipe, faults)

    def wait_for_change(self, session_id: str, stamp: str, timeout_ms: int | None = None) -> PageObservation:
        """Re-read until the page stamp differs from `stamp`, or the timeout passes."""
        return self._worker.call(self._settle, session_id, stamp, change_timeout_ms() if timeout_ms is None else timeout_ms)

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

    def _clear(self, session_id: str) -> None:
        self._failures.pop(session_id, None)
        self._versions.pop(session_id, None)
        self._trail.pop(session_id, None)
        self._status.pop(session_id, None)
        self._ctype.pop(session_id, None)
        self._focus.pop(session_id, None)
        self._submitted.pop(session_id, None)
        self._queried.pop(session_id, None)

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
        self._status.clear()
        self._ctype.clear()
        self._focus.clear()
        self._submitted.clear()
        self._queried.clear()

    def _forget(self, session_id: str) -> None:
        page = self._pages.pop(session_id, None)
        self._clear(session_id)
        if page is not None:
            try:
                page.context.close()
            except Exception:
                pass

    def _current_url(self, session_id: str) -> str:
        page = self._pages.get(session_id)
        return page.url if page is not None else ""

    def _open(self, session_id: str, target: str, url: str | None = None) -> PageObservation:
        if target not in TARGETS and target not in {"public", "url"}:
            raise KeyError(target)
        page = self._page(session_id)
        self._clear(session_id)
        if target == "url":
            return self._open_external(session_id, page, normalize_url(url))
        if target == "public":
            return self._open_external(session_id, page, public_url())
        page.goto(self._local(target), wait_until="domcontentloaded", timeout=10000)
        return self._observe(session_id)

    def _open_external(self, session_id: str, page, url: str) -> PageObservation:
        """Open a page from the network. Any failure is reported, never guessed around."""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=OPEN_TIMEOUT_MS)
        except Exception as exc:  # noqa: BLE001 - every network failure becomes an honest page
            observation = failure_observation(url, unreachable_message(url, _reason(exc)))
            self._failures[session_id] = observation
            return observation
        try:
            # Many pages build their content after the first paint. Give them a moment.
            page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass
        return self._observe(session_id)

    def _evaluate(self, page) -> dict:
        try:
            return page.evaluate(OBSERVE_JS)
        except Exception:
            # A navigation may have been in flight when the read started. Read once more.
            page.wait_for_load_state("domcontentloaded", timeout=5000)
            return page.evaluate(OBSERVE_JS)

    def _observe(self, session_id: str) -> PageObservation:
        failed = self._failures.get(session_id)
        if failed is not None:
            return failed
        page = self._pages.get(session_id)
        if page is None:
            return failure_observation("about:blank", "No browser page is open.")
        raw = self._evaluate(page)
        if raw.get("mirror") and not self._is_local(page.url):
            # The mirror marker is trusted only on pages this app serves. A website cannot claim it.
            raw["mirror"] = ""
        version = self._versions.get(session_id, 0) + 1
        self._versions[session_id] = version
        trail = self._trail.setdefault(session_id, [])
        if not trail or trail[-1] != page.url:
            trail.append(page.url)
        blocked = detect_blocker(raw, page.url, self._status.get(session_id, 0), self._ctype.get(session_id, ""))
        if blocked is not None:
            return blocked_observation(page.url, blocked[0], blocked[1], version=version)
        observation = observe_from_dom(raw, page.url, version=version, focus=self._focus.get(session_id))
        if not observation.status and self._submitted.get(session_id) == observation.stamp:
            # A page with no status area says nothing about a submit. The only honest evidence
            # is that this fresh read differs from the one before the confirmed submit.
            observation.facts["submitted"] = True
        queried = self._queried.get(session_id)
        if queried is not None and queried[0] == observation.stamp:
            observation.facts["query"] = queried[1]
        return observation

    def _settle(self, session_id: str, stamp: str, timeout_ms: int) -> PageObservation:
        """Re-read until the stamp changes or the timeout passes. Returns the last fresh read."""
        page = self._pages.get(session_id)
        deadline = time.monotonic() + timeout_ms / 1000
        observation = self._observe(session_id)
        waited = False
        while observation.stamp == stamp and page is not None and time.monotonic() < deadline:
            page.wait_for_timeout(SETTLE_STEP_MS)
            observation = self._observe(session_id)
            waited = True
        if waited and observation.stamp != stamp and page is not None:
            # The change just arrived. Read once more so a half-built page is not the evidence.
            page.wait_for_timeout(SETTLE_STEP_MS)
            observation = self._observe(session_id)
        return observation

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
        self._focus.pop(session_id, None)
        return True

    def _act(self, session_id: str, recipe: str, faults: set[str]) -> tuple[ExecResult, PageObservation]:
        before = self._observe(session_id)
        kind = recipe.partition(":")[0]
        if session_id in self._failures or before.blocker or recipe in READ_RECIPES or kind in READ_RECIPES:
            return ExecResult(status="unchanged", changed=False), before
        if "selector_failure" in faults:
            return ExecResult(status="rejected", error_class="selector_not_found", changed=False), before
        if "verification_mismatch" in faults:
            # Claim a write without touching the page. The verifier must not accept it.
            return ExecResult(status="applied", changed=False), before
        page = self._pages[session_id]
        try:
            self._run(page, session_id, recipe)
        except Exception:
            after = self._observe(session_id)
            return ExecResult(status="rejected", error_class="selector_not_found", changed=False), after
        if kind in {"follow", "search", "submit"}:
            try:
                # A navigation may be under way. Let the new document finish before it is read.
                page.wait_for_load_state("load", timeout=4000)
            except Exception:
                pass
        after = self._settle(session_id, before.stamp, change_timeout_ms()) if kind in WRITE_RECIPES else self._observe(session_id)
        if kind == "search":
            self._queried.pop(session_id, None)
            term = recipe.partition("=")[2].strip()
            seen = f"{after.url} {after.title} {after.h1}".lower()
            # A search that sends the person to another page leaves the box empty there. The
            # fresh page still has to be different and has to carry the term in its address or title.
            if term and after.stamp != before.stamp and not after.blocker and after.facts.get("query") != term:
                if term.lower() in seen or term.lower().replace(" ", "+") in seen or term.lower().replace(" ", "%20") in seen:
                    self._queried[session_id] = (after.stamp, term)
                    after.facts["query"] = term
        if kind == "submit":
            if after.stamp != before.stamp and not after.blocker:
                self._submitted[session_id] = after.stamp
                if not after.status:
                    after.facts["submitted"] = True
            else:
                self._submitted.pop(session_id, None)
        return ExecResult(status="applied", changed=after.stamp != before.stamp), after

    def _element(self, page, kind: str, index: int):
        handle = page.evaluate_handle(ELEMENT_JS, {"kind": kind, "index": index})
        element = handle.as_element()
        if element is None:
            raise LookupError(f"no {kind} number {index}")
        return element

    def _run(self, page, session_id: str, recipe: str) -> None:
        kind, _, rest = recipe.partition(":")
        if kind == "activate":
            self._focus[session_id] = int(rest)
            self._element(page, "disclosure", int(rest)).click(timeout=3000)
            return
        if kind == "follow":
            link = self._element(page, "link", int(rest))
            self._focus.pop(session_id, None)
            if (link.get_attribute("target") or "").lower() == "_blank":
                # A new tab would leave the page Wayfinder is tracking. Open it in this one.
                page.goto(link.evaluate("el => el.href"), wait_until="domcontentloaded", timeout=OPEN_TIMEOUT_MS)
            else:
                link.click(timeout=3000)
                page.wait_for_load_state("domcontentloaded")
            return
        if kind in {"search", "fill", "choose"}:
            index, _, value = rest.partition("=")
            field = self._element(page, "field", int(index))
            if field.evaluate("el => el.type === 'password'"):
                raise RuntimeError("passwords are never typed")
            if kind == "choose":
                chosen = field.select_option(label=value, timeout=3000)
                if not chosen:
                    raise LookupError(f"no option {value}")
                return
            field.fill(value, timeout=3000)
            if kind == "search":
                field.press("Enter")
            return
        if kind in {"filter", "submit"}:
            self._element(page, "button", int(rest)).click(timeout=3000)
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
        context = browser.new_context(accept_downloads=False, locale="en-US")
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.dismiss())
        page.on("response", lambda response, sid=session_id, p=page: self._note_response(sid, p, response))
        self._pages[session_id] = page
        return page

    def _note_response(self, session_id: str, page, response) -> None:
        """Remember how the last page document answered. Sub-resources are ignored."""
        try:
            if response.request.is_navigation_request() and response.frame == page.main_frame:
                self._status[session_id] = response.status
                self._ctype[session_id] = (response.headers.get("content-type") or "").lower()
        except Exception:
            return

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

    def _is_local(self, url: str) -> bool:
        if self._server is None:
            return False
        return url.startswith(f"http://127.0.0.1:{self._server.server_address[1]}/")

    def _local(self, target: str) -> str:
        self._ensure_server()
        assert self._server is not None
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/{TARGETS[target]}"
