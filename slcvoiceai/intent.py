"""Map a free-form utterance onto one of SLC's currently available controls.

The model never invents an action. It is given the exact list of controls SLC
is offering right now and must either pick one by index or decline. Declining
is a first-class answer: misfiring a cabin command mid-approach is worse than
asking the pilot to repeat themselves.
"""

from __future__ import annotations

import logging
from typing import Optional

import anthropic
from pydantic import BaseModel, Field

from .config import LlmConfig
from .slc_ui import Action

log = logging.getLogger(__name__)


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


class Decision(BaseModel):
    """The model's routing decision."""

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


class IntentRouter:
    def __init__(self, cfg: LlmConfig, api_key: str):
        self.cfg = cfg
        self.client = anthropic.Anthropic(api_key=api_key, timeout=cfg.timeout_seconds)

    def decide(self, utterance: str, actions: list[Action],
               flight_context: str = "") -> Decision:
        if not actions:
            return Decision(reasoning="SLC is offering no buttons right now.")

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
