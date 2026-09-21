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
    # Toolbar chrome. Its name comes from cmdMainButton and says nothing about
    # what it does (it collapses SLC's icon row), while acting as a magnet for
    # any sentence containing "button": "press the button to fasten the
    # seatbelt" reached it at 0.71 and hid the toolbar.
    "main button",
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
    # SLC asks "Exit Self-Loading Cargo?" before quitting, and the buttons on
    # that dialog are named in the user's Windows language - 'Tak' and 'Nie'
    # on a Polish install. Nothing in DENYLIST catches those, so the one
    # button that ends the flight outright would otherwise be on offer under
    # a name no denylist can anticipate.
    "exit self-loading cargo",
)


#: AutomationId prefixes for controls that begin a flight, and so end
#: whatever flight is already running. SLC has six, and they cannot be
#: recognised by their labels: cmdStartNewPassengerLineFlight is displayed
#: as 'LINE PILOT MODE', which says nothing about starting anything. The id
#: is the honest signal, and it does not change with the display language.
FLIGHT_STARTER_IDS = ("cmdstartnew", "cmdrestoreprevious")

#: Fallbacks, for an SLC that renames its controls. Matched against the
#: label and against the tooltip, both lowercased.
_FLIGHT_STARTER_TEXT = ("start new", "restore previous flight")


def starts_a_new_flight(name: str, automation_id: str = "",
                        tooltip: str = "") -> bool:
    """Would pressing this control throw away the flight in progress?

    Unlike everything in DENYLIST these are perfectly reasonable at SLC's
    launcher, which is why they are not simply refused - but SLC keeps them
    on the toolbar during the flight too, a few icons along from the
    seatbelt sign.
    """
    if (automation_id or "").strip().lower().startswith(FLIGHT_STARTER_IDS):
        return True
    for text in (name, tooltip):
        low = (text or "").strip().lower()
        if any(hint in low for hint in _FLIGHT_STARTER_TEXT) and "flight" in low:
            return True
    return False


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
    #: SLC's own description of the control, where it has one. Worth keeping
    #: because the name above is sometimes ours rather than SLC's.
    tooltip: str = ""
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


_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _visible_windows_with_pids() -> list[tuple[int, int]]:
    """(handle, process id) for every visible top-level window on the desktop.

    Hidden windows are left out deliberately. A process of SLC's size keeps
    dozens of them alive - IME frames, broadcast sinks, video handlers - and
    walking into their trees would cost time to find nothing.
    """
    found: list[tuple[int, int]] = []

    def visit(hwnd, _lparam):
        # An exception raised inside a ctypes callback cannot propagate: it
        # would be printed and swallowed, and the enumeration would carry on
        # regardless. Keep it from happening at all.
        try:
            if _USER32.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                _USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    found.append((hwnd, pid.value))
        except Exception:
            pass
        return True

    callback = _WNDENUMPROC(visit)
    _USER32.EnumWindows(callback, 0)
    return found


def _handles_of_process(process_name: str, listing=None) -> list[int]:
    """Which of those windows belong to a process with this name."""
    if listing is None:
        listing = _visible_windows_with_pids()
    target = process_name.strip().lower()
    names: dict[int, str] = {}
    handles = []
    for hwnd, pid in listing:
        if pid not in names:
            names[pid] = _process_name(pid).lower()
        if names[pid] == target:
            handles.append(hwnd)
    return handles


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


def _identity(control):
    """Enough of a control to recognise it again, or None."""
    try:
        rect = control.BoundingRectangle
        box = (rect.left, rect.top, rect.right, rect.bottom)
        return (control.ControlTypeName,
                (control.AutomationId or "").strip(),
                (control.Name or "").strip(), box)
    except Exception:
        return None


def is_topmost(control) -> bool:
    """Would a click at this control's middle actually reach it?

    `is_visible` asks whether a control is laid out. That is not the same
    as being on top, and SLC stacks controls: the three menus each have a
    BACK button, two of them at identical coordinates, and one menu's BACK
    sits under the GO AHEAD of a conversation. All of them report a real
    rectangle; only one can be pressed.

    Windows can settle it - ask what is at the point. Used only to break a
    tie between two same-named controls, because it costs a COM call and
    the answer only matters when the name alone is not enough.

    False when it cannot tell, which includes SLC being covered by another
    window. That is the safe direction: the caller then keeps whichever
    control it already had, which is what it did before this existed.
    """
    mine = _identity(control)
    if mine is None:
        return False
    try:
        rect = control.BoundingRectangle
        x = (rect.left + rect.right) // 2
        y = (rect.top + rect.bottom) // 2
        hit = auto.ControlFromPoint(x, y)
    except Exception:
        return False

    # The hit lands on the innermost element under the cursor, which for
    # an SLC button is the Text drawn inside it - so walk back up.
    for _ in range(4):
        if hit is None:
            return False
        if _identity(hit) == mine:
            return True
        try:
            hit = hit.GetParentControl()
        except Exception:
            return False
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


