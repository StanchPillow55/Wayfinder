/** 3D Grade 1 Braille plate: persistent pins, staggered word/cell animation. */
(() => {
  const COLS = 16;
  const ROWS = 2;
  const WINDOW = COLS * ROWS;
  const DOT_ORDER = [1, 4, 2, 5, 3, 6];
  const CELL_STAGGER_MS = 52;
  const WORD_GAP_MS = 140;
  const PIN_TRANSITION_MS = 220;

  let offset = 0;
  let lastSpeech = "";
  let tiltX = 62;
  let tiltZ = -22;
  let drag = null;
  let animToken = 0;
  let pendingTimers = [];

  let scene = null;
  let stage = null;
  let slots = [];
  let lastState = null;
  let lastViewSig = "";

  function text(id, value) {
    document.querySelector(id).textContent = value;
  }

  function wordRangesFromCells(cells) {
    const ranges = [];
    let start = null;
    for (let i = 0; i < cells.length; i += 1) {
      if (cells[i] === 0) {
        if (start !== null) {
          ranges.push({ start, end: i - 1 });
          start = null;
        }
      } else if (start === null) {
        start = i;
      }
    }
    if (start !== null) ranges.push({ start, end: cells.length - 1 });
    return ranges;
  }

  function cancelAnimation() {
    animToken += 1;
    for (const timer of pendingTimers) clearTimeout(timer);
    pendingTimers = [];
  }

  function schedule(ms, fn) {
    const token = animToken;
    const timer = setTimeout(() => {
      if (token !== animToken) return;
      fn();
    }, ms);
    pendingTimers.push(timer);
  }

  function applyMaskToSlot(slotIndex, mask) {
    const cell = slots[slotIndex];
    if (!cell) return;
    cell.dataset.mask = String(mask);
    for (const pin of cell.querySelectorAll(".pin")) {
      const dot = Number(pin.dataset.dot);
      const up = Boolean(mask & (1 << (dot - 1)));
      pin.classList.toggle("up", up);
    }
  }

  function buildPlate() {
    const plate = document.querySelector("#braille-plate");
    plate.replaceChildren();
    slots = [];
    for (let row = 0; row < ROWS; row += 1) {
      const line = document.createElement("div");
      line.className = "braille-line";
      for (let col = 0; col < COLS; col += 1) {
        const cell = document.createElement("div");
        cell.className = "braille-cell";
        cell.dataset.slot = String(row * COLS + col);
        for (const dot of DOT_ORDER) {
          const pin = document.createElement("span");
          pin.className = "pin";
          pin.dataset.dot = String(dot);
          pin.style.transitionDuration = `${PIN_TRANSITION_MS}ms`;
          cell.append(pin);
        }
        line.append(cell);
        slots.push(cell);
      }
      plate.append(line);
    }
  }

  function visibleMasks(cells) {
    const masks = [];
    for (let i = 0; i < WINDOW; i += 1) {
      const index = offset + i;
      masks.push(index < cells.length ? cells[index] : -1);
    }
    return masks;
  }

  function viewSignature(cells, speech) {
    return `${speech}\0${offset}\0${cells.join(",")}`;
  }

  function animateWindowStagger(cells) {
    cancelAnimation();
    const token = animToken;
    const target = visibleMasks(cells);
    let delay = 0;
    for (let slot = 0; slot < WINDOW; slot += 1) {
      const mask = target[slot];
      const value = mask < 0 ? 0 : mask;
      schedule(delay, () => {
        if (token !== animToken) return;
        applyMaskToSlot(slot, value);
      });
      delay += CELL_STAGGER_MS;
    }
  }

  function animateWordsForming(cells) {
    cancelAnimation();
    const token = animToken;
    for (let slot = 0; slot < WINDOW; slot += 1) {
      applyMaskToSlot(slot, 0);
    }

    const ranges = wordRangesFromCells(cells);
    let delay = PIN_TRANSITION_MS * 0.35;

    const queueCell = (cellIndex, mask) => {
      if (cellIndex < offset || cellIndex >= offset + WINDOW) return null;
      const slot = cellIndex - offset;
      const at = delay;
      schedule(delay, () => {
        if (token !== animToken) return;
        applyMaskToSlot(slot, mask);
      });
      delay += CELL_STAGGER_MS;
      return at;
    };

    // Hook for the tactile loop (tactile.js): tell it when each word has finished forming.
    const words = lastState?.braille?.words || [];
    for (const [wordIndex, range] of ranges.entries()) {
      let formedAt = null;
      for (let ci = range.start; ci <= range.end; ci += 1) {
        const at = queueCell(ci, cells[ci]);
        if (at !== null) formedAt = at;
      }
      if (formedAt !== null && words[wordIndex]) {
        const detail = { word: words[wordIndex], index: wordIndex, count: ranges.length };
        schedule(formedAt, () => {
          window.dispatchEvent(new CustomEvent("wayfinder:braille-word", { detail }));
        });
      }
      delay += WORD_GAP_MS;
    }
  }

  function updateCaption(cells, note) {
    const shown = Math.min(WINDOW, Math.max(0, cells.length - offset));
    const start = cells.length ? offset + 1 : 0;
    const end = cells.length ? offset + shown : 0;
    const captionNote = note || "Software Grade 1 projection of the live speech.";
    text(
      "#braille-caption",
      cells.length
        ? `Grade 1 Braille, cells ${start}–${end} of ${cells.length}. ${captionNote}`
        : "No Braille cells yet.",
    );
    const maxOffset = Math.max(0, Math.ceil(cells.length / WINDOW) * WINDOW - WINDOW);
    document.querySelector("#braille-prev").disabled = offset === 0;
    document.querySelector("#braille-next").disabled = offset >= maxOffset;
  }

  function applyTilt() {
    scene.style.transform = `rotateX(${tiltX}deg) rotateZ(${tiltZ}deg)`;
  }

  function render(state) {
    lastState = state;
    const cells = state.braille?.cells || [];
    const speech = state.speech || "";
    const speechChanged = speech !== lastSpeech;
    if (speechChanged) {
      lastSpeech = speech;
      offset = 0;
    }
    const maxOffset = Math.max(0, Math.ceil(cells.length / WINDOW) * WINDOW - WINDOW);
    if (offset > maxOffset) offset = maxOffset;

    const sig = viewSignature(cells, speech);
    if (sig === lastViewSig) {
      updateCaption(cells, state.braille?.note);
      return;
    }
    lastViewSig = sig;

    if (speechChanged) animateWordsForming(cells);
    else animateWindowStagger(cells);
    updateCaption(cells, state.braille?.note);
  }

  function shiftWindow(delta) {
    const cells = lastState?.braille?.cells || [];
    offset = Math.max(0, offset + delta);
    const maxOffset = Math.max(0, Math.ceil(cells.length / WINDOW) * WINDOW - WINDOW);
    if (offset > maxOffset) offset = maxOffset;
    lastViewSig = viewSignature(cells, lastSpeech);
    animateWindowStagger(cells);
    updateCaption(cells, lastState?.braille?.note);
  }

  function bindControls() {
    stage.addEventListener("pointerdown", (event) => {
      drag = { x: event.clientX, y: event.clientY, tiltX, tiltZ };
      stage.setPointerCapture(event.pointerId);
    });
    stage.addEventListener("pointermove", (event) => {
      if (!drag) return;
      tiltZ = drag.tiltZ + (event.clientX - drag.x) * 0.35;
      tiltX = Math.max(28, Math.min(78, drag.tiltX - (event.clientY - drag.y) * 0.22));
      applyTilt();
    });
    stage.addEventListener("pointerup", () => {
      drag = null;
    });
    stage.addEventListener("pointercancel", () => {
      drag = null;
    });
    stage.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft") tiltZ -= 8;
      else if (event.key === "ArrowRight") tiltZ += 8;
      else if (event.key === "ArrowUp") tiltX = Math.max(28, tiltX - 6);
      else if (event.key === "ArrowDown") tiltX = Math.min(78, tiltX + 6);
      else return;
      event.preventDefault();
      applyTilt();
    });

    document.querySelector("#braille-prev").addEventListener("click", () => {
      shiftWindow(-WINDOW);
    });
    document.querySelector("#braille-next").addEventListener("click", () => {
      shiftWindow(WINDOW);
    });
  }

  function init() {
    scene = document.querySelector("#braille-scene");
    stage = document.querySelector("#braille-stage");
    buildPlate();
    bindControls();
    applyTilt();
  }

  window.WayfinderBraille = { init, render };
})();
