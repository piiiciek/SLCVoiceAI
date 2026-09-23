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

from .aliases import aliases_for
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
    #: Why the offline matcher stopped, when it did - so the cascade can tell
    #: the two kinds of failure apart without reading English back out of
    #: `reasoning`. "ambiguous" means two buttons scored level and the tie is
    #: worth a second opinion; "nothing_close" means nothing on screen
    #: resembled the utterance, which a second opinion almost never fixes.
    #: Empty on a decision that settled, and on anything the cloud returns.
    gave_up: str = Field(default="", exclude=True)


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
    "would", "you", "us", "them", "slc", "captain", "cockpit", "me", "my",
    "some", "just", "now", "there",
    # A preposition like the ones above, and the only reason "5 by 5" - the
    # standard answer to a radio check - scored 0.67 against 'Stand By' and
    # pressed it in flight. One shared preposition was carrying the whole
    # match. 'Stand By' still matches on "stand", and "wait", "hold on" and
    # the rest reach it through the alias table either way.
    "by",
    # "press the button to..." is how a person describes using a UI, not part
    # of what they want done. No SLC control carries the word except the one
    # that toggles the toolbar, which is denylisted for exactly this reason.
    "button", "press", "click", "switch",
}

#: Dropped from what the pilot says, but kept in button names. "thank you
#: very much for the information" asks for the same thing as "thanks for the
#: information", so the intensifiers only inflate the denominator the reach
#: damping divides by - but SLC has a button literally called "THANKS VERY
#: MUCH", and stripping those words from it leaves a near-duplicate of
#: "THANK YOU" that ties with it on every thank-you phrase.
_UTTERANCE_FILLER = {"very", "much", "really", "quite", "please"}

_PUNCT = re.compile(r"[^a-z0-9 ]+")
#: Straight and curly, because Whisper uses both.
_APOSTROPHE = re.compile(r"[’']")

#: Fraction of an alias's own words that must appear in the utterance before
#: that alias is allowed to stand in for its button. Guards against short
#: aliases matching on one incidental shared word.
ALIAS_MIN_COVERAGE = 0.6

#: Words that open a sentence without adding to it. They are still matched
#: against - "ok" alone is a valid acknowledgement - but they do not count
#: when working out how much of an utterance an alias has to account for.
#: Without this, "OK, understood" scored 0.50 against ROGER where a bare
#: "understood" scored 1.00: the alias is one word and the sentence was two,
#: so the reach damping halved a perfect match.
_DISCOURSE = frozenset({
    "ok", "okay", "alright", "well", "right", "yeah", "yep", "hmm", "so",
})

#: A decisive win can stand in for a high score. Chatter does not merely score
#: low, it scores low *against everything* - measured over a dozen ATC and
#: small-talk phrases, the gap between first and second place never exceeded
#: 0.08, while genuine commands cleared 0.16. So a clear winner above
#: RELAXED_FLOOR is accepted even below min_confidence.
RELAXED_FLOOR = 0.55
DECISIVE_MARGIN = 0.12

#: What a polarity conflict does to a score. Low enough to lose decisively.
POLARITY_PENALTY = 0.45

#: Prefixes that invert a word's meaning. "disconnect jetway" and "connect
#: jetway" are one character apart for a string matcher and exact opposites
#: to a pilot, so they must never be treated as near-identical.
_NEGATING_PREFIXES = ("dis", "un", "de", "non")

#: Opposites that are not formed by prefixing.
_OPPOSITES = (
    frozenset({"open", "close"}),
    frozenset({"start", "stop"}),
    frozenset({"on", "off"}),
    frozenset({"arm", "disarm"}),
    frozenset({"yes", "no"}),
    frozenset({"connect", "remove"}),
    frozenset({"attach", "detach"}),
    frozenset({"up", "down"}),
    frozenset({"raise", "lower"}),
    frozenset({"more", "less"}),
    # Length and timing. SLC has twenty delay buttons and several differ only
    # by these words: "we expect a long delay" reached SHORT DELAY EXPECTED
    # at 0.83 before they were listed here.
    frozenset({"short", "long"}),
    frozenset({"short", "extended"}),
    frozenset({"brief", "extended"}),
    frozenset({"slight", "extended"}),
    frozenset({"early", "late"}),
    frozenset({"departure", "arrival"}),
)


