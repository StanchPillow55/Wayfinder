// Use by voice. Browser Web Speech API only: no server model, no LLM, no sponsor SDK.
// The grammar below mirrors src/wayfinder/access_input.py, which is what pytest checks.
// Spoken commands go through the same turn and open-page calls as the buttons, so the
// session still gates anything consequential. Voice cannot skip confirmation.
//
// Ambient-ready, not surveillance: browsers block the mic until a gesture. The first press
// (the button, or the V key) arms it, and then the channel stays open with a visible MIC
// indicator and a mute. Raw audio is never stored or uploaded by this app. The browser's
// own recognizer may send audio to its vendor (Chrome does); the page says so.

const WayfinderVoiceGrammar = (() => {
  const TARGET_WORDS = {
    harbor: "fixture",
    "harbor line": "fixture",
    fixture: "fixture",
    faq: "faq",
    "library help": "faq",
    library: "faq",
    search: "search",
    "city services": "search",
    form: "form",
    "room form": "form",
    "room request": "form",
    shop: "shop",
    store: "shop",
    shopping: "shop",
    commerce: "shop",
    jobs: "jobs",
    careers: "jobs",
    "job board": "jobs",
    unlabeled: "unlabeled",
    "help desk": "unlabeled",
    repair: "unlabeled",
  };
  const NUMBERS = { one: 1, two: 2, three: 3, four: 4, five: 5, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5 };
  const INJECTION = [
    "ignore previous",
    "ignore all previous",
    "ignore the above",
    "ignore your instructions",
    "disregard",
    "system prompt",
    "you are now",
    "new instructions",
  ];
  const set = (...items) => new Set(items);
  const STOP = set("stop", "stop talking", "be quiet", "quiet", "silence", "hush", "cancel", "never mind", "nevermind");
  const HELP = set("help", "what can i say", "commands", "list commands");
  const DESCRIBE = set("what is here", "whats here", "where am i", "what can i do", "what can i do here");
  const NEXT = set("next", "next option", "next one");
  const PREVIOUS = set("previous", "previous option", "previous one");
  const BACK = set("back", "go back");
  const CONFIRM = set("confirm", "select", "select this", "choose this", "choose it", "this one", "open this");
  const YES = set("yes", "do it", "go ahead", "confirmed");
  const NO = set("no", "nope", "deny", "dont", "do not");
  const READ = set("read", "read it", "read this", "read page", "read aloud", "read again", "repeat");
  const PASS = set(
    "less detail", "more detail", "explain", "compare", "continue",
    "wait", "hold on", "pause", "what did you just say", "say that again", "explain that",
    "read everything", "forget that constraint", "just do it for me", "just do it",
    "where was i", "who is acting", "mute", "unmute", "mute audio", "unmute audio",
    "reset my preferences", "fewer options", "more options",
    "switch to visual", "switch to audio", "switch to braille", "switch to tactile", "switch to hands free",
  );
  const GOAL = [
    /^(?:actually|instead|new priority)\b.+$/,
    /^(?:my goal is|goal|i want to|i need to|i want|i need|i would like to|id like to|help me)\b.+$/,
    /^(?:request|reserve|book|apply for|buy|order|purchase|add|take me|review)\b.+$/,
  ];
  const PROJECT = [
    /^(?:save|name)(?: this| my| the)?(?: project| workspace| search| task)? (?:as|called|named) [a-z0-9 ]{2,60}$/,
    /^(?:continue|resume|reopen|pick up)(?: with)?(?: my| the)?(?: project| workspace)? [a-z0-9 ]{2,60}$/,
  ];
  const FORGET = /^forget (?:the |my )?[a-z$0-9 -]{2,40} constraint$/;
  const OPEN_ADDRESS = set(
    "open address", "open the address", "open the typed address", "open the address i typed", "go to the address", "go to the typed address",
  );
  const URL_OPEN = /^(?:open|go to|visit|load)(?: the)?(?: website| site| address| url| page)? ([a-z0-9][a-z0-9 .\-/]*)$/;
  const URL_SHAPE = /^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:\/[a-z0-9._~%/-]*)?$/;
  const ENTER = [
    /^(?:search|find|look up)(?: for)? .+$/,
    /^(?:set|fill in|fill|enter|type|put) .+ (?:to|with|as) .+$/,
  ];
  const OPEN = /^(?:open|go to|show|load)(?: the)? ([a-z ]+?)(?: page)?$/;
  const NUMBER = /^(?:(?:number|option) )?([1-5]|one|two|three|four|five)$/;

  function normalize(raw) {
    return String(raw)
      .trim()
      .toLowerCase()
      .replace(/\u2019/g, "'")
      .replace(/'/g, "")
      .replace(/[?!.,;:]+/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  // "open example dot org slash docs" or "open example.org" -> https://example.org/docs.
  // Only a host-shaped word list with a dot becomes an address.
  function spokenUrl(raw) {
    const match = URL_OPEN.exec(String(raw).trim().toLowerCase());
    if (!match) return null;
    let text = match[1].trim();
    text = text.replace(/\s+dot\s+/g, ".").replace(/\s+slash\s+/g, "/");
    text = text.replace(/\s*\.\s*/g, ".").replace(/ /g, "");
    return URL_SHAPE.test(text) ? `https://${text}` : null;
  }

  function interpret(raw) {
    const n = normalize(raw);
    if (!n) return { kind: "unknown" };
    if (INJECTION.some((marker) => n.includes(marker))) return { kind: "rejected", text: n.slice(0, 80) };
    if (STOP.has(n) || n.startsWith("stop ")) return { kind: "stop", text: "stop" };
    if (HELP.has(n)) return { kind: "help" };
    if (DESCRIBE.has(n) || n.startsWith("what is here")) return { kind: "describe", text: "what is here" };
    if (NEXT.has(n)) return { kind: "next" };
    if (PREVIOUS.has(n)) return { kind: "previous" };
    if (BACK.has(n)) return { kind: "back", text: "go back" };
    if (CONFIRM.has(n)) return { kind: "confirm", text: n };
    if (YES.has(n)) return { kind: "yes", text: n };
    if (NO.has(n)) return { kind: "no", text: "no" };
    if (READ.has(n)) return { kind: "read" };
    if (PASS.has(n)) return { kind: "command", text: n };
    const opened = OPEN.exec(n);
    if (opened) {
      const target = TARGET_WORDS[opened[1].trim()];
      if (target) return { kind: "open", target };
    }
    if (OPEN_ADDRESS.has(n)) return { kind: "open_address" };
    const address = spokenUrl(raw);
    if (address) return { kind: "open_url", url: address };
    if (PROJECT.some((pattern) => pattern.test(n)) || FORGET.test(n)) return { kind: "command", text: n.slice(0, 120) };
    if (GOAL.some((pattern) => pattern.test(n))) return { kind: "goal", text: n.slice(0, 200) };
    if (ENTER.some((pattern) => pattern.test(n))) return { kind: "enter", text: n.slice(0, 200) };
    const number = NUMBER.exec(n);
    if (number) return { kind: "number", number: NUMBERS[number[1]] };
    return { kind: "unknown", text: n.slice(0, 80) };
  }

  // Needs local state (cursor, pending, branch ids). Mirrors plan_voice in Python.
  function plan(command, { cursor, branchIds, pending }) {
    const count = branchIds.length;
    const waiting = { kind: "speak", cursor, note: "A confirmation is waiting. Say confirm, or say no." };
    if (command.kind === "next" || command.kind === "previous") {
      if (pending) return waiting;
      if (!count) return { kind: "none", note: "No options to move through." };
      const step = command.kind === "next" ? 1 : -1;
      return { kind: "move", cursor: (((cursor + step) % count) + count) % count };
    }
    if (command.kind === "confirm" || command.kind === "yes") {
      if (pending) return { kind: "confirm", cursor, post: "yes" };
      if (command.kind === "yes") {
        return {
          kind: "speak",
          cursor,
          note: "Nothing is waiting for confirmation. Say confirm to choose the current option.",
        };
      }
      if (!count) return { kind: "none", note: "No option is selected." };
      const index = cursor % count;
      return { kind: "select", cursor: index, actionId: branchIds[index] };
    }
    if (command.kind === "number") {
      if (pending) return waiting;
      const number = command.number || 0;
      if (number < 1 || number > count) {
        return { kind: "speak", cursor, note: `There is no option ${number}. Say a number from 1 to ${count}.` };
      }
      return { kind: "select", cursor: number - 1, actionId: branchIds[number - 1] };
    }
    return { kind: "none" };
  }

  return { interpret, plan, normalize, spokenUrl };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = WayfinderVoiceGrammar;
}

if (typeof document !== "undefined" && window.WayfinderApp) {
  (() => {
    const app = window.WayfinderApp;
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const synth = "speechSynthesis" in window ? window.speechSynthesis : null;

    const toggle = document.querySelector("#voice-toggle");
    const talk = document.querySelector("#voice-talk");
    const heard = document.querySelector("#voice-heard");
    const speechToggle = document.querySelector("#speech-toggle");
    const textForm = document.querySelector("#voice-text-form");
    const textInput = document.querySelector("#voice-text");

    // micIntent: the person armed the mic. micMuted: they muted it. Recognition runs only
    // while armed and not muted, and restarts itself because browsers end it after silence.
    let micIntent = false;
    let micMuted = false;
    let micBlocked = false;
    let recognizer = null;
    let restartTimer = null;
    let failures = 0;
    let speaking = false;
    let speakingUntil = 0;
    let lastSpokenTurn = null;
    let lastSession = null;
    let orientationBlocked = false;
    let first = true;

    const HELP_TEXT =
      "You can say: what is here. Next. Previous. Confirm. Back. Stop. Wait. What did you just say. Explain that. " +
      "Less detail. Read everything. Where was I. Forget that constraint. Just do it for me. " +
      "Find the hours. Request the north room. Open shop, jobs, faq, search, or form. " +
      "Open example dot org. Switch to braille. Mute. Or a number from one to five.";
    const HARD_STOP = new Set(["stop", "quiet", "be quiet", "silence", "hush", "mute", "mute audio", "wait"]);

    function say(message) {
      heard.textContent = message;
    }

    function muted() {
      const state = app.state();
      return Boolean(state && state.speech_muted);
    }

    function micStatus() {
      if (!Recognition) return "unsupported";
      if (micBlocked) return "blocked";
      if (!micIntent) return "available";
      return micMuted ? "muted" : "armed";
    }

    function reportMic() {
      const status = micStatus();
      const chip = document.querySelector("#chip-mic");
      if (chip) {
        const words = {
          available: "MIC: off. Press V to arm",
          armed: "MIC: live, listening",
          muted: "MIC: muted",
          blocked: "MIC: blocked by the browser",
          unsupported: "MIC: not supported here",
        };
        chip.textContent = words[status];
        chip.dataset.status = status;
      }
      toggle.setAttribute("aria-pressed", String(status === "armed"));
      toggle.textContent = status === "armed" ? "Mute microphone" : status === "muted" ? "Unmute microphone" : "Arm microphone";
      talk.setAttribute("aria-pressed", String(status === "armed"));
      app.reportChannel({ mic: status });
    }

    function shutUp() {
      speaking = false;
      if (synth) synth.cancel();
    }

    function speak(message) {
      if (!synth || !message || muted()) return;
      synth.cancel();
      const utterance = new SpeechSynthesisUtterance(message);
      utterance.rate = 1;
      utterance.onstart = () => {
        speaking = true;
      };
      utterance.onend = () => {
        speaking = false;
        speakingUntil = performance.now() + 500;
      };
      utterance.onerror = (event) => {
        speaking = false;
        if (event.error === "not-allowed") {
          orientationBlocked = true;
          say("The browser blocked spoken output until you press a key or a button. The text is on this page.");
        }
      };
      synth.speak(utterance);
    }

    function branchLabelList(state) {
      if (!state.branches.length) return "There are no options right now.";
      return (
        "Options. " +
        state.branches.map((branch, index) => `${index + 1}. ${branch.label}. ${branch.risk_label}.`).join(" ")
      );
    }

    function orientation(state) {
      return `Wayfinder is ready. ${state.page.purpose} ${branchLabelList(state)} Say help for the list.`;
    }

    function readout(state) {
      return `${state.page.purpose} ${state.page.summary} ${branchLabelList(state)}`;
    }

    // The highlighted option is kept in the DOM so voice, camera, and switches share it.
    function branchButtons() {
      return Array.from(document.querySelectorAll("#branches .branch"));
    }

    function getCursor() {
      const index = branchButtons().findIndex((button) => button.getAttribute("aria-current") === "true");
      return index < 0 ? 0 : index;
    }

    function setCursor(index) {
      branchButtons().forEach((button, position) => {
        const on = position === index;
        button.classList.toggle("access-cursor", on);
        if (on) button.setAttribute("aria-current", "true");
        else button.removeAttribute("aria-current");
      });
    }

    function describeCursor(state, index) {
      const branch = state.branches[index];
      if (!branch) return "No option.";
      return `Option ${index + 1} of ${state.branches.length}: ${branch.label}. ${branch.risk_label}.`;
    }

    // ---- microphone: armed once, then kept open --------------------------------------

    function stopRecognizer() {
      clearTimeout(restartTimer);
      if (recognizer) {
        recognizer.onend = null;
        try {
          recognizer.abort();
        } catch (_error) {
          // Already stopped.
        }
      }
      recognizer = null;
    }

    function startRecognizer() {
      if (!Recognition || !micIntent || micMuted || micBlocked) return;
      stopRecognizer();
      recognizer = new Recognition();
      recognizer.lang = document.documentElement.lang || "en-US";
      recognizer.continuous = true;
      recognizer.interimResults = false;
      recognizer.maxAlternatives = 1;
      recognizer.onstart = () => {
        failures = 0;
        say("Microphone is live. Say a command. Press V or the Mute button to close it.");
      };
      recognizer.onresult = (event) => {
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          if (!event.results[i].isFinal) continue;
          const transcript = event.results[i][0].transcript;
          // Our own voice comes back through the mic. While speaking, only a hard stop counts.
          const talking = speaking || performance.now() < speakingUntil;
          if (talking && !HARD_STOP.has(WayfinderVoiceGrammar.normalize(transcript))) continue;
          handle(transcript);
        }
      };
      recognizer.onerror = (event) => {
        const reasons = {
          "not-allowed": "Microphone permission was denied. Allow the microphone for this page, or use the command box.",
          "service-not-allowed": "This browser will not run speech recognition here. Use the command box.",
          "audio-capture": "No microphone was found. Use the command box.",
          network: "Speech recognition needs the browser's online service and could not reach it. Use the command box.",
          "no-speech": "",
          aborted: "",
        };
        if (event.error === "not-allowed" || event.error === "service-not-allowed" || event.error === "audio-capture") {
          micBlocked = true;
          reportMic();
        }
        const message = event.error in reasons ? reasons[event.error] : `Speech recognition stopped: ${event.error}.`;
        if (message) {
          say(message);
          speak(message);
        }
      };
      recognizer.onend = () => {
        recognizer = null;
        if (micIntent && !micMuted && !micBlocked) {
          failures += 1;
          restartTimer = setTimeout(startRecognizer, Math.min(5000, 250 * failures));
        }
      };
      try {
        recognizer.start();
      } catch (_error) {
        say("Could not start the microphone. Try again, or use the command box.");
      }
    }

    // One control: arm on first use, then mute or unmute. Never needs a settings step.
    function toggleMic() {
      if (!Recognition) {
        say("Speech recognition is not available in this browser. The typed command box and the voice text box below still work.");
        reportMic();
        return;
      }
      if (!micIntent) {
        micIntent = true;
        micMuted = false;
        micBlocked = false;
        shutUp();
        startRecognizer();
      } else if (micMuted) {
        micMuted = false;
        micBlocked = false;
        startRecognizer();
        say("Microphone unmuted.");
      } else {
        micMuted = true;
        stopRecognizer();
        say("Microphone muted. Nothing is being listened to. Press V to unmute.");
      }
      reportMic();
    }

    async function setSpeechMuted(next) {
      if (next) shutUp();
      await app.setPreferences({ speech_muted: next });
      speechToggle.setAttribute("aria-pressed", String(next));
      speechToggle.textContent = next ? "Unmute spoken audio" : "Mute spoken audio";
    }

    // ---- commands ---------------------------------------------------------------------

    async function send(body) {
      try {
        await app.turn({ ...body, channel: "speech" });
      } catch (_error) {
        const message = "That did not reach the session. Nothing changed.";
        say(message);
        speak(message);
      }
    }

    async function handle(raw) {
      // A new utterance cancels speech in progress right away. Steps already taken stay.
      shutUp();
      say(`Heard: “${raw}”.`);
      const state = app.state();
      if (!state) return;
      const command = WayfinderVoiceGrammar.interpret(raw);
      const pending = Boolean(state.pending_confirmation);
      const branchIds = state.branches.map((branch) => branch.id);
      const cursor = getCursor();

      switch (command.kind) {
        case "stop": {
          await send({ text: "stop" });
          return;
        }
        case "help":
          say(`Heard: “${raw}”. ${HELP_TEXT}`);
          speak(HELP_TEXT);
          return;
        case "describe":
        case "back":
        case "no":
        case "enter":
        case "goal":
        case "command":
          await send({ text: command.text });
          return;
        case "open":
          await app.openTarget(command.target);
          return;
        case "open_url":
          await app.openTarget("url", command.url);
          return;
        case "open_address": {
          const address = document.querySelector("#url-input").value.trim();
          if (/^https?:\/\/\S+$/i.test(address)) await app.openTarget("url", address);
          else {
            say("There is no web address in the address box yet. Type one, or say: open example dot org.");
            speak("There is no web address in the address box yet.");
          }
          return;
        }
        case "read":
          speak(readout(state));
          return;
        case "next":
        case "previous":
        case "confirm":
        case "yes":
        case "number": {
          const step = WayfinderVoiceGrammar.plan(command, { cursor, branchIds, pending });
          if (step.kind === "move") {
            const description = describeCursor(state, step.cursor);
            say(`Heard: “${raw}”. ${description}`);
            await app.moveCursor(step.cursor, "voice");
            speak(description);
          } else if (step.kind === "select") {
            setCursor(step.cursor);
            await send({ action_id: step.actionId });
          } else if (step.kind === "confirm") {
            await send({ text: step.post });
          } else if (step.note) {
            say(`Heard: “${raw}”. ${step.note}`);
            speak(step.note);
          }
          return;
        }
        case "rejected": {
          const message = "That sounded like an instruction to me, not a command, so I did not act on it.";
          say(`Heard: “${raw}”. ${message}`);
          speak(message);
          return;
        }
        default: {
          const message = "I did not catch a command. Say help for the list.";
          say(`Heard: “${raw}”. ${message}`);
          speak(message);
        }
      }
    }

    document.addEventListener("wayfinder:render", (event) => {
      const state = event.detail;
      const cursor = state.branch_cursor ? state.branch_cursor.index : 0;
      setCursor(cursor);
      speechToggle.setAttribute("aria-pressed", String(Boolean(state.speech_muted)));
      speechToggle.textContent = state.speech_muted ? "Unmute spoken audio" : "Mute spoken audio";
      if (state.speech_muted) shutUp();
      const newSession = state.session_id !== lastSession;
      lastSession = state.session_id;
      if (first) {
        first = false;
        lastSpokenTurn = state.turn_id;
        speak(orientation(state));
        reportMic();
        return;
      }
      if (state.turn_id === lastSpokenTurn && !newSession) return;
      lastSpokenTurn = state.turn_id;
      // Say the new line. Whatever was still being said was already cut off by the new turn.
      if (newSession) speak(orientation(state));
      else speak(state.speech);
    });

    toggle.addEventListener("click", toggleMic);
    talk.addEventListener("click", () => {
      if (orientationBlocked && app.state()) speak(orientation(app.state()));
      orientationBlocked = false;
      if (!micIntent || micMuted) toggleMic();
      else say("The microphone is already live. Just speak.");
    });
    speechToggle.addEventListener("click", () => setSpeechMuted(!muted()));

    textForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const value = textInput.value.trim();
      if (!value) return;
      textInput.value = "";
      handle(value);
    });

    window.addEventListener("keydown", (event) => {
      if (event.metaKey || event.ctrlKey || event.altKey || event.repeat) return;
      const tag = event.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const key = event.key.toLowerCase();
      if (key === "v" || (event.key === " " && (tag === "BODY" || tag === "HTML"))) {
        event.preventDefault();
        toggleMic();
      } else if (key === "m") {
        event.preventDefault();
        setSpeechMuted(!muted());
      }
    });

    window.addEventListener("pagehide", stopRecognizer);

    window.WayfinderVoice = { handle, grammar: WayfinderVoiceGrammar, micStatus };

    if (!Recognition) {
      say("Speech recognition is not available in this browser, so the mic cannot listen. The typed command form and the voice text box still work.");
    }
    if (!synth) {
      say("Spoken output is not available in this browser. Replies appear on the page.");
    }
    reportMic();
  })();
}
