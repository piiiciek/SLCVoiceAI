"""Notice when the copy on GitHub is newer than this one.

This project is given away, so most people running it will never watch the
repository. A line in the panel saying a newer version exists is the whole
mechanism - nothing downloads itself, nothing restarts, nothing is replaced.
Updating stays a deliberate act: pull, or download the zip again.

The check reads one file over HTTPS and sends nothing about the machine it
runs on beyond what any HTTP request carries. It reads slcvoiceai/__init__.py
straight off the default branch rather than asking the releases API, so a
version becomes "available" the moment it is pushed, with no tag or release
to remember to create.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request

from . import __version__

log = logging.getLogger(__name__)

REPOSITORY = "piiiciek/SLCVoiceAI"
VERSION_URL = ("https://raw.githubusercontent.com/{repo}/main/"
               "slcvoiceai/__init__.py".format(repo=REPOSITORY))
RELEASES_URL = "https://github.com/{repo}".format(repo=REPOSITORY)

_VERSION_LINE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""",
                           re.MULTILINE)


def _fetch(url: str, timeout: float) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": "SLCVoiceAI/" + __version__})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def published_version(timeout: float = 5.0) -> str | None:
    """The version on GitHub's default branch, or None if it cannot be read."""
    try:
        match = _VERSION_LINE.search(_fetch(VERSION_URL, timeout))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("Update check could not reach GitHub: %s", exc)
        return None
    if match is None:
        log.debug("Update check found no version line at %s", VERSION_URL)
        return None
    return match.group(1).strip()


def is_newer(published: str, running: str = __version__) -> bool:
    """Is `published` a later dated version than `running`?

    Compared field by field as numbers, so 261001 beats 260918 and the
    same-day suffix in 260918.1 beats a bare 260918. Anything that is not a
    dated version - someone's fork, a local edit - compares as different
    rather than newer, because telling a person their own build is out of
    date is worse than saying nothing.
    """
    def parts(value: str):
        pieces = value.strip().split(".")
        if not all(piece.isdigit() for piece in pieces) or not pieces[0]:
            return None
        return [int(piece) for piece in pieces]

    here, there = parts(running), parts(published)
    if here is None or there is None:
        return False
    return there > here


def check(timeout: float = 5.0) -> str | None:
    """The newer version available, or None when this copy is current.

    Never raises: an update check that takes the bridge down with it would
    be worse than no update check.
    """
    try:
        published = published_version(timeout)
    except Exception:  # pragma: no cover - belt and braces
        return None
    if not published or not is_newer(published):
        return None
    log.info("SLCVoiceAI %s is available (running %s): %s",
             published, __version__, RELEASES_URL)
    return published
