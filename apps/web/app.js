let state = null;
let brailleOffset = 0;
let brailleSpeech = "";
const brailleCols = 16;
const brailleRows = 2;
const brailleWindow = brailleCols * brailleRows;
let brailleTiltX = 62;
let brailleTiltZ = -22;

const live = document.querySelector("#live");
const command = document.querySelector("#command");
const brailleScene = document.querySelector("#braille-scene");
const brailleStage = document.querySelector("#braille-stage");

function text(id, value) {
  document.querySelector(id).textContent = value;
}

async function turn(body) {
  const response = await fetch(`/api/sessions/${state.session_id}/turns`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  state = await response.json();
  render();
}

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
  return response.json();
}

function render() {
  live.textContent = state.speech || "";
  text("#purpose", state.page.purpose);
  text("#summary", state.page.summary);
  const goal = state.goal.constraints.length
    ? `Goal: ${state.goal.text} Constraints: ${state.goal.constraints.join(", ")}.`
    : "No extra goal yet.";
  text("#goal", goal);

  const uncertainty = document.querySelector("#uncertainty");
  uncertainty.replaceChildren();
  for (const item of state.wir?.uncertainty || []) {
    const li = document.createElement("li");
    li.textContent = item;
    uncertainty.append(li);
  }

  const branches = document.querySelector("#branches");
  branches.replaceChildren();
  state.branches.forEach((branch, index) => {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "branch";
    button.dataset.actionId = branch.id;
    const name = document.createElement("span");
    name.textContent = `${index + 1}. ${branch.label}`;
    const risk = document.createElement("span");
    risk.className = "risk";
    risk.textContent = `${branch.risk_label}. ${branch.rationale}`;
    button.append(name, risk);
    button.addEventListener("click", () => turn({ action_id: branch.id }));
    li.append(button);
    branches.append(li);
  });

  const check = document.querySelector("#verification");
  if (!state.verification) {
    check.className = "";
    check.textContent = "No page change has been checked yet.";
  } else {
    const status = state.verification.status;
    check.className = status === "SUCCESS" ? "status-ok" : "status-bad";
    check.textContent = `${status}. Expected ${JSON.stringify(state.verification.expected)}. Observed ${JSON.stringify(state.verification.observed)}. ${state.verification.detail}`;
  }

  const decision = state.decision_record;
  const decisionNode = document.querySelector("#decision");
  if (!decision) {
    decisionNode.textContent = "No decision recorded for this turn.";
  } else {
    const alternatives = (decision.alternative_branches || []).join(", ") || "none";
    decisionNode.textContent =
      `Decision: ${decision.chosen_label}. Policy ${decision.policy_outcome}. ` +
      `Risk ${decision.risk_class}. Confidence ${decision.confidence}. ` +
      `Evidence ${decision.evidence}. Other branches: ${alternatives}.`;
  }

  const page = state.page;
  const browser = page.kind === "browser";
  text(
    "#page-mode",
    browser
      ? `Live browser page read by ${state.adapter} at ${page.url}. Page family: ${page.family}${page.supported ? "" : ", not supported"}. Read ${page.read_version}.`
      : "Harbor Line fixture. This is the labeled offline fallback and regression fixture. It is not the live browser.",
  );
  if (browser) {
    const extra = page.status ? ` Page status: ${page.status}` : "";
    text("#page-meta", `${page.title}.${extra}`);
  } else {
    const filter = page.step_free_only ? "Step-free filter is on." : "No station filter.";
    const view = page.view === "evening" ? "Evening Shuttle view." : "Daytime overview.";
    const alert = page.alert === "requested" ? "Arrival alert requested." : "No arrival alert.";
    text("#page-meta", `${view} ${filter} ${alert}`);
  }

  const evidence_list = document.querySelector("#page-evidence");
  evidence_list.replaceChildren();
  for (const item of page.evidence || []) {
    const li = document.createElement("li");
    li.textContent = `${item.kind} ${item.ref}: ${item.text}`;
    evidence_list.append(li);
  }
  document.querySelector("#evidence-heading").hidden = !browser;
  evidence_list.hidden = !browser;

  const stations = document.querySelector("#stations");
  stations.replaceChildren();
  for (const station of page.stations) {
    const li = document.createElement("li");
    li.textContent = `${station.name}, ${station.access}. ${station.note}`;
    stations.append(li);
  }
  stations.hidden = browser;
  const routes = document.querySelector("#routes");
  routes.replaceChildren();
  for (const route of page.routes) {
    const li = document.createElement("li");
    li.textContent = `${route.name}. ${route.when} Serves ${route.serves}`;
    routes.append(li);
  }
  routes.hidden = browser;
  const untrusted = page.untrusted_text[0];
  text(
    "#page-notice",
    `${page.fallback_note}${untrusted ? ` Untrusted page text, not followed: ${untrusted}` : ""}`,
  );
  const frame = document.querySelector("#page-frame");
  if (browser) {
    if (frame.dataset.ref !== page.evidence_ref) {
      frame.removeAttribute("src");
      frame.srcdoc = page.html || "";
      frame.dataset.ref = page.evidence_ref;
    }
  } else {
    const src = `/demo/harbor?session=${encodeURIComponent(state.session_id)}&turn=${encodeURIComponent(state.turn_id)}`;
    frame.removeAttribute("srcdoc");
    frame.dataset.ref = "";
    frame.src = src;
  }

  for (const button of document.querySelectorAll("[data-target]")) {
    button.setAttribute("aria-pressed", String(button.dataset.target === state.target));
  }
  const labels = {
    faq: "Library help FAQ",
    search: "City services search",
    form: "Room request form",
    unsupported: "Market poster, an unsupported page",
    public: "A public page, best effort",
    fixture: "Harbor Line, the offline fixture",
  };
  text("#target-status", `Open page: ${labels[state.target] || state.target}. Adapter: ${state.adapter}.`);

  const tactile = state.tactile;
  const duty = Number(tactile.duty_percent || 0);
  document.querySelector("#motor-bar").style.width = `${duty}%`;
  const run = `${duty}% duty for ${tactile.duration_ms || 0} ms, ${tactile.direction || "stop"}`;
  const ack = tactile.acknowledged
    ? (tactile.simulated
      ? "The simulator acknowledged this motor run. The physical motor has not spun."
      : tactile.loopback
        ? "Loopback acknowledged this motor run. No physical board answered, and the motor has not spun."
        : "The DevKit acknowledged this render on USB. A motor run is not verified by that acknowledgement alone.")
    : "No DevKit acknowledged this. The on-screen tactile view shows the state and the motor has not spun.";
  text("#tactile-text", `${tactile.label}. Pose ${tactile.state_token}. Planned run: ${run}. ${ack}`);
  renderBraille();

  const glance = document.querySelector("#glance");
  glance.replaceChildren();
  for (const line of (state.glance && state.glance.lines) || []) {
    const li = document.createElement("li");
    li.textContent = line;
    glance.append(li);
  }

  const devices = document.querySelector("#devices");
  devices.replaceChildren();
  const mac = document.createElement("li");
  mac.textContent = "Mac. Canonical WIR and orchestration.";
  devices.append(mac);
  for (const device of state.devices) {
    const li = document.createElement("li");
    li.textContent = `${device.board}. Role ${device.role}. Link ${device.link}. Status ${device.status}.`;
    devices.append(li);
  }

  const devkit = state.devices.find((device) => device.id === "devkit_tactile");
  document.querySelector("#devkit-connection").textContent =
    devkit && devkit.status === "disconnected" ? "Connect DevKit simulator" : "Disconnect DevKit";
  document.querySelector("#actuator").textContent =
    devkit && devkit.status === "lockout" ? "Enable actuator" : "Disable actuator";

  const evidence = document.querySelector("#evidence");
  evidence.replaceChildren();
  if (!state.evidence.length) {
    const li = document.createElement("li");
    li.textContent = "No camera events.";
    evidence.append(li);
  }
  for (const note of state.evidence) {
    const li = document.createElement("li");
    li.textContent = note;
    evidence.append(li);
  }

  const traceIds = new Set((state.events || []).map((event) => event.trace_id));
  text(
    "#trace-id",
    traceIds.size === 1
      ? `One trace id for this turn: ${state.trace_id}.`
      : `This turn has ${traceIds.size} trace ids.`,
  );
  const trace = document.querySelector("#trace");
  trace.replaceChildren();
  for (const event of state.events) {
    const li = document.createElement("li");
    const agent = event.detail && (event.detail.agent || event.detail.capability);
    li.textContent = agent
      ? `${event.source}. ${event.action}. ${event.status}. ${agent}.`
      : `${event.source}. ${event.action}. ${event.status}.`;
    trace.append(li);
  }

  const confirm = document.querySelector("#confirm");
  const wasHidden = confirm.hidden;
  if (state.pending_confirmation) {
    confirm.hidden = false;
    text("#confirm-text", `${state.pending_confirmation.risk_label}. ${state.pending_confirmation.preview}`);
    if (wasHidden) document.querySelector("#confirm-yes").focus();
  } else {
    const focusedInside = confirm.contains(document.activeElement);
    confirm.hidden = true;
    text("#confirm-text", "");
    if (focusedInside) command.focus();
  }
}

