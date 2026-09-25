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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slcvoiceai import aliases  # noqa: E402
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


#: Openers that add nothing. They must not dilute the command behind them,
#: and must not carry a command of their own when there is one behind them.
DISCOURSE_TRANSCRIPTS = [
    ("OK, understood", "ROGER"),
    ("OK, roger that", "ROGER"),
    ("OK, never mind", "DISREGARD"),
    ("OK, thanks.", "THANK YOU"),
    ("alright thanks", "THANK YOU"),
    ("yeah go ahead", "GO AHEAD"),
    ("ok", "ROGER"),
]

DISCOURSE_BUTTONS = POLITE_BUTTONS + ["GO AHEAD", "DISREGARD", "YES", "NO"]


@pytest.fixture(scope="module")
def discourse_actions() -> list["FakeAction"]:
    return [FakeAction(n) for n in DISCOURSE_BUTTONS]


@pytest.mark.parametrize("said,expected", DISCOURSE_TRANSCRIPTS)
def test_openers_do_not_dilute_the_command(router, discourse_actions,
                                           said, expected):
    """"OK, understood" scored 0.50 against ROGER where a bare "understood"
    scored 1.00 - the reach damping counted "OK" as content the one-word
    alias had to cover. "alright thanks" then tied ROGER against THANK YOU,
    because "alright" is registered for one and "thanks" for the other."""
    decision = router.decide(said, discourse_actions)
    assert decision.action_index is not None, "declined: " + said
    assert discourse_actions[decision.action_index].name == expected


@pytest.mark.parametrize("said", [
    "ok so we are cleared to land runway two seven",
    "well the weather looks bad today",
])
def test_an_opener_does_not_turn_chatter_into_a_command(router, discourse_actions, said):
    decision = router.decide(said, discourse_actions)
    assert decision.action_index is None, "fired {n!r} on {s!r}".format(
        n=discourse_actions[decision.action_index].name, s=said)


def test_a_weak_accept_is_a_known_limit_of_the_offline_layer(router, discourse_actions):
    """Documented, not asserted away - and it got slightly worse.

    "alright everyone lets get going" reaches "HOW'S IT GOING?" on the
    strength of the word "going" alone. String matching cannot tell that
    apart from a real command; only the cloud can, which is why
    CascadeRouter escalates weak accepts as well as refusals.

    It used to score 0.62 - under the 0.65 floor, so the cascade caught it.
    Since normalise() stopped splitting contractions the button reduces to
    two tokens rather than three ("hows going", not "how s going"), one of
    which matches, and it now scores 0.67: over the floor, so it settles
    offline instead of being asked about. The trade was worth making - no
    alias containing a contraction could fire at all before - but this is
    its price, and min_confidence is the lever if it ever misfires for real.
    So far it has not: this phrase is invented, not recorded in a flight.

    Tried and rejected as a fix: damping a button's name by how much of the
    utterance it covers instead of only damping one-word names. It breaks
    four recorded transcripts, "Let's start with the ground operation" ->
    GROUND CREW > among them, because a short button legitimately answers a
    long sentence. The asymmetry in _name_reach is deliberate.
    """
    decision = router.decide("alright everyone lets get going", discourse_actions)
    assert decision.confidence < 0.75, (
        "chatter is drifting up the scale, not merely over the floor")


#: SLC has twenty delay buttons, several differing only by a word like
#: short/long or departure/arrival. In a flight they appear in different
#: phases; listing them together here is the hardest case, not a typical one.
DELAY_BUTTONS = [
    "SORRY FOR THE DELAY", "NO DEPARTURE DELAY", "SHORT DELAY EXPECTED",
    "WE'RE RUNNING BEHIND", "ATC DELAY", "EXTENDED DELAY EXPECTED",
    "NO ENROUTE DELAYS", "SLIGHT ENROUTE DELAY", "EXTENDED ENROUTE DELAY",
    "MEDIUM DELAY EXPECTED", "WE'RE RUNNING LATE", "NO ARRIVAL DELAY EXPECTED",
    "SHORT ARRIVAL DELAY EXPECTED", "LONG ARRIVAL DELAY EXPECTED",
    "A LITTLE DELAYED", "RUNNING LATE", "SHORT ARRIVAL DELAY", "ARRIVAL DELAY",
    "WELCOME ABOARD", "ROGER", "BACK",
]

