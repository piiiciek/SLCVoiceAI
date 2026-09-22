"""A small always-on-top panel for running and tuning the bridge.

The point of this window is to answer, without reading a log file:

* is the bridge actually listening, and on which device
* what did it hear, what did it match, and did it press anything
* why did it refuse - the runner-up score usually explains it

It also takes typed input, so the whole matching chain can be exercised
without a microphone and without being in a flight. That reads SLC when
you ask it to and not before: a live list of SLC's buttons, refreshed on a
timer, meant the panel scanned SLC continuously for as long as it was open
and competed with the scans that carry a command.

Why a webview and not tkinter
-----------------------------

The window is drawn by the WebView2 runtime that ships with Windows, via
`pywebview`. Nothing here bundles a browser: the engine is already on the
machine, and the dependency costs 2.6 MB to install beside the 2 GB of
CUDA libraries faster-whisper already requires. The panel it replaced was
tkinter, chosen back when the alternative looked like shipping 200 MB.

The split is strict, and worth keeping that way:

* Python owns every decision and every word. What an entry means, whether
  a score passes, which language a phrase is in - all answered here.
* `web/` owns nothing but layout. It holds no wording, only keys.

So adding a language still means editing `i18n.py` alone, and the rules
the old panel was careful about - the log stays in English, the feed is
never retranslated - are unchanged and now have tests of their own.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import logging
import queue
import re
import threading
import time
from pathlib import Path

import webview

from . import i18n
from .config import Config
from .i18n import t
from .slc_ui import SlcUI

log = logging.getLogger(__name__)

WEB = Path(__file__).resolve().parent / "web"

#: Which CSS state each status phrase puts the card into. It lives here
#: rather than in the page so the page never has to recognise the word
#: "listening" in the pilot's language.
STATUS_STATES = {
    "status.stopped": "stopped",
    "status.loading": "loading",
    "status.listening": "listening",
    "status.failed": "failed",
}

#: How to spell the cloud matchers when naming one to the pilot. The
#: config takes them lowercase; nobody writes "gemini" in a sentence.
AI_NAMES = {"gemini": "Gemini", "claude": "Claude"}

#: Where the tip jar lives. The button in the title bar is drawn by
#: hand rather than by the script buymeacoffee.com hands out: that script
#: arrives over the network before the page can finish parsing, ends in
#: document.write - which blanks a page that has already parsed - and
#: would be running with the whole Python bridge in reach.
COFFEE_URL = "https://buymeacoffee.com/piciek"


def links() -> dict[str, str]:
    """Pages the panel is allowed to send someone to.

    Named rather than passed as a URL. The page asks for "coffee" and this
    file decides what that means, so nothing on the page can point the
    browser somewhere this module has not already agreed to.
    """
    from . import update

    return {"repository": update.RELEASES_URL, "coffee": COFFEE_URL}


def without_scheme(url: str) -> str:
    """github.com/piiiciek/SLCVoiceAI, not https://github.com/...

    The hover card has room for one and not the other, and showing the
    address at all is the point: it is how someone decides whether to
    click it.
    """
    return url.split("://", 1)[-1]


_I18N_ATTR = re.compile(r'data-i18n(?:-[a-z-]+)?="([^"]+)"')
_I18N_SAY = re.compile(r'\bsay\(\s*"([a-z][a-z0-9_.]+)"\s*\)')


def _running_version() -> str:
    from . import __version__
    return __version__


def phrase_keys(markup: str, script: str = "") -> list[str]:
    """Every phrase the page asks for, read off the page itself.

    Collected rather than listed by hand: a hand-kept list drifts from the
    page the first time someone adds a label, and the failure mode is a
    pilot reading `card.controls` where a heading belongs.

    Both places count. A label written in the markup carries a data-i18n
    attribute, but rows the script builds - the hotkey card - ask for
    their wording with say("..."), and scanning only the markup left those
    showing their raw keys.
    """
    return sorted(set(_I18N_ATTR.findall(markup))
                  | set(_I18N_SAY.findall(script)))


def tag_for(message: str, level: int) -> str:
    """Which colour an activity line gets.

    Matching on English words in the log is deliberate, and is the reason
    the log is not translated: `tools/replay_log.py` parses the same words,
    and every log already sent to someone for help is in English.
    Translating them would break colouring and replay at once.

    The words have to be ones the bridge really logs, which the old panel
    had no way to check - it carried a branch for "Below confidence" that
    nothing has ever written. A decline is logged as "Declined: <reason>"
    and is caught below. tests/test_panel.py now holds the two sides
    together.
    """
    if level >= logging.ERROR:
        return "bad"
    if level >= logging.WARNING:
        return "warn"
    if "Pressed" in message:
        return "ok"
    if "DRY RUN" in message:
        return "accent"
    if "Transcribed" in message or "Captured" in message:
        return "plain"
    if "Declined" in message:
        return "warn"
    if "Ready" in message:
        return "ok"
    return "muted"


class QueueHandler(logging.Handler):
    """Funnel log records to the panel."""

    def __init__(self, sink: queue.Queue):
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        # Never let a UI problem take the bridge down.
        try:
            self.sink.put_nowait(record)
        except Exception:
            pass


def _guard(method):
    """Nothing the page calls may raise into pywebview's bridge.

    An exception there becomes a rejected promise the pilot never sees,
    leaving the panel looking wedged with no clue why.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:
            log.exception("Panel action %s failed", method.__name__)
            return None
    return wrapper


