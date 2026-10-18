# Wayfinder pitch notes

## North star

Same Internet. Same task. Different interface.

Wayfinder turns any web page into one semantic state (what is here, what matters, what you can do and what each choice costs, warnings, the path so far, and your goal). Every output reads that one state. A person uses whichever channel suits them, switches channels mid-task without losing the task, and is asked before anything consequential happens.

## Answers for the judges

**Why not ChatGPT plus Browser Use?** That pairing is one model with the keys to the page. Here the page is read by a read-only interpreter, a policy gate answers allow, confirm, or deny without running anything, and only a navigator that can run a grounded action may act, after the page offered that exact action. A verifier re-reads the page afterward and says success, partial, failure, or ambiguous. No model is required, and nothing in this build calls one. Text on a page is shown, never obeyed.

**Why not a screen reader?** A screen reader reads the page you have. Wayfinder decides what matters on it, offers about three grounded choices with their consequences, keeps your goal and path across pages, and renders the same state as speech, cards, Braille, a tactile cue, or a quiet glance. It also does not replace your screen reader. It can sit beside it.

**Why not a browser agent?** An agent takes over and acts. Wayfinder narrates, branches, and acts only on a choice you made. "Just do it for me" takes the safe steps toward your goal and still stops for your own yes before a submit. Stop, wait, and go back are always available and keep your place.

**Why a registry?** The router needs a visible answer to "who is acting, and how far can it be trusted?" Each capability (Reader, Browser Navigator, Goal Planner, Memory, Commerce Agent, Jobs Agent) declares its permissions and trust. The screen says which was selected, for example "Browser Navigator selected". Adding a specialist is one entry and one planner, behind the same router and gate. It is a local list. It is not an app store and not a sponsor SDK.

**Why not only fix websites?** Most pages will not be fixed, and the ones that are fixed still read the same way to everyone. Wayfinder works from the page as it is, marks any name it had to infer, and never edits the live site. Better sites make it better. It does not wait for them.

## The multimodal shell is these pieces together

The shell is not a separate operating system and nothing here boots one. It is the semantic state, the capability registry and its trust boundaries, the channel layer (voice, camera, keyboard, switches in; speech, cards, Braille, tactile, glance out), the three local memories, and the policy gate and verifier, used together in one web interface on the Mac.

## What is real and what is scaffolding

- Real now: Playwright reading arbitrary http(s) pages, the Harbor Line offline fixture, local shop and jobs mirrors (no payment, nothing sent), Grade 1 Braille projection, browser speech recognition and speech output, laptop webcam motion input, software tactile cue and button stand-ins.
- Scaffolding: the motor has not spun, no firmware has been flashed, and the P4X-EYE is not live. The laptop webcam is the vision path.
- No account, no diagnosis, no stored audio or video. Memory is the session path, a named project, and preferences, all on this Mac.
