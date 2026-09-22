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


# -- the first run, which is a download and not a hang ---------------------
#
# "loading the speech model, this takes a few seconds" is true from the
# second run onwards. The first fetches the model: 1.5 GB on the default
# choice. Somebody watching a disabled button for ten minutes reasonably
# decides it has hung and kills it, leaving half a model behind.

def test_it_says_nothing_about_a_model_already_downloaded(tmp_path, monkeypatch, caplog):
    from slcvoiceai import stt

    hub = tmp_path / "hub"
    (hub / "models--Systran--faster-whisper-medium").mkdir(parents=True)
    monkeypatch.setattr(stt, "_cache_dir", lambda: hub)

    with caplog.at_level("INFO", logger="slcvoiceai.stt"):
        stt._warn_if_not_downloaded("medium")
    assert caplog.text.strip() == "", "it warned about a model that is there"


def test_it_warns_with_the_size_before_a_first_download(tmp_path, monkeypatch, caplog):
    from slcvoiceai import stt

    monkeypatch.setattr(stt, "_cache_dir", lambda: tmp_path / "hub")
    with caplog.at_level("INFO", logger="slcvoiceai.stt"):
        stt._warn_if_not_downloaded("medium")
    assert "1500" in caplog.text and "once" in caplog.text
    assert "not a hang" in caplog.text, (
        "the whole point is telling them it has not frozen")


def test_a_model_of_their_own_is_left_alone(tmp_path, monkeypatch, caplog):
    """A path in config.toml is someone's own model directory, and guessing
    at a download size for it would be inventing one."""
    from slcvoiceai import stt

    monkeypatch.setattr(stt, "_cache_dir", lambda: tmp_path / "hub")
    mine = tmp_path / "my-whisper"
    mine.mkdir()
    with caplog.at_level("INFO", logger="slcvoiceai.stt"):
        stt._warn_if_not_downloaded(str(mine))
    assert caplog.text.strip() == ""


def test_an_unknown_name_still_says_it_is_downloading(tmp_path, monkeypatch, caplog):
    from slcvoiceai import stt

    monkeypatch.setattr(stt, "_cache_dir", lambda: tmp_path / "hub")
    with caplog.at_level("INFO", logger="slcvoiceai.stt"):
        stt._warn_if_not_downloaded("some-new-model")
    assert "downloaded" in caplog.text or "downloading" in caplog.text


def test_looking_is_never_worth_failing_a_start_over(monkeypatch, caplog):
    """It runs on the path to loading the model. An exception here would
    stop the bridge starting over a cosmetic question."""
    from slcvoiceai import stt

    def boom():
        raise OSError("the cache is on a drive that went away")

    monkeypatch.setattr(stt, "_cache_dir", boom)
    stt._warn_if_not_downloaded("medium")          # must not raise


def test_it_looks_where_huggingface_really_keeps_them(monkeypatch):
    """HF_HOME moves the cache, and a check that ignored it would announce
    a download on every start for anyone who sets it."""
    from slcvoiceai import stt

    monkeypatch.setenv("HF_HOME", r"D:\models\hf")
    assert stt._cache_dir() == Path(r"D:\models\hf") / "hub"
    monkeypatch.delenv("HF_HOME")
    assert stt._cache_dir() == Path.home() / ".cache" / "huggingface" / "hub"


def test_every_model_the_picker_can_choose_has_a_size():
    """Otherwise the one message that matters falls back to the vague one."""
    from slcvoiceai import stt

    for free_mb in (14000, 3856, 2500, 1500, 800, 200):
        with _with_gpu(free_mb):
            model, _device, _ct = choose("auto")
        assert model in stt.MODEL_MB, (
            "auto can pick {m}, which has no download size".format(m=model))


def test_check_hardware_says_when_it_still_has_to_download(tmp_path, monkeypatch):
    """The command where someone finds out which model they get is the
    place to find out it is a 2.9 GB fetch."""
    from slcvoiceai import hardware, stt

    monkeypatch.setattr(stt, "_cache_dir", lambda: tmp_path / "hub")
    with _with_gpu(14000):
        said = hardware.describe()
    assert "large-v3" in said and "2900 MB" in said and "once" in said


def test_check_hardware_stays_quiet_once_it_is_there(tmp_path, monkeypatch):
    from slcvoiceai import hardware, stt

    hub = tmp_path / "hub"
    (hub / "models--Systran--faster-whisper-large-v3").mkdir(parents=True)
    monkeypatch.setattr(stt, "_cache_dir", lambda: hub)
    with _with_gpu(14000):
        said = hardware.describe()
    assert "downloaded" not in said and "MB, once" not in said
    assert "large-v3" in said, "it stopped saying which model it picks"


def test_the_note_survives_having_no_gpu(tmp_path, monkeypatch):
    """The CPU branch is a different return statement, and it was the one
    left out the first time this was written."""
    from slcvoiceai import hardware, stt

    monkeypatch.setattr(stt, "_cache_dir", lambda: tmp_path / "hub")
    with _with_gpu(None):
        said = hardware.describe()
    assert "No NVIDIA GPU" in said
    assert "not downloaded yet" in said
