"""Set __version__ to today, so a push is always an announcement.

Versions here are dates, YYMMDD. The panel's update check reads this line
off the default branch, so a change that ships without bumping it reaches
nobody: their copy compares equal and says nothing. Remembering to do that
by hand is exactly the sort of thing that gets forgotten on the one day it
matters, so the pre-commit hook runs this.

    python tools/bump_version.py            set it to today
    python tools/bump_version.py --check    say whether it is current

Idempotent within a day: several commits on the same date share a version,
which is what a dated version means - "the state of this project on that
day". If you need two announceable releases in one day, add a suffix by
hand (260919.1); this leaves a version that is already ahead alone rather
than winding it backwards.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

INIT = Path(__file__).resolve().parent.parent / "slcvoiceai" / "__init__.py"
VERSION_LINE = re.compile(r'^(__version__\s*=\s*")([^"]*)(")', re.MULTILINE)


def today() -> str:
    return dt.date.today().strftime("%y%m%d")


def parts(value: str):
    """A dated version as comparable numbers, or None if it is not one."""
    pieces = value.strip().split(".")
    if not pieces or not all(p.isdigit() for p in pieces):
        return None
    return [int(p) for p in pieces]


def current(text: str) -> str | None:
    match = VERSION_LINE.search(text)
    return match.group(2) if match else None


def bumped(text: str, now: str | None = None) -> tuple[str, str, str]:
    """(new text, old version, new version). Never moves a version back."""
    now = now or today()
    was = current(text)
    if was is None:
        raise SystemExit("No __version__ line in {p}".format(p=INIT))

    here, target = parts(was), parts(now)
    if here is not None and target is not None and here >= target:
        # Already today, or ahead of it - someone set a suffix on purpose, or
        # the clock disagrees. Either way, leave it.
        return text, was, was
    return VERSION_LINE.sub(r"\g<1>" + now + r"\g<3>", text, count=1), was, now


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the version is behind today, change nothing")
    args = ap.parse_args()

    text = INIT.read_text(encoding="utf-8")
    new_text, was, now = bumped(text)

    if args.check:
        if was == now:
            print("version {v} is current".format(v=was))
            return 0
        print("version {w} is behind {n}".format(w=was, n=now))
        return 1

    if new_text == text:
        print("version {v} already".format(v=was))
        return 0
    INIT.write_text(new_text, encoding="utf-8")
    print("version {w} -> {n}".format(w=was, n=now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
