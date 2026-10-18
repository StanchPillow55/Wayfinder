"""WIR 0.2, agent envelopes, and the USB device message contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wayfinder.contracts import (
    ALLOWED_TRANSPORTS,
    BOARD_IDS,
    MAC_ID,
    MAX_ANGLE_DEG,
    MAX_MESSAGE_BYTES,
    MAX_PULSE_US,
    MIN_PULSE_US,
    PROTOCOL_VERSION,
    RAW_MEDIA_KEYS,
    RETIRED_ENDPOINT_IDS,
    WIR_VERSION,
)

RiskClass = Literal["read", "reversible_write", "consequential", "irreversible"]
PolicyDecisionName = Literal["ALLOW", "REQUIRE_CONFIRMATION", "DENY"]
VerificationStatus = Literal["SUCCESS", "PARTIAL", "FAILURE", "AMBIGUOUS"]
Density = Literal["brief", "standard", "full"]
IntentType = Literal[
    "what_is_here",
    "explain",
    "compare",
    "less_detail",
    "more_detail",
    "go_back",
    "stop",
    "continue",
    "select",
    "enter_text",
    "confirm",
    "deny",
    "ambiguous",
    "unknown",
    "wait",
    "repeat",
    "explain_that",
    "read_all",
    "forget_constraint",
    "just_do_it",
    "where_was_i",
    "set_goal",
    "mute",
    "unmute",
    "set_renderer",
    "save_project",
    "continue_project",
    "set_preference",
    "reset_preferences",
    "who_acting",
]
MessageType = Literal[
    "tactile.render",
    "display.render",
    "speech.render",
    "speech.event",
    "touch.event",
    "vision.event",
    "safe_disable",
    "health",
    "ack",
    "error",
]


class TopologyError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    name: str
    attributes: dict[str, str] = Field(default_factory=dict)
    trust: Literal["observed", "untrusted"] = "observed"


class Control(BaseModel):
    """A grounded page capability. The router cannot invent one of these."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    rationale: str
    capability: str
    risk_class: RiskClass
    semantic_target: str
    expected_postcondition: dict[str, str | bool]
    informational: bool = False
    preview: str
    priority: int = 0


class A11yNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    name: str
    node_id: str | None = None


class PageState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: Literal["overview", "evening"] = "overview"
    step_free_only: bool = False
    alert: Literal["none", "requested"] = "none"


class Snapshot(BaseModel):
    """Accessibility-oriented reading of the controlled page. Not a DOM dump."""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str
    purpose: str
    summary: str
    entities: list[Entity]
    controls: list[Control]
    accessibility_tree: list[A11yNode]
    untrusted_text: list[str]
    view: str
    step_free_only: bool
    alert: str
    stations: list[dict[str, str]]
    routes: list[dict[str, str]]
    hidden_station_count: int = 0


class Goal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""
    constraints: list[str] = Field(default_factory=list)


