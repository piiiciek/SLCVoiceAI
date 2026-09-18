"""The six buttons that throw a flight away.

SLC keeps them on the toolbar for the whole flight, a few icons along from
the seatbelt sign, and pressing one ends the flight. They cannot simply be
denylisted - at SLC's launcher they are the ordinary way to begin - so the
bridge has to know whether there is a flight to lose, and has to be careful
about what it does when it cannot tell.

They also cannot be recognised by their labels. One of them is displayed as
'LINE PILOT MODE'.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai import app  # noqa: E402
from slcvoiceai.context import flight_is_underway  # noqa: E402
from slcvoiceai.slc_ui import starts_a_new_flight  # noqa: E402


class FakeAction:
    def __init__(self, name, automation_id="", tooltip=""):
        self.name = name
        self.automation_id = automation_id
        self.tooltip = tooltip

    def __repr__(self):
        return "FakeAction({n!r})".format(n=self.name)


def bridge():
    return app.Bridge.__new__(app.Bridge)


#: Every flight-starting control SLC v1.6.7.3 has, captured from a live
#: scan, with the ids and tooltips it reports for them.
STARTERS = [
    ("Start New Single Flight", "cmdStartNewSingleFlight", ""),
    ("Start New Passenger Flight", "cmdStartNewPassengerFlight",
     "Start New Airliner Flight With Full Captain/Crew Interactivity"),
    ("Start New Simple Passenger Flight", "cmdStartNewSimplePassengerFlight",
     "Start New Airliner Flight With Cabin Crew Announcements"),
    ("LINE PILOT MODE", "cmdStartNewPassengerLineFlight",
     "Start New Line Pilot Flight"),
    ("Start New Cargo Flight", "cmdStartNewCargoFlight",
     "Start New Cargo Flight"),
    ("Restore Previous Flight", "cmdRestorePreviousFlight",
     "Start New Airliner Flight With Full Captain/Crew Interactivity"),
]

SAFE = [
    ("Seatbelts", "cmdSeatbelts", "Toggle Seatbelts"),
    ("Toggle Doors", "cmdToggleDoors", "Toggle Aircraft Doors"),
    ("Tannoy", "cmdTannoy", "Address The Cabin"),
    ("GROUND CREW >", "cmdPlayCaptainCockpitToGround",
     "Show Ground Crew PA Options"),
    ("ROGER", "cmdPlayCaptainRoger", ""),
    ("START BOARDING WHEN READY", "cmdPlayCaptainBoardingStartBoarding",
     "Play the 'Start boarding whenever you're ready' response"),
    ("READY TO START BOARDING", "cmdPlayCaptainReadyToBoard",
     "Play the 'Start Boarding' notification from Captain"),
    ("Flight Rating Window", "cmdFlightRatingWindow",
     "Open Flight Rating Window"),
    ("Check List", "cmdCheckList", "Open Scoring Checklist"),
]

TOOLBAR = ([FakeAction(*s) for s in SAFE[:3]]
           + [FakeAction(*STARTERS[1]), FakeAction(*STARTERS[3])]
           + [FakeAction(*s) for s in SAFE[3:5]])


def names(actions):
    return [a.name for a in actions]


# -- naming the risky ones ------------------------------------------------

@pytest.mark.parametrize("name,ident,tooltip", STARTERS)
def test_every_flight_starter_is_recognised(name, ident, tooltip):
    assert starts_a_new_flight(name, ident, tooltip)


def test_the_one_that_hides_behind_its_label():
    """'LINE PILOT MODE' says nothing at all about starting a flight, and
    matching on labels would sail straight past it. Its id does say so, and
    an id does not change with the Windows display language."""
    assert starts_a_new_flight("LINE PILOT MODE",
                               "cmdStartNewPassengerLineFlight", "")


def test_the_id_alone_is_enough():
    """SLC could relabel these tomorrow; the ids have been stable."""
    for _name, ident, _tooltip in STARTERS:
        assert starts_a_new_flight("", ident, ""), ident


def test_the_label_alone_is_enough():
    """And if the ids ever change, the wording is a second chance."""
    assert starts_a_new_flight("Start New Cargo Flight", "cmdWhatever", "")


@pytest.mark.parametrize("name,ident,tooltip", SAFE)
def test_ordinary_buttons_are_not(name, ident, tooltip):
    """'START BOARDING WHEN READY' and 'READY TO START BOARDING' are the
    trap here: they are about starting something, and they are exactly the
    kind of thing the bridge exists to press."""
    assert not starts_a_new_flight(name, ident, tooltip)


# -- reading the flight state ---------------------------------------------

def test_no_export_means_no_answer():
    """Not False. The distinction is the whole point of the guard."""
    assert flight_is_underway({}) is None


def test_a_flight_number_means_a_flight():
    assert flight_is_underway({"SLC_flightNumber": "LO282"}) is True


def test_a_route_means_a_flight():
    assert flight_is_underway({"SLC_departureAirportICAO": "EPWA"}) is True


def test_a_status_means_a_flight():
    assert flight_is_underway({"SLC_flightStatus": "Boarding"}) is True


def test_exporting_with_the_flight_fields_empty_means_no_flight():
    assert flight_is_underway({
        "SLC_flightStatus": "",
        "SLC_flightNumber": "  ",
        "SLC_aircraft": "A320",
    }) is False


def test_fields_that_say_nothing_about_a_flight_do_not_count():
    """SLC keeps exporting the aircraft and the airline between flights."""
    assert flight_is_underway({"SLC_aircraft": "A320",
                               "SLC_airline": "LOT"}) is False


# -- the guard ------------------------------------------------------------

def test_they_are_hidden_during_a_flight():
    actions = list(TOOLBAR)
    kept = bridge()._without_flight_enders(
        actions, {"SLC_flightNumber": "LO282"})
    assert "Start New Passenger Flight" not in names(kept)
    assert "LINE PILOT MODE" not in names(kept), (
        "the one whose label gives nothing away survived the guard")


def test_they_are_hidden_when_slc_will_not_say():
    """The asymmetry: a pilot who wanted one can click it, a pilot who loses
    a flight to a misheard sentence cannot get it back."""
    actions = list(TOOLBAR)
    kept = bridge()._without_flight_enders(actions, {})
    assert "Start New Passenger Flight" not in names(kept)


def test_they_are_offered_when_there_is_no_flight_to_lose():
    actions = list(TOOLBAR)
    kept = bridge()._without_flight_enders(
        actions, {"SLC_flightStatus": "", "SLC_aircraft": "A320"})
    assert "Start New Passenger Flight" in names(kept)


def test_everything_else_is_left_alone():
    actions = list(TOOLBAR)
    kept = bridge()._without_flight_enders(actions, {})
    assert names(kept) == ["Seatbelts", "Toggle Doors", "Tannoy",
                           "GROUND CREW >", "ROGER"]


def test_the_list_is_untouched_when_they_are_not_on_it():
    """The common case - mid-flight with the comms popup open. It must not
    cost a copy, a log line, or a surprise."""
    actions = [FakeAction("Seatbelts"), FakeAction("ROGER")]
    kept = bridge()._without_flight_enders(actions, {})
    assert kept is actions


def test_an_empty_list_survives_the_guard():
    assert bridge()._without_flight_enders([], {}) == []


def test_the_pilot_is_told_why(caplog):
    actions = list(TOOLBAR)
    with caplog.at_level("INFO"):
        bridge()._without_flight_enders(actions, {"SLC_flightNumber": "LO282"})
    assert "a flight is in progress" in caplog.text


def test_the_pilot_is_told_how_to_get_them_back(caplog):
    actions = list(TOOLBAR)
    with caplog.at_level("INFO"):
        bridge()._without_flight_enders(actions, {})
    assert "stream_export_dir" in caplog.text
