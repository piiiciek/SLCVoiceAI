"""Bumping the version, which nothing else can be trusted to remember.

The panel's update check reads __version__ off GitHub's default branch. A
change pushed without bumping it reaches nobody - every copy compares equal
and stays quiet - so the pre-commit hook does it. That makes this code run
on every single commit, which is a good reason for it to be dull and for
its edges to be pinned down.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import bump_version as bump  # noqa: E402

TEMPLATE = '"""Doc."""\n\n#: a comment\n__version__ = "{v}"\n'


def text(version: str) -> str:
    return TEMPLATE.format(v=version)


def test_a_stale_version_moves_to_today():
    new, was, now = bump.bumped(text("260918"), now="260919")
    assert (was, now) == ("260918", "260919")
    assert '__version__ = "260919"' in new


def test_todays_version_is_left_alone():
    """Several commits in a day share a version - that is what a dated
    version means."""
    original = text("260919")
    new, was, now = bump.bumped(original, now="260919")
    assert new == original
    assert was == now == "260919"


def test_a_hand_written_suffix_is_not_wound_back():
    """Someone wanting two announceable releases in one day adds 260919.1.
    The next commit that day must not undo it."""
    original = text("260919.1")
    new, was, now = bump.bumped(original, now="260919")
    assert new == original
    assert now == "260919.1"


def test_a_version_ahead_of_the_clock_is_left_alone():
    """A machine whose date is wrong must not drag the version backwards."""
    original = text("261231")
    new, _was, now = bump.bumped(original, now="260919")
    assert new == original
    assert now == "261231"


def test_something_that_is_not_a_date_is_replaced():
    """A fork carrying "0.1.0" or "dev" gets a real dated version rather
    than being left in a state update.py cannot compare."""
    new, was, now = bump.bumped(text("0.1.0"), now="260919")
    assert was == "0.1.0"
    assert now == "260919"
    assert '__version__ = "260919"' in new


def test_only_the_version_line_is_touched():
    source = ('__version__ = "260918"\n'
              'OTHER = "260918"\n'
              '# __version__ = "999999" in a comment\n')
    new, _was, _now = bump.bumped(source, now="260919")
    assert '__version__ = "260919"' in new
    assert 'OTHER = "260918"' in new, "it rewrote an unrelated line"
    assert '999999' in new, "it rewrote a comment"


def test_a_file_without_a_version_is_an_error_not_a_silent_pass():
    with pytest.raises(SystemExit):
        bump.bumped('nothing here\n', now="260919")


def test_today_is_six_digits():
    assert len(bump.today()) == 6
    assert bump.today().isdigit()


def test_it_reads_the_real_package():
    """Wired to the file update.py publishes, not a copy of it."""
    assert bump.INIT.name == "__init__.py"
    assert bump.INIT.parent.name == "slcvoiceai"
    assert bump.current(bump.INIT.read_text(encoding="utf-8"))


def test_the_shipped_version_is_a_comparable_date():
    from slcvoiceai import __version__
    assert bump.parts(__version__) is not None, (
        "update.py cannot compare this, so nobody would be told about it")


def test_the_hook_exists_and_calls_the_bumper():
    hook = ROOT / "tools" / "hooks" / "pre-commit"
    assert hook.is_file(), "the hook is what makes this automatic"
    body = hook.read_text(encoding="utf-8")
    assert "bump_version.py" in body
    assert "git add slcvoiceai/__init__.py" in body, (
        "bumping without staging leaves the commit carrying the old version")
