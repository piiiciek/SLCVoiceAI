"""The panel's wording, in more than one language.

The door is meant to stay open: adding a language should be copying one
dict and translating the values. These tests are what makes that safe -
they catch a key invented in one language and not the other, a placeholder
lost in translation, and a phrase the panel asks for that nobody wrote.
"""

from __future__ import annotations

import re
import string
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slcvoiceai import i18n  # noqa: E402


@pytest.fixture(autouse=True)
def english_by_default():
    """Each test starts from a known language and leaves one behind."""
    i18n.set_language("en")
    yield
    i18n.set_language("en")


def placeholders(text: str) -> set[str]:
    return {name for _lit, name, _spec, _conv
            in string.Formatter().parse(text) if name}


# -- the translations themselves ------------------------------------------

def test_every_language_is_named_in_the_picker():
    for code in i18n.TRANSLATIONS:
        assert code in i18n.LANGUAGES, code
    for code in i18n.LANGUAGES:
        assert code in i18n.TRANSLATIONS, code


def test_english_is_the_complete_one():
    """Everything falls back to English, so English has to have every key."""
    english = set(i18n.TRANSLATIONS["en"])
    for code, phrases in i18n.TRANSLATIONS.items():
        invented = set(phrases) - english
        assert not invented, "{c} has keys English does not: {k}".format(
            c=code, k=sorted(invented))


@pytest.mark.parametrize("code", sorted(i18n.TRANSLATIONS))
def test_a_translation_keeps_its_placeholders(code):
    """{key} and {model} are filled in by the panel. A translation that
    drops one leaves a hole; one that invents another raises at format
    time, in front of a pilot, mid-flight."""
    for key, english in i18n.TRANSLATIONS["en"].items():
        translated = i18n.TRANSLATIONS[code].get(key)
        if translated is None:
            continue
        assert placeholders(translated) == placeholders(english), key


@pytest.mark.parametrize("code", sorted(i18n.TRANSLATIONS))
def test_nothing_is_left_untranslated_by_accident(code):
    """A phrase identical to the English is fine for a word like 'PASS',
    but a whole sentence that matches usually means it was missed."""
    if code == "en":
        return
    same = [k for k, v in i18n.TRANSLATIONS[code].items()
            if v == i18n.TRANSLATIONS["en"].get(k) and len(v.split()) > 3]
    assert not same, "looks untranslated in {c}: {k}".format(c=code, k=same)


# -- choosing one ---------------------------------------------------------

def test_a_known_language_is_selected():
    assert i18n.set_language("pl") == "pl"
    assert i18n.t("button.start") == i18n.TRANSLATIONS["pl"]["button.start"]


def test_an_unknown_language_falls_back_rather_than_failing():
    assert i18n.set_language("de") == "en"
    assert i18n.t("button.start") == i18n.TRANSLATIONS["en"]["button.start"]


def test_auto_picks_something_the_panel_speaks():
    assert i18n.set_language("auto") in i18n.TRANSLATIONS
    assert i18n.detect() in i18n.TRANSLATIONS


@pytest.mark.parametrize("value", ["", None, "   ", "PL", "pl-PL"])
def test_odd_settings_do_not_break_it(value):
    assert i18n.set_language(value) in i18n.TRANSLATIONS


# -- asking for a phrase --------------------------------------------------

def test_a_missing_key_falls_back_to_english():
    i18n.set_language("pl")
    i18n.TRANSLATIONS["pl"].pop("test.match", None)
    try:
        assert i18n.t("test.match") == i18n.TRANSLATIONS["en"]["test.match"]
    finally:
        i18n.TRANSLATIONS["pl"]["test.match"] = "Dopasuj"


def test_a_key_nobody_wrote_returns_the_key_not_an_exception():
    assert i18n.t("nothing.like.this") == "nothing.like.this"


def test_a_phrase_that_does_not_fit_is_shown_rather_than_raised():
    """The panel must not die mid-flight over a bad translation."""
    i18n.TRANSLATIONS["en"]["test.only"] = "needs {missing}"
    try:
        assert i18n.t("test.only", other="x") == "needs {missing}"
    finally:
        del i18n.TRANSLATIONS["en"]["test.only"]


def test_placeholders_are_filled():
    i18n.set_language("pl")
    assert "scroll_lock" in i18n.t("feed.welcome", key="scroll_lock")


# -- the panel and the table agree ----------------------------------------

def test_every_phrase_the_panel_asks_for_exists():
    """Catches a typo in a key, which would otherwise show the pilot a
    lowercase dotted identifier where a sentence belongs."""
    source = (ROOT / "slcvoiceai" / "gui.py").read_text(encoding="utf-8")
    asked = set(re.findall(r'\bt\(\s*"([a-z][a-z0-9_.]+)"', source))
    assert asked, "no translated phrases found - did the panel stop using t()?"
    missing = sorted(asked - set(i18n.TRANSLATIONS["en"]))
    assert not missing, "the panel asks for phrases nobody wrote: " + str(missing)


def test_every_phrase_the_page_asks_for_exists():
    """The other half of the panel.

    Since the rewrite, the static labels live in the markup as data-i18n
    keys rather than as t() calls, so scanning gui.py alone would no longer
    see them. Same failure this guards against: a heading rendering as
    `card.controls` in front of a pilot.
    """
    from slcvoiceai.gui import phrase_keys

    markup = (ROOT / "slcvoiceai" / "web" / "index.html").read_text(
        encoding="utf-8")
    asked = set(phrase_keys(markup))
    assert asked, "the page asks for no phrases - did the attributes change?"
    missing = sorted(asked - set(i18n.TRANSLATIONS["en"]))
    assert not missing, "the page asks for phrases nobody wrote: " + str(missing)


def test_the_log_is_not_translated():
    """Deliberate. The panel colours entries by matching on them,
    tools/replay_log.py parses them, and every log already sent to someone
    for help is in English."""
    for module in ("app.py", "intent.py", "slc_ui.py", "audio.py"):
        source = (ROOT / "slcvoiceai" / module).read_text(encoding="utf-8")
        assert "i18n" not in source, (
            "{m} imports i18n - log messages are meant to stay in English"
            .format(m=module))
