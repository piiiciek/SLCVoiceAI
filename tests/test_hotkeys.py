"""Keys bound straight to an SLC button.

A hotkey stands in for a physical button on the overhead panel, and that
sets the bar: it does the same thing every time or it does nothing. The
fuzzy matcher is right for speech, where the pilot hears the result and
can say it again - it is wrong here, so most of these tests are about
refusing rather than matching.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slcvoiceai import config as config_module  # noqa: E402
from slcvoiceai.keys import parse_key  # noqa: E402


class FakeAction:
    """Stands in for an SLC control. Records whether it was pressed."""

    def __init__(self, name, automation_id="", control_type="Button"):
        self.name = name
        self.automation_id = automation_id
        self.control_type = control_type
        self.tooltip = ""
        self.window = "Self Loading Cargo"
        self.presses = 0

    def invoke(self):
        self.presses += 1


# Real names, taken from the button lists logged during actual flights.
REAL = ["INTERCOM >", "GROUND CREW >", "P A SYSTEM >", "PHONE >",
        "PURSER TO INTERCOM", "CABIN CREW ARE CALLING...", "START BOARDING",
        "DISREGARD", "ROGER", "STANDBY"]


def bridge_with(actions, **behaviour):
    """A Bridge that reads `actions` instead of SLC, built without touching
    UI Automation, a microphone or a speech model."""
    from slcvoiceai.app import Bridge

    cfg = config_module.Config()
    for name, value in behaviour.items():
        setattr(cfg.behaviour, name, value)

    bridge = Bridge.__new__(Bridge)     # no Whisper, no router, no COM
    bridge.cfg = cfg
    bridge._last_withheld = None

    class FakeUI:
        def list_actions(self_inner, **kwargs):
            return list(actions)

    bridge.ui = FakeUI()
    return bridge


# -- finding the right button ---------------------------------------------

@pytest.mark.parametrize("wanted, expected", [
    ("INTERCOM", "INTERCOM >"),          # the decoration SLC adds
    ("intercom", "INTERCOM >"),          # case does not matter
    ("  INTERCOM  ", "INTERCOM >"),
    ("GROUND CREW", "GROUND CREW >"),
    ("P A SYSTEM", "P A SYSTEM >"),
    ("START BOARDING", "START BOARDING"),
])
def test_a_binding_finds_the_button_it_names(wanted, expected):
    actions = [FakeAction(n) for n in REAL]
    found = bridge_with(actions).find_named(actions, wanted)
    assert found is not None and found.name == expected


def test_a_name_nobody_offers_finds_nothing():
    actions = [FakeAction(n) for n in REAL]
    assert bridge_with(actions).find_named(actions, "LAND THE AIRCRAFT") is None


def test_an_ambiguous_name_presses_nothing():
    """The whole reason this is not the fuzzy matcher. Two buttons fit, so
    a key that is standing in for a physical switch does nothing rather
    than picking one."""
    actions = [FakeAction("INTERCOM >"), FakeAction("PURSER TO INTERCOM")]
    bridge = bridge_with(actions)
    assert bridge.find_named(actions, "INTERCOM") is not None, (
        "an exact match must still win over a mere substring")

    both = [FakeAction("CREW REST"), FakeAction("CREW MEALS")]
    assert bridge_with(both).find_named(both, "CREW") is None


def test_an_exact_match_beats_a_longer_one():
    actions = [FakeAction("PHONE >"), FakeAction("ANSWER THE PHONE NOW")]
    found = bridge_with(actions).find_named(actions, "PHONE")
    assert found.name == "PHONE >"


def test_an_empty_binding_finds_nothing():
    actions = [FakeAction(n) for n in REAL]
    assert bridge_with(actions).find_named(actions, "   ") is None


# -- pressing --------------------------------------------------------------

def test_the_named_button_is_the_one_pressed():
    actions = [FakeAction(n) for n in REAL]
    bridge_with(actions).press_named("insert", ("INTERCOM",))
    pressed = [a.name for a in actions if a.presses]
    assert pressed == ["INTERCOM >"]


def test_a_sequence_presses_in_order():
    actions = [FakeAction("GROUND CREW >"), FakeAction("START BOARDING")]
    bridge_with(actions).press_named("end", ("GROUND CREW", "START BOARDING"))
    assert [a.presses for a in actions] == [1, 1]


def test_a_sequence_stops_at_the_first_thing_it_cannot_find():
    actions = [FakeAction("GROUND CREW >"), FakeAction("START BOARDING")]
    bridge_with(actions).press_named("end", ("NOT A BUTTON", "START BOARDING"))
    assert [a.presses for a in actions] == [0, 0], (
        "it carried on after losing its place in the menu")


def test_dry_run_presses_nothing():
    actions = [FakeAction(n) for n in REAL]
    bridge_with(actions, dry_run=True).press_named("insert", ("INTERCOM",))
    assert not any(a.presses for a in actions)


def test_nothing_on_offer_is_survivable():
    bridge_with([]).press_named("insert", ("INTERCOM",))   # must not raise


def test_a_button_that_throws_does_not_take_the_bridge_down():
    class Exploding(FakeAction):
        def invoke(self):
            raise RuntimeError("SLC went away")

    actions = [Exploding("INTERCOM >")]
    bridge_with(actions).press_named("insert", ("INTERCOM",))   # must not raise


def test_a_hotkey_cannot_press_something_that_would_end_the_flight():
    """The spoken path withholds these once a flight is underway. A key
    bound to one is still a key that would end the flight."""
    from slcvoiceai.slc_ui import FLIGHT_STARTER_IDS

    starter = FakeAction("Start New Passenger Flight", FLIGHT_STARTER_IDS[0])
    actions = [FakeAction("INTERCOM >"), starter]

    bridge = bridge_with(actions)
    bridge._without_flight_enders = lambda acts, flight: [
        a for a in acts if a is not starter]        # stands in for "in flight"

    bridge.press_named("insert", ("Start New Passenger Flight",))
    assert starter.presses == 0


# -- the listener ----------------------------------------------------------

def test_the_keys_in_the_example_config_all_parse():
    for name in ("insert", "delete", "home", "end", "scroll_lock"):
        assert parse_key(name, "hotkeys." + name) is not None


def test_an_unusable_key_name_says_which_binding_it_was():
    """With several bindings in one file, "unrecognised key" without a name
    means reading all of them."""
    with pytest.raises(ValueError) as raised:
        parse_key("plugh", "hotkeys.plugh")
    assert "hotkeys.plugh" in str(raised.value)


def test_a_press_does_not_run_on_the_listener_thread():
    """pynput's Windows listener is a low-level keyboard hook. Reading SLC
    takes seconds, and doing that on the hook thread makes every keystroke
    on the machine late."""
    from slcvoiceai.hotkeys import Hotkeys

    ran_on = []
    done = threading.Event()

    def slow(label, buttons):
        ran_on.append(threading.current_thread().name)
        time.sleep(0.2)
        done.set()

    listener = Hotkeys({"insert": ("INTERCOM",)}, slow)
    here = threading.current_thread().name

    started = time.time()
    listener._on_press(parse_key("insert"))
    assert time.time() - started < 0.1, "the press blocked its caller"

    assert done.wait(3), "the binding never ran"
    assert ran_on and ran_on[0] != here


def test_holding_a_key_fires_once_not_once_per_repeat():
    from slcvoiceai.hotkeys import Hotkeys

    fired = []
    listener = Hotkeys({"insert": ("INTERCOM",)},
                       lambda label, buttons: fired.append(label))
    key = parse_key("insert")

    for _ in range(5):            # auto-repeat while the key is held
        listener._on_press(key)
    time.sleep(0.3)
    assert fired == ["insert"]

    listener._on_release(key)
    listener._on_press(key)
    time.sleep(0.3)
    assert fired == ["insert", "insert"], "a real second press was swallowed"


def test_a_key_still_working_ignores_another_press():
    """A scan outlasts a press, and nobody wants a queue of clicks."""
    from slcvoiceai.hotkeys import Hotkeys

    fired = []
    release = threading.Event()

    def slow(label, buttons):
        fired.append(label)
        release.wait(2)

    listener = Hotkeys({"insert": ("INTERCOM",)}, slow)
    key = parse_key("insert")

    listener._on_press(key)
    time.sleep(0.1)
    listener._on_release(key)
    listener._on_press(key)      # a genuine second press, while still busy
    time.sleep(0.1)
    assert fired == ["insert"]
    release.set()


def test_an_unbound_key_is_ignored():
    from slcvoiceai.hotkeys import Hotkeys

    fired = []
    listener = Hotkeys({"insert": ("INTERCOM",)},
                       lambda label, buttons: fired.append(label))
    listener._on_press(parse_key("delete"))
    time.sleep(0.2)
    assert fired == []


def test_a_binding_that_throws_does_not_wedge_the_key():
    from slcvoiceai.hotkeys import Hotkeys

    calls = []

    def explode(label, buttons):
        calls.append(label)
        raise RuntimeError("no")

    listener = Hotkeys({"insert": ("INTERCOM",)}, explode)
    key = parse_key("insert")

    listener._on_press(key)
    time.sleep(0.2)
    listener._on_release(key)
    listener._on_press(key)
    time.sleep(0.2)
    assert calls == ["insert", "insert"], "the key stayed marked as busy"


# -- reading the config ----------------------------------------------------

def write(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_binding_reads_as_a_one_step_sequence(tmp_path):
    cfg = config_module.load(write(tmp_path, '[hotkeys]\ninsert = "INTERCOM"\n'))
    assert cfg.hotkeys == {"insert": ("INTERCOM",)}


def test_a_list_reads_as_a_sequence(tmp_path):
    cfg = config_module.load(write(
        tmp_path, '[hotkeys]\nend = ["GROUND CREW", "START BOARDING"]\n'))
    assert cfg.hotkeys == {"end": ("GROUND CREW", "START BOARDING")}


def test_key_names_are_case_insensitive(tmp_path):
    cfg = config_module.load(write(tmp_path, '[hotkeys]\nInsert = "INTERCOM"\n'))
    assert "insert" in cfg.hotkeys


def test_no_hotkeys_section_is_normal(tmp_path):
    assert config_module.load(write(tmp_path, '[behaviour]\n')).hotkeys == {}


@pytest.mark.parametrize("body", [
    '[hotkeys]\ninsert = 5\n',
    '[hotkeys]\ninsert = []\n',
    '[hotkeys]\ninsert = ""\n',
    '[hotkeys]\ninsert = ["OK", 7]\n',
    '[hotkeys]\ninsert = true\n',
])
def test_a_binding_that_is_not_a_button_name_is_refused(tmp_path, body):
    """Better a message at startup than a key that silently does nothing."""
    with pytest.raises(ValueError):
        config_module.load(write(tmp_path, body))


def test_the_example_config_documents_the_section():
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert "[hotkeys]" in text
    assert "INTERCOM" in text, "the example does not show a real button name"