def _negated_stems(tokens: set[str]) -> set[str]:
    """Stems that appear in negated form, e.g. {"connect"} for "disconnect"."""
    stems = set()
    for token in tokens:
        for prefix in _NEGATING_PREFIXES:
            if token.startswith(prefix) and len(token) > len(prefix) + 2:
                stems.add(token[len(prefix):])
    return stems


def polarity_conflict(said_tokens: set[str], candidate_tokens: set[str]) -> bool:
    """Do these two say opposite things about the same subject?

    True when one negates a word the other asserts plainly ("disconnect
    jetway" vs "connect jetway"), or when they hold two halves of a known
    opposite pair ("open the doors" vs "close the doors").
    """
    if _negated_stems(said_tokens) & candidate_tokens:
        return True
    if _negated_stems(candidate_tokens) & said_tokens:
        return True
    for pair in _OPPOSITES:
        said_side = pair & said_tokens
        cand_side = pair & candidate_tokens
        if said_side and cand_side and said_side != cand_side:
            return True
    return False


def normalise(text: str, spoken: bool = True) -> str:
    """Lowercase, drop punctuation and filler, collapse whitespace.

    Button names carry decoration the pilot never says - trailing '>' on
    submenu entries, '...' on pending states, ALL CAPS throughout.

    `spoken` distinguishes what the pilot said from what a button is called.
    Intensifiers are noise in the first and meaning in the second.
    """
    # Apostrophes are removed, not turned into a space, so a contraction
    # stays one word. Splitting on them made "I'm" into "i" and "m", the
    # first dropped as noise and the second matching nothing - while the
    # alias table is written the other way round ("im listening", "thats
    # fine", "didnt catch that"), so no alias holding a contraction could
    # fire at all. Whisper writes them both ways from one utterance to the
    # next, and this is what makes the two spellings the same word.
    text = _PUNCT.sub(" ", _APOSTROPHE.sub("", text.lower()))
    drop = _NOISE | _UTTERANCE_FILLER if spoken else _NOISE
    words = [w for w in text.split() if w and w not in drop]
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

    def _best_score(self, said: str, action: Action) -> float:
        """Score against the button's own name and its registered aliases.

        String similarity compares spelling, so "understood" scores near zero
        against "ROGER" however obviously a pilot means it. The alias table
        supplies the phrasings that carry the same intent; the best match of
        any of them stands in for the button.
        """
        said_tokens = set(said.split())
        name = normalise(action.name, spoken=False)
        best = self._score(said, name) * self._name_reach(action.name, said_tokens)
        for alias in aliases_for(action.name):
            if best >= 0.99:
                break
            candidate = normalise(alias, spoken=False)
            if not candidate:
                continue
            # An alias should fire when the utterance *is* that phrase, not
            # when it merely shares a word with it. token_set_ratio rewards
            # subsets, so without this guard "what can i say" (which reduces
            # to "what say") matched "what is the weather in krakow today" on
            # the strength of "what" alone.
            tokens = set(candidate.split())
            coverage = len(tokens & said_tokens) / len(tokens)
            if coverage < ALIAS_MIN_COVERAGE:
                continue

            # An alias made only of discourse markers cannot carry a sentence
            # that has content of its own. "alright" is registered for ROGER
            # and "thanks" for THANK YOU, so "alright thanks" tied at 1.00 and
            # was refused - when a person hears it as thanks with a filler in
            # front. A bare "ok" is still an acknowledgement, so the rule
            # lifts when the whole utterance is markers too.
            if tokens <= _DISCOURSE and not said_tokens <= _DISCOURSE:
                continue

            # A short alias must not claim a long utterance. token_set_ratio
            # scores a subset as a perfect match, so the one-word alias "send"
            # (from "send it", registered for GO AHEAD) rated "send me your
            # catering" 1.00 - tying with the button that actually meant it.
            # Damp by how much of the utterance the alias can account for.
            score = self._score(said, candidate) * self._reach(candidate, said_tokens)
            if score > best:
                best = score

        # Penalise the action as a whole, after aliases, not each candidate
        # string. Otherwise a neutral alias routes around the check: "jetway
        # please" is registered for CONNECT JETWAY and carries no polarity of
        # its own, so it scored "disconnect jetway" just as highly as the
        # correct button and the two tied.
        if polarity_conflict(said_tokens, set(name.split())):
            best *= POLARITY_PENALTY
        return best

    #: How close two words must be to count as the same one when measuring
    #: reach. "delays" and "delay" are the same word for this purpose;
    #: "ground" and "stewardess" are not.
    _SAME_WORD = 85

    def _covers(self, candidate_tokens: set[str], word: str) -> bool:
        """Does the candidate account for this word the pilot said?

        Exact first, then a similarity pass: the score itself is fuzzy, so
        the reach that damps it has to be too. Counting exact matches only
        made "no delays expected" stop covering "delay" over a plural, which
        quietly broke a tie the router depends on.
        """
        if word in candidate_tokens:
            return True
        return any(self._fuzz.ratio(word, token) >= self._SAME_WORD
                   for token in candidate_tokens)

    def _reach(self, candidate: str, said_tokens: set[str]) -> float:
        """How much of what was said does this candidate actually account for?

        token_set_ratio scores a subset as a perfect match, so a short
        candidate claims any longer sentence containing its words. Discourse
        markers are excluded from the denominator: "ok" in front of a command
        is not content the alias should have to cover.

        Counted by the words the candidate shares with the utterance, not by
        how long the candidate happens to be. Length flatters an alias made
        mostly of connective words: "connect me with ground" is three words
        for a full score, and it took "connect me with the stewardess" at
        0.77 on "connect" and "with" alone - pressing the ground crew while
        the pilot asked for the cabin. Its one distinguishing word was never
        said, and now that is what it is measured on.
        """
        content = said_tokens - _DISCOURSE
        # When the whole utterance is markers there is no content to cover,
        # and a bare "ok" still means ROGER. Fall back to what was said.
        denominator = content or said_tokens
        tokens = set(candidate.split())
        covered = sum(1 for word in said_tokens if self._covers(tokens, word))
        return min(1.0, covered / max(len(denominator), 1))

    @staticmethod
    def _name_reach(raw_name: str, said_tokens: set[str]) -> float:
        """Damping for a button's own name - only for single-word names.

        "my phone battery is charging" scored 1.00 against the button
        "PHONE >", and the same held for BACK, YES, NO and SETTINGS: one
        incidental word was enough to claim a whole sentence.

        Measured on what the name reduces to, not on how it is written.
        "THANK YOU" is two words that come down to the single token "thank",
        and exempting it as a two-word name let a courtesy opener claim
        everything behind it: "ok, thank you, you can sit down, we will land
        in a moment" scored 1.00 for THANK YOU - certain enough that nothing
        escalated to the model that would have read it properly.

        That exemption was added because damping THANK YOU halved "OK,
        thanks." and tied it with "Stand By". It is not needed for that any
        more: discourse markers now come out of the denominator, so "ok" in
        front costs nothing and a bare thanks still scores 1.00.

        Applied only to names of one token, deliberately. Damping every name
        by length punishes the ordinary case, where a short button
        legitimately answers a longer sentence - "Let's start with the ground
        operation" is five words for the two of "GROUND CREW >".
        """
        if len(normalise(raw_name, spoken=False).split()) > 1:
            return 1.0
        content = said_tokens - _DISCOURSE
        return min(1.0, 1.0 / max(len(content), 1))

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

    def rank(self, utterance: str, actions: list[Action]) -> list[tuple[float, int, Action]]:
        """Every candidate scored, best first.

        Exposed so a UI can show *why* something was picked or refused - the
        margin over the runner-up is usually more informative than the winning
        score on its own.
        """
        said = normalise(utterance)
        if not said or not actions:
            return []
        return sorted(
            ((self._best_score(said, a), i, a) for i, a in enumerate(actions)),
            key=lambda t: t[0], reverse=True,
        )

    def decide(self, utterance: str, actions: list[Action],
               flight_context: str = "") -> Decision:
        if not actions:
            return Decision(reasoning="SLC is offering no buttons right now.")

        said = normalise(utterance)
        scored = self.rank(utterance, actions)
        if not scored:
            return Decision(gave_up="nothing_close",
                             reasoning="Nothing matchable in that utterance.")
        best_score, best_index, best_action = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0

        # A clear winner matters as much as a high score. Two buttons scoring
        # 0.80 and 0.79 means we are guessing, not matching.
        margin = best_score - runner_up
        log.info("Fuzzy best: %r %.2f (runner-up %r %.2f, margin %.2f)",
                 best_action.name, best_score,
                 scored[1][2].name if len(scored) > 1 else "-", runner_up, margin)

        # Two candidates within a whisker of each other means guessing, not
        # matching - and when they are opposites ("connect" / "disconnect"
        # jetway) guessing is actively dangerous.
        if margin < 0.05:
            # Saying a button's name exactly is the strongest signal there is,
            # so let it settle a tie: "intercom" should reach "INTERCOM >" and
            # not stall against "PURSER TO INTERCOM", which merely contains
            # the word.
            tied = [c for c in scored if best_score - c[0] < 0.05]
            exact = [c for c in tied if normalise(c[2].name) == said]
            if len(exact) == 1:
                score, index, action = exact[0]
                return Decision(
                    action_index=index, confidence=score,
                    reasoning="Exact match for {name!r}.".format(name=action.name),
                )
            return Decision(
                confidence=best_score, gave_up="ambiguous",
                reasoning="Ambiguous: {a!r} and {b!r} score almost the same.".format(
                    a=best_action.name, b=scored[1][2].name),
            )

        # A decisive win counts for as much as a high score. "Let the catering
        # come" scored 0.64 against GSX, START CATERING with the runner-up on
        # 0.48 - obviously right, and refused for want of 0.01.
        decisive = best_score >= RELAXED_FLOOR and margin >= DECISIVE_MARGIN
        if best_score < self.min_confidence and not decisive:
            return Decision(
                confidence=best_score, gave_up="nothing_close",
                reasoning="Too weak a match for {name!r} ({s:.2f}).".format(
                    name=best_action.name, s=best_score),
            )

        return Decision(
            action_index=best_index,
            confidence=best_score,
            reasoning="{how} match to {name!r}.".format(
                how="Decisive" if decisive and best_score < self.min_confidence
                else "Closest", name=best_action.name),
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


class CascadeRouter:
    """Offline matcher first; ask the cloud only when it cannot settle.

    Lifted from BlueLine Realism, which logs the same two outcomes:
    "Layer 1 local intent - skipping cloud" and "Layer 1 soft local -
    consulting cloud".

    The point is that most commands are unambiguous - "roger", "connect the
    jetway", "intercom" - and resolve offline at full confidence. Only the
    awkward ones ("super, moze byc") travel, so a flight costs a handful of
    requests rather than one per command.
    """

    def __init__(self, local: Router, cloud: Router, cloud_name: str,
                 when: str = "ties"):
        #: "ties" spends the round trip only on a tie or a shaky acceptance;
        #: "always" also asks when nothing on screen came close. See
        #: `_worth_asking` for why the default is not "always".
        self.when = when
        self.local = local
        self.cloud = cloud
        self.cloud_name = cloud_name

    def decide(self, utterance: str, actions: list[Action],
               flight_context: str = "") -> Decision:
        decision = self.local.decide(utterance, actions, flight_context)
        floor = getattr(self.local, "min_confidence", 0.0)

        # Escalate on a weak *acceptance* as well as on a refusal. A match
        # taken on margin alone is the shakiest kind: "alright everyone lets
        # get going" reaches "HOW'S IT GOING?" at 0.62 on the strength of one
        # word, and nothing about a refusal-only rule would ever catch it.
        if decision.action_index is not None and decision.confidence >= floor:
            log.info("Settled offline - not asking %s", self.cloud_name)
            return decision
        if not actions:
            return decision

        if not self._worth_asking(decision):
            log.info("Not asking %s: %s", self.cloud_name, decision.reasoning)
            return decision

        if decision.action_index is None:
            log.info("Offline matcher unsure (%s) - asking %s",
                     decision.reasoning, self.cloud_name)
        else:
            log.info("Offline match is weak (%r at %.2f, floor %.2f) - asking %s",
                     actions[decision.action_index].name, decision.confidence,
                     floor, self.cloud_name)
        escalated = self.cloud.decide(utterance, actions, flight_context)
        if escalated.action_index is None:
            # The cloud has the last word, including when it overrules a weak
            # local accept - that is the point of asking.
            return escalated if escalated.reasoning else decision
        return escalated

    def _worth_asking(self, decision: Decision) -> bool:
        """Is this failure one the cloud has ever been any use on?

        The two ways the offline matcher gives up are not alike, and the logs
        to 2026-09-23 separate them cleanly across 128 escalations:

        * **a tie** - two buttons scoring level (73 escalations). The cloud
          broke it with a different answer 19 times. This is the case it is
          actually good at: it can see that "tell them to sit down" is about
          passengers and not about the flight phase, which no amount of
          string similarity will.

        * **nothing close** - the best score on screen was below the floor
          (51 escalations). The cloud refused 38 of those and overrode 6, and
          of the 6, three were panel furniture that is no longer offered at
          all and one was `nevermind` -> DISREGARD, which the alias table now
          settles by itself. That leaves two real saves in fifty-one, bought
          with three seconds of waiting on every one.

        So the default asks on a tie and on a shaky acceptance, and answers
        "nothing close" itself, immediately. That is also the better answer
        to give: app.py names the button that was missing and the key the
        pilot has bound to reach it, which arrives now instead of after the
        round trip. Set escalate_when = "always" to restore the old cascade.
        """
        if self.when == "always":
            return True
        return decision.gave_up != "nothing_close"

    @property
    def min_confidence(self) -> float:
        return getattr(self.local, "min_confidence", 0.0)

    @min_confidence.setter
    def min_confidence(self, value: float) -> None:
        if hasattr(self.local, "min_confidence"):
            self.local.min_confidence = value

    def rank(self, utterance: str, actions: list[Action]):
        """Delegate to the local matcher so the panel can still show scores."""
        ranker = getattr(self.local, "rank", None)
        return ranker(utterance, actions) if ranker else []


def _build_single(name: str, cfg: Config) -> Router:
    if name == "fuzzy":
        return FuzzyRouter(cfg.behaviour.min_confidence)
    if name == "claude":
        return ClaudeRouter(cfg.llm, cfg.api_key)
    if name == "gemini":
        from .gemini import GeminiRouter
        return GeminiRouter(cfg.gemini, cfg.gemini_key, SYSTEM_PROMPT)
    raise ValueError(
        "Unknown intent backend {b!r}. Use 'fuzzy', 'gemini' or 'claude'.".format(b=name))


def build_router(cfg: Config) -> Router:
    backend = cfg.intent.backend.strip().lower()
    escalate = cfg.intent.escalate_to.strip().lower()
    local = _build_single(backend, cfg)

    if escalate in ("", "none") or escalate == backend:
        log.info("Intent backend: %s", backend)
        return local

    try:
        cloud = _build_single(escalate, cfg)
    except (RuntimeError, ValueError) as exc:
        # Escalation is an enhancement, not a requirement: the offline layer
        # answers most commands on its own. Refusing to start over a missing
        # key would take away everything that does work.
        log.warning("Escalation to %s is unavailable (%s) - carrying on with "
                    "%s alone", escalate, exc, backend)
        return local

    when = cfg.intent.escalate_when.strip().lower()
    if when not in ("ties", "always"):
        log.warning("[intent] escalate_when = %r is not 'ties' or 'always'; "
                    "using 'ties'.", cfg.intent.escalate_when)
        when = "ties"
    log.info("Intent pipeline: %s offline, escalating to %s on %s",
             backend, escalate, "a tie" if when == "ties" else "anything unsure")
    return CascadeRouter(local, cloud, escalate, when)