DELAY_TRANSCRIPTS = [
    # Verbatim Whisper output for "mozemy spodziewac sie krotkiego opoznienia"
    ("we can expect a short delay", "SHORT DELAY EXPECTED"),
    ("a short delay is expected", "SHORT DELAY EXPECTED"),
    ("extended delay expected", "EXTENDED DELAY EXPECTED"),
    ("medium delay", "MEDIUM DELAY EXPECTED"),
    ("we are running a bit behind", "WE'RE RUNNING BEHIND"),
    ("air traffic control delay", "ATC DELAY"),
    ("sorry for the delay", "SORRY FOR THE DELAY"),
]


@pytest.fixture(scope="module")
def delay_actions() -> list["FakeAction"]:
    return [FakeAction(n) for n in DELAY_BUTTONS]


@pytest.mark.parametrize("said,expected", DELAY_TRANSCRIPTS)
def test_delay_announcements_route(router, delay_actions, said, expected):
    decision = router.decide(said, delay_actions)
    assert decision.action_index is not None, "declined: " + said
    assert delay_actions[decision.action_index].name == expected


def test_a_long_delay_never_announces_a_short_one(router, delay_actions):
    """The dangerous direction. "we expect a long delay" reached SHORT DELAY
    EXPECTED at 0.83 until short/long joined the opposites table - announcing
    the opposite of what the captain said to a cabin full of passengers."""
    decision = router.decide("we expect a long delay", delay_actions)
    if decision.action_index is not None:
        assert delay_actions[decision.action_index].name != "SHORT DELAY EXPECTED"


@pytest.mark.parametrize("said", ["short delay", "no delay expected"])
def test_departure_and_arrival_variants_are_left_ambiguous(router, delay_actions,
                                                           said):
    """SLC offers a departure and an arrival version of several of these, and
    a bare phrase does not say which. Refusing beats guessing - and in a real
    flight only one of the pair is on screen anyway."""
    assert router.decide(said, delay_actions).action_index is None


UI_WORD_TRANSCRIPTS = [
    # Verbatim, from "wlacz przycisk zapiecia pasow"
    (", press the button to fasten the seatbelt,", "Seatbelts"),
    ("turn on the seatbelt sign", "Seatbelts"),
    ("fasten seat belts", "Seatbelts"),
    ("belts on", "Seatbelts"),
    ("press the doors button", "Toggle Doors"),
]


@pytest.mark.parametrize("said,expected", UI_WORD_TRANSCRIPTS)
def test_describing_the_ui_does_not_hijack_the_command(router, discourse_actions,
                                                       said, expected):
    """"press the button to..." is how a person describes using an interface,
    not part of what they want done. The word "button" reached the control
    named "Main Button" at 0.71 and collapsed SLC's toolbar, while Seatbelts
    - one word against a four-word sentence - was damped to 0.25."""
    actions = discourse_actions + [FakeAction("Seatbelts"),
                                   FakeAction("Toggle Doors")]
    decision = router.decide(said, actions)
    assert decision.action_index is not None, "declined: " + said
    assert actions[decision.action_index].name == expected


ANSWERING_TRANSCRIPTS = [
    # "prosze mowic" and "tak slucham", as Whisper renders them. Neither
    # shares a word with "GO AHEAD".
    ("Please speak", "GO AHEAD"),
    ("Yes I am listening", "GO AHEAD"),
    ("I am listening", "GO AHEAD"),
    ("talk to me", "GO AHEAD"),
    ("what do you need", "GO AHEAD"),
]


@pytest.mark.parametrize("said,expected", ANSWERING_TRANSCRIPTS)
def test_answering_a_call_routes(router, discourse_actions, said, expected):
    actions = discourse_actions + [FakeAction("Tannoy"), FakeAction("Seatbelts")]
    decision = router.decide(said, actions)
    assert decision.action_index is not None, "declined: " + said
    assert actions[decision.action_index].name == expected


def test_talking_about_speaking_is_not_a_command(router, discourse_actions):
    actions = discourse_actions + [FakeAction("Tannoy"), FakeAction("Seatbelts")]
    assert router.decide("we can speak about it after landing",
                         actions).action_index is None


def test_toolbar_toggle_is_not_a_voice_target():
    """Its name comes from cmdMainButton, says nothing about what it does,
    and attracts any sentence containing "button"."""
    from slcvoiceai.slc_ui import is_denied
    assert is_denied("Main Button")


def test_thanks_for_information_reaches_roger_when_thanks_is_absent(router):
    """SLC does not always offer THANK YOU.

    After a ground crew exchange the only acknowledgement on screen is often
    ROGER, and "ok dzieki za informacje" found nothing at all - the offline
    layer landed on AUDIO MANAGER at 0.42 and Gemini correctly declined,
    because there was no right button to press.
    """
    actions = [FakeAction(n) for n in [
        "Notifications", "Seatbelts", "AUDIO MANAGER", "GROUND CREW >",
        "INTERCOM >", "ROGER", "REPEAT TRANSMISSION", "BACK",
    ]]
    for said in ("OK, thank you very much for the information.",
                 "thanks for the info", "OK, understood"):
        decision = router.decide(said, actions)
        assert decision.action_index is not None, "declined: " + said
        assert actions[decision.action_index].name == "ROGER"


