"""Model selection must degrade sensibly as the simulator takes the card."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai import hardware  # noqa: E402
from slcvoiceai.hardware import GpuInfo, choose  # noqa: E402


def _with_gpu(free_mb: int | None):
    info = None if free_mb is None else GpuInfo("RTX 5070 Ti", free_mb, 16303)
    return mock.patch.object(hardware, "detect_gpu", return_value=info)


@pytest.mark.parametrize("free_mb,model,device", [
    (14000, "large-v3", "cuda"),   # nothing else running
    (6000, "large-v3", "cuda"),
    (3856, "large-v3", "cuda"),    # measured with MSFS 2024 in a flight
    (2400, "medium", "cuda"),
    (1300, "small", "cuda"),
    (800, "base", "cuda"),
    (400, "base", "cpu"),          # a starved GPU loses to a free CPU
    (None, "base", "cpu"),         # no NVIDIA card, or no nvidia-smi
])
def test_ladder(free_mb, model, device):
    with _with_gpu(free_mb):
        chosen, dev, compute = choose("auto")
    assert (chosen, dev) == (model, device)
    assert compute == "int8"


def test_explicit_model_is_not_second_guessed():
    """A name in config.toml is a decision, not a suggestion."""
    with _with_gpu(400):
        assert choose("large-v3")[0] == "large-v3"


def test_ladder_is_ordered_and_leaves_headroom():
    """Each rung must need less than the one above, and ask for more free
    memory than it uses - a model that only just fits is the one that
    thrashes when the simulator grows."""
    rungs = hardware._LADDER
    needs = [n for _m, n, _r in rungs]
    required = [r for _m, _n, r in rungs]
    assert needs == sorted(needs, reverse=True)
    assert required == sorted(required, reverse=True)
    for _model, need, require in rungs:
        assert require > need


def test_detect_gpu_survives_a_missing_nvidia_smi():
    with mock.patch.object(hardware.shutil, "which", return_value=None):
        assert hardware.detect_gpu() is None


def test_detect_gpu_survives_garbage_output():
    completed = mock.Mock(stdout="not, really, numbers\n")
    with mock.patch.object(hardware.shutil, "which", return_value="nvidia-smi"), \
         mock.patch.object(hardware.subprocess, "run", return_value=completed):
        assert hardware.detect_gpu() is None
