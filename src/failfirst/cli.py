"""Command line entry point: wire revisions, worktrees, child runs and the verdict."""

import argparse
import json
import shutil
import os
import signal
import subprocess
import sys
import tempfile
import time

from . import verdict as vd
from . import worktree as wt

EXIT_CODES = {vd.DETECTED: 0, vd.NOT_DETECTED: 1, vd.INCONCLUSIVE: 2}
EXIT_USAGE = 3
CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "child.py")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"failfirst: error: {message}\n")


def build_parser():
    p = _Parser(
        prog="failfirst",
        description="Run the tests a fix commit ships against the code before the fix "
        "and report whether they actually fail there.",
    )
    p.add_argument("--repo", required=True, help="path to the git repository")
    p.add_argument("--after", required=True, help="revision that contains the fix and the test")
    p.add_argument("--before", help="revision before the fix (default: first parent of --after)")
    p.add_argument("--test", action="append", required=True, metavar="PATH",
                   help="test file, relative to the repo root (repeatable)")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    p.add_argument("--keep", action="store_true", help="keep the temporary worktrees")
    p.add_argument("--timeout", type=float, default=300.0, metavar="SEC",
                   help="timeout per side in seconds (default: 300)")
    p.add_argument("--python", default=sys.executable, help="Python used to run the tests")
    return p


def _tail(text, n=2000):
    return text[-n:] if text else ""


def run_side(python, worktree_dir, tests, timeout, scratch, name):
    """Run child.py in one worktree. Always returns a side dict."""
    out_path = os.path.join(scratch, f"{name}-result.json")
    cmd = [python, CHILD, "--worktree", worktree_dir, "--out", out_path]
    for t in tests:
        cmd += ["--test", t]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    side = {"timed_out": False, "fatal": None, "load_errors": [], "tests_run": 0,
            "results": [], "import_check": {"local_modules": []}}
    start = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=worktree_dir, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace",
                            start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        side["timed_out"] = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        output, _ = proc.communicate()
    side["duration_sec"] = round(time.monotonic() - start, 2)
    side["output_tail"] = _tail(output)
    if not side["timed_out"]:
        try:
            with open(out_path, encoding="utf-8") as fh:
                side.update(json.load(fh))
        except (OSError, ValueError):
            side["fatal"] = (f"child exited with code {proc.returncode} without writing "
                             f"a result; output tail: {_tail(output, 500)!r}")
    return side


def _short(sha):
    return sha[:12]


def _unit_label(u, tests_with_subtests=frozenset()):
    if u["subtest"]:
        return f"{u['test']} {u['subtest']}"
    if u["test"] in tests_with_subtests:
        return f"{u['test']} (test body, outside subTests)"
    return u["test"]


def _outcome_text(outcome):
    # Text report only; the JSON report keeps the raw value.
    return "pass (subTests reported separately)" if outcome == "subtests_only" else outcome


def render_text(report):
    lines = []
    add = lines.append
    add(f"failfirst verdict: {report['verdict']}")
    add("")
    add(f"before: {report['before']['sha']}" + ("" if report["before"]["explicit"] else "  (first parent of after)"))
    add(f"after:  {report['after']['sha']}")
    add("")
    add("Test files copied from after into before:")
    for t in report["tests"]:
        note = ("did not exist in before (added by the change)" if t["in_before"] == "added"
                else "existed in before (overwritten with the after version)")
        add(f"  {t['path']}: {note}")
    add("")
    add("Import check (where the code under test was loaded from):")
    for side in ("before", "after"):
        mods = report["sides"][side]["import_check"]["local_modules"]
        if not mods:
            add(f"  {side}: no local module loaded")
        for m in mods:
            where = "inside its worktree" if m["inside_worktree"] else "OUTSIDE its worktree"
            add(f"  {side}: {m['name']} -> {m['file']} ({where})")
    add("")
    add("Run summary:")
    for side in ("before", "after"):
        s = report["sides"][side]
        extra = " TIMED OUT" if s["timed_out"] else ""
        add(f"  {side}: {s['tests_run']} test(s) run in {s.get('duration_sec', '?')}s{extra}")
    if report["reasons"]:
        add("")
        add("Why this is inconclusive:")
        for r in report["reasons"]:
            add(f"  - {r}")
    units = report["units"]
    if units:
        c = report["counts"]
        add("")
        add(f"Per test / subTest ({len(units)} units: {c['detected']} detected, "
            f"{c['not-detected']} not-detected, {c['inconclusive']} inconclusive):")
        with_subtests = frozenset(u["test"] for u in units if u["subtest"])
        for u in units:
            add(f"  [{u['status']}] {_unit_label(u, with_subtests)}")
            add(f"      before: {_outcome_text(u['before'])}  after: {_outcome_text(u['after'])}")
            if u["status"] == vd.DETECTED:
                add(f"      evidence (before): {u['evidence'].splitlines()[0]}")
            elif u["status"] == vd.NOT_DETECTED:
                add("      passes on both sides: this unit does not tell before and after apart")
            else:
                add(f"      reason: {u['reason']}")
    add("")
    return "\n".join(lines)


def run(args):
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        raise wt.UsageError(f"repo path is not a directory: {args.repo}")
    tests = [wt.normalize_test_path(t) for t in args.test]
    after_sha = wt.resolve_commit(repo, args.after)
    before_sha = wt.resolve_commit(repo, args.before) if args.before else wt.first_parent(repo, after_sha)

    scratch = tempfile.mkdtemp(prefix="failfirst-")
    before_dir = os.path.join(scratch, "before")
    after_dir = os.path.join(scratch, "after")
    created = []
    try:
        for path, sha in ((before_dir, before_sha), (after_dir, after_sha)):
            wt.add_worktree(repo, path, sha)
            created.append(path)
        test_info = [{"path": t, "in_before": wt.copy_test_file(after_dir, before_dir, t)} for t in tests]
        sides = {
            "before": run_side(args.python, before_dir, tests, args.timeout, scratch, "before"),
            "after": run_side(args.python, after_dir, tests, args.timeout, scratch, "after"),
        }
    finally:
        if args.keep:
            print(f"failfirst: worktrees kept in {scratch}", file=sys.stderr)
        else:
            for path in created:
                wt.remove_worktree(repo, path)
            wt.prune(repo)
            shutil.rmtree(scratch, ignore_errors=True)

    result = vd.evaluate(sides["before"], sides["after"])
    return {
        "verdict": result["verdict"],
        "exit_code": EXIT_CODES[result["verdict"]],
        "before": {"sha": before_sha, "explicit": bool(args.before)},
        "after": {"sha": after_sha},
        "tests": test_info,
        "reasons": result["reasons"],
        "counts": result["counts"],
        "units": result["units"],
        "sides": sides,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        report = run(args)
    except wt.UsageError as exc:
        print(f"failfirst: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # tool bug or environment problem
        print(f"failfirst: internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
