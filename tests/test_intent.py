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


def test_short_alias_cannot_claim_a_long_utterance(router, opposite_actions):
    """A one-word alias must not outrank the button that actually matches.

    "send it" is registered for GO AHEAD and reduces to the single word
    "send"; token_set_ratio scores a subset as perfect, so it tied with
    GSX, START CATERING on "Send me your catering" and the pair was refused
    as ambiguous.
    """
    decision = router.decide("Send me your catering.", opposite_actions)
    assert decision.action_index is not None
    assert opposite_actions[decision.action_index].name == "GSX, START CATERING"


#: Buttons SLC showed during a real boarding sequence.
BOARDING_BUTTONS = SLC_BUTTONS + [
    "YES", "NO", "HELLO?",
    "START BOARDING WHEN READY", "YES, START BOARDING", "REQUEST LOADING UPDATE",
    "REQUEST OFFLOADING UPDATE", "YES, START LOADING", "GSX, START BOARDING",
    "GSX, START CATERING", "CONNECT JETWAY", "DISCONNECT JETWAY",
]

#: Verbatim Whisper output captured in use. Polish "ladowac" means both
#: letting passengers on and loading cargo, so it arrives as "load" or even
#: "charge" - words with nothing in common with "BOARDING".
REAL_TRANSCRIPTS = [
    ("OK, you can load if you are ready.", "START BOARDING WHEN READY"),
    ("You can charge passengers when you are ready.", "START BOARDING WHEN READY"),
    ("How does the charging look like?", "REQUEST LOADING UPDATE"),
    ("Connect the Jetway.", "CONNECT JETWAY"),
    ("Cockpit for ground control", "GROUND CREW >"),
    ("Hello?", "HELLO?"),
]


@pytest.fixture(scope="module")
def boarding_actions() -> list["FakeAction"]:
    return [FakeAction(n) for n in BOARDING_BUTTONS]


@pytest.mark.parametrize("said,expected", REAL_TRANSCRIPTS)
def test_real_transcripts_route(router, boarding_actions, said, expected):
    decision = router.decide(said, boarding_actions)
    assert decision.action_index is not None, "declined a real command: " + said
    assert boarding_actions[decision.action_index].name == expected


#: Sentences that merely contain a one-word button's name. token_set_ratio
#: scores a subset as perfect, so each of these fired its button at 1.00.
INCIDENTAL_WORDS = [
    "my phone battery is charging",
    "yes we should go back to the gate",
    "no idea what the settings are",
    "i will call you back later tonight",
]


@pytest.mark.parametrize("said", INCIDENTAL_WORDS)
def test_one_word_buttons_need_more_than_one_shared_word(router, boarding_actions, said):
    decision = router.decide(said, boarding_actions)
    assert decision.action_index is None, "fired {n!r} on {s!r}".format(
        n=boarding_actions[decision.action_index].name, s=said)


@pytest.mark.parametrize("said,expected", [
    ("phone", "PHONE >"), ("back", "BACK"), ("hello", "HELLO?"),
])
def test_one_word_commands_still_work(router, boarding_actions, said, expected):
    """The damping must not cost us the short commands themselves."""
    decision = router.decide(said, boarding_actions)
    assert decision.action_index is not None
    assert boarding_actions[decision.action_index].name == expected


#: Cabin-crew buttons, several of which contain each other's words.
CABIN_BUTTONS = [
    "GROUND CREW >", "INTERCOM >", "P A SYSTEM >", "PHONE >",
    "PURSER TO INTERCOM", "CABIN CREW TO INTERCOM", "THAT'S PERFECT",
    "HOW'S IT GOING?", "HOW ARE THE PASSENGERS?", "TURN THE MUSIC UP",
    "TURN THE MUSIC DOWN", "UP A BIT MORE", "DOWN A BIT MORE", "ROGER", "BACK",
    "Inflight Services", "Settings", "Check List",
]

#: More verbatim Whisper output. "moze byc" arrives as "it can be";
#: "jak leci" as "how does it go"; "stewardesa" as "stewardess", a word on
#: no SLC button at all.
CABIN_TRANSCRIPTS = [
    ("It can be. Super.", "THAT'S PERFECT"),
    ("Great, it can be done.", "THAT'S PERFECT"),
    ("How does it go?", "HOW'S IT GOING?"),
    ("Call the stewardess.", "INTERCOM >"),
    ("Turn up the music.", "TURN THE MUSIC UP"),
    ("A little bit more.", "UP A BIT MORE"),
    ("intercom", "INTERCOM >"),
    ("purser", "PURSER TO INTERCOM"),
]


@pytest.fixture(scope="module")
def cabin_actions() -> list["FakeAction"]:
    return [FakeAction(n) for n in CABIN_BUTTONS]


@pytest.mark.parametrize("said,expected", CABIN_TRANSCRIPTS)
def test_cabin_transcripts_route(router, cabin_actions, said, expected):
    decision = router.decide(said, cabin_actions)
    assert decision.action_index is not None, "declined: " + said
    assert cabin_actions[decision.action_index].name == expected


#: "THANK YOU" is two words that normalise to one, because "you" is filler.
#: Damping it as a one-word button halved "OK, thanks." and tied it with
#: "Stand By", while a bare "Thank you." scored 1.00 - so the command worked
#: only when said with nothing in front of it.
POLITE_BUTTONS = CABIN_BUTTONS + ["THANK YOU", "THANKS VERY MUCH", "Stand By"]

POLITE_TRANSCRIPTS = [
    ("OK, thanks.", "THANK YOU"),
    ("Thank you.", "THANK YOU"),
    ("OK, thank you.", "THANK YOU"),
    ("How does it look like?", "HOW'S IT GOING?"),
    ("Cockpit for stewardess", "INTERCOM >"),
]


@pytest.fixture(scope="module")
def polite_actions() -> list["FakeAction"]:
    return [FakeAction(n) for n in POLITE_BUTTONS]


@pytest.mark.parametrize("said,expected", POLITE_TRANSCRIPTS)
def test_filler_before_a_command_does_not_break_it(router, polite_actions,
                                                   said, expected):
    decision = router.decide(said, polite_actions)
    assert decision.action_index is not None, "declined: " + said
    assert polite_actions[decision.action_index].name == expected


def test_specific_key_beats_generic_one(router):
    """A button must not inherit aliases from a key it merely contains.

    "intercom" is a word inside "PURSER TO INTERCOM", and applying both keys
    gave that button every generic intercom alias - so "call the crew" tied
    between the two and was refused.
    """
    from slcvoiceai.aliases import aliases_for
    generic = set(aliases_for("INTERCOM >"))
    specific = set(aliases_for("PURSER TO INTERCOM"))
    assert generic and specific
    assert not (generic & specific)


def test_empty_action_list_declines(router):
    assert router.decide("intercom", []).action_index is None


def test_normalise_strips_button_decoration():
    assert normalise("GROUND CREW >") == "ground crew"
    assert normalise("CABIN CREW ARE CALLING...") == "cabin crew calling"
    assert normalise("Please take your seats") == "take seats"
