"""Turning what someone wrote in config.toml into a key to listen for.

Shared by the push-to-talk key and the hotkeys, so the two cannot drift
apart on what counts as a valid name - and so a typo is reported the same
way whichever setting it was in.
"""

from __future__ import annotations

from pynput import keyboard

#: Names that are not single characters. Taken from pynput rather than
#: written out, so a new pynput release does not make this list a lie.
NAMED = sorted(name for name in dir(keyboard.Key) if not name.startswith("_"))


#: KeyboardEvent.code -> the name config.toml uses, for the keys whose
#: names do not simply lowercase. Everything regular - the letters, the
#: digits and the function keys - is worked out below instead of listed.
#:
#: The page sends the raw browser code and this does the translating, so
#: the panel never has to know what pynput calls anything.
_BROWSER_CODES = {
    "Escape": "esc",
    "Enter": "enter",
    "NumpadEnter": "enter",
    "Space": "space",
    "Backspace": "backspace",
    "Tab": "tab",
    "CapsLock": "caps_lock",
    "NumLock": "num_lock",
    "ScrollLock": "scroll_lock",
    "PrintScreen": "print_screen",
    "Pause": "pause",
    "Insert": "insert",
    "Delete": "delete",
    "Home": "home",
    "End": "end",
    "PageUp": "page_up",
    "PageDown": "page_down",
    "ArrowUp": "up",
    "ArrowDown": "down",
    "ArrowLeft": "left",
    "ArrowRight": "right",
    "ControlLeft": "ctrl_l",
    "ControlRight": "ctrl_r",
    "AltLeft": "alt_l",
    "AltRight": "alt_r",
    "ShiftLeft": "shift_l",
    "ShiftRight": "shift_r",
    "ContextMenu": "menu",
}


def from_browser_code(code: str) -> str | None:
    """A KeyboardEvent.code as a name config.toml accepts, or None.

    None means "that key cannot be bound", which the panel reports rather
    than saving something that would fail to arm at the next start.
    """
    code = (code or "").strip()
    if code in _BROWSER_CODES:
        return _BROWSER_CODES[code]
    if code.startswith("Key") and len(code) == 4:          # KeyA -> a
        return code[3].lower()
    if code.startswith("Digit") and len(code) == 6:        # Digit7 -> 7
        return code[5]
    if code.startswith("F") and code[1:].isdigit():        # F13 -> f13
        name = code.lower()
        return name if hasattr(keyboard.Key, name) else None
    return None


def parse_key(name: str, setting: str = "key"):
    """A config string such as 'f13', 'insert' or 'x' as a pynput key.

    `setting` only appears in the error, which matters more than it looks:
    with several bindings in one file, "unrecognised key" without saying
    which line it came from means reading all of them.
    """
    cleaned = (name or "").strip().lower()
    if hasattr(keyboard.Key, cleaned):
        return getattr(keyboard.Key, cleaned)
    if len(cleaned) == 1:
        return keyboard.KeyCode.from_char(cleaned)
    raise ValueError(
        "Unrecognised {setting} {name!r}. Use a single character, or one of: "
        "{names}".format(setting=setting, name=name, names=", ".join(NAMED))
    )
