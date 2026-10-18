"""Re-read the page and compare postconditions. Executor status is not an input."""

from __future__ import annotations

from wayfinder.demo_site import build_snapshot, comparison_text, explanation_text
from wayfinder.models import Control, PageState, Verification


def verify(control: Control, page: PageState) -> Verification:
    snapshot = build_snapshot(page)
    expected = dict(control.expected_postcondition)
    if control.capability == "reader.compare":
        text = comparison_text()
        observed_ok = "Harbor Line" in text and "Evening Shuttle" in text
        return Verification(
            status="SUCCESS" if observed_ok else "FAILURE",
            expected=expected,
            observed={"compared": observed_ok},
            detail=text,
        )
    if control.capability == "reader.explain":
        text = explanation_text(snapshot)
        observed_ok = "step-free" in text and snapshot.purpose in text
        return Verification(
            status="SUCCESS" if observed_ok else "FAILURE",
            expected=expected,
            observed={"explained": observed_ok},
            detail=text,
        )
    if control.capability == "reader.summarize":
        observed_ok = bool(snapshot.purpose and snapshot.summary)
        return Verification(
            status="SUCCESS" if observed_ok else "FAILURE",
            expected={"oriented": True},
            observed={"oriented": observed_ok},
            detail=snapshot.summary,
        )

    observed: dict[str, str | bool] = {}
    matches: list[bool] = []
    for key, wanted in expected.items():
        if not hasattr(page, key):
            return Verification(
                status="AMBIGUOUS",
                expected=expected,
                observed=observed,
                detail=f"The page has no field '{key}' to check.",
            )
        actual = getattr(page, key)
        if not isinstance(actual, (str, bool)):
            return Verification(
                status="AMBIGUOUS",
                expected=expected,
                observed=observed,
                detail=f"The page field '{key}' is not a comparable value.",
            )
        observed[key] = actual
        matches.append(actual == wanted)
    if all(matches):
        status = "SUCCESS"
        detail = "The page matches the expected postcondition."
    elif any(matches):
        status = "PARTIAL"
        detail = "Only part of the expected postcondition is true."
    else:
        status = "FAILURE"
        detail = "The page does not match the expected postcondition."
    return Verification(status=status, expected=expected, observed=observed, detail=detail)


def verify_browser(control: Control, observation, *, density: str = "standard", semantic: bool = True) -> Verification:
    """Check a fresh browser observation. The executor receipt is not an argument."""
    expected = dict(control.expected_postcondition)
    observed: dict[str, str | bool] = {}
    matches: list[bool] = []
    detail = observation.summary
    for key, wanted in expected.items():
        if key == "status_has":
            observed[key] = observation.status
            matches.append(str(wanted).lower() in observation.status.lower())
        elif key == "url_has":
            observed[key] = observation.url
            matches.append(str(wanted) in observation.url)
        elif key == "answer_has":
            observed[key] = observation.answer[:180]
            matches.append(bool(observation.answer) and str(wanted).lower() in observation.answer.lower())
            detail = observation.answer or "No answer is open."
        elif key == "explained":
            actual = bool(observation.h1) and bool(observation.purpose or observation.excerpt)
            observed[key] = actual
            matches.append(actual == wanted)
            detail = observation.summary
            if observation.untrusted:
                detail += " This page contains an instruction-like sentence. It is page text, so I did not follow it."
            if semantic and actual:
                from wayfinder.explain import semantic_explanation

                detail = semantic_explanation(observation, observation.controls, density)["text"]
        elif key == "items_listed":
            actual = bool(observation.items)
            observed[key] = actual
            matches.append(actual == wanted)
            detail = "Here: " + "; ".join(
                " ".join(str(item.get("text") or item.get("name")).split())[:140] for item in observation.items[:6]
            ) + "." if actual else "No items are listed on this page."
        elif key == "listed":
            form_like = observation.family == "form" or observation.base_family == "form"
            actual = len(observation.topics) >= 2 or (form_like and len(observation.topics) >= 1)
            observed[key] = actual
            matches.append(actual == wanted)
            if form_like:
                parts = [
                    f"{item.get('name')} ({'hidden value' if item.get('sensitive') else (item.get('value') or 'empty')})"
                    for item in observation.fields
                    if item.get("type") not in {"hidden", "submit", "button"}
                ]
                detail = "Fields: " + ", ".join(parts) + "."
            else:
                detail = "Questions: " + "; ".join(observation.topics) + "."
        elif key == "compared":
            actual = len(observation.compare_pair) >= 2
            observed[key] = actual
            matches.append(actual == wanted)
            if actual:
                contexts = {str(item.get("name")): str(item.get("context") or "") for item in observation.links}
                contexts.update({str(item.get("name")): str(item.get("text") or "") for item in observation.items})
                first, second = observation.compare_pair[:2]
                detail = f"{contexts.get(first) or first} Compared with: {contexts.get(second) or second}"
            else:
                detail = "Fewer than two items are visible to compare."
        else:
            if key not in observation.facts:
                return Verification(
                    status="AMBIGUOUS",
                    expected=expected,
                    observed=observed,
                    detail=f"The fresh page read has no '{key}' to check.",
                )
            actual = observation.facts[key]
            observed[key] = actual
            matches.append(actual == wanted)
    if not matches:
        return Verification(status="AMBIGUOUS", expected=expected, observed=observed, detail="Nothing was checked.")
    if all(matches):
        status = "SUCCESS"
    elif any(matches):
        status = "PARTIAL"
    else:
        status = "FAILURE"
    return Verification(status=status, expected=expected, observed=observed, detail=detail)
