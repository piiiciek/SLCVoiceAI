"""The probe, which is how a question about SLC gets answered from a flight.

It is left running for a whole flight and read afterwards, so the thing
that makes or breaks it is not the walking - it is whether the file that
comes back can be read at all. A full dump is 66 KB; at two-second
intervals over an hour that is a hundred megabytes of near-identical text
to search by hand for the one moment that mattered.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import probe_slc  # noqa: E402

#: Six lines of a real dump, taken with the ground crew dialogue open.
REAL = """  [Button          ] GROUND CREW >   <id=cmdPlayCaptainCockpitToGround, enabled=True, rect=172x20@26,1289  BRIDGE-SEES=yes, patterns=Invoke>
  [Button          ] INTERCOM >   <id=cmdPlayCaptainIntercom, enabled=True, rect=172x19@26,1314  BRIDGE-SEES=yes, patterns=Invoke>
  [Text            ] "GROUND TO COCKPIT?"   <id=txtGroundCrewLastResponse, enabled=True, rect=172x13@26,1289  BRIDGE-SEES=yes>
  [Button          ] AWAITING RESPONSE...   <id=cmdGroundCrewAwaitingResponse, enabled=True, rect=0x0@0,0  BRIDGE-SEES=NO, patterns=Invoke>
  [Button          ] GO AHEAD   <id=cmdPlayCaptainGoAhead, enabled=True, rect=172x20@26,1306  BRIDGE-SEES=yes, patterns=Invoke>
  [Button          ] STARTING ENGINE 1   <id=cmdPlayCaptainStartingEngine1, enabled=True, rect=0x0@0,0  BRIDGE-SEES=NO, patterns=Invoke>"""

#: What the README tells a pilot to chase the GO AHEAD question with.
TERMS = ["goahead", "groundcrew", "cockpittoground"]


def test_no_terms_means_the_whole_dump():
    """Filtering is opt-in; the plain probe must be unchanged."""
    assert probe_slc.only_matching(REAL, []) == REAL


def test_it_keeps_every_control_the_question_turns_on():
    kept = probe_slc.only_matching(REAL, TERMS)
    for wanted in ("cmdPlayCaptainGoAhead", "txtGroundCrewLastResponse",
                   "cmdGroundCrewAwaitingResponse",
                   "cmdPlayCaptainCockpitToGround"):
        assert wanted in kept, wanted + " was filtered out"


def test_it_drops_what_the_question_is_not_about():
    kept = probe_slc.only_matching(REAL, TERMS)
    assert "StartingEngine1" not in kept
    assert "cmdPlayCaptainIntercom" not in kept
    assert len(kept.splitlines()) < len(REAL.splitlines())


def test_it_matches_the_automation_id_not_only_the_name():
    """The steadier half. Nothing on screen says "groundcrew", but
    txtGroundCrewLastResponse does, whatever SLC calls it that day."""
    only_id = ('  [Text] "ANYTHING AT ALL"   '
               "<id=txtGroundCrewLastResponse, rect=0x0@0,0  BRIDGE-SEES=NO>")
    assert probe_slc.only_matching(only_id, ["groundcrew"]) == only_id


def test_a_snapshot_with_nothing_in_it_says_so():
    """Rather than an empty block, which reads as a broken probe."""
    assert probe_slc.only_matching(REAL, ["nosuchthing"]) == "(nothing matching)"


def test_the_terms_the_readme_gives_really_find_it():
    """If the documented command missed the button, the flight it was run
    on would be wasted - and there is no second chance at that moment."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if "--only" not in readme:
        return                      # not documented yet; nothing to check
    for term in TERMS:
        assert term in readme, (
            "the README's --only line does not include " + term)
