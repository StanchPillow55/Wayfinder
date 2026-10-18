// Use by looking. A LAPTOP-CAMERA DEMO. It is not gaze tracking, not a blink detector,
// and not the P4X-EYE. It watches how much a face-sized box in the preview changes from
// frame to frame, in this tab only. No frame is stored, sent, or put in the page model.
//
// Signal (the part that is honest about what a webcam and a few lines of JS can do):
//   - A clearly large nod, lean, or hand wave in the box = "next".
//   - A longer large move, still clearly deliberate, = "select" the highlighted option.
//   - Small expression changes, blinks, brief motion, and ambiguous motion do nothing.
//   - Becoming still after an expression does not choose anything. An empty chair
//     or a blank stare cannot choose anything either.
//   - Choosing something that needs confirmation takes a select signal a second time.
//     Two quick nexts decline. The session decides what needs confirmation.
//
// Failure modes are shown in the UI: dim or changing light, a moving background, a
// camera that shifts, and the missing Face Detector. This is not gaze tracking.

const WayfinderVisionCore = (() => {
  const DEFAULTS = {
    motion: 28, // clearly larger than a blink or a small expression change
    big: 70, // so big it is a bumped camera or a lighting jump: ignored
    minFrames: 4, // a blink or a brief twitch is shorter than this
    minBurstMs: 320, // brief motion is not a nod
    selectBurstMs: 650, // a longer large move chooses; stillness never does
    burstMaxMs: 1200, // longer than this is walking around, not a nod
    burstCooldownMs: 400,
    selectCooldownMs: 1500,
  };

  // Mean absolute change between two gray frames after removing a global brightness shift
  // (auto exposure), so a camera adjusting itself is not read as a move.
  function frameEnergy(previous, current) {
    const count = Math.min(previous.length, current.length);
    if (!count) return 0;
    let sumPrevious = 0;
    let sumCurrent = 0;
    for (let i = 0; i < count; i += 1) {
      sumPrevious += previous[i];
      sumCurrent += current[i];
    }
    const shift = (sumCurrent - sumPrevious) / count;
    let total = 0;
    for (let i = 0; i < count; i += 1) total += Math.abs(current[i] - previous[i] - shift);
    return total / count;
  }

  class Selector {
    constructor(options = {}) {
      this.o = { ...DEFAULTS, ...options };
      this.reset();
      this.cooldownUntil = 0;
    }

    reset() {
      this.burstStart = null;
      this.burstFrames = 0;
      this.burstTooLong = false;
    }

    // One frame. t is ms, energy comes from frameEnergy, present is false when a face
    // detector is running and sees no face. Returns {event, status, progress}.
    // Stillness never confirms. A face that settles after an expression does nothing.
    push(t, energy, present = true) {
      const o = this.o;
      if (!present) {
        this.reset();
        return { event: null, status: "absent", progress: 0 };
      }
      if (energy >= o.big) {
        this.reset();
        return { event: null, status: "ignored", progress: 0 };
      }
      // Below the deliberate threshold: expression, blink-sized change, or quiet.
      // Ambiguous energy does not arm a choice and does not confirm.
      if (energy < o.motion) {
        if (this.burstStart === null) return { event: null, status: "steady", progress: 0 };
        const duration = t - this.burstStart;
        const deliberate =
          this.burstFrames >= o.minFrames &&
          duration >= o.minBurstMs &&
          !this.burstTooLong &&
          t >= this.cooldownUntil;
        const choose = deliberate && duration >= o.selectBurstMs;
        this.reset();
        if (choose) {
          this.cooldownUntil = t + o.selectCooldownMs;
          return { event: "select", status: "move", progress: 1 };
        }
        if (deliberate) {
          this.cooldownUntil = t + o.burstCooldownMs;
          return { event: "next", status: "move", progress: 0 };
        }
        return { event: null, status: "settling", progress: 0 };
      }
      if (this.burstStart === null) {
        this.burstStart = t;
        this.burstFrames = 0;
        this.burstTooLong = false;
      }
      this.burstFrames += 1;
      if (t - this.burstStart > o.burstMaxMs) this.burstTooLong = true;
      return { event: null, status: "moving", progress: 0 };
    }
  }

  // Mirrors plan_signal in src/wayfinder/access_input.py.
  function planSignal(signal, { cursor, branchIds, pending, recentNext = false }) {
    const count = branchIds.length;
    if (signal === "next") {
      if (pending) {
        if (recentNext) return { kind: "decline", cursor, post: "no", note: "Declined." };
        return {
          kind: "arm",
          cursor,
          note: "Confirmation waiting. A long deliberate move confirms, or move twice to decline.",
        };
      }
      if (!count) return { kind: "none", note: "No options to move through." };
      return { kind: "move", cursor: (cursor + 1) % count };
    }
    if (signal === "select") {
      if (pending) return { kind: "confirm", cursor, post: "yes", note: "Second signal. Confirming." };
      if (!count) return { kind: "none", note: "No option is selected." };
      const index = cursor % count;
      return { kind: "select", cursor: index, actionId: branchIds[index] };
    }
    return { kind: "none", note: "Unknown signal." };
  }

  // Mirrors VisionMapper in Python. For a future P4X-EYE vision.event. Holds no media.
  const STEADY_EVENTS_TO_SELECT = 3;
  class VisionMapper {
    constructor() {
      this.armed = false;
      this.steady = 0;
    }

    feed(payload) {
      const event = payload && payload.event;
      if (event === "scene_changed") {
        this.armed = true;
        this.steady = 0;
        return "next";
      }
      if (event === "user_present") {
        if (!this.armed) return null;
        this.steady += 1;
        if (this.steady >= STEADY_EVENTS_TO_SELECT) {
          this.armed = false;
          this.steady = 0;
          return "select";
        }
        return null;
      }
      return null; // scene_summary is untrusted text: evidence only, never an action
    }
  }

  return { DEFAULTS, frameEnergy, Selector, planSignal, VisionMapper, STEADY_EVENTS_TO_SELECT };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = WayfinderVisionCore;
}

if (typeof document !== "undefined" && window.WayfinderApp) {
  (() => {
    const app = window.WayfinderApp;
    const core = WayfinderVisionCore;

    const toggle = document.querySelector("#vision-toggle");
    const offButton = document.querySelector("#vision-off");
    const muteButton = document.querySelector("#vision-mute");
    const chip = document.querySelector("#chip-camera");
    const stateNode = document.querySelector("#vision-state");
    const cursorNode = document.querySelector("#vision-cursor");
    const stage = document.querySelector("#vision-stage");
    const video = document.querySelector("#vision-video");
    const overlay = document.querySelector("#vision-overlay");
    const tuning = document.querySelector("#vision-tuning");
    const threshold = document.querySelector("#vision-threshold");
    const meter = document.querySelector("#vision-meter");

    const WORK_W = 80;
    const WORK_H = 60;
    // The face-sized box, as fractions of the frame. Fixed: the user centers their face in it.
    const BOX = { x: 0.25, y: 0.08, w: 0.5, h: 0.84 };

    let on = false;
    let starting = false;
    let paused = false;
    // Bumped by an explicit off so a permission request still in flight cannot turn the camera back on.
    let cameraToken = 0;
    let blocked = false;
    let stream = null;
    let timer = null;
    let detectTimer = null;
    let detector = null;
    let facePresent = true;
    let previous = null;
    let selector = null;
    let mapper = new core.VisionMapper();
    let lastNextAt = -Infinity;
    let lastStatusKey = "";
    const work = document.createElement("canvas");
    work.width = WORK_W;
    work.height = WORK_H;
    const workContext = work.getContext("2d", { willReadFrequently: true });

    function setState(message) {
      stateNode.textContent = message;
    }

    function cameraStatus() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return "unsupported";
      if (blocked) return "blocked";
      if (!on) return "available";
      return paused ? "muted" : "armed";
    }

    // Status words only go to the Mac. No frame, image, or pixel ever does.
    function report() {
      const status = cameraStatus();
      const words = {
        available: "CAMERA off",
        armed: "CAMERA on",
        muted: "CAMERA muted",
        blocked: "CAMERA blocked",
        unsupported: "CAMERA not supported here",
      };
      const motion = "A clear nod or wave moves to the next choice. Holding still does not choose.";
      const live = {
        available: "CAMERA off.",
        armed: "CAMERA on.",
        muted: "CAMERA on.",
        blocked: "Camera permission was denied. The camera stays off.",
        unsupported: "CAMERA not supported in this browser.",
      };
      if (chip) {
        chip.textContent = words[status];
        chip.dataset.status = status;
      }
      muteButton.hidden = !on;
      muteButton.textContent = paused ? "Unmute camera" : "Mute camera";
      muteButton.setAttribute("aria-pressed", String(paused));
      // Arm stays pressed while the camera is on. Denial leaves it unpressed.
      toggle.setAttribute("aria-pressed", on ? "true" : "false");
      toggle.textContent = on ? "Camera on" : "Arm camera";
      app.setCaptureStatus({
        camera: live[status],
        motion: status === "armed" || status === "muted" ? motion : "",
      });
      app.reportChannel({ camera: status });
    }

    function setPaused(next) {
      paused = next;
      if (stream) stream.getTracks().forEach((track) => (track.enabled = !paused));
      previous = null;
      if (paused) setState("Camera muted. Nothing is being watched. Unmute it to move and choose by looking.");
      else setState("Camera unmuted. Laptop-camera demo, not gaze tracking. A clear nod moves to the next choice.");
      report();
    }

    function branchButtons() {
      return Array.from(document.querySelectorAll("#branches .branch"));
    }

    function getCursor() {
      const index = branchButtons().findIndex((button) => button.getAttribute("aria-current") === "true");
      return index < 0 ? 0 : index;
    }

    function setCursor(index) {
      branchButtons().forEach((button, position) => {
        const active = position === index;
        button.classList.toggle("access-cursor", active);
        if (active) button.setAttribute("aria-current", "true");
        else button.removeAttribute("aria-current");
      });
    }

    function describeCursor(state, index) {
      const branch = state.branches[index];
      if (!branch) return "No option is highlighted.";
      return `Highlighted: option ${index + 1} of ${state.branches.length}, ${branch.label}. ${branch.risk_label}.`;
    }

    function showCursor() {
      const state = app.state();
      if (!state) return;
      if (state.pending_confirmation) {
        cursorNode.textContent = `Confirmation waiting: ${state.pending_confirmation.label}. A long deliberate move confirms. Two quick moves decline. Or tap Yes or No.`;
      } else {
        cursorNode.textContent = describeCursor(state, getCursor());
      }
    }

    async function send(body) {
      try {
        await app.turn({ ...body, channel: "vision" });
      } catch (_error) {
        setState("That did not reach the session. Nothing changed.");
      }
    }

    // Apply one signal ("next" or "select") from the camera or a device event.
    async function apply(signal, now) {
      const state = app.state();
      if (!state) return;
      const plan = core.planSignal(signal, {
        cursor: getCursor(),
        branchIds: state.branches.map((branch) => branch.id),
        pending: Boolean(state.pending_confirmation),
        recentNext: now - lastNextAt < 1500,
      });
      if (signal === "next") lastNextAt = now;
      if (plan.kind === "move") {
        setState("Clear move. Next option.");
        await app.moveCursor(plan.cursor, "camera");
        showCursor();
      } else if (plan.kind === "select") {
        setCursor(plan.cursor);
        const label = state.branches[plan.cursor] ? state.branches[plan.cursor].label : "option";
        setState(`Long move. Chose: ${label}.`);
        await send({ action_id: plan.actionId });
        showCursor();
      } else if (plan.kind === "confirm") {
        setState("Second signal seen. Confirming now.");
        await send({ text: plan.post });
        showCursor();
      } else if (plan.kind === "decline") {
        setState("Two quick moves seen. Declined. Nothing was submitted.");
        await send({ text: plan.post });
        showCursor();
      } else if (plan.kind === "arm") {
        setState(plan.note);
        showCursor();
      } else if (plan.note) {
        setState(plan.note);
      }
    }

    function grayRegion() {
      workContext.drawImage(video, 0, 0, WORK_W, WORK_H);
      const x0 = Math.floor(BOX.x * WORK_W);
      const y0 = Math.floor(BOX.y * WORK_H);
      const w = Math.floor(BOX.w * WORK_W);
      const h = Math.floor(BOX.h * WORK_H);
      const data = workContext.getImageData(x0, y0, w, h).data;
      const gray = new Float32Array(w * h);
      for (let i = 0; i < gray.length; i += 1) {
        gray[i] = data[i * 4] * 0.3 + data[i * 4 + 1] * 0.59 + data[i * 4 + 2] * 0.11;
      }
      return gray;
    }

    function drawOverlay(status, progress) {
      const context = overlay.getContext("2d");
      context.clearRect(0, 0, overlay.width, overlay.height);
      const x = BOX.x * overlay.width;
      const y = BOX.y * overlay.height;
      const w = BOX.w * overlay.width;
      const h = BOX.h * overlay.height;
      const colors = {
        moving: "#ffcc00",
        holding: "#38bdf8",
        held: "#22c55e",
        move: "#22c55e",
        absent: "#ef4444",
        ignored: "#ef4444",
      };
      context.lineWidth = 4;
      context.strokeStyle = colors[status] || "#ffffff";
      context.strokeRect(x, y, w, h);
      if (status === "holding") {
        context.fillStyle = "#38bdf8";
        context.fillRect(x, y + h + 4, w * progress, 6);
      }
    }

    const STATUS_TEXT = {
      absent: "No face seen in the box. Nothing will be chosen. Move into the box.",
      ignored: "The picture jumped (light change or the camera moved). Ignored.",
      moving: "Large move. A short one goes to the next choice. A longer one chooses it.",
      settling: "That movement was too small, too short, or too long. Nothing was chosen.",
      steady: "CAMERA on. A clear nod or wave moves to the next choice. Holding still does nothing.",
    };

    function frame() {
      if (!on || paused || video.readyState < 2) return;
      const now = performance.now();
      const current = grayRegion();
      const energy = previous ? core.frameEnergy(previous, current) : 0;
      previous = current;
      const result = selector.push(now, energy, facePresent);
      meter.textContent = `Motion ${energy.toFixed(1)} of ${selector.o.motion} needed. ${result.status}.`;
      drawOverlay(result.status, result.progress);
      if (result.event) {
        lastStatusKey = "";
        apply(result.event, now);
        return;
      }
      // Only change the live region when the coarse status changes, not every frame.
      const key = result.status;
      if (key !== lastStatusKey && STATUS_TEXT[key]) {
        lastStatusKey = key;
        setState(STATUS_TEXT[key]);
      }
    }

    async function detectLoop() {
      if (!detector || !on || video.readyState < 2) return;
      try {
        const faces = await detector.detect(video);
        facePresent = faces.length > 0;
      } catch (_error) {
        // Detector failed: stop trusting it, fall back to the fixed box.
        detector = null;
        facePresent = true;
        setState("The browser's Face Detector failed, so a fixed box is used and nobody checks that a face is in it.");
      }
    }

    async function start() {
      // Already on, or a permission request is in flight. Never stop from this path.
      if (on || starting) return;
      if (blocked) {
        setState("Camera permission was denied. The camera stays off. Nothing is being captured.");
        toggle.setAttribute("aria-pressed", "false");
        report();
        return;
      }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        setState(
          "This browser cannot open a camera here. It needs getUserMedia on localhost or https. " +
            "Nothing is being captured. The page works without the camera.",
        );
        return;
      }
      const mine = ++cameraToken;
      starting = true;
      setState("Asking for the laptop camera. Allow it if the browser asks.");
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { width: 320, height: 240, facingMode: "user" },
          audio: false,
        });
      } catch (error) {
        const denied = error && (error.name === "NotAllowedError" || error.name === "SecurityError");
        starting = false;
        on = false;
        stream = null;
        blocked = Boolean(denied);
        toggle.setAttribute("aria-pressed", "false");
        setState(
          denied
            ? "Camera permission was denied. The camera stays off. Nothing is being captured."
            : `The camera could not be opened (${error && error.name ? error.name : "unknown error"}). The camera stays off. Nothing is being captured.`,
        );
        report();
        return;
      }
      if (mine !== cameraToken) {
        starting = false;
        stream.getTracks().forEach((track) => track.stop());
        stream = null;
        return;
      }
      starting = false;
      blocked = false;
      paused = false;
      video.srcObject = stream;
      try {
        await video.play();
      } catch (_error) {
        // Autoplay of a muted camera stream is allowed. If not, frames simply will not arrive.
      }
      on = true;
      toggle.setAttribute("aria-pressed", "true");
      stage.hidden = false;
      tuning.hidden = false;
      previous = null;
      selector = new core.Selector({ motion: Number(threshold.value) });
      mapper = new core.VisionMapper();
      lastStatusKey = "";
      facePresent = true;
      detector = null;
      if ("FaceDetector" in window) {
        try {
          detector = new window.FaceDetector({ fastMode: true, maxDetectedFaces: 1 });
        } catch (_error) {
          detector = null;
        }
      }
      setState(
        detector
          ? "CAMERA on. A clear nod or wave moves to the next choice. Holding still does nothing. A face check is running. This is not gaze tracking."
          : "CAMERA on. A clear nod or wave moves to the next choice. Holding still does nothing. This browser has no Face Detector, so a fixed box is used. This is not gaze tracking.",
      );
      timer = window.setInterval(frame, 90);
      if (detector) detectTimer = window.setInterval(detectLoop, 300);
      showCursor();
      report();
    }

    function stop(message) {
      cameraToken += 1;
      on = false;
      starting = false;
      paused = false;
      if (timer) window.clearInterval(timer);
      if (detectTimer) window.clearInterval(detectTimer);
      timer = null;
      detectTimer = null;
      if (stream) stream.getTracks().forEach((track) => track.stop());
      stream = null;
      video.srcObject = null;
      stage.hidden = true;
      tuning.hidden = true;
      toggle.setAttribute("aria-pressed", "false");
      setState(message || "Camera is off.");
      cursorNode.textContent = "No option is highlighted by the camera selector.";
      report();
    }

    toggle.addEventListener("click", () => start());
    offButton.addEventListener("click", () => {
      // The gesture that arms capture must not also run this stop.
      if (app.armingGesture()) return;
      if (!on && !starting) {
        setState("Camera is already off.");
        return;
      }
      stop("Camera is off. Nothing is being captured.");
    });
    muteButton.addEventListener("click", () => {
      if (app.armingGesture()) return;
      if (!on) return;
      setPaused(!paused);
    });
    window.addEventListener("keydown", (event) => {
      if (event.metaKey || event.ctrlKey || event.altKey || event.repeat) return;
      const tag = event.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (event.key.toLowerCase() === "c") {
        event.preventDefault();
        start();
      }
    });

    threshold.addEventListener("input", () => {
      if (selector) selector.o.motion = Number(threshold.value);
    });

    document.addEventListener("wayfinder:render", (event) => {
      setCursor(event.detail.branch_cursor ? event.detail.branch_cursor.index : 0);
      if (on) showCursor();
      report();
    });
    report();

    window.addEventListener("pagehide", () => {
      if (stream) stream.getTracks().forEach((track) => track.stop());
    });

    // A future P4X-EYE vision.event payload ({event, summary}) can be handed here. No frames
    // are received by this page. This maps only the same next and select signals and is
    // active only while "Use by looking" is on.
    window.WayfinderVision = {
      cameraStatus,
      arm: start,
      handleDeviceEvent(payload) {
        if (!on || paused) return null;
        const signal = mapper.feed(payload);
        if (signal) apply(signal, performance.now());
        return signal;
      },
    };
  })();
}
