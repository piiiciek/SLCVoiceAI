"""Google Gemini as the fallback intent matcher.

Chosen for the cascade because its free tier is generous enough that a flight
costs nothing: only the commands the offline matcher could not settle ever
reach it, which in practice is a handful per hour.

Talks to the REST API directly through the standard library - no SDK, no
extra dependency, and the request shape stays visible in one place.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent")

#: action_index uses -1 rather than null to decline: Gemini's response schema
#: handles a plain integer far more reliably than a nullable one.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "action_index": {
            "type": "integer",
            "description": "0-based index of the chosen button, or -1 to decline.",
        },
        "confidence": {
            "type": "number",
            "description": "0.0 to 1.0, how sure you are this is the button meant.",
        },
        "reasoning": {
            "type": "string",
            "description": "One short sentence.",
        },
    },
    "required": ["action_index", "confidence", "reasoning"],
}


class GeminiRouter:
    """Second opinion for utterances the offline matcher could not place."""

    def __init__(self, cfg, api_key: str, system_prompt: str):
        self.cfg = cfg
        self.api_key = api_key
        self.system_prompt = system_prompt

    def decide(self, utterance: str, actions, flight_context: str = ""):
        from .intent import Decision

        if not actions:
            return Decision(reasoning="SLC is offering no buttons right now.")

        listing = "\n".join(
            "{i}. {name}".format(i=i, name=a.name) for i, a in enumerate(actions))
        user_text = (
            "Flight context:\n{ctx}\n\n"
            "Buttons available right now:\n{listing}\n\n"
            "The pilot said: \"{said}\""
        ).format(ctx=flight_context or "(none)", listing=listing, said=utterance)

        payload = {
            "system_instruction": {"parts": [{"text": self.system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
                "temperature": 0,
                "maxOutputTokens": self.cfg.max_tokens,
            },
        }

        raw = self._post(self.cfg.model, payload)
        if raw is None and self.cfg.fallback_model:
            log.info("Retrying on %s", self.cfg.fallback_model)
            raw = self._post(self.cfg.fallback_model, payload)
        if raw is None:
            return Decision(reasoning="Gemini did not answer.")

        try:
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            log.error("Could not read Gemini's reply: %s", exc)
            return Decision(reasoning="Gemini returned something unreadable.")

        index = parsed.get("action_index", -1)
        confidence = float(parsed.get("confidence", 0.0) or 0.0)
        reasoning = str(parsed.get("reasoning", ""))[:200]

        if not isinstance(index, int) or index < 0:
            log.info("Gemini declined: %s", reasoning)
            return Decision(confidence=confidence, reasoning=reasoning or "Declined.")
        if index >= len(actions):
            log.warning("Gemini chose index %s of %d", index, len(actions))
            return Decision(reasoning="Gemini chose a button that does not exist.")

        log.info("Gemini: %r %.2f - %s", actions[index].name, confidence, reasoning)
        return Decision(action_index=index,
                        confidence=max(0.0, min(1.0, confidence)),
                        reasoning=reasoning)

    def _post(self, model: str, payload: dict) -> dict | None:
        url = ENDPOINT.format(model=model)
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": self.api_key})
        try:
            with urllib.request.urlopen(request, timeout=self.cfg.timeout_seconds) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            if exc.code in (429, 503):
                # Free-tier throttling. Worth naming plainly: it means the
                # cascade silently stops helping, not that anything broke.
                log.warning("Gemini is rate limited (%s) - the offline matcher "
                            "is on its own until it clears", exc.code)
            else:
                log.error("Gemini HTTP %s: %s", exc.code, detail)
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.error("Could not reach Gemini: %s", exc)
            return None