def test_plain_thanks_declines_when_there_is_nothing_to_thank(router):
    """Correct behaviour, not a gap: with no THANK YOU on screen, a bare
    thank-you has no target and pressing something else would be worse."""
    actions = [FakeAction(n) for n in ["Seatbelts", "GROUND CREW >", "ROGER"]]
    assert router.decide("OK, thank you very much", actions).action_index is None


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


# -- radio check and dismissal, captured in flight 2026-09-18 -------------

#: Exactly what SLC offered at 08:51:51, answering a radio check.
RADIO_BUTTONS = [
    "Notifications", "Toggle Door Mode", "Seatbelts", "Inflight Services",
    "Aircraft Layout", "Available Phrases Window", "Narration Window",
    "Check List", "Settings", "Stand By", "Toggle Doors", "Tannoy",
    "Start New Passenger Flight", "Start New Simple Passenger Flight",
    "AUDIO MANAGER", "GROUND CREW >", "INTERCOM >", "P A SYSTEM >", "PHONE >",
    "LOUD AND CLEAR", "REPEAT TRANSMISSION", "BACK",
]

#: And at 08:52:43, mid ground-crew exchange.
GROUND_BUTTONS = [
    "Notifications", "Toggle Door Mode", "Seatbelts", "Inflight Services",
    "Aircraft Layout", "Available Phrases Window", "Narration Window",
    "Check List", "Settings", "Stand By", "Toggle Doors", "Tannoy",
    "Start New Passenger Flight", "Start New Simple Passenger Flight",
    "AUDIO MANAGER", "GROUND CREW >", "INTERCOM >", "P A SYSTEM >", "PHONE >",
    "CONNECT JETWAY", "DISCONNECT JETWAY", "REQUEST LOADING UPDATE",
    "STARTING THE APU", "RADIO CHECK", "STANDBY", "DISREGARD", "BACK",
]


@pytest.fixture(scope="module")
def radio_actions() -> list["FakeAction"]:
    return [FakeAction(n) for n in RADIO_BUTTONS]


@pytest.fixture(scope="module")
def ground_actions() -> list["FakeAction"]:
    return [FakeAction(n) for n in GROUND_BUTTONS]


#: Whisper's translation of Polish "piec na piec" and "slychac dobrze".
RADIO_ANSWERS = [
    ("5 by 5", "LOUD AND CLEAR"),
    ("5 to 5, good to hear", "LOUD AND CLEAR"),
    ("five by five", "LOUD AND CLEAR"),
    ("hear loud and clear", "LOUD AND CLEAR"),
    ("i hear you well", "LOUD AND CLEAR"),
]


@pytest.mark.parametrize("said,expected", RADIO_ANSWERS)
def test_answering_a_radio_check_routes(router, radio_actions, said, expected):
    """'5 by 5' is the standard answer to a radio check, and it shares a word
    with 'Stand By' and nothing at all with 'LOUD AND CLEAR'. It pressed
    Stand By in flight - a wrong button, not a decline."""
    decision = router.decide(said, radio_actions)
    assert decision.action_index is not None, "declined: " + said
    assert radio_actions[decision.action_index].name == expected
    assert decision.confidence >= THRESHOLD


#: Whisper's translation of Polish "nie wazne".
DISMISSALS = [
    ("ok, not important", "DISREGARD"),
    ("doesnt matter", "DISREGARD"),
    ("it doesnt matter", "DISREGARD"),
    ("not important", "DISREGARD"),
]


@pytest.mark.parametrize("said,expected", DISMISSALS)
def test_waving_something_away_reaches_disregard(router, ground_actions,
                                                 said, expected):
    decision = router.decide(said, ground_actions)
    assert decision.action_index is not None, "declined: " + said
    assert ground_actions[decision.action_index].name == expected


def test_five_by_five_does_not_fall_back_onto_stand_by(router):
    """The alias fixes the flight case, but the trap underneath it remains:
    '5 by 5' shares a word with 'Stand By'. With LOUD AND CLEAR off screen
    the answer is to say nothing, not to press the button that rhymes."""
    without = [FakeAction(n) for n in RADIO_BUTTONS if n != "LOUD AND CLEAR"]
    decision = router.decide("5 by 5", without)
    if decision.action_index is not None:
        assert without[decision.action_index].name != "Stand By"


