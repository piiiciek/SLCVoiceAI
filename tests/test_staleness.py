"""A command has to be current when it is pressed, not when it was decoded.

The clock does not stop once Whisper is done. A Gemini call that timed out
and retried took one command to 31 seconds: transcribed in 0.3s, so the age
check waved it through, and SLC was told to start an engine half a minute
after the pilot asked.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai import app  # noqa: E402


def bridge(limit=12.0):
    b = app.Bridge.__new__(app.Bridge)
    b.cfg = SimpleNamespace(
        behaviour=SimpleNamespace(max_command_age_seconds=limit))
    return b


def test_a_fresh_command_passes():
    assert bridge()._too_old(time.time(), "roger", "transcribing") is False


def test_an_old_command_is_dropped():
    assert bridge()._too_old(time.time() - 30, "roger", "deciding") is True


def test_the_limit_is_the_boundary():
    b = bridge(limit=10.0)
    assert b._too_old(time.time() - 9.5, "roger", "deciding") is False
    assert b._too_old(time.time() - 10.5, "roger", "deciding") is True


def test_zero_disables_the_check():
    assert bridge(limit=0)._too_old(time.time() - 600, "roger", "x") is False


def test_no_capture_time_means_no_opinion():
    """--dry-run harnesses and the phrase tester hand over a clip with no
    timestamp; they must not start being refused."""
    assert bridge()._too_old(None, "roger", "deciding") is False


def test_the_log_says_which_stage_ran_long(caplog):
    with caplog.at_level("WARNING"):
        bridge()._too_old(time.time() - 31, "start engine 2", "deciding")
    assert "deciding" in caplog.text
    assert "start engine 2" in caplog.text


def test_it_is_checked_after_the_decision_not_only_after_transcription():
    """The regression this exists for: fast transcription, slow routing."""
    source = Path(__file__).resolve().parent.parent / "slcvoiceai" / "app.py"
    handle = source.read_text(encoding="utf-8").split("def handle(")[1]
    before_press = handle.split("action.invoke()")[0]
    assert before_press.count("_too_old(") >= 2, (
        "the age is checked once, so a slow cloud call still lands late")
