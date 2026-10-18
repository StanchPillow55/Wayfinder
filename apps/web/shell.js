/**
 * The multimodal shell: one web interface over the browser session. It reads the one
 * semantic state the Mac builds for each turn and shows it as channel status, the capability
 * the router selected, the registry, the goal and path, and the honest hardware status.
 * It does not interpret anything itself and it holds no task state.
 */
(() => {
  const $ = (selector) => document.querySelector(selector);

  function list(target, items, build) {
    const node = typeof target === "string" ? $(target) : target;
    node.replaceChildren();
    for (const item of items) {
      const li = document.createElement("li");
      build(li, item);
      node.append(li);
    }
    return node;
  }

  function setText(selector, value) {
    $(selector).textContent = value;
  }

  function renderChips(state) {
    const muted = state.speech_muted;
    const audio = $("#chip-audio");
    audio.textContent = muted ? "AUDIO: muted" : "AUDIO: speaking";
    audio.dataset.status = muted ? "muted" : "armed";
    const memory = $("#chip-memory");
    memory.textContent = state.memory.project ? `MEMORY: session + project “${state.memory.project}” (local)` : "MEMORY: session only";
    memory.dataset.status = "armed";
    $("#chip-renderer").textContent = `OUTPUT: ${state.channels.renderer_label}`;
    $("#memory-scope").textContent = state.memory.scope;
  }

  function renderRouter(state) {
    setText("#router-line", state.selected ? state.selected.line : "No capability selected yet.");
    list("#selections", state.selections.slice(-6), (li, item) => {
      li.textContent = `${item.line}. Trust: ${item.trust}.`;
    });
  }

  function renderRenderer(state) {
    for (const button of document.querySelectorAll("[data-renderer]")) {
      button.setAttribute("aria-pressed", String(button.dataset.renderer === state.renderer));
    }
    const label = state.channels.renderer_label;
    setText(
      "#renderer-status",
      `Output: ${label}. Speech can keep going. The page and the choices stay on screen. Braille and the motor cue are an extra sensation, not a replacement. Your goal, path, and any waiting confirmation stay.`,
    );
    document.body.dataset.renderer = state.renderer;
  }

  function renderChannels(state) {
    list("#channels-in", state.channels.input, (li, item) => {
      li.textContent = `${item.label}: ${item.status.replace("_", " ")}. ${item.note}`;
    });
    list("#channels-out", state.channels.output, (li, item) => {
      li.textContent = `${item.label}: ${item.status}${item.foreground ? ", foreground" : ""}.`;
    });
  }

  function renderCapabilities(state) {
    list("#capabilities", state.capabilities, (li, item) => {
      li.className = item.acting ? "cap acting" : "cap";
      const head = document.createElement("strong");
      head.textContent = item.name;
      const meta = document.createElement("span");
      meta.className = "risk";
      const perms = item.permissions.length ? item.permissions.join(", ") : "no page permissions";
      meta.textContent =
        ` ${item.role} Trust: ${item.trust}. Permissions: ${perms}. ${item.availability}` +
        `${item.acting ? ", acting this turn" : ""}${item.selected ? ", selected" : ""}.`;
      li.append(head, meta);
      const row = document.createElement("div");
      row.className = "row";
      if (!item.locked) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = item.enabled ? `Turn ${item.name} off` : `Turn ${item.name} on`;
        button.setAttribute("aria-pressed", String(item.enabled));
        button.addEventListener("click", () =>
          window.WayfinderApp.sessionAction("capabilities", { id: item.id, enabled: !item.enabled }),
        );
        row.append(button);
      } else {
        const note = document.createElement("span");
        note.className = "hint";
        note.textContent = "Always on. This role keeps the trust boundaries.";
        row.append(note);
      }
      if (item.try_it && item.enabled) {
        const tryButton = document.createElement("button");
        tryButton.type = "button";
        tryButton.textContent = `Try: ${item.try_it}`;
        tryButton.addEventListener("click", () => window.WayfinderApp.turn({ text: item.try_it }));
        row.append(tryButton);
      }
      li.append(row);
    });
  }

  function renderGoal(state) {
    const plan = state.goal_plan;
    setText(
      "#goal-plan-text",
      plan
        ? `Goal: ${plan.text}. Status: ${plan.status.replace("_", " ")}.${plan.waiting_for ? ` Waiting for: ${plan.waiting_for}.` : ""} ${plan.limit}`
        : "No goal plan yet. Say a goal such as: find the hours, request the north room, or add the talking kitchen scale to my cart.",
    );
    list("#goal-steps", plan ? plan.steps : [], (li, step) => {
      li.textContent = `${step.label} (${step.status.replace("_", " ")})`;
    });
    list("#journey", state.journey, (li, item) => {
      li.textContent = item.text;
    });
    const memory = state.memory;
    setText(
      "#prefs-line",
      `Preferences kept on this Mac: detail ${memory.preferences.density}, about ${memory.preferences.branch_limit} options` +
        `${memory.preferences.preferred_channels.length ? `, prefers ${memory.preferences.preferred_channels.join(" and ")}` : ""}. ` +
        "Preferences only. No diagnosis, no audio, no video.",
    );
    setText(
      "#projects-line",
      memory.projects.length
        ? `Saved projects: ${memory.projects.join(", ")}. Say: continue my ${memory.projects[0]}.`
        : "No saved projects yet. Name this task to continue it later.",
    );
  }

  function renderSemantic(state) {
    const s = state.semantic_state;
    setText("#sem-situation", `${s.situation.title}. ${s.situation.purpose} (${s.situation.family || s.situation.mode})`);
    list("#sem-matters", s.what_matters, (li, text) => (li.textContent = text));
    list("#sem-actions", s.actions, (li, action) => {
      li.textContent =
        `${action.n}. ${action.label}. ${action.risk_label}${action.needs_confirmation ? ", asks first" : ""}. ` +
        `Confidence: ${action.confidence}. Consequence: ${action.consequence}`;
    });
    list("#sem-warnings", s.warnings, (li, text) => (li.textContent = text));
    list("#sem-path", s.prior_path, (li, text) => (li.textContent = text));
    setText(
      "#sem-goal",
      `Goal: ${s.user_goal.text || "none yet"}. Constraints: ${s.user_goal.constraints.join(", ") || "none"}. Plan: ${s.user_goal.plan_status}.`,
    );
    const c = state.compiled;
    setText(
      "#compiled-line",
      `Every channel below is rendered from this one state (${c.source.schema}, turn ${c.source.turn_id.slice(0, 8)}): ` +
        `speech ${c.speech.muted ? "muted" : "on"}, ${c.visual.cards.length} cards, ${c.braille.cells.length} Braille cells, ` +
        `tactile cue ${c.tactile.state_token}, ${c.glance.lines.length} glance lines.`,
    );
  }

  function renderRepairs(state) {
    const repairs = state.page.repairs || [];
    list("#repairs", repairs, (li, item) => {
      li.textContent =
        `${item.kind} ${item.ref}: suggested name “${item.inferred_name}” (inferred from ${item.source}).` +
        `${item.sensitive ? " This looks like a sensitive field, so it is never filled." : ""}`;
    });
    $("#repairs-empty").hidden = repairs.length > 0;
    const semantics = state.page.semantics;
    setText(
      "#semantics-text",
      semantics
        ? semantics.text || semantics.limit
        : "No page semantics to describe on this page.",
    );
    setText("#semantics-limit", semantics && semantics.limit ? `Limit: ${semantics.limit}` : "");
  }

  function renderHardware(state) {
    const hw = state.hardware;
    setText("#hardware-statement", hw.statement);
    list(
      "#hardware-facts",
      [
        `physical_motor_ran: ${hw.physical_motor_ran}. The motor has not spun.`,
        `Firmware flashed: no. Nothing here claims a board was flashed.`,
        `P4X-EYE: ${hw.p4_connected ? "a link answered" : "not connected"}, and not live. The laptop webcam is the vision path.`,
        "BOX-3 and the DevKit are simulated unless a USB acknowledgement says otherwise.",
      ],
      (li, text) => (li.textContent = text),
    );
  }

  function render(state) {
    renderChips(state);
    renderRouter(state);
    renderRenderer(state);
    renderChannels(state);
    renderCapabilities(state);
    renderGoal(state);
    renderSemantic(state);
    renderRepairs(state);
    renderHardware(state);
  }

  document.querySelectorAll("[data-renderer]").forEach((button) => {
    button.addEventListener("click", () => {
      window.WayfinderApp.cancelSpeech();
      window.WayfinderApp.sessionAction("renderer", { renderer: button.dataset.renderer }).then(() => {
        // Hands-free expects the mic and camera to be armed. Arming is idempotent, so this cannot turn them off.
        if (button.dataset.renderer === "hands_free") {
          if (window.WayfinderVoice && window.WayfinderVoice.arm) window.WayfinderVoice.arm();
          if (window.WayfinderVision && window.WayfinderVision.arm) window.WayfinderVision.arm();
        }
      });
    });
  });

  $("#project-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("#project-name").value.trim();
    if (name.length < 2) return;
    $("#project-name").value = "";
    window.WayfinderApp.sessionAction("projects", { name });
  });

  window.WayfinderShell = { render };
})();
