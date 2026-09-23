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
