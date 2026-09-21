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

import sys
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCHER = ROOT / "tools" / "watch_for_slc.py"
PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "SLCVoiceAI Watcher"


def command() -> str:
    """What Windows should run at login.

    pythonw, not python, when it is there: a console window appearing at
    every login on a machine about to run a flight simulator is its own
    bug report.
    """
    runner = PYTHONW if PYTHONW.is_file() else Path(sys.executable)
    return '"{r}" "{w}"'.format(r=runner, w=WATCHER)


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
