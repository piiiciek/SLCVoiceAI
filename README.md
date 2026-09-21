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
press and hold the PTT key
      │
      ├──────────────────────────►  UI Automation: read SLC's live
      │                             button list, while you are still
 you speak                          speaking
      │                                        │
release the key                                │
      │                                        │
      ▼                                        │
faster-whisper (local, on your GPU)            │
      │   free-form transcript, any language   │
      └────────────────────┬───────────────────┘
                           ▼
        which button did the pilot mean?
        local matcher first, cloud only if unsure
                           │   a confidence score, and free to decline
                           ▼
        UI Automation: press that button
```

SLC is a WPF application, so every button it draws is exposed in the Windows UI Automation tree with a name, an enabled flag and an Invoke pattern — the same mechanism a screen reader uses. That gives the bridge both halves of the problem: it can see exactly which commands SLC is offering at this instant, and it can trigger the one you meant.

The available-button list is read fresh on every utterance and filtered to what is actually on screen, so the bridge tracks SLC's context sensitivity rather than working around it. It can only ever press something SLC is already offering. (That filtering is not free — see below.)

Note the fork at the top. Reading SLC takes about as long as a short sentence, and the button list does not depend on what you are about to say — so it is read *while* you say it, starting the moment the key goes down. By the time you let go it is usually already done.

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

Three SLC quirks worth knowing if you extend this:

- **Toolbar buttons are icon-only** and carry no accessible name at all, just an
  `AutomationId` like `cmdToggleDoorMode`. `humanise_id()` turns those back into
  `Toggle Door Mode`.
- **A name we invented can be a false friend.** `cmdStandBy` humanises to a
  perfectly sensible-looking `Stand By` — and its tooltip reads *"Close SLC or
  Restart Flight"*. A pilot answering a radio check with "5 by 5" pressed it.
  Where the label is our guess rather than SLC's own name, the tooltip is the
  control's real description, so the denylist is checked against both.
- **`IsOffscreen` is useless here** — it is `True` for everything, visible or not.
- **A rectangle means "laid out", not "on top".** SLC stacks controls: the three
  menus each have a `BACK` button, two of them at identical coordinates, and one
  sits under a conversation's `GO AHEAD`. All report a real rectangle. Where two
  controls share a name, `is_topmost()` asks Windows what is at the point and
  keeps that one — the rectangle alone would pick by luck.

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

### What that reading costs, and why you do not wait for it

Reading SLC's window is the slowest thing the bridge does — around 1.4s, against
roughly 1s to transcribe a short sentence. It used to sit squarely in the wait:
you let go of the key, and only then did the bridge start asking SLC what was on
offer. Two changes took it out of the wait entirely.

**The scan starts with the key, not with the clip.** It runs during the
utterance, so by the time you stop speaking the button list is already in hand.
Measured over a flight, the time from releasing the key to SLC reacting went from
a median of 4.0s to 1.1s — and per command, the time spent on anything other than
transcription is now near zero:

```
transcription 0.82s   whole command 0.8s   everything else -0.02s
transcription 1.45s   whole command 1.5s   everything else  0.05s
transcription 1.10s   whole command 1.1s   everything else  0.00s
```

Set `prescan = false` under `[behaviour]` if you would rather the list were read
strictly after you stop talking. `max_scan_age_seconds` bounds how stale that
list may get if you hold the key through a very long sentence; past it, SLC is
read again.

**Finding SLC's windows goes through Win32, not UIA.** Asking UI Automation for
the desktop's children and reading a process id off each cost 0.5s of that scan
on its own. `EnumWindows` answers the same question in 0.009s, and only the
handles that turn out to be SLC's are handed to UIA. If that lookup ever comes
up empty the old one still runs before the bridge concludes SLC is not there —
an empty result reads downstream as "SLC is offering no buttons", and that is
not a conclusion worth reaching quickly.

Caching the UIA tree, which looks like the obvious next step, is a dead end —
`tools/probe_cache.py` says why, and measures it against your own SLC.

---

## Requirements

- Windows 10/11
- Python 3.11+ (3.12 recommended — the config loader uses `tomllib`)
- Self-Loading Cargo v1.6+
- A CUDA GPU is strongly recommended for Whisper. CPU works but adds seconds to every command.
- Whisper's model is chosen automatically to fit the VRAM you have left — see below.
- **No API key needed** on the default offline backend — see below.
- The control panel draws its window with the **WebView2 runtime**, which is part of Windows 10 and 11 already; nothing here bundles a browser. If a stripped-down install does not have it, Microsoft's [Evergreen runtime](https://developer.microsoft.com/microsoft-edge/webview2/) is a free download, and running headless does not need it at all.

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

## Picking a Whisper model

`model = "auto"` (the default) reads free VRAM at startup and picks the largest
model that fits with headroom:

| Free VRAM | Model | Per command | Notes |
|---|---|---|---|
| ≥ 3600 MB | `large-v3` | 1.61s | best at translating non-English |
| ≥ 2200 MB | `medium` | 1.25s | noticeably looser translation |
| ≥ 1100 MB | `small` | 0.96s | translates non-English literally |
| ≥ 700 MB | `base` | 0.40s | weaker still |
| < 700 MB | `base` on CPU | ~1.6s | a starved GPU loses to a free CPU |

This matters because the bridge shares a card with the simulator and the
simulator wins: on a 16 GB card MSFS 2024 routinely holds 15 GB, which left
`large-v3` in float16 fighting for the last few hundred megabytes and turning
2-second transcriptions into 92-second ones.

Free VRAM is read when the bridge starts, so **start it after the simulator**
for the choice to reflect a real flight. To see what it would pick:

```bash
python -m slcvoiceai --check-hardware
```

Naming a model in `config.toml` overrides all of it — an explicit choice is
treated as a decision, not a suggestion.

## Matching intent: local first, cloud only if needed

`[intent] backend` picks the matcher, and `escalate_to` decides who gets asked
when it cannot settle an utterance.

**`fuzzy`** (default) — offline string matching plus the synonym table in
`aliases.py`. Free, instant, no API key, no VRAM beyond Whisper. That last
point matters: you are running MSFS at the same time, and a local 7–14B model
would want ~8 GB of the same card.

It handles anything close to a button's wording, and `aliases.py` covers the
synonyms that string similarity cannot reach on its own — *"zrozumiałem"*
arrives from Whisper as *"I understand"*, which shares no letters at all with
`ROGER`. Add your own there; each entry is scored alongside the button's real
name.

What it cannot do is follow phrasing with no shared vocabulary and no alias
yet written. That is what escalation is for.

### The cascade

```toml
[intent]
backend = "fuzzy"
escalate_to = "gemini"    # none | gemini | claude
```

Most commands are unambiguous — "roger", "connect the jetway", "intercom" —
and resolve offline at full confidence with **nothing sent anywhere**. Only the
awkward ones travel. In a logged flight that was a handful of utterances, not
one per command, which is what keeps it inside a free tier.

The log says which path each command took:

```
Fuzzy best: 'ROGER' 1.00 (runner-up 'INTERCOM >' 0.31, margin 0.69)
Settled offline - not asking gemini
...
Offline matcher unsure (Ambiguous: ...) - asking gemini
Gemini: "THAT'S PERFECT" 0.90 - pilot is satisfied with the volume
```

The idea is lifted from [BlueLine Realism](https://youtube.com/@BlueLineVibes),
an LSPDFR dispatch mod that solves the same problem for GTA V and logs the same
two outcomes. Its vocabulary-hint trick is in `vocabulary.py`.

**Gemini** needs a free key from [AI Studio](https://aistudio.google.com/apikey):

```bash
setx GEMINI_API_KEY your-key
```

**Claude** is the paid alternative (`ANTHROPIC_API_KEY`, roughly a third of a
grosz per escalated command). Either way the key is yours and the billing is
yours — see *What it costs, and who pays*.

## Use

### The control panel

Double-click **`SLCVoiceAI.bat`** — or, from a terminal:

```bash
python -m slcvoiceai --gui
```

(`SLCVoiceAI-console.bat` runs the same panel but keeps a console window, so
startup errors stay visible if the silent launcher does nothing.)

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

Hovering the version number in the corner says who wrote this and links to the
repository; the button in the middle of the title bar opens the tip jar. Both
open in your own browser, never in this window — it has no address bar to find
your way back from. Nothing on the page is fetched over the network, so the
panel opens the same with the connection pulled out.

### Hotkeys: three calls straight from the keyboard

Some things do not need saying. An Airbus rings the cabin and the ground crew
from two buttons on the overhead — ATT and MECH — and SLC answers both, but MSFS
does not reliably pass ATT through, so the cabin call is the one that sticks.

Three calls can be bound, and what each one presses is fixed:

| setting | what it does |
|---|---|
| `intercom` | call the cabin crew — the **ATT** button |
| `ground` | call the ground crew — the **MECH** button |
| `pa` | announcement to the passengers |
| `back` | back out of whichever menu is open |

`back` takes one binding for all three menus. That needed work: SLC lays out
**three** BACK buttons at once, one per menu, two of them at identical
coordinates and a third underneath a conversation's GO AHEAD. All of them report
a real rectangle, so the rectangle cannot say which one you can click. The bridge
now asks Windows what is actually at the point and keeps that one — see
`is_topmost` in `slcvoiceai/slc_ui.py`. It is asked only when two controls share
a name, so it costs nothing on the common path.

Set them in the panel: click a key in the **Keys bound to SLC buttons** card and
press the combination you want. It is written to `config.toml` and armed on the
spot — nothing needs restarting. Or write it yourself:

```toml
[hotkeys]
intercom = "ctrl+q"
ground   = "ctrl+w"
pa       = "ctrl+e"
back     = "ctrl+r"
```

**Use a combination, not a bare key.** MSFS already has a binding for nearly
every single key, so `ctrl+q` is far likelier to be free than `q`. Modifiers are
`ctrl`, `alt`, `shift` and `cmd` (also spelled `win`, `super` or `meta`); left
and right count as the same modifier, so `ctrl+q` fires on either Ctrl.

Nothing is bound by default. A combination already given to another call is
refused rather than quietly stolen, and so is the bare push-to-talk key — it
would fire every time you spoke.

Hotkeys obey `dry_run`, and they are held to the same flight guard as speech.
Pressing one costs the same few seconds a spoken command does, because SLC has
to be read either way.

### Starting by itself

Two different things, and it is worth knowing which one you want.

**The panel is already open, and should start listening when SLC does.** Tick
**"Start listening when SLC opens"** at the top of the panel. It waits with
nothing loaded — no speech model, no VRAM — until SLC turns up.

**The panel is not open, and should launch itself.** Tick **"Start SLCVoiceAI
with SLC"**, the box above it. That registers a small watcher to run at every
Windows login; the same thing from a terminal, if you prefer:

```bash
python tools/watch_for_slc.py --install
```

From the next login it sits in the background, and when `SLC.exe` appears it
runs `SLCVoiceAI.bat` exactly as double-clicking would. It launches on the
transition only: not again while SLC stays up, and not if the panel is already
open — so closing the panel mid-session leaves it closed. `--uninstall` removes
it, `--status` says what is installed and what is running, and nothing here
needs administrator rights (it is one value under `HKCU\...\Run`).

The watcher imports nothing but the standard library. It has to be cheap: it
runs from login to shutdown, and asking ctypes whether a process exists costs
about ten milliseconds every five seconds.

**Worth knowing before you install it.** The panel loads Whisper when it
starts, so starting it with SLC means starting it *after* the simulator — when
the graphics card has least to spare. That is what pushes the model down to
`small` and what makes the occasional command take tens of seconds. Launching
the panel before the simulator is still the better habit; the watcher is for
when you would rather not have to remember.

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

## What it costs, and who pays

Nothing, by default, for anyone.

The default `fuzzy` backend touches no network at all: Whisper runs on your own
machine and the matching is plain text comparison. You can clone this, run it,
and use it forever without an account anywhere.

If you switch to `backend = "claude"`, the requests go out under **your own**
API key, read from your own `ANTHROPIC_API_KEY` environment variable, billed to
your own account at roughly a third of a grosz per command. There is no server
in this project, no shared key, and no hosted component — every install talks
only to its own machine, and to Anthropic only if its own user set that up.

That matters if you are forking or redistributing this: nobody inherits anybody
else's bill, and the author of a fork pays nothing when others use it. The only
way that could change is if someone committed a real API key to a repository,
which is why `config.toml` is in `.gitignore` and the key is never read from a
file.

## Safety

Deliberate guardrails, because a misfire mid-approach is worse than being asked to repeat yourself:

- **Visibility filter.** The model is only ever shown controls SLC is currently displaying, so it cannot reach a command that is out of context for the phase of flight.
- **Denylist.** Session-ending and flight-destroying controls — `EXIT SELF-LOADING CARGO`, `CLOSE SELF-LOADING CARGO`, `CANCEL SINGLE FLIGHT`, `DISPATCH NEXT FLIGHT`, `DO NOT RESTORE PREVIOUS FLIGHT` and friends — are stripped before the model ever sees them. It cannot press what it cannot see. The list lives in `slcvoiceai/slc_ui.py`.
- **The denylist is checked against the tooltip too**, not only against the name, because some of those names are ours rather than SLC's. `cmdStandBy` has no accessible name at all and humanises to a harmless-looking `Stand By`; its tooltip reads *"Close SLC or Restart Flight"*.
- **Start-a-new-flight buttons are gated on flight state.** SLC leaves them on the toolbar for the whole flight, and pressing one ends it. They cannot be denylisted outright — at the launcher they are the ordinary way to begin — so they are offered only when SLC's stream export says there is no flight in progress. With the export off the bridge cannot tell, and keeps them hidden: you can always start a flight with the mouse, and you cannot un-end one.
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
  hardware.py   VRAM detection and model selection
  vocabulary.py Whisper hint list
  gemini.py     Gemini fallback matcher
  keys.py       config key names -> a key to listen for
  hotkeys.py    keys bound straight to an SLC button, no speaking
  gui.py        control panel (--gui): decides, and drives the page
  web/          the panel's markup, stylesheet and script - layout only
  i18n.py       panel wording, English and Polish
  aliases.py    synonym table for aviation phraseology
  update.py     notice when GitHub has a newer version
tools/
  probe_slc.py    standalone UIA diagnostic
  probe_cache.py  ways of reading the UIA tree, measured against each other
  close_slc.py    close SLC, answering its "are you sure?" dialog
  replay_log.py   replay a flight log against today's matcher
  watch_for_slc.py launch the panel when SLC starts (--install)
  bump_version.py set __version__ to today (the pre-commit hook runs it)
  hooks/          git hooks: git config core.hooksPath tools/hooks
tests/
  test_intent.py    routing regressions, runs without SLC
  test_hardware.py  model selection across VRAM levels
  test_cascade.py   the cloud is only asked when the local layer is unsure
  test_scan.py      the scan behaves as if it had not run in the background
  test_prescan.py   one key press, one scan, reused once
  test_windows.py   finding SLC's windows, and the windows to ignore
  test_flight_guard.py the six buttons that would end the flight
  test_update.py    version comparison, and failing quietly offline
  test_i18n.py      translations stay complete and keep their placeholders
  test_panel.py     what an activity line means, and the Python/page seam
  test_hotkeys.py   a bound key does one thing, or nothing
  test_overlap.py   same name, same place: which one can actually be clicked
  test_autostart.py stalls get explained; the panel arms itself when SLC opens
  test_watcher.py   launching the panel once, and not on top of itself
  test_config_save.py  writing a setting back without wrecking config.toml
  test_bump_version.py  the version never moves backwards
  test_config_keys.py  API keys: resolved, masked, never committed
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

**A phrase you keep saying never lands** — look it up in the log first. Every
utterance prints what Whisper actually heard, the full list of buttons SLC was
offering, and the score of the best match. That usually shows the problem in one
line: the button was not on offer at all, or the translation shares no words with
it.

The second case is what `aliases.py` is for. Whisper translates, so Polish
"piec na piec" arrives as "5 by 5" — not one word of which appears in
`LOUD AND CLEAR`, while `Stand By` happened to share "by". String similarity
compares spelling, not meaning; the alias table closes that gap without a model
or a network call. Add your phrase under the button it means, add a case to
`tests/test_intent.py` using the exact text from the log, and the gap stays
closed.

---

## Disclaimer

Not affiliated with, endorsed by, or supported by Self-Loading Cargo or Lanilogic. SLC is commercial software you must own separately. This project only automates its user interface from the outside; it does not modify, patch, decompile or redistribute any part of it.

## License

MIT — see [LICENSE](LICENSE).
