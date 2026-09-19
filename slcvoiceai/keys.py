"""Key combinations, as config.toml writes them and as pynput wants them.

MSFS has a binding for nearly every bare key, so a single key is often not
available to give away. A combination usually is - which is why hotkeys
are written `ctrl+q` rather than `q`, and why this module exists at all.

Three spellings of the same thing, kept apart on purpose:

    config.toml     ctrl+shift+q     what a pilot writes and reads
    pynput          <ctrl>+<shift>+q what GlobalHotKeys parses
    the panel       Ctrl + Shift + Q what the keycap shows

Left and right modifiers are the same modifier here. pynput's listener
canonicalises ctrl_l and ctrl_r to Key.ctrl, so `ctrl+q` fires on either -
which is what anyone binding a hotkey expects.
"""

from __future__ import annotations

from pynput import keyboard

#: What a modifier may be called in config.toml, and what it becomes.
#: Spellings differ by keyboard and by habit; they all mean four things.
MODIFIERS = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "option": "alt",
    "shift": "shift",
    "cmd": "cmd", "win": "cmd", "super": "cmd", "meta": "cmd",
}

#: The order modifiers are written in, so one combination has one spelling
#: whichever order it was typed or pressed in.
ORDER = ("ctrl", "alt", "shift", "cmd")

#: Names that are not single characters. Taken from pynput rather than
#: written out, so a new pynput release does not make this list a lie.
NAMED = sorted(name for name in dir(keyboard.Key) if not name.startswith("_"))

#: KeyboardEvent.code -> the name config.toml uses, for the keys whose
#: names do not simply lowercase. The letters, digits and function keys
#: are worked out below instead of listed.
_BROWSER_CODES = {
    "Escape": "esc", "Enter": "enter", "NumpadEnter": "enter",
    "Space": "space", "Backspace": "backspace", "Tab": "tab",
    "CapsLock": "caps_lock", "NumLock": "num_lock",
    "ScrollLock": "scroll_lock", "PrintScreen": "print_screen",
    "Pause": "pause", "Insert": "insert", "Delete": "delete",
    "Home": "home", "End": "end", "PageUp": "page_up", "PageDown": "page_down",
    "ArrowUp": "up", "ArrowDown": "down",
    "ArrowLeft": "left", "ArrowRight": "right",
    "ContextMenu": "menu",
}

#: Pressing Ctrl on its own is not a binding, it is the start of one.
_BARE_MODIFIER_CODES = ("Control", "Alt", "Shift", "Meta", "OS")


def base_key(name: str):
    """The non-modifier half of a combination, as a pynput key."""
    cleaned = (name or "").strip().lower()
    if hasattr(keyboard.Key, cleaned):
        return getattr(keyboard.Key, cleaned)
    if len(cleaned) == 1:
        return keyboard.KeyCode.from_char(cleaned)
    return None


def parse_key(name: str, setting: str = "key"):
    """A single key, for settings that cannot take a combination.

    The push-to-talk key is held down while speaking, so it is one key and
    not a combination - holding Ctrl for the length of a sentence is not
    something to ask of anyone.
    """
    key = base_key(name)
    if key is None:
        raise ValueError(
            "Unrecognised {setting} {name!r}. Use a single character, or one "
            "of: {names}".format(setting=setting, name=name,
                                 names=", ".join(NAMED)))
    return key


def parse_combo(spec: str) -> tuple[tuple[str, ...], str]:
    """`"ctrl+shift+q"` as (('ctrl', 'shift'), 'q'). Raises on nonsense."""
    parts = [part.strip().lower() for part in (spec or "").split("+")]
    parts = [part for part in parts if part]
    if not parts:
        raise ValueError("Empty key combination.")

    *front, last = parts
    modifiers = []
    for part in front:
        if part not in MODIFIERS:
            raise ValueError(
                "{part!r} is not a modifier. Use one of: {names}".format(
                    part=part, names=", ".join(sorted(set(MODIFIERS)))))
        modifiers.append(MODIFIERS[part])

    if last in MODIFIERS:
        raise ValueError(
            "A combination needs a key as well as modifiers: {spec!r}".format(
                spec=spec))
    if base_key(last) is None:
        raise ValueError(
            "Unrecognised key {last!r}. Use a single character, or one of: "
            "{names}".format(last=last, names=", ".join(NAMED)))

    ordered = tuple(m for m in ORDER if m in modifiers)
    return ordered, last


def canonical(spec: str) -> str:
    """One spelling per combination: `"Q+CTRL"` and `"ctrl+q"` both give
    `"ctrl+q"`, so two bindings that are the same are seen to be."""
    modifiers, key = parse_combo(spec)
    return "+".join(modifiers + (key,))


def to_pynput(spec: str) -> str:
    """The form `keyboard.GlobalHotKeys` parses."""
    modifiers, key = parse_combo(spec)
    tail = key if len(key) == 1 else "<{k}>".format(k=key)
    return "+".join(["<{m}>".format(m=m) for m in modifiers] + [tail])


def pretty(spec: str) -> str:
    """What the panel shows on the keycap."""
    modifiers, key = parse_combo(spec)
    words = [m.capitalize() for m in modifiers]
    words.append(key.upper() if len(key) == 1
                 else key.replace("_", " ").title())
    return " + ".join(words)


def from_browser(event: dict) -> str | None:
    """A KeyboardEvent from the panel as a combination, or None.

    None means "that cannot be bound", which the panel says at the
    keypress rather than saving something that fails to arm later. A bare
    modifier is None too: holding Ctrl is the start of a combination, not
    a binding.
    """
    code = str((event or {}).get("code") or "").strip()
    if not code or code.startswith(_BARE_MODIFIER_CODES):
        return None

    if code in _BROWSER_CODES:
        key = _BROWSER_CODES[code]
    elif code.startswith("Key") and len(code) == 4:          # KeyA -> a
        key = code[3].lower()
    elif code.startswith("Digit") and len(code) == 6:        # Digit7 -> 7
        key = code[5]
    elif code.startswith("F") and code[1:].isdigit():        # F13 -> f13
        key = code.lower()
        if not hasattr(keyboard.Key, key):
            return None
    else:
        return None

    modifiers = [name for name, held in (("ctrl", event.get("ctrl")),
                                         ("alt", event.get("alt")),
                                         ("shift", event.get("shift")),
                                         ("cmd", event.get("meta")))
                 if held]
    try:
        return canonical("+".join(modifiers + [key]))
    except ValueError:
        return None
