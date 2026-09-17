"""Read and drive Self-Loading Cargo through UI Automation.

SLC is a WPF application, so every button it draws with a standard control
template is exposed in the UIA tree with a name, an enabled flag and an
Invoke pattern. That gives us both halves of the bridge: we can see which
commands are available right now, and we can trigger the one we want.

Nothing here modifies SLC. It is the same mechanism a screen reader uses.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass

import uiautomation as auto

log = logging.getLogger(__name__)

# Controls the bridge must never press, however the model phrases its intent.
# Matched case-insensitively as substrings of the control name.
DENYLIST = (
    # Ends the session outright.
    "exit self-loading cargo",
    "close self-loading cargo",
    "exit slc",
    "close slc",
    # Throws away the flight you are in the middle of.
    "cancel single flight",
    "do not restore previous flight",
    "dispatch next flight",
    # Licensing, settings and chrome - never something you "say".
    "activate self-loading cargo",
    "join discord",
    "user manual",
    "apply changes and exit",
    "close window",
    "delete",
    "reset",
    # Settings-window commit buttons, in case a config window slips through.
    "save changes and close",
    "apply changes",
)

#: Windows that are configuration or meta UI rather than flight controls.
#: Matched case-insensitively as substrings of the window title.
#:
#: Deliberately a denylist, not an allowlist: SLC puts genuinely voice-worthy
#: controls in several satellite windows (doors, tannoy, cabin management),
#: and an allowlist would silently cut them off.
WINDOW_DENYLIST = (
    "settings window",
    "voice recognition prompt window",
    "audio manager",
)


class UIAUnavailable(RuntimeError):
    """The UI Automation tree could not be read this time.

    Distinct from "SLC is offering nothing": one is a failed read, the other
    is a real answer. Reporting a failed read as an empty result loses
    commands silently, which is exactly how "Please repeat." vanished with
    nothing in the log but a misleading "no buttons right now".
    """


def is_denied_window(title: str) -> bool:
    low = (title or "").strip().lower()
    return any(bad in low for bad in WINDOW_DENYLIST)


@dataclass
class Action:
    """One invokable SLC control."""

    name: str
    window: str
    control_type: str
    automation_id: str
    enabled: bool
    _control: object = None

    def __str__(self) -> str:
        return self.name

    def invoke(self) -> None:
        ctl = self._control
        if ctl is None:
            raise RuntimeError("Action {n!r} has no live control".format(n=self.name))

        pattern = _get_pattern(ctl, auto.PatternId.InvokePattern)
        if pattern is not None:
            pattern.Invoke()
            return
        pattern = _get_pattern(ctl, auto.PatternId.TogglePattern)
        if pattern is not None:
            pattern.Toggle()
            return
        pattern = _get_pattern(ctl, auto.PatternId.SelectionItemPattern)
        if pattern is not None:
            pattern.Select()
            return
        ctl.Click(simulateMove=False)


#: Patterns that mean "this control can be activated", in the order we try them.
ACTIVATION_PATTERNS = (
    auto.PatternId.InvokePattern,
    auto.PatternId.TogglePattern,
    auto.PatternId.SelectionItemPattern,
)


def _get_pattern(control, pattern_id):
    """Return the pattern object, or None if the control does not support it.

    uiautomation raises on some controls rather than returning None, and a
    stale element (SLC's popup closing mid-walk) raises too.
    """
    try:
        return control.GetPattern(pattern_id)
    except Exception:
        return None


def _is_activatable(control) -> bool:
    return any(_get_pattern(control, pid) is not None for pid in ACTIVATION_PATTERNS)


def _process_name(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        k32.CloseHandle(handle)


def is_denied(name: str) -> bool:
    low = name.strip().lower()
    return any(bad in low for bad in DENYLIST)


#: AutomationId prefixes SLC uses for its controls, stripped when we humanise.
_ID_PREFIXES = ("cmd", "btn", "img", "chk", "tgl")


def humanise_id(automation_id: str) -> str:
    """Turn 'cmdToggleDoorMode' into 'Toggle Door Mode'.

    Most of SLC's toolbar buttons are icon-only: they carry no accessible
    name at all, just an AutomationId. Those ids are descriptive enough to
    route on once split back into words.
    """
    ident = automation_id.strip()
    if not ident:
        return ""
    for prefix in _ID_PREFIXES:
        if ident.lower().startswith(prefix) and len(ident) > len(prefix):
            ident = ident[len(prefix):]
            break

    words: list[str] = []
    current = ""
    for char in ident:
        if char in "_-":
            if current:
                words.append(current)
                current = ""
            continue
        # Start a new word at a lower->upper transition, but keep runs of
        # capitals together so "PA" and "GSX" survive intact.
        if char.isupper() and current and not current[-1].isupper():
            words.append(current)
            current = char
        else:
            current += char
    if current:
        words.append(current)
    return " ".join(w for w in words if w).strip()


def is_visible(control) -> bool:
    """Is this control actually on screen right now?

    SLC keeps its entire command tree alive in the WPF visual tree - all ~336
    buttons, every one of them reporting IsEnabled=True and IsOffscreen=True
    whether or not the pilot can currently use it. The only property that
    actually distinguishes "on screen" from "collapsed" is the bounding
    rectangle: live controls have a real one, hidden controls are 0x0.

    This is what keeps the bridge honest about context. Without it the model
    would be offered every command in the game at once and could fire a
    descent announcement while still at the gate.
    """
    try:
        rect = control.BoundingRectangle
    except Exception:
        return False
    if rect is None:
        return False
    try:
        return (rect.right - rect.left) > 0 and (rect.bottom - rect.top) > 0
    except Exception:
        return False


def label_for(control) -> str:
    """Best human-readable label for a control: its name, else its id."""
    try:
        name = (control.Name or "").strip()
    except Exception:
        name = ""
    if len(name) >= 2:
        return name
    try:
        return humanise_id(control.AutomationId or "")
    except Exception:
        return ""


class SlcUI:
    """A live view of what Self-Loading Cargo can currently be told to do."""

    def __init__(self, process_name: str = "SLC.exe", max_depth: int = 25):
        self.process_name = process_name
        self.max_depth = max_depth

    def is_running(self) -> bool:
        try:
            return bool(self.windows())
        except UIAUnavailable:
            return False

    def windows(self, attempts: int = 3) -> list:
        """Top-level windows owned by SLC.

        UIA occasionally fails a whole enumeration with a transient COM error
        (EVENT_E_ALL_SUBSCRIBERS_FAILED and friends) even though SLC is
        running normally. Retrying costs milliseconds and almost always
        succeeds; treating the first failure as "nothing is there" silently
        drops whatever the pilot just said.
        """
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                found = []
                root = auto.GetRootControl()
                for win in root.GetChildren():
                    pid = getattr(win, "ProcessId", 0)
                    if pid and _process_name(pid).lower() == self.process_name.lower():
                        found.append(win)
                if attempt:
                    log.info("UI scan recovered on attempt %d", attempt + 1)
                return found
            except Exception as exc:
                last = exc
                log.debug("UI scan attempt %d failed: %s", attempt + 1, exc)
                time.sleep(0.15 * (attempt + 1))
        raise UIAUnavailable(
            "could not read SLC's UI after {n} attempts: {e}".format(n=attempts, e=last))

    def list_actions(self, include_disabled: bool = False,
                     include_hidden: bool = False) -> list[Action]:
        """Every control SLC is actually offering the pilot right now.

        Re-walks the tree on every call: SLC's communications popup appears
        and disappears constantly, and stale UIA references throw.

        Pass include_hidden=True to get the whole command tree instead - only
        useful for exploring what SLC can do, never for routing a command.
        """
        actions: list[Action] = []
        seen: set[tuple[str, str]] = set()

        for win in self.windows():
            win_name = win.Name or "(untitled)"
            if is_denied_window(win_name):
                log.debug("Skipping configuration window %r", win_name)
                continue
            for ctl in self._walk(win, prune_hidden=not include_hidden):
                try:
                    # Order matters for speed, not just correctness: every
                    # property read is a COM round-trip. _walk has already
                    # dropped collapsed subtrees, so what arrives here is the
                    # ~75 nodes on screen rather than all ~330.
                    enabled = bool(getattr(ctl, "IsEnabled", True))
                    if not enabled and not include_disabled:
                        continue
                    # Most SLC toolbar buttons are icon-only and carry no
                    # accessible name - fall back to their AutomationId.
                    name = label_for(ctl)
                    if len(name) < 2:
                        continue
                    if not _is_activatable(ctl):
                        continue
                    if is_denied(name):
                        log.debug("Skipping denylisted control %r", name)
                        continue
                    key = (win_name, name.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    actions.append(Action(
                        name=name,
                        window=win_name,
                        control_type=ctl.ControlTypeName.replace("Control", ""),
                        automation_id=(ctl.AutomationId or "").strip(),
                        enabled=enabled,
                        _control=ctl,
                    ))
                except Exception:
                    continue
        return actions

    def _walk(self, node, depth: int = 0, prune_hidden: bool = True):
        """Yield descendants, skipping collapsed subtrees by default.

        A control with a 0x0 bounding rectangle is collapsed, and so is
        everything beneath it - there is no point paying a COM round-trip per
        node to walk into it. On a live SLC this takes the traversal from 558
        nodes to 75 while returning exactly the same actions.
        """
        if depth > self.max_depth:
            return
        try:
            children = node.GetChildren()
        except Exception:
            return
        for child in children:
            if prune_hidden and not is_visible(child):
                continue
            yield child
            yield from self._walk(child, depth + 1, prune_hidden)