def test_waving_away_declines_when_disregard_is_absent(router, radio_actions):
    """RADIO_BUTTONS has no DISREGARD. Nothing there means 'never mind'."""
    for said in ("ok, not important", "doesnt matter"):
        decision = router.decide(said, radio_actions)
        assert decision.action_index is None, (
            "pressed {n!r} for {s!r}".format(
                n=radio_actions[decision.action_index].name, s=said))


#: What the scan now offers where GROUND_BUTTONS had both: the toolbar's
#: 'Stand By' is gone, refused by slc_ui because its tooltip reads
#: "Close SLC or Restart Flight". Only the radio button is left.
GROUND_BUTTONS_TODAY = [n for n in GROUND_BUTTONS if n != "Stand By"]


@pytest.fixture(scope="module")
def ground_today() -> list["FakeAction"]:
    return [FakeAction(n) for n in GROUND_BUTTONS_TODAY]


@pytest.mark.parametrize("said", [
    "standby", "stand by", "wait", "hold on", "one moment",
    "just a second", "please wait", "wait a moment",
])
def test_telling_someone_to_wait_reaches_the_radio_standby(router, ground_today,
                                                           said):
    """SLC used to show two buttons a person would call 'stand by': the
    radio reply, and a toolbar icon that closes SLC or restarts the flight.
    They tied, so the router declined and the pilot got nothing."""
    decision = router.decide(said, ground_today)
    assert decision.action_index is not None, "declined: " + said
    assert ground_today[decision.action_index].name == "STANDBY"


def test_the_two_stand_bys_used_to_tie(router):
    """Why the fix belongs in the scan and not in the matcher: as names
    alone, these two are equally good answers and no threshold can separate
    them. Only SLC's tooltip says one of them ends the flight."""
    both = [FakeAction(n) for n in ("Stand By", "STANDBY", "ROGER")]
    decision = router.decide("hold on", both)
    assert decision.action_index is None
    assert "ambiguous" in decision.reasoning.lower()


# -- a courtesy opener must not claim the sentence behind it --------------

#: What SLC offered at 23:17:53 on 2026-09-18, trimmed to what matters.
LANDING_BUTTONS = [
    "THANK YOU", "BE SEATED FOR LANDING NOW", "PREPARE CABIN FOR LANDING",
    "Seatbelts", "ROGER", "Check List", "Tannoy", "INTERCOM >",
    "GROUND CREW >", "Interactions Crew", "Interactions Ground Crew",
]


@pytest.fixture(scope="module")
def landing_actions() -> list["FakeAction"]:
    return [FakeAction(n) for n in LANDING_BUTTONS]


def test_thanks_in_front_does_not_swallow_the_command(router, landing_actions):
    """Whisper heard this perfectly; the matcher threw it away. 'THANK YOU'
    normalises to the single token 'thank', token_set_ratio scores a subset
    as perfect, and the button was exempt from reach damping - so a courtesy
    opener claimed a thirteen-word sentence at 1.00 and, being certain, never
    escalated to the model that would have understood it."""
    said = "ok, thank you, you can sit down, we will land in a moment"
    decision = router.decide(said, landing_actions)
    if decision.action_index is not None:
        assert landing_actions[decision.action_index].name != "THANK YOU", (
            "the courtesy opener won again")


@pytest.mark.parametrize("said", [
    "Thank you.",
    "ok, thanks",
    "OK, thank you very much",
    "thank you very much",
])
def test_actually_thanking_someone_still_works(router, landing_actions, said):
    """The damping must not cost the case it was exempted for."""
    decision = router.decide(said, landing_actions)
    assert decision.action_index is not None, "declined plain thanks: " + said
    assert landing_actions[decision.action_index].name == "THANK YOU"


def test_a_filler_heavy_alias_does_not_win_on_its_filler(router):
    """'connect me with ground' is registered for GROUND CREW, but two of its
    three words are connective. It took 'connect me with the stewardess' at
    0.77 - above the floor, so nothing escalated - and pressed the ground
    crew while the pilot was asking for the cabin."""
    actions = [FakeAction(n) for n in
               ("INTERCOM >", "Interactions Ground Crew", "Interactions Crew",
                "Seatbelts", "Check List", "Tannoy")]
    decision = router.decide("connect me with the stewardess", actions)
    if decision.action_index is not None:
        assert actions[decision.action_index].name != "Interactions Ground Crew", (
            "still reaching for the ground crew")


