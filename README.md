# SLCVoiceAI

Talk to [Self-Loading Cargo](https://www.selfloadingcargo.com/) the way you would talk to a real crew — in your own words, in your own language — instead of reciting fixed English phrases at a speech engine that was never trained on your accent.

SLCVoiceAI is an external bridge. **It does not modify Self-Loading Cargo in any way.**

---

## Why this exists

SLC's built-in voice control uses the classic Windows SAPI desktop recognizer with a fixed grammar. That design has three consequences:

1. **You must say the exact phrase.** "Cockpit to Ground?" works; "ask the ground crew if they're there" does not.
2. **Only a handful of languages are supported**, all of them recognized by an engine tuned for native speakers. Non-native accents get rejected at the confidence threshold.
3. **On a non-English Windows install, the classic Windows Speech Recognition app refuses to start at all** — it requires the recognizer language to match the Windows display language. You can still install the engine and SLC will use it, but you lose the microphone wizard and voice training that would have compensated for your accent.

This bridge sidesteps all three by taking the speech recognition out of SLC's hands entirely.

## How it works

```
your microphone
      │   hold the bridge's own PTT key
      ▼
faster-whisper (local, on your GPU)
      │   free-form transcript, any language
      ▼
UI Automation: read SLC's live button list
      │   ["Request Pushback", "Ready For Departure", ...]
      ▼
Claude: which button did the pilot mean?
      │   with a confidence score, and free to decline
      ▼
UI Automation: press that button
```

SLC is a WPF application, so every button it draws is exposed in the Windows UI Automation tree with a name, an enabled flag and an Invoke pattern — the same mechanism a screen reader uses. That gives the bridge both halves of the problem: it can see exactly which commands SLC is offering at this instant, and it can trigger the one you meant.

The available-button list is read fresh on every utterance and filtered to what is actually on screen, so the bridge tracks SLC's context sensitivity rather than working around it. It can only ever press something SLC is already offering. (That filtering is not free — see below.)

### What this gets you

- **Speak Polish, German, Spanish — whatever.** Whisper transcribes it, Claude translates the intent, SLC gets an English button press.
- **Speak loosely.** "tell them we're good to push", "let's get moving", "możemy pchać" all land on the pushback request.
- **No grammar, no confidence threshold, no voice training.**
- **Survives SLC updates.** Matching is by button name, not by binary offsets.

---

## Status: confirmed working

The UI Automation approach is verified against a running SLC v1.6.7.3. Its
entire control surface reads cleanly — 336 controls, with real names, not just
opaque ids:

```
GROUND CREW >            READY FOR PUSHBACK       START BOARDING
RELEASE THE CABIN CREW   TAKE SEATS FOR LANDING   TURN THE MUSIC UP
```

Those are exactly SLC's voice commands, which is what makes routing to them
tractable.

### The catch, and how it is handled

SLC keeps its **whole** command tree alive in the WPF visual tree at all times.
Every one of those 336 buttons reports `IsEnabled=True` and `IsOffscreen=True`
whether or not you can currently use it, so neither property tells you what is
actually on offer. Handing all of them to the model would let it announce the
descent while you are still parked at the gate.

The property that does discriminate is the **bounding rectangle**: live controls
have a real one, collapsed controls are `0x0`. `is_visible()` in
`slcvoiceai/slc_ui.py` filters on exactly that, which is what restores SLC's
context sensitivity — 335 controls in the tree, 33 on screen during flight setup,
22 back at the launcher, tracking live as the UI changes.

Two SLC quirks worth knowing if you extend this:

- **Toolbar buttons are icon-only** and carry no accessible name at all, just an
  `AutomationId` like `cmdToggleDoorMode`. `humanise_id()` turns those back into
  `Toggle Door Mode`.
- **`IsOffscreen` is useless here** — it is `True` for everything, visible or not.

Check it yourself at any time:

```bash
python tools/probe_slc.py --watch
python -m slcvoiceai --list-actions
```

The probe also takes `--process` if you want to sanity-check your setup against
another app before SLC is even running:

```bash
python tools/probe_slc.py --process chrome.exe --depth 6
```

---

## Requirements

- Windows 10/11
- Python 3.11+ (3.12 recommended — the config loader uses `tomllib`)
- Self-Loading Cargo v1.6+
- A CUDA GPU is strongly recommended for Whisper. CPU works but adds seconds to every command.
- **No API key needed** on the default offline backend — see below.

## Install

```bash
git clone https://github.com/piiiciek/SLCVoiceAI.git
cd SLCVoiceAI
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy the example config and edit it:

```bash
copy config.example.toml config.toml
```

The two settings worth checking first:

- **`ptt_key`** — must be a *different* key from SLC's own PTT (SLC defaults to `LeftCtrl`), or the two will fight over the same press. `f13` is a good choice if your keyboard or Stream Deck can send it.
- **`input_device`** — leave empty to use the Windows default, but verify that the default is the microphone you actually speak into. Virtual devices such as *Steam Streaming Microphone* frequently steal the default slot and record silence.

```bash
python -m slcvoiceai --list-devices
```

## Two ways to match intent

`[intent] backend` in `config.toml` picks how a spoken phrase becomes a button press.

**`fuzzy`** (default) — offline string matching. Free, instant, no API key, and
no VRAM beyond Whisper. That last point matters more than it sounds: you are
running MSFS at the same time, and a local 7–14B model would want ~8 GB of the
same card. Whisper's `translate` task turns your Polish into English first, so
the matching happens against SLC's own English button names.

It handles anything close to the button's wording — *"połącz mnie z obsługą
naziemną"* → `GROUND CREW >`, *"pasy bezpieczeństwa"* → `Seatbelts`. It will not
handle genuinely indirect phrasing like *"tell them we're good to push"*.

**`claude`** — the Anthropic API, which does handle indirect phrasing, and can
use flight context to disambiguate. Costs roughly a third of a grosz per
command (about 0.20 zł per flight); needs credit on
[console.anthropic.com](https://console.anthropic.com/) and
`setx ANTHROPIC_API_KEY "sk-ant-..."`. Drop `min_confidence` to ~0.55 when you
switch — that model reports calibrated confidence and declines on its own.

Start on `fuzzy`. It costs nothing to find out whether it is good enough for how
you actually speak.

## Use

### The control panel

```bash
python -m slcvoiceai --gui
```

A small always-on-top window that sits beside the simulator and answers,
without opening a log file: is it listening and on which device, what did it
hear, what did it match, did it press anything, and if it refused — why.

It shows every candidate's score, not just the winner, because the margin over
the runner-up explains a refusal better than the winning number does. The
confidence floor is a live slider and dry-run is a checkbox, so tuning needs no
restart.

The **"try a phrase"** box runs the whole matching chain on typed text — no
microphone, no flight needed. Useful for working out why a command missed, and
for testing on the ground.

### Headless

Always start in dry-run, which decides and logs but never presses anything:

```bash
python -m slcvoiceai --dry-run
```

Hold your PTT key, say something, and watch the log. When you are happy with what it picks, drop the flag:

```bash
python -m slcvoiceai
```

To see what SLC is offering at any moment without saying anything:

```bash
python -m slcvoiceai --list-actions
```

---

## Safety

Two deliberate guardrails, because a misfire mid-approach is worse than being asked to repeat yourself:

- **Visibility filter.** The model is only ever shown controls SLC is currently displaying, so it cannot reach a command that is out of context for the phase of flight.
- **Denylist.** Session-ending and flight-destroying controls — `EXIT SELF-LOADING CARGO`, `CLOSE SELF-LOADING CARGO`, `CANCEL SINGLE FLIGHT`, `DISPATCH NEXT FLIGHT`, `DO NOT RESTORE PREVIOUS FLIGHT` and friends — are stripped before the model ever sees them. It cannot press what it cannot see. The list lives in `slcvoiceai/slc_ui.py`.
- **Confidence floor.** Below `min_confidence` (default `0.80`) the bridge does nothing and logs why. On the fuzzy backend this is the guardrail that matters most: string matching always finds a *nearest* neighbour, so without a high floor, radio chatter lands on a cabin command — *"tower london zero two"* scored 0.60 against `PHONE >`. The default was picked by sweeping thresholds against real SLC buttons; 0.75–0.85 separates commands from chatter cleanly. `tests/test_intent.py` locks that in.
- **Ambiguity check.** If the two best candidates score within 0.05 of each other, the bridge declines rather than picking one.

## Privacy

On the default `fuzzy` backend **nothing leaves your machine at all** — Whisper runs locally and the matching is plain string comparison.

On the `claude` backend, audio still never leaves your machine; what is sent is the resulting transcript, the list of button names SLC is currently showing, and whatever flight context you enabled in `[slc] stream_export_dir`.

---

## Project layout

```
slcvoiceai/
  __main__.py   CLI entry point
  app.py        orchestration
  audio.py      push-to-talk capture
  stt.py        faster-whisper
  slc_ui.py     UI Automation: read and press SLC's controls
  intent.py     Claude: utterance + button list -> decision
  context.py    optional flight context from SLC's stream export
  config.py     config.toml loading
  gui.py        tkinter control panel (--gui)
tools/
  probe_slc.py  standalone UIA diagnostic
tests/
  test_intent.py  routing regressions, runs without SLC
```

```bash
python -m pytest tests/ -q
```

## Troubleshooting

**"SLC is offering no buttons right now"** — either SLC has no communications popup open, or its buttons are not UIA-visible. Run the probe.

**Whisper falls back to CPU** — on Windows this is usually the CUDA runtime, not the driver. `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` are in `requirements.txt` for exactly this; without them the model loads on the GPU and then every transcription dies with `Library cublas64_12.dll is not found`.

**Transcription is slow** — check `beam_size` is 1. Measured on real speech with MSFS running, `beam_size = 5` took 6.00s against 1.17s for byte-identical output. If it is still slow, the simulator is competing for the GPU: `medium` (1.24s), `small` (0.68s) and `base` (0.40s) all transcribed the benchmark phrase correctly, though the smaller ones are noticeably weaker outside English.

**Nothing is transcribed** — wrong input device. `--list-devices`, then set `input_device` explicitly.

**It presses the wrong thing** — raise `min_confidence`, and enable `stream_export_dir` so the model knows what phase of flight you are in.

---

## Disclaimer

Not affiliated with, endorsed by, or supported by Self-Loading Cargo or Lanilogic. SLC is commercial software you must own separately. This project only automates its user interface from the outside; it does not modify, patch, decompile or redistribute any part of it.

## License

MIT — see [LICENSE](LICENSE).
