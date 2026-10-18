"""The three-minute Harbor Line script, run through the API with no board."""

import time

from fastapi.testclient import TestClient

from wayfinder.api import app


def test_demo_script_completes_without_a_board():
    started = time.perf_counter()
    with TestClient(app) as client:
        opened = client.post("/api/sessions")
        assert opened.status_code == 200
        session = opened.json()
        session_id = session["session_id"]
        assert 3 <= len(session["branches"]) <= 5
        assert session["page"]["alert"] == "none"
        assert session["tactile"]["simulated"] is True

        here = client.post(f"/api/sessions/{session_id}/turns", json={"text": "what is here"}).json()
        assert here["page"]["purpose"]
        assert len({event["trace_id"] for event in here["events"]}) == 1

        filtered = client.post(
            f"/api/sessions/{session_id}/turns",
            json={"action_id": "filter_step_free"},
        ).json()
        assert filtered["verification"]["status"] == "SUCCESS"
        assert "Market" not in [station["name"] for station in filtered["page"]["stations"]]
        assert filtered["decision_record"]["confidence"] == "certain"
        assert len({event["trace_id"] for event in filtered["events"]}) == 1

        waiting = client.post(
            f"/api/sessions/{session_id}/turns",
            json={"action_id": "request_alert"},
        ).json()
        assert waiting["pending_confirmation"]["id"] == "request_alert"
        assert waiting["page"]["alert"] == "none"
        stopped = client.post(f"/api/sessions/{session_id}/turns", json={"text": "stop"}).json()
        assert stopped["pending_confirmation"] is None
        assert stopped["page"]["alert"] == "none"
        assert stopped["page"]["step_free_only"] is True

        client.post(f"/api/sessions/{session_id}/turns", json={"action_id": "request_alert"})
        confirmed = client.post(f"/api/sessions/{session_id}/turns", json={"text": "yes"}).json()
        assert confirmed["verification"]["status"] == "SUCCESS"
        assert confirmed["page"]["alert"] == "requested"
        assert confirmed["tactile"]["simulated"] is True

        disconnected = client.post(
            "/api/devices/devkit_tactile/connection",
            json={"status": "disconnected"},
        )
        assert disconnected.status_code == 200
        again = client.post(f"/api/sessions/{session_id}/turns", json={"text": "what is here"}).json()
        assert again["page"]["alert"] == "requested"
        assert again["page"]["step_free_only"] is True
        devkit = next(device for device in again["devices"] if device["id"] == "devkit_tactile")
        assert devkit["status"] == "disconnected"
        assert again["tactile"]["acknowledged"] is False
        assert len({event["trace_id"] for event in again["events"]}) == 1

    assert time.perf_counter() - started < 5
