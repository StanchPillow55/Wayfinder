"""Turn a browser observation into a page family and grounded actions.

The rules look at structure: questions, a search box, or a form. They do not
encode a site or a business rule. Page text is data. It never becomes a goal,
a constraint, or an action.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from wayfinder.models import Control

INJECTION_MARKS = (
    "ignore previous instructions",
    "ignore all previous",
    "ignore the above",
    "disregard previous",
    "disregard all previous",
    "disregard the above",
    "new instructions:",
)
TARGETS = {
    "faq": "library-help.html",
    "search": "city-services.html",
    "form": "room-request.html",
    "unsupported": "market-poster.html",
    "shop": "shop.html",
    "jobs": "jobs.html",
    "unlabeled": "unlabeled.html",
}
# A public page is a best-effort extra. The browser may fail to reach it.
PUBLIC_URL = "https://www.w3.org/WAI/fundamentals/accessibility-intro/"

CONSEQUENTIAL_WORDS = ("pay", "buy", "purchase", "delete", "order", "donate", "checkout", "send")

FALLBACK_NOTE = "A local FAQ, search, or form page can still be opened through the browser."
# Blockers are structural: a network failure, an HTTP status, a sign-in form, or a challenge page.
BLOCKERS = ("unreachable", "login", "captcha", "http_error")

# Specialist agents (capabilities.py) own these families. A mirror page marks itself with
# data-wf-mirror, and the bridge trusts that marker only on pages this app serves.
# Adding a specialist: add a family here, a _plan_<family> below, and a capability entry.
SPECIALIST_FAMILIES = {"commerce": "commerce_agent", "jobs": "jobs_agent"}
INFERRED_NOTE = "Name inferred from nearby visible text. The live page was not changed."


def normalize_url(raw: str | None) -> str:
    """Accept only a plain http(s) URL. Anything else is refused before a browser sees it."""
    text = (raw or "").strip()
    if not text or len(text) > 2048 or any(ch.isspace() or ord(ch) < 32 for ch in text):
        raise ValueError("invalid_url")
    parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid_url")
    if parsed.username or parsed.password:
        # Credentials in a URL would end up in traces. Wayfinder never types secrets.
        raise ValueError("invalid_url")
    try:
        parsed.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError as exc:
        raise ValueError("invalid_url") from exc
    return text


@dataclass
class PageObservation:
    version: int
    url: str
    title: str
    h1: str
    purpose: str
    summary: str
    excerpt: str
    family: str
    supported: bool
    stamp: str
    headings: list[dict] = field(default_factory=list)
    details: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    buttons: list[dict] = field(default_factory=list)
    fields: list[dict] = field(default_factory=list)
    status: str = ""
    answer: str = ""
    topics: list[str] = field(default_factory=list)
    compare_pair: list[str] = field(default_factory=list)
    facts: dict[str, str | bool] = field(default_factory=dict)
    untrusted: list[str] = field(default_factory=list)
    html: str = ""
    error: str = ""
    blocker: str = ""
    notes: list[str] = field(default_factory=list)
    landmarks: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    mirror: str = ""
    base_family: str = ""
    fulltext: str = ""
    # Specialist capabilities the person has turned off. The orchestrator fills this per session.
    specialists_off: frozenset = frozenset()

    @property
    def repairs(self) -> list[dict]:
        """Controls whose accessible name was inferred. A read-only overlay, never a page edit."""
        found: list[dict] = []
        for kind, group in (("link", self.links), ("button", self.buttons), ("field", self.fields)):
            for item in group:
                if item.get("inferred") and item.get("name") and not item.get("hidden"):
                    found.append({
                        "kind": kind,
                        "ref": f"{kind}:{item.get('index')}",
                        "inferred_name": str(item.get("name"))[:80],
                        "source": str(item.get("name_source") or "nearby text"),
                        "sensitive": bool(item.get("sensitive")),
                        "note": INFERRED_NOTE,
                    })
        return found[:12]

    @property
    def controls(self) -> list[Control]:
        return plan(self)

    @property
    def evidence_ref(self) -> str:
        return f"browser:{self.url}#v{self.version}:{self.stamp}"

    def evidence(self) -> list[dict]:
        """A compact, bounded list of what the adapter actually read."""
        items: list[dict] = [{"kind": "title", "ref": "document.title", "text": self.title[:120]}]
        for item in self.headings[:8]:
            items.append({"kind": str(item.get("level")), "ref": f"heading:{item.get('index')}", "text": str(item.get("text"))[:120]})
        for item in self.details[:8]:
            state = "open" if item.get("open") else "closed"
            items.append({"kind": "question", "ref": f"details:{item.get('index')}", "text": f"{str(item.get('summary'))[:100]} ({state})"})
        for item in self.links[:10]:
            hidden = " (hidden)" if item.get("hidden") else ""
            guess = " (inferred name)" if item.get("inferred") else ""
            items.append({"kind": "link", "ref": f"link:{item.get('index')}", "text": f"{str(item.get('name'))[:80]}{guess} -> {item.get('href')}{hidden}"})
        for item in self.buttons[:6]:
            guess = " (inferred name)" if item.get("inferred") else ""
            items.append({"kind": "button", "ref": f"button:{item.get('index')}", "text": f"{str(item.get('name'))[:80]}{guess}"})
        for item in self.fields[:8]:
            value = "[redacted]" if item.get("sensitive") else str(item.get("value"))[:60]
            guess = " (inferred name)" if item.get("inferred") else ""
            items.append({"kind": f"field:{item.get('type')}", "ref": f"field:{item.get('index')}", "text": f"{str(item.get('name'))[:60]}{guess} = {value or 'empty'}"})
        if self.status:
            items.append({"kind": "status", "ref": "[role=status]", "text": self.status[:120]})
        return items


_DONE_WORDS = re.compile(r"submitted|\bsent\b|thank you|thanks|received|success|confirmed|saved|completed?\b|placed")
_FAIL_WORDS = re.compile(r"\bnot\b|n't|fail|error|invalid|required|try again|unable|denied")


def _status_says_done(status: str) -> bool:
    low = status.lower()
    return bool(low) and bool(_DONE_WORDS.search(low)) and not _FAIL_WORDS.search(low)


def _stamp(url: str, raw: dict) -> str:
    body = {
        "url": url,
        "title": raw.get("title", ""),
        "details": [(item.get("summary"), item.get("open")) for item in raw.get("details", [])],
        "fields": [(item.get("name"), item.get("value")) for item in raw.get("fields", [])],
        "links": [(item.get("name"), item.get("hidden")) for item in raw.get("links", [])],
        "status": raw.get("status", ""),
        "text": hashlib.sha256(str(raw.get("fulltext") or raw.get("excerpt") or "").encode()).hexdigest()[:16],
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return digest[:12]


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:40] or "item"


def _injection_sentences(text: str) -> list[str]:
    found: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        lowered = sentence.lower()
        if any(mark in lowered for mark in INJECTION_MARKS):
            found.append(sentence.strip()[:200])
    return found[:3]


def followable_links(observation: PageObservation) -> list[dict]:
    """Visible, same-origin links that leave the current page. Nothing else is offered."""
    return _followable(observation.url, observation.links)


def _followable(url: str, links: list[dict]) -> list[dict]:
    here = urlparse(url).path
    seen: set[str] = set()
    content: list[dict] = []
    chrome: list[dict] = []
    for link in links:
        href = str(link.get("href") or "")
        name = str(link.get("name") or "")
        if link.get("hidden") or not link.get("same_origin") or not name or len(name) > 100:
            continue
        if href.startswith(("#", "mailto:", "javascript:", "tel:")) or link.get("download"):
            continue
        path = str(link.get("path") or "")
        if not path or path == here:
            continue
        key = str(link.get("abs") or path).split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        # Links in the page body come before site navigation, so a long menu cannot crowd them out.
        (chrome if link.get("region") == "chrome" else content).append(link)
    return content + chrome


def observe_from_dom(raw: dict, url: str, *, version: int = 1, focus: int | None = None) -> PageObservation:
    # Hidden controls keep their index for the recipe but are never offered or described.
    details = [item for item in (raw.get("details") or []) if not item.get("hidden")]
    links = [item for item in (raw.get("links") or []) if item.get("name")]
    buttons = [item for item in (raw.get("buttons") or []) if not item.get("hidden")]
    fields = [item for item in (raw.get("fields") or []) if not item.get("hidden")]
    headings = list(raw.get("headings") or [])
    status = str(raw.get("status") or "")
    h1 = str(raw.get("h1") or raw.get("title") or "Untitled page")
    purpose = str(raw.get("purpose") or h1)
    excerpt = str(raw.get("excerpt") or purpose)
    search_fields = [item for item in fields if item.get("type") == "search"]
    text_fields = [
        item for item in fields
        if item.get("type") not in {"search", "hidden", "submit", "button", "checkbox", "radio", "file", "image", "reset", "range", "color"}
    ]
    has_submit = any(item.get("type") == "submit" for item in buttons)
    # A search box in the site header does not make the page a search page. The box has to be
    # part of the page body, or a search has to be active in the address.
    active_search = any(
        item.get("field_name") and parse_qs(urlparse(url).query).get(str(item.get("field_name")))
        for item in search_fields
    )
    if any(item.get("region") != "chrome" for item in search_fields) or active_search:
        family = "search"
    elif text_fields and has_submit:
        family = "form"
    elif len(details) >= 2:
        family = "faq"
    elif _followable(url, links):
        # A plain content page that links onward: read it, follow its links, go back.
        family = "docs"
    else:
        family = "unsupported"
    base_family = family
    mirror = str(raw.get("mirror") or "")
    if mirror in SPECIALIST_FAMILIES:
        family = mirror
    open_details = [item for item in details if item.get("open")]
    if focus is not None:
        # The question the person just opened leads, even if another one was already open.
        open_details.sort(key=lambda item: item.get("index") != focus)
    answer = str(open_details[0].get("answer") or "") if open_details else ""
    topics = [str(item.get("summary")) for item in details if item.get("summary")]
    if not topics:
        topics = [
            str(item.get("text")) for item in headings
            if str(item.get("level", "")).lower() in {"h2", "h3"} and item.get("text")
        ]
    if family == "form":
        topics = [str(item.get("name")) for item in text_fields]
    visible_links = sorted(
        (item for item in links if not item.get("hidden")),
        key=lambda item: item.get("region") == "chrome",
    )
    compare_pair = [str(item.get("name")) for item in visible_links[:2]]
    if len(compare_pair) < 2 and len(details) >= 2:
        compare_pair = [str(item.get("summary")) for item in details[:2]]
    page_items = [item for item in (raw.get("items") or []) if item.get("name")]
    if mirror in SPECIALIST_FAMILIES and len(page_items) >= 2:
        compare_pair = [str(item.get("name")) for item in page_items[:2]]
    if mirror in SPECIALIST_FAMILIES and page_items and base_family != "form":
        topics = [str(item.get("name")) for item in page_items] or topics
    query = str(search_fields[0].get("value") or "") if search_fields else ""
    if search_fields and not query:
        # A results page often empties the box and keeps the term in the address.
        param = str(search_fields[0].get("field_name") or "")
        values = parse_qs(urlparse(url).query).get(param) if param else None
        query = values[0] if values else ""
    facts: dict[str, str | bool] = {
        "expanded": str(open_details[0].get("summary") or "") if open_details else "",
        "query": query,
        "submitted": _status_says_done(status),
    }
    for item in fields:
        name = str(item.get("name") or "")
        if name and not item.get("sensitive"):
            facts[f"field:{name}"] = str(item.get("value") or "")
    untrusted = _injection_sentences(str(raw.get("fulltext") or excerpt))
    notes: list[str] = []
    if (raw.get("signals") or {}).get("captcha_widget"):
        notes.append("This page shows a CAPTCHA. Wayfinder cannot solve it, so a submission may be refused.")
    supported = family != "unsupported"
    if family == "faq":
        summary = f"{h1}. Topics: {', '.join(topics[:5]) or 'none'}."
    elif family == "search":
        names = ", ".join(str(item.get("name")) for item in visible_links[:5]) or "none"
        summary = f"{h1}. Results: {names}. Search text: {query or 'empty'}. {status}"
    elif family == "form":
        summary = f"{h1}. Fields: {', '.join(topics) or 'none'}. Status: {status.rstrip('.') or 'unknown'}."
    elif family == "docs":
        names = ", ".join(str(item.get("name")) for item in _followable(url, links)[:3])
        sections = [
            str(item.get("text")) for item in headings
            if str(item.get("level", "")).lower() in {"h2", "h3"} and item.get("text")
        ][:6]
        summary = f"{h1}. {purpose.rstrip('.')}."
        if len(sections) >= 2:
            facts["sections"] = "; ".join(sections)
            summary += f" Sections: {', '.join(sections)}."
        summary += f" Links: {names}."
    elif family in SPECIALIST_FAMILIES:
        raw_items = [item for item in (raw.get("items") or []) if item.get("name")]
        names = ", ".join(str(item.get("name")) for item in raw_items[:5])
        noun = "Products" if family == "commerce" else "Roles"
        summary = f"{h1}. {purpose.rstrip('.')}."
        if raw_items:
            summary += f" {noun if len(raw_items) > 1 else 'Item'}: {names}."
        if status:
            summary += f" Status: {status.rstrip('.')}."
        if base_family == "form" and not names:
            summary += f" Fields: {', '.join(topics)}."
    else:
        summary = (
            f"This page is not a supported FAQ, search, or form. Title: {h1}. "
            "No actions were invented and the page was not changed."
        )
    return PageObservation(
        version=version,
        url=url,
        title=str(raw.get("title") or h1),
        h1=h1,
        purpose=purpose,
        summary=summary.strip(),
        excerpt=excerpt,
        family=family,
        supported=supported,
        stamp=_stamp(url, raw),
        headings=headings,
        details=details,
        links=links,
        buttons=buttons,
        fields=fields,
        status=status,
        answer=answer,
        topics=topics,
        compare_pair=compare_pair,
        facts=facts,
        untrusted=untrusted,
        html=str(raw.get("html") or "")[:60000],
        notes=notes,
        landmarks=list(raw.get("landmarks") or []),
        items=page_items[:12],
        mirror=mirror if mirror in SPECIALIST_FAMILIES else "",
        base_family=base_family,
        fulltext=str(raw.get("fulltext") or excerpt)[:6000],
    )


def failure_observation(url: str, message: str, blocker: str = "unreachable") -> PageObservation:
    raw = {
        "title": "Page unavailable",
        "h1": "Page unavailable",
        "purpose": message,
        "excerpt": message,
        "fulltext": message,
        "status": message,
        "details": [],
        "links": [],
        "buttons": [],
        "fields": [],
        "headings": [],
        "html": f"<main><h1>Page unavailable</h1><p>{message}</p></main>",
    }
    observation = observe_from_dom(raw, url)
    observation.error = message
    observation.blocker = blocker
    observation.supported = False
    observation.family = "unsupported"
    observation.summary = message
    return observation


def _host(url: str) -> str:
    return urlparse(url).hostname or "that address"


def unreachable_message(url: str, reason: str = "") -> str:
    detail = f" ({reason})" if reason else ""
    return f"The page at {_host(url)} could not be read{detail}. No actions were invented. {FALLBACK_NOTE}"


def detect_blocker(raw: dict, url: str, http_status: int = 0, content_type: str = "") -> tuple[str, str] | None:
    """Say honestly when a page cannot be used. Returns (blocker, message) or None.

    Structure only: an HTTP status, a sign-in-shaped form, or a bot-challenge interstitial.
    Nothing here knows a site by name.
    """
    host = _host(url)
    signals = raw.get("signals") or {}
    if content_type and "html" not in content_type:
        kind = content_type.split(";")[0].strip()
        return "http_error", (
            f"The address at {host} is not a web page ({kind}). No actions were invented. {FALLBACK_NOTE}"
        )
    if http_status in {401, 407}:
        return "login", (
            f"The page at {host} needs you to sign in. Wayfinder does not enter passwords, so it left the page "
            f"unchanged. No actions were invented. {FALLBACK_NOTE}"
        )
    if http_status == 403 or http_status == 429:
        return "http_error", (
            f"The site at {host} refused this read (HTTP {http_status}). It may block automated browsers or need "
            f"a sign-in. No actions were invented. {FALLBACK_NOTE}"
        )
    if http_status >= 400:
        return "http_error", (
            f"The page at {host} answered with an error (HTTP {http_status}). No actions were invented. {FALLBACK_NOTE}"
        )
    if signals.get("challenge"):
        return "captcha", (
            f"The page at {host} is asking to verify that you are human, such as a CAPTCHA or browser check. "
            f"Wayfinder cannot complete that, so it left the page unchanged. No actions were invented. {FALLBACK_NOTE}"
        )
    fields = [item for item in (raw.get("fields") or []) if not item.get("hidden")]
    passwords = [item for item in fields if item.get("type") == "password"]
    others = [
        item for item in fields
        if item.get("type") not in {"password", "hidden", "submit", "button", "checkbox", "radio", "search"}
    ]
    if passwords and len(others) <= 1:
        return "login", (
            f"The page at {host} is a sign-in page. Wayfinder does not enter passwords or sign in, so it left the "
            f"page unchanged. No actions were invented. {FALLBACK_NOTE}"
        )
    return None


def blocked_observation(url: str, blocker: str, message: str, *, version: int = 1) -> PageObservation:
    observation = failure_observation(url, message, blocker)
    observation.version = version
    return observation


def _control(
    *,
    stamp: str,
    control_id: str,
    label: str,
    rationale: str,
    capability: str,
    risk: str,
    recipe: str,
    expected: dict[str, str | bool],
    preview: str,
    priority: int,
) -> Control:
    return Control(
        id=control_id,
        label=label,
        rationale=rationale,
        capability=capability,
        risk_class=risk,  # type: ignore[arg-type]
        semantic_target=f"{stamp}|{recipe}",
        expected_postcondition=expected,
        informational=capability.startswith("reader."),
        preview=preview,
        priority=priority,
    )


def _shown(item: dict, text: str | None = None) -> str:
    """A control's spoken name. An inferred name says so, so nobody mistakes a guess for the page's own label."""
    name = text if text is not None else str(item.get("name"))
    return f"{name} (inferred name)" if item.get("inferred") else name


def _follow_control(stamp: str, link: dict, *, priority: int, noun: str) -> Control:
    name = str(link.get("name"))
    path = str(link.get("path") or "")
    tail = path.rstrip("/").split("/")[-1] or path
    query = urlparse(str(link.get("abs") or "")).query
    if query:
        tail = f"{tail}?{query}"
    guess = bool(link.get("inferred"))
    return _control(
        stamp=stamp,
        control_id=f"open-{noun}-{_slug(name)}",
        label=f"Open {_shown(link, name[:80])}",
        rationale=(
            f"Shown because this {noun} is on the page. Its name was {INFERRED_NOTE.lower()}"
            if guess
            else f"Shown because this {noun} is on the page."
        ),
        capability="browser.navigate",
        risk="reversible_write",
        recipe=f"follow:{link.get('index')}",
        expected={"url_has": tail},
        preview=f"This follows the {noun} {name}.",
        priority=priority,
    )


def plan(observation: PageObservation) -> list[Control]:
    stamp = observation.stamp
    controls = [
        _control(
            stamp=stamp,
            control_id="explain-page",
            label="Explain this page",
            rationale="Shown because the page has a title and readable text.",
            capability="reader.explain",
            risk="read",
            recipe="read",
            expected={"explained": True},
            preview=f"This reads {observation.h1} without changing the page.",
            priority=80,
        )
    ]
    if not observation.supported:
        return controls
    family = observation.family
    if family in SPECIALIST_FAMILIES and SPECIALIST_FAMILIES[family] in observation.specialists_off:
        family = observation.base_family or "docs"
    if family == "commerce":
        controls.extend(_plan_commerce(observation))
    elif family == "jobs":
        controls.extend(_plan_jobs(observation))
    elif family == "faq":
        controls.extend(_plan_faq(observation))
    elif family == "search":
        controls.extend(_plan_search(observation))
    elif family == "form":
        controls.extend(_plan_form(observation))
        for order, link in enumerate(followable_links(observation)[:2]):
            controls.append(_follow_control(stamp, link, priority=52 - order, noun="link"))
    elif family == "docs":
        if observation.facts.get("sections"):
            controls.append(
                _control(
                    stamp=stamp,
                    control_id="list-sections",
                    label="List the sections",
                    rationale="Shown because this page has several headed sections.",
                    capability="reader.topics",
                    risk="read",
                    recipe="topics",
                    expected={"sections": str(observation.facts["sections"])},
                    preview="This reads the section headings without leaving the page.",
                    priority=66,
                )
            )
        for order, link in enumerate(followable_links(observation)[:3]):
            controls.append(_follow_control(stamp, link, priority=58 - order, noun="link"))
    choose = _plan_choose(observation)
    if choose is not None:
        controls.append(choose)
    return controls


def _choices(item: dict) -> list[dict]:
    """Real options a person could pick. A blank-valued option is a placeholder, not a choice."""
    return [
        option for option in (item.get("options") or [])
        if not option.get("disabled") and str(option.get("value") or "").strip() and str(option.get("label") or "").strip()
    ]


def match_option(item: dict, wanted: str) -> dict | None:
    """Find the option the user named. An unknown or ambiguous name matches nothing."""
    target = re.sub(r"[^a-z0-9]+", " ", wanted.lower()).strip()
    if not target:
        return None
    choices = _choices(item)
    exact = [
        option for option in choices
        if target in {
            re.sub(r"[^a-z0-9]+", " ", str(option.get("label")).lower()).strip(),
            re.sub(r"[^a-z0-9]+", " ", str(option.get("value")).lower()).strip(),
        }
    ]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    partial = [
        option for option in choices
        if target in re.sub(r"[^a-z0-9]+", " ", str(option.get("label")).lower())
    ]
    return partial[0] if len(partial) == 1 else None


def _choose_control(stamp: str, item: dict, option: dict, *, control_id: str, priority: int, typed: bool) -> Control:
    name = str(item.get("name") or "field")
    label = str(option.get("label"))
    return _control(
        stamp=stamp,
        control_id=control_id,
        label=f"Choose {label[:60]} for {name[:40]}",
        rationale="You asked for this option and the list has it." if typed else "Shown because this option is in the page's list.",
        capability="browser.fill",
        risk="reversible_write",
        recipe=f"choose:{item.get('index')}={label}",
        expected={f"field:{name}": label},
        preview=f"This picks {label} in the list {name}. It does not submit the form.",
        priority=priority,
    )


def _plan_choose(observation: PageObservation) -> Control | None:
    for item in observation.fields:
        if item.get("type") != "select" or item.get("sensitive"):
            continue
        selected = str(item.get("value") or "")
        option = next((o for o in _choices(item) if str(o.get("label")) != selected), None)
        if option is not None:
            return _choose_control(
                observation.stamp,
                item,
                option,
                control_id=f"choose-{_slug(str(item.get('name')))}-{_slug(str(option.get('label')))}",
                priority=63,
                typed=False,
            )
    return None


def _plan_faq(observation: PageObservation) -> list[Control]:
    stamp = observation.stamp
    controls = [
        _control(
            stamp=stamp,
            control_id="list-topics",
            label="List the questions",
            rationale="Shown because this page has several questions or topics.",
            capability="reader.topics",
            risk="read",
            recipe="topics",
            expected={"listed": True},
            preview="This lists the questions on the page without opening one.",
            priority=40 if observation.answer else 70,
        )
    ]
    closed = [item for item in observation.details if not item.get("open")][:2]
    for order, item in enumerate(closed):
        summary = str(item.get("summary") or "question")
        controls.append(
            _control(
                stamp=stamp,
                control_id=f"open-{_slug(summary)}",
                label=f"Open {summary}",
                rationale="Shown because this question is on the page.",
                capability="browser.navigate",
                risk="reversible_write",
                recipe=f"activate:{item.get('index')}",
                expected={"expanded": summary},
                preview=f"This opens the question “{summary}”.",
                priority=60 - order,
            )
        )
    if observation.answer:
        controls.append(
            _control(
                stamp=stamp,
                control_id="read-answer",
                label="Read the open answer",
                rationale="Shown because a question on this page is open.",
                capability="reader.answer",
                risk="read",
                recipe="answer",
                expected={"answer_has": observation.answer},
                preview="This reads the answer that is already open.",
                priority=75,
            )
        )
    links = followable_links(observation)
    if links:
        controls.append(_follow_control(stamp, links[0], priority=58, noun="link"))
    return controls


_LABEL_WORDS = re.compile(
    r"search|find|type|enter|your|please|select|choose|optional|required|e\.g|example|"
    r"email|e-mail|phone|password|address|number|first name|last name|full name|@|\d{3}|\.\.\.|…|_{2,}|x{3,}",
    re.IGNORECASE,
)


def example_value(placeholder: object, label: object = "") -> str:
    """A placeholder is a usable suggestion only when it reads like a sample value.

    Hints such as "Search the site" or "Your email" are labels, not values, so they are
    never turned into an action.
    """
    text = re.sub(r"\s+", " ", str(placeholder or "")).strip()
    if not text or len(text) > 40 or len(text.split()) > 4 or _LABEL_WORDS.search(text):
        return ""
    if re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() == re.sub(r"[^a-z0-9]+", " ", str(label or "").lower()).strip():
        return ""
    return text


def _plan_search(observation: PageObservation) -> list[Control]:
    stamp = observation.stamp
    controls: list[Control] = []
    search = next((item for item in observation.fields if item.get("type") == "search"), None)
    query = str(observation.facts.get("query") or "")
    sample = example_value((search or {}).get("placeholder"), (search or {}).get("name"))
    if search and sample and sample != query:
        controls.append(
            _control(
                stamp=stamp,
                control_id=f"search-{_slug(sample)}",
                label=f"Search for {sample}",
                rationale="Shown because the search box suggests this query.",
                capability="browser.fill",
                risk="reversible_write",
                recipe=f"search:{search.get('index')}={sample}",
                expected=(
                    {"query": sample, "status_has": sample} if observation.status else {"query": sample}
                ),
                preview=f"This types {sample} into the search box and runs the search.",
                priority=70,
            )
        )
    if search and query:
        controls.append(
            _control(
                stamp=stamp,
                control_id="clear-search",
                label="Clear the search",
                rationale="Shown because a search is active.",
                capability="browser.fill",
                risk="reversible_write",
                recipe=f"search:{search.get('index')}=",
                expected={"query": ""},
                preview="This empties the search box and shows every result again.",
                priority=62,
            )
        )
    # A filter is offered only when the page announces results in a status area, so the
    # verifier has something on the page to check afterwards.
    filter_button = next(
        (
            item for item in observation.buttons
            if observation.status
            and item.get("type") == "button"
            and any(word in str(item.get("name", "")).lower() for word in ("only", "filter"))
        ),
        None,
    )
    if filter_button:
        name = str(filter_button.get("name"))
        controls.append(
            _control(
                stamp=stamp,
                control_id=f"filter-{_slug(name)}",
                label=_shown(filter_button, name),
                rationale="Shown because this filter button is on the page.",
                capability="browser.filter",
                risk="reversible_write",
                recipe=f"filter:{filter_button.get('index')}",
                expected={"status_has": "open today" if "open today" in name.lower() else name.lower()},
                preview=f"This applies the page control “{name}”.",
                priority=65,
            )
        )
    if len(observation.compare_pair) >= 2:
        first, second = observation.compare_pair[:2]
        controls.append(
            _control(
                stamp=stamp,
                control_id="compare-results",
                label=f"Compare {first} and {second}",
                rationale="Shown because at least two results are visible.",
                capability="reader.compare",
                risk="read",
                recipe="compare",
                expected={"compared": True},
                preview=f"This compares {first} and {second} without opening them.",
                priority=55,
            )
        )
    for order, link in enumerate(followable_links(observation)[:2]):
        controls.append(_follow_control(stamp, link, priority=50 - order, noun="result"))
    return controls


def _plan_form(observation: PageObservation) -> list[Control]:
    stamp = observation.stamp
    controls = [
        _control(
            stamp=stamp,
            control_id="inspect-fields",
            label="Inspect the fields",
            rationale="Shown because this page has a form.",
            capability="reader.fields",
            risk="read",
            recipe="fields",
            expected={"listed": True},
            preview="This reads the field names without filling them.",
            priority=70,
        )
    ]
    sample_field = next(
        (
            item for item in observation.fields
            if item.get("type") in {"text", "textarea", "search"}
            and example_value(item.get("placeholder"), item.get("name"))
            and str(item.get("value") or "") != example_value(item.get("placeholder"), item.get("name"))
            and not item.get("sensitive")
        ),
        None,
    )
    if sample_field:
        sample = example_value(sample_field.get("placeholder"), sample_field.get("name"))
        name = str(sample_field.get("name") or "field")
        controls.append(
            _control(
                stamp=stamp,
                control_id=f"fill-{_slug(name)}",
                label=f"Fill {name} with {sample}",
                rationale="Shown because the page itself suggests this value.",
                capability="browser.fill",
                risk="reversible_write",
                recipe=f"fill:{sample_field.get('index')}={sample}",
                expected={f"field:{name}": sample},
                preview=f"This types {sample} into {name}. It does not submit the form.",
                priority=65,
            )
        )
    submit = next((item for item in observation.buttons if item.get("type") == "submit" and not item.get("disabled")), None)
    if submit:
        values = {
            str(item.get("name")): str(item.get("value") or "")
            for item in observation.fields
            if item.get("type") not in {"search", "hidden", "submit", "button"} and not item.get("sensitive")
        }
        described = " ".join(
            f"{name} {'says ' + value if value else 'is empty'}." for name, value in values.items()
        )
        if observation.notes:
            described = f"{described} {' '.join(observation.notes)}".strip()
        expected: dict[str, str | bool] = {"submitted": True}
        if observation.status:
            # With a status area the page is expected to stay put, so the typed values should
            # still read back. Without one, a successful submit usually leaves the page.
            for name, value in values.items():
                if value:
                    expected[f"field:{name}"] = value
        controls.append(
            _control(
                stamp=stamp,
                control_id="submit-form",
                label=_shown(submit, str(submit.get("name") or "Submit")),
                rationale="Shown because the form has a submit button. Submission needs confirmation.",
                capability="browser.submit",
                risk="consequential",
                recipe=f"submit:{submit.get('index')}",
                expected=expected,
                preview=f"This submits the form. {described}".strip(),
                priority=45,
            )
        )
    return controls


_ADD_BUTTON = re.compile(r"^\s*add\b", re.IGNORECASE)
_CART_LINK = re.compile(r"cart|checkout|basket|review", re.IGNORECASE)
_APPLY_LINK = re.compile(r"appl(y|ication)", re.IGNORECASE)


def _bump(controls: list[Control], control_id: str, priority: int) -> list[Control]:
    return [control.model_copy(update={"priority": priority}) if control.id == control_id else control for control in controls]


def _list_items_control(observation: PageObservation, noun: str) -> Control | None:
    if not observation.items:
        return None
    return _control(
        stamp=observation.stamp,
        control_id=f"list-{noun}",
        label=f"List the {noun}",
        rationale=f"Shown because this page lists {noun}.",
        capability="reader.topics",
        risk="read",
        recipe="topics",
        expected={"items_listed": True},
        preview=f"This reads the {noun} on the page without opening one.",
        priority=70,
    )


def _compare_items_control(observation: PageObservation, noun: str) -> Control | None:
    if len(observation.compare_pair) < 2:
        return None
    first, second = observation.compare_pair[:2]
    return _control(
        stamp=observation.stamp,
        control_id=f"compare-{noun}",
        label=f"Compare {first} and {second}",
        rationale=f"Shown because at least two {noun} are visible.",
        capability="reader.compare",
        risk="read",
        recipe="compare",
        expected={"compared": True},
        preview=f"This compares {first} and {second} without opening them.",
        priority=64,
    )


def add_controls(observation: PageObservation) -> list[Control]:
    """One reversible 'add to cart' control per add button. Used by the shop planner and by goals."""
    found: list[Control] = []
    for item in observation.buttons:
        name = str(item.get("name"))
        if not _ADD_BUTTON.match(name) or item.get("disabled") or item.get("type") == "submit":
            continue
        product = re.sub(r"\s+to\s+(?:the\s+)?cart\s*$", "", re.sub(r"^\s*add\s+", "", name, flags=re.IGNORECASE), flags=re.IGNORECASE).strip() or "item"
        found.append(
            _control(
                stamp=observation.stamp,
                control_id=f"add-{_slug(product)}",
                label=_shown(item, name),
                rationale="Shown because this page has an add button. Adding changes the demo cart only. No payment exists here.",
                capability="browser.filter",
                risk="reversible_write",
                recipe=f"filter:{item.get('index')}",
                expected={"status_has": f"added {product}"},
                preview=f"This adds {product} to the demo cart. It does not check out and nothing is paid.",
                priority=68,
            )
        )
    return found


def _plan_commerce(observation: PageObservation) -> list[Control]:
    """Local shop mirror: browse, compare, add, review. Adding is reversible. Checkout waits for yes."""
    stamp = observation.stamp
    controls: list[Control] = []
    listed = _list_items_control(observation, "products")
    if listed:
        controls.append(listed)
    adds = add_controls(observation)
    if adds:
        controls.append(adds[0])
    page_filter = next(
        (
            item for item in observation.buttons
            if observation.status and item.get("type") == "button" and not item.get("disabled")
            and any(word in str(item.get("name", "")).lower() for word in ("only", "filter"))
        ),
        None,
    )
    if page_filter:
        name = str(page_filter.get("name"))
        controls.append(
            _control(
                stamp=stamp,
                control_id=f"filter-{_slug(name)}",
                label=_shown(page_filter, name),
                rationale="Shown because this filter button is on the page.",
                capability="browser.filter",
                risk="reversible_write",
                recipe=f"filter:{page_filter.get('index')}",
                expected={"status_has": name.lower()},
                preview=f"This applies the page control “{name}”. It does not add anything to the cart.",
                priority=71,
            )
        )
    compare = _compare_items_control(observation, "products")
    if compare:
        controls.append(compare)
    cart_link = next((link for link in followable_links(observation) if _CART_LINK.search(str(link.get("name")))), None)
    if cart_link:
        controls.append(_follow_control(stamp, cart_link, priority=66, noun="link"))
    others = [link for link in followable_links(observation) if link is not cart_link]
    for order, link in enumerate(others[:2]):
        controls.append(_follow_control(stamp, link, priority=67 - order * 9, noun="product" if observation.items else "link"))
    if observation.fields and any(item.get("type") == "submit" for item in observation.buttons):
        controls.extend(_bump(_plan_form(observation), "submit-form", 69))
    return controls


def _plan_jobs(observation: PageObservation) -> list[Control]:
    """Local jobs mirror: search roles, open one, review an application. Sending waits for yes."""
    stamp = observation.stamp
    controls: list[Control] = []
    if any(item.get("type") == "search" for item in observation.fields):
        controls.extend(
            control for control in _plan_search(observation)
            if control.id != "compare-results" and not control.id.startswith("open-result-")
        )
    listed = _list_items_control(observation, "roles")
    if listed:
        controls.append(listed)
    compare = _compare_items_control(observation, "roles")
    if compare:
        controls.append(compare)
    links = followable_links(observation)
    apply_link = next((link for link in links if _APPLY_LINK.search(str(link.get("name")))), None)
    if apply_link:
        controls.append(_follow_control(stamp, apply_link, priority=67, noun="link"))
    for order, link in enumerate([link for link in links if link is not apply_link][:2]):
        controls.append(_follow_control(stamp, link, priority=66 - order * 8, noun="role" if observation.items else "link"))
    if observation.fields and any(item.get("type") == "submit" for item in observation.buttons) and not any(
        item.get("type") == "search" for item in observation.fields
    ):
        controls.extend(_bump(_plan_form(observation), "submit-form", 69))
    return controls


def field_by_name(observation: PageObservation, name: str) -> dict | None:
    """Find a real, non-sensitive text field by its label. Used for typed entry."""
    wanted = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    if not wanted:
        return None
    best: dict | None = None
    for item in observation.fields:
        if item.get("sensitive") or item.get("type") in {"hidden", "submit", "button", "checkbox", "radio"}:
            continue
        label = re.sub(r"[^a-z0-9]+", " ", str(item.get("name")).lower()).strip()
        if label == wanted:
            return item
        if wanted == "search" and item.get("type") == "search":
            # "search for X" means the page's search box, whatever its label says.
            return item
        attribute = re.sub(r"[^a-z0-9]+", " ", str(item.get("field_name") or "").lower()).strip()
        if attribute and attribute == wanted:
            return item
        if wanted in label or label in wanted:
            best = best or item
    return best


def entry_control(observation: PageObservation, name: str, value: str) -> Control | None:
    """Ground a spoken 'set X to Y' in a real field. Returns None when no field matches."""
    item = field_by_name(observation, name)
    if item is None or not observation.supported:
        return None
    value = value.strip()[:120]
    label = str(item.get("name"))
    if item.get("type") == "select":
        option = match_option(item, value)
        if option is None:
            return None
        return _choose_control(
            observation.stamp, item, option, control_id=f"enter-{_slug(label)}", priority=66, typed=True
        )
    is_search = item.get("type") == "search"
    kind = "search" if is_search else "fill"
    expected: dict[str, str | bool] = {"query": value} if is_search else {f"field:{label}": value}
    return _control(
        stamp=observation.stamp,
        control_id=f"enter-{_slug(label)}",
        label=f"{'Search for' if is_search else 'Fill ' + label + ' with'} {value}",
        rationale="You asked for this entry and the page has a matching field.",
        capability="browser.fill",
        risk="reversible_write",
        recipe=f"{kind}:{item.get('index')}={value}",
        expected=expected,
        preview=f"This types {value} into {label}." + ("" if is_search else " It does not submit the form."),
        priority=66,
    )
