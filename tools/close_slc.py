"""Close Self-Loading Cargo, answering its confirmation dialog.

SLC never exits on a close request alone. It puts up 'Exit Self-Loading
Cargo?' - "All flight progress will be lost." - and waits. Anything that
asks it to close and then walks away leaves that dialog sitting on screen
with SLC still running, which is exactly what happens when you measure
against a live SLC and tidy up afterwards.

    python tools/close_slc.py
    python tools/close_slc.py --dry-run     # say what it would press
    python tools/close_slc.py --force       # kill it if it will not go

The confirm button is found by its dialog control id, not its label: this is
a standard Windows #32770 dialog, so Yes is always id 6 whatever language
Windows is installed in ('Tak' on a Polish install, 'Yes' on an English
one). The label is only a fallback.

This is a developer utility and nothing in the bridge may ever call it. The
button it presses is the one thing SLCVoiceAI must never press - DENYLIST in
slc_ui.py exists to keep 'exit self-loading cargo' away from the router, and
WINDOW_DENYLIST keeps this dialog's buttons out of reach as well. Closing
SLC is something a person asks for explicitly, at a shell.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import comtypes
import uiautomation as auto

from slcvoiceai import slc_ui

WM_CLOSE = 0x0010
#: Standard Windows dialog control ids. Language-independent.
IDOK, IDYES, IDNO = "1", "6", "7"
#: Only used if the dialog turns out not to be a standard one.
AFFIRMATIVE = ("tak", "yes", "ok", "ja", "oui", "si", "sim", "da")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def pids_named(process_name: str) -> list[int]:
    TH32CS_SNAPPROCESS = 0x00000002

    class ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    entry = ENTRY()
    entry.dwSize = ctypes.sizeof(ENTRY)
    found = []
    if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
        while True:
            if entry.szExeFile.lower() == process_name.lower():
                found.append(entry.th32ProcessID)
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
    kernel32.CloseHandle(snap)
    return found


def name_of(window) -> str:
    try:
        return window.Name or ""
    except Exception:
        return ""


def main_window(ui: slc_ui.SlcUI):
    for win in ui.windows():
        low = name_of(win).lower()
        if low.startswith("exit "):
            continue
        if "self-loading cargo" in low:
            return win
    return None


def confirmation(ui: slc_ui.SlcUI):
    """The 'Exit Self-Loading Cargo?' dialog, if it is up."""
    for win in ui.windows():
        try:
            if win.ClassName == "#32770" and name_of(win).lower().startswith("exit"):
                return win
        except Exception:
            continue
    return None


def confirm_button(ui: slc_ui.SlcUI, dialog):
    """The Yes button: by dialog control id first, by label second."""
    by_name = None
    for control in ui._walk(dialog):
        try:
            if control.ControlTypeName != "ButtonControl":
                continue
            if (control.AutomationId or "").strip() in (IDYES, IDOK):
                return control
            if (control.Name or "").strip().lower() in AFFIRMATIVE:
                by_name = by_name or control
        except Exception:
            continue
    return by_name


def press(control) -> bool:
    for pattern_id in (auto.PatternId.InvokePattern,
                       auto.PatternId.LegacyIAccessiblePattern):
        try:
            pattern = control.GetPattern(pattern_id)
        except Exception:
            pattern = None
        if pattern is None:
            continue
        try:
            if pattern_id == auto.PatternId.InvokePattern:
                pattern.Invoke()
            else:
                pattern.DoDefaultAction()
            return True
        except Exception:
            continue
    try:
        control.Click(simulateMove=False)
        return True
    except Exception:
        return False


def wait_until_gone(process_name: str, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not pids_named(process_name):
            return True
        time.sleep(0.25)
    return not pids_named(process_name)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--process", default="SLC.exe")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the dialog and the button, press nothing")
    ap.add_argument("--force", action="store_true",
                    help="terminate the process if it does not close")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
    ui = slc_ui.SlcUI(args.process)

    if not pids_named(args.process):
        print("{p} is not running.".format(p=args.process))
        return 0

    window = main_window(ui)
    if window is None:
        print("{p} is running but has no window to close.".format(p=args.process))
        return 1

    print("Asking {n!r} to close...".format(n=name_of(window)))
    user32.PostMessageW(window.NativeWindowHandle, WM_CLOSE, 0, 0)

    dialog = None
    deadline = time.time() + 6.0
    while time.time() < deadline:
        time.sleep(0.3)
        dialog = confirmation(ui)
        if dialog is not None:
            break
        if not pids_named(args.process):
            print("It closed without asking.")
            return 0

    if dialog is None:
        print("No confirmation appeared and it is still running.")
        return 1 if not args.force else _force(args)

    print("It asked: {t!r}".format(t=name_of(dialog)))
    button = confirm_button(ui, dialog)
    if button is None:
        print("Could not find the confirm button. Buttons on the dialog:")
        for control in ui._walk(dialog):
            try:
                if control.ControlTypeName == "ButtonControl":
                    print("   {n!r}  id={i!r}".format(
                        n=control.Name, i=control.AutomationId))
            except Exception:
                continue
        return 1

    label = (button.Name or "?").strip()
    ident = (button.AutomationId or "").strip()
    if args.dry_run:
        print("DRY RUN - would press {l!r} (id {i!r})".format(l=label, i=ident))
        # Leaving a modal dialog up would be a strange way for a dry run to
        # change nothing, so answer it the other way and put SLC back.
        for control in ui._walk(dialog):
            try:
                if (control.AutomationId or "").strip() == IDNO:
                    press(control)
                    print("Dismissed the dialog; {p} left running.".format(
                        p=args.process))
                    break
            except Exception:
                continue
        return 0

    print("Pressing {l!r} (id {i!r})...".format(l=label, i=ident))
    if not press(button):
        print("Could not press it.")
        return 1

    if wait_until_gone(args.process, args.timeout):
        print("{p} closed.".format(p=args.process))
        return 0

    print("Confirmed, but {p} is still running after {t:.0f}s.".format(
        p=args.process, t=args.timeout))
    return _force(args) if args.force else 1


def _force(args) -> int:
    for pid in pids_named(args.process):
        print("Terminating pid {p}...".format(p=pid))
        handle = kernel32.OpenProcess(0x0001, False, pid)   # PROCESS_TERMINATE
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
    return 0 if wait_until_gone(args.process, 5.0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
