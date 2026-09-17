"""Wire the pieces together: hold a key, speak, SLC acts."""

from __future__ import annotations

import logging
import sys
import threading
import time

from .config import Config
from .context import format_context, read_flight_context
from .intent import build_router
from .slc_ui import SlcUI, UIAUnavailable

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

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

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
        # Imported lazily: loading Whisper takes a while and pulls in CUDA.
        from .stt import Transcriber
        self.stt = Transcriber(cfg.stt)

    def handle(self, audio, captured_at: float | None = None) -> None:
        started = time.time()

        # Reading SLC's UI costs about as long as transcribing, and the two do
        # not depend on each other - the button list is the same whatever the
        # pilot turns out to have said. Run them together and the command
        # takes as long as the slower one instead of both in turn.
        scan = _Scan(self.ui)
        scan.start()

        text, language = self.stt.transcribe(audio)

        # Transcription can take far longer than expected when the simulator
        # is starving the GPU. A command that old no longer reflects what the
        # pilot wants pressed, so drop it rather than fire it late.
        if captured_at is not None:
            age = time.time() - captured_at
            limit = self.cfg.behaviour.max_command_age_seconds
            if limit and age > limit:
                log.warning("Ignoring %r - it took %.1fs to transcribe, older "
                            "than the %.0fs limit", text, age, limit)
                return

        if not text:
            log.info("Nothing intelligible in that clip.")
            return

        try:
            actions = scan.result()
        except UIAUnavailable as exc:
            log.error("Heard %r but %s - command dropped, please say it again.",
                      text, exc)
            return

        if not actions:
            log.warning("Heard %r but SLC is offering no buttons right now "
                        "(is it running, and in a flight?).", text)
            return
        # The whole list, not a sample. When a command does not land, the
        # first question is always whether the button was even on offer -
        # and a truncated list cannot answer it.
        log.info("SLC is offering %d action(s): %s",
                 len(actions), ", ".join(a.name for a in actions))

        context = format_context(read_flight_context(self.cfg.slc.stream_export_dir))
        decision = self.router.decide(text, actions, context)

        if decision.action_index is None:
            log.info("Declined: %s", decision.reasoning)
            return

        action = actions[decision.action_index]
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

        ptt = PushToTalk(self.cfg.audio)
        ptt.start()

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
        return 0
