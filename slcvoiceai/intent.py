"""Map a free-form utterance onto one of SLC's currently available controls.

Two interchangeable backends, selected by ``[intent] backend`` in config.toml:

``fuzzy``
    Offline string matching against the button names. Free, instant, needs no
    API key and no VRAM beyond Whisper itself - which matters when the GPU is
    already busy running the simulator. Relies on Whisper's ``translate`` task
    to turn Polish speech into English before matching.

``claude``
    The Anthropic API. Markedly better at loose, idiomatic or indirect
    phrasing ("tell them we're good to push"), at the cost of roughly a third
    of a grosz per command.

Both return the same :class:`Decision`, and neither may invent an action: the
choice is always an index into the list of controls SLC is offering right now.
Declining is a first-class answer - misfiring a cabin command mid-approach is
worse than asking the pilot to repeat themselves.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Protocol

from pydantic import BaseModel, Field

from .config import Config
from .slc_ui import Action

log = logging.getLogger(__name__)


class Decision(BaseModel):
    """A routing decision, from whichever backend produced it."""

    action_index: Optional[int] = Field(
        default=None,
        description="0-based index into the numbered button list, or null to decline.",
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Calibrated probability that this is the intended button.",
    )
    reasoning: str = Field(
        default="", description="One short sentence explaining the choice.",
    )


class Router(Protocol):
    def decide(self, utterance: str, actions: list[Action],
               flight_context: str = "") -> Decision:
        ...


# --------------------------------------------------------------------------
# Offline fuzzy backend
# --------------------------------------------------------------------------

#: Words that carry no signal in an SLC button name or a spoken command.
_NOISE = {
    "the", "a", "an", "to", "for", "of", "and", "is", "are", "be", "please",
    "our", "your", "we", "i", "it", "that", "this", "will", "can", "could",
    "would", "you", "us", "them", "slc", "captain", "cockpit",
}

_PUNCT = re.compile(r"[^a-z0-9 ]+")


def normalise(text: str) -> str:
    """Lowercase, drop punctuation and filler, collapse whitespace.

    Button names carry decoration the pilot never says - trailing '>' on
    submenu entries, '...' on pending states, ALL CAPS throughout.
    """
    text = _PUNCT.sub(" ", text.lower())
    words = [w for w in text.split() if w and w not in _NOISE]
    return " ".join(words)


class FuzzyRouter:
    """Offline matcher. No network, no API key, no model weights."""

    def __init__(self, min_confidence: float):
        self.min_confidence = min_confidence
        try:
            from rapidfuzz import fuzz
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "The fuzzy backend needs rapidfuzz. Run: pip install rapidfuzz"
            ) from exc
        self._fuzz = fuzz

    def _score(self, said: str, candidate: str) -> float:
        """0.0-1.0 similarity, forgiving of word order and extra words."""
        if not said or not candidate:
            return 0.0
        # token_set_ratio only, deliberately. Combining it with partial_ratio
        # (the obvious "take whichever is higher") destroys the signal:
        # partial_ratio scores ATC chatter like "tower london zero two" at 0.71
        # against real buttons - as high as a genuine command - so the max()
        # drags noise up to the level of intent. Measured on real Whisper
        # output, token_set_ratio alone separates by 0.21 where the
        # combination separated by 0.02.
        return self._fuzz.token_set_ratio(said, candidate) / 100.0

    def decide(self, utterance: str, actions: list[Action],
               flight_context: str = "") -> Decision:
        if not actions:
            return Decision(reasoning="SLC is offering no buttons right now.")

        said = normalise(utterance)
        if not said:
            return Decision(reasoning="Nothing matchable in that utterance.")

        scored = sorted(
            ((self._score(said, normalise(a.name)), i, a) for i, a in enumerate(actions)),
            key=lambda t: t[0], reverse=True,
        )
        best_score, best_index, best_action = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0

        # A clear winner matters as much as a high score. Two buttons scoring
        # 0.80 and 0.79 means we are guessing, not matching.
        margin = best_score - runner_up
        if best_score >= self.min_confidence and margin < 0.05:
            return Decision(
                confidence=best_score,
                reasoning="Ambiguous: {a!r} and {b!r} score almost the same.".format(
                    a=best_action.name, b=scored[1][2].name),
            )

        log.info("Fuzzy best: %r %.2f (runner-up %r %.2f)",
                 best_action.name, best_score, scored[1][2].name if len(scored) > 1 else "-",
                 runner_up)
        return Decision(
            action_index=best_index,
            confidence=best_score,
            reasoning="Closest match to {name!r}.".format(name=best_action.name),
        )


# --------------------------------------------------------------------------
# Claude backend
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You route a pilot's spoken words to a button in \
Self-Loading Cargo, a cabin-crew simulation for flight simulators.

You are given the buttons SLC is offering at this instant. Each is numbered.
Pick the single button that best matches what the pilot wants, or decline.

Rules:
- Only ever choose from the numbered list. Never invent an action.
- The pilot may speak any language (often Polish). The buttons are English.
  Translate intent, not words: "niech zaczynaja boarding" -> a boarding button.
- Pilots speak loosely and idiomatically. "tell them we're good to push",
  "let's get moving", "mozemy pchac" all mean the pushback request.
- If nothing on the list plausibly matches, or you are guessing between two
  unrelated options, decline. Set action_index to null and say why briefly.
- Decline anything that sounds like chatter to ATC, a copilot, or yourself
  rather than an instruction to the cabin or ground crew.
- confidence is your own estimate from 0.0 to 1.0 that this is the button the
  pilot meant. Be honest and calibrated; do not inflate it.
"""


