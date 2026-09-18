"""Finding SLC's windows.

Asking UI Automation for the desktop's children and reading a process id off
each cost about half a second on every command. Win32 answers the same
question in ten milliseconds - but only if it answers it identically, because
a window missed here is a set of commands that quietly stop working, and a
window found here that is not really on screen is buttons offered that the
pilot cannot see.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai import slc_ui  # noqa: E402
from slcvoiceai.slc_ui import UIAUnavailable  # noqa: E402


# -- picking the process's windows ----------------------------------------

def test_only_the_named_process_is_kept(monkeypatch):
    listing = [(11, 100), (22, 200), (33, 100), (44, 300)]
    names = {100: "SLC.exe", 200: "chrome.exe", 300: "explorer.exe"}
    monkeypatch.setattr(slc_ui, "_process_name", lambda pid: names[pid])
    assert slc_ui._handles_of_process("SLC.exe", listing) == [11, 33]


def test_the_process_name_is_matched_case_insensitively(monkeypatch):
    monkeypatch.setattr(slc_ui, "_process_name", lambda pid: "SLC.EXE")
    assert slc_ui._handles_of_process("slc.exe", [(11, 100)]) == [11]


def test_window_order_is_preserved(monkeypatch):
    """EnumWindows hands them back in z-order, topmost first. Keeping that
    order means the window the pilot is looking at is scanned first."""
    monkeypatch.setattr(slc_ui, "_process_name", lambda pid: "SLC.exe")
    listing = [(9, 1), (8, 1), (7, 1)]
    assert slc_ui._handles_of_process("SLC.exe", listing) == [9, 8, 7]


def test_each_process_is_looked_up_once(monkeypatch):
    """A process handle per window would undo the saving on a process with
    several windows open - SLC has up to four."""
    calls = []

    def counting(pid):
        calls.append(pid)
        return "SLC.exe"

    monkeypatch.setattr(slc_ui, "_process_name", counting)
    listing = [(11, 100), (22, 100), (33, 100), (44, 200)]
    slc_ui._handles_of_process("SLC.exe", listing)
    assert sorted(calls) == [100, 200]


def test_nothing_on_the_desktop_means_no_windows(monkeypatch):
    monkeypatch.setattr(slc_ui, "_process_name", lambda pid: "other.exe")
    assert slc_ui._handles_of_process("SLC.exe", []) == []


def test_a_process_that_cannot_be_named_is_skipped(monkeypatch):
    """_process_name returns '' when the process cannot be opened."""
    monkeypatch.setattr(slc_ui, "_process_name", lambda pid: "")
    assert slc_ui._handles_of_process("SLC.exe", [(11, 100)]) == []


# -- the lookup as a whole ------------------------------------------------

class FakeControl:
    def __init__(self, handle):
        self.handle = handle


def use_handles(monkeypatch, handles, controls=None):
    monkeypatch.setattr(slc_ui, "_handles_of_process", lambda name: handles)
    made = controls if controls is not None else {h: FakeControl(h) for h in handles}
    monkeypatch.setattr(slc_ui.auto, "ControlFromHandle", lambda h: made.get(h))


def test_the_cheap_lookup_is_used_when_it_finds_something(monkeypatch):
    use_handles(monkeypatch, [11, 22])
    called = []
    monkeypatch.setattr(slc_ui.SlcUI, "_windows_via_uia",
                        lambda self: called.append(1) or [])
    found = slc_ui.SlcUI("SLC.exe").windows()
    assert [c.handle for c in found] == [11, 22]
    assert not called, "the slow lookup ran even though the fast one worked"


def test_a_handle_uia_will_not_open_is_dropped(monkeypatch):
    use_handles(monkeypatch, [11, 22], controls={11: FakeControl(11), 22: None})
    found = slc_ui.SlcUI("SLC.exe").windows()
    assert [c.handle for c in found] == [11]


def test_finding_nothing_falls_back_to_the_slow_lookup(monkeypatch):
    """The dangerous case. An empty result reads downstream as 'SLC is
    offering no buttons', which is how commands used to vanish - so being
    sure is worth half a second on a path that is already lost."""
    use_handles(monkeypatch, [])
    sentinel = [FakeControl(99)]
    monkeypatch.setattr(slc_ui.SlcUI, "_windows_via_uia", lambda self: sentinel)
    assert slc_ui.SlcUI("SLC.exe").windows() is sentinel


def test_slc_really_not_running_still_reports_nothing(monkeypatch):
    use_handles(monkeypatch, [])
    monkeypatch.setattr(slc_ui.SlcUI, "_windows_via_uia", lambda self: [])
    ui = slc_ui.SlcUI("SLC.exe")
    assert ui.windows() == []
    assert ui.is_running() is False


def test_a_transient_failure_is_retried(monkeypatch):
    monkeypatch.setattr(slc_ui.time, "sleep", lambda _s: None)
    attempts = []

    def flaky(name):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("EVENT_E_ALL_SUBSCRIBERS_FAILED")
        return [11]

    monkeypatch.setattr(slc_ui, "_handles_of_process", flaky)
    monkeypatch.setattr(slc_ui.auto, "ControlFromHandle", FakeControl)
    found = slc_ui.SlcUI("SLC.exe").windows()
    assert [c.handle for c in found] == [11]
    assert len(attempts) == 2


def test_failing_every_attempt_raises_rather_than_returning_empty(monkeypatch):
    """A failed read and 'SLC is offering nothing' are different answers,
    and confusing them is what used to lose commands in silence."""
    monkeypatch.setattr(slc_ui.time, "sleep", lambda _s: None)

    def always_bad(name):
        raise OSError("COM said no")

    monkeypatch.setattr(slc_ui, "_handles_of_process", always_bad)
    with pytest.raises(UIAUnavailable):
        slc_ui.SlcUI("SLC.exe").windows(attempts=2)


def test_is_running_reports_false_rather_than_raising(monkeypatch):
    monkeypatch.setattr(slc_ui.time, "sleep", lambda _s: None)

    def always_bad(name):
        raise OSError("COM said no")

    monkeypatch.setattr(slc_ui, "_handles_of_process", always_bad)
    assert slc_ui.SlcUI("SLC.exe").is_running() is False


# -- windows the bridge must not take commands from -----------------------

def test_the_exit_confirmation_is_never_a_source_of_commands():
    """SLC asks 'Exit Self-Loading Cargo?' before quitting, and that dialog's
    buttons are named in the Windows display language - 'Tak' and 'Nie' on a
    Polish install, 'Yes' and 'No' on an English one. No button denylist can
    cover every language, so the window has to be refused as a whole."""
    assert slc_ui.is_denied_window("Exit Self-Loading Cargo?")
    assert slc_ui.is_denied_window("exit self-loading cargo?")


def test_the_configuration_windows_are_still_refused():
    for title in ("Settings Window", "Audio Manager",
                  "Voice Recognition Prompt Window"):
        assert slc_ui.is_denied_window(title), title


def test_the_flight_windows_are_not_refused():
    """The denylist is a denylist on purpose: SLC puts real, voice-worthy
    controls in satellite windows, and an allowlist would cut them off."""
    for title in ("Scoring CheckList Window", "Self-Loading Cargo",
                  "Tannoy", "Cabin Management"):
        assert not slc_ui.is_denied_window(title), title


def test_an_untitled_window_is_not_refused():
    assert not slc_ui.is_denied_window("")
    assert not slc_ui.is_denied_window(None)


# -- controls whose name is our invention, not SLC's ----------------------

class FakeControl2:
    """A UIA control, as much of one as slc_ui actually touches."""

    def __init__(self, name="", automation_id="", tooltip="", enabled=True):
        self.Name = name
        self.AutomationId = automation_id
        self.HelpText = tooltip
        self.IsEnabled = enabled
        self.ControlTypeName = "ButtonControl"


def test_a_named_control_keeps_its_own_name():
    assert slc_ui.label_for(FakeControl2(name="STANDBY")) == "STANDBY"


def test_an_icon_only_control_gets_a_name_invented_from_its_id():
    assert slc_ui.label_for(
        FakeControl2(automation_id="cmdToggleDoorMode")) == "Toggle Door Mode"


def test_the_tooltip_is_read():
    assert slc_ui.help_text(
        FakeControl2(tooltip=" Toggle Seatbelts ")) == "Toggle Seatbelts"


def test_a_control_that_cannot_be_read_yields_nothing():
    class Stale:
        @property
        def Name(self):
            raise OSError("element is stale")

        @property
        def HelpText(self):
            raise OSError("element is stale")

        @property
        def AutomationId(self):
            raise OSError("element is stale")

    assert slc_ui.label_for(Stale()) == ""
    assert slc_ui.help_text(Stale()) == ""


def test_the_button_that_closes_slc_is_refused_by_its_tooltip():
    """cmdStandBy has no accessible name, so humanise_id calls it 'Stand By'
    - which nothing in DENYLIST catches, and which the pilot's "5 by 5"
    pressed in flight. SLC's own tooltip says what it really is."""
    control = FakeControl2(automation_id="cmdStandBy",
                           tooltip="Close SLC or Restart Flight")
    assert slc_ui.label_for(control) == "Stand By"
    assert not slc_ui.is_denied(slc_ui.label_for(control)), (
        "if the name alone were enough, the tooltip check would be pointless")
    assert slc_ui.is_denied(slc_ui.help_text(control))


