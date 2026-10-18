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
    ingest_message,
    on_board_event,
    session_traces,
    session_view,
    ping_device,
    set_actuator,
    set_connection,
    set_fault,
)
from wayfinder.state import get_runtime

WEB_DIR = Path(__file__).resolve().parents[2] / "apps" / "web"


class TurnIn(BaseModel):
    text: str | None = None
    action_id: str | None = None
    channel: str = "text"


class SessionIn(BaseModel):
    target: str | None = None


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
        "page_targets": ["faq", "search", "form", "unsupported", "public", "fixture"],
        "transports": ["usb_serial", "usb_debug", "simulator"],
    }


@app.post("/api/sessions")
def open_session(body: SessionIn | None = Body(default=None)):
    """An empty body opens the Harbor Line fixture. A target opens a browser page."""
    return create_session(body.target if body else None)


@app.get("/api/sessions/{session_id}")
def read_session(session_id: str):
    return session_view(session_id)


@app.post("/api/sessions/{session_id}/turns")
def post_turn(session_id: str, body: TurnIn):
    if body.action_id is None and body.text is None:
        raise TopologyError("invalid_payload")
    return handle_turn(session_id, body.text, body.action_id, body.channel)


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
