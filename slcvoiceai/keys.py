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
