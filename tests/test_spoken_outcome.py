"""What the person hears after a checked action. The status stays on the record."""

from wayfinder.orchestrator import create_session, handle_turn, set_fault


def test_confirmed_alert_is_spoken_as_a_phone_menu():
    view = create_session()
    sid = view["session_id"]
    view = handle_turn(sid, action_id="request_alert")
    assert view["pending_confirmation"]["id"] == "request_alert"
    view = handle_turn(sid, "yes")
    speech = view["speech"]
    assert view["session_id"] == sid
    assert view["verification"]["status"] == "SUCCESS"
    assert view["page"]["alert"] == "requested"
    assert view["branches"]
    lowered = speech.lower()
    assert "verification" not in lowered
    assert "success" not in lowered
    assert "partial" not in lowered
    assert "failure" not in lowered
    assert "the arrival alert is requested." in lowered
    assert "you can " in lowered


def test_failed_check_says_what_is_still_on_the_page():
    view = create_session()
    set_fault(view["session_id"], "verification_mismatch", True)
    failed = handle_turn(view["session_id"], action_id="filter_step_free")
    speech = failed["speech"]
    assert failed["verification"]["status"] == "FAILURE"
    assert failed["page"]["step_free_only"] is False
    lowered = speech.lower()
    assert "verification" not in lowered
    assert "success" not in lowered
    assert "failure" not in lowered
    assert "stations shown" in lowered
    assert "you can " in lowered
