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
#: below: with token_set_ratio alone, 0.50-0.70 separates them cleanly.
#: 0.65 sits inside that band with margin on both sides.
THRESHOLD = 0.65

#: Whisper's `translate` output for Polish commands, and the button meant.
COMMANDS = [
    # Real Whisper `translate` output for "polacz mnie z obsluga naziemna".
    # Nothing like the tidy phrase that was originally assumed, which is why
    # the first threshold was tuned too high and rejected a correct match.
    ("Let's start with the ground operation.", "GROUND CREW >"),
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


#: Intent carried by words that share no spelling with the button. This is
#: what the alias table exists for - "understood" against "ROGER" scores
#: essentially zero on string similarity alone.
SYNONYMS = [
    ("I understand", "ROGER"),
    ("Understood", "ROGER"),
    ("copy that", "ROGER"),
    ("acknowledged", "ROGER"),
    ("say again", "REPEAT TRANSMISSION"),
    ("never mind", "DISREGARD"),
    ("we are ready to push", "READY FOR PUSHBACK"),
    ("thanks a lot", "THANK YOU"),
]

#: Buttons SLC shows once a channel is open, needed for the synonym cases.
COMMS_BUTTONS = SLC_BUTTONS + [
    "ROGER", "GO AHEAD", "DISREGARD", "THANK YOU", "READY FOR PUSHBACK",
    "REPEAT TRANSMISSION", "LOUD AND CLEAR",
]


@pytest.fixture(scope="module")
def comms_actions() -> list["FakeAction"]:
    return [FakeAction(n) for n in COMMS_BUTTONS]


@pytest.mark.parametrize("said,expected", SYNONYMS)
def test_synonyms_route_through_aliases(router, comms_actions, said, expected):
    decision = router.decide(said, comms_actions)
    assert decision.action_index is not None, "declined a synonym: " + said
    assert decision.confidence >= THRESHOLD
    assert comms_actions[decision.action_index].name == expected


@pytest.mark.parametrize("said", NOT_COMMANDS)
def test_chatter_still_rejected_with_aliases(router, comms_actions, said):
    """Aliases widen the matching surface - make sure they did not widen it
    onto radio chatter. "what can i say" reduces to "what say", which matched
    "what is the weather in krakow today" until alias coverage was required."""
    decision = router.decide(said, comms_actions)
    fired = decision.action_index is not None and decision.confidence >= THRESHOLD
    assert not fired, "would have pressed {n!r} on {s!r}".format(
        n=comms_actions[decision.action_index].name, s=said)


#: Buttons that are exact opposites of each other. These share nearly every
#: letter, so a string matcher rates them near-identical - the most dangerous
#: confusion in the whole set.
OPPOSITE_BUTTONS = [
    "CONNECT JETWAY", "DISCONNECT JETWAY",
    "CONNECT STAIRS", "DISCONNECT STAIRS",
    "OPEN THE DOORS", "PLEASE CLOSE THE DOORS",
    "GSX, START CATERING", "GSX, START BOARDING",
    "ROGER", "GO AHEAD", "BACK",
]

OPPOSITES = [
    ("Disconnect Jetway.", "DISCONNECT JETWAY"),
    ("Connect Jetway.", "CONNECT JETWAY"),
    ("attach the jetway", "CONNECT JETWAY"),
    ("remove the jetway", "DISCONNECT JETWAY"),
    ("Disconnect the stairs", "DISCONNECT STAIRS"),
    ("bring the stairs", "CONNECT STAIRS"),
    ("open the doors", "OPEN THE DOORS"),
    ("close the doors", "PLEASE CLOSE THE DOORS"),
]


@pytest.fixture(scope="module")
def opposite_actions() -> list["FakeAction"]:
    return [FakeAction(n) for n in OPPOSITE_BUTTONS]


@pytest.mark.parametrize("said,expected", OPPOSITES)
def test_opposites_are_not_confused(router, opposite_actions, said, expected):
    """connect/disconnect must never be mistaken for one another.

    Two bugs made them indistinguishable: string similarity rates them
    near-identical, and aliases_for matched keys as raw substrings, so
    "connect jetway" (a literal substring of "disconnect jetway") handed
    every connect alias to the disconnect button as well.
    """
    decision = router.decide(said, opposite_actions)
    assert decision.action_index is not None, "declined an opposite: " + said
    assert opposite_actions[decision.action_index].name == expected


def test_decisive_margin_accepts_a_clear_but_low_score(router, opposite_actions):
    """A decisive win stands in for a high score.

    "Let the catering come" scored 0.64 with the runner-up on 0.48 - obviously
    right, and refused for want of 0.01 against a flat floor.
    """
    decision = router.decide("Let the catering come.", opposite_actions)
    assert decision.action_index is not None
    assert opposite_actions[decision.action_index].name == "GSX, START CATERING"


def test_empty_action_list_declines(router):
    assert router.decide("intercom", []).action_index is None


def test_normalise_strips_button_decoration():
    assert normalise("GROUND CREW >") == "ground crew"
    assert normalise("CABIN CREW ARE CALLING...") == "cabin crew calling"
    assert normalise("Please take your seats") == "take seats"
