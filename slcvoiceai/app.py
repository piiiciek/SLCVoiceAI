"""Wire the pieces together: hold a key, speak, SLC acts."""

from __future__ import annotations

import logging
import sys
import threading
import time

from .config import Config
from .context import (FLIGHT_FIELDS, flight_is_underway, format_context,
                      read_flight_context)
from .intent import build_router
from .slc_ui import SlcUI, UIAUnavailable, starts_a_new_flight

log = logging.getLogger(__name__)


def setup_logging(log_file: str) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)


class _Scan:
    """Reads SLC's buttons on a worker thread, alongside transcription.

    UI Automation is COM, so the thread needs its own apartment - and which
    one matters. Measured against a live SLC, the same scan takes 2.14s in a
    single-threaded apartment and 2.90s in a multi-threaded one, where every
    call has to be marshalled. STA also beats running it on the main thread
    (2.47s).
    """

    def __init__(self, ui: SlcUI):
        self.ui = ui
        self._thread: threading.Thread | None = None
        self._actions: list | None = None
        self._error: Exception | None = None
        self.started_at: float | None = None

    def start(self) -> None:
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def age(self) -> float:
        """How long ago this scan began - how stale its picture of SLC is."""
        if self.started_at is None:
            return 0.0
        return time.time() - self.started_at

    def _run(self) -> None:
        try:
            import comtypes
            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
        except Exception:  # pragma: no cover - already initialised is fine
            pass
        try:
            self._actions = self.ui.list_actions()
        except Exception as exc:
            self._error = exc

    def result(self, timeout: float = 15.0) -> list:
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise UIAUnavailable("the UI scan did not finish in time")
        if self._error is not None:
            raise self._error
        return self._actions or []


