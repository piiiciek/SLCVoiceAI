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


# -- binding a key from inside the panel -----------------------------------

@pytest.mark.parametrize("code, expected", [
    ("Insert", "insert"),
    ("Delete", "delete"),
    ("Home", "home"),
    ("End", "end"),
    ("ScrollLock", "scroll_lock"),
    ("PageUp", "page_up"),
    ("ControlRight", "ctrl_r"),
    ("Escape", "esc"),
    ("KeyQ", "q"),
    ("Digit7", "7"),
    ("F13", "f13"),
    ("NumpadEnter", "enter"),
])
def test_a_key_pressed_in_the_panel_becomes_a_config_name(code, expected):
    from slcvoiceai.keys import from_browser_code

    assert from_browser_code(code) == expected


@pytest.mark.parametrize("code", ["", "Unidentified", "F99", "MediaPlayPause",
                                  "Numpad5", "KeyAB", None])
def test_a_key_that_cannot_be_bound_says_so(code):
    """Better to refuse at the keypress than to write a binding that fails
    to arm at the next start, where nobody would connect the two."""
    from slcvoiceai.keys import from_browser_code

    assert from_browser_code(code) is None


@pytest.mark.parametrize("code", ["Insert", "Delete", "Home", "End", "F13", "KeyQ"])
def test_every_capturable_key_is_one_the_listener_accepts(code):
    """The two halves have to agree, or the panel offers a binding that
    then cannot be armed."""
    from slcvoiceai.keys import from_browser_code

    assert parse_key(from_browser_code(code), "hotkeys") is not None


def panel_with(tmp_path, body='[audio]\nptt_key = "scroll_lock"\n'):
    from slcvoiceai.gui import App

    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return App(config_module.load(path))


def test_the_panel_saves_what_it_was_given(tmp_path):
    panel = panel_with(tmp_path)
    answer = panel.save_hotkeys([{"key": "insert", "buttons": "INTERCOM"}])

    assert answer["error"] == ""
    assert answer["rows"] == [{"key": "insert", "buttons": "INTERCOM"}]
    # ...and it is on disk, not just in memory.
    assert config_module.load(panel.cfg.source).hotkeys == {
        "insert": ("INTERCOM",)}


def test_a_sequence_typed_with_pipes_is_stored_as_steps(tmp_path):
    panel = panel_with(tmp_path)
    panel.save_hotkeys([{"key": "end", "buttons": "GROUND CREW | START BOARDING"}])
    assert config_module.load(panel.cfg.source).hotkeys == {
        "end": ("GROUND CREW", "START BOARDING")}


def test_a_button_name_containing_a_comma_survives(tmp_path):
    """SLC really does offer "GSX, START CATERING" - which is why steps are
    separated with a pipe and not a comma."""
    panel = panel_with(tmp_path)
    panel.save_hotkeys([{"key": "insert", "buttons": "GSX, START CATERING"}])
    assert config_module.load(panel.cfg.source).hotkeys == {
        "insert": ("GSX, START CATERING",)}


def test_removing_a_binding_removes_it_from_the_file(tmp_path):
    panel = panel_with(tmp_path)
    panel.save_hotkeys([{"key": "insert", "buttons": "INTERCOM"},
                        {"key": "delete", "buttons": "GROUND CREW"}])
    panel.save_hotkeys([{"key": "insert", "buttons": "INTERCOM"}])
    assert config_module.load(panel.cfg.source).hotkeys == {
        "insert": ("INTERCOM",)}


def test_binding_the_push_to_talk_key_is_refused(tmp_path):
    """It would fire a hotkey every time the pilot spoke."""
    panel = panel_with(tmp_path)
    answer = panel.save_hotkeys([{"key": "scroll_lock", "buttons": "INTERCOM"}])
    assert answer["error"], "it accepted the talk key"
    assert answer["rows"] == [], "it saved it anyway"


def test_capturing_the_push_to_talk_key_is_refused_at_the_keypress(tmp_path):
    panel = panel_with(tmp_path)
    assert panel.capture_key("ScrollLock")["error"]
    assert panel.capture_key("ScrollLock")["key"] == ""
    assert panel.capture_key("Insert") == {"key": "insert", "error": ""}


def test_an_unusable_key_is_refused_at_the_keypress(tmp_path):
    assert panel_with(tmp_path).capture_key("MediaPlayPause")["error"]


def test_a_half_filled_row_is_ignored_rather_than_refused(tmp_path):
    """A row exists from the moment Add is clicked, before a key has been
    pressed. That is not an error the pilot should be told about."""
    panel = panel_with(tmp_path)
    answer = panel.save_hotkeys([{"key": "insert", "buttons": "INTERCOM"},
                                 {"key": "", "buttons": ""}])
    assert answer["error"] == ""
    assert answer["rows"] == [{"key": "insert", "buttons": "INTERCOM"}]


def test_the_same_key_twice_is_refused(tmp_path):
    panel = panel_with(tmp_path)
    answer = panel.save_hotkeys([{"key": "insert", "buttons": "INTERCOM"},
                                 {"key": "insert", "buttons": "GROUND CREW"}])
    assert answer["error"]


def test_the_answer_always_carries_what_is_really_bound(tmp_path):
    """The page redraws from this, so a refused edit cannot linger on
    screen looking as though it saved."""
    panel = panel_with(tmp_path)
    panel.save_hotkeys([{"key": "insert", "buttons": "INTERCOM"}])
    refused = panel.save_hotkeys([{"key": "scroll_lock", "buttons": "X"}])
    assert refused["rows"] == [{"key": "insert", "buttons": "INTERCOM"}]


def test_saving_without_a_config_on_disk_does_not_explode():
    """A Config nobody loaded has nowhere to write. The binding still has
    to take effect for this session."""
    from slcvoiceai.gui import App

    panel = App(config_module.Config())
    answer = panel.save_hotkeys([{"key": "insert", "buttons": "INTERCOM"}])
    assert answer["error"] == ""
    assert panel.cfg.hotkeys == {"insert": ("INTERCOM",)}


def test_the_comment_block_in_the_section_survives_an_edit(tmp_path):
    """config.example.toml explains the section in a dozen lines. Editing a
    binding in the panel must not wipe them."""
    path = tmp_path / "config.toml"
    path.write_text('[audio]\nptt_key = "scroll_lock"\n\n'
                    '[hotkeys]\n# how this works\n# and some more\n'
                    'insert = "INTERCOM"  # the ATT button\n', encoding="utf-8")
    from slcvoiceai.gui import App

    panel = App(config_module.load(path))
    panel.save_hotkeys([{"key": "insert", "buttons": "PHONE"},
                        {"key": "delete", "buttons": "GROUND CREW"}])

    after = path.read_text(encoding="utf-8")
    assert "# how this works" in after
    assert "# and some more" in after
    assert "# the ATT button" in after, "the note on the surviving key was lost"
    assert 'insert = "PHONE"' in after


def test_the_example_config_documents_the_section():
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert "[hotkeys]" in text
    assert "INTERCOM" in text, "the example does not show a real button name"
