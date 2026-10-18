"""HTTP entry for the Mac orchestrator."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from wayfinder.contracts import ACTUATOR, ADR_ID, PROPOSAL_VERSION, RETIRED_ENDPOINT_IDS
from wayfinder.demo_site import build_snapshot, to_html
from wayfinder.models import TopologyError
from wayfinder.orchestrator import (
    SessionNotFound,
    create_session,
    handle_turn,
    continue_project,
    ingest_message,
    on_board_event,
    save_project,
    set_capability,
    set_channel_status,
    set_preferences,
    set_renderer,
    session_traces,
    session_view,
    ping_device,
    set_actuator,
    set_connection,
    set_fault,
)
from wayfinder.state import get_runtime
from wayfinder.tactile_loop import handle_input as handle_tactile_input
from wayfinder.tactile_loop import publish_word_cue
from wayfinder.tactile_loop import status_view as tactile_status_view

WEB_DIR = Path(__file__).resolve().parents[2] / "apps" / "web"


class TurnIn(BaseModel):
    text: str | None = None
    action_id: str | None = None
    channel: str = "text"


class SessionIn(BaseModel):
    target: str | None = None
    url: str | None = None
    # Carry the goal, path, and preferences into the newly opened page.
    continue_from: str | None = None


class CapabilityIn(BaseModel):
    id: str
    enabled: bool


class RendererIn(BaseModel):
    renderer: str = Field(pattern="^(all|visual|audio|tactile|hands_free)$")


class PreferencesIn(BaseModel):
    speech_muted: bool | None = None
    density: str | None = None
    branch_limit: int | None = None


class ChannelStatusIn(BaseModel):
    mic: str | None = None
    camera: str | None = None


class ProjectIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)


class CursorIn(BaseModel):
    index: int = Field(ge=0, le=20)
    source: str = Field(default="on_screen", pattern="^(on_screen|voice|camera)$")


class FaultIn(BaseModel):
    name: str
    enabled: bool = True


class ConnectionIn(BaseModel):
    status: str = Field(pattern="^(disconnected|simulator)$")


class ActuatorIn(BaseModel):
    enabled: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    plane = get_runtime().plane
    plane.on_event = on_board_event
    plane.start_configured_links()
    yield
    get_runtime().plane.shutdown()
    runtime = get_runtime()
    if runtime._browser is not None:
        runtime._browser.close()


app = FastAPI(title="Wayfinder", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.exception_handler(TopologyError)
async def topology_handler(_request, exc: TopologyError):
    return JSONResponse(status_code=400, content={"error": exc.code, "adr": ADR_ID})


@app.exception_handler(SessionNotFound)
async def session_handler(_request, _exc: SessionNotFound):
    return JSONResponse(status_code=404, content={"error": "unknown_session"})


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health():
    plane = get_runtime().plane
    return {
        "status": "ok",
        "proposal_version": PROPOSAL_VERSION,
        "adr": ADR_ID,
        "orchestrator": "mac",
        "topology": [plane.entry(board_id) for board_id in plane.boards],
        "retired": sorted(RETIRED_ENDPOINT_IDS),
        "actuator": ACTUATOR,
        "browser_adapter": "playwright",
        "page_targets": ["faq", "search", "form", "unsupported", "public", "fixture", "url", "shop", "jobs", "unlabeled"],
        "transports": ["usb_serial", "usb_debug", "simulator"],
    }


@app.post("/api/sessions")
def open_session(body: SessionIn | None = Body(default=None)):
    """An empty body opens the Harbor Line fixture. A target opens a browser page.

    {"target": "url", "url": "https://..."} opens any http(s) page through Playwright.
    """
    return create_session(
        body.target if body else None,
        body.url if body else None,
        body.continue_from if body else None,
    )


@app.get("/api/sessions/{session_id}")
def read_session(session_id: str):
    return session_view(session_id)


@app.post("/api/sessions/{session_id}/turns")
def post_turn(session_id: str, body: TurnIn):
    if body.action_id is None and body.text is None:
        raise TopologyError("invalid_payload")
    return handle_turn(session_id, body.text, body.action_id, body.channel)


@app.post("/api/sessions/{session_id}/capabilities")
def capability(session_id: str, body: CapabilityIn):
    """Turn a specialist capability on or off. Core roles are locked on."""
    return set_capability(session_id, body.id, body.enabled)


@app.post("/api/sessions/{session_id}/renderer")
def renderer(session_id: str, body: RendererIn):
    """Switch the active output. The goal, path, and pending confirmation are not touched."""
    return set_renderer(session_id, body.renderer)


@app.post("/api/sessions/{session_id}/preferences")
def preferences(session_id: str, body: PreferencesIn):
    return set_preferences(session_id, body.speech_muted, body.density, body.branch_limit)


@app.post("/api/sessions/{session_id}/channels")
def channels(session_id: str, body: ChannelStatusIn):
    """Mic and camera status words from the browser. Never audio or video."""
    return set_channel_status(session_id, body.mic, body.camera)


@app.post("/api/sessions/{session_id}/projects")
def save(session_id: str, body: ProjectIn):
    return save_project(session_id, body.name)


@app.post("/api/sessions/{session_id}/projects/continue")
def resume(session_id: str, body: ProjectIn):
    return continue_project(session_id, body.name)


@app.get("/api/projects")
def list_projects():
    from wayfinder.conversation import projects

    return {"projects": projects()}


@app.post("/api/sessions/{session_id}/cursor")
def cursor(session_id: str, body: CursorIn):
    """Move the shared highlight. Voice and camera call this so every channel follows one cursor."""
    from wayfinder.tactile_loop import set_cursor

    return set_cursor(session_id, body.index, body.source)


@app.post("/api/sessions/{session_id}/cancel")
def cancel(session_id: str):
    return handle_turn(session_id, "stop", None, "text")


@app.post("/api/sessions/{session_id}/faults")
def faults(session_id: str, body: FaultIn):
    return set_fault(session_id, body.name, body.enabled)


@app.get("/api/sessions/{session_id}/traces")
def traces(session_id: str):
    return {"events": session_traces(session_id)}


@app.post("/api/sessions/{session_id}/device-messages")
def device_messages(session_id: str, body: dict):
    return ingest_message(session_id, body)


class CueIn(BaseModel):
    word: str = Field(min_length=1, max_length=40)
    index: int = Field(default=0, ge=0, le=500)
    count: int = Field(default=1, ge=1, le=500)


class TactileInputIn(BaseModel):
    input: str = Field(pattern="^(next|confirm|back|stop|analog)$")
    level: int | None = Field(default=None, ge=0, le=100)


@app.post("/api/sessions/{session_id}/tactile/cue")
def tactile_cue(session_id: str, body: CueIn):
    """One bounded motor cue for a word the plate formed. Sent as tactile.render when a board is bound."""
    return publish_word_cue(session_id, body.word, body.index, body.count)


@app.post("/api/sessions/{session_id}/tactile/input")
def tactile_input(session_id: str, body: TactileInputIn):
    """On-screen stand-in for the DevKit buttons and analog input. Same actions as the board path."""
    return handle_tactile_input(session_id, body.input, body.level, "on_screen")


@app.get("/api/sessions/{session_id}/tactile")
def tactile_status(session_id: str):
    runtime = get_runtime()
    with runtime.lock:
        try:
            session = runtime.sessions[session_id]
        except KeyError as exc:
            raise SessionNotFound(session_id) from exc
        return tactile_status_view(session)


@app.post("/api/devices/{board_id}/connection")
def connection(board_id: str, body: ConnectionIn):
    return set_connection(board_id, body.status)


@app.post("/api/devices/devkit_tactile/actuator")
def actuator(body: ActuatorIn):
    return set_actuator(body.enabled)


@app.post("/api/devices/{board_id}/health")
def device_health(board_id: str):
    return ping_device(board_id)


@app.get("/demo/harbor")
def harbor(session: str):
    runtime = get_runtime()
    with runtime.lock:
        try:
            page = runtime.sessions[session].page
        except KeyError as exc:
            raise SessionNotFound(session) from exc
        snapshot = build_snapshot(page)
    return HTMLResponse(to_html(snapshot))


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
