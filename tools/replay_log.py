"""Replay every utterance in a flight log against today's matcher.

    python tools/replay_log.py [slcvoiceai.log]

Pairs each transcript with the button list that was on screen when it was
said, so the question asked is the one that matters: with the code as it
stands now, what would have happened?

Parsing the log is fiddlier than it looks. Transcripts are printed with
whichever quote does not clash with the text, so a regex that only knows
about apostrophes silently keeps the previous utterance and pairs it with
the next button list. And several buttons have a comma in the name - "GSX,
START CATERING", "THANKS, PIN RIGHT" - so the list cannot simply be split on
one. Both of those made the first version of this report nonsense.

Offline only: no Gemini calls, because the point is to find what the free
layer still cannot do.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai.intent import FuzzyRouter
from slcvoiceai.slc_ui import SPOKEN_ICONS, humanise_id, is_denied

_args = argparse.ArgumentParser(
    description="Replay a flight log against today's matcher.")
_args.add_argument("log", nargs="?",
                   default=str(Path(__file__).resolve().parent.parent
                               / "slcvoiceai.log"))
_args.add_argument("--floor", type=float, default=0.65,
                   help="min_confidence to replay against (default 0.65)")
_opts = _args.parse_args()
LOG = _opts.log
FLOOR = _opts.floor

SAID = re.compile(r"Transcribed in [\d.]+s \[\w+\]: (.+)$")
OFFERING = re.compile(r"SLC is offering (\d+) action\(s\): (.+)$")
PRESSED = re.compile(r"(?:Pressed|would press) ['\"](.+?)['\"] +\(")
REFUSED = re.compile(r"(Declined:|Below confidence|Nothing intelligible)")
#: Whether the offline layer settled it or handed it on. Replaying only the
#: offline matcher, a command the cloud answered SHOULD come back refused -
#: counting those as regressions is how the first version of this reported
#: twenty-one failures that were the cascade working as designed.
OFFLINE = re.compile(r"Settled offline")
ESCALATED = re.compile(r"asking gemini|Offline match is weak")


@dataclass
class A:
    name: str


def is_furniture(name: str) -> bool:
    """Would today's code keep this name out of the matcher's reach?

    slc_ui.is_chrome answers this from the AutomationId, which the log does
    not carry - it prints names only. So the id is reconstructed from the
    name and humanised back: the furniture names ARE humanised ids, so they
    survive the round trip, while a name SLC wrote itself does not.
    'Interactions Ground Crew' comes back unchanged; 'THANK YOU' comes back
    as 'THANKYOU', because the spaces were never word boundaries.

    The round trip alone is not enough, and the first version of this was
    wrong because of it: a single word with no spaces always survives it, so
    'ROGER' and 'HELLO?' came back as furniture and the report claimed the
    fix had taken 58 real commands away. The real is_chrome never had that
    problem - it humanises cmdPlayCaptainRoger, which gives 'Play Captain
    Roger' and does not match. Reconstructing from the name loses that, so
    the second half of the test is the one SLC's own labels always pass:
    they are written in capitals, and a humanised camelCase id never is.

    An approximation, and it says so - but an approximation of a rule, not
    a hand-kept list, which is what stops it drifting from the real one.
    """
    if is_denied(name):
        return True
    if name == name.upper():
        return False                       # a label SLC wrote itself
    ident = "cmd" + name.replace(" ", "")
    if ident.lower() in SPOKEN_ICONS:
        return False
    return humanise_id(ident) == name


def unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


# Names that really do contain a comma, harvested from the log itself so the
# list does not have to be maintained by hand.
lines = [line.rstrip() for line in open(LOG, encoding="utf-8")]
known = set()
for line in lines:
    m = PRESSED.search(line)
    if m:
        known.add(m.group(1))
comma_names = sorted((n for n in known if ", " in n), key=len, reverse=True)


def split_buttons(text: str) -> list[str]:
    """Split the offering line, keeping names that contain a comma whole."""
    placeholders = {}
    for i, name in enumerate(comma_names):
        token = "\x00{i}\x00".format(i=i)
        if name in text:
            text = text.replace(name, token)
            placeholders[token] = name
    parts = [p.strip() for p in text.split(", ") if p.strip()]
    return [placeholders.get(p, p) for p in parts]


records = []
said = offered = None
settled_offline = None
for line in lines:
    m = SAID.search(line)
    if m:
        said, offered = unquote(m.group(1)), None
        continue
    m = OFFERING.search(line)
    if m:
        # Older builds truncated this line, so it can announce 22 buttons and
        # then list thirteen. Replaying against a list the pressed button is
        # missing from reports a regression that never happened - which is
        # exactly what the first run of this said. Trust only complete lines.
        names = split_buttons(m.group(2))
        offered = names if len(names) == int(m.group(1)) else None
        continue
    if OFFLINE.search(line):
        settled_offline = True
        continue
    if ESCALATED.search(line):
        settled_offline = False
        continue
    if said is None or offered is None:
        continue
    m = PRESSED.search(line)
    if m:
        records.append((said, offered, m.group(1), settled_offline))
        said = offered = settled_offline = None
        continue
    if REFUSED.search(line):
        records.append((said, offered, None, settled_offline))
        said = offered = settled_offline = None

router = FuzzyRouter(min_confidence=FLOOR)
regressed, fixed, changed, still = [], [], [], []

#: Utterances whose old answer today's code will not give, because the
#: button it pressed was part of the panel. Not regressions - the point.
furniture_presses = []
furniture_seen = 0

for said, buttons, before, offline in records:
    if not said.strip():
        continue
    if any(is_furniture(b) for b in buttons):
        furniture_seen += 1
    buttons = [b for b in buttons if not is_furniture(b)]
    if before is not None and is_furniture(before):
        furniture_presses.append((said, before))
        before = None
    actions = [A(b) for b in buttons]
    d = router.decide(said, actions)
    now = actions[d.action_index].name if d.action_index is not None else None
    if before and not now:
        # Only a regression if the offline layer had it before.
        if offline:
            regressed.append((said, before, d.confidence))
    elif not before and now:
        fixed.append((said, now, d.confidence))
    elif before and now and before != now:
        changed.append((said, before, now, d.confidence))
    elif not before and not now:
        still.append((said, d.confidence))

total = sum(1 for line in lines if SAID.search(line))
print("utterances replayed: {n} of {t} - the rest were logged with a "
      "truncated button list".format(n=len(records), t=total))
print("panel furniture was on offer in {f} of them, and is now stripped "
      "before the matcher sees it".format(f=furniture_seen))
print("")
print("pressed furniture then, cannot now ({n}):".format(
    n=len(furniture_presses)))
for said, before in furniture_presses:
    print("   {s!r:<48} was {b}".format(s=said[:46], b=before))
print("")
print("refused then, matched now ({n}):".format(n=len(fixed)))
for said, now, conf in fixed:
    print("   {s!r:<48} -> {n} {c:.2f}".format(s=said[:46], n=now, c=conf))

print("")
print("pressed then, refused now ({n}) - REGRESSIONS:".format(n=len(regressed)))
for said, before, conf in regressed:
    print("   {s!r:<48} was {b} (now {c:.2f})".format(
        s=said[:46], b=before, c=conf))

print("")
print("different button now ({n}):".format(n=len(changed)))
for said, before, now, conf in changed:
    print("   {s!r:<40} {b} -> {n} {c:.2f}".format(
        s=said[:38], b=before, n=now, c=conf))

print("")
print("still refused offline, most frequent first:")
counts = {}
for said, conf in still:
    key = said.lower()
    counts.setdefault(key, [0, conf])
    counts[key][0] += 1
for said, (count, conf) in sorted(counts.items(), key=lambda kv: -kv[1][0])[:18]:
    print("   {c}x {s!r:<46} best {b:.2f}".format(c=count, s=said[:44], b=conf))