def test_asking_for_ground_still_reaches_ground(router):
    actions = [FakeAction(n) for n in
               ("INTERCOM >", "GROUND CREW >", "Seatbelts", "Check List")]
    decision = router.decide("connect me with ground", actions)
    assert decision.action_index is not None
    assert actions[decision.action_index].name == "GROUND CREW >"


# -- descent, which Whisper words differently every time ------------------

#: SLC shows exactly one of its descent buttons at a time - captured over a
#: flight, no two of them were ever on screen together.
DESCENT_SCREENS = {
    "DESCENDING SOON": ["DESCENDING SOON", "NORMAL CRUISE", "Check List",
                        "Aircraft Layout", "P A SYSTEM >", "Seatbelts"],
    "DESCENDING SHORTLY": ["DESCENDING SHORTLY", "NORMAL CRUISE", "Check List",
                           "Aircraft Layout", "P A SYSTEM >", "Seatbelts"],
    "DESCENT STARTING": ["DESCENT STARTING", "NORMAL CRUISE", "Check List",
                         "Aircraft Layout", "P A SYSTEM >", "Seatbelts"],
    "WE'VE STARTED OUR DESCENT": ["WE'VE STARTED OUR DESCENT", "NORMAL CRUISE",
                                  "Check List", "P A SYSTEM >", "Seatbelts"],
}

#: Whisper's translations of "Wkrótce będziemy zniżać" and "Zaczynamy
#: zniżanie", before and after the vocabulary was seeded with "descending".
DESCENT_TRANSCRIPTS = [
    ("shortly, we will descend", "DESCENDING SOON"),
    ("we will start descending soon", "DESCENDING SOON"),
    ("shortly we will descend", "DESCENDING SHORTLY"),
    ("we are descending", "DESCENT STARTING"),
    ("start lowering", "DESCENT STARTING"),
    ("we started to lower", "WE'VE STARTED OUR DESCENT"),
]


@pytest.mark.parametrize("said,expected", DESCENT_TRANSCRIPTS)
def test_descent_wording_reaches_the_button_on_screen(router, said, expected):
    actions = [FakeAction(n) for n in DESCENT_SCREENS[expected]]
    decision = router.decide(said, actions)
    assert decision.action_index is not None, "declined: " + said
    assert actions[decision.action_index].name == expected


def test_a_transcript_that_lost_the_meaning_is_still_refused(router):
    """No alias can rescue "short, we will reduce" - the word for descending
    is simply not in it. That is what the vocabulary seeding is for, and the
    matcher's job here is to decline rather than invent something."""
    actions = [FakeAction(n) for n in DESCENT_SCREENS["DESCENDING SOON"]]
    for said in ("shortly we will reduce", "we will short-circuit"):
        decision = router.decide(said, actions)
        assert decision.action_index is None, "matched nonsense: " + said


def test_soon_and_starting_are_left_ambiguous_if_both_ever_appear(router):
    """A bare "we are descending" does not say whether the descent has begun
    or is about to. SLC has never shown both buttons at once, so this has no
    effect in a flight - but if that changes, refusing is the right answer."""
    actions = [FakeAction(n) for n in ("DESCENDING SOON", "DESCENT STARTING",
                                       "Check List")]
    assert router.decide("we are descending", actions).action_index is None


# -- contractions, which Whisper produces constantly ----------------------

def test_a_contraction_survives_normalising():
    """normalise() replaced the apostrophe with a space, so "I'm" became the
    two fragments "i" and "m" and the first was dropped as noise. The alias
    table is written the other way - "im listening", "thats fine", "didnt
    catch that" - so no alias containing a contraction could ever match."""
    assert normalise("I'm listening") == normalise("im listening")
    assert normalise("that's fine") == normalise("thats fine")
    assert normalise("we're running late") == normalise("were running late")


def test_a_contraction_matches_with_or_without_the_apostrophe():
    """Whisper writes it both ways from one utterance to the next."""
    actions = [FakeAction(n) for n in
               ("THAT'S PERFECT", "HOW'S IT GOING?", "I'LL CALL YOU BACK",
                "ROGER", "Check List", "Seatbelts")]
    router = FuzzyRouter(min_confidence=THRESHOLD)
    for said, expected in [("that's perfect", "THAT'S PERFECT"),
                           ("thats perfect", "THAT'S PERFECT"),
                           ("how's it going", "HOW'S IT GOING?"),
                           ("hows it going", "HOW'S IT GOING?"),
                           ("I'll call you back", "I'LL CALL YOU BACK"),
                           ("ill call you back", "I'LL CALL YOU BACK")]:
        decision = router.decide(said, actions)
        assert decision.action_index is not None, "declined: " + said
        assert actions[decision.action_index].name == expected, said


