"""Running the SLC watcher at every Windows login.

One value under HKEY_CURRENT_USER, which needs no administrator rights
and is removed as easily as it is added. It points at
`tools/watch_for_slc.py`, which is what actually waits for SLC and opens
the panel - this module only decides whether that watcher gets started
with Windows.

Deliberately light. The panel imports it, and so does the watcher, which
is written to pull in nothing heavier than the standard library: it runs
from login to shutdown, so `import numpy` here would be hundreds of
megabytes held for the sake of one registry value.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCHER = ROOT / "tools" / "watch_for_slc.py"
PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "SLCVoiceAI Watcher"

#: Held by whichever watcher is currently running, so a second one can
#: tell there is no work for it and stand down. A kernel object rather
#: than a file of process ids: it cannot be left behind by a watcher that
#: was killed, and it cannot name a process id that has since been
#: handed to something else.
MUTEX_NAME = r"Local\SLCVoiceAI-Watcher"


def argv() -> list[str]:
    """The watcher, as a list to spawn.

    pythonw, not python, when it is there: a console window appearing at
    every login on a machine about to run a flight simulator is its own
    bug report.
    """
    runner = PYTHONW if PYTHONW.is_file() else Path(sys.executable)
    return [str(runner), str(WATCHER)]


def command() -> str:
    """The same thing as one quoted string, which is what Run holds."""
    return " ".join('"{a}"'.format(a=a) for a in argv())


def installed() -> str | None:
    """The command currently registered, or None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0]
    except FileNotFoundError:
        return None
    except OSError:
        return None


def is_installed() -> bool:
    return installed() is not None


def install() -> None:
    """Register it, or re-register it if the folder has moved."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command())


def uninstall() -> None:
    """Remove it. Not being there is not a failure."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_NAME)
    except FileNotFoundError:
        pass


def set_installed(on: bool) -> None:
    install() if on else uninstall()


# -- the watcher that is running now ---------------------------------------
#
# Registering it and running it are different questions. The Run key is
# only read at login, so ticking the box and expecting the next SLC to
# open the panel meant rebooting first - which is not what the box says,
# and is how this was found: the entry was there, correct, and nothing
# was watching.

def claim() -> int | None:
    """Take the watcher mutex, or None if another watcher holds it.

    The handle is the claim: hold it for as long as the watcher runs and
    let the process ending release it.
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.CreateMutexW(None, True, MUTEX_NAME)
    if not handle:
        return None
    if ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
        k32.CloseHandle(handle)
        return None
    return handle


def release(handle: int) -> None:
    """Give the claim back, for a watcher that stops without exiting."""
    ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)


def running() -> bool:
    """Is a watcher up right now?

    Asked by taking the claim and immediately dropping it again, which
    only works because nothing here waits on the mutex.
    """
    handle = claim()
    if handle is None:
        return True
    release(handle)
    return False


def start() -> bool:
    """Start a watcher now, without waiting for the next login.

    Starting a second one is harmless - it finds the mutex taken and
    stops - but it is still worth not doing.
    """
    if running():
        return False
    # Detached and console-less: it outlives the panel that started it,
    # and nothing flashes up on screen.
    subprocess.Popen(argv(), cwd=str(ROOT), close_fds=True,
                     creationflags=0x00000008 | 0x00000200)
    return True
