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
from urllib.parse import urlparse

from wayfinder.models import Control

INJECTION_MARKS = ("ignore previous instructions", "ignore all previous", "disregard previous")
TARGETS = {
    "faq": "library-help.html",
    "search": "city-services.html",
    "form": "room-request.html",
    "unsupported": "market-poster.html",
}
# A public page is a best-effort extra. The browser may fail to reach it.
PUBLIC_URL = "https://www.w3.org/WAI/fundamentals/accessibility-intro/"

CONSEQUENTIAL_WORDS = ("pay", "buy", "purchase", "delete", "order", "donate", "checkout", "send")


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
            items.append({"kind": "link", "ref": f"link:{item.get('index')}", "text": f"{str(item.get('name'))[:80]} -> {item.get('href')}{hidden}"})
        for item in self.buttons[:6]:
            items.append({"kind": "button", "ref": f"button:{item.get('index')}", "text": str(item.get("name"))[:80]})
        for item in self.fields[:8]:
            value = "[redacted]" if item.get("sensitive") else str(item.get("value"))[:60]
            items.append({"kind": f"field:{item.get('type')}", "ref": f"field:{item.get('index')}", "text": f"{str(item.get('name'))[:60]} = {value or 'empty'}"})
        if self.status:
            items.append({"kind": "status", "ref": "[role=status]", "text": self.status[:120]})
        return items


def _stamp(url: str, raw: dict) -> str:
    body = {
        "url": url,
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
    result = []
    for link in links:
        href = str(link.get("href") or "")
        if link.get("hidden") or not link.get("same_origin") or not link.get("name"):
            continue
        if href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        path = str(link.get("path") or "")
        if not path or path == here:
            continue
        result.append(link)
    return result


def observe_from_dom(raw: dict, url: str, *, version: int = 1) -> PageObservation:
    details = list(raw.get("details") or [])
    links = [item for item in (raw.get("links") or []) if item.get("name")]
    buttons = list(raw.get("buttons") or [])
    fields = list(raw.get("fields") or [])
    headings = list(raw.get("headings") or [])
    status = str(raw.get("status") or "")
    h1 = str(raw.get("h1") or raw.get("title") or "Untitled page")
    purpose = str(raw.get("purpose") or h1)
    excerpt = str(raw.get("excerpt") or purpose)
    search_fields = [item for item in fields if item.get("type") == "search"]
    text_fields = [
        item for item in fields
        if item.get("type") not in {"search", "hidden", "submit", "button", "checkbox", "radio"}
    ]
    has_submit = any(item.get("type") == "submit" for item in buttons)
    if search_fields:
        family = "search"
    elif text_fields and has_submit:
        family = "form"
    elif len(details) >= 2 or (len(headings) >= 3 and len(links) >= 1):
        family = "faq"
    elif _followable(url, links):
        # A plain content page that links onward: read it, follow its links, go back.
        family = "docs"
    else:
        family = "unsupported"
    open_details = [item for item in details if item.get("open")]
    answer = str(open_details[0].get("answer") or "") if open_details else ""
    topics = [str(item.get("summary")) for item in details if item.get("summary")]
    if not topics:
        topics = [
            str(item.get("text")) for item in headings
            if str(item.get("level", "")).lower() in {"h2", "h3"} and item.get("text")
        ]
    if family == "form":
        topics = [str(item.get("name")) for item in text_fields]
    visible_links = [item for item in links if not item.get("hidden")]
    compare_pair = [str(item.get("name")) for item in visible_links[:2]]
    if len(compare_pair) < 2 and len(details) >= 2:
        compare_pair = [str(item.get("summary")) for item in details[:2]]
    query = str(search_fields[0].get("value") or "") if search_fields else ""
    facts: dict[str, str | bool] = {
        "expanded": str(open_details[0].get("summary") or "") if open_details else "",
        "query": query,
        "submitted": "submitted" in status.lower() and "not submitted" not in status.lower(),
    }
    for item in fields:
        name = str(item.get("name") or "")
        if name and not item.get("sensitive"):
            facts[f"field:{name}"] = str(item.get("value") or "")
    untrusted = _injection_sentences(str(raw.get("fulltext") or excerpt))
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
        summary = f"{h1}. {purpose.rstrip('.')}. Links: {names}."
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
    )


def failure_observation(url: str, message: str) -> PageObservation:
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
    observation.supported = False
    observation.family = "unsupported"
    observation.summary = message
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


def _follow_control(stamp: str, link: dict, *, priority: int, noun: str) -> Control:
    name = str(link.get("name"))
    path = str(link.get("path") or "")
    tail = path.rstrip("/").split("/")[-1] or path
    return _control(
        stamp=stamp,
        control_id=f"open-{noun}-{_slug(name)}",
        label=f"Open {name}",
        rationale=f"Shown because this {noun} is on the page.",
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
    if observation.family == "faq":
        controls.extend(_plan_faq(observation))
    elif observation.family == "search":
        controls.extend(_plan_search(observation))
    elif observation.family == "form":
        controls.extend(_plan_form(observation))
    elif observation.family == "docs":
        for order, link in enumerate(followable_links(observation)[:3]):
            controls.append(_follow_control(stamp, link, priority=58 - order, noun="link"))
    return controls


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


def _plan_search(observation: PageObservation) -> list[Control]:
    stamp = observation.stamp
    controls: list[Control] = []
    search = next((item for item in observation.fields if item.get("type") == "search"), None)
    query = str(observation.facts.get("query") or "")
    sample = str((search or {}).get("placeholder") or "").strip()
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
                expected={"query": sample, "status_has": sample},
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
    filter_button = next(
        (
            item for item in observation.buttons
            if item.get("type") == "button" and any(word in str(item.get("name", "")).lower() for word in ("only", "filter"))
        ),
        None,
    )
    if filter_button:
        name = str(filter_button.get("name"))
        controls.append(
            _control(
                stamp=stamp,
                control_id=f"filter-{_slug(name)}",
                label=name,
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
            if str(item.get("placeholder") or "").strip()
            and str(item.get("value") or "") != str(item.get("placeholder")).strip()
            and not item.get("sensitive")
        ),
        None,
    )
    if sample_field:
        sample = str(sample_field.get("placeholder")).strip()
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
        expected: dict[str, str | bool] = {"submitted": True}
        for name, value in values.items():
            if value:
                expected[f"field:{name}"] = value
        controls.append(
            _control(
                stamp=stamp,
                control_id="submit-form",
                label=str(submit.get("name") or "Submit"),
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