@pytest.mark.parametrize("said", [
    "you can say", "I'm listening", "im listening", "you can speak",
    "you can talk", "please speak", "yes I'm listening",
])
def test_answering_a_call_reaches_go_ahead(router, said):
    """Polish "prosze mowic" and "tak slucham" come back worded a different
    way each time. "you can say" was refused four times in one flight with
    GO AHEAD on screen."""
    actions = [FakeAction(n) for n in
               ("GO AHEAD", "Tannoy", "Check List", "Seatbelts", "BACK")]
    decision = router.decide(said, actions)
    assert decision.action_index is not None, "declined: " + said
    assert actions[decision.action_index].name == "GO AHEAD"


# -- the gaps a coverage audit of all 295 SLC lines turned up --------------
#
# Every case below was measured against the real matcher before the aliases
# existed, and the score it reached then is in the comment. None were
# reachable by wording alone: Whisper translates the sense of the Polish
# rather than its shape, so the words that arrive share nothing with the
# button's own.

#: Around the seating button, whatever it is. SLC offers these variants one
#: at a time - across 238 scans logged in flight, never two together - so a
#: test that puts them all on screen is testing a situation that does not
#: arise, and the matcher rightly refuses to choose between them.
NEIGHBOURS = ("THANK YOU", "INTERCOM >", "P A SYSTEM >", "BACK")


@pytest.mark.parametrize("said,expected", [
    # "usiadzcie" -> refused at 0.38 with PLEASE BE SEATED on screen
    ("sit down please", "PLEASE BE SEATED"),
    ("please sit down", "PLEASE BE SEATED"),
    ("everyone sit down", "PLEASE BE SEATED"),          # was 0.32
    # "zostancie na miejscach" -> 0.54, under the floor
    ("stay in your seats", "PLEASE REMAIN SEATED"),
    ("please stay seated", "PLEASE REMAIN SEATED"),
    # the same instruction with a phase attached, which is how it is said
    ("sit down we are landing", "BE SEATED FOR LANDING NOW"),
    ("sit down we are taking off", "BE SEATED FOR TAKEOFF NOW"),
])
def test_seating_the_cabin_reaches_the_right_button(router, said, expected):
    actions = [FakeAction(n) for n in (expected,) + NEIGHBOURS]
    decision = router.decide(said, actions)
    assert decision.action_index is not None, "declined: " + said
    assert actions[decision.action_index].name == expected, said


def test_a_phrase_with_no_phase_does_not_pick_one(router):
    """If SLC ever did offer the takeoff and landing variants together,
    "sit down please" says nothing about which - so refusing and asking is
    right, and guessing would be a confident wrong press.

    This is also why the aliases are keyed to whole button names: a key of
    "be seated" would have handed both variants the phase-less phrases and
    made this tie unbreakable.
    """
    actions = [FakeAction(n) for n in
               ("BE SEATED FOR TAKEOFF NOW", "BE SEATED FOR LANDING NOW",
                "THANK YOU", "BACK")]
    decision = router.decide("sit down please", actions)
    assert decision.action_index is None, (
        "picked {n!r} with nothing to go on".format(
            n=actions[decision.action_index].name))


def test_the_recorded_sit_down_for_landing_utterance(router):
    """From the flight of 18.09: "Ok dziekuje, mozecie usiasc, zaraz
    bedziemy ladowac". It pressed THANK YOU, then - once reach was measured
    properly - reached 0.35 and was refused. Neither is the button meant."""
    actions = [FakeAction(n) for n in
               ("THANK YOU", "BE SEATED FOR LANDING NOW", "INTERCOM >",
                "I'll CALL WHEN WE'RE READY", "BACK")]
    said = "ok, thank you, you can sit down, we will land in a moment"
    decision = router.decide(said, actions)
    assert decision.action_index is not None, "still refused"
    assert actions[decision.action_index].name == "BE SEATED FOR LANDING NOW"


def test_thanking_on_its_own_still_thanks(router):
    """The other half of that case: the seating aliases must not swallow a
    plain thank-you, which opens with the same words."""
    actions = [FakeAction(n) for n in
               ("THANK YOU", "BE SEATED FOR LANDING NOW", "BACK")]
    decision = router.decide("ok, thank you", actions)
    assert actions[decision.action_index].name == "THANK YOU"


@pytest.mark.parametrize("said,expected", [
    # "przepraszam" -> APOLOGIES at 0.12, 272nd of 295
    ("i am sorry", "APOLOGIES"),
    ("my apologies", "APOLOGIES"),
    # "moja wina" -> THAT'S MY BAD at 0.29
    ("my fault", "THAT'S MY BAD"),
    ("my mistake", "THAT'S MY BAD"),
])
def test_apologising_reaches_an_apology(router, said, expected):
    actions = [FakeAction(n) for n in
               ("APOLOGIES", "THAT'S MY BAD", "THANK YOU", "ROGER", "BACK")]
    decision = router.decide(said, actions)
    assert decision.action_index is not None, "declined: " + said
    assert actions[decision.action_index].name == expected, said


