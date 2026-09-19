"""Writing a setting back to config.toml without wrecking it.

The panel's floor, dry run and language used to last until the window
closed. Making them stick means writing to the one file in this project
that must not be damaged: it holds the API key, and every setting in it
carries a comment explaining what it is for.

So the writer patches lines and never serialises a Config. These tests
are what keeps it that way - most of them are about what the file still
contains afterwards, not about what changed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slcvoiceai import config as config_module  # noqa: E402

REAL_LOOKING = '''\
# SLCVoiceAI configuration.

[llm]
# Paste your key here, or leave it empty and set ANTHROPIC_API_KEY.
# Both values below are too short to be keys, deliberately: a realistic
# length here would look like a leak to a secret scanner.
api_key = "sk-ant-api03-EXAMPLE-NOT-A-KEY"
api_key_env = "ANTHROPIC_API_KEY"

[gemini]
api_key = "AIza-EXAMPLE-NOT-A-KEY"

[behaviour]
# How sure the matcher has to be before anything is pressed.
min_confidence = 0.65
dry_run = false          # decide, never press
log_file = "slcvoiceai.log"

[ui]
language = "auto"
'''


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(REAL_LOOKING, encoding="utf-8")
    return path


def lines_of(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


# -- the thing that must not happen ---------------------------------------

def test_the_api_keys_are_still_there_afterwards(config_file):
    """The reason this is a line patcher and not a dump."""
    config_module.save_settings(config_file, {"behaviour": {"dry_run": True}})
    after = config_file.read_text(encoding="utf-8")
    assert 'sk-ant-api03-EXAMPLE-NOT-A-KEY' in after
    assert 'AIza-EXAMPLE-NOT-A-KEY' in after


def test_a_key_that_lives_in_the_environment_is_not_written_in(config_file, monkeypatch):
    """cfg.api_key resolves from the environment when the file is empty.
    Anything that serialised the Config would bake that secret into the
    file; this must not."""
    config_file.write_text(
        '[llm]\napi_key = ""\napi_key_env = "ANTHROPIC_API_KEY"\n'
        '[behaviour]\nmin_confidence = 0.65\n', encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FROM-THE-ENVIRONMENT")

    cfg = config_module.load(config_file)
    assert cfg.api_key == "sk-ant-FROM-THE-ENVIRONMENT", "test is not testing it"

    config_module.save_settings(config_file, {"behaviour": {"min_confidence": 0.8}})
    assert "FROM-THE-ENVIRONMENT" not in config_file.read_text(encoding="utf-8")


def test_only_the_line_asked_about_changes(config_file):
    before = lines_of(config_file)
    config_module.save_settings(config_file, {"behaviour": {"min_confidence": 0.8}})
    after = lines_of(config_file)

    assert len(before) == len(after)
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(differing) == 1, "it rewrote more than the one setting"
    assert "min_confidence" in after[differing[0]]


def test_the_comments_survive(config_file):
    config_module.save_settings(config_file, {"ui": {"language": "pl"}})
    after = config_file.read_text(encoding="utf-8")
    assert "# SLCVoiceAI configuration." in after
    assert "# How sure the matcher has to be before anything is pressed." in after


def test_a_comment_on_the_same_line_survives(config_file):
    config_module.save_settings(config_file, {"behaviour": {"dry_run": True}})
    line = [l for l in lines_of(config_file) if l.startswith("dry_run")][0]
    assert line == "dry_run = true          # decide, never press"


# -- what it writes --------------------------------------------------------

def test_the_new_value_reads_back_with_the_right_type(config_file):
    config_module.save_settings(config_file, {
        "behaviour": {"min_confidence": 0.8, "dry_run": True},
        "ui": {"language": "pl"},
    })
    cfg = config_module.load(config_file)
    assert cfg.behaviour.min_confidence == 0.8
    assert isinstance(cfg.behaviour.min_confidence, float)
    assert cfg.behaviour.dry_run is True
    assert cfg.ui.language == "pl"


def test_a_round_number_stays_a_float():
    """format(1.0, 'g') is '1', which TOML reads back as an integer - and
    min_confidence would quietly change type."""
    assert config_module._as_toml(1.0) == "1.0"
    assert config_module._as_toml(0.65) == "0.65"


def test_booleans_are_toml_booleans_not_python_ones():
    assert config_module._as_toml(True) == "true"
    assert config_module._as_toml(False) == "false"


def test_a_key_the_file_does_not_mention_is_added_to_its_section(config_file):
    """config.example.toml does not list every setting, so a fresh copy is
    missing most of them."""
    config_module.save_settings(config_file, {"behaviour": {"prescan": False}})
    cfg = config_module.load(config_file)
    assert cfg.behaviour.prescan is False
    assert cfg.behaviour.min_confidence == 0.65, "it disturbed a neighbour"


def test_a_section_the_file_does_not_have_is_appended(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[behaviour]\nmin_confidence = 0.65\n', encoding="utf-8")
    config_module.save_settings(path, {"ui": {"language": "pl"}})
    cfg = config_module.load(path)
    assert cfg.ui.language == "pl"
    assert cfg.behaviour.min_confidence == 0.65


def test_a_file_that_does_not_exist_yet_is_created(tmp_path):
    path = tmp_path / "config.toml"
    config_module.save_settings(path, {"ui": {"language": "en"}})
    assert config_module.load(path).ui.language == "en"


def test_writing_twice_does_not_grow_the_file(config_file):
    config_module.save_settings(config_file, {"behaviour": {"min_confidence": 0.7}})
    once = config_file.read_text(encoding="utf-8")
    config_module.save_settings(config_file, {"behaviour": {"min_confidence": 0.7}})
    assert config_file.read_text(encoding="utf-8") == once


def test_settings_saved_at_once_from_many_threads_all_survive(config_file):
    """Found the hard way, driving the real panel: the page's controls
    arrive on whatever thread pywebview dispatches them on, and three
    changes in quick succession each read the file before the previous
    write landed. Two of the three were silently lost."""
    import threading

    settings = [("behaviour", "min_confidence", 0.78),
                ("behaviour", "dry_run", True),
                ("behaviour", "prescan", False),
                ("ui", "language", "pl")]

    start = threading.Barrier(len(settings))

    def write(section, key, value):
        start.wait()          # maximise the overlap
        config_module.save_settings(config_file, {section: {key: value}})

    threads = [threading.Thread(target=write, args=one) for one in settings]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    cfg = config_module.load(config_file)
    assert cfg.behaviour.min_confidence == 0.78
    assert cfg.behaviour.dry_run is True
    assert cfg.behaviour.prescan is False
    assert cfg.ui.language == "pl"


def test_the_file_survives_writers_the_lock_cannot_see(config_file, monkeypatch):
    """Two panels open at once are two processes, and no lock in this one
    covers the other. They must still not be able to corrupt config.toml -
    which is what a shared "<file>.tmp" name allowed: threads handed each
    other half a file and the result would not parse at all.

    Standing in for the second process by disabling the lock.
    """
    import contextlib as ctx
    import threading

    monkeypatch.setattr(config_module, "_SAVE_LOCK", ctx.nullcontext())

    settings = [("behaviour", "min_confidence", 0.78),
                ("behaviour", "dry_run", True),
                ("ui", "language", "pl")]
    start = threading.Barrier(len(settings))

    def write(section, key, value):
        start.wait()
        with ctx.suppress(Exception):
            config_module.save_settings(config_file, {section: {key: value}})

    threads = [threading.Thread(target=write, args=one) for one in settings]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # A setting may be lost without the lock - that is what the lock is
    # for. The file still has to be readable, and still have the key.
    cfg = config_module.load(config_file)
    assert cfg.llm.api_key.startswith("sk-ant-"), "the API key did not survive"


def test_it_leaves_no_temporary_file_behind(config_file):
    config_module.save_settings(config_file, {"ui": {"language": "pl"}})
    strays = [p.name for p in config_file.parent.iterdir() if p.name != "config.toml"]
    assert not strays, "a half-written file was left next to the real one"


# -- knowing where to write ------------------------------------------------

def test_a_loaded_config_remembers_where_it_came_from(config_file):
    cfg = config_module.load(config_file)
    assert cfg.source == config_file


def test_a_config_nobody_loaded_has_no_source():
    """What stops a default Config, in a test or headless run, writing a
    file nobody asked for."""
    assert config_module.Config().source is None