def test_the_radio_standby_is_not_caught_with_it():
    """The comms button really is called STANDBY and really does mean
    'wait' - it has to survive, or answering a ground crew call stops
    working. It carries no tooltip at all."""
    control = FakeControl2(name="STANDBY", automation_id="cmdPlayCaptainStandby")
    assert not slc_ui.is_denied(slc_ui.label_for(control))
    assert not slc_ui.is_denied(slc_ui.help_text(control))


def test_the_toolbar_buttons_that_matter_survive_their_tooltips():
    """Every icon on SLC's toolbar has a tooltip; only one of them is a way
    out of the flight. Captured from SLC v1.6.7.3."""
    harmless = [
        ("cmdNotifications", "Show Current Flight Information"),
        ("cmdSeatbelts", "Toggle Seatbelts"),
        ("cmdInflightServices", "Toggle Cabin Management Window"),
        ("cmdAircraftLayout", "Open Aircraft Layout"),
        ("cmdAvailablePhrasesWindow", "Open Speech Assistance Window"),
        ("cmdNarrationWindow", "Open Narration Window"),
        ("cmdCheckList", "Open Scoring Checklist"),
        ("cmdToggleDoors", "Toggle Aircraft Doors"),
        ("cmdTannoy", "Address The Cabin"),
        ("cmdToggleDoorMode", "Toggle Gate Mode to 'Stairs' (Currently Jetway)"),
    ]
    for ident, tooltip in harmless:
        control = FakeControl2(automation_id=ident, tooltip=tooltip)
        assert not slc_ui.is_denied(slc_ui.help_text(control)), ident
        assert not slc_ui.is_denied(slc_ui.label_for(control)), ident


def test_an_empty_tooltip_denies_nothing():
    """is_denied('') must not match, or every comms button would vanish."""
    assert not slc_ui.is_denied("")
