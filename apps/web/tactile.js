/**
 * Tactile loop panel. Sits beside the Braille plate and does not touch its animation.
 *
 * Out: when the plate finishes forming a word (event "wayfinder:braille-word" from
 * braille.js), ask the Mac for one short motor cue. The Mac sends it as tactile.render
 * when a DevKit is bound. With no board, this panel still shows the cue and says the
 * motor has not spun.
 *
 * In: on-screen buttons and a slider send the same actions as the DevKit demo
 * buttons and analog input. They are stand-ins. A light poll picks up real board
 * events that the Mac already applied to the session.
 *
 * Demo map, not a clinical device. A cue does not prove the motor rotated.
 */
(() => {
  const POLL_MS = 1500;
  const MAX_QUEUED = 12;

  const ORIGIN_TEXT = {
    on_screen: "an on-screen stand-in, not the board",
    board_usb: "the DevKit over USB",
    loopback: "a loopback test stand-in, not a board",
    simulated: "a simulated message, not a board",
  };

  let lastState = null;
  let lastSpeech = null;
  let generation = 0;
  let queue = Promise.resolve();
  let queued = 0;
  let polling = false;
  let lastSeq = 0;
  let sliderTimer = null;

  function node(id) {
    return document.querySelector(id);
  }

  function setText(id, value) {
    const element = node(id);
    if (element) element.textContent = value;
  }

  function describeCue(cue) {
    const word = `"${cue.word}"`;
    const place = cue.word_count > 1 ? ` (word ${cue.word_index + 1} of ${cue.word_count})` : "";
    return (
      `Latest motor cue for ${word}${place}: ${cue.duty_percent}% duty for ${cue.duration_ms} ms, ` +
      `${cue.direction}, pose ${cue.state_token}. ${cue.note}`
    );
  }

  function showCue(cue, live) {
    if (!cue) return;
    setText("#tactile-cue-text", describeCue(cue));
    if (!live) return;
    // The existing motor bar shows the planned duty. It is a software view, not a measurement.
    const bar = node("#motor-bar");
    if (bar) bar.style.width = `${Number(cue.duty_percent) || 0}%`;
  }

  function showInput(input, cursor) {
    if (input) {
      const level = input.level === null || input.level === undefined ? "" : ` Level ${input.level}%.`;
      const origin = ORIGIN_TEXT[input.origin] || input.origin;
      setText("#tactile-input-text", `Last input: ${input.label}.${level} Came from ${origin}.`);
    } else {
      setText("#tactile-input-text", "No button or analog event yet.");
    }
    setText(
      "#tactile-cursor-text",
      cursor
        ? `Highlighted branch ${cursor.position} of ${cursor.count}: ${cursor.label}.`
        : "No branch highlighted.",
    );
  }

  function publishState(next) {
    window.dispatchEvent(new CustomEvent("wayfinder:session-state", { detail: next }));
  }

  async function sendCue(detail, gen) {
    if (gen !== generation || !lastState) return;
    try {
      const response = await fetch(`/api/sessions/${lastState.session_id}/tactile/cue`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ word: detail.word, index: detail.index, count: detail.count }),
      });
      if (!response.ok) throw new Error(String(response.status));
      const cue = await response.json();
      if (gen === generation) showCue(cue, true);
    } catch (error) {
      if (gen === generation) {
        setText("#tactile-cue-text", "The word cue could not be sent. The motor has not spun.");
      }
    }
  }

  function onWord(event) {
    if (!lastState || queued >= MAX_QUEUED) return;
    const gen = generation;
    queued += 1;
    queue = queue
      .then(() => sendCue(event.detail, gen))
      .finally(() => {
        queued -= 1;
      });
  }

  async function sendInput(kind, level) {
    if (!lastState) return;
    const body = { input: kind };
    if (kind === "analog") body.level = level;
    const response = await fetch(`/api/sessions/${lastState.session_id}/tactile/input`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      setText("#tactile-input-text", "That input could not be applied. The page is unchanged.");
      return;
    }
    publishState(await response.json());
  }

  async function poll() {
    if (polling || document.hidden || !lastState) return;
    polling = true;
    try {
      const sessionId = lastState.session_id;
      const response = await fetch(`/api/sessions/${sessionId}/tactile`);
      if (!response.ok) return;
      const status = await response.json();
      if (!lastState || lastState.session_id !== sessionId) return;
      if (status.input_seq !== lastSeq || status.turn_id !== lastState.turn_id) {
        const full = await fetch(`/api/sessions/${sessionId}`);
        if (full.ok) publishState(await full.json());
      }
    } catch (error) {
      // No Mac answer this round. The next poll retries.
    } finally {
      polling = false;
    }
  }

  function bind() {
    for (const button of document.querySelectorAll("[data-tactile-input]")) {
      button.addEventListener("click", () => sendInput(button.dataset.tactileInput));
    }
    const slider = node("#tactile-analog");
    if (slider) {
      slider.addEventListener("input", () => {
        clearTimeout(sliderTimer);
        sliderTimer = setTimeout(() => sendInput("analog", Number(slider.value)), 180);
      });
    }
    window.addEventListener("wayfinder:braille-word", onWord);
    setInterval(poll, POLL_MS);
  }

  function render(state) {
    lastState = state;
    lastSeq = state.tactile_input ? state.tactile_input.seq : 0;
    if (state.speech !== lastSpeech) {
      // A new line of speech restarts the plate. Drop cues that belong to the old one.
      lastSpeech = state.speech;
      generation += 1;
    }
    showInput(state.tactile_input, state.branch_cursor);
    if (state.tactile_cue) showCue(state.tactile_cue, false);
  }

  function init() {
    bind();
  }

  window.WayfinderTactile = { init, render };
})();
