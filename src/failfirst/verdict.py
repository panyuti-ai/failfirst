"""Pure verdict logic. No file access, no subprocesses.

Input is two "side" dicts (one per revision) as produced by ``child.py`` and
completed by the CLI::

    {
      "timed_out": bool,
      "fatal": str | None,            # child crashed or produced no result
      "load_errors": [str],
      "tests_run": int,
      "results": [                    # one record per test body and per subTest
        {"test": id, "subtest": str | None, "outcome": str,
         "exc_type": str | None, "message": str | None, "traceback": str | None},
      ],
      "import_check": {"local_modules": [{"name", "file", "inside_worktree"}]},
    }

Outcomes are: pass, failure, error, skip, expected_failure,
unexpected_success, and (test bodies only) subtests_only.
"""

DETECTED = "detected"
NOT_DETECTED = "not-detected"
INCONCLUSIVE = "inconclusive"
SKIPPED = "skipped"  # unit status only: skipped on both sides

PASSING = ("pass", "subtests_only")


def _is_pass(outcome):
    return outcome in PASSING


def _first_line(text, limit=300):
    for line in (text or "").strip().splitlines()[::-1]:
        if line.strip():
            return line.strip()[:limit]
    return ""


def side_problems(name, side):
    """Reasons that make a whole side unusable (timeout, crash, load error, ...)."""
    problems = []
    if side.get("timed_out"):
        problems.append(f"{name}: test run timed out")
    if side.get("fatal"):
        problems.append(f"{name}: test run failed: {_first_line(side['fatal'])}")
    for err in side.get("load_errors") or []:
        problems.append(f"{name}: load error: {_first_line(err)}")
    return problems


def _side_usable(side):
    return not (side.get("timed_out") or side.get("fatal") or side.get("load_errors"))


def import_problems(name, side):
    """Reasons the code under test cannot be tied to this side's checkout."""
    mods = (side.get("import_check") or {}).get("local_modules") or []
    problems = []
    for mod in mods:
        if not mod.get("inside_worktree"):
            problems.append(
                f"{name}: local module '{mod.get('name')}' was loaded from outside "
                f"its worktree ({mod.get('file')})"
            )
    if not mods:
        problems.append(
            f"{name}: no local module was loaded, so the code under test cannot "
            "be tied to this revision"
        )
    return problems


def _index(side):
    """Map (test, subtest) -> record; repeated keys are kept apart by order."""
    out = {}
    for rec in side.get("results") or []:
        key = (rec["test"], rec.get("subtest"))
        n = 1
        while (key[0], key[1], n) in out:
            n += 1
        out[(key[0], key[1], n)] = rec
    return out


def _evidence(rec):
    exc = rec.get("exc_type") or "AssertionError"
    msg = (rec.get("message") or "").strip()
    return f"{exc}: {msg}" if msg else exc


def compare_unit(before, after):
    """Return (status, reason) for one unit. Records may be None (missing)."""
    if before is None or after is None:
        missing = [n for n, r in (("before", before), ("after", after)) if r is None]
        return INCONCLUSIVE, "missing on " + " and ".join(missing) + " side"
    bo, ao = before["outcome"], after["outcome"]
    if bo == "skip" and ao == "skip":
        why = before.get("message") or after.get("message") or "no reason given"
        return SKIPPED, f"skipped on both sides: {why}"
    if not _is_pass(ao):
        extra = f": {after['exc_type']}" if after.get("exc_type") else ""
        return INCONCLUSIVE, f"after side did not pass (outcome {ao}{extra})"
    if _is_pass(bo):
        return NOT_DETECTED, None
    if bo == "failure":
        return DETECTED, None
    if bo == "error":
        return INCONCLUSIVE, (
            f"before raised {before.get('exc_type') or 'an exception'}, "
            "not an assertion failure"
        )
    return INCONCLUSIVE, f"before outcome was {bo}, not an assertion failure"


def evaluate(before, after):
    """Combine two sides into a verdict.

    Returns {"verdict", "reasons", "units", "counts"}. ``reasons`` lists every
    cause of an inconclusive result (empty otherwise).

    A unit skipped on both sides has status ``skipped``. It is not a pass, but it
    does not spoil a ``detected`` verdict either; without any detected unit it
    makes the verdict inconclusive, because the skipped test may be the one that
    exercises the fix.
    """
    reasons = []
    for name, side in (("before", before), ("after", after)):
        reasons += side_problems(name, side)
        if _side_usable(side) and not side.get("tests_run"):
            reasons.append(f"{name}: no tests were run")
        if _side_usable(side):
            reasons += import_problems(name, side)

    units = []
    if _side_usable(before) and _side_usable(after):
        bi, ai = _index(before), _index(after)
        keys = list(bi) + [k for k in ai if k not in bi]
        for key in keys:
            b, a = bi.get(key), ai.get(key)
            status, why = compare_unit(b, a)
            units.append(
                {
                    "test": key[0],
                    "subtest": key[1],
                    "before": b["outcome"] if b else None,
                    "after": a["outcome"] if a else None,
                    "status": status,
                    "reason": why,
                    "evidence": _evidence(b) if status == DETECTED else None,
                    "before_traceback": b.get("traceback") if b and status == DETECTED else None,
                }
            )
        for u in units:
            if u["status"] == INCONCLUSIVE:
                label = u["test"] + (f" {u['subtest']}" if u["subtest"] else "")
                reasons.append(f"{label}: {u['reason']}")

    if not reasons and not units:
        reasons.append("no comparable test results were recorded")

    counts = {DETECTED: 0, NOT_DETECTED: 0, INCONCLUSIVE: 0, SKIPPED: 0}
    for u in units:
        counts[u["status"]] += 1

    if reasons:
        verdict = INCONCLUSIVE
    elif counts[DETECTED] > 0:
        verdict = DETECTED
    elif counts[SKIPPED] > 0:
        verdict = INCONCLUSIVE
        reasons.append(
            f"{counts[SKIPPED]} test(s) were skipped on both sides; they may be the "
            "ones that exercise the fix"
        )
        for u in units:
            if u["status"] == SKIPPED:
                label = u["test"] + (f" {u['subtest']}" if u["subtest"] else "")
                reasons.append(f"{label}: {u['reason']}")
    else:
        verdict = NOT_DETECTED
    return {"verdict": verdict, "reasons": reasons, "units": units, "counts": counts}
