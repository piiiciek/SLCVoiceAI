"""Wire the pieces together: hold a key, speak, SLC acts."""

from __future__ import annotations

import logging
import sys
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
            actions = self.ui.list_actions()
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