@pytest.mark.parametrize("said", [
    "we are going back", "we are turning back", "heading back",
])
def test_turning_back_reaches_the_return(router, said):
    """"wracamy" -> RETURNING TO AIRPORT at 0.37, 37th of 295."""
    actions = [FakeAction(n) for n in
               ("RETURNING TO AIRPORT", "CONTINUE FLIGHT", "THANK YOU", "BACK")]
    decision = router.decide(said, actions)
    assert decision.action_index is not None, "declined: " + said
    assert actions[decision.action_index].name == "RETURNING TO AIRPORT", said


@pytest.mark.parametrize("said,expected", [
    ("listen carefully", "LISTEN TO INSTRUCTIONS"),     # was 0.55
    ("sit back and relax", "RELAX AND ENJOY"),          # was 0.62
    ("enjoy the flight", "RELAX AND ENJOY"),
    ("hang up", "CANCEL / HANGUP"),                     # was 0.60
    ("end the call", "CANCEL / HANGUP"),
])
def test_the_remaining_audit_gaps_are_reachable(router, said, expected):
    actions = [FakeAction(n) for n in
               ("LISTEN TO INSTRUCTIONS", "PLEASE LISTEN TO THE CABIN CREW",
                "RELAX AND ENJOY", "CANCEL / HANGUP", "DISCONNECT JETWAY",
                "THANK YOU", "BACK")]
    decision = router.decide(said, actions)
    assert decision.action_index is not None, "declined: " + said
    assert actions[decision.action_index].name == expected, said


def test_ending_a_call_is_not_pulling_the_jetway(router):
    """"disconnect" is deliberately not an alias for hanging up: it is what
    you say to move a jetway, and losing that distinction would be worse
    than the gap the alias closes."""
    actions = [FakeAction(n) for n in
               ("CANCEL / HANGUP", "DISCONNECT JETWAY", "CONNECT JETWAY")]
    decision = router.decide("disconnect the jetway", actions)
    assert actions[decision.action_index].name == "DISCONNECT JETWAY"


# -- saying what was missing, not what was wrong ---------------------------
#
# A pilot answering a ground crew that is not waiting used to be told the
# phrase was too ambiguous. It was not: "go ahead" is exact. The button was
# absent. One flight lost eight minutes to that message, tried in Polish and
# then in English, because it sent them looking for better words.

#: The top-level menu, as SLC really logged it, with no GO AHEAD on screen.
QUIET = ["Notifications", "Toggle Door Mode", "Seatbelts", "Inflight Services",
         "Aircraft Layout", "Available Phrases Window", "Narration Window",
         "Check List", "Settings", "Toggle Doors", "Tannoy", "AUDIO MANAGER",
         "GROUND CREW >", "INTERCOM >", "P A SYSTEM >", "PHONE >", "BACK"]
CALLING = QUIET + ["GO AHEAD"]


@pytest.mark.parametrize("said", [
    "go ahead",           # exactly the button's own words
    "you can speak",      # was already an alias, and still found nothing
    "you can continue",   # was not an alias until this flight
    "im listening",
])
def test_it_names_the_button_that_was_not_there(said):
    assert aliases.target_not_offered(said, QUIET) == "GO AHEAD"


@pytest.mark.parametrize("said", ["go ahead", "you can speak", "im listening"])
def test_it_says_nothing_when_the_button_is_on_screen(said):
    """Then the refusal really was about matching, and whatever the matcher
    said about it stands."""
    assert aliases.target_not_offered(said, CALLING) is None


@pytest.mark.parametrize("said", [
    # "ok" is an alias for ROGER and sits inside half of what a pilot says.
    # Reporting this as reaching for ROGER would be a confident lie.
    "ok, you can start boarding if you are ready",
    "Take care!",
    "help me with these doors",
    "",
    "   ",
])
def test_a_word_in_passing_names_nothing(said):
    assert aliases.target_not_offered(said, QUIET) is None


def test_the_one_it_names_is_a_button_slc_really_has():
    """The message tells the pilot to look for something. Naming a family
    key SLC never puts on a button would send them looking for nothing."""
    assert aliases.target_not_offered("go ahead", QUIET) == "GO AHEAD"
    # and the name it gives has to be the thing that would satisfy it
    assert aliases.target_not_offered("go ahead", ["GO AHEAD"]) is None