function applyBrailleTilt() {
  brailleScene.style.transform = `rotateX(${brailleTiltX}deg) rotateZ(${brailleTiltZ}deg)`;
}

function renderBraille() {
  const cells = state.braille?.cells || [];
  if ((state.speech || "") !== brailleSpeech) {
    brailleSpeech = state.speech || "";
    brailleOffset = 0;
  }
  const maxOffset = Math.max(0, Math.ceil(cells.length / brailleWindow) * brailleWindow - brailleWindow);
  if (brailleOffset > maxOffset) brailleOffset = maxOffset;
  const plate = document.querySelector("#braille-plate");
  plate.replaceChildren();
  for (let row = 0; row < brailleRows; row += 1) {
    const line = document.createElement("div");
    line.className = "braille-line";
    for (let col = 0; col < brailleCols; col += 1) {
      const index = brailleOffset + row * brailleCols + col;
      const mask = index < cells.length ? cells[index] : 0;
      const cell = document.createElement("div");
      cell.className = "braille-cell";
      if (index < cells.length) cell.dataset.mask = String(mask);
      for (const dot of [1, 4, 2, 5, 3, 6]) {
        const pin = document.createElement("span");
        pin.className = mask & (1 << (dot - 1)) ? "pin up" : "pin";
        cell.append(pin);
      }
      line.append(cell);
    }
    plate.append(line);
  }
  const shown = Math.min(brailleWindow, Math.max(0, cells.length - brailleOffset));
  const start = cells.length ? brailleOffset + 1 : 0;
  const end = cells.length ? brailleOffset + shown : 0;
  const note = state.braille?.note || "Software Grade 1 projection of the live speech.";
  text(
    "#braille-caption",
    cells.length ? `Grade 1 Braille, cells ${start}–${end} of ${cells.length}. ${note}` : "No Braille cells yet.",
  );
  document.querySelector("#braille-prev").disabled = brailleOffset === 0;
  document.querySelector("#braille-next").disabled = brailleOffset >= maxOffset;
}

