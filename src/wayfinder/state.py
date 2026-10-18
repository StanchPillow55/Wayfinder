"""Process-wide session store. The Mac is the only owner of committed state."""

from __future__ import annotations

import os
import threading
import uuid
from typing import Literal

from wayfinder.devices import DevicePlane
from wayfinder.memory import MemoryCurator
from wayfinder.models import Control, Goal, PageState, Verification, WIR

Density = Literal["brief", "standard", "full"]


class Session:
    def __init__(self) -> None:
        self.id = str(uuid.uuid4())
        self.mode = "fixture"
        self.browser_target = ""
        self.observation = None
        self.fallback_reason = ""
        self.fallback_announced = False
        self.page = PageState()
        self.history: list[PageState] = []
        self.density: Density = "standard"
        self.goal = Goal()
        self.pending: Control | None = None
        self.branches: list[Control] = []
        self.wir: WIR | None = None
        self.faults: set[str] = set()
        self.evidence: list[str] = []
        self.traces: list[dict] = []
        self.memory_lines: list[str] = []
        self.tactile: dict = {
            "state_token": "cleared",
            "angle_deg": 0,
            "pulse_us": 1000,
            "label": "Idle",
            "actuator": "130_mini_dc_brushless",
            "duty_percent": 0,
            "duration_ms": 0,
            "direction": "stop",
            "transport": "simulator",
            "acknowledged": False,
            "simulated": True,
            "fallback": None,
            "device_status": "simulator",
            "device_angle": 0,
        }
        self.last_verification: Verification | None = None
        self.last_decision: dict | None = None
        self.last_speech = ""
        self.last_events: list[dict] = []
        self.last_turn_id = ""
        self.last_trace_id = ""
        self.box3_display: dict | None = None


class Runtime:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}
        self.active_session_id: str | None = None
        self.plane = DevicePlane()
        path = os.environ.get("WAYFINDER_MEMORY_PATH", "data/memory.db")
        self.memory = MemoryCurator(path)
        self._browser = None

    @property
    def browser(self):
        if self._browser is None:
            from wayfinder.browser.bridge import BrowserBridge

            self._browser = BrowserBridge()
        return self._browser

    def remember(self, session: Session, summary: str) -> None:
        session.memory_lines.append(summary)
        session.memory_lines = session.memory_lines[-20:]
        try:
            self.memory.add(session.id, summary)
        except Exception:
            # Semantic state on the session still stands if sqlite is unavailable.
            return


runtime = Runtime()


def get_runtime() -> Runtime:
    return runtime


def reset_runtime() -> None:
    if runtime._browser is not None:
        runtime._browser.reset_pages()
    callback = runtime.plane.on_event
    runtime.plane.shutdown()
    runtime.sessions.clear()
    runtime.active_session_id = None
    runtime.plane = DevicePlane()
    runtime.plane.on_event = callback
    try:
        runtime.memory.close()
    except Exception:
        pass
    path = os.environ.get("WAYFINDER_MEMORY_PATH", "data/memory.db")
    runtime.memory = MemoryCurator(path)
