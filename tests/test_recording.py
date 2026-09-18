"""Capturing the whole of what was said.

Four clips in one flight came back too short to decode - one of them a
quarter of a four-second sentence, transcribed as nothing at all. The stream
was being opened after the key went down, so the pilot was already talking
while it opened.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai import audio  # noqa: E402

RATE = 16000
BLOCK = 1600          # 0.1s, as a real callback delivers


def recorder(max_seconds=20.0, min_seconds=0.35):
    ptt = audio.PushToTalk.__new__(audio.PushToTalk)
    ptt.cfg = SimpleNamespace(sample_rate=RATE, max_seconds=max_seconds,
                              min_seconds=min_seconds)
    ptt._held = threading.Event()
    ptt._stop = threading.Event()
    ptt._clips = queue.Queue()
    ptt.device = None
    return ptt


def feed(blocks, seconds):
    for _ in range(int(seconds * RATE / BLOCK)):
        blocks.put(np.full((BLOCK, 1), 0.1, dtype="float32"))


def collect_once(ptt, blocks):
    """Run _collect until it has produced a clip or given up."""
    thread = threading.Thread(target=ptt._collect, args=(blocks,), daemon=True)
    thread.start()
    return thread


def test_a_press_produces_a_clip_of_what_was_said():
    ptt = recorder()
    blocks = queue.Queue()
    collect_once(ptt, blocks)

    ptt._held.set()
    feed(blocks, 2.0)
    time.sleep(0.4)
    ptt._held.clear()

    captured_at, clip = ptt._clips.get(timeout=3)
    assert 1.5 < len(clip) / RATE <= 2.1, len(clip) / RATE
    ptt._stop.set()


def test_audio_recorded_before_the_press_is_not_included():
    """The stream runs the whole time now, so silence from between commands
    is sitting in the queue. It must not be glued to the front of the clip."""
    ptt = recorder()
    blocks = queue.Queue()
    feed(blocks, 5.0)                  # a long quiet stretch, already queued
    collect_once(ptt, blocks)
    time.sleep(0.4)                    # let it drain

    ptt._held.set()
    feed(blocks, 1.0)
    time.sleep(0.3)
    ptt._held.clear()

    _at, clip = ptt._clips.get(timeout=3)
    assert len(clip) / RATE < 2.0, "stale audio was prepended"
    ptt._stop.set()


def test_a_tap_too_short_to_be_speech_is_dropped():
    ptt = recorder(min_seconds=0.35)
    blocks = queue.Queue()
    collect_once(ptt, blocks)

    ptt._held.set()
    feed(blocks, 0.2)
    time.sleep(0.3)
    ptt._held.clear()

    time.sleep(0.4)
    assert ptt._clips.empty()
    ptt._stop.set()


def test_a_very_long_press_stops_at_the_limit():
    ptt = recorder(max_seconds=1.0)
    blocks = queue.Queue()
    collect_once(ptt, blocks)

    ptt._held.set()
    feed(blocks, 5.0)
    _at, clip = ptt._clips.get(timeout=3)
    assert len(clip) / RATE <= 1.2
    ptt._held.clear()
    ptt._stop.set()


def test_the_log_compares_the_clip_with_the_press(caplog):
    """The diagnostic that settles it: a clip far shorter than the press is
    the recorder's fault, one that matches it is a key let go early."""
    ptt = recorder()
    blocks = queue.Queue()
    with caplog.at_level("INFO"):
        collect_once(ptt, blocks)
        ptt._held.set()
        feed(blocks, 1.0)
        time.sleep(0.3)
        ptt._held.clear()
        ptt._clips.get(timeout=3)
    assert "key held" in caplog.text
    ptt._stop.set()


def test_losing_audio_is_reported_not_swallowed(caplog):
    ptt = recorder()
    blocks = queue.Queue()
    with caplog.at_level("WARNING"):
        collect_once(ptt, blocks)
        ptt._held.set()
        feed(blocks, 0.5)              # half a second of a long press
        time.sleep(1.5)
        ptt._held.clear()
        ptt._clips.get(timeout=3)
    assert "not captured" in caplog.text
    ptt._stop.set()


def test_the_stream_is_opened_once_and_not_per_press():
    """The regression this exists for."""
    source = (Path(__file__).resolve().parent.parent
              / "slcvoiceai" / "audio.py").read_text(encoding="utf-8")
    loop = source.split("def _record_loop(")[1].split("def _collect(")[0]
    assert "sd.InputStream(" in loop, "the stream moved somewhere unexpected"
    collect = source.split("def _collect(")[1]
    assert "sd.InputStream(" not in collect, (
        "the stream is being opened per press again")
