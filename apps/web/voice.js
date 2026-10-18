// Use by voice. Browser Web Speech API only: no server model, no LLM, no sponsor SDK.
// The grammar below mirrors src/wayfinder/access_input.py, which is what pytest checks.
// Spoken commands go through the same turn and open-page calls as the buttons, so the
// session still gates anything consequential. Voice cannot skip confirmation.

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
  const PASS = set("less detail", "more detail", "explain", "compare", "continue");
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

  return { interpret, plan, normalize };
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

    let voiceOn = false;
    let listening = false;
    let recognizer = null;
    let lastSpokenTurn = null;
    let lastSession = null;
    let orientationBlocked = false;
    let orientationText = "";
    let first = true;

    const HELP_TEXT =
      "You can say: what is here. Next. Previous. Confirm. Back. Stop. Read. " +
      "Search for something. Set room to something. Open harbor, faq, search, or form. " +
      "Or a number from one to five.";

    function say(message) {
      heard.textContent = message;
    }

    function speak(message) {
      if (!synth || !message) return;
      synth.cancel();
      const utterance = new SpeechSynthesisUtterance(message);
      utterance.rate = 1;
      utterance.onerror = (event) => {
        if (event.error === "not-allowed") {
          orientationBlocked = true;
          say("The browser blocked spoken output until you press Talk, press V, or press Use by voice. The text is on this page.");
        }
      };
      synth.speak(utterance);
    }

    function shutUp() {
      if (synth) synth.cancel();
    }

    function branchLabelList(state) {
      if (!state.branches.length) return "There are no options right now.";
      return (
        "Options. " +
        state.branches.map((branch, index) => `${index + 1}. ${branch.label}. ${branch.risk_label}.`).join(" ")
      );
    }

    function orientation(state) {
      return (
        `Wayfinder is ready. ${state.page.purpose} ${branchLabelList(state)} ` +
        "Press the Talk button or the V key, then say a command. Say help for the list."
      );
    }

    function readout(state) {
      return `${state.page.purpose} ${state.page.summary} ${branchLabelList(state)}`;
    }

    // The highlighted option is kept in the DOM so voice and vision share it.
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

    function setVoice(on) {
      voiceOn = on;
      toggle.setAttribute("aria-pressed", String(on));
      talk.setAttribute("aria-pressed", String(listening));
      if (!on) {
        shutUp();
        stopListening();
        say("Voice is off. The command box and buttons still work.");
      }
    }

    function beep() {
      try {
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) return;
        const context = new Context();
        const tone = context.createOscillator();
        tone.frequency.value = 880;
        tone.connect(context.destination);
        tone.start();
        tone.stop(context.currentTime + 0.12);
        tone.onended = () => context.close();
      } catch (_error) {
        // A missing tone is not an error. The live region still says Listening.
      }
    }

    function stopListening() {
      if (recognizer) {
        try {
          recognizer.abort();
        } catch (_error) {
          // Already stopped.
        }
      }
      listening = false;
      talk.setAttribute("aria-pressed", "false");
    }

    function startListening() {
      if (!Recognition) {
        say(
          "Speech recognition is not available in this browser. Try Chrome, Edge, or Safari, " +
            "or use the command box below. The typed command form still works.",
        );
        return;
      }
      if (listening) {
        stopListening();
        say("Stopped listening.");
        return;
      }
      if (!voiceOn) {
        voiceOn = true;
        toggle.setAttribute("aria-pressed", "true");
      }
      shutUp();
      recognizer = new Recognition();
      recognizer.lang = document.documentElement.lang || "en-US";
      recognizer.continuous = false;
      recognizer.interimResults = false;
      recognizer.maxAlternatives = 1;
      recognizer.onstart = () => {
        listening = true;
        talk.setAttribute("aria-pressed", "true");
        say("Listening. Speak one command.");
        beep();
      };
      recognizer.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        handle(transcript);
      };
      recognizer.onerror = (event) => {
        const reasons = {
          "not-allowed": "Microphone permission was denied. Allow the microphone for this page, or use the command box.",
          "service-not-allowed": "This browser will not run speech recognition here. Use the command box.",
          "no-speech": "I heard nothing. Press Talk and try again.",
          "audio-capture": "No microphone was found. Use the command box.",
          network: "Speech recognition needs the browser's online service and could not reach it. Use the command box.",
          aborted: "",
        };
        const message = event.error in reasons ? reasons[event.error] : `Speech recognition stopped: ${event.error}.`;
        if (message) {
          say(message);
          speak(message);
        }
      };
      recognizer.onend = () => {
        listening = false;
        talk.setAttribute("aria-pressed", "false");
      };
      try {
        recognizer.start();
      } catch (_error) {
        say("Could not start the microphone. Try again, or use the command box.");
      }
    }

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
      // Echo first, in the live region, then act.
      say(`Heard: “${raw}”.`);
      const state = app.state();
      if (!state) return;
      const command = WayfinderVoiceGrammar.interpret(raw);
      const pending = Boolean(state.pending_confirmation);
      const branchIds = state.branches.map((branch) => branch.id);
      const cursor = getCursor();

      switch (command.kind) {
        case "stop": {
          shutUp();
          // Silent unless a confirmation was waiting. Then say plainly that nothing was submitted.
          if (!pending) lastSpokenTurn = "silent-next";
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
        case "command":
          await send({ text: command.text });
          return;
        case "open":
          shutUp();
          await app.openTarget(command.target);
          return;
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
            setCursor(step.cursor);
            const description = describeCursor(state, step.cursor);
            say(`Heard: “${raw}”. ${description}`);
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
      setCursor(0);
      const newSession = state.session_id !== lastSession;
      lastSession = state.session_id;
      const silent = lastSpokenTurn === "silent-next";
      if (first) {
        first = false;
        lastSpokenTurn = state.turn_id;
        orientationText = orientation(state);
        speak(orientationText);
        return;
      }
      if (!voiceOn) {
        lastSpokenTurn = state.turn_id;
        return;
      }
      if (state.turn_id === lastSpokenTurn && !newSession) return;
      lastSpokenTurn = state.turn_id;
      if (silent) return;
      if (newSession) {
        orientationText = orientation(state);
        speak(orientationText);
      } else {
        speak(state.speech);
      }
    });

    toggle.addEventListener("click", () => {
      if (voiceOn) {
        setVoice(false);
        return;
      }
      setVoice(true);
      const state = app.state();
      if (state) {
        lastSpokenTurn = state.turn_id;
        say("Voice is on. Press Talk, or press V, then speak.");
        speak(orientationBlocked || !synth ? orientation(state) : "Voice is on. Press Talk, or press V, then speak.");
        orientationBlocked = false;
      }
    });

    talk.addEventListener("click", () => {
      if (orientationBlocked && app.state()) speak(orientation(app.state()));
      orientationBlocked = false;
      startListening();
    });

    window.addEventListener("keydown", (event) => {
      if (event.metaKey || event.ctrlKey || event.altKey || event.repeat) return;
      const tag = event.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const key = event.key.toLowerCase();
      if (key === "v" || (event.key === " " && (tag === "BODY" || tag === "HTML"))) {
        event.preventDefault();
        startListening();
      }
    });

    if (!Recognition) {
      say("Speech recognition is not available in this browser, so Talk cannot listen. The typed command form still works.");
    }
    if (!synth) {
      say("Spoken output is not available in this browser. Replies appear on the page.");
    }
  })();
}
