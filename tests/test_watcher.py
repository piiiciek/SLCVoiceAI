"""Launching the panel when SLC starts.

This runs invisibly from login to shutdown, which sets the bar: it must
not open a second panel, must not reopen one the pilot has just closed,
and must not die quietly on the first error and leave them believing
something is watching.

The loop under test never returns on its own. Every test here drives it
by counting sleeps and stopping at a known point, so a fault shows up as
a failure rather than a hung suite.
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import watch_for_slc as watcher  # noqa: E402


class Enough(Exception):
    """Raised from the patched sleep to end the loop."""


def drive(monkeypatch, answers, panel_open=False, looks=None):
    """Run watch() over a scripted sequence of "is SLC running" answers.

    Returns how many times it launched. The sequence's first value is what
    the loop sees before it starts, the way the real one reads once up
    front to avoid treating an already-running SLC as news.
    """
    launched = []
    seen = {"n": 0}

    def is_running(_name):
        i = seen["n"]
        seen["n"] += 1
        value = answers[min(i, len(answers) - 1)]
        if isinstance(value, Exception):
            raise value
        return value

    def sleep(_seconds):
        if seen["n"] >= (looks if looks is not None else len(answers)):
            raise Enough

    monkeypatch.setattr(watcher, "is_running", is_running)
    monkeypatch.setattr(watcher, "panel_is_open", lambda: panel_open)
    monkeypatch.setattr(watcher, "launch",
                        lambda: (launched.append(True), True)[1])
    monkeypatch.setattr(watcher.time, "sleep", sleep)

    try:
        watcher.watch("SLC.exe", every=0, settle=0, once=False)
    except Enough:
        pass
    return launched


# -- noticing things -------------------------------------------------------

def test_it_finds_a_process_that_is_really_there():
    """Whatever is running these tests is running."""
    assert watcher.is_running(Path(sys.executable).name)


def test_it_does_not_find_one_that_is_not():
    assert not watcher.is_running("NoSuchProgramReally.exe")


def test_the_process_name_is_matched_whatever_the_case():
    assert watcher.is_running(Path(sys.executable).name.upper())


def test_a_window_nobody_has_is_not_found(monkeypatch):
    """Asserting the panel is shut would only pass while it is shut - and
    it was open the first time this suite ran on a real machine. The rule
    under test is the lookup, not what happens to be running."""
    monkeypatch.setattr(watcher, "PANEL_TITLE", "NoWindowIsCalledThis-4f2a")
    assert not watcher.panel_is_open()


def test_a_window_that_is_there_is_found(monkeypatch):
    """The other direction, against whatever this desktop really has open,
    so the enumeration itself is exercised rather than mocked away."""
    titles = _open_window_titles()
    assert titles, "no visible window has a title - cannot test this here"
    monkeypatch.setattr(watcher, "PANEL_TITLE", titles[0])
    assert watcher.panel_is_open()


def test_the_panel_is_matched_on_the_whole_title(monkeypatch):
    """There is an Explorer window called "SLCVoiceAI - Eksplorator plikow"
    on the machine this was written on. Matching a fragment would read
    that as the panel and then never launch anything."""
    titles = _open_window_titles()
    long_enough = next((t for t in titles if len(t) > 6), None)
    assert long_enough, "no title long enough to take a fragment of"
    monkeypatch.setattr(watcher, "PANEL_TITLE", long_enough[:len(long_enough) // 2])
    assert not watcher.panel_is_open()


def _open_window_titles():
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.WinDLL("user32", use_last_error=True)
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if u32.IsWindowVisible(hwnd):
            length = u32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                u32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.strip():
                    found.append(buf.value.strip())
        return True

    u32.EnumWindows(visit, 0)
    return found


# -- the loop --------------------------------------------------------------

def test_it_launches_when_slc_appears(monkeypatch):
    assert drive(monkeypatch, [False, False, True]) == [True]


def test_it_launches_once_and_not_again_while_slc_stays_up(monkeypatch):
    """SLC runs for the whole flight. One launch, not one every five
    seconds."""
    assert drive(monkeypatch, [False, True, True, True, True]) == [True]


def test_slc_already_running_is_not_news(monkeypatch):
    """Installing this mid-session must not fling a panel open on top of
    whatever is being done."""
    assert drive(monkeypatch, [True, True, True]) == []


def test_it_launches_when_slc_is_restarted(monkeypatch):
    assert drive(monkeypatch, [True, True, False, True]) == [True]


def test_it_does_not_open_a_second_panel(monkeypatch):
    assert drive(monkeypatch, [False, True], panel_open=True) == []


def test_closing_the_panel_leaves_it_closed(monkeypatch):
    """SLC stays up, the pilot closes the panel. Reopening it would make
    the window impossible to get rid of."""
    assert drive(monkeypatch, [False, True, True, True]) == [True]


def test_it_keeps_watching_after_an_error(monkeypatch):
    """The process list is not always readable. Dying here leaves the
    pilot believing something is watching when nothing is."""
    boom = RuntimeError("cannot read the process list")
    assert drive(monkeypatch, [boom, boom, False, True]) == [True]


def test_an_error_on_the_very_first_look_is_survived(monkeypatch):
    """The first read happens before the loop. Letting it through uncaught
    is the bug this file exists to have caught once already."""
    boom = RuntimeError("UI is not up yet")
    assert drive(monkeypatch, [boom, False, True]) == [True]


# -- installing ------------------------------------------------------------

@pytest.fixture
def scratch_key(monkeypatch):
    """A registry key of our own.

    Deliberately not the real Run key: a suite that writes there is a
    suite that can leave something starting at every login.
    """
    parent = r"Software\SLCVoiceAI-tests"
    path = parent + r"\run"
    monkeypatch.setattr(watcher, "RUN_KEY", path)
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
    yield path
    # Both levels: leaving the parent behind is a suite that litters the
    # registry of everyone who ever runs it.
    for key in (path, parent):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass


def test_install_then_uninstall_leaves_nothing(scratch_key):
    assert watcher.installed() is None
    watcher.install()
    assert watcher.installed() == watcher.command()
    watcher.uninstall()
    assert watcher.installed() is None


def test_uninstalling_what_was_never_installed_is_not_an_error(scratch_key):
    assert watcher.uninstall() == 0


def test_installing_twice_does_not_double_up(scratch_key):
    watcher.install()
    watcher.install()
    assert watcher.installed() == watcher.command()
    watcher.uninstall()


def test_the_command_runs_without_a_console_when_it_can():
    """pythonw, not python: a console window at every login on a machine
    about to run a flight simulator is its own bug report."""
    if watcher.PYTHONW.is_file():
        assert "pythonw.exe" in watcher.command()
    assert str(Path(watcher.__file__).resolve()) in watcher.command()


def test_it_launches_the_same_thing_a_double_click_would():
    assert watcher.LAUNCHER.name == "SLCVoiceAI.bat"
    assert watcher.LAUNCHER.is_file(), "the launcher this points at is gone"


def test_it_costs_nothing_to_import():
    """It runs from login to shutdown. Importing the bridge here would
    load hundreds of megabytes to answer a question ctypes answers in a
    millisecond."""
    source = (ROOT / "tools" / "watch_for_slc.py").read_text(encoding="utf-8")
    for heavy in ("import numpy", "import webview", "uiautomation",
                  "faster_whisper", "from slcvoiceai"):
        assert heavy not in source, "the watcher imports " + heavy
