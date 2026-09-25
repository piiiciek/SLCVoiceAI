"""Tell Whisper what vocabulary to expect before it transcribes.

Whisper decodes a short clip with no idea what domain it is in, so it guesses
from general usage - which is how "obsluga naziemna" became "the service to
the earth" and how Polish "ladowac" (letting passengers on) kept arriving as
"charge". Seeding the decoder with the words that actually occur in a cabin
makes those readings less likely at the source, rather than patching each one
afterwards with an alias.

The terms below are derived from SLC's own button names - 325 distinct words
across 336 controls - filtered down to what is both distinctive and likely to
be misheard. Edit `vocabulary.txt` next to config.toml to add your own; it
replaces this list when present.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

#: Whisper's prompt window is small - roughly 224 tokens - and anything past
#: it is silently dropped. Keeping the list short also keeps it sharp: a
#: prompt naming every word in the game biases nothing in particular.
MAX_PROMPT_CHARS = 900

#: Domain terms from SLC's controls, the ones worth biasing towards. Words
#: that are already unambiguous in general English ("please", "thanks") are
#: left out - they cost prompt budget and change nothing.
DEFAULT_TERMS = (
    # The pilot's own language comes first, because it is the half at
    # risk. The English half is largely covered anyway: build_prompt
    # puts the buttons SLC is showing right now in front of all of
    # this, and those are English. What no button supplies is the
    # Polish word for the thing - and on 2026-09-25 "do interkomu"
    # came back as "do literkomu", which no alias can rescue because
    # the word never arrived.
    "interkom", "rękaw", "jetway", "tankowanie", "catering",
    "boarding", "pasażerowie", "załoga", "obsługa naziemna",
    "kokpit", "szef pokładu", "kabina", "pasy", "drzwi",
    "schody", "wypychanie", "odladzanie", "zasilanie", "hamulec",
    "zniżanie", "lądowanie", "muzyka", "herbata", "kawa", "woda",
    # ground handling
    "ground crew", "jetway", "stairs", "pushback", "tow", "GSX",
    "refuelling", "catering", "deicing", "chocks", "GPU", "APU",
    # cabin
    "cabin crew", "purser", "flight attendant", "intercom", "public address",
    "boarding", "deboarding", "disembarking", "seatbelts", "galley",
    "inflight services", "trolley", "tannoy",
    # phase of flight
    "pushback", "taxi", "takeoff", "climb", "cruise", "descent", "approach",
    "go around", "landing", "turnaround",
    # The verb, not only the noun. Polish "znizac" has no cognate here, so
    # the decoder guessed from general usage: "Wkrotce bedziemy znizac" came
    # back as "we will short-circuit", and on a second attempt as "short, we
    # will reduce" - nothing a button could be matched to. Measured on the
    # same clips, seeding these turns those into "shortly, we will descend"
    # and "we are descending".
    "descending", "descending soon", "starting our descent", "top of descent",
    # radio
    "roger", "wilco", "understood", "acknowledged", "copy that",
    "standby", "disregard", "say again", "radio check",
    "loud and clear", "go ahead", "cockpit to ground",
    # things Whisper gets wrong in Polish
    "start boarding", "loading", "offloading", "connect", "disconnect",
)


def load_terms(config_dir: Path | str = ".") -> tuple[str, ...]:
    """Terms from vocabulary.txt if it exists, else the defaults."""
    path = Path(config_dir) / "vocabulary.txt"
    if not path.is_file():
        return DEFAULT_TERMS
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Could not read %s (%s) - using the built-in vocabulary",
                    path, exc)
        return DEFAULT_TERMS

    terms = []
    for line in text.splitlines():
        line = line.split(";", 1)[0].strip()
        if not line:
            continue
        terms.extend(t.strip() for t in line.split(",") if t.strip())
    if not terms:
        return DEFAULT_TERMS
    log.info("Loaded %d vocabulary terms from %s", len(terms), path)
    return tuple(terms)


def build_prompt(terms: tuple[str, ...], extra: tuple[str, ...] = ()) -> str:
    """A comma-separated hint list, trimmed to what Whisper will read.

    `extra` is for button names SLC is showing right now - the most relevant
    vocabulary there can be - and goes first so it survives the trim.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for term in tuple(extra) + tuple(terms):
        key = term.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(term.strip())

    out: list[str] = []
    length = 0
    for term in ordered:
        cost = len(term) + 2
        if length + cost > MAX_PROMPT_CHARS:
            break
        out.append(term)
        length += cost
    return ", ".join(out)
