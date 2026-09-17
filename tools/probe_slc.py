"""Dump the UI Automation tree of every Self-Loading Cargo window.

This is the feasibility check for SLCVoiceAI: it answers whether SLC's
communications buttons are readable (and clickable) from outside the process.

Run it while SLC is in a flight with the communications popup OPEN:

    python tools/probe_slc.py --watch

Anything listed as [Button] with enabled=True can be driven by the bridge.
If the popup shows up as an empty or nameless subtree, the buttons are
custom-drawn without automation peers and we need the OCR fallback instead.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

try:
    import uiautomation as auto
except ImportError:
    sys.exit("Missing dependency. Run:  pip install uiautomation")


INTERESTING = {"ButtonControl", "TextControl", "CheckBoxControl",
               "ListItemControl", "MenuItemControl", "RadioButtonControl"}


def slc_windows(process_name: str = "SLC.exe"):
    """Every top-level window owned by the SLC process."""
    found = []
    root = auto.GetRootControl()
    for win in root.GetChildren():
        try:
            if win.ProcessId and _proc_name(win.ProcessId).lower() == process_name.lower():
                found.append(win)
        except Exception:
            continue
    return found


def _proc_name(pid: int) -> str:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        k32.CloseHandle(h)


def walk(node, depth: int = 0, max_depth: int = 25, out=None):
    """Recursively print the automation subtree."""
    if out is None:
        out = []
    if depth > max_depth:
        return out

    try:
        ctype = node.ControlTypeName
        name = (node.Name or "").strip()
        aid = (node.AutomationId or "").strip()
    except Exception as exc:
        # SLC's popup can close while we are walking it; the element goes stale
        # and every property access throws.
        out.append("  " * depth + "  !! stale element: " + str(exc))
        return out

    if name or aid or ctype in INTERESTING:
        enabled = getattr(node, "IsEnabled", None)
        patterns = []
        for label, pattern_id in (("Invoke", auto.PatternId.InvokePattern),
                                  ("Toggle", auto.PatternId.TogglePattern),
                                  ("Select", auto.PatternId.SelectionItemPattern)):
            try:
                if node.GetPattern(pattern_id) is not None:
                    patterns.append(label)
            except Exception:
                pass

        line = "{indent}[{ctype:<16}] {name}".format(
            indent="  " * depth,
            ctype=ctype.replace("Control", ""),
            name=name or "(no name)",
        )
        extras = []
        if aid:
            extras.append("id=" + aid)
        if enabled is not None:
            extras.append("enabled=" + str(enabled))
        if patterns:
            extras.append("patterns=" + "/".join(patterns))
        if extras:
            line += "   <" + ", ".join(extras) + ">"
        out.append(line)

    try:
        for child in node.GetChildren():
            walk(child, depth + 1, max_depth, out)
    except Exception as exc:
        out.append("  " * depth + "  !! cannot enumerate children: " + str(exc))
    return out


def snapshot(max_depth: int, process_name: str = "SLC.exe") -> str:
    wins = slc_windows(process_name)
    if not wins:
        return "Not running: no top-level window owned by {p}.".format(p=process_name)

    lines = ["Windows found: {n}".format(n=len(wins))]
    for win in wins:
        lines.append("")
        lines.append("=" * 78)
        lines.append("WINDOW: '{name}'   class={cls}".format(
            name=win.Name, cls=win.ClassName))
        lines.append("=" * 78)
        lines.extend(walk(win, 0, max_depth))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", action="store_true",
                    help="re-dump every --interval seconds until Ctrl+C")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--depth", type=int, default=25)
    ap.add_argument("--out", help="also append each snapshot to this file")
    ap.add_argument("--process", default="SLC.exe",
                    help="process to inspect (default: SLC.exe); handy for testing")
    args = ap.parse_args()

    def emit(text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        block = "\n----- snapshot {stamp} -----\n{text}\n".format(stamp=stamp, text=text)
        # Write the file first: it is the artefact that matters, and a console
        # that cannot encode a control name (cp1250, cp852, ...) must never
        # take the run down with it.
        if args.out:
            with open(args.out, "a", encoding="utf-8") as fh:
                fh.write(block)
        try:
            print(block)
        except UnicodeEncodeError:
            print(block.encode("ascii", "replace").decode("ascii"))

    if not args.watch:
        emit(snapshot(args.depth, args.process))
        return 0

    print("Watching SLC. Open the communications popup now. Ctrl+C to stop.\n")
    try:
        while True:
            try:
                emit(snapshot(args.depth, args.process))
            except Exception as exc:
                # One bad snapshot (a window closing mid-walk, a COM hiccup)
                # must not end a watch the user left running for a whole flight.
                emit("snapshot failed: {t}: {e}".format(t=type(exc).__name__, e=exc))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