class Bridge:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ui = SlcUI(cfg.slc.process_name)
        self.router = build_router(cfg)
        self._pending: _Scan | None = None
        self._pending_lock = threading.Lock()
        self._last_flight_state: dict | None = None
        self._last_withheld: tuple | None = None
        # Imported lazily: loading Whisper takes a while and pulls in CUDA.
        from .stt import Transcriber
        self.stt = Transcriber(cfg.stt)

    def prescan(self) -> None:
        """Start reading SLC the moment the pilot presses the key.

        A scan takes about as long as a short sentence, and the button list
        does not depend on what is about to be said. Running it while the
        pilot is still speaking takes it off the critical path altogether,
        which is worth far more than any amount of tuning the scan itself:
        measured against a live SLC, every faster-looking way of reading the
        tree either came out the same or could not see most of the buttons.
        """
        if not self.cfg.behaviour.prescan:
            return
        scan = _Scan(self.ui)
        scan.start()
        with self._pending_lock:
            self._pending = scan

    def _claim_scan(self) -> tuple[_Scan, bool]:
        """The scan that started with the key, or a fresh one beside it.

        Returns the scan and whether it came from the key press, because a
        pre-scan that failed deserves a second attempt: it ran seconds ago,
        under conditions that have since changed.
        """
        with self._pending_lock:
            scan, self._pending = self._pending, None

        if scan is not None:
            limit = self.cfg.behaviour.max_scan_age_seconds
            if not limit or scan.age() <= limit:
                return scan, True
            log.info("The scan from the key press is %.0fs old; reading SLC "
                     "again rather than trusting it.", scan.age())

        scan = _Scan(self.ui)
        scan.start()
        return scan, False

    def _actions_for(self, scan: _Scan, from_prescan: bool, text: str):
        """SLC's current buttons, or None if it could not be read."""
        try:
            return scan.result()
        except UIAUnavailable as exc:
            if not from_prescan:
                log.error("Heard %r but %s - command dropped, please say it "
                          "again.", text, exc)
                return None
            log.info("The scan started with the key failed (%s); asking SLC "
                     "again before giving up on what was said.", exc)

        retry = _Scan(self.ui)
        retry.start()
        try:
            return retry.result()
        except UIAUnavailable as exc:
            log.error("Heard %r but %s - command dropped, please say it "
                      "again.", text, exc)
            return None

    def _log_flight_state(self, flight: dict) -> None:
        """Say what SLC reported, the first time and whenever it changes.

        Only the fields the guard actually reads. The export also carries
        altitude and vertical speed, which change on every single utterance
        and would bury the log in noise.
        """
        seen = {field: (flight.get(field) or "").strip()
                for field in FLIGHT_FIELDS}
        if seen == self._last_flight_state:
            return
        self._last_flight_state = seen
        if not flight:
            log.info("SLC flight state: nothing exported "
                     "([slc] stream_export_dir is not set).")
            return
        reported = ", ".join("{k}={v!r}".format(k=k, v=v)
                             for k, v in seen.items()) or "(none of them)"
        log.info("SLC flight state: %s -> flight underway: %s",
                 reported, flight_is_underway(flight))

    def _without_flight_enders(self, actions: list, flight: dict) -> list:
        """Hide the start-a-new-flight buttons unless there is no flight.

        SLC leaves them on the toolbar for the whole flight, next to the
        seatbelt sign, and pressing one throws the flight away. They are not
        denylisted outright because at the launcher they are the ordinary
        way to begin - so the question is whether a flight is in progress,
        and only SLC can answer it.

        When SLC is not answering, the buttons stay hidden. A pilot who
        wanted one can click it; a pilot who loses a flight to a misheard
        sentence cannot get it back.
        """
        def risky_one(action) -> bool:
            return starts_a_new_flight(action.name,
                                       getattr(action, "automation_id", ""),
                                       getattr(action, "tooltip", ""))

        risky = [a for a in actions if risky_one(a)]
        if not risky:
            return actions

        underway = flight_is_underway(flight)
        if underway is False:
            return actions

        # Once, and again when the answer changes. Said on every utterance it
        # buried the log in a sentence that never varies - and a log nobody
        # reads is the one thing this project cannot afford, because reading
        # it is how every one of these problems was found.
        withheld = tuple(sorted(a.name for a in risky)), underway
        if withheld != self._last_withheld:
            self._last_withheld = withheld
            names = ", ".join(repr(a.name) for a in risky)
            if underway is None:
                log.info("Not offering %s: SLC's stream export is off, so "
                         "there is no way to tell whether a flight is in "
                         "progress. Set [slc] stream_export_dir to have them "
                         "back at the launcher.", names)
            else:
                log.info("Not offering %s: a flight is in progress.", names)
        return [a for a in actions if not risky_one(a)]

    # -- pressing a button by name, with no speaking involved ---------------
    def find_named(self, actions: list, wanted: str):
        """The one action called `wanted`, or None.

        A hotkey has to do the same thing every single time, so this is
        deliberately not the fuzzy matcher. Names are compared normalised -
        which is what lets config.toml say "INTERCOM" for a button SLC
        calls "INTERCOM >" - and a name fitting more than one button
        presses nothing at all. Guessing is reasonable when someone is
        speaking and can hear the result; it is not reasonable for a key
        that is standing in for a physical button.
        """
        from .intent import normalise

        target = normalise(wanted, spoken=False)
        if not target:
            return None

        exact = [a for a in actions if normalise(a.name, spoken=False) == target]
        found = exact or [a for a in actions
                          if target in normalise(a.name, spoken=False)]
        if len(found) == 1:
            return found[0]
        if found:
            log.warning("Hotkey name %r matches %d buttons (%s) - pressing "
                        "none of them.", wanted, len(found),
                        ", ".join(repr(a.name) for a in found))
        return None

    def press_named(self, label: str, wanted: tuple[str, ...]) -> None:
        """Press SLC buttons by name, in order, for a hotkey.

        SLC is read again between presses because that is the point of a
        sequence: the first button opens a submenu, and the buttons behind
        it did not exist a moment ago.
        """
        for step, name in enumerate(wanted, start=1):
            started = time.time()
            try:
                actions = self.ui.list_actions()
            except Exception as exc:
                log.error("Hotkey %s: could not read SLC: %s", label, exc)
                return

            if not actions:
                log.warning("Hotkey %s: SLC is offering no buttons right now "
                            "(is it running, and in a flight?).", label)
                return

            # The same guard the spoken path uses. A key bound to something
            # that would end the flight is still something that would end
            # the flight.
            actions = self._without_flight_enders(
                actions, read_flight_context(self.cfg.slc.stream_export_dir))

            action = self.find_named(actions, name)
            if action is None:
                log.warning("Hotkey %s: nothing called %r among %d button(s): %s",
                            label, name, len(actions),
                            ", ".join(a.name for a in actions))
                return

            if self.cfg.behaviour.dry_run:
                log.info("DRY RUN - would press %r  (hotkey %s, step %d/%d)",
                         action.name, label, step, len(wanted))
                continue

            try:
                action.invoke()
                log.info("Pressed %r  (hotkey %s, step %d/%d, %.1fs)",
                         action.name, label, step, len(wanted),
                         time.time() - started)
            except Exception as exc:
                log.error("Hotkey %s: could not press %r: %s",
                          label, action.name, exc)
                return

    def start_hotkeys(self):
        """Arm the configured hotkeys, or return None if there are none."""
        from .hotkeys import Hotkeys

        if not self.cfg.hotkeys:
            return None

        clash = [k for k in self.cfg.hotkeys
                 if k == self.cfg.audio.ptt_key.strip().lower()]
        if clash:
            log.warning("Hotkey %s is also the push-to-talk key, so it will do "
                        "both. Give one of them a different key.",
                        ", ".join(clash))

        listener = Hotkeys(self.cfg.hotkeys, self.press_named)
        listener.start()
        return listener

    def _too_old(self, captured_at: float | None, text: str, stage: str) -> bool:
        """Has this command sat around long enough to be worth dropping?

        Checked more than once. Transcription can take far longer than
        expected when the simulator is starving the GPU, and the cloud can
        take longer still - and pressing a button for something said half a
        minute ago is worse than missing it.
        """
        if captured_at is None:
            return False
        limit = self.cfg.behaviour.max_command_age_seconds
        if not limit:
            return False
        age = time.time() - captured_at
        if age <= limit:
            return False
        log.warning("Ignoring %r - %s left it %.1fs old, past the %.0fs limit.",
                    text, stage, age, limit)
        return True

    def handle(self, audio, captured_at: float | None = None) -> None:
        started = time.time()

        # Reading SLC's UI costs about as long as transcribing, and the two do
        # not depend on each other - the button list is the same whatever the
        # pilot turns out to have said. Usually the scan is already running,
        # started when the key went down; if it is not, start it here and let
        # it run beside transcription as it always did.
        scan, from_prescan = self._claim_scan()

        text, language = self.stt.transcribe(audio)

        if self._too_old(captured_at, text, "transcribing"):
            return

        if not text:
            log.info("Nothing intelligible in that clip.")
            return

        actions = self._actions_for(scan, from_prescan, text)
        if actions is None:
            return

        if not actions:
            log.warning("Heard %r but SLC is offering no buttons right now "
                        "(is it running, and in a flight?).", text)
            return

        flight = read_flight_context(self.cfg.slc.stream_export_dir)
        self._log_flight_state(flight)
        actions = self._without_flight_enders(actions, flight)

        # The whole list, not a sample. When a command does not land, the
        # first question is always whether the button was even on offer -
        # and a truncated list cannot answer it.
        log.info("SLC is offering %d action(s): %s",
                 len(actions), ", ".join(a.name for a in actions))

        decision = self.router.decide(text, actions, format_context(flight))

        if decision.action_index is None:
            log.info("Declined: %s", decision.reasoning)
            return

        action = actions[decision.action_index]

        # Again, because the clock does not stop once the words are decoded.
        # A Gemini call that timed out and retried took one command to 31
        # seconds - transcribed in 0.3s, so the check above waved it through,
        # and SLC was told to start an engine half a minute after the fact.
        if self._too_old(captured_at, text, "deciding"):
            return

        elapsed = time.time() - started

        if self.cfg.behaviour.dry_run:
            log.info("DRY RUN - would press %r  (%.2f confidence, %.1fs, %s)",
                     action.name, decision.confidence, elapsed, language)
            return

        try:
            action.invoke()
            log.info("Pressed %r  (%.2f confidence, %.1fs, %s)",
                     action.name, decision.confidence, elapsed, language)
        except Exception as exc:
            log.error("Could not press %r: %s", action.name, exc)

    def run(self) -> int:
        from .audio import PushToTalk

        ptt = PushToTalk(self.cfg.audio, on_talk_start=self.prescan)
        ptt.start()
        hotkeys = self.start_hotkeys()

        if self.cfg.behaviour.check_for_updates:
            # On a worker, because a slow network must not delay the point at
            # which the key starts working.
            from .update import check
            threading.Thread(target=check, daemon=True).start()

        mode = " [DRY RUN - nothing will be pressed]" if self.cfg.behaviour.dry_run else ""
        log.info("Ready.%s Hold %s and speak. Ctrl+C to quit.",
                 mode, self.cfg.audio.ptt_key)
        if not self.ui.is_running():
            log.warning("SLC is not running yet - start it whenever you like.")

        try:
            for captured_at, clip in ptt.clips():
                try:
                    self.handle(clip, captured_at)
                except Exception:
                    log.exception("Failed to handle an utterance; continuing.")
        except KeyboardInterrupt:
            log.info("Shutting down.")
        finally:
            ptt.stop()
            if hotkeys is not None:
                hotkeys.stop()
        return 0
