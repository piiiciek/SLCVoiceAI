"""Two things a pilot asked for after a flight went wrong.

A transcription that took 52 seconds to decode 1.2 seconds of speech, and
then a command that vanished with nothing on screen connecting the two.
And having to remember to press Start at all.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slcvoiceai import config as config_module  # noqa: E402
from slcvoiceai import gui, stt  # noqa: E402


# -- saying why a command vanished ----------------------------------------

class Bare(stt.Transcriber):
    """A Transcriber with nothing loaded - only the reporting is wanted."""

    def __init__(self, device="cuda"):
        self._device = device


def warnings_from(took, clip_seconds, device="cuda"):
    records = []

    class Catch(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Catch()
    log = logging.getLogger("slcvoiceai.stt")
    log.addHandler(handler)
    try:
        Bare(device)._report_if_stalled(took, int(clip_seconds * 16000))
    finally:
        log.removeHandler(handler)
    return [r for r in records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("took, clip", [
    (52.40, 1.17),    # the one that prompted this, from a real flight
    (92.9, 2.5),      # the worst in the log
    (33.02, 1.5),
])
def test_a_stall_is_explained(took, clip):
    said = warnings_from(took, clip)
    assert said, "{t}s on {c}s of audio passed without comment".format(
        t=took, c=clip)
    message = said[0].getMessage()
    assert "simulator" in message, "it does not name the likely cause"
    assert "cpu" in message, "it does not say what to do about it"


@pytest.mark.parametrize("took, clip", [
    (0.99, 1.2),      # the median across 275 logged transcriptions
    (0.55, 1.1),
    (3.11, 2.8),      # the 90th percentile, which is not a fault
    (7.0, 3.0),       # slow, but a long clip on a busy machine
    (5.0, 0.7),       # under the floor: short clip, unremarkable wait
])
def test_an_ordinary_wait_is_not_cried_over(took, clip):
    """A warning on every slightly slow command is a warning nobody reads,
    and the feed colours warnings - it would be orange most of a flight."""
    assert not warnings_from(took, clip), "{t}s on {c}s complained".format(
        t=took, c=clip)


def test_the_explanation_names_the_device_in_use():
    """Telling someone already on the CPU to switch to the CPU is worse
    than saying nothing.

    This test used to assert only that "cpu" appeared in the message, which
    the broken advice satisfied by containing the words `device = "cpu"` -
    so it passed while the pilot read the nonsense four times in the flight
    of 2026-09-23. What it has to check is the advice, not the word.
    """
    said = warnings_from(52.0, 1.2, device="cpu")[0].getMessage()
    assert 'device = "cpu"' not in said, (
        "it tells someone already on the CPU to move to the CPU")
    assert "listening" in said, "it does not say what would actually help"


def test_the_gpu_explanation_still_offers_the_cpu():
    """The advice that is right on the GPU has to survive the branch."""
    said = warnings_from(52.0, 1.2, device="cuda")[0].getMessage()
    assert 'device = "cpu"' in said


def test_a_stall_is_a_warning_so_the_panel_colours_it():
    assert warnings_from(52.0, 1.2)[0].levelno == logging.WARNING
    assert gui.tag_for(warnings_from(52.0, 1.2)[0].getMessage(),
                       logging.WARNING) == "warn"


# -- starting when SLC does ------------------------------------------------

class FakeUI:
    def __init__(self, running=False):
        self.running = running
        self.process_name = "SLC.exe"

    def is_running(self):
        return self.running


def panel(tmp_path, running=False, **behaviour):
    path = tmp_path / "config.toml"
    path.write_text('[audio]\nptt_key = "scroll_lock"\n', encoding="utf-8")
    cfg = config_module.load(path)
    for name, value in behaviour.items():
        setattr(cfg.behaviour, name, value)
    app = gui.App(cfg)
    app.ui = FakeUI(running)
    app.started = []
    app._start = lambda: app.started.append(True)
    return app


def test_it_starts_when_slc_turns_up(tmp_path):
    app = panel(tmp_path, running=True, start_with_slc=True)
    app._maybe_auto_start()
    assert app.started == [True]


def test_it_waits_while_slc_is_not_there(tmp_path):
    app = panel(tmp_path, running=False, start_with_slc=True)
    app._maybe_auto_start()
    assert app.started == []


def test_it_does_nothing_when_not_asked(tmp_path):
    app = panel(tmp_path, running=True, start_with_slc=False)
    app._maybe_auto_start()
    assert app.started == []


def test_it_does_not_start_twice(tmp_path):
    """SLC's window comes and goes - the launcher collapses, popups open.
    Restarting a running bridge would reload Whisper every time."""
    app = panel(tmp_path, running=True, start_with_slc=True)
    app.bridge = object()
    app._maybe_auto_start()
    assert app.started == []


def test_switching_it_on_with_slc_already_up_starts_now(tmp_path):
    """Nobody ticks this box meaning "next time"."""
    app = panel(tmp_path, running=True)
    app.set_auto_start(True)
    assert app.started == [True]
    assert config_module.load(app.cfg.source).behaviour.start_with_slc is True


def test_switching_it_off_is_remembered(tmp_path):
    app = panel(tmp_path, running=True, start_with_slc=True)
    app.set_auto_start(False)
    assert app.started == []
    assert config_module.load(app.cfg.source).behaviour.start_with_slc is False


def test_the_watcher_fires_on_the_transition_not_on_every_look(tmp_path):
    """SLC being up is not news on the second poll; SLC appearing is."""
    app = panel(tmp_path, running=False, start_with_slc=True)
    app.WATCH_SECONDS = 0.02

    worker = __import__("threading").Thread(target=app._watch_for_slc,
                                            daemon=True)
    worker.start()
    time.sleep(0.1)
    assert app.started == [], "it started before SLC was there"

    app.ui.running = True
    time.sleep(0.2)
    app._closing = True
    worker.join(timeout=2)
    assert app.started == [True], "expected exactly one start, got {n}".format(
        n=len(app.started))


def test_the_watcher_survives_slc_going_away(tmp_path):
    """is_running raises when UI Automation is unavailable; the watcher
    must keep looking rather than die and leave the box ticked but dead."""
    app = panel(tmp_path, running=False, start_with_slc=True)
    app.WATCH_SECONDS = 0.02

    calls = []

    def sometimes_broken():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("UIA is not answering")
        return True

    app.ui.is_running = sometimes_broken
    worker = __import__("threading").Thread(target=app._watch_for_slc,
                                            daemon=True)
    worker.start()
    time.sleep(0.3)
    app._closing = True
    worker.join(timeout=2)
    assert app.started == [True], "it gave up on the first error"


def test_the_setting_reads_back_from_the_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[behaviour]\nstart_with_slc = true\n", encoding="utf-8")
    assert config_module.load(path).behaviour.start_with_slc is True


def test_it_is_off_unless_asked_for():
    """Starting a speech model because a window appeared is not something
    to do to somebody who did not ask."""
    assert config_module.Config().behaviour.start_with_slc is False
