"""Compare ways of reading a UIA tree: the current walk vs cached FindAll.

Run it against a live SLC to check both halves of any change to the scan -
does it still see every button, and is it any faster.

    python tools/probe_cache.py --process SLC.exe --repeats 5

Strategies are run INTERLEAVED: all of them, back to back, once per repeat.
A live app's tree changes from second to second (virtualised lists, popups,
a clock ticking), so a difference seen in one repeat means nothing. Only a
difference that survives every repeat is a real property of the tree view.

What this measured on SLC's main window, and why the scan is still a plain
walk:

  * The control view - which is what FindAll uses unless told otherwise -
    cannot see most of SLC. It returned 4 of the 17 buttons the walk finds.
    SLC draws its toolbar as template parts and WPF marks those
    IsControlElement=False.
  * Asking for the raw view fixes that completely: the cached strategies
    below returned exactly the walk's buttons, in every repeat.
  * TreeScope_Descendants cannot be trusted against SLC at all. Asked for
    the window's descendants ten times in a row it returned nothing eight
    times, and 545 elements twice - whatever the condition, whatever the
    filter. That is what an earlier session mistook for 'RawViewCondition
    returns 0'. TreeScope_Children, level by level, returned 538 every
    single time. In the bridge an empty result reads as 'SLC is offering no
    buttons', so any fast path built on Descendants would lose commands
    silently.
  * None of them was faster anyway. A cached property read saves about a
    millisecond, and the scan's time goes on tree navigation, which every
    strategy pays alike. Caching properties optimises the cheap half.

The 'findall' rows below are kept precisely so that those two failures stay
visible: expect 'findall control' to miss most buttons and the Descendants
rows to report nothing at all.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import comtypes
import comtypes.client
from comtypes.gen import UIAutomationClient as UIA

from slcvoiceai import slc_ui

P_NAME = UIA.UIA_NamePropertyId
P_AID = UIA.UIA_AutomationIdPropertyId
P_CTYPE = UIA.UIA_ControlTypePropertyId
P_ENABLED = UIA.UIA_IsEnabledPropertyId
P_RECT = UIA.UIA_BoundingRectanglePropertyId
P_INVOKE = UIA.UIA_IsInvokePatternAvailablePropertyId
P_TOGGLE = UIA.UIA_IsTogglePatternAvailablePropertyId
P_SELECT = UIA.UIA_IsSelectionItemPatternAvailablePropertyId
P_ISCTL = UIA.UIA_IsControlElementPropertyId
P_HELP = UIA.UIA_HelpTextPropertyId

CACHED = (P_NAME, P_AID, P_CTYPE, P_ENABLED, P_RECT,
          P_INVOKE, P_TOGGLE, P_SELECT, P_ISCTL, P_HELP)


_API = None
_REQUESTS = {}


def iua():
    """One automation object for the process, built once.

    Creating it costs real time, and production would hold on to it - paying
    for a fresh one inside every measured call made the cached strategies
    look slower than they are.
    """
    global _API
    if _API is None:
        _API = comtypes.client.CreateObject(
            UIA.CUIAutomation, interface=UIA.IUIAutomation)
    return _API


def cache_request(api, tree_filter=None, key=None):
    if key in _REQUESTS:
        return _REQUESTS[key]
    req = api.CreateCacheRequest()
    for pid in CACHED:
        req.AddProperty(pid)
    req.AutomationElementMode = UIA.AutomationElementMode_Full
    if tree_filter is not None:
        req.TreeFilter = tree_filter
    _REQUESTS[key] = req
    return req


def cached_label(el) -> str:
    """Same rule as slc_ui.label_for, but off cached properties."""
    try:
        name = (el.CachedName or "").strip()
    except Exception:
        name = ""
    if len(name) >= 2:
        return name
    try:
        return slc_ui.humanise_id(el.GetCachedPropertyValue(P_AID) or "")
    except Exception:
        return ""


def cached_visible(el) -> bool:
    try:
        rect = el.CachedBoundingRectangle
    except Exception:
        return False
    try:
        return (rect.right - rect.left) > 0 and (rect.bottom - rect.top) > 0
    except Exception:
        return False


def cached_activatable(el) -> bool:
    for pid in (P_INVOKE, P_TOGGLE, P_SELECT):
        try:
            if bool(el.GetCachedPropertyValue(pid)):
                return True
        except Exception:
            continue
    return False


def actions_from_cached(elements, win_name, stats, seen):
    """Apply exactly the production filter chain to cached elements.

    Including the (window, lowercased name) de-duplication - SLC has both a
    'SETTINGS' text button and a cmdSettings icon, and without the same
    de-dupe the comparison reports a difference the bridge would never see.
    """
    out = []
    for el in elements:
        try:
            if not cached_visible(el):
                continue
            stats["visible"] += 1
            if not bool(el.GetCachedPropertyValue(P_ENABLED)):
                continue
            label = cached_label(el)
            if len(label) < 2:
                continue
            if not cached_activatable(el):
                continue
            if slc_ui.is_denied(label):
                continue
            # Production checks the tooltip as well, because some of these
            # labels are humanise_id's invention rather than SLC's own -
            # cmdStandBy reads as 'Stand By' and closes SLC. Without this the
            # probe reports that button as one production is missing.
            hint = el.GetCachedPropertyValue(P_HELP) or ""
            if hint and slc_ui.is_denied(hint):
                continue
            key = (win_name, label.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(label)
        except Exception:
            continue
    return out


# --------------------------------------------------------------- strategies

def strat_walk(process_name):
    """Baseline: whatever production does today, untouched.

    Deliberately no instrumentation inside it. _walk recurses through
    self._walk, so wrapping the method wraps every level of the recursion -
    which inflated both the node count and the baseline's own timing.
    """
    ui = slc_ui.SlcUI(process_name)
    acts = ui.list_actions()
    return [a.name for a in acts], {"nodes": 0, "visible": 0}


def count_walk_nodes(process_name):
    """How many nodes the baseline visits - measured outside the timing."""
    ui = slc_ui.SlcUI(process_name)
    total = 0
    for win in ui.windows():
        for _ in ui._walk(win):
            total += 1
    return total


def _top_windows(api, process_name):
    root = api.GetRootElement()
    walker = api.ControlViewWalker
    wins = []
    child = walker.GetFirstChildElement(root)
    while child:
        try:
            pid = child.CurrentProcessId
            if pid and slc_ui._process_name(pid).lower() == process_name.lower():
                wins.append(child)
        except Exception:
            pass
        child = walker.GetNextSiblingElement(child)
    return wins


def _window_name(win):
    try:
        return win.CurrentName or "(untitled)"
    except Exception:
        return "(untitled)"


def _activatable_condition(api):
    """Server-side equivalent of slc_ui._is_activatable.

    Lets UIA do the filtering inside SLC's process instead of marshalling
    every node across and testing it here.
    """
    return api.CreateOrCondition(
        api.CreatePropertyCondition(P_INVOKE, True),
        api.CreateOrCondition(
            api.CreatePropertyCondition(P_TOGGLE, True),
            api.CreatePropertyCondition(P_SELECT, True)))


def _find_all(process_name, tree_filter_name, narrow=False, enabled_only=False):
    api = iua()
    tree_filter = None
    if tree_filter_name == "true":
        tree_filter = api.CreateTrueCondition()
    elif tree_filter_name == "raw":
        tree_filter = api.RawViewCondition
    elif tree_filter_name == "control":
        tree_filter = api.ControlViewCondition

    req = cache_request(api, tree_filter, key=tree_filter_name)
    if narrow:
        cond = _activatable_condition(api)
        if enabled_only:
            cond = api.CreateAndCondition(
                cond, api.CreatePropertyCondition(P_ENABLED, True))
    else:
        cond = api.CreateTrueCondition()

    found = []
    seen = set()
    stats = {"nodes": 0, "visible": 0}
    for win in _top_windows(api, process_name):
        win_name = _window_name(win)
        if slc_ui.is_denied_window(win_name):
            continue
        arr = win.FindAllBuildCache(UIA.TreeScope_Descendants, cond, req)
        n = arr.Length
        stats["nodes"] += n
        elements = [arr.GetElement(i) for i in range(n)]
        found.extend(actions_from_cached(elements, win_name, stats, seen))
    return found, stats


def strat_find_default(p):
    return _find_all(p, None)


def strat_find_control(p):
    return _find_all(p, "control")


def strat_find_true(p):
    return _find_all(p, "true")


def strat_find_raw(p):
    return _find_all(p, "raw")


def strat_find_raw_narrow(p):
    return _find_all(p, "raw", narrow=True)


def strat_find_raw_narrow_en(p):
    return _find_all(p, "raw", narrow=True, enabled_only=True)


def strat_raw_walker(process_name):
    """Manual raw-view walk with GetFirstChildElementBuildCache, pruning
    collapsed subtrees the way _walk does."""
    api = iua()
    req = cache_request(api, key="rawwalk")
    walker = api.RawViewWalker
    stats = {"nodes": 0, "visible": 0}

    def walk(node, depth=0):
        if depth > 25:
            return
        try:
            child = walker.GetFirstChildElementBuildCache(node, req)
        except Exception:
            return
        while child:
            stats["nodes"] += 1
            if cached_visible(child):
                yield child
                yield from walk(child, depth + 1)
            try:
                child = walker.GetNextSiblingElementBuildCache(child, req)
            except Exception:
                return

    found = []
    seen = set()
    for win in _top_windows(api, process_name):
        win_name = _window_name(win)
        if slc_ui.is_denied_window(win_name):
            continue
        found.extend(actions_from_cached(list(walk(win)), win_name, stats, seen))
    return found, stats


def strat_level_cache(process_name, props=CACHED):
    """One cached FindAll per visible parent - pruning AND caching.

    The walk's strength is that it never descends into a collapsed subtree:
    it touches ~57 nodes where FindAll(Descendants) must return all ~527.
    Its weakness is that every surviving node then costs a round-trip per
    property. This keeps the pruning and pays for the properties once, in
    the same call that fetches the children.
    """
    api = iua()
    key = "level" + str(len(props))
    if key in _REQUESTS:
        req = _REQUESTS[key]
    else:
        req = api.CreateCacheRequest()
        for pid in props:
            req.AddProperty(pid)
        req.AutomationElementMode = UIA.AutomationElementMode_Full
        req.TreeFilter = api.RawViewCondition
        _REQUESTS[key] = req
    cond = api.CreateTrueCondition()
    stats = {"nodes": 0, "visible": 0}

    def children(node):
        try:
            arr = node.FindAllBuildCache(UIA.TreeScope_Children, cond, req)
        except Exception:
            return []
        return [arr.GetElement(i) for i in range(arr.Length)]

    def walk(node, depth=0):
        if depth > 25:
            return
        for child in children(node):
            stats["nodes"] += 1
            if cached_visible(child):
                yield child
                yield from walk(child, depth + 1)

    found = []
    seen = set()
    for win in _top_windows(api, process_name):
        win_name = _window_name(win)
        if slc_ui.is_denied_window(win_name):
            continue
        found.extend(actions_from_cached(list(walk(win)), win_name, stats, seen))
    return found, stats


#: The properties production actually consults. ControlType is only used for
#: a display string and IsControlElement is not used at all.
LEAN = (P_NAME, P_AID, P_ENABLED, P_RECT, P_INVOKE, P_TOGGLE,
        P_SELECT, P_HELP)


def strat_level_lean(p):
    return strat_level_cache(p, props=LEAN)


STRATEGIES = (
    ("walk (baseline)", strat_walk),
    ("level cache", strat_level_cache),
    ("level cache lean", strat_level_lean),
    ("findall default", strat_find_default),
    ("findall control", strat_find_control),
    ("findall raw", strat_find_raw),
    ("findall true", strat_find_true),
    ("raw + patterns", strat_find_raw_narrow),
    ("raw + pat + enabled", strat_find_raw_narrow_en),
    ("raw walk + cache", strat_raw_walker),
)

#: The interesting few, for when a full run is more patience than you have.
QUICK = ("walk (baseline)", "level cache lean", "findall control", "findall raw")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", default="SLC.exe")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--show", action="store_true", help="print the label set")
    ap.add_argument("--label", default="", help="what state SLC is in")
    ap.add_argument("--quick", action="store_true",
                    help="only the strategies worth re-checking")
    args = ap.parse_args()

    strategies = STRATEGIES
    if args.quick:
        strategies = tuple((n, f) for n, f in STRATEGIES if n in QUICK)

    comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)

    print("target: {p}   state: {s}   repeats: {r}".format(
        p=args.process, s=args.label or "(unlabelled)", r=args.repeats))
    print("")

    times = {name: [] for name, _ in strategies}
    nodes = {name: [] for name, _ in strategies}
    labels = {name: [] for name, _ in strategies}
    errors = {}

    # Warm-up: build the automation object and the cache requests, let SLC's
    # provider spin up. None of that repeats in production, so none of it
    # belongs in the numbers.
    for name, fn in strategies:
        try:
            fn(args.process)
        except Exception:
            pass

    for _ in range(args.repeats):
        for name, fn in strategies:
            # UIA fails a whole enumeration now and then with a transient COM
            # error, the same one slc_ui.windows() retries through. Losing a
            # strategy from the comparison over it tells us nothing.
            for attempt in range(3):
                t0 = time.perf_counter()
                try:
                    result, stats = fn(args.process)
                    break
                except Exception as exc:
                    errors[name] = "{t}: {e}".format(t=type(exc).__name__, e=exc)
                    result = None
                    time.sleep(0.15 * (attempt + 1))
            if result is None:
                continue
            errors.pop(name, None)
            times[name].append(time.perf_counter() - t0)
            nodes[name].append(stats["nodes"])
            # Compare case-insensitively: everything downstream (matching,
            # the denylist) lowercases anyway, so 'SETTINGS' and 'Settings'
            # are the same button as far as the bridge is concerned.
            labels[name].append(frozenset(lbl.lower() for lbl in result))

    base_name = strategies[0][0]
    base_nodes = count_walk_nodes(args.process)
    header = "{n:<18} {t:>7} {m:>7} {nodes:>7} {acts:>7} {miss:>8} {extra:>7} {ch:>6}".format(
        n="strategy", t="median", m="best", nodes="nodes", acts="labels",
        miss="missing", extra="extra", ch="churn")
    print(header)
    print("-" * len(header))

    for name, _ in strategies:
        if name in errors:
            print("{n:<18} FAILED  {e}".format(n=name, e=errors[name]))
            continue
        runs = labels[name]
        if not runs:
            print("{n:<18} no runs".format(n=name))
            continue
        # A difference is real only if it survives every repeat.
        missing = set.intersection(*[set(labels[base_name][i]) - set(runs[i])
                                     for i in range(len(runs))])
        extra = set.intersection(*[set(runs[i]) - set(labels[base_name][i])
                                   for i in range(len(runs))])
        churn = len(set(runs))
        node_count = statistics.median(nodes[name]) or base_nodes
        print("{n:<18} {t:>7.2f} {m:>7.2f} {nodes:>7.0f} {acts:>7.0f} {miss:>8} {extra:>7} {ch:>6}".format(
            n=name,
            t=statistics.median(times[name]),
            m=min(times[name]),
            nodes=node_count,
            acts=statistics.median([len(r) for r in runs]),
            miss=len(missing), extra=len(extra), ch=churn))

    print("")
    print("persistent differences (present in EVERY repeat):")
    any_diff = False
    for name, _ in strategies:
        if name in errors or name == base_name or not labels[name]:
            continue
        runs = labels[name]
        missing = set.intersection(*[set(labels[base_name][i]) - set(runs[i])
                                     for i in range(len(runs))])
        extra = set.intersection(*[set(runs[i]) - set(labels[base_name][i])
                                   for i in range(len(runs))])
        if missing or extra:
            any_diff = True
            print("  {n}:".format(n=name))
            if missing:
                print("     missing: " + " | ".join(sorted(missing)))
            if extra:
                print("     extra:   " + " | ".join(sorted(extra)))
    if not any_diff:
        print("  none - every strategy agreed with the walk in every repeat")

    if args.show and labels[base_name]:
        common = set.intersection(*[set(r) for r in labels[base_name]])
        print("")
        print("baseline labels stable across repeats ({n}):".format(n=len(common)))
        for lbl in sorted(common):
            print("   " + lbl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
