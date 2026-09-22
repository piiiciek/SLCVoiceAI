"""Launch SLCVoiceAI by itself when Self-Loading Cargo starts.

    python tools/watch_for_slc.py --install     run it at every Windows login
    python tools/watch_for_slc.py --uninstall   stop doing that
    python tools/watch_for_slc.py               run it now, in this console
    python tools/watch_for_slc.py --status      what is installed, what is up

Windows has no usable event for "a process started". The one there is
lives in the Security log and needs administrator rights plus process
auditing turned on, which is a lot to ask of a flight simulator add-on.
So this polls - and because it polls, it is written to cost nothing:

* No imports beyond the standard library. No numpy, no whisper, no UI
  Automation. Importing the bridge here would load hundreds of megabytes
  to answer a question ctypes answers in a millisecond.
* Two API calls every few seconds, over a process snapshot.

It launches SLCVoiceAI.bat exactly as double-clicking it would, on the
transition from "SLC is not running" to "SLC is running". Not while SLC
stays up, and not if the panel is already open - so closing the panel
during a session leaves it closed, which is what closing something means.

A note worth reading before installing this. The panel loads Whisper when
it starts, and starting it with SLC means starting it AFTER the simulator,
which is when the graphics card has least to spare. That is what pushes
the model down to 'small' and what makes the occasional command take tens
of seconds. Convenient, and slower. Launching the panel before the
simulator is still the better habit; this is for when you would rather not
have to remember.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The one import from the package, and a deliberate one: the registry
# value has to be written the same way whether the pilot ticks the box in
# the panel or runs --install here. slcvoiceai.autostart pulls in nothing
# but winreg, so this stays as cheap as the rest of the file.
from slcvoiceai import autostart  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "SLCVoiceAI.bat"
PYTHONW = autostart.PYTHONW

#: The panel's own window title, which is how we tell it is already up.
PANEL_TITLE = "SLCVoiceAI"

log = logging.getLogger("watcher")


# -- is SLC running? -------------------------------------------------------

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


def is_running(process_name: str) -> bool:
    """Is a process with this executable name alive?

    A process snapshot rather than a window search: SLC is running for a
    moment before it draws anything, and waiting for the window would mean
    launching into a half-started application.
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return False
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return False
        wanted = process_name.lower()
        while True:
            if entry.szExeFile.lower() == wanted:
                return True
            if not k32.Process32NextW(snapshot, ctypes.byref(entry)):
                return False
    finally:
        k32.CloseHandle(snapshot)


# -- is the panel already up? ----------------------------------------------

def panel_is_open() -> bool:
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if not u32.IsWindowVisible(hwnd):
            return True
        length = u32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        u32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.strip() == PANEL_TITLE:
            found.append(hwnd)
            return False
        return True

    u32.EnumWindows(visit, 0)
    return bool(found)


# -- launching -------------------------------------------------------------

def launch() -> bool:
    if not LAUNCHER.is_file():
        log.error("Cannot find %s - has the folder moved?", LAUNCHER)
        return False
    try:
        # The same thing double-clicking does, detached, with no console
        # flashing up on a machine that is about to be a flight simulator.
        subprocess.Popen(["cmd", "/c", "start", "", str(LAUNCHER)],
                         cwd=str(ROOT),
                         creationflags=0x08000000)   # CREATE_NO_WINDOW
        log.info("SLC appeared - launched %s", LAUNCHER.name)
        return True
    except Exception as exc:
        log.error("Could not launch %s: %s", LAUNCHER.name, exc)
        return False


# -- the loop --------------------------------------------------------------

def watch(process_name: str, every: float, settle: float, once: bool) -> int:
    # One watcher is enough. The panel starts one the moment the box is
    # ticked and Windows starts one at every login, so the two meet the
    # first time the machine is restarted - and two watchers racing to
    # open one panel is how you get two panels.
    claim = autostart.claim()
    if claim is None:
        log.info("Another watcher is already running; nothing to do here.")
        return 0
    try:
        return _watch(process_name, every, settle, once)
    finally:
        # Handing it back matters even though the process usually ends
        # here: --once returns, and the tests call this over and over in
        # one process, where a claim kept past the end would make every
        # run after the first think a watcher was already up.
        autostart.release(claim)


