"""Push-to-talk microphone capture."""

from __future__ import annotations

import logging
import queue
import threading
import time

import numpy as np
import sounddevice as sd
from pynput import keyboard

from .config import AudioConfig

log = logging.getLogger(__name__)


def _parse_key(name: str):
    """Turn a config string such as 'f13', 'ctrl_r' or 'x' into a pynput key."""
    name = name.strip().lower()
    if hasattr(keyboard.Key, name):
        return getattr(keyboard.Key, name)
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise ValueError(
        "Unrecognised ptt_key {name!r}. Use a pynput key name (f13, ctrl_r, "
        "alt_r, scroll_lock, ...) or a single character.".format(name=name)
    )


def resolve_device(name: str):
    """Map a device-name substring to a sounddevice index; '' = system default."""
    if not name:
        return None
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and name.lower() in dev["name"].lower():
            log.info("Using input device [%d] %s", idx, dev["name"])
            return idx
    available = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
    raise RuntimeError(
        "No input device matching {name!r}. Available: {avail}".format(
            name=name, avail=", ".join(available))
    )


class PushToTalk:
    """Records mono float32 audio for as long as the PTT key is held down."""

    def __init__(self, cfg: AudioConfig, on_talk_start=None):
        self.cfg = cfg
        self.key = _parse_key(cfg.ptt_key)
        self.device = resolve_device(cfg.input_device)
        #: Called once as the key goes down, before a word has been said.
        #: The bridge uses it to start reading SLC's buttons during the
        #: utterance rather than after it.
        self._on_talk_start = on_talk_start
        self._held = threading.Event()
        self._clips: queue.Queue = queue.Queue()
        self._listener: keyboard.Listener | None = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        self._worker = threading.Thread(target=self._record_loop, daemon=True)
        self._worker.start()
        log.info("Push-to-talk armed on %r", self.cfg.ptt_key)

    def stop(self) -> None:
        self._stop.set()
        self._held.clear()
        if self._listener:
            self._listener.stop()

    def clips(self):
        """Yield captured audio, newest only, discarding any backlog.

        If transcription falls behind - a saturated GPU will do it - queued
        utterances are stale by the time they decode, and pressing a button
        for a command given a minute ago is worse than not pressing at all.
        Yields (captured_at, audio) so the caller can refuse stale work.
        """
        while not self._stop.is_set():
            try:
                item = self._clips.get(timeout=0.25)
            except queue.Empty:
                continue

            dropped = 0
            while True:
                try:
                    item = self._clips.get_nowait()
                    dropped += 1
                except queue.Empty:
                    break
            if dropped:
                log.warning("Discarded %d queued utterance(s) - transcription is "
                            "falling behind; only the newest is used", dropped)
            yield item

    # -- internals ---------------------------------------------------------
    def _on_press(self, key) -> None:
        if key != self.key:
            return
        # A held key auto-repeats, so this fires over and over for a single
        # press. Setting an Event twice is harmless, but telling the bridge
        # to start a scan thirty times is not - only the transition counts.
        if self._held.is_set():
            return
        self._held.set()
        if self._on_talk_start is None:
            return
        try:
            self._on_talk_start()
        except Exception:
            # This runs on pynput's listener thread. An exception escaping
            # here takes push-to-talk down for the rest of the session, and
            # the hook is only an optimisation.
            log.exception("Talk-start hook failed; carrying on without it.")

    def _on_release(self, key) -> None:
        if key == self.key:
            self._held.clear()

    def _record_loop(self) -> None:
        """Hold one input stream open and collect from it between presses.

        The stream used to be opened when the key went down and closed when
        it came up. Opening one is not instant, and while it happens the
        pilot is already talking into nothing: four clips in one flight came
        back too short to decode, one of them a quarter of a four-second
        sentence. Opening it once removes that from the path entirely.
        """
        blocks: queue.Queue = queue.Queue()

        def callback(indata, _frames, _time, status):
            if status:
                log.debug("audio status: %s", status)
            blocks.put(indata.copy())

        while not self._stop.is_set():
            try:
                with sd.InputStream(samplerate=self.cfg.sample_rate,
                                    channels=1, dtype="float32",
                                    device=self.device, callback=callback):
                    log.debug("Input stream open on device %s", self.device)
                    self._collect(blocks)
            except Exception as exc:
                log.error("Recording failed: %s", exc)
                self._held.clear()
                # Do not spin on a device that has gone away - a microphone
                # unplugged mid-flight would otherwise fill the log.
                self._stop.wait(1.0)

    def _collect(self, blocks: queue.Queue) -> None:
        """Turn key presses into clips, for as long as the stream is open."""
        max_frames = int(self.cfg.max_seconds * self.cfg.sample_rate)

        while not self._stop.is_set():
            # Everything recorded while nobody was talking. Dropped here
            # rather than never captured, because the stream has been running
            # the whole time.
            while True:
                try:
                    blocks.get_nowait()
                except queue.Empty:
                    break

            if not self._held.wait(timeout=0.25):
                continue
            pressed_at = time.time()

            frames: list[np.ndarray] = []
            total = 0
            while self._held.is_set() and total < max_frames:
                try:
                    block = blocks.get(timeout=0.1)
                except queue.Empty:
                    continue
                frames.append(block)
                total += len(block)

            held = time.time() - pressed_at
            if not frames:
                log.debug("Key held %.2fs but nothing was recorded", held)
                continue
            clip = np.concatenate(frames, axis=0).flatten()
            duration = len(clip) / self.cfg.sample_rate
            if duration < self.cfg.min_seconds:
                log.debug("Clip too short (%.2fs of %.2fs held), ignored",
                          duration, held)
                continue
            # Saying both is what settles an argument the log could not:
            # a clip far shorter than the press is the recorder's fault, one
            # that matches it is a key let go early.
            log.info("Captured %.2fs of audio (key held %.2fs)",
                     duration, held)
            if held - duration > 0.5:
                log.warning("Recorded %.2fs of a %.2fs press - %.2fs of that "
                            "was not captured.", duration, held, held - duration)
            self._clips.put((time.time(), clip))
