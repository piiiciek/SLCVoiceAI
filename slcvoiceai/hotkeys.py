"""Three calls, on three key combinations.

The aircraft has buttons for this. On an Airbus the ATT and MECH calls on
the overhead ring the cabin and the ground crew, and SLC answers both -
but MSFS does not reliably pass ATT through, so the one that opens the
cabin is the one that sticks.

What is bound is fixed and what it presses is fixed. Only the keys are
the pilot's to choose, because these three are the whole point: a general
"bind any key to any button" was more rope than anyone asked for, and
every extra field was another way to end up with a binding pointing at
nothing.

Combinations, not single keys: MSFS already has a binding for nearly
every bare key, so `ctrl+q` is far likelier to be free than `q`.

Two things this is careful about:

* **Never block the listener.** pynput's Windows listener is a low-level
  keyboard hook. Work done on that thread delays every keystroke on the
  machine, and reading SLC takes seconds - so a press hands off to a
  worker thread and returns immediately.

* **Never queue.** A scan outlasts a keypress comfortably. A call already
  working ignores further presses rather than stacking up a run of clicks
  nobody asked for.
"""

from __future__ import annotations

import logging
import threading

from pynput import keyboard

from .keys import to_pynput

log = logging.getLogger(__name__)

#: The calls that can be bound, and the SLC buttons each one presses.
#:
#: The names on the right are what SLC really calls them - "P A SYSTEM"
#: with the spaces, because that is the string the matcher normalises
#: against. Nothing here is user-editable; the keys on the left are what
#: config.toml and the panel talk about.
#:
#: "back" takes one binding for all three menus, but not for the reason it
#: first appeared to. SLC lays out THREE BACK buttons at once -
#:
#:     cmdTannoySwitchBackGroundCrew   172x19 @ 13,1341
#:     cmdTannoySwitchBackCabinCrew    172x19 @ 13,1341   <- same point
#:     cmdTannoySwitchBackPassengers   172x19 @ 13,1324   <- under GO AHEAD
#:
#: - all reporting a real rectangle. The logs showed one only because
#: list_actions drops same-named controls in the same window; the tree has
#: three. A rectangle means "laid out", not "on top", so picking whichever
#: the walk reached first was right only by luck.
#:
#: slc_ui.is_topmost settles it by asking Windows what is at the point, so
#: the list carries the BACK the pilot can actually click - which is the
#: one belonging to the menu that is open. Hence one binding.
ACTIONS: dict[str, tuple[str, ...]] = {
    "intercom": ("INTERCOM",),
    "ground": ("GROUND CREW",),
    "pa": ("P A SYSTEM",),
    "back": ("BACK",),
}


class Hotkeys:
    """Watches for the bound combinations and hands each press to `on_fire`.

    `on_fire(action, buttons)` is called on a worker thread, where
    `action` is one of ACTIONS and `buttons` is what it presses.
    """

    def __init__(self, bindings: dict[str, str], on_fire):
        self._on_fire = on_fire
        self._listener: keyboard.GlobalHotKeys | None = None
        self._lock = threading.Lock()
        self._busy: set = set()

        # pynput does the combination matching, including treating left
        # and right modifiers as one and swallowing the auto-repeat of a
        # held key - all of which is fiddly to get right by hand.
        self._combos: dict[str, str] = {}
        self._map = {}
        for action, spec in bindings.items():
            if action not in ACTIONS or not spec:
                continue
            self._combos[action] = spec
            self._map[to_pynput(spec)] = self._pressed(action)

    def _pressed(self, action: str):
        def fire():
            with self._lock:
                if action in self._busy:
                    log.info("%s is still working; ignoring this press.", action)
                    return
                self._busy.add(action)
            threading.Thread(target=self._work, args=(action,),
                             daemon=True).start()
        return fire

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if not self._map:
            return
        self._listener = keyboard.GlobalHotKeys(self._map)
        self._listener.start()
        for action, spec in self._combos.items():
            log.info("Hotkey %s -> %s (%s)", spec, action,
                     " then ".join(ACTIONS[action]))

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    def describe(self) -> str:
        return ", ".join("{s} -> {a}".format(s=spec, a=action)
                         for action, spec in self._combos.items())

    # -- internals ---------------------------------------------------------
    def _work(self, action: str) -> None:
        try:
            self._on_fire(action, ACTIONS[action])
        except Exception:
            # A hotkey that throws must not take the listener - or the
            # bridge - down with it.
            log.exception("Hotkey %s failed.", action)
        finally:
            with self._lock:
                self._busy.discard(action)