class WIR(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.2"] = WIR_VERSION
    session_id: str
    turn_id: str
    trace_id: str
    page: dict[str, str]
    goal: Goal
    entities: list[Entity]
    actions: list[Control]
    alerts: list[dict[str, str]]
    history_summary: str = ""
    available_channels: dict[str, list[str]]
    devices: dict[str, list[dict[str, str]]]
    uncertainty: list[str] = Field(default_factory=list)
    information_density: Density = "standard"


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: IntentType
    text: str
    selection: int | None = None
    action_id: str | None = None
    channel: str = "text"
    field: str | None = None
    value: str | None = None


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecisionName
    risk_class: RiskClass
    permissions: list[str]
    reason: str


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    agent: Literal["reader", "browser_navigator"]
    permissions: list[str]


class ExecResult(BaseModel):
    """What the executor did. The verifier must not treat this as proof."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["applied", "rejected", "unchanged"]
    error_class: str | None = None
    changed: bool = False


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VerificationStatus
    expected: dict[str, str | bool]
    observed: dict[str, str | bool]
    detail: str


class AgentEnvelope(BaseModel):
    """Structured agent handoff. Hardware messages do not use this shape."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["0.1"] = PROTOCOL_VERSION
    trace_id: str
    request_id: str
    parent_request_id: str | None = None
    sender: str
    recipient: str
    task_type: str
    input_refs: list[str]
    required_capability: str
    permissions: list[str]
    risk_class: RiskClass
    expected_output_schema: str
    deadline_ms: int = Field(ge=1, le=60_000)
    metadata: dict[str, str | bool] = Field(default_factory=dict)


class DeviceMessage(BaseModel):
    """Mac ↔ board envelope. Transport is USB or a simulator, never UART."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["0.1"] = PROTOCOL_VERSION
    transport: Literal["usb_serial", "usb_debug", "simulator"]
    trace_id: str = Field(min_length=1, max_length=80)
    message_id: str = Field(min_length=1, max_length=80)
    sequence: int = Field(ge=0, le=1_000_000)
    source: str = Field(min_length=1, max_length=64)
    destination: str = Field(min_length=1, max_length=64)
    type: MessageType
    created_at_ms: int = Field(ge=0)
    payload: dict[str, object] = Field(default_factory=dict)
    requires_ack: bool = False

    @model_validator(mode="after")
    def enforce_topology(self) -> DeviceMessage:
        if self.transport not in ALLOWED_TRANSPORTS:
            raise TopologyError("uart_topology_retired")
        if self.source in RETIRED_ENDPOINT_IDS or self.destination in RETIRED_ENDPOINT_IDS:
            raise TopologyError("retired_topology")
        if self.source in BOARD_IDS and self.destination in BOARD_IDS:
            raise TopologyError("peer_link_forbidden")
        ends = {self.source, self.destination}
        if MAC_ID not in ends:
            raise TopologyError("mac_must_be_endpoint")
        known = BOARD_IDS | {MAC_ID}
        if self.source not in known or self.destination not in known:
            raise TopologyError("unknown_endpoint")
        _reject_raw_media(self.payload)
        _validate_payload(self.type, self.payload)
        return self


def _reject_raw_media(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in RAW_MEDIA_KEYS:
                raise TopologyError("raw_media_forbidden")
            _reject_raw_media(item)
    elif isinstance(value, list):
        for item in value:
            _reject_raw_media(item)


def _validate_payload(message_type: str, payload: dict[str, object]) -> None:
    if message_type == "vision.event":
        allowed = {"event", "summary"}
        if set(payload) - allowed:
            raise TopologyError("raw_media_forbidden")
        event = payload.get("event")
        if event not in {"user_present", "scene_changed", "scene_summary"}:
            raise TopologyError("invalid_payload")
        summary = payload.get("summary")
        if summary is not None and (not isinstance(summary, str) or len(summary) > 200):
            raise TopologyError("invalid_payload")
    elif message_type == "speech.event":
        transcript = payload.get("transcript")
        if not isinstance(transcript, str) or not transcript.strip() or len(transcript) > 500:
            raise TopologyError("invalid_payload")
        if set(payload) - {"transcript"}:
            raise TopologyError("raw_media_forbidden")
    elif message_type == "touch.event":
        keys = set(payload)
        if keys == {"selection"}:
            selection = payload["selection"]
            if not isinstance(selection, int) or not 1 <= selection <= 5:
                raise TopologyError("invalid_payload")
        elif keys == {"control"}:
            if payload["control"] not in {"confirm", "deny", "stop"}:
                raise TopologyError("invalid_payload")
        elif keys == {"demo_input"}:
            # Additive touch.event shape for the DevKit demo buttons (protocol stays 0.1,
            # no new message type). The orchestrator accepts it from devkit_tactile only.
            if payload["demo_input"] not in {"next", "back"}:
                raise TopologyError("invalid_payload")
        elif keys == {"demo_input", "level"}:
            level = payload["level"]
            if payload["demo_input"] != "analog" or isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
                raise TopologyError("invalid_payload")
        else:
            raise TopologyError("invalid_payload")
    elif message_type == "tactile.render":
        angle = payload.get("angle_deg")
        pulse = payload.get("pulse_us")
        token = payload.get("state_token")
        if not isinstance(angle, int) or not 0 <= angle <= MAX_ANGLE_DEG:
            raise TopologyError("invalid_payload")
        if not isinstance(pulse, int) or not MIN_PULSE_US <= pulse <= MAX_PULSE_US:
            raise TopologyError("invalid_payload")
        if not isinstance(token, str) or len(token) > 40:
            raise TopologyError("invalid_payload")
    elif message_type == "speech.render":
        text = payload.get("text")
        if not isinstance(text, str) or len(text) > 280:
            raise TopologyError("invalid_payload")


def parse_device_message(data: dict[str, object]) -> DeviceMessage:
    """Parse a board or simulator message. UART and the C3 id are hard errors."""
    transport = data.get("transport")
    if transport == "uart":
        raise TopologyError("uart_topology_retired")
    source = data.get("source")
    destination = data.get("destination")
    if source in RETIRED_ENDPOINT_IDS or destination in RETIRED_ENDPOINT_IDS:
        raise TopologyError("retired_topology")
    if source in BOARD_IDS and destination in BOARD_IDS:
        raise TopologyError("peer_link_forbidden")
    try:
        message = DeviceMessage.model_validate(data)
    except TopologyError:
        raise
    except Exception as exc:  # pydantic wraps our validator errors
        message = str(exc)
        for code in (
            "uart_topology_retired",
            "retired_topology",
            "peer_link_forbidden",
            "mac_must_be_endpoint",
            "unknown_endpoint",
            "raw_media_forbidden",
            "message_too_large",
            "invalid_payload",
            "protocol_version",
        ):
            if code in message:
                raise TopologyError(code) from exc
        raise TopologyError("invalid_message") from exc
    encoded = len(message.model_dump_json().encode("utf-8"))
    if encoded > MAX_MESSAGE_BYTES:
        raise TopologyError("message_too_large")
    return message
