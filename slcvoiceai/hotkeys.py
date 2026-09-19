"""Keys bound straight to SLC buttons, with no speaking involved.

The aircraft has physical buttons for this. On an Airbus the ATT and MECH
calls on the overhead ring the cabin and the ground crew, and SLC answers
both - but MSFS does not always pass ATT through, so the one that opens
the cabin is the one that sticks. A key on the keyboard is not a fix for
that, but it does the same job from the same seat.

Nothing here knows what any SLC button is called. Every binding comes from
config.toml, so a pilot can bind whatever keys they have free to whatever
their SLC is offering:

    [hotkeys]
    insert = "INTERCOM"
    delete = "GROUND CREW"
    home = "P A SYSTEM"

Two things this is careful about:

* **Never block the listener.** pynput's Windows listener is a low-level
  keyboard hook. Work done on that thread delays every keystroke on the
  machine, and reading SLC takes seconds - so a press hands off to a
  worker thread and returns immediately.

* **Never queue.** Holding a key auto-repeats, and a scan outlasts a
  press comfortably. A binding already working ignores further presses
  rather than stacking up a run of clicks nobody asked for.
"""

from __future__ import annotations

import logging
import threading

from pynput import keyboard

from .keys import parse_key

log = logging.getLogger(__name__)


class Hotkeys:
    """Watches for bound keys and hands each press to `on_fire`.

    `on_fire(label, buttons)` is called on a worker thread, where `label`
    is the key as written in config.toml and `buttons` is the sequence of
    SLC buttons that press asked for.
    """

    def __init__(self, bindings: dict[str, tuple[str, ...]], on_fire):
        # A list of pairs rather than a dict keyed by the key object:
        # pynput hands the listener a KeyCode carrying a virtual-key code,
        # which compares equal to one built from a character but need not
        # hash the same. PushToTalk compares with == for the same reason.
        self._bound = [(parse_key(name, "hotkeys." + name), name, tuple(buttons))
                       for name, buttons in bindings.items()]
        self._on_fire = on_fire
        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()
        self._held: set = set()
        self._busy: set = set()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if not self._bound:
            return
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        for _key, label, buttons in self._bound:
            log.info("Hotkey %s -> %s", label, " then ".join(repr(b) for b in buttons))

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    def describe(self) -> str:
        return ", ".join("{k} -> {b}".format(k=label, b=" then ".join(buttons))
                         for _key, label, buttons in self._bound)

    # -- internals ---------------------------------------------------------
    def _match(self, key):
        for bound, label, buttons in self._bound:
            if key == bound:
                return label, buttons
        return None

    def _on_press(self, key) -> None:
        found = self._match(key)
        if found is None:
            return
        label, buttons = found

        with self._lock:
            if label in self._held:
                return                    # auto-repeat, not a second press
            self._held.add(label)
            if label in self._busy:
                log.info("Hotkey %s is still working; ignoring this press.", label)
                return
            self._busy.add(label)

        threading.Thread(target=self._fire, args=(label, buttons),
                         daemon=True).start()

    def _on_release(self, key) -> None:
        found = self._match(key)
        if found is not None:
            with self._lock:
                self._held.discard(found[0])

    def _fire(self, label: str, buttons: tuple[str, ...]) -> None:
        try:
            self._on_fire(label, buttons)
        except Exception:
            # A hotkey that throws must not take the listener - or the
            # bridge - down with it.
            log.exception("Hotkey %s failed.", label)
        finally:
            with self._lock:
                self._busy.discard(label)
