"""The cascade must not reach for the cloud when the offline layer is sure.

That restraint is the whole economic argument: a flight should cost a handful
of requests, not one per command.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai.intent import CascadeRouter, Decision, FuzzyRouter  # noqa: E402

BUTTONS = [
    "ROGER", "GO AHEAD", "THAT'S PERFECT", "GSX, START CATERING",
    "CONNECT JETWAY", "DISCONNECT JETWAY", "INTERCOM >", "BACK",
]


@dataclass
class FakeAction:
    name: str
    window: str = "Self-Loading Cargo"


class CountingCloud:
    """Stands in for Gemini, and counts how often it was bothered."""

    def __init__(self, decision: Decision | None = None):
        self.calls = 0
        self.decision = decision or Decision(
            action_index=2, confidence=0.9, reasoning="cloud picked it")

    def decide(self, utterance, actions, flight_context=""):
        self.calls += 1
        return self.decision


@pytest.fixture
def actions() -> list[FakeAction]:
    return [FakeAction(n) for n in BUTTONS]


@pytest.fixture
def cloud() -> CountingCloud:
    return CountingCloud()


@pytest.fixture
def cascade(cloud) -> CascadeRouter:
    return CascadeRouter(FuzzyRouter(0.65), cloud, "gemini")


@pytest.mark.parametrize("said,expected", [
    ("roger", "ROGER"),
    ("connect the jetway", "CONNECT JETWAY"),
    ("intercom", "INTERCOM >"),
    ("I understand", "ROGER"),
    ("copy that", "ROGER"),
])
def test_confident_local_match_never_reaches_the_cloud(cascade, cloud, actions,
                                                       said, expected):
    decision = cascade.decide(said, actions)
    assert decision.action_index is not None
    assert actions[decision.action_index].name == expected
    assert cloud.calls == 0, "paid for a command the offline layer had already settled"


def test_unsettled_utterance_escalates(cascade, cloud, actions):
    decision = cascade.decide("hmm no dobra niech bedzie tak jak mowisz", actions)
    assert cloud.calls == 1
    assert decision.action_index == 2


def test_no_buttons_means_no_request(cascade, cloud):
    """Nothing to choose between is not a question worth paying for."""
    assert cascade.decide("roger", []).action_index is None
    assert cloud.calls == 0


def test_cloud_refusal_is_reported(actions):
    cloud = CountingCloud(Decision(reasoning="nothing here matches that"))
    cascade = CascadeRouter(FuzzyRouter(0.65), cloud, "gemini")
    decision = cascade.decide("what is the weather in krakow today", actions)
    assert decision.action_index is None
    assert decision.reasoning == "nothing here matches that"


def test_panel_can_still_rank_through_the_cascade(cascade, actions):
    """The control panel shows every candidate's score; wrapping the local
    matcher must not hide that."""
    ranked = cascade.rank("roger", actions)
    assert ranked and ranked[0][2].name == "ROGER"


def test_threshold_slider_reaches_the_local_matcher(cascade):
    cascade.min_confidence = 0.8
    assert cascade.min_confidence == pytest.approx(0.8)
    assert cascade.local.min_confidence == pytest.approx(0.8)
