"""Three calls, on three key combinations.

What is bound and what it presses are both fixed - only the keys are the
pilot's. That is the whole design, and most of what is worth testing sits
either side of it: the combination parsing, which has three spellings that
must not drift apart, and the refusals, because a hotkey stands in for a
switch on the overhead and does the same thing every time or nothing.
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
from slcvoiceai import keys  # noqa: E402
from slcvoiceai.hotkeys import ACTIONS  # noqa: E402


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


# -- the three calls hit the buttons they name -----------------------------

def test_there_are_exactly_three_calls():
    """Deliberately not "bind any key to any button". Every extra field
    was another way to end up with a binding pointing at nothing."""
    assert sorted(ACTIONS) == ["ground", "intercom", "pa"]


@pytest.mark.parametrize("action, expected", [
    ("intercom", "INTERCOM >"),
    ("ground", "GROUND CREW >"),
    ("pa", "P A SYSTEM >"),
])
def test_each_call_finds_its_button_among_the_real_ones(action, expected):
    """The names in ACTIONS have to survive normalisation onto what SLC
    actually offers - "P A SYSTEM" is the one that would break if someone
    tidied it to "PA SYSTEM", because the noise filter drops the lone A."""
    actions = [FakeAction(n) for n in REAL]
    bridge = bridge_with(actions)
    found = bridge.find_named(actions, ACTIONS[action][0])
    assert found is not None and found.name == expected


def test_each_call_presses_exactly_one_button():
    for action, buttons in ACTIONS.items():
        actions = [FakeAction(n) for n in REAL]
        bridge_with(actions).press_named(action, buttons)
        pressed = [a.name for a in actions if a.presses]
        assert len(pressed) == 1, "{a} pressed {p}".format(a=action, p=pressed)


def test_an_ambiguous_name_presses_nothing():
    """A key standing in for a physical switch does not guess."""
    both = [FakeAction("CREW REST"), FakeAction("CREW MEALS")]
    assert bridge_with(both).find_named(both, "CREW") is None


def test_dry_run_presses_nothing():
    actions = [FakeAction(n) for n in REAL]
    bridge_with(actions, dry_run=True).press_named("intercom", ACTIONS["intercom"])
    assert not any(a.presses for a in actions)


def test_nothing_on_offer_is_survivable():
    bridge_with([]).press_named("intercom", ACTIONS["intercom"])   # must not raise


def test_a_button_that_throws_does_not_take_the_bridge_down():
    class Exploding(FakeAction):
        def invoke(self):
            raise RuntimeError("SLC went away")

    bridge_with([Exploding("INTERCOM >")]).press_named(
        "intercom", ACTIONS["intercom"])                            # must not raise


def test_a_hotkey_cannot_press_something_that_would_end_the_flight():
    from slcvoiceai.slc_ui import FLIGHT_STARTER_IDS

    starter = FakeAction("Start New Passenger Flight", FLIGHT_STARTER_IDS[0])
    bridge = bridge_with([FakeAction("INTERCOM >"), starter])
    bridge._without_flight_enders = lambda acts, flight: [
        a for a in acts if a is not starter]        # stands in for "in flight"

    bridge.press_named("intercom", ("Start New Passenger Flight",))
    assert starter.presses == 0


# -- combinations ----------------------------------------------------------

@pytest.mark.parametrize("spec, expected", [
    ("ctrl+q", ("ctrl", "q")),
    ("CTRL+Q", ("ctrl", "q")),
    (" ctrl + q ", ("ctrl", "q")),
    ("control+q", ("ctrl", "q")),
    ("win+q", ("cmd", "q")),
    ("shift+ctrl+q", ("ctrl", "shift", "q")),   # written back in a fixed order
    ("ctrl+alt+shift+f13", ("ctrl", "alt", "shift", "f13")),
    ("insert", ("insert",)),
    ("ctrl+insert", ("ctrl", "insert")),
])
def test_a_combination_has_one_spelling(spec, expected):
    """Two bindings that are the same combination must be seen to be, so
    one order and one case wins."""
    assert keys.canonical(spec) == "+".join(expected)


@pytest.mark.parametrize("spec", ["", "   ", "+", "ctrl", "ctrl+", "ctrl+shift",
                                  "ctrl+nonsense", "nonsense", "meta+alt"])
def test_nonsense_is_refused_rather_than_half_understood(spec):
    with pytest.raises(ValueError):
        keys.canonical(spec)


@pytest.mark.parametrize("spec, expected", [
    ("ctrl+q", "<ctrl>+q"),
    ("ctrl+shift+q", "<ctrl>+<shift>+q"),
    ("insert", "<insert>"),
    ("ctrl+f13", "<ctrl>+<f13>"),
])
def test_pynput_gets_the_spelling_it_parses(spec, expected):
    from pynput.keyboard import HotKey

    assert keys.to_pynput(spec) == expected
    assert HotKey.parse(expected), "pynput cannot read what we hand it"


@pytest.mark.parametrize("spec, shown", [
    ("ctrl+q", "Ctrl + Q"),
    ("ctrl+shift+q", "Ctrl + Shift + Q"),
    ("insert", "Insert"),
    ("ctrl+page_up", "Ctrl + Page Up"),
])
def test_the_keycap_reads_like_a_keycap(spec, shown):
    assert keys.pretty(spec) == shown


def test_left_and_right_modifiers_are_the_same_modifier():
    """pynput's listener canonicalises ctrl_l and ctrl_r to Key.ctrl, which
    is what makes ctrl+q fire on either - and is why the config has no
    separate spelling for them."""
    from pynput import keyboard

    listener = keyboard.Listener(on_press=lambda k: None)
    assert listener.canonical(keyboard.Key.ctrl_l) == keyboard.Key.ctrl
    assert listener.canonical(keyboard.Key.ctrl_r) == keyboard.Key.ctrl


# -- a combination pressed in the panel ------------------------------------

@pytest.mark.parametrize("event, expected", [
    ({"code": "KeyQ", "ctrl": True}, "ctrl+q"),
    ({"code": "KeyQ", "ctrl": True, "shift": True}, "ctrl+shift+q"),
    ({"code": "Insert"}, "insert"),
    ({"code": "F13", "ctrl": True}, "ctrl+f13"),
    ({"code": "Digit7", "alt": True}, "alt+7"),
    ({"code": "PageUp", "ctrl": True, "meta": True}, "ctrl+cmd+page_up"),
])
def test_a_combination_pressed_in_the_panel_is_understood(event, expected):
    assert keys.from_browser(event) == expected


@pytest.mark.parametrize("event", [
    {}, {"code": ""}, {"code": "MediaPlayPause"}, {"code": "F99"},
    {"code": "ControlLeft", "ctrl": True},      # a modifier is not a binding
    {"code": "ShiftRight", "shift": True},
    {"code": "Numpad5"},
])
def test_something_that_cannot_be_bound_says_so(event):
    """Better to refuse at the keypress than to write a binding that fails
    to arm at the next start, where nobody would connect the two."""
    assert keys.from_browser(event) is None


@pytest.mark.parametrize("event", [
    {"code": "KeyQ", "ctrl": True}, {"code": "Insert"},
    {"code": "F13", "ctrl": True, "shift": True},
])
def test_everything_capturable_is_something_the_listener_can_arm(event):
    """The two halves have to agree, or the panel offers a binding that
    then cannot be armed."""
    from pynput import keyboard

    spec = keys.from_browser(event)
    assert keyboard.HotKey.parse(keys.to_pynput(spec))


# -- the listener ----------------------------------------------------------

def test_a_press_does_not_run_on_the_listener_thread():
    """pynput's Windows listener is a low-level keyboard hook. Reading SLC
    takes seconds, and doing that on the hook thread makes every keystroke
    on the machine late."""
    from slcvoiceai.hotkeys import Hotkeys

    ran_on, done = [], threading.Event()

    def slow(action, buttons):
        ran_on.append(threading.current_thread().name)
        time.sleep(0.2)
        done.set()

    listener = Hotkeys({"intercom": "ctrl+q"}, slow)
    here = threading.current_thread().name

    started = time.time()
    listener._pressed("intercom")()
    assert time.time() - started < 0.1, "the press blocked its caller"
    assert done.wait(3), "the binding never ran"
    assert ran_on and ran_on[0] != here


def test_a_call_still_working_ignores_another_press():
    """A scan outlasts a keypress, and nobody wants a queue of clicks."""
    from slcvoiceai.hotkeys import Hotkeys

    fired, release = [], threading.Event()

    def slow(action, buttons):
        fired.append(action)
        release.wait(2)

    listener = Hotkeys({"intercom": "ctrl+q"}, slow)
    listener._pressed("intercom")()
    time.sleep(0.15)
    listener._pressed("intercom")()
    time.sleep(0.15)
    assert fired == ["intercom"]
    release.set()


def test_a_binding_that_throws_does_not_wedge_the_key():
    from slcvoiceai.hotkeys import Hotkeys

    calls = []

    def explode(action, buttons):
        calls.append(action)
        raise RuntimeError("no")

    listener = Hotkeys({"intercom": "ctrl+q"}, explode)
    listener._pressed("intercom")()
    time.sleep(0.25)
    listener._pressed("intercom")()
    time.sleep(0.25)
    assert calls == ["intercom", "intercom"], "the call stayed marked as busy"


def test_the_listener_passes_the_right_buttons_along():
    from slcvoiceai.hotkeys import Hotkeys

    seen = []
    listener = Hotkeys({"pa": "ctrl+e"},
                       lambda action, buttons: seen.append((action, buttons)))
    listener._pressed("pa")()
    time.sleep(0.25)
    assert seen == [("pa", ACTIONS["pa"])]


def test_an_unknown_call_in_the_config_is_not_armed():
    from slcvoiceai.hotkeys import Hotkeys

    listener = Hotkeys({"nonsense": "ctrl+q"}, lambda a, b: None)
    assert listener._map == {}


# -- reading the config ----------------------------------------------------

def write(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_binding_reads_back(tmp_path):
    cfg = config_module.load(write(
        tmp_path, '[hotkeys]\nintercom = "ctrl+q"\nground = "ctrl+w"\n'))
    assert cfg.hotkeys == {"intercom": "ctrl+q", "ground": "ctrl+w"}


def test_a_binding_is_stored_in_its_one_spelling(tmp_path):
    cfg = config_module.load(write(tmp_path, '[hotkeys]\npa = "SHIFT+Ctrl+E"\n'))
    assert cfg.hotkeys == {"pa": "ctrl+shift+e"}


def test_no_hotkeys_section_is_normal(tmp_path):
    assert config_module.load(write(tmp_path, '[behaviour]\n')).hotkeys == {}


@pytest.mark.parametrize("body", [
    '[hotkeys]\nnonsense = "ctrl+q"\n',        # not one of the three calls
    '[hotkeys]\nintercom = 5\n',
    '[hotkeys]\nintercom = ""\n',
    '[hotkeys]\nintercom = "ctrl+nonsense"\n',
    '[hotkeys]\nintercom = "ctrl"\n',          # modifiers alone
    '[hotkeys]\ninsert = "INTERCOM"\n',        # the shape this replaced
])
def test_a_line_that_makes_no_sense_is_skipped_not_fatal(tmp_path, body):
    """Refusing to start over one stale line is a poor trade when the
    alternative is starting with one hotkey unbound - and the log says
    exactly which line and why."""
    assert config_module.load(write(tmp_path, body)).hotkeys == {}


# -- binding from inside the panel -----------------------------------------

def panel_with(tmp_path, body='[audio]\nptt_key = "scroll_lock"\n'):
    from slcvoiceai.gui import App

    return App(config_module.load(write(tmp_path, body)))


def test_the_card_always_shows_all_three(tmp_path):
    """Bound or not: the set is fixed, so an unbound call is a row showing
    nothing rather than a row that is missing."""
    rows = panel_with(tmp_path).hotkey_rows()
    assert [row["action"] for row in rows] == list(ACTIONS)
    assert all(row["shown"] == "" for row in rows)


def test_binding_one_from_the_panel_sticks(tmp_path):
    panel = panel_with(tmp_path)
    answer = panel.set_binding("intercom", {"code": "KeyQ", "ctrl": True})

    assert answer["error"] == ""
    assert [r["shown"] for r in answer["rows"] if r["action"] == "intercom"] \
        == ["Ctrl + Q"]
    assert config_module.load(panel.cfg.source).hotkeys == {"intercom": "ctrl+q"}


def test_unbinding_removes_the_line(tmp_path):
    panel = panel_with(tmp_path)
    panel.set_binding("intercom", {"code": "KeyQ", "ctrl": True})
    panel.set_binding("ground", {"code": "KeyW", "ctrl": True})
    panel.clear_binding("intercom")
    assert config_module.load(panel.cfg.source).hotkeys == {"ground": "ctrl+w"}


def test_rebinding_replaces_rather_than_adds(tmp_path):
    panel = panel_with(tmp_path)
    panel.set_binding("pa", {"code": "KeyE", "ctrl": True})
    panel.set_binding("pa", {"code": "KeyR", "ctrl": True, "shift": True})
    assert config_module.load(panel.cfg.source).hotkeys == {"pa": "ctrl+shift+r"}


def test_the_same_combination_on_two_calls_is_refused(tmp_path):
    panel = panel_with(tmp_path)
    panel.set_binding("intercom", {"code": "KeyQ", "ctrl": True})
    answer = panel.set_binding("ground", {"code": "KeyQ", "ctrl": True})
    assert answer["error"]
    assert config_module.load(panel.cfg.source).hotkeys == {"intercom": "ctrl+q"}


def test_rebinding_a_call_to_what_it_already_has_is_fine(tmp_path):
    """It clashes only with itself, which is not a clash."""
    panel = panel_with(tmp_path)
    panel.set_binding("intercom", {"code": "KeyQ", "ctrl": True})
    assert panel.set_binding("intercom", {"code": "KeyQ", "ctrl": True})["error"] == ""


def test_the_bare_push_to_talk_key_is_refused(tmp_path):
    """It would fire a hotkey every time the pilot spoke."""
    panel = panel_with(tmp_path)
    answer = panel.set_binding("intercom", {"code": "ScrollLock"})
    assert answer["error"]
    assert panel.cfg.hotkeys == {}


def test_the_push_to_talk_key_with_a_modifier_is_allowed(tmp_path):
    """Ctrl + Scroll Lock is not Scroll Lock; the talk key still works."""
    panel = panel_with(tmp_path)
    assert panel.set_binding(
        "intercom", {"code": "ScrollLock", "ctrl": True})["error"] == ""


def test_something_unbindable_is_refused(tmp_path):
    assert panel_with(tmp_path).set_binding(
        "intercom", {"code": "MediaPlayPause"})["error"]


def test_an_unknown_call_is_refused(tmp_path):
    assert panel_with(tmp_path).set_binding(
        "nonsense", {"code": "KeyQ", "ctrl": True})["error"]


def test_the_answer_always_carries_what_is_really_bound(tmp_path):
    """The page redraws from this, so a refused edit cannot linger on
    screen looking as though it saved."""
    panel = panel_with(tmp_path)
    panel.set_binding("intercom", {"code": "KeyQ", "ctrl": True})
    refused = panel.set_binding("ground", {"code": "KeyQ", "ctrl": True})
    assert [(r["action"], r["shown"]) for r in refused["rows"]] == [
        ("intercom", "Ctrl + Q"), ("ground", ""), ("pa", "")]


def test_binding_without_a_config_on_disk_does_not_explode():
    """A Config nobody loaded has nowhere to write. The binding still has
    to take effect for this session."""
    from slcvoiceai.gui import App

    panel = App(config_module.Config())
    assert panel.set_binding("pa", {"code": "KeyE", "ctrl": True})["error"] == ""
    assert panel.cfg.hotkeys == {"pa": "ctrl+e"}


def test_the_comment_block_in_the_section_survives_an_edit(tmp_path):
    """config.example.toml explains the section in two dozen lines. Setting
    a key in the panel must not wipe them."""
    panel = panel_with(tmp_path,
                       '[audio]\nptt_key = "scroll_lock"\n\n'
                       '[hotkeys]\n# how this works\n# and some more\n'
                       'intercom = "ctrl+q"  # the ATT button\n')
    panel.set_binding("ground", {"code": "KeyW", "ctrl": True})

    after = panel.cfg.source.read_text(encoding="utf-8")
    assert "# how this works" in after
    assert "# and some more" in after
    assert "# the ATT button" in after, "the note on the surviving key was lost"


def test_every_call_has_a_label_in_every_language():
    from slcvoiceai import i18n

    for code in i18n.TRANSLATIONS:
        i18n.set_language(code)
        try:
            for action in ACTIONS:
                assert i18n.t("hotkeys." + action) != "hotkeys." + action, (
                    "{a} shows as a bare key in {c}".format(a=action, c=code))
        finally:
            i18n.set_language("en")


def test_the_example_config_documents_the_section():
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert "[hotkeys]" in text
    for action in ACTIONS:
        assert action in text, "the example does not mention " + action
    assert "ctrl+q" in text, "the example does not show a combination"
