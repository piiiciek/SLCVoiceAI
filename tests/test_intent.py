"""Regression tests for the offline fuzzy intent router.

Runs without SLC: the button names below are a real capture from SLC v1.6.7.3
with the communications popup open.

    python -m pytest tests/ -q
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai.intent import FuzzyRouter, normalise  # noqa: E402

#: Captured from a running SLC, Ground Crew menu open.
SLC_BUTTONS = [
    "Main Button", "Notifications", "Toggle Door Mode", "Seatbelts",
    "Inflight Services", "Aircraft Layout", "Available Phrases Window",
    "Narration Window", "Check List", "Settings", "Stand By", "Toggle Doors",
    "Tannoy", "Start New Passenger Flight", "Start New Simple Passenger Flight",
    "AUDIO MANAGER", "GROUND CREW >", "INTERCOM >", "P A SYSTEM >", "PHONE >",
    "BACK",
]

#: The shipped default. Chosen by sweeping thresholds against the two sets
#: below: 0.75-0.85 separates them cleanly, 0.80 sits in the middle.
THRESHOLD = 0.80

#: Whisper's `translate` output for Polish commands, and the button meant.
COMMANDS = [
    ("connect me with the ground crew", "GROUND CREW >"),
    ("give me ground crew", "GROUND CREW >"),
    ("intercom", "INTERCOM >"),
    ("public address system", "P A SYSTEM >"),
    ("open available phrases", "Available Phrases Window"),
    ("seatbelts", "Seatbelts"),
    ("back", "BACK"),
    ("show the aircraft layout", "Aircraft Layout"),
    ("open the checklist", "Check List"),
    ("door mode", "Toggle Door Mode"),
    ("notifications", "Notifications"),
    ("stand by", "Stand By"),
]

#: Things a pilot says that are NOT instructions to SLC. Fuzzy matching always
#: finds a nearest neighbour, so these are the cases that actually matter:
#: "tower london zero two" scored 0.60 against PHONE > before the threshold
#: was raised.
NOT_COMMANDS = [
    "tower london zero two",
    "what is the weather in krakow today",
    "gear up flaps five",
    "runway two seven right cleared for takeoff",
    "i think we should get some coffee later",
    "lufthansa one two three heavy",
    "descend and maintain flight level one two zero",
]


@dataclass
class FakeAction:
    """Stands in for slc_ui.Action; the router only reads .name."""

    name: str
    window: str = "Self-Loading Cargo"


@pytest.fixture(scope="module")
def actions() -> list[FakeAction]:
    return [FakeAction(n) for n in SLC_BUTTONS]


@pytest.fixture(scope="module")
def router() -> FuzzyRouter:
    return FuzzyRouter(THRESHOLD)


@pytest.mark.parametrize("said,expected", COMMANDS)
def test_commands_route_correctly(router, actions, said, expected):
    decision = router.decide(said, actions)
    assert decision.action_index is not None, "declined a real command: " + said
    assert decision.confidence >= THRESHOLD, (
        "{said!r} scored {c:.2f}, below the {t} floor".format(
            said=said, c=decision.confidence, t=THRESHOLD)
    )
    assert actions[decision.action_index].name == expected


@pytest.mark.parametrize("said", NOT_COMMANDS)
def test_chatter_is_rejected(router, actions, said):
    """The dangerous direction: firing a cabin command on ATC chatter."""
    decision = router.decide(said, actions)
    fired = decision.action_index is not None and decision.confidence >= THRESHOLD
    assert not fired, (
        "would have pressed {name!r} on {said!r} ({c:.2f})".format(
            name=actions[decision.action_index].name, said=said,
            c=decision.confidence)
    )


def test_empty_action_list_declines(router):
    assert router.decide("intercom", []).action_index is None


def test_normalise_strips_button_decoration():
    assert normalise("GROUND CREW >") == "ground crew"
    assert normalise("CABIN CREW ARE CALLING...") == "cabin crew calling"
    assert normalise("Please take your seats") == "take seats"
