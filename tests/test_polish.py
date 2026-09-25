"""Polish reaching an English button without passing through a translator.

The bridge used to ask Whisper to translate, because SLC's buttons are
English and string similarity compares spelling. That put a lossy step in
front of every command: "mozecie tankowac" came back as "boarding", as "can
you pass the bus to tank?", and as "you can refuel" on three different days.

BlueLine Realism does not translate - its Polish pack maps Polish straight to
the command - and the only thing stopping SLCVoiceAI from doing the same was
mechanical. normalise() replaced anything outside [a-z0-9 ] with a space, so
"mozecie" arrived as "mo ecie" and no Polish entry could ever have fired.

These tests pin both halves: that accented letters survive normalisation, and
that the Polish the pilot actually speaks reaches the button they mean.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai.intent import FuzzyRouter, fold, normalise  # noqa: E402


@dataclass
class FakeAction:
    name: str
    window: str = "Self-Loading Cargo"


#: Two real offer lists from the flight of 2026-09-23, with the panel
#: furniture stripped as slc_ui.is_chrome now strips it.
GATE = ["GROUND CREW >", "INTERCOM >", "P A SYSTEM >", "PHONE >",
        "CONNECT JETWAY", "GSX, START REFUELLING", "DISCONNECT JETWAY",
        "REQUEST LOADING UPDATE", "STARTING THE APU", "RADIO CHECK",
        "STANDBY", "DISREGARD", "BACK", "GSX, START BOARDING",
        "GSX, START CATERING", "PARKING BRAKE IS SET", "Seatbelts"]
CREW = ["ROGER", "THANK YOU", "GO AHEAD", "HELLO?", "STANDBY", "DISREGARD",
        "LOUD AND CLEAR", "I'M FEELING FINE", "BACK", "SEATS FOR TAKEOFF",
        "BE SEATED FOR TAKEOFF NOW", "PREPARE CABIN FOR LANDING",
        "RELEASE THE CABIN CREW", "LADIES AND GENTLEMEN", "WELCOME ABOARD"]


#: The offer lists exactly as SLC showed them during the ground test of
#: 2026-09-25, furniture already stripped.
CABIN_25 = ["Toggle Door Mode", "Seatbelts", "Toggle Doors", "INTERCOM >",
            "P A SYSTEM >", "PHONE >", "HOW'S IT GOING?",
            "HOW LONG UNTIL BOARDING?", "TURN THE MUSIC ON",
            "TURN THE MUSIC OFF", "TURN THE MUSIC UP", "TURN THE MUSIC DOWN",
            "CAN I HAVE SOME TEA?", "CAN I HAVE SOME COFFEE?",
            "CAN I HAVE SOME WATER?", "PURSER TO INTERCOM", "BACK"]
GROUND_25 = ["GROUND CREW >", "GO AHEAD", "ROGER", "LOUD AND CLEAR",
             "REPEAT TRANSMISSION", "STANDBY", "DISREGARD", "BACK",
             "CONNECT JETWAY", "DISCONNECT JETWAY", "I'LL ASK FOR A JETWAY",
             "Toggle Door Mode", "Toggle Doors"]

def route(said: str, buttons: list[str]) -> str | None:
    actions = [FakeAction(b) for b in buttons]
    decision = FuzzyRouter(min_confidence=0.65).decide(said, actions)
    if decision.action_index is None:
        return None
    return actions[decision.action_index].name


# -- the letters ------------------------------------------------------------

@pytest.mark.parametrize("said,want", [
    ("możecie tankować", "mozecie tankowac"),
    ("głośno i wyraźnie", "glosno wyraznie"),   # "i" is filler, as in English
    ("zrozumiałem", "zrozumialem"),
    ("przygotujcie kabinę", "przygotujcie kabine"),
    ("żółw", "zolw"),
])
def test_polish_letters_survive_normalisation(said, want):
    """Before fold(), 'mozecie' came out as 'mo ecie' - two words, neither of
    them a word. Every Polish entry in the alias table would have been dead
    on arrival."""
    assert normalise(said) == want


def test_l_with_stroke_is_not_a_letter_plus_a_mark():
    """NFKD takes apart z-with-dot but not l-with-stroke, which is a letter
    in its own right - so it needs saying explicitly, or 'slucham' stays
    broken while 'zrozumialem' works and the bug looks random."""
    assert fold("ł") == "l"
    assert fold("żółw") == "zolw"


def test_english_is_untouched():
    """The table above this one is still English and still has to work."""
    assert normalise("I'm listening") == "im listening"
    assert normalise("GSX, START REFUELLING", spoken=False) == \
        "gsx start refuelling"


# -- the words --------------------------------------------------------------

@pytest.mark.parametrize("said,want", [
    # the one that started it: said on 2026-09-23 at 12:00, heard as
    # "can you pass the bus to tank?", routed to catering
    ("możecie tankować", "GSX, START REFUELLING"),
    ("tankujcie", "GSX, START REFUELLING"),
    ("zaczynajcie boarding", "GSX, START BOARDING"),
    ("jak idzie załadunek", "REQUEST LOADING UPDATE"),
    ("próba radia", "RADIO CHECK"),
    ("hamulec zaciągnięty", "PARKING BRAKE IS SET"),
    ("uruchamiam apu", "STARTING THE APU"),
    ("czekajcie", "STANDBY"),
    ("nieważne", "DISREGARD"),
    ("zapnijcie pasy", "Seatbelts"),
])
def test_polish_reaches_the_gate_buttons(said, want):
    assert route(said, GATE) == want


@pytest.mark.parametrize("said,want", [
    ("zrozumiałem", "ROGER"),
    ("rozumiem", "ROGER"),
    ("dziękuję", "THANK YOU"),
    # "prosze mowic" was refused four times in one flight as "you can say"
    ("słucham", "GO AHEAD"),
    ("możecie mówić", "GO AHEAD"),
    ("proszę mówić", "GO AHEAD"),
    ("głośno i wyraźnie", "LOUD AND CLEAR"),
    ("pięć na pięć", "LOUD AND CLEAR"),
    ("wszystko w porządku", "I'M FEELING FINE"),
    ("przygotujcie kabinę do lądowania", "PREPARE CABIN FOR LANDING"),
    ("zwalniam załogę", "RELEASE THE CABIN CREW"),
    ("witamy na pokładzie", "WELCOME ABOARD"),
])
def test_polish_reaches_the_cabin_buttons(said, want):
    assert route(said, CREW) == want


# -- the pairs that must never swap ----------------------------------------

@pytest.mark.parametrize("said,want", [
    ("podłączcie rękaw", "CONNECT JETWAY"),
    ("podłącz rękaw", "CONNECT JETWAY"),
    ("odłączcie rękaw", "DISCONNECT JETWAY"),
    ("odłącz rękaw", "DISCONNECT JETWAY"),
])
def test_connect_and_disconnect_do_not_swap_in_polish(said, want):
    """In English these differ by a whole word. In Polish they differ by a
    prefix - podlacz / odlacz - and both sit in the same offer list.
    'odlaczyc' was misread as 'connect' on 2026-09-23 at 12:15."""
    assert route(said, GATE) == want


@pytest.mark.parametrize("said,want", [
    ("zajmijcie miejsca", "SEATS FOR TAKEOFF"),
    ("zaraz startujemy", "BE SEATED FOR TAKEOFF NOW"),
    ("siadajcie", "BE SEATED FOR TAKEOFF NOW"),
])
def test_asking_for_places_and_telling_people_to_sit_stay_apart(said, want):
    """These two buttons mean nearly the same thing and SLC offers both, so
    the two sets of Polish words are kept disjoint on purpose. token_set_ratio
    scores a subset as a perfect match, so a single phrase appearing in both
    families ties them at 1.00 and the command is refused - which is exactly
    what happened the first time this table was written."""
    assert route(said, CREW) == want


def test_a_phrase_with_no_entry_is_still_refused():
    """Adding a language must not turn the matcher into a guesser."""
    assert route("powtórzcie", CREW) is None

# -- what the ground test of 2026-09-25 taught -----------------------------

@pytest.mark.parametrize("said,want", [
    # Every one of these went to the cloud and came back three seconds later.
    # The offline layer had the meaning; it did not have the wording.
    ("Czy mogę poprosić się o herbatę?", "CAN I HAVE SOME TEA?"),
    ("Poproszę kogoś do interkomu", "PURSER TO INTERCOM"),
    ("Możecie włączyć muzykę?", "TURN THE MUSIC ON"),
])
def test_politeness_and_an_infinitive_reach_the_button(said, want):
    """He does not give orders in the imperative, he asks: "mozecie wlaczyc",
    "czy moge poprosic". The table was written the other way round."""
    assert route(said, CABIN_25) == want


@pytest.mark.parametrize("said,want", [
    ("Czysto i wyraźnie", "LOUD AND CLEAR"),
    ("Okej, zrozumiałem", "ROGER"),
    ("dobra, zrozumiałem", "ROGER"),
    ("Możecie podłączyć jetway?", "CONNECT JETWAY"),
])
def test_more_of_the_same_from_the_ground_test(said, want):
    assert route(said, GROUND_25) == want


def test_okej_is_a_filler_like_ok():
    """"OK, zrozumialem" scored 1.00 and "Okej, zrozumialem" scored 0.50, for
    no better reason than that "ok" happens to be a ROGER alias and its Polish
    twin was not - so one word was accounted for and the other was not."""
    assert route("OK, zrozumiałem", GROUND_25) == "ROGER"
    assert route("Okej, zrozumiałem", GROUND_25) == "ROGER"


@pytest.mark.parametrize("said,want", [
    ("Możecie podłączyć jetway?", "CONNECT JETWAY"),
    ("Możecie odłączyć jetway?", "DISCONNECT JETWAY"),
    ("Możecie włączyć muzykę?", "TURN THE MUSIC ON"),
    ("Możecie wyłączyć muzykę?", "TURN THE MUSIC OFF"),
])
def test_the_politeness_word_does_not_tie_the_pairs(said, want):
    """The first draft of this batch put "mozecie" in both halves of each
    pair. Two words in three are then shared, the two score 1.00 together,
    and the command is refused - which is how the fix would have shipped
    looking like a regression."""
    offer = CABIN_25 if "muzyk" in said else GROUND_25
    assert route(said, offer) == want


def test_polish_filler_leaves_only_what_was_meant():
    assert normalise("Czy mogę poprosić się o herbatę?") == "poprosic herbate"


def test_the_polish_vocabulary_survives_a_full_screen():
    """"do interkomu" came back as "do literkomu", which no alias can rescue -
    the word never arrived. The hint list is where that is fixed, and it has a
    budget, so the Polish has to survive a screen full of button names."""
    from slcvoiceai.vocabulary import build_prompt, load_terms

    live = ("GROUND CREW >", "INTERCOM >", "P A SYSTEM >", "CONNECT JETWAY",
            "GSX, START REFUELLING", "DISCONNECT JETWAY", "STANDBY",
            "REQUEST LOADING UPDATE", "STARTING THE APU", "RADIO CHECK",
            "DISREGARD", "BACK", "ROGER", "GO AHEAD", "LOUD AND CLEAR")
    prompt = build_prompt(load_terms(), live)
    for word in ("interkom", "rękaw", "herbata"):
        assert word in prompt, word

# -- the second ground test, 2026-09-25 ------------------------------------

#: What SLC offered on the ground channel that afternoon.
GROUND_25B = ["GROUND CREW >", "INTERCOM >", "GO AHEAD", "ROGER", "WILL DO",
              "LOUD AND CLEAR", "STANDBY", "DISREGARD", "BACK",
              "CONNECT JETWAY", "DISCONNECT JETWAY", "PLEASE DISCONNECT GPU",
              "STARTING THE APU", "GSX, START BOARDING", "GSX, START CATERING",
              "Toggle Door Mode", "Toggle Doors", "Seatbelts"]
CABIN_25B = ["INTERCOM >", "P A SYSTEM >", "PHONE >", "HOW'S IT GOING?",
             "HOW LONG UNTIL BOARDING?", "INSTANT BOARDING",
             "TURN THE MUSIC ON", "CAN I HAVE SOME TEA?",
             "HOW ARE THE PASSENGERS?", "ROGER", "Toggle Door Mode",
             "Toggle Doors", "BACK"]


def test_a_question_about_boarding_does_not_start_boarding():
    """The one wrong press of the session, and the worst kind: he asked how
    long boarding would take and INSTANT BOARDING was pressed, which starts
    it. Two buttons share the word "boarding"; the shorter name won on the
    single word they have in common - 0.67 against 0.54 - which is a rule
    about string length deciding something about meaning.
    """
    assert route("jak długo będzie boarding", CABIN_25B) == \
        "HOW LONG UNTIL BOARDING?"
    assert route("ile potrwa boarding", CABIN_25B) == "HOW LONG UNTIL BOARDING?"


@pytest.mark.parametrize("said,want", [
    ("OK, poproszę o odłączenie zasilania zewnętrznego",
     "PLEASE DISCONNECT GPU"),
    ("Zaczynamy procedurę włączenia APU", "STARTING THE APU"),
    ("zrozumiano", "ROGER"),
    ("Ok, zrozumiem", "ROGER"),
    ("ok, zrobię to", "WILL DO"),
    ("podłącz jetway", "CONNECT JETWAY"),
])
def test_the_second_ground_test_settles_offline(said, want):
    assert route(said, GROUND_25B) == want


@pytest.mark.parametrize("said,want", [
    ("podłącz jetway", "CONNECT JETWAY"),
    ("odłącz jetway", "DISCONNECT JETWAY"),
    ("możecie podłączyć jetway", "CONNECT JETWAY"),
    ("możecie odłączyć jetway", "DISCONNECT JETWAY"),
])
def test_stemming_did_not_merge_the_jetway_pair(said, want):
    """The stem rule shortens words to five characters, and the whole
    question is whether that is short enough to keep podlacz from odlacz.
    It is - but stemming the *score* as well was tried and was not: it
    lifted CONNECT into a tie with DISCONNECT and refused the command.
    """
    assert route(said, GROUND_25B) == want


@pytest.mark.parametrize("word_a,word_b,same", [
    ("zasilanie", "zasilania", True),
    ("zrozumialem", "zrozumiano", True),
    ("podlaczcie", "podlacz", True),
    ("odlaczcie", "podlacz", False),
    ("wlaczyc", "wylaczyc", False),
    ("herbate", "kawe", False),
    # too short to stem: these must stay exact or everything collides
    ("tak", "tam", False),
    ("apu", "apu", True),
])
def test_the_stem_rule_itself(word_a, word_b, same):
    from slcvoiceai.intent import same_word

    assert same_word(word_a, word_b) is same

