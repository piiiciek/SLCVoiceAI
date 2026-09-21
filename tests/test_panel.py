"""The control panel, now that most of it can be tested.

The tkinter panel decided an entry's colour inside a widget call, so the
rule - which is the reason the log is not translated - had no test and was
only ever checked by looking at it. Splitting the window into Python that
decides and a page that draws made that rule an ordinary function.

The rest of this file guards the seam between the two halves. Python calls
the page by name and the page finds elements by id, so a rename on either
side fails silently at runtime: a button that stops responding, a status
line that stops updating, and nothing in the log about it.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slcvoiceai import gui, i18n  # noqa: E402

WEB = ROOT / "slcvoiceai" / "web"
MARKUP = (WEB / "index.html").read_text(encoding="utf-8")
SCRIPT = (WEB / "panel.js").read_text(encoding="utf-8")
PYTHON = (ROOT / "slcvoiceai" / "gui.py").read_text(encoding="utf-8")


# -- what an activity line means ------------------------------------------

@pytest.mark.parametrize("message, expected", [
    ("Pressed CABIN CREW PREPARE FOR LANDING", "ok"),
    ("Ready - listening on Microphone (2- USB Audio)", "ok"),
    ("DRY RUN - would have pressed BOARDING COMPLETE", "accent"),
    ('Transcribed: "cabin crew prepare for landing"', "plain"),
    ("Captured 2.31s of audio (key held 2.44s)", "plain"),
    ("Declined: nothing scored above the floor", "warn"),
    ("Asked Gemini for a second opinion", "muted"),
])
def test_an_entry_gets_the_colour_it_reads_as(message, expected):
    assert gui.tag_for(message, logging.INFO) == expected


def test_severity_outranks_wording():
    """An error that happens to contain a cheerful word is still an error."""
    assert gui.tag_for("Pressed nothing: SLC went away", logging.ERROR) == "bad"
    assert gui.tag_for("Ready, but the model is on CPU", logging.WARNING) == "warn"


@pytest.mark.parametrize("word, module", [
    ("Pressed", "app.py"),
    ("DRY RUN", "app.py"),
    ("Declined", "app.py"),
    ("Ready", "app.py"),
    ("Transcribed", "stt.py"),
    ("Captured", "audio.py"),
])
def test_the_words_it_matches_are_the_ones_the_bridge_logs(word, module):
    """The colouring reads English words out of the log, so a reworded log
    line turns the feed grey with nothing to say it happened.

    This is how the dead "Below confidence" branch was found: it had been
    carried since the tkinter panel and nothing had ever logged it.
    """
    source = (ROOT / "slcvoiceai" / module).read_text(encoding="utf-8")
    assert word in source, (
        "gui.tag_for colours on {w!r} but {m} no longer logs it"
        .format(w=word, m=module))


# -- the page's wording ----------------------------------------------------

def test_phrase_keys_finds_every_kind_of_attribute():
    markup = ('<h2 data-i18n="card.status"></h2>'
              '<input data-i18n-placeholder="test.placeholder">'
              '<button data-i18n-title="feed.clear"></button>')
    assert gui.phrase_keys(markup) == [
        "card.status", "feed.clear", "test.placeholder"]


def test_phrase_keys_also_finds_what_the_script_asks_for():
    """Rows the script builds carry no attributes for the sweep to find.
    Missing them showed a pilot `hotkeys.unbound` on an unbound key."""
    assert gui.phrase_keys("", 'cap.textContent = say("hotkeys.unbound");') == [
        "hotkeys.unbound"]


def test_no_phrase_the_script_asks_for_is_missing():
    """The bug this pair of lines exists to prevent, on the real files."""
    from slcvoiceai import i18n

    asked = set(re.findall(r'\bsay\(\s*"([a-z][a-z0-9_.]+)"\s*\)', SCRIPT))
    assert asked, "panel.js asks for no phrases - did say() get renamed?"
    missing = sorted(asked - set(i18n.TRANSLATIONS["en"]))
    assert not missing, "panel.js asks for phrases nobody wrote: " + str(missing)


def test_phrase_keys_says_each_key_once():
    markup = '<b data-i18n="a.b"></b><i data-i18n="a.b"></i>'
    assert gui.phrase_keys(markup) == ["a.b"]


def test_the_page_carries_keys_and_not_sentences():
    """The whole point of the split: a new language means editing i18n.py
    and nothing else. A translated word left in the markup would be
    invisible to that, and permanently English."""
    written = [text.strip() for text
               in re.findall(r'data-i18n="[^"]+"\s*>([^<]*)<', MARKUP)]
    assert not any(written), (
        "the markup spells out wording instead of asking for it: "
        + str([text for text in written if text]))


@pytest.mark.parametrize("code", sorted(i18n.TRANSLATIONS))
def test_every_label_on_the_page_has_a_phrase(code):
    i18n.set_language(code)
    try:
        for key in gui.phrase_keys(MARKUP, SCRIPT):
            assert i18n.t(key) != key, (
                "{k} shows as a bare key in {c}".format(k=key, c=code))
    finally:
        i18n.set_language("en")


@pytest.mark.parametrize("escalate_to, expected", [
    ("gemini", "Gemini"),
    ("claude", "Claude"),
])
def test_the_floor_names_whoever_is_actually_asked(escalate_to, expected):
    from slcvoiceai import config as config_module

    cfg = config_module.Config()
    cfg.intent.escalate_to = escalate_to
    assert expected in gui.App(cfg)._confidence_hint()


@pytest.mark.parametrize("escalate_to", ["none", "", "  "])
def test_with_nobody_to_ask_it_does_not_promise_anything(escalate_to):
    """A copy running purely offline has no cloud matcher. Telling its
    pilot that raising the floor means "asks Gemini" would be a lie."""
    from slcvoiceai import config as config_module

    cfg = config_module.Config()
    cfg.intent.escalate_to = escalate_to
    hint = gui.App(cfg)._confidence_hint()
    for name in gui.AI_NAMES.values():
        assert name not in hint, hint
    assert hint.strip(), "it says nothing at all"


def test_the_hint_the_page_gets_is_the_one_for_this_setup():
    """phrases() is what the page is sent, so the substitution has to
    survive that trip - and a language change re-sends it."""
    from slcvoiceai import config as config_module

    cfg = config_module.Config()
    cfg.intent.escalate_to = "gemini"
    assert "Gemini" in gui.App(cfg).phrases()["control.hint"]


def test_every_status_has_a_look():
    """A status with no entry here would raise a KeyError on the line that
    reports it - which is exactly when the panel must not fail."""
    statuses = {key for key in i18n.TRANSLATIONS["en"]
                if key.startswith("status.")}
    assert statuses == set(gui.STATUS_STATES)


def test_the_states_match_the_stylesheet():
    """Python names a state, the stylesheet colours it."""
    css = (WEB / "panel.css").read_text(encoding="utf-8")
    for state in gui.STATUS_STATES.values():
        if state == "stopped":
            continue  # the resting look is the plain .card, styled already
        assert ".is-" + state in css, (
            "no .is-{s} rule, so that status would look like any other"
            .format(s=state))


# -- the seam between Python and the page ----------------------------------

def test_python_only_calls_page_functions_that_exist():
    called = set(re.findall(r'_push\(\s*"(\w+)"', PYTHON))
    assert called, "gui.py pushes nothing - did _push get renamed?"
    for name in sorted(called):
        assert re.search(r"^\s+" + name + r":", SCRIPT, re.M), (
            "gui.py calls panel.{n}() but panel.js does not define it"
            .format(n=name))


def test_the_page_only_looks_for_elements_that_exist():
    wanted = set(re.findall(r'\$\("([\w-]+)"\)', SCRIPT))
    assert wanted, "panel.js looks nothing up - did the $ helper change?"
    for element in sorted(wanted):
        assert 'id="{e}"'.format(e=element) in MARKUP, (
            "panel.js wants #{e}, which is not in index.html".format(e=element))


def methods_the_page_calls() -> set[str]:
    """Both ways the page reaches Python.

    `ask("name")` is the fire-and-forget helper; `api.name(...)` is a
    direct call, used where the page needs the answer back. Looking for
    only one of them made this pair of tests quietly wrong the first time
    a method returned something.
    """
    return (set(re.findall(r'ask\("(\w+)"', SCRIPT))
            | set(re.findall(r'\bapi\.(\w+)\s*\(', SCRIPT)))


def test_the_page_only_calls_methods_python_exposes():
    """Anything the page asks for has to be a public method on Api, or the
    control is dead and the failure is a silent rejected promise."""
    asked = methods_the_page_calls()
    assert asked, "the page calls nothing - did the helpers get renamed?"
    exposed = {name for name in vars(gui.Api) if not name.startswith("_")}
    missing = sorted(asked - exposed)
    assert not missing, "the page calls Api methods that do not exist: " + str(missing)


def test_everything_api_exposes_is_reachable_from_the_page():
    """The other direction. Api is reachable from JavaScript, so a method
    nobody calls is surface for no reason."""
    unused = sorted({name for name in vars(gui.Api) if not name.startswith("_")}
                    - methods_the_page_calls())
    assert not unused, "Api exposes methods the page never calls: " + str(unused)


def test_the_page_loads_the_files_that_ship_with_it():
    for asset in ("panel.css", "panel.js"):
        assert asset in MARKUP, "index.html does not load " + asset
        assert (WEB / asset).is_file(), asset + " is missing"


def test_the_feed_is_written_as_text_not_markup():
    """Transcriptions are whatever the microphone heard and button names
    are whatever SLC calls them. Neither is trusted markup."""
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", SCRIPT, flags=re.S)
    assert "innerHTML" not in code, (
        "panel.js uses innerHTML somewhere; the feed must go in as text")


# -- leaving the program ---------------------------------------------------

def links_the_page_asks_for() -> set[str]:
    return set(re.findall(r'ask\("open_link",\s*"(\w+)"', SCRIPT))


def test_the_page_only_asks_for_links_this_file_knows():
    """The page names a destination and gui.py decides what it means. A
    name nobody here has heard of is a button that does nothing, silently,
    the way a rejected promise always does."""
    asked = links_the_page_asks_for()
    assert asked, "nothing on the page opens a link - did the call change?"
    unknown = sorted(asked - set(gui.links()))
    assert not unknown, "the page asks for links that do not exist: " + str(unknown)


def test_every_link_is_reachable_from_the_page():
    """The other direction, as with Api: a destination nothing can reach
    is a URL sitting in the source for no reason."""
    assert set(gui.links()) == links_the_page_asks_for()


@pytest.mark.parametrize("name", ["repository", "coffee"])
def test_a_known_name_opens_its_page(monkeypatch, name):
    import webbrowser

    from slcvoiceai import config as config_module

    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    gui.App(config_module.Config()).open_link(name)
    assert opened == [gui.links()[name]]


@pytest.mark.parametrize("name", ["https://example.invalid", "nonsense", ""])
def test_an_unknown_name_opens_nothing(monkeypatch, name):
    """Why the page passes a name and not a URL. This window has no
    address bar to notice with, so the one place that decides where the
    browser is sent is here."""
    import webbrowser

    from slcvoiceai import config as config_module

    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    gui.App(config_module.Config()).open_link(name)
    assert opened == []


def test_the_address_on_the_card_is_the_address_it_opens():
    """The hover card shows where the link goes, which is how someone
    decides whether to click it. Written out twice it would drift."""
    url = gui.links()["repository"]
    shown = gui.without_scheme(url)
    assert shown and shown in url
    assert not shown.startswith("http")


def test_the_tip_jar_is_the_one_the_button_was_made_for():
    assert gui.COFFEE_URL.rstrip("/").endswith("/piciek")


def test_the_panel_needs_nothing_from_the_network():
    """It opens on a machine about to fly a simulator, sometimes with no
    connection at all, and the window is drawn before anything else can
    happen. A stylesheet or a script fetched from a CDN is a panel that
    opens blank until the request gives up - which is the whole reason the
    coffee button is drawn here rather than by the script its owners hand
    out."""
    css = (WEB / "panel.css").read_text(encoding="utf-8")
    loaded = (re.findall(r'\b(?:src|href)\s*=\s*"([^"]+)"', MARKUP)
              + re.findall(r'url\(\s*["\']?([^)"\']+)', css)
              + re.findall(r'@import\s+["\']([^"\']+)', css))
    assert loaded, "the page loads nothing at all - did the markup change?"
    remote = [ref for ref in loaded if "//" in ref]
    assert not remote, "the panel would wait on the network for " + str(remote)
