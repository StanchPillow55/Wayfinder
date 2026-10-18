"""The browser MVP demo script (docs/demo/script.md), run through the API with no board."""

import time

import pytest

pytest.importorskip("playwright", reason="playwright is not installed")

from fastapi.testclient import TestClient

from wayfinder.api import app


def test_browser_demo_script_completes_without_a_board():
    started = time.perf_counter()
    with TestClient(app) as client:

        def open_page(target):
            body = client.post("/api/sessions", json={"target": target}).json()
            if body["mode"] != "browser":
                pytest.skip("Chromium is not available")
            return body

        def say(session_id, **kwargs):
            return client.post(f"/api/sessions/{session_id}/turns", json=kwargs).json()

        # 1. Boots on a browser FAQ page, not the fixture.
        faq = open_page("faq")
        sid = faq["session_id"]
        assert faq["page"]["kind"] == "browser" and faq["tactile"]["simulated"] is True
        assert 3 <= len(faq["branches"]) <= 5
        assert faq["page"]["untrusted_text"]

        # 2. Open a question. The answer comes from a fresh read.
        opened = say(sid, text="open how do i get a card")
        assert opened["verification"]["status"] == "SUCCESS" and "photo ID" in opened["speech"]

        # 3. Out of range.
        assert "not one of the current options" in say(sid, text="9")["speech"]

        # 4. Follow a link, then go back.
        billing = say(sid, action_id="open-link-billing-questions")
        assert billing["page"]["url"].endswith("billing-questions.html")
        assert say(sid, text="go back")["page"]["url"].endswith("library-help.html")

        # 5. Search page.
        search = open_page("search")
        sid = search["session_id"]
        assert say(sid, text="search for permit")["verification"]["status"] == "SUCCESS"
        assert "Apply for a permit" in say(sid, text="compare")["speech"]
        assert say(sid, action_id="open-result-building-permits")["page"]["url"].endswith("building-permits.html")
        assert say(sid, text="back")["page"]["url"].endswith("city-services.html")

        # 6. Form: decline leaves it unchanged, confirm verifies.
        form = open_page("form")
        sid = form["session_id"]
        assert say(sid, text="set name to Grace Hopper")["verification"]["status"] == "SUCCESS"
        assert say(sid, action_id="fill-room")["verification"]["status"] == "SUCCESS"
        waiting = say(sid, action_id="submit-form")
        assert waiting["pending_confirmation"]["id"] == "submit-form"
        assert say(sid, text="no")["page"]["status"] == "Not submitted."
        say(sid, action_id="submit-form")
        confirmed = say(sid, text="yes")
        assert confirmed["verification"]["status"] == "SUCCESS"
        assert "Reservation submitted" in confirmed["page"]["status"]

        # 7. Unsupported page.
        poster = open_page("unsupported")
        assert poster["page"]["supported"] is False and len(poster["branches"]) == 1

        # 8. Hardware stays simulated, and a disconnected DevKit does not stop the page task.
        client.post("/api/devices/devkit_tactile/connection", json={"status": "disconnected"})
        again = say(sid, text="what is here")
        assert again["tactile"]["acknowledged"] is False
        assert again["page"]["kind"] == "browser"
        faq_after = open_page("faq")
        assert faq_after["branches"] and faq_after["tactile"]["acknowledged"] is False
        assert "motor" in faq_after["hardware_note"]

        # 9. The labeled fixture fallback.
        fixture = client.post("/api/sessions", json={"target": "fixture"}).json()
        assert fixture["page"]["kind"] == "fixture" and "fixture" in fixture["page"]["fallback_note"].lower()

    assert time.perf_counter() - started < 60
