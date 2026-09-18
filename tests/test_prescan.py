"""The scan now starts when the key goes down, not when the clip arrives.

Reading SLC takes about as long as a short sentence, so running it during the
utterance hides it behind the pilot's own speech. That only holds if the scan
really is started once per press, reused once, and quietly re-done when it is
too old or when it failed while nobody was waiting on it.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai import app  # noqa: E402
from slcvoiceai.slc_ui import UIAUnavailable  # noqa: E402


class FakeUI:
    def __init__(self, actions=None, errors=()):
        self.actions = actions if actions is not None else ["BOARDING"]
        #: One entry per call: an exception to raise, or None to succeed.
        self.errors = list(errors)
        self.calls = 0

    def list_actions(self):
        self.calls += 1
        if self.errors:
            err = self.errors.pop(0)
            if err is not None:
                raise err
        return self.actions


def make_bridge(ui, prescan=True, max_scan_age=30.0):
    """A Bridge without its constructor, which loads Whisper and CUDA."""
    bridge = app.Bridge.__new__(app.Bridge)
    bridge.ui = ui
    bridge.cfg = SimpleNamespace(behaviour=SimpleNamespace(
        prescan=prescan, max_scan_age_seconds=max_scan_age))
    bridge._pending = None
    bridge._pending_lock = threading.Lock()
    return bridge


# -- starting with the key ------------------------------------------------

def test_the_key_press_starts_the_scan():
    ui = FakeUI()
    bridge = make_bridge(ui)
    bridge.prescan()
    bridge._pending.result()
    assert ui.calls == 1, "the scan should have started without a clip"


def test_the_utterance_reuses_that_scan_instead_of_starting_another():
    ui = FakeUI()
    bridge = make_bridge(ui)
    bridge.prescan()
    scan, from_prescan = bridge._claim_scan()
    assert from_prescan
    assert scan.result() == ["BOARDING"]
    assert ui.calls == 1, "SLC was read twice for one command"


def test_without_a_key_press_the_scan_still_happens():
    """The old path has to keep working: --dry-run harnesses, the GUI's
    phrase tester, anything that hands a clip straight to the bridge."""
    ui = FakeUI()
    bridge = make_bridge(ui)
    scan, from_prescan = bridge._claim_scan()
    assert not from_prescan
    assert scan.result() == ["BOARDING"]
    assert ui.calls == 1


def test_a_scan_is_claimed_only_once():
    ui = FakeUI()
    bridge = make_bridge(ui)
    bridge.prescan()
    first, _ = bridge._claim_scan()
    first.result()
    second, from_prescan = bridge._claim_scan()
    second.result()
    assert not from_prescan, "the same scan was handed out twice"
    assert ui.calls == 2


def test_the_newest_press_wins():
    """Clips can be discarded when transcription falls behind; the button
    list that matters is the one from the press being handled."""
    ui = FakeUI()
    bridge = make_bridge(ui)
    bridge.prescan()
    first = bridge._pending
    bridge.prescan()
    scan, _ = bridge._claim_scan()
    assert scan is not first


def test_it_can_be_switched_off():
    ui = FakeUI()
    bridge = make_bridge(ui, prescan=False)
    bridge.prescan()
    assert bridge._pending is None
    assert ui.calls == 0
    _, from_prescan = bridge._claim_scan()
    assert not from_prescan


# -- staleness ------------------------------------------------------------

def test_a_stale_scan_is_thrown_away_and_slc_read_again():
    ui = FakeUI()
    bridge = make_bridge(ui, max_scan_age=5.0)
    bridge.prescan()
    bridge._pending.result()
    bridge._pending.started_at = time.time() - 60
    scan, from_prescan = bridge._claim_scan()
    scan.result()
    assert not from_prescan
    assert ui.calls == 2


def test_a_zero_limit_means_no_staleness_check():
    ui = FakeUI()
    bridge = make_bridge(ui, max_scan_age=0)
    bridge.prescan()
    bridge._pending.started_at = time.time() - 3600
    _, from_prescan = bridge._claim_scan()
    assert from_prescan


def test_age_grows_from_the_press_not_the_clip():
    scan = app._Scan(FakeUI())
    assert scan.age() == 0.0, "an unstarted scan has no age"
    scan.start()
    scan.started_at = time.time() - 2
    assert 1.9 < scan.age() < 2.5


# -- failure --------------------------------------------------------------

def test_a_prescan_that_failed_is_tried_again():
    """It failed seconds ago, while nobody was waiting on it. Dropping the
    command over that would be worse than paying for one more read."""
    ui = FakeUI(errors=[UIAUnavailable("COM said no"), None])
    bridge = make_bridge(ui)
    bridge.prescan()
    scan, from_prescan = bridge._claim_scan()
    assert bridge._actions_for(scan, from_prescan, "open the doors") == ["BOARDING"]
    assert ui.calls == 2


def test_a_scan_that_just_failed_is_not_retried():
    """A fresh scan already retries inside windows(); trying again here just
    adds delay before telling the pilot to repeat themselves."""
    ui = FakeUI(errors=[UIAUnavailable("COM said no")])
    bridge = make_bridge(ui)
    scan, from_prescan = bridge._claim_scan()
    assert bridge._actions_for(scan, from_prescan, "open the doors") is None
    assert ui.calls == 1


def test_both_attempts_failing_drops_the_command():
    ui = FakeUI(errors=[UIAUnavailable("first"), UIAUnavailable("second")])
    bridge = make_bridge(ui)
    bridge.prescan()
    scan, from_prescan = bridge._claim_scan()
    assert bridge._actions_for(scan, from_prescan, "open the doors") is None


def test_an_empty_slc_is_an_answer_not_a_failure():
    """No buttons means SLC is offering nothing - that must not look like a
    failed read and must not trigger a retry."""
    ui = FakeUI(actions=[])
    bridge = make_bridge(ui)
    bridge.prescan()
    scan, from_prescan = bridge._claim_scan()
    assert bridge._actions_for(scan, from_prescan, "open the doors") == []
    assert ui.calls == 1


# -- the key itself -------------------------------------------------------

def test_holding_the_key_starts_exactly_one_scan():
    """Windows repeats a held key. Firing the hook per repeat would spawn a
    scan thread every few milliseconds for as long as the pilot speaks."""
    from slcvoiceai import audio

    ptt = audio.PushToTalk.__new__(audio.PushToTalk)
    ptt.key = "K"
    ptt._held = threading.Event()
    starts = []
    ptt._on_talk_start = lambda: starts.append(1)

    for _ in range(30):          # auto-repeat while the key is held
        ptt._on_press("K")
    assert starts == [1]

    ptt._on_release("K")
    ptt._on_press("K")
    assert starts == [1, 1], "a second press should scan again"


def test_another_key_is_ignored():
    from slcvoiceai import audio

    ptt = audio.PushToTalk.__new__(audio.PushToTalk)
    ptt.key = "K"
    ptt._held = threading.Event()
    starts = []
    ptt._on_talk_start = lambda: starts.append(1)

    ptt._on_press("J")
    assert starts == []
    assert not ptt._held.is_set()


def test_a_broken_hook_does_not_stop_push_to_talk():
    """The hook runs on pynput's listener thread. An exception escaping it
    takes the key down with it for the rest of the flight, and the hook is
    only ever an optimisation."""
    from slcvoiceai import audio

    ptt = audio.PushToTalk.__new__(audio.PushToTalk)
    ptt.key = "K"
    ptt._held = threading.Event()

    def boom():
        raise RuntimeError("no SLC today")

    ptt._on_talk_start = boom
    ptt._on_press("K")
    assert ptt._held.is_set(), "recording must still have started"