let brailleDrag = null;
brailleStage.addEventListener("pointerdown", (event) => {
  brailleDrag = { x: event.clientX, y: event.clientY, tiltX: brailleTiltX, tiltZ: brailleTiltZ };
  brailleStage.setPointerCapture(event.pointerId);
});
brailleStage.addEventListener("pointermove", (event) => {
  if (!brailleDrag) return;
  brailleTiltZ = brailleDrag.tiltZ + (event.clientX - brailleDrag.x) * 0.35;
  brailleTiltX = Math.max(28, Math.min(78, brailleDrag.tiltX - (event.clientY - brailleDrag.y) * 0.22));
  applyBrailleTilt();
});
brailleStage.addEventListener("pointerup", () => {
  brailleDrag = null;
});
brailleStage.addEventListener("pointercancel", () => {
  brailleDrag = null;
});
brailleStage.addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft") brailleTiltZ -= 8;
  else if (event.key === "ArrowRight") brailleTiltZ += 8;
  else if (event.key === "ArrowUp") brailleTiltX = Math.max(28, brailleTiltX - 6);
  else if (event.key === "ArrowDown") brailleTiltX = Math.min(78, brailleTiltX + 6);
  else return;
  event.preventDefault();
  applyBrailleTilt();
});

document.querySelector("#braille-prev").addEventListener("click", () => {
  brailleOffset = Math.max(0, brailleOffset - brailleWindow);
  renderBraille();
});
document.querySelector("#braille-next").addEventListener("click", () => {
  brailleOffset += brailleWindow;
  renderBraille();
});
applyBrailleTilt();