class ClaudeRouter:
    def __init__(self, cfg, api_key: str):
        import anthropic

        self._anthropic = anthropic
        self.cfg = cfg
        self.client = anthropic.Anthropic(api_key=api_key, timeout=cfg.timeout_seconds)

    def decide(self, utterance: str, actions: list[Action],
               flight_context: str = "") -> Decision:
        if not actions:
            return Decision(reasoning="SLC is offering no buttons right now.")

        anthropic = self._anthropic
        listing = "\n".join(
            "{i}. {name}   (window: {win})".format(i=i, name=a.name, win=a.window)
            for i, a in enumerate(actions)
        )
        user_content = (
            "Flight context:\n{ctx}\n\n"
            "Buttons available right now:\n{listing}\n\n"
            "The pilot said: \"{utterance}\""
        ).format(ctx=flight_context or "(none)", listing=listing, utterance=utterance)

        try:
            response = self.client.messages.parse(
                model=self.cfg.model,
                max_tokens=self.cfg.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                output_format=Decision,
            )
        except anthropic.APITimeoutError:
            log.error("Intent model timed out after %.1fs", self.cfg.timeout_seconds)
            return Decision(reasoning="Intent model timed out.")
        except anthropic.RateLimitError:
            log.error("Rate limited by the Anthropic API")
            return Decision(reasoning="Rate limited.")
        except anthropic.APIStatusError as exc:
            log.error("Anthropic API error %s: %s", exc.status_code, exc)
            return Decision(reasoning="API error {c}.".format(c=exc.status_code))
        except anthropic.APIConnectionError as exc:
            log.error("Could not reach the Anthropic API: %s", exc)
            return Decision(reasoning="Network error.")

        decision = response.parsed_output
        if decision is None:
            return Decision(reasoning="Model returned no parseable decision.")

        # The model is told to stay in range, but never trust an index blindly.
        if decision.action_index is not None:
            if not 0 <= decision.action_index < len(actions):
                log.warning("Model returned out-of-range index %s (have %d actions)",
                            decision.action_index, len(actions))
                return Decision(reasoning="Model chose an index that does not exist.")

        log.info("Decision: index=%s confidence=%.2f - %s",
                 decision.action_index, decision.confidence, decision.reasoning)
        return decision


def build_router(cfg: Config) -> Router:
    backend = cfg.intent.backend.strip().lower()
    if backend == "fuzzy":
        log.info("Intent backend: fuzzy (offline, free)")
        return FuzzyRouter(cfg.behaviour.min_confidence)
    if backend == "claude":
        log.info("Intent backend: Claude (%s)", cfg.llm.model)
        return ClaudeRouter(cfg.llm, cfg.api_key)
    raise ValueError(
        "Unknown [intent] backend {b!r}. Use 'fuzzy' or 'claude'.".format(b=backend)
    )
