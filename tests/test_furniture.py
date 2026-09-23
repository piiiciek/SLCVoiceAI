"""SLC's own furniture is not something the pilot says.

Half of what SLC lays out is panel: a toolbar of icons and a strip of tabs.
None of them carry an accessible name, so the bridge invents one from the
AutomationId - and the inventions read like commands. 'Interactions Crew'
is a tab. 'Interaction Flight Phase' is a tab. 'Tannoy' is a toolbar icon.

Handing those to the matcher alongside the real phrases was not a small
untidiness. Over the logs to 2026-09-23, across 304 fuzzy decisions, an
invented name was the top pick 69 times and the runner-up 162 times, and
92 of the 123 escalations to Gemini were caused by one of them scoring
level with a real command. Thirteen presses landed on furniture.

These tests pin the two halves of the rule: an invented name is refused,
and a name SLC wrote itself is not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slcvoiceai import slc_ui  # noqa: E402


class Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top = left, top
        self.right, self.bottom = right, bottom


class FakeControl:
    """An SLC control. An icon-only one carries no Name at all."""

    def __init__(self, name="", automation_id="", tooltip=""):
        self.Name = name
        self.AutomationId = automation_id
        self.HelpText = tooltip
        self.ControlTypeName = "ButtonControl"
        self.BoundingRectangle = Rect(0, 0, 100, 20)
        self.IsEnabled = True

    def GetParentControl(self):
        return None

    def GetPattern(self, _pattern_id):
        return object()

    def GetChildren(self):
        return []


def offering(monkeypatch, controls, **kwargs):
    window = FakeControl(name="Self-Loading Cargo")
    window.ControlTypeName = "WindowControl"
    ui = slc_ui.SlcUI("SLC.exe")
    monkeypatch.setattr(type(ui), "windows", lambda self: [window])
    monkeypatch.setattr(type(ui), "_walk",
                        lambda self, node, **kw: iter(controls))
    monkeypatch.setattr(slc_ui, "_is_activatable", lambda c: True)
    monkeypatch.setattr(slc_ui, "is_topmost", lambda c: True)
    return [a.name for a in ui.list_actions(**kwargs)]


# -- the rule --------------------------------------------------------------

#: Every furniture control the pilot's logs caught the bridge pressing, or
#: saw it score. Captured from the offer lists of 2026-09-23.
FURNITURE = [
    ("cmdTannoy", "Tannoy"),
    ("cmdInflightServices", "Inflight Services"),
    ("cmdInteractionFlightPhase", "Interaction Flight Phase"),
    ("cmdInteractionsCrew", "Interactions Crew"),
    ("cmdInteractionsGroundCrew", "Interactions Ground Crew"),
    ("cmdInteractionsPassengers", "Interactions Passengers"),
    ("cmdInteractionsMedical", "Interactions Medical"),
    ("cmdInteractionsWarning", "Interactions Warning"),
    ("cmdNotifications", "Notifications"),
    ("cmdAircraftLayout", "Aircraft Layout"),
    ("cmdAvailablePhrasesWindow", "Available Phrases Window"),
    ("cmdNarrationWindow", "Narration Window"),
    ("cmdCheckList", "Check List"),
    ("cmdSettings", "Settings"),
]


@pytest.mark.parametrize("ident,label", FURNITURE)
def test_an_invented_name_is_furniture(ident, label):
    """humanise_id produced the label, so the label is ours, not SLC's."""
    assert slc_ui.humanise_id(ident) == label, "the test's own premise"
    assert slc_ui.is_chrome(label, ident)


#: The phrases, exactly as SLC labels them. These are the point of the
#: program and none of them may be touched by the rule.
SPEECH = [
    ("cmdPlayCaptainCockpitToGround", "GROUND CREW >"),
    ("cmdPlayCaptainGoAhead", "GO AHEAD"),
    ("cmdPlayCaptainStandby", "STANDBY"),
    ("cmdPlayCaptainRoger", "ROGER"),
    ("cmdPlayCaptainWelcome", "LADIES AND GENTLEMEN"),
    ("cmdPlayCaptainPa", "P A SYSTEM >"),
    ("cmdTannoySwitchBackGroundCrew", "BACK"),
    ("cmdPlayCaptainGsxBoarding", "GSX, START BOARDING"),
]


@pytest.mark.parametrize("ident,label", SPEECH)
def test_a_name_slc_wrote_is_never_furniture(ident, label):
    assert slc_ui.is_chrome(label, ident) is False


@pytest.mark.parametrize("ident,label", [
    ("cmdSeatbelts", "Seatbelts"),
    ("cmdToggleDoors", "Toggle Doors"),
    ("cmdToggleDoorMode", "Toggle Door Mode"),
])
def test_the_three_icons_that_change_the_aircraft_survive(ident, label):
    """These are icon-only too, and their names are just as invented - but
    they move doors and signs rather than panels, and the pilot has used
    all three by voice. 'Seatbelts' went in at 1.00 on 00:15:23."""
    assert slc_ui.humanise_id(ident) == label, "the test's own premise"
    assert slc_ui.is_chrome(label, ident) is False


def test_nothing_is_furniture_without_an_id():
    """A control with no AutomationId cannot have had its name invented."""
    assert slc_ui.is_chrome("ROGER", "") is False
    assert slc_ui.is_chrome("", "cmdTannoy") is False


# -- through list_actions --------------------------------------------------

def test_the_offer_carries_speech_only(monkeypatch):
    """The real incident: at 12:01:06 SLC offered 32 controls, 18 of them
    furniture, and Gemini answered 'Interactions Crew' to "start boarding"."""
    controls = [FakeControl(name=label) for _, label in SPEECH]
    controls += [FakeControl(automation_id=ident) for ident, _ in FURNITURE]

    offered = offering(monkeypatch, controls)

    assert offered == [label for _, label in SPEECH]
    assert "Interactions Crew" not in offered


def test_exploring_still_sees_the_whole_panel(monkeypatch):
    """--list-actions has to agree with the screen, or it stops being a
    diagnostic."""
    controls = [FakeControl(automation_id=ident) for ident, _ in FURNITURE]
    assert offering(monkeypatch, controls, include_chrome=True) == [
        label for _, label in FURNITURE]
    assert offering(monkeypatch, controls) == []


def test_the_toggles_are_offered_among_the_speech(monkeypatch):
    controls = [
        FakeControl(name="GO AHEAD", automation_id="cmdPlayCaptainGoAhead"),
        FakeControl(automation_id="cmdSeatbelts"),
        FakeControl(automation_id="cmdTannoy"),
    ]
    assert offering(monkeypatch, controls) == ["GO AHEAD", "Seatbelts"]


def test_audio_manager_is_refused(monkeypatch):
    """It has a real name, in capitals, so the furniture rule cannot see it -
    but all it does is open the window WINDOW_DENYLIST already refuses."""
    assert slc_ui.is_denied("AUDIO MANAGER")
    controls = [FakeControl(name="AUDIO MANAGER", automation_id="cmdAudioManager"),
                FakeControl(name="ROGER", automation_id="cmdPlayCaptainRoger")]
    assert offering(monkeypatch, controls) == ["ROGER"]