def help_text(control) -> str:
    """The tooltip SLC shows on hover - what the control really does.

    Worth reading because the label above can be a fiction of ours. An
    icon-only control has no accessible name, so humanise_id() invents one
    from its id, and an invented name can be wrong about the control rather
    than merely clumsy: cmdStandBy reads as a perfectly sensible 'Stand By'
    and its tooltip says "Close SLC or Restart Flight".
    """
    try:
        return (control.HelpText or "").strip()
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

        Found through Win32 and then handed to UI Automation one handle at a
        time. Asking UIA for the desktop's children and reading a process id
        off each cost about half a second on every single command - a quarter
        of the whole scan - where EnumWindows plus one lookup per SLC window
        costs about ten milliseconds. Checked against the old lookup with
        SLC's main window alone, with a satellite window open, and after
        closing it again: the same windows every time.

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
                for handle in _handles_of_process(self.process_name):
                    control = auto.ControlFromHandle(handle)
                    if control is not None:
                        found.append(control)
                if found:
                    if attempt:
                        log.info("UI scan recovered on attempt %d", attempt + 1)
                    return found
                # Nothing. Before reporting that SLC is not there - which
                # reads downstream as "SLC is offering no buttons" and loses
                # the command in silence - spend the half second and ask UIA
                # the old way. It costs nothing on the path that matters,
                # because that path never gets here.
                return self._windows_via_uia()
            except Exception as exc:
                last = exc
                log.debug("UI scan attempt %d failed: %s", attempt + 1, exc)
                time.sleep(0.15 * (attempt + 1))
        raise UIAUnavailable(
            "could not read SLC's UI after {n} attempts: {e}".format(n=attempts, e=last))

    def _windows_via_uia(self) -> list:
        """The old lookup: walk the desktop's children and match on process."""
        found = []
        root = auto.GetRootControl()
        for win in root.GetChildren():
            pid = getattr(win, "ProcessId", 0)
            if pid and _process_name(pid).lower() == self.process_name.lower():
                found.append(win)
        if found:
            log.info("Found %d SLC window(s) only via the slower lookup.",
                     len(found))
        return found

    def list_actions(self, include_disabled: bool = False,
                     include_hidden: bool = False) -> list[Action]:
        """Every control SLC is actually offering the pilot right now.

        Re-walks the tree on every call: SLC's communications popup appears
        and disappears constantly, and stale UIA references throw.

        Pass include_hidden=True to get the whole command tree instead - only
        useful for exploring what SLC can do, never for routing a command.
        """
        actions: list[Action] = []
        #: name -> [index into actions, is that one on top?]. The second
        #: entry stays None until a collision makes it worth finding out.
        seen: dict[tuple[str, str], list] = {}

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
                    # The name is not always the control's own. cmdStandBy
                    # has no accessible name, humanises to a reasonable
                    # looking 'Stand By', and its tooltip reads "Close SLC
                    # or Restart Flight" - it was pressed in flight by
                    # someone answering a radio check with "5 by 5". So the
                    # denylist gets to see what SLC says the control does,
                    # not only what we decided to call it.
                    hint = help_text(ctl)
                    if hint and is_denied(hint):
                        log.debug("Skipping %r - its tooltip says %r",
                                  name, hint)
                        continue
                    action = Action(
                        name=name,
                        window=win_name,
                        control_type=ctl.ControlTypeName.replace("Control", ""),
                        automation_id=(ctl.AutomationId or "").strip(),
                        enabled=enabled,
                        tooltip=hint,
                        _control=ctl,
                    )
                    key = (win_name, name.lower())
                    if key not in seen:
                        seen[key] = [len(actions), None]   # index, topmost?
                        actions.append(action)
                        continue

                    # Two controls, one name, one window, both laid out.
                    # SLC stacks the three menus' BACK buttons in the same
                    # place; only one of them can be clicked. Which one is
                    # a question the rectangle cannot answer, so it is only
                    # asked when there is a collision - never on the common
                    # path, where it would cost a COM call per control.
                    slot = seen[key]
                    if slot[1] is None:
                        slot[1] = is_topmost(actions[slot[0]]._control)
                    if slot[1]:
                        continue                  # the kept one is on top
                    if not is_topmost(ctl):
                        continue                  # neither is; keep the first
                    log.debug("Two %r in %s; taking the one on top (%s)",
                              name, win_name, action.automation_id)
                    actions[slot[0]] = action
                    slot[1] = True
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