def _watch(process_name: str, every: float, settle: float, once: bool) -> int:
    # Only a watcher that was started as the installed one stands down
    # when the entry goes; running this by hand is not affected.
    installed_at_start = autostart.is_installed()

    log.info("Watching for %s every %.0fs. Launching %s when it appears.",
             process_name, every, LAUNCHER.name)
    def looking() -> bool:
        # Every read, including the first. The process list is not always
        # readable, and letting that first one through uncaught would end
        # the watcher on the spot - leaving something installed at login
        # that quietly does nothing for the rest of the session.
        try:
            return is_running(process_name)
        except Exception:
            log.debug("Could not read the process list", exc_info=True)
            return False

    # SLC already being up when this starts is not news - at login it will
    # not be, and installing the watcher mid-session should not fling a
    # panel open on top of whatever is being done.
    seen = looking()
    if seen:
        log.info("%s is already running; waiting for the next time.",
                 process_name)
    while True:
        time.sleep(every)

        # Unticking the box in the panel removes the entry; this is how
        # the watcher already running hears about it, rather than lingering
        # until the machine is next restarted.
        if installed_at_start and not autostart.is_installed():
            log.info("Starting with SLC was switched off; stopping.")
            return 0

        running = looking()

        if running and not seen:
            if panel_is_open():
                log.info("%s appeared, but the panel is already open.",
                         process_name)
            elif launch():
                if once:
                    return 0
                # The panel takes several seconds to draw its window.
                # Without this pause the next poll sees no panel and opens
                # a second one.
                time.sleep(settle)
        seen = running


# -- installing ------------------------------------------------------------

command = autostart.command
installed = autostart.installed


def install() -> int:
    if not PYTHONW.is_file():
        print("Warning: {p} is missing, so this will use {s} instead and a "
              "console window may appear at login.".format(p=PYTHONW,
                                                           s=sys.executable))
    autostart.install()
    print("Installed. From the next login, SLCVoiceAI starts when SLC does.")
    print("  " + command())
    print("Remove it with:  python tools/watch_for_slc.py --uninstall")
    return 0


def uninstall() -> int:
    if installed() is None:
        print("It was not installed; nothing to remove.")
        return 0
    autostart.uninstall()
    print("Removed. Nothing starts at login any more.")
    return 0


def status(process_name: str) -> int:
    where = installed()
    print("runs at login : {w}".format(w=where or "no"))
    print("launcher      : {p} ({e})".format(
        p=LAUNCHER, e="found" if LAUNCHER.is_file() else "MISSING"))
    print("{n:<14}: {r}".format(n=process_name,
                                r="running" if is_running(process_name) else "not running"))
    print("panel         : {p}".format(
        p="open" if panel_is_open() else "not open"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true",
                    help="run this watcher at every Windows login")
    ap.add_argument("--uninstall", action="store_true",
                    help="stop running it at login")
    ap.add_argument("--status", action="store_true",
                    help="show what is installed and what is running")
    ap.add_argument("--once", action="store_true",
                    help="exit after launching the panel once")
    ap.add_argument("--process", default="SLC.exe",
                    help="the process to wait for (default: SLC.exe)")
    ap.add_argument("--every", type=float, default=5.0,
                    help="seconds between looks (default: 5)")
    ap.add_argument("--settle", type=float, default=45.0,
                    help="seconds to leave the panel alone after launching "
                         "it, while it draws its window (default: 45)")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.FileHandler(ROOT / "slcvoiceai-watcher.log",
                                      encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])

    if args.install:
        return install()
    if args.uninstall:
        return uninstall()
    if args.status:
        return status(args.process)
    try:
        return watch(args.process, args.every, args.settle, args.once)
    except KeyboardInterrupt:
        log.info("Stopped.")
        return 0


if __name__ == "__main__":
    if os.name != "nt":
        sys.exit("This only means anything on Windows.")
    sys.exit(main())