document.querySelector("#command-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = command.value.trim();
  if (!value) return;
  command.value = "";
  await turn({ text: value });
});

document.querySelectorAll("[data-command]").forEach((button) => {
  button.addEventListener("click", () => turn({ text: button.dataset.command }));
});

document.querySelector("#stop").addEventListener("click", () => turn({ text: "stop" }));
document.querySelector("#confirm-yes").addEventListener("click", () => turn({ text: "yes" }));
document.querySelector("#confirm-no").addEventListener("click", () => turn({ text: "no" }));

document.querySelector("#devkit-connection").addEventListener("click", async () => {
  const devkit = state.devices.find((device) => device.id === "devkit_tactile");
  const status = devkit && devkit.status === "disconnected" ? "simulator" : "disconnected";
  await post("/api/devices/devkit_tactile/connection", { status });
  state = await (await fetch(`/api/sessions/${state.session_id}`)).json();
  render();
});

document.querySelector("#actuator").addEventListener("click", async () => {
  const devkit = state.devices.find((device) => device.id === "devkit_tactile");
  const enabled = Boolean(devkit && devkit.status === "lockout");
  await post("/api/devices/devkit_tactile/actuator", { enabled });
  state = await (await fetch(`/api/sessions/${state.session_id}`)).json();
  render();
});

document.querySelector("#ping").addEventListener("click", async () => {
  const health = await post("/api/devices/devkit_tactile/health", {});
  live.textContent = health.detail;
});

document.querySelector("#box3-speech").addEventListener("click", async () => {
  state = await post(`/api/sessions/${state.session_id}/device-messages`, {
    protocol_version: "0.1",
    transport: "simulator",
    trace_id: "ui-box3",
    message_id: `ui-${Date.now()}`,
    sequence: 1,
    source: "esp32_s3_box3",
    destination: "mac_orchestrator",
    type: "speech.event",
    created_at_ms: Date.now(),
    payload: { transcript: "what is here" },
    requires_ack: false,
  });
  render();
});

document.querySelector("#p4-event").addEventListener("click", async () => {
  state = await post(`/api/sessions/${state.session_id}/device-messages`, {
    protocol_version: "0.1",
    transport: "usb_debug",
    trace_id: "ui-p4",
    message_id: `ui-${Date.now()}`,
    sequence: 1,
    source: "p4_eye",
    destination: "mac_orchestrator",
    type: "vision.event",
    created_at_ms: Date.now(),
    payload: { event: "user_present" },
    requires_ack: false,
  });
  render();
});

window.addEventListener("keydown", (event) => {
  const tag = event.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA") return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === "Escape" && state.pending_confirmation) {
    event.preventDefault();
    turn({ text: "no" });
    return;
  }
  if (state.pending_confirmation) return;
  if (/^[1-5]$/.test(event.key)) {
    const branch = state.branches[Number(event.key) - 1];
    if (branch) turn({ action_id: branch.id });
  }
});

async function openTarget(target) {
  text("#target-status", `Opening ${target}...`);
  const response = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  });
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    live.textContent = `Could not open ${target}: ${failure.error || response.status}.`;
    text("#target-status", live.textContent);
    return;
  }
  state = await response.json();
  render();
}

document.querySelectorAll("[data-target]").forEach((button) => {
  button.addEventListener("click", () => openTarget(button.dataset.target));
});

async function boot() {
  const wanted = new URLSearchParams(window.location.search).get("target") || "faq";
  await openTarget(wanted);
}

boot();
