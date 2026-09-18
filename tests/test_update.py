"""Noticing that GitHub has a newer version.

The whole mechanism is one line of text. Nothing downloads or replaces
itself, so the only ways this can do harm are by lying about being behind,
by taking the bridge down when GitHub is unreachable, or by delaying the
moment the key starts working.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai import update  # noqa: E402


# -- comparing dated versions ---------------------------------------------

@pytest.mark.parametrize("published,running", [
    ("260919", "260918"),
    ("261001", "260918"),      # October is later than September
    ("270101", "261231"),      # and next year is later than this one
    ("260918.1", "260918"),    # a second release on the same day
    ("260918.2", "260918.1"),
])
def test_later_versions_are_newer(published, running):
    assert update.is_newer(published, running)


@pytest.mark.parametrize("published,running", [
    ("260918", "260918"),
    ("260918", "260919"),
    ("260918", "261001"),
    ("260918", "260918.1"),    # the suffixed build is ahead of the bare one
])
def test_same_or_older_is_not_newer(published, running):
    assert not update.is_newer(published, running)


def test_the_scheme_orders_the_way_a_calendar_does():
    """The reason for YYMMDD over 0.18.9: six digits sort as dates do, with
    no date parsing anywhere. 0.18.9 would have put October behind September."""
    dates = ["260918", "260919", "261001", "261231", "270101"]
    for earlier, later in zip(dates, dates[1:]):
        assert update.is_newer(later, earlier), (earlier, later)
        assert not update.is_newer(earlier, later), (earlier, later)


@pytest.mark.parametrize("published", ["", "   ", "main", "v2", "0.1.0",
                                       "26-09-18", "latest", "260918-dirty"])
def test_a_version_that_is_not_a_date_never_counts_as_newer(published):
    """Someone running a fork or a local edit must not be told their own
    build is out of date - saying nothing is the better failure."""
    assert not update.is_newer(published, "260918")


def test_a_local_build_that_is_not_a_date_is_left_alone():
    assert not update.is_newer("270101", "my-own-version")


# -- reading the published version ----------------------------------------

REAL_INIT = '''"""SLCVoiceAI - natural-language voice control."""

#: a comment mentioning __version__ = "999999" to throw the parser off
__version__ = "261001"
'''


def test_the_version_is_read_from_the_published_file(monkeypatch):
    monkeypatch.setattr(update, "_fetch", lambda url, timeout: REAL_INIT)
    assert update.published_version() == "261001"


def test_a_commented_version_does_not_win(monkeypatch):
    """The regex is anchored to the start of a line for exactly this."""
    monkeypatch.setattr(update, "_fetch", lambda url, timeout: REAL_INIT)
    assert update.published_version() != "999999"


def test_github_being_unreachable_is_not_an_error(monkeypatch):
    def boom(url, timeout):
        raise OSError("no network in the cockpit")

    monkeypatch.setattr(update, "_fetch", boom)
    assert update.published_version() is None
    assert update.check() is None


def test_a_file_without_a_version_is_not_an_error(monkeypatch):
    monkeypatch.setattr(update, "_fetch", lambda url, timeout: "nothing here")
    assert update.published_version() is None


def test_check_returns_the_version_when_behind(monkeypatch):
    monkeypatch.setattr(update, "published_version", lambda timeout=5.0: "270101")
    assert update.check() == "270101"


def test_check_says_nothing_when_current(monkeypatch):
    monkeypatch.setattr(update, "published_version",
                        lambda timeout=5.0: update.__version__)
    assert update.check() is None


def test_check_never_raises(monkeypatch):
    def boom(timeout=5.0):
        raise RuntimeError("something nobody anticipated")

    monkeypatch.setattr(update, "published_version", boom)
    assert update.check() is None


def test_it_reads_the_branch_not_the_releases_api():
    """Deliberate: a push is enough, with no tag or release to remember to
    create. If this ever moves to releases, the version becomes available
    later than it is published and that is a different promise."""
    assert "raw.githubusercontent.com" in update.VERSION_URL
    assert update.VERSION_URL.endswith("slcvoiceai/__init__.py")


def test_the_running_version_is_a_date():
    from slcvoiceai import __version__
    assert __version__.split(".")[0].isdigit()
    assert len(__version__.split(".")[0]) == 6
