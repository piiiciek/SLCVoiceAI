"""Two controls, one name, one place - only one of them can be clicked.

`is_visible` asks whether a control is laid out. SLC stacks controls, so
that is not the same as being on top: the three menus each carry a BACK
button, two of them at identical coordinates, and a third sits under the
GO AHEAD of a live conversation. All report a real rectangle.

The list used to keep whichever the tree walk reached first, which is
right only by luck. Now a collision is settled by asking Windows what is
actually at the point. These tests pin the tie-break and, just as
importantly, that it is not paid for when there is no tie.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slcvoiceai import slc_ui  # noqa: E402


class Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top = left, top
        self.right, self.bottom = right, bottom


class FakeControl:
    """An SLC control, as much of one as list_actions touches."""

    def __init__(self, name, automation_id="", rect=(0, 0, 100, 20),
                 control_type="ButtonControl", parent=None):
        self.Name = name
        self.AutomationId = automation_id
        self.ControlTypeName = control_type
        self.BoundingRectangle = Rect(*rect)
        self.IsEnabled = True
        self._parent = parent

    def GetParentControl(self):
        return self._parent

    def GetPattern(self, _pattern_id):
        return object()          # invokable

    def GetChildren(self):
        return []


def offering(monkeypatch, controls, topmost=None, count=None):
    """Run list_actions over `controls`, with `topmost` on top."""
    window = FakeControl("Self-Loading Cargo", control_type="WindowControl")

    ui = slc_ui.SlcUI("SLC.exe")
    monkeypatch.setattr(type(ui), "windows", lambda self: [window])
    monkeypatch.setattr(type(ui), "_walk",
                        lambda self, node, **kw: iter(controls))
    monkeypatch.setattr(slc_ui, "_is_activatable", lambda c: True)
    monkeypatch.setattr(slc_ui, "help_text", lambda c: "")

    def hit(control):
        if count is not None:
            count.append(getattr(control, "AutomationId", ""))
        return control is topmost

    monkeypatch.setattr(slc_ui, "is_topmost", hit)
    return ui.list_actions()


# -- the tie-break ---------------------------------------------------------

def test_the_one_on_top_wins_when_it_comes_first(monkeypatch):
    first = FakeControl("BACK", "cmdTannoySwitchBackGroundCrew")
    second = FakeControl("BACK", "cmdTannoySwitchBackCabinCrew")
    actions = offering(monkeypatch, [first, second], topmost=first)

    assert [a.automation_id for a in actions] == ["cmdTannoySwitchBackGroundCrew"]


def test_the_one_on_top_wins_when_it_comes_last(monkeypatch):
    """The case the old code got wrong: it kept the first it happened to
    reach, which was the one behind."""
    first = FakeControl("BACK", "cmdTannoySwitchBackCabinCrew")
    second = FakeControl("BACK", "cmdTannoySwitchBackGroundCrew")
    actions = offering(monkeypatch, [first, second], topmost=second)

    assert [a.automation_id for a in actions] == ["cmdTannoySwitchBackGroundCrew"]


def test_the_one_on_top_wins_from_the_middle_of_three(monkeypatch):
    """SLC really does lay out three BACKs at once."""
    behind = FakeControl("BACK", "cmdTannoySwitchBackPassengers")
    real = FakeControl("BACK", "cmdTannoySwitchBackGroundCrew")
    also = FakeControl("BACK", "cmdTannoySwitchBackCabinCrew")
    actions = offering(monkeypatch, [behind, real, also], topmost=real)

    assert [a.automation_id for a in actions] == ["cmdTannoySwitchBackGroundCrew"]


def test_with_nothing_on_top_the_first_is_kept(monkeypatch):
    """SLC covered by another window answers "none of them". Keeping the
    first is what the list did before any of this, so an unanswerable
    question costs nothing rather than emptying the list."""
    first = FakeControl("BACK", "cmdOne")
    second = FakeControl("BACK", "cmdTwo")
    actions = offering(monkeypatch, [first, second], topmost=None)

    assert [a.automation_id for a in actions] == ["cmdOne"]


def test_only_one_of_them_is_offered(monkeypatch):
    """Whatever the answer, the pilot is offered one BACK and not three -
    three would be an ambiguity the hotkey refuses to press through."""
    controls = [FakeControl("BACK", "cmd{i}".format(i=i)) for i in range(3)]
    assert len(offering(monkeypatch, controls, topmost=controls[2])) == 1


# -- what it costs ---------------------------------------------------------

def test_a_name_that_does_not_collide_is_never_hit_tested(monkeypatch):
    """One COM round-trip per control would undo the walk's own pruning.
    The question is only asked when the name alone cannot answer it."""
    asked = []
    controls = [FakeControl("GO AHEAD", "cmdPlayCaptainGoAhead"),
                FakeControl("INTERCOM >", "cmdIntercom"),
                FakeControl("BACK", "cmdBack")]
    offering(monkeypatch, controls, topmost=None, count=asked)

    assert asked == [], "it hit-tested controls with no collision to settle"


def test_a_collision_is_settled_in_at_most_two_questions(monkeypatch):
    asked = []
    first = FakeControl("BACK", "cmdOne")
    second = FakeControl("BACK", "cmdTwo")
    offering(monkeypatch, [first, second], topmost=first, count=asked)

    assert len(asked) <= 2, asked


def test_the_kept_one_is_asked_about_only_once(monkeypatch):
    """Three BACKs must not mean three questions about the same control."""
    asked = []
    controls = [FakeControl("BACK", "cmdOne"), FakeControl("BACK", "cmdTwo"),
                FakeControl("BACK", "cmdThree")]
    offering(monkeypatch, controls, topmost=controls[0], count=asked)

    assert asked.count("cmdOne") == 1, asked


# -- the hit test itself ---------------------------------------------------

def hit_returns(monkeypatch, control):
    monkeypatch.setattr(slc_ui.auto, "ControlFromPoint",
                        lambda x, y: control)


def test_a_control_that_is_the_hit_is_on_top(monkeypatch):
    button = FakeControl("BACK", "cmdBack", rect=(13, 1341, 185, 1360))
    hit_returns(monkeypatch, button)
    assert slc_ui.is_topmost(button)


def test_a_control_whose_own_text_is_the_hit_is_on_top(monkeypatch):
    """ControlFromPoint lands on the innermost element, and every SLC
    button has a Text drawn inside it - so the real control is a parent
    of what comes back, never the hit itself."""
    button = FakeControl("BACK", "cmdBack", rect=(13, 1341, 185, 1360))
    label = FakeControl("BACK", "", rect=(87, 1344, 111, 1357),
                        control_type="TextControl", parent=button)
    hit_returns(monkeypatch, label)
    assert slc_ui.is_topmost(button)


def test_a_covered_control_is_not_on_top(monkeypatch):
    """What SLC actually does: one menu's BACK sits under the GO AHEAD of
    a live conversation."""
    covered = FakeControl("BACK", "cmdTannoySwitchBackPassengers",
                          rect=(13, 1324, 185, 1343))
    on_top = FakeControl("GO AHEAD", "cmdPlayCaptainGoAhead",
                         rect=(13, 1316, 185, 1336))
    hit_returns(monkeypatch, on_top)
    assert not slc_ui.is_topmost(covered)


def test_two_controls_in_the_same_place_are_told_apart(monkeypatch):
    """The pair that started this: same name, same size, same coordinates,
    different AutomationId."""
    front = FakeControl("BACK", "cmdTannoySwitchBackGroundCrew",
                        rect=(13, 1341, 185, 1360))
    behind = FakeControl("BACK", "cmdTannoySwitchBackCabinCrew",
                         rect=(13, 1341, 185, 1360))
    hit_returns(monkeypatch, front)
    assert slc_ui.is_topmost(front)
    assert not slc_ui.is_topmost(behind)


def test_nothing_at_the_point_is_not_on_top(monkeypatch):
    hit_returns(monkeypatch, None)
    assert not slc_ui.is_topmost(FakeControl("BACK", "cmdBack"))


def test_a_hit_test_that_throws_is_not_on_top(monkeypatch):
    """SLC being covered, or the element going stale mid-walk. False is
    the safe answer: the caller keeps what it had."""
    def explode(x, y):
        raise RuntimeError("no window there")

    monkeypatch.setattr(slc_ui.auto, "ControlFromPoint", explode)
    assert not slc_ui.is_topmost(FakeControl("BACK", "cmdBack"))


def test_an_unreadable_control_is_not_on_top(monkeypatch):
    class Stale:
        @property
        def BoundingRectangle(self):
            raise RuntimeError("stale element")

    assert not slc_ui.is_topmost(Stale())


def test_an_unrelated_hit_does_not_walk_up_forever(monkeypatch):
    """A deep chain of parents must not make this loop on every control."""
    node = FakeControl("something else", "cmdOther")
    for _ in range(50):
        node = FakeControl("parent", "cmdParent", parent=node)
    hit_returns(monkeypatch, node)
    assert not slc_ui.is_topmost(FakeControl("BACK", "cmdBack"))
