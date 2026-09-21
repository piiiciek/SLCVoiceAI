"""The panel's own wording, in the pilot's language.

Only the interface is translated. Log messages stay in English on purpose:
the panel colours entries by matching on them ("Pressed", "Declined"),
`tools/replay_log.py` parses them to replay a flight against a newer
matcher, and every log already sent to someone for help is in English.
Translating them would break all three at once, and a diagnostic trail is
worth more in one language than in five.

Adding a language
-----------------

Copy the English block, translate the values, and add the name to
LANGUAGES. Nothing else needs touching - the panel asks for keys, not
sentences, and an untranslated key falls back to English rather than
showing a raw key or crashing, so a half-finished translation is still
usable. `tests/test_i18n.py` checks that a new language invents no keys
and leaves no placeholder behind.

Placeholders in braces are filled by the caller and must survive
translation: {key}, {model} and the rest have to appear in both versions,
which the tests enforce.
"""

from __future__ import annotations

import ctypes
import logging

log = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "en"

#: Code -> what to call it in its own language, for the picker.
LANGUAGES = {
    "en": "English",
    "pl": "Polski",
}

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # -- status line. The panel draws the indicator itself, so these
        # are the words alone.
        "status.stopped": "Stopped",
        "status.loading": "Loading Whisper",
        "status.listening": "Listening",
        "status.failed": "Failed to start",
        "button.start": "Start listening",
        "button.starting": "Starting...",
        "button.stop": "Stop",
        "subtitle.ready": ("hold {key} and speak   ·   whisper {model} on "
                           "{dev}   ·   {backend}"),
        # -- card headings. Shown in small capitals by the stylesheet, so
        # write them as ordinary words.
        "card.status": "Status",
        "card.controls": "Controls",
        "card.test": "Test without a microphone",
        "card.hotkeys": "Keys bound to SLC buttons",
        "pane.activity": "Activity",
        # -- hotkeys. The three calls are named by what they do, not by
        # what SLC calls the button - the pilot never types either.
        "hotkeys.intercom": "Call the cabin crew  (ATT)",
        "hotkeys.ground": "Call the ground crew  (MECH)",
        "hotkeys.pa": "Announcement to the passengers",
        "hotkeys.back": "Back out of the menu",
        "hotkeys.press": "press keys",
        "hotkeys.unbound": "not set",
        "hotkeys.clear": "Unbind",
        "hotkeys.none": "nothing bound",
        "hotkeys.unusable": "-- that cannot be bound; try another combination",
        "hotkeys.is_ptt": "-- {key} is already the push-to-talk key",
        "hotkeys.duplicate": "-- {key} is already bound to another call",
        # -- controls
        "control.dry_run": "Dry run (decide, never press)",
        "control.confidence": "min confidence",
        # Two versions, because the second half is only true when a cloud
        # matcher is configured to be asked. {ai} is whichever one that is.
        "control.hint": ("The lower the slider, the more often the program "
                         "presses on its own - quicker, but likelier to get "
                         "one wrong. The higher, the more often it asks {ai} "
                         "or does nothing - slower, but surer."),
        "control.hint_alone": ("The lower the slider, the more often the "
                               "program presses on its own - quicker, but "
                               "likelier to get one wrong. The higher, the "
                               "more often it does nothing - slower, but "
                               "surer."),
        "control.language": "language",
        "control.auto_start": "Start listening when SLC opens",
        "control.auto_start_hint": ("the panel has to be open for this - it "
                                    "waits without loading anything"),
        # The example stays in English in every language: it is what you
        # would say to SLC, and SLC's buttons are English.
        "test.placeholder": "e.g. cabin crew, prepare for landing",
        "test.match": "Match",
        "test.hint": ("Runs typed text through the whole matching chain and "
                      "shows every score, but never presses anything. Needs "
                      "SLC running."),
        "feed.clear": "Clear",
        # -- feed
        "feed.welcome": ("SLCVoiceAI ready. Start SLC, get into a flight, "
                         "then press and hold {key} to speak."),
        "feed.welcome_typed": ("You can also type a phrase above to test "
                               "matching without speaking."),
        "feed.loading_model": "-- loading the speech model, this takes a few seconds",
        "feed.stopped": "-- stopped listening",
        "feed.dry_run": "-- dry run {state}",
        "feed.dry_on": "on",
        "feed.dry_off": "off",
        "feed.typed": '-- typed: "{said}"  (reading SLC...)',
        "feed.read_failed": "     could not read SLC: {error}",
        "feed.no_actions": "     no actions to match against (is SLC running?)",
        "feed.declined": "     declined: {reason}",
        "feed.save_failed": "-- could not save that to config.toml: {error}",
        "feed.hotkeys": "-- keys: {what}",
        "feed.auto_start": "-- start with SLC {state}",
        "feed.slc_appeared": "-- SLC is up; starting on its own",
        "feed.pass": "PASS",
        "feed.below_floor": "below floor",
        "feed.language_changed": "-- interface language: {name}",
        # -- updates
        "update.banner": "SLCVoiceAI {new} is available - you have {old}",
        "update.open": "open GitHub",
        "update.feed": "-- version {new} is on GitHub; this is {old}  ({url})",
    },
    "pl": {
        "status.stopped": "Zatrzymany",
        "status.loading": "Wczytywanie Whispera",
        "status.listening": "Nasłuchuje",
        "status.failed": "Nie udało się uruchomić",
        "button.start": "Zacznij nasłuchiwać",
        "button.starting": "Uruchamianie...",
        "button.stop": "Zatrzymaj",
        "subtitle.ready": ("przytrzymaj {key} i mów   ·   whisper {model} na "
                           "{dev}   ·   {backend}"),
        "card.status": "Stan",
        "card.controls": "Sterowanie",
        "card.test": "Test bez mikrofonu",
        "card.hotkeys": "Klawisze pod przyciski SLC",
        "pane.activity": "Przebieg",
        "hotkeys.intercom": "Wezwij kabinę  (ATT)",
        "hotkeys.ground": "Wezwij obsługę naziemną  (MECH)",
        "hotkeys.pa": "Komunikat do pasażerów",
        "hotkeys.back": "Wyjdź z menu (BACK)",
        "hotkeys.press": "naciśnij klawisze",
        "hotkeys.unbound": "nie przypisano",
        "hotkeys.clear": "Odepnij",
        "hotkeys.none": "nic nie przypisano",
        "hotkeys.unusable": "-- tego nie da się przypisać; spróbuj innej kombinacji",
        "hotkeys.is_ptt": "-- {key} jest już klawiszem do mówienia",
        "hotkeys.duplicate": "-- {key} jest już przypisany do innego wezwania",
        "control.dry_run": "Próba (decyduj, nic nie wciskaj)",
        "control.confidence": "min. pewność",
        "control.hint": ("Im niżej suwak, tym częściej program wciska sam — "
                         "szybciej, ale łatwiej o pomyłkę. Im wyżej, tym "
                         "częściej dopytuje AI {ai} albo nie robi nic — "
                         "wolniej, ale pewniej."),
        "control.hint_alone": ("Im niżej suwak, tym częściej program wciska "
                               "sam — szybciej, ale łatwiej o pomyłkę. Im "
                               "wyżej, tym częściej nie robi nic — wolniej, "
                               "ale pewniej."),
        "control.language": "język",
        "control.auto_start": "Zacznij nasłuchiwać, gdy ruszy SLC",
        "control.auto_start_hint": ("panel musi być otwarty — czeka, nic nie "
                                    "wczytując"),
        "test.placeholder": "np. cabin crew, prepare for landing",
        "test.match": "Dopasuj",
        "test.hint": ("Przepuszcza wpisany tekst przez całe dopasowanie i "
                      "pokazuje wyniki, ale niczego nie wciska. Wymaga "
                      "uruchomionego SLC."),
        "feed.clear": "Wyczyść",
        "feed.welcome": ("SLCVoiceAI gotowy. Uruchom SLC, wejdź w lot, "
                         "a potem przytrzymaj {key} i mów."),
        "feed.welcome_typed": ("Możesz też wpisać frazę powyżej, żeby "
                               "sprawdzić dopasowanie bez mówienia."),
        "feed.loading_model": "-- wczytywanie modelu mowy, to chwilę potrwa",
        "feed.stopped": "-- nasłuch zatrzymany",
        "feed.dry_run": "-- próba {state}",
        "feed.dry_on": "włączona",
        "feed.dry_off": "wyłączona",
        "feed.typed": '-- wpisano: "{said}"  (odczyt SLC...)',
        "feed.read_failed": "     nie udało się odczytać SLC: {error}",
        "feed.no_actions": "     brak akcji do dopasowania (czy SLC działa?)",
        "feed.declined": "     odmowa: {reason}",
        "feed.save_failed": "-- nie udało się zapisać tego do config.toml: {error}",
        "feed.hotkeys": "-- klawisze: {what}",
        "feed.auto_start": "-- start razem z SLC {state}",
        "feed.slc_appeared": "-- SLC wstał; uruchamiam się sam",
        "feed.pass": "PRZECHODZI",
        "feed.below_floor": "poniżej progu",
        "feed.language_changed": "-- język interfejsu: {name}",
        "update.banner": "SLCVoiceAI {new} jest dostępny - masz {old}",
        "update.open": "otwórz GitHuba",
        "update.feed": "-- wersja {new} jest na GitHubie; ta to {old}  ({url})",
    },
}

