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

    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg
        self.key = _parse_key(cfg.ptt_key)
        self.device = resolve_device(cfg.input_device)
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
        if key == self.key:
            self._held.set()

    def _on_release(self, key) -> None:
        if key == self.key:
            self._held.clear()

    def _record_loop(self) -> None:
        while not self._stop.is_set():
            if not self._held.wait(timeout=0.25):
                continue

            frames: list[np.ndarray] = []
            blocks = queue.Queue()

            def callback(indata, _frames, _time, status):
                if status:
                    log.debug("audio status: %s", status)
                blocks.put(indata.copy())

            try:
                with sd.InputStream(samplerate=self.cfg.sample_rate,
                                    channels=1, dtype="float32",
                                    device=self.device, callback=callback):
                    max_frames = int(self.cfg.max_seconds * self.cfg.sample_rate)
                    total = 0
                    while self._held.is_set() and total < max_frames:
                        try:
                            block = blocks.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        frames.append(block)
                        total += len(block)
            except Exception as exc:
                log.error("Recording failed: %s", exc)
                self._held.clear()
                continue

            if not frames:
                continue
            clip = np.concatenate(frames, axis=0).flatten()
            duration = len(clip) / self.cfg.sample_rate
            if duration < self.cfg.min_seconds:
                log.debug("Clip too short (%.2fs), ignored", duration)
                continue
            log.info("Captured %.2fs of audio", duration)
            self._clips.put((time.time(), clip))
