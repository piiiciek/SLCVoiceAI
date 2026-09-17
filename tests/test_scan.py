"""The UI scan runs beside transcription, so it must behave exactly as if it
had not - same results, same errors, no silent empties."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai.app import _Scan  # noqa: E402
from slcvoiceai.slc_ui import UIAUnavailable  # noqa: E402


class FakeUI:
    def __init__(self, actions=None, error=None, delay=0.0):
        self.actions = actions if actions is not None else ["a", "b"]
        self.error = error
        self.delay = delay
        self.calls = 0

    def list_actions(self):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.actions


def test_returns_what_the_scan_found():
    scan = _Scan(FakeUI(["GROUND CREW >", "ROGER"]))
    scan.start()
    assert scan.result() == ["GROUND CREW >", "ROGER"]


def test_runs_while_the_caller_is_busy():
    """The whole point: the caller transcribes while this runs."""
    ui = FakeUI(delay=0.3)
    scan = _Scan(ui)
    started = time.time()
    scan.start()
    time.sleep(0.3)          # stands in for transcription
    scan.result()
    assert time.time() - started < 0.55, "the two did not overlap"


def test_a_failed_scan_still_raises():
    """A scan that failed must not look like SLC offering nothing - that
    distinction is what stopped commands vanishing behind a misleading
    'no buttons right now'."""
    scan = _Scan(FakeUI(error=UIAUnavailable("COM said no")))
    scan.start()
    with pytest.raises(UIAUnavailable):
        scan.result()


def test_an_unexpected_error_is_not_swallowed():
    scan = _Scan(FakeUI(error=ValueError("something else entirely")))
    scan.start()
    with pytest.raises(ValueError):
        scan.result()


def test_a_hung_scan_times_out_rather_than_blocking_the_flight():
    scan = _Scan(FakeUI(delay=5.0))
    scan.start()
    with pytest.raises(UIAUnavailable):
        scan.result(timeout=0.2)


def test_the_scan_happens_once():
    ui = FakeUI()
    scan = _Scan(ui)
    scan.start()
    scan.result()
    scan.result()
    assert ui.calls == 1