def test_the_decline_path_uses_it():
    """The function is only worth having if the refusal actually calls it."""
    source = (ROOT / "slcvoiceai" / "app.py").read_text(encoding="utf-8")
    decline = source[source.index("if decision.action_index is None:"):]
    decline = decline[:decline.index("action = actions[")]
    assert "target_not_offered" in decline, (
        "the refusal does not ask what was missing")
    assert "decision.reasoning" in decline, (
        "the matcher's own reason has to survive for the other cases")


# -- and what to do about it ----------------------------------------------

def _advice(bindings, names):
    """Bridge._how_to_reach without building a Bridge, which would want a
    router, SLC and a speech model to answer a question about wording."""
    import types

    from slcvoiceai.app import Bridge

    stand_in = types.SimpleNamespace(
        cfg=types.SimpleNamespace(hotkeys=bindings),
        BEHIND_A_SUBMENU=Bridge.BEHIND_A_SUBMENU)
    actions = [types.SimpleNamespace(name=n) for n in names]
    return Bridge._how_to_reach(stand_in, "GO AHEAD", actions)


def test_it_names_the_key_the_pilot_actually_bound():
    """Telling someone to press Page Up when they bound Home is worse than
    telling them nothing."""
    assert "Page Up" in _advice({"ground": "page_up"}, QUIET)
    assert "Home" in _advice({"ground": "home"}, QUIET)
    assert "Ctrl + G" in _advice({"ground": "ctrl+g"}, QUIET)


def test_with_no_key_bound_it_says_where_to_bind_one():
    said = _advice({}, QUIET)
    assert "GROUND CREW" in said and "panel" in said
    assert "your" not in said, "it invented a key binding"


def test_it_does_not_point_at_a_way_in_that_is_not_on_screen():
    """The advice sends the pilot looking for a button. If that button is
    not there either, it sends them looking for nothing."""
    without = [n for n in QUIET if "GROUND CREW" not in n]
    said = _advice({"ground": "page_up"}, without)
    assert "Page Up" not in said and "Open" not in said


def test_every_submenu_it_names_is_one_a_key_can_open():
    """The advice offers to make it a single keypress, so the action it
    names has to be one the hotkey card really offers."""
    from slcvoiceai.app import Bridge
    from slcvoiceai.hotkeys import ACTIONS

    for button, action in Bridge.BEHIND_A_SUBMENU.items():
        assert action in ACTIONS, (
            "{b} is said to be behind {a}, which is not a bindable action"
            .format(b=button, a=action))


def test_one_button_and_a_bad_match_does_not_crash():
    """SLC offers a single button often enough - a submenu showing only BACK -
    and with one candidate the runner-up score is 0.0, so the margin equals
    the score. A Polish sentence scoring 0.03 against 'BACK' therefore looked
    like a tie, and the tie message reached for a second candidate that was
    not there. The command was lost with a traceback.

    Found while watching a real test session on 2026-09-25.
    """
    from slcvoiceai.intent import FuzzyRouter

    class One:
        name = "BACK"
        window = "Self-Loading Cargo"

    decision = FuzzyRouter(min_confidence=0.65).decide(
        "przygotujcie kabinę do lądowania proszę", [One()])
    assert decision.action_index is None
    assert decision.gave_up == "nothing_close", (
        "a lone poor candidate is a weak match, not a tie")

def _advice_for(missing, bindings, names):
    """As _advice, for a button other than GO AHEAD."""
    import types

    from slcvoiceai.app import Bridge

    stand_in = types.SimpleNamespace(
        cfg=types.SimpleNamespace(hotkeys=bindings),
        BEHIND_A_SUBMENU=Bridge.BEHIND_A_SUBMENU)
    actions = [types.SimpleNamespace(name=n) for n in names]
    return Bridge._how_to_reach(stand_in, missing, actions)


def test_the_intercom_calls_get_the_same_help_as_the_ground_ones():
    """On 2026-09-25 "Hello" was refused with "that button is not being
    offered", while the identical situation on the ground channel said "open
    GROUND CREW first - your Page Up". The information was there both times;
    only one message had been taught to use it.
    """
    on_screen = ["INTERCOM >", "P A SYSTEM >", "PHONE >", "BACK"]
    said = _advice_for("HELLO", {"intercom": "home"}, on_screen)
    assert "INTERCOM" in said and "Home" in said


def test_a_button_behind_no_menu_still_says_only_what_it_knows():
    assert "not being offered" in _advice_for(
        "SOME OTHER BUTTON", {"intercom": "home"}, ["INTERCOM >"])

