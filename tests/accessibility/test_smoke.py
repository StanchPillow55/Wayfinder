"""Static accessibility smoke for the Mac UI. No board is required."""

import re
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

from wayfinder.api import app

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"


def _channel(value: int) -> float:
    scaled = value / 255
    if scaled <= 0.04045:
        return scaled / 12.92
    return ((scaled + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    red = int(color[1:3], 16)
    green = int(color[3:5], 16)
    blue = int(color[5:7], 16)
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(foreground: str, background: str) -> float:
    dark, light = sorted((luminance(foreground), luminance(background)))
    return (light + 0.05) / (dark + 0.05)


def css_color(css: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})", css)
    assert match, name
    return match.group(1)


class _FocusOrder(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.controls: list[tuple[str, str | None]] = []
        self.labels: list[str | None] = []
        self.live: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"a", "button", "input"} or values.get("tabindex") == "0":
            self.controls.append((tag, values.get("id") or values.get("href")))
        if tag == "label":
            self.labels.append(values.get("for"))
        if values.get("aria-live"):
            self.live.append(values.get("id"))


def test_keyboard_path_labels_and_live_status():
    page = (WEB / "index.html").read_text()
    script = (WEB / "app.js").read_text()
    parsed = _FocusOrder()
    parsed.feed(page)
    ids = [item[1] for item in parsed.controls]
    assert ids[:6] == ["#command", "stop", "braille-stage", "braille-prev", "braille-next", "command"]
    assert ids.index("confirm-yes") < ids.index("confirm-no")
    assert "command" in parsed.labels
    assert "live" in parsed.live
    assert 'aria-live="polite"' in page
    assert "confirm-yes" in page and "confirm-no" in page
    assert 'focus("#confirm-yes")' in script or 'querySelector("#confirm-yes").focus()' in script
    assert 'event.key === "Escape"' in script
    assert 'turn({ text: "no" })' in script
    assert "state.pending_confirmation) return" in script
    assert "ArrowLeft" in script and "brailleTiltZ" in script
    assert "live.textContent = state.speech" in script
    assert 'title="Page snapshot read by the browser adapter"' in page
    assert 'id="trace-id"' in page
    assert "One trace id for this turn" in script
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/styles.css").status_code == 200


def test_text_and_status_colors_clear_contrast():
    css = (WEB / "styles.css").read_text()
    ink = css_color(css, "--ink")
    muted = css_color(css, "--muted")
    card = css_color(css, "--card")
    background = css_color(css, "--bg")
    pairs = [
        (ink, background),
        (ink, card),
        (muted, card),
        (muted, background),
        ("#ffffff", ink),
        (css_color(css, "--ok-ink"), css_color(css, "--ok-bg")),
        (css_color(css, "--warn-ink"), css_color(css, "--warn-bg")),
        (css_color(css, "--bad-ink"), css_color(css, "--bad-bg")),
    ]
    for foreground, back in pairs:
        assert contrast(foreground, back) >= 4.5
    assert contrast(css_color(css, "--focus"), "#ffffff") >= 3
    assert ":focus-visible" in css