class Api:
    """The methods the page is allowed to call.

    Deliberately thin: everything public here is reachable from
    JavaScript, so the surface stays at what the panel's controls need.
    """

    def __init__(self, app: "App"):
        self._app = app

    @_guard
    def boot(self) -> dict:
        return self._app.opening_state()

    @_guard
    def toggle(self) -> None:
        self._app.toggle()

    @_guard
    def set_dry(self, on: bool) -> None:
        self._app.set_dry(bool(on))

    @_guard
    def set_threshold(self, value: float) -> None:
        self._app.set_threshold(float(value))

    @_guard
    def set_language(self, code: str) -> None:
        self._app.set_language(str(code))

    @_guard
    def try_phrase(self, said: str) -> None:
        self._app.try_phrase(str(said))

    @_guard
    def open_link(self, name: str) -> None:
        self._app.open_link(str(name))

    @_guard
    def set_auto_start(self, on: bool) -> None:
        self._app.set_auto_start(bool(on))

    @_guard
    def set_auto_launch(self, on: bool) -> bool:
        return self._app.set_auto_launch(bool(on))

    @_guard
    def set_binding(self, action: str, event: dict) -> dict:
        return self._app.set_binding(str(action), event or {})

    @_guard
    def clear_binding(self, action: str) -> dict:
        return self._app.clear_binding(str(action))


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ui = SlcUI(cfg.slc.process_name)
        self.bridge = None
        self.ptt = None
        self.hotkeys = None
        self.window = None

        i18n.set_language(cfg.ui.language)
        self._keys = phrase_keys(
            (WEB / "index.html").read_text(encoding="utf-8"),
            (WEB / "panel.js").read_text(encoding="utf-8"))

        #: Which phrases are showing, so a language change can redraw them.
        self._status_key = "status.stopped"
        self._button_key = "button.start"
        self._button_enabled = True
        self._subtitle = ""
        self._update_version = None

        self.records: queue.Queue = queue.Queue()
        self._outbox: queue.Queue = queue.Queue()
        #: Entries written before the page existed to show them.
        self._pending: list[dict] = []
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._closing = False

    # -- talking to the page -----------------------------------------------
    def _push(self, function: str, *args) -> None:
        """Queue a call into the page.

        Everything goes through one queue drained by one thread, which
        keeps entries in order and means a method the page calls never
        blocks waiting for the page to answer.
        """
        self._outbox.put((function, args))

    def _pump(self) -> None:
        # Nothing may be evaluated until the page has booted, or it lands
        # in a document that has not run panel.js yet.
        self._ready.wait()
        while True:
            function, args = self._outbox.get()
            if function is None or self._closing:
                break
            payload = ", ".join(json.dumps(arg) for arg in args)
            try:
                self.window.evaluate_js("window.panel && panel.{f}({a});".format(
                    f=function, a=payload))
            except Exception:
                # The window can go away between the check and the call.
                log.debug("Could not reach the panel with %s()", function,
                          exc_info=True)

    def _write(self, text: str, tag: str = "muted") -> None:
        entry = {"time": "{:%H:%M:%S}".format(dt.datetime.now()),
                 "text": text, "tag": tag}
        with self._lock:
            if not self._ready.is_set():
                self._pending.append(entry)
                return
        self._push("feed", entry)

    # -- logging bridge ----------------------------------------------------
    def _attach_logging(self) -> None:
        handler = QueueHandler(self.records)
        handler.setLevel(logging.INFO)
        root = logging.getLogger("slcvoiceai")
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    def _drain_logs(self) -> None:
        while not self._closing:
            try:
                record = self.records.get(timeout=0.25)
            except queue.Empty:
                continue
            message = record.getMessage()
            self._write(message, tag_for(message, record.levelno))

    # -- opening state ------------------------------------------------------
    def opening_state(self) -> dict:
        """Everything the page needs to draw itself, in one answer.

        One call rather than a stream of pushes, so the panel is never
        briefly visible with English labels or a stale threshold.
        """
        with self._lock:
            entries, self._pending = self._pending, []
            self._ready.set()
        return {
            "version": _running_version(),
            "repo": without_scheme(links()["repository"]),
            "phrases": self.phrases(),
            "languages": sorted(i18n.available().items()),
            "language": i18n.current(),
            "dry": bool(self.cfg.behaviour.dry_run),
            "auto": bool(self.cfg.behaviour.start_with_slc),
            "launch": self._launches_with_slc(),
            "confidence": float(self.cfg.behaviour.min_confidence),
            "status": {"text": t(self._status_key),
                       "state": STATUS_STATES[self._status_key]},
            "button": t(self._button_key),
            "subtitle": self._subtitle,
            "hotkeys": self.hotkey_rows(),
            "entries": entries,
        }

    def phrases(self) -> dict[str, str]:
        said = {key: t(key) for key in self._keys}
        if "control.hint" in said:
            said["control.hint"] = self._confidence_hint()
        return said

    def _confidence_hint(self) -> str:
        """What raising the floor actually does, which depends on setup.

        With an escalation backend configured, a match below the floor is
        handed to it by name. Without one there is nobody to ask, so the
        sentence must not promise that anything is - it simply declines.
        """
        helper = self.cfg.intent.escalate_to.strip().lower()
        if helper in ("", "none"):
            return t("control.hint_alone")
        return t("control.hint", ai=AI_NAMES.get(helper, helper.title()))

    # -- language ----------------------------------------------------------
    def _set_status(self, key: str) -> None:
        self._status_key = key
        self._push("status", t(key), STATUS_STATES[key])

    def _set_button(self, key: str, enabled: bool = True) -> None:
        self._button_key, self._button_enabled = key, enabled
        self._push("button", t(key), enabled)

    def set_language(self, code: str) -> None:
        i18n.set_language(code)
        self.cfg.ui.language = i18n.current()
        self._retranslate()
        self._write(t("feed.language_changed",
                      name=i18n.available().get(i18n.current(), code)), "accent")
        # The resolved code, not what was asked for: picking a language the
        # panel does not speak must not write that into the file.
        self._remember("ui", "language", i18n.current())

    def _retranslate(self) -> None:
        """Re-label everything in place.

        The activity feed is left as it stands: those lines are a record of
        what happened, and rewriting history in a new language would be a
        strange thing for a log to do. New entries arrive translated.

        The rule for everything else: what the page was sent as finished
        text has to be sent again. The data-i18n sweep in panel.js only
        redraws what carries a key, and anything Python translated before
        putting it in a message - a status phrase, a hotkey's label - is
        already a sentence by the time the page sees it.
        """
        self._push("phrases", self.phrases())
        self._set_status(self._status_key)
        self._set_button(self._button_key, self._button_enabled)
        self._push("hotkeys", self.hotkey_rows())
        if self.bridge is not None:
            self._describe_bridge()
        if self._update_version:
            self._push("update", t("update.banner", new=self._update_version,
                                   old=_running_version()))

    # -- update notice ------------------------------------------------------
    def _check_for_updates(self) -> None:
        """Ask GitHub, off the UI thread, and only speak up if behind."""
        if not self.cfg.behaviour.check_for_updates:
            return

        def work():
            from . import update

            newer = update.check()
            if newer:
                self._show_update(newer)

        threading.Thread(target=work, daemon=True).start()

    def _show_update(self, version: str) -> None:
        from . import update

        self._update_version = version
        self._push("update", t("update.banner", new=version,
                               old=_running_version()))
        self._write(t("update.feed", new=version, old=_running_version(),
                      url=update.RELEASES_URL), "ok")

    def open_link(self, name: str) -> None:
        import webbrowser

        url = links().get(name)
        if url is None:
            log.warning("The panel asked to open %r, which is not one of %s",
                        name, sorted(links()))
            return
        try:
            webbrowser.open(url)
        except Exception as exc:  # pragma: no cover - browser is the OS's
            log.warning("Could not open %s: %s", url, exc)

    # -- reading SLC on demand ----------------------------------------------
    def _scan_in_background(self, then) -> None:
        """Read SLC once, off the UI thread, and hand the result to `then`.

        The panel used to keep a live list of SLC's buttons, refreshed every
        2.5 seconds for as long as it was open. That was affordable when a
        scan cost 1.7s at the launcher; in a flight a scan costs 3-8s, so the
        panel was reading SLC essentially without pause and competing with
        the scans that actually carry a command. Now nothing scans unless
        someone asks it to.
        """
        def work():
            from .app import _Scan

            scan = _Scan(self.ui)
            scan.start()
            try:
                actions, error = scan.result(), None
            except Exception as exc:
                actions, error = [], exc
            then(actions, error)

        threading.Thread(target=work, daemon=True).start()

    # -- controls ----------------------------------------------------------
    def _remember(self, section: str, key: str, value) -> None:
        """Write a setting back to the config.toml it was loaded from.

        A control that forgets what you set it to the moment the window
        closes is worse than no control, so this runs on every change
        rather than at shutdown - a panel killed mid-flight still keeps
        the tuning that was done during it.

        Failing to save must never take the panel down, but it must not
        be silent either: the pilot would go on believing the setting had
        stuck.
        """
        from . import config as config_module

        if self.cfg.source is None:
            return          # nothing was loaded from disk; nothing to write
        try:
            config_module.save_settings(self.cfg.source, {section: {key: value}})
        except Exception as exc:
            log.debug("Could not save %s.%s", section, key, exc_info=True)
            self._write(t("feed.save_failed", error=exc), "warn")

    def set_dry(self, on: bool) -> None:
        self.cfg.behaviour.dry_run = on
        if self.bridge is not None:
            self.bridge.cfg.behaviour.dry_run = on
        self._write(t("feed.dry_run",
                      state=t("feed.dry_on") if on else t("feed.dry_off")),
                    "accent")
        self._remember("behaviour", "dry_run", on)

    def set_auto_start(self, on: bool) -> None:
        self.cfg.behaviour.start_with_slc = on
        self._write(t("feed.auto_start",
                      state=t("feed.dry_on") if on else t("feed.dry_off")),
                    "accent")
        self._remember("behaviour", "start_with_slc", on)
        if on:
            # Switched on with SLC already up, the pilot means now, not the
            # next time they remember to launch it.
            self._maybe_auto_start()

    def _launches_with_slc(self) -> bool:
        """Is the login entry there? Never let this stop the panel opening."""
        from . import autostart

        try:
            return autostart.is_installed()
        except Exception:
            log.debug("Could not read the login entry", exc_info=True)
            return False

    def set_auto_launch(self, on: bool) -> bool:
        """Have Windows start the SLC watcher at login, or stop it.

        Returns what is actually registered afterwards, not what was
        asked for. The registry can refuse, and a tick box showing a
        setting that did not take is worse than no tick box.

        This lives outside config.toml on purpose: it is a Windows
        setting, and copying the folder to another machine should not
        bring an autostart entry with it.
        """
        from . import autostart

        try:
            autostart.set_installed(on)
        except Exception as exc:
            log.warning("Could not change the login entry: %s", exc)
            self._write(t("feed.auto_launch_failed", error=exc), "warn")
            return autostart.is_installed()

        now = autostart.is_installed()
        self._write(t("feed.auto_launch",
                      state=t("feed.dry_on") if now else t("feed.dry_off")),
                    "accent")

        # Registering it is not running it. The Run key is read at login
        # and nowhere else, so ticking this box meant nothing at all until
        # the machine was next restarted - the entry sat there, correct,
        # and no watcher existed. Switching it off needs no matching call:
        # the watcher that is running reads the same entry and stands down
        # within a few seconds of it going.
        if now:
            try:
                if autostart.start():
                    self._write(t("feed.watcher_started"), "muted")
            except Exception as exc:
                log.warning("Could not start the watcher: %s", exc)
                self._write(t("feed.watcher_failed", error=exc), "warn")
        return now

    def _watch_for_slc(self) -> None:
        """Arm the bridge when SLC appears, if that was asked for.

        Polls rather than subscribing: finding SLC's windows costs about
        five milliseconds, so once every few seconds is free - and there is
        no Windows event for "a process started" that does not want
        administrator rights and process auditing switched on.
        """
        def looking() -> bool:
            # Every read, including the first. UI Automation is not always
            # answering at the moment the panel opens, and letting that
            # first one through uncaught killed the thread outright - the
            # box stayed ticked and nothing ever watched again.
            try:
                return self.ui.is_running()
            except Exception:
                log.debug("Could not tell whether SLC is up", exc_info=True)
                return False

        seen = looking()
        while not self._closing:
            time.sleep(self.WATCH_SECONDS)
            running = looking()
            if running and not seen:
                self._maybe_auto_start()
            seen = running

    def _maybe_auto_start(self) -> None:
        """Start listening, if that is wanted and not already happening."""
        if not self.cfg.behaviour.start_with_slc or self.bridge is not None:
            return
        if not self.ui.is_running():
            return
        self._write(t("feed.slc_appeared"), "accent")
        self._start()

    def set_threshold(self, value: float) -> None:
        value = round(value, 2)
        self.cfg.behaviour.min_confidence = value
        if self.bridge is not None:
            self.bridge.cfg.behaviour.min_confidence = value
            router = getattr(self.bridge, "router", None)
            if hasattr(router, "min_confidence"):
                router.min_confidence = value
        self._remember("behaviour", "min_confidence", value)

    #: How often to look for SLC when auto-start is armed. Long enough to
    #: be invisible, short enough that the model is loading while the
    #: pilot is still clicking through SLC's own menus.
    WATCH_SECONDS = 4.0

    # -- hotkeys -----------------------------------------------------------
    def hotkey_rows(self) -> list[dict]:
        """The three calls, each with the combination bound to it.

        Always all three, bound or not: the set is fixed, so an unbound
        call is a row showing nothing rather than a row that is missing.
        """
        from . import keys
        from .hotkeys import ACTIONS

        rows = []
        for action in ACTIONS:
            spec = self.cfg.hotkeys.get(action, "")
            rows.append({"action": action,
                         "label": t("hotkeys." + action),
                         "combo": spec,
                         "shown": keys.pretty(spec) if spec else ""})
        return rows

    def set_binding(self, action: str, event: dict) -> dict:
        """Bind one call to the combination the pilot just pressed."""
        from . import keys
        from .hotkeys import ACTIONS

        if action not in ACTIONS:
            return self._hotkey_refusal(t("hotkeys.unusable"))

        spec = keys.from_browser(event or {})
        if spec is None:
            return self._hotkey_refusal(t("hotkeys.unusable"))

        modifiers, key = keys.parse_combo(spec)
        if not modifiers and key == self.cfg.audio.ptt_key.strip().lower():
            return self._hotkey_refusal(t("hotkeys.is_ptt", key=key))

        taken = [other for other, bound in self.cfg.hotkeys.items()
                 if bound == spec and other != action]
        if taken:
            return self._hotkey_refusal(
                t("hotkeys.duplicate", key=keys.pretty(spec)))

        bindings = dict(self.cfg.hotkeys)
        bindings[action] = spec
        return self._store_hotkeys(bindings)

    def clear_binding(self, action: str) -> dict:
        bindings = {k: v for k, v in self.cfg.hotkeys.items() if k != action}
        return self._store_hotkeys(bindings)

    def _store_hotkeys(self, bindings: dict) -> dict:
        """Keep it, arm it, save it - in that order, so a failure to write
        still leaves the binding working for this session."""
        self.cfg.hotkeys = bindings
        self._rearm_hotkeys()

        if self.cfg.source is not None:
            from . import config as config_module
            try:
                config_module.save_hotkeys(self.cfg.source, bindings)
            except Exception as exc:
                log.debug("Could not save hotkeys", exc_info=True)
                self._write(t("feed.save_failed", error=exc), "warn")

        self._write(t("feed.hotkeys", what=self._describe_hotkeys()), "accent")
        return {"rows": self.hotkey_rows(), "error": ""}

    def _hotkey_refusal(self, message: str) -> dict:
        """Say why, and hand back what is actually bound."""
        self._write(message, "warn")
        return {"rows": self.hotkey_rows(), "error": message}

    def _describe_hotkeys(self) -> str:
        from . import keys

        if not self.cfg.hotkeys:
            return t("hotkeys.none")
        return ", ".join(
            "{k} -> {a}".format(k=keys.pretty(spec), a=t("hotkeys." + action))
            for action, spec in sorted(self.cfg.hotkeys.items()))

    def _rearm_hotkeys(self) -> None:
        """Take effect now, not at the next start.

        A binding saved but inert until the program restarts would be the
        same wart the settings had before they were written back at all.
        """
        if self.bridge is None:
            return                   # nothing is listening yet; start() arms it
        if self.hotkeys is not None:
            self.hotkeys.stop()
        self.bridge.cfg.hotkeys = self.cfg.hotkeys
        self.hotkeys = self.bridge.start_hotkeys()

    def try_phrase(self, said: str) -> None:
        said = said.strip()
        if not said:
            return
        self._write(t("feed.typed", said=said), "accent")
        self._scan_in_background(lambda actions, error:
                                 self._show_ranking(said, actions, error))

    def _show_ranking(self, said: str, actions: list, error) -> None:
        if error is not None:
            self._write(t("feed.read_failed", error=error), "warn")
            return
        if not actions:
            self._write(t("feed.no_actions"), "warn")
            return

        router = getattr(self.bridge, "router", None)
        if router is None:
            from .intent import build_router
            router = build_router(self.cfg)

        if hasattr(router, "rank"):
            for score, _i, action in router.rank(said, actions)[:4]:
                passes = score >= self.cfg.behaviour.min_confidence
                self._write("     {:.2f}  {:<34} {}".format(
                    score, action.name,
                    t("feed.pass") if passes else t("feed.below_floor")),
                    "ok" if passes else "muted")
        else:
            decision = router.decide(said, actions)
            if decision.action_index is None:
                self._write(t("feed.declined", reason=decision.reasoning), "warn")
            else:
                self._write("     {:.2f}  {}".format(
                    decision.confidence,
                    actions[decision.action_index].name), "ok")

    # -- bridge lifecycle --------------------------------------------------
    def toggle(self) -> None:
        if self.bridge is None:
            self._start()
        else:
            self._stop()

    def _start(self) -> None:
        self._set_button("button.starting", enabled=False)
        self._set_status("status.loading")
        self._write(t("feed.loading_model"), "muted")
        threading.Thread(target=self._start_worker, daemon=True).start()

    def _start_worker(self) -> None:
        try:
            from .app import Bridge
            from .audio import PushToTalk

            bridge = Bridge(self.cfg)
            ptt = PushToTalk(self.cfg.audio, on_talk_start=bridge.prescan)
            ptt.start()
            self.hotkeys = bridge.start_hotkeys()
            self.bridge, self.ptt = bridge, ptt
            self._started()
            for captured_at, clip in ptt.clips():
                try:
                    bridge.handle(clip, captured_at)
                except Exception:
                    log.exception("Failed to handle an utterance; continuing.")
        except Exception as exc:
            log.error("Could not start: %s", exc)
            self._start_failed()

    def _started(self) -> None:
        self._set_status("status.listening")
        self._describe_bridge()
        self._set_button("button.stop")

    def _describe_bridge(self) -> None:
        """The subtitle line, rebuilt - it is also what a language change
        has to redraw, so it lives on its own."""
        device = getattr(getattr(self.bridge, "stt", None), "_device", "?")
        self._subtitle = t("subtitle.ready", key=self.cfg.audio.ptt_key,
                           model=self.cfg.stt.model, dev=device,
                           backend=self.cfg.intent.backend)
        self._push("subtitle", self._subtitle)

    def _start_failed(self) -> None:
        self.bridge = None
        self._set_status("status.failed")
        self._set_button("button.start")

    def _stop(self) -> None:
        if self.ptt is not None:
            self.ptt.stop()
        if self.hotkeys is not None:
            self.hotkeys.stop()
        self.bridge, self.ptt, self.hotkeys = None, None, None
        self._set_status("status.stopped")
        self._subtitle = ""
        self._push("subtitle", "")
        self._set_button("button.start")
        self._write(t("feed.stopped"), "muted")

    def _on_closed(self) -> None:
        self._closing = True
        if self.ptt is not None:
            self.ptt.stop()
        if self.hotkeys is not None:
            self.hotkeys.stop()
        # Let the pump out of its wait, whichever side of boot it is on.
        self._ready.set()
        self._outbox.put((None, ()))

    # -- running -----------------------------------------------------------
    def run(self) -> int:
        self._attach_logging()
        self._write(t("feed.welcome", key=self.cfg.audio.ptt_key), "muted")
        self._write(t("feed.welcome_typed"), "muted")

        self.window = webview.create_window(
            "SLCVoiceAI",
            str(WEB / "index.html"),
            js_api=Api(self),
            # The window, not the page: Windows keeps 16 of these for the
            # frame, so 620 is the 604 the title bar is laid out against.
            # Measured, after a version of this shipped at 580 and gave
            # the page 564 - one pixel under the width at which the name
            # beside the logo stands down.
            width=620,
            height=760,
            min_size=(430, 480),
            on_top=True,
            background_color="#11161c",
        )
        self.window.events.closed += self._on_closed

        for worker in (self._pump, self._drain_logs, self._watch_for_slc):
            threading.Thread(target=worker, daemon=True).start()
        self._check_for_updates()

        webview.start()
        return 0


def run(cfg: Config) -> int:
    return App(cfg).run()
