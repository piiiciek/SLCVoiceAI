"""Wire the pieces together: hold a key, speak, SLC acts."""

from __future__ import annotations

import logging
import sys
import time

from .config import Config
from .context import format_context, read_flight_context
from .intent import IntentRouter
from .slc_ui import SlcUI

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
        self.router = IntentRouter(cfg.llm, cfg.api_key)
        # Imported lazily: loading Whisper takes a while and pulls in CUDA.
        from .stt import Transcriber
        self.stt = Transcriber(cfg.stt)

    def handle(self, audio) -> None:
        started = time.time()

        text, language = self.stt.transcribe(audio)
        if not text:
            log.info("Nothing intelligible in that clip.")
            return

        if not self.ui.is_running():
            log.warning("Heard %r but SLC is not running.", text)
            return

        actions = self.ui.list_actions()
        if not actions:
            log.warning("Heard %r but SLC is offering no buttons right now.", text)
            return
        log.info("SLC is offering %d action(s): %s",
                 len(actions), ", ".join(a.name for a in actions[:12]))

        context = format_context(read_flight_context(self.cfg.slc.stream_export_dir))
        decision = self.router.decide(text, actions, context)

        if decision.action_index is None:
            log.info("Declined: %s", decision.reasoning)
            return

        if decision.confidence < self.cfg.behaviour.min_confidence:
            log.info("Below confidence floor (%.2f < %.2f), ignoring: %s",
                     decision.confidence, self.cfg.behaviour.min_confidence,
                     decision.reasoning)
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
            for clip in ptt.clips():
                try:
                    self.handle(clip)
                except Exception:
                    log.exception("Failed to handle an utterance; continuing.")
        except KeyboardInterrupt:
            log.info("Shutting down.")
        finally:
            ptt.stop()
        return 0
