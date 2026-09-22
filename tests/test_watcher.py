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

sys.path.insert(0, str(ROOT))
from slcvoiceai import autostart  # noqa: E402


class Enough(Exception):
    """Raised from the patched sleep to end the loop."""


@pytest.fixture(autouse=True)
def hermetic_claim(monkeypatch):
    """A watcher claim name of our own, for every test in this file.

    The real one is held by any watcher actually running on the machine -
    and once starting with SLC works, that is the normal state. Left
    alone, watch() would find the claim taken and return before doing
    anything, and half this file would fail on a machine where the
    feature is switched on. Which is exactly how this was found.
    """
    monkeypatch.setattr(autostart, "MUTEX_NAME", r"Local\SLCVoiceAI-tests-claim")


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
    # Both names: the tool delegates to the package, so patching
    # only one of them would write to the real Run key.
    monkeypatch.setattr(autostart, "RUN_KEY", path)
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
    millisecond.

    slcvoiceai.autostart is the one exception, and pulls in winreg alone -
    the registry value has to be written the same way whether it is the
    tick box in the panel or --install here.
    """
    assert _heavy_imports(ROOT / "tools" / "watch_for_slc.py") == []


def test_the_shared_module_stays_cheap():
    """What the exception above depends on."""
    assert _heavy_imports(ROOT / "slcvoiceai" / "autostart.py") == []


#: Names that mean megabytes. slcvoiceai.autostart is not among them: it
#: imports winreg and nothing else, which is the whole reason it exists.
HEAVY = ("numpy", "webview", "uiautomation", "faster_whisper", "sounddevice",
         "pynput", "rapidfuzz", "torch")


def _heavy_imports(path: Path) -> list[str]:
    """Modules a file really imports, read as code rather than as text.

    Searching the source for "import numpy" also finds it in a comment
    explaining why numpy is not imported - which is how this test first
    failed, on a docstring it was written to protect.
    """
    import ast

    found = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            root = name.split(".")[0]
            if root in HEAVY or name.startswith(("slcvoiceai.gui",
                                                 "slcvoiceai.stt",
                                                 "slcvoiceai.app")):
                found.append(name)
    return found


def test_the_panel_and_the_tool_register_the_same_thing():
    """Two ways to switch this on - the tick box and --install - and one
    of them writing a different value would leave the box showing the
    wrong state, or two entries fighting."""
    from slcvoiceai import autostart

    assert watcher.command() == autostart.command()
    assert watcher.installed is autostart.installed


# -- one watcher, and only while it is wanted ------------------------------
#
# The bug these are here for: the tick box wrote the registry value and
# nothing else, so the entry was correct and no watcher existed until the
# machine was next restarted. Now the box starts one - which makes "is one
# already up" and "is it still wanted" questions that have to be right.

def no_sleeping(monkeypatch, limit=6):
    """Let the loop run `limit` times, then stop it.

    The loop never returns on its own when it is working, so a fault here
    would hang the suite rather than fail it.
    """
    count = {"n": 0}

    def sleep(_seconds):
        count["n"] += 1
        if count["n"] > limit:
            raise Enough

    monkeypatch.setattr(watcher.time, "sleep", sleep)
    return count


def test_a_second_watcher_stands_down(monkeypatch):
    """Windows starts one at login and the panel starts one when the box
    is ticked, so the two meet the first time the machine restarts. Two
    watchers racing to open one panel is how you get two panels."""
    launched = []
    monkeypatch.setattr(watcher.autostart, "claim", lambda: None)
    monkeypatch.setattr(watcher, "launch", lambda: launched.append(True))
    monkeypatch.setattr(watcher, "is_running", lambda _n: True)
    # Without this the loop is what stops the test, and a broken guard
    # hangs the suite instead of failing it - which is exactly what
    # happened the first time this was written.
    no_sleeping(monkeypatch, limit=3)

    assert watcher.watch("SLC.exe", every=0, settle=0, once=False) == 0
    assert launched == [], "it went on watching anyway"


def test_it_stops_when_starting_with_slc_is_switched_off(monkeypatch):
    """Unticking the box removes the registry value. The watcher already
    running has to notice, or it keeps opening the panel until the machine
    is restarted - and the box says it is off."""
    answers = iter([True, True, False, False, False])
    monkeypatch.setattr(watcher.autostart, "is_installed",
                        lambda: next(answers, False))
    monkeypatch.setattr(watcher, "is_running", lambda _n: False)
    no_sleeping(monkeypatch)

    assert watcher.watch("SLC.exe", every=0, settle=0, once=False) == 0


def test_running_it_by_hand_is_not_stopped_by_the_entry(monkeypatch):
    """Someone running the tool from a terminal never installed it, so an
    entry that is not there is not a message to stop."""
    monkeypatch.setattr(watcher.autostart, "is_installed", lambda: False)
    monkeypatch.setattr(watcher, "is_running", lambda _n: False)
    no_sleeping(monkeypatch, limit=4)

    with pytest.raises(Enough):
        watcher.watch("SLC.exe", every=0, settle=0, once=False)


def test_the_claim_is_handed_back_when_it_stops(monkeypatch):
    """--once returns, and so does standing down. A claim kept past the
    end would make the next watcher think one was already up."""
    monkeypatch.setattr(watcher, "is_running", lambda _n: True)
    monkeypatch.setattr(watcher, "panel_is_open", lambda: False)
    monkeypatch.setattr(watcher, "launch", lambda: True)
    monkeypatch.setattr(watcher.autostart, "is_installed", lambda: False)
    no_sleeping(monkeypatch, limit=3)

    assert not autostart.running(), "something was holding it before we began"
    try:
        watcher.watch("SLC.exe", every=0, settle=0, once=True)
    except Enough:
        pass
    assert not autostart.running(), "the claim was kept after it stopped"



# -- starting it now -------------------------------------------------------
#
# The registry value is read at login and nowhere else, so for a while
# ticking the box in the panel did nothing at all until the machine was
# restarted: the entry was there, correct, and no watcher existed.

def test_nothing_holds_a_fresh_claim():
    assert not autostart.running()


def test_a_claim_is_held_until_it_is_given_back():
    handle = autostart.claim()
    assert handle, "could not take a claim nobody holds"
    try:
        assert autostart.running()
        assert autostart.claim() is None, "two watchers both got the claim"
    finally:
        autostart.release(handle)
    assert not autostart.running()


def test_ticking_the_box_starts_one_now(monkeypatch):
    started = []
    monkeypatch.setattr(autostart.subprocess, "Popen",
                        lambda argv, **kw: started.append((argv, kw)))

    assert autostart.start() is True
    assert len(started) == 1
    argv, kwargs = started[0]
    assert argv == autostart.argv()
    assert kwargs["creationflags"], "a console would flash up at login"


def test_it_does_not_start_a_second_one(monkeypatch):
    started = []
    monkeypatch.setattr(autostart.subprocess, "Popen",
                        lambda argv, **kw: started.append(argv))

    handle = autostart.claim()
    try:
        assert autostart.start() is False
    finally:
        autostart.release(handle)
    assert started == [], "it started one on top of the one already running"


def test_the_string_windows_holds_is_the_list_we_spawn():
    """Two spellings of the same command. Drift between them would mean
    the box starting one thing and login starting another."""
    assert autostart.command() == " ".join(
        '"{a}"'.format(a=a) for a in autostart.argv())


def test_launching_leaves_no_console_behind(monkeypatch):
    """Windows' `start` runs a batch file as `cmd /K`, and /K means "keep
    the window". Every launch used to leave a console sitting at the
    project folder for the rest of the session, and the no-window flag
    could not reach it - it applies to the cmd created here, not to the
    one start goes on to spawn.

    The launcher detaches the panel by itself, so there is nothing for
    start to do here.
    """
    seen = {}
    monkeypatch.setattr(watcher.subprocess, "Popen",
                        lambda argv, **kw: seen.update(argv=argv, kw=kw))

    assert watcher.launch() is True
    assert "start" not in seen["argv"], (
        "launching through start leaves a console window open: "
        + str(seen["argv"]))
    assert seen["argv"] == ["cmd", "/c", str(watcher.LAUNCHER)]
    assert seen["kw"]["creationflags"] & 0x08000000, "a console would flash up"
