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

Because the available-button list is read fresh on every utterance, the bridge inherits SLC's context sensitivity for free. It can only ever press something SLC is already offering.

### What this gets you

- **Speak Polish, German, Spanish — whatever.** Whisper transcribes it, Claude translates the intent, SLC gets an English button press.
- **Speak loosely.** "tell them we're good to push", "let's get moving", "możemy pchać" all land on the pushback request.
- **No grammar, no confidence threshold, no voice training.**
- **Survives SLC updates.** Matching is by button name, not by binary offsets.

---

## Status: unverified on the communications popup

**Read this before investing time.**

The UI Automation approach is confirmed working against SLC's launcher window — buttons, names and enabled state all read correctly, and they are invokable. What has **not** yet been confirmed is that SLC's in-flight *communications popup* exposes its buttons the same way. If those buttons are custom-drawn on a `Canvas` without automation peers, UIA will not see them and this approach needs an OCR fallback instead.

Find out in two minutes before you configure anything else:

```bash
python tools/probe_slc.py --watch
```

Start a flight, open the communications menu, and watch the output. If you see your comms options listed as `[Button]` entries with `patterns=Invoke`, the bridge will work. If the popup shows up as an empty or nameless subtree, it will not — please open an issue with the probe output.

The probe also takes `--process` if you want to point it at something else to confirm your setup is sane before SLC is even running:

```bash
python tools/probe_slc.py --process chrome.exe --depth 6
```

---

## Requirements

- Windows 10/11
- Python 3.11+ (3.12 recommended — the config loader uses `tomllib`)
- Self-Loading Cargo v1.6+
- An [Anthropic API key](https://console.anthropic.com/)
- A CUDA GPU is strongly recommended for Whisper. CPU works but adds seconds to every command.

## Install

```bash
git clone https://github.com/piiiciek/SLCVoiceAI.git
cd SLCVoiceAI
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Set your API key once, in a terminal:

```bash
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Then copy the example config and edit it:

```bash
copy config.example.toml config.toml
```

The two settings worth checking first:

- **`ptt_key`** — must be a *different* key from SLC's own PTT (SLC defaults to `LeftCtrl`), or the two will fight over the same press. `f13` is a good choice if your keyboard or Stream Deck can send it.
- **`input_device`** — leave empty to use the Windows default, but verify that the default is the microphone you actually speak into. Virtual devices such as *Steam Streaming Microphone* frequently steal the default slot and record silence.

```bash
python -m slcvoiceai --list-devices
```

## Use

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

- **Denylist.** Destructive or session-ending controls — `EXIT SELF-LOADING CARGO`, `DELETE`, `RESET` and friends — are filtered out of the list before the model ever sees them. It cannot press what it cannot see. The list lives in `slcvoiceai/slc_ui.py`.
- **Confidence floor.** Below `min_confidence` (default `0.55`) the bridge does nothing and logs why. The model is also explicitly instructed that declining is a valid answer.

## Privacy

Audio never leaves your machine — Whisper runs locally. What *is* sent to the Anthropic API is the resulting transcript, the list of button names SLC is currently showing, and whatever flight context you enabled in `[slc] stream_export_dir`.

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
tools/
  probe_slc.py  standalone UIA diagnostic
```

## Troubleshooting

**"SLC is offering no buttons right now"** — either SLC has no communications popup open, or its buttons are not UIA-visible. Run the probe.

**Whisper falls back to CPU** — your PyTorch/CUDA install does not see the GPU. The bridge will still work, just slower.

**Nothing is transcribed** — wrong input device. `--list-devices`, then set `input_device` explicitly.

**It presses the wrong thing** — raise `min_confidence`, and enable `stream_export_dir` so the model knows what phase of flight you are in.

---

## Disclaimer

Not affiliated with, endorsed by, or supported by Self-Loading Cargo or Lanilogic. SLC is commercial software you must own separately. This project only automates its user interface from the outside; it does not modify, patch, decompile or redistribute any part of it.

## License

MIT — see [LICENSE](LICENSE).