_current = DEFAULT_LANGUAGE


def available() -> dict[str, str]:
    """Languages the panel can be shown in, code -> its own name."""
    return dict(LANGUAGES)


def current() -> str:
    return _current


def detect() -> str:
    """The language Windows is displayed in, if the panel speaks it."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        code = kernel32.GetUserDefaultUILanguage() & 0x3FF
    except Exception:
        return DEFAULT_LANGUAGE
    # Primary language ids, the low ten bits of the LANGID.
    primary = {0x09: "en", 0x15: "pl"}
    return primary.get(code, DEFAULT_LANGUAGE)


def set_language(code: str) -> str:
    """Switch language. Unknown codes fall back rather than failing."""
    global _current
    wanted = (code or "").strip().lower()
    if wanted in ("", "auto"):
        wanted = detect()
    if wanted not in TRANSLATIONS:
        log.warning("No translation for %r; using %s.", code, DEFAULT_LANGUAGE)
        wanted = DEFAULT_LANGUAGE
    _current = wanted
    return _current


def t(phrase: str, /, **kwargs) -> str:
    """The phrase for this key, in the current language.

    The key is positional-only on purpose: one of the placeholders is
    called {key} - the push-to-talk key - and naming the parameter the same
    thing made t("feed.welcome", key="scroll_lock") raise TypeError, which
    is the first line the panel prints.

    Never raises and never shows a bare key: an untranslated key falls back
    to English, and a placeholder the translation forgot leaves the phrase
    as written rather than blowing up the panel mid-flight.
    """
    text = TRANSLATIONS.get(_current, {}).get(phrase)
    if text is None:
        text = TRANSLATIONS[DEFAULT_LANGUAGE].get(phrase)
    if text is None:
        log.debug("No phrase for %r", phrase)
        return phrase
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        log.debug("Phrase %r does not fit %r", phrase, sorted(kwargs))
        return text
