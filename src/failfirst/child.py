"""Run test files inside one checkout and write per-test results as JSON.

This file is executed by path (not imported) in a separate interpreter whose
cwd is the checkout. It only uses the standard library, except that the pytest
runner imports pytest from the target interpreter.

Usage: child.py --worktree DIR --out FILE [--runner unittest|pytest]
                --test tests/test_x.py [--test ...]
"""

import argparse
import json
import os
import sys
import traceback
import unittest

MAX_MESSAGE = 4000
TB_TAIL_LINES = 8


def module_name(test_path):
    path = test_path.replace(os.sep, "/")
    if path.endswith(".py"):
        path = path[:-3]
    return path.strip("/").replace("/", ".")


def _describe(err, test):
    exc_type, value, tb = err
    lines = "".join(traceback.format_exception(exc_type, value, tb)).rstrip().splitlines()
    return {
        "exc_type": exc_type.__name__,
        "message": str(value)[:MAX_MESSAGE],
        "traceback": "\n".join(lines[-TB_TAIL_LINES:]),
    }


class RecordingResult(unittest.TestResult):
    """Records one entry per test body and one per subTest."""

    def __init__(self):
        super().__init__()
        self.records = []
        self._body = {}  # test id -> record
        self._subtest_bad = set()  # test ids with a failed/errored subTest
        self._sub_seen = {}  # (test id, description) -> count
        self.tests_started = 0

    def _add(self, test, outcome, err=None, subtest=None):
        rec = {
            "test": test.id(),
            "subtest": subtest,
            "outcome": outcome,
            "exc_type": None,
            "message": None,
            "traceback": None,
        }
        if err is not None:
            rec.update(_describe(err, test))
        self.records.append(rec)
        return rec

    def startTest(self, test):
        super().startTest(test)
        self.tests_started += 1

    def stopTest(self, test):
        super().stopTest(test)
        tid = test.id()
        if tid not in self._body:
            # unittest skips addSuccess when a subTest failed. Everything outside
            # the subTests then ran without error.
            outcome = "subtests_only" if tid in self._subtest_bad else "unknown"
            self._body[tid] = self._add(test, outcome)

    def _body_outcome(self, test, outcome, err=None):
        self._body[test.id()] = self._add(test, outcome, err)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._body_outcome(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._body_outcome(test, "failure", err)

    def addError(self, test, err):
        super().addError(test, err)
        self._body_outcome(test, "error", err)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._body_outcome(test, "skip")
        self._body[test.id()]["message"] = str(reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._body_outcome(test, "expected_failure", err)

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._body_outcome(test, "unexpected_success")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        # On Python 3.11+ this is also called for passing subTests (err is None).
        try:
            desc = subtest._subDescription()
        except Exception:
            desc = str(subtest)
        key = (test.id(), desc)
        self._sub_seen[key] = self._sub_seen.get(key, 0) + 1
        if self._sub_seen[key] > 1:
            desc = f"{desc} #{self._sub_seen[key]}"
        if err is None:
            self._add(test, "pass", subtest=desc)
            return
        is_assertion = isinstance(err[1], test.failureException)
        self._subtest_bad.add(test.id())
        self._add(test, "failure" if is_assertion else "error", err, subtest=desc)


def _walk(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _walk(item)
        else:
            yield item


def local_candidates(roots, skip_names):
    names = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            full = os.path.join(root, entry)
            if entry.startswith("."):
                continue
            if os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                names.add(entry)
            elif entry.endswith(".py") and os.path.isfile(full):
                stem = entry[:-3]
                if stem not in ("setup", "conftest", "__init__"):
                    names.add(stem)
    return sorted(names - skip_names)


def import_check(roots, worktree, skip_names):
    real_root = os.path.realpath(worktree)
    mods = []
    for name in local_candidates(roots, skip_names):
        mod = sys.modules.get(name)
        if mod is None:
            continue
        file = getattr(mod, "__file__", None)
        if file is None:
            paths = list(getattr(mod, "__path__", []) or [])
            file = paths[0] if paths else None
        inside = False
        if file:
            real = os.path.realpath(file)
            inside = real == real_root or real.startswith(real_root + os.sep)
        mods.append({"name": name, "file": file, "inside_worktree": inside})
    return mods


# A unit's outcome is the worst outcome of its setup/call/teardown phases.
_SEVERITY = {"pass": 0, "expected_failure": 1, "skip": 2, "unexpected_success": 3,
             "failure": 4, "error": 5}
MAX_LOAD_ERROR = 2000


def _skip_reason(report):
    lr = report.longrepr
    reason = lr[2] if isinstance(lr, tuple) and len(lr) == 3 else str(lr or "")
    if reason.startswith("Skipped: "):
        reason = reason[len("Skipped: "):]
    return reason[:MAX_MESSAGE]


def make_pytest_recorder(pytest):
    """Build the plugin object. Needs the pytest module, so it is created lazily."""

    class Recorder:
        def __init__(self):
            self.units = {}  # nodeid -> record, in first-seen order
            self.load_errors = []
            self.subtest_failed = set()

        def _merge(self, nodeid, outcome, **info):
            rec = self.units.get(nodeid)
            if rec is None:
                rec = {"test": nodeid, "subtest": None, "outcome": "pass",
                       "exc_type": None, "message": None, "traceback": None}
                self.units[nodeid] = rec
            if _SEVERITY[outcome] > _SEVERITY[rec["outcome"]]:
                rec.update(outcome=outcome, exc_type=None, message=None, traceback=None)
                rec.update(info)

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            outcome = yield
            report = outcome.get_result()
            nodeid = item.nodeid
            excinfo = call.excinfo
            wasxfail = hasattr(report, "wasxfail")
            if report.skipped:
                if wasxfail:
                    self._merge(nodeid, "expected_failure", message=str(report.wasxfail))
                else:
                    self._merge(nodeid, "skip", message=_skip_reason(report))
            elif report.failed:
                if excinfo is None and str(report.longrepr).startswith("[XPASS(strict)]"):
                    self._merge(nodeid, "unexpected_success")
                elif excinfo is None:
                    self._merge(nodeid, "error", message=str(report.longrepr)[:MAX_MESSAGE])
                else:
                    failure_exc = getattr(getattr(item, "instance", None),
                                          "failureException", AssertionError)
                    is_assertion = (report.when == "call"
                                    and excinfo.errisinstance((AssertionError, failure_exc)))
                    self._merge(nodeid, "failure" if is_assertion else "error",
                                **_describe((excinfo.type, excinfo.value, excinfo.tb), item))
            elif wasxfail and report.when == "call":
                self._merge(nodeid, "unexpected_success")
            else:
                self._merge(nodeid, "pass")

        def pytest_runtest_logreport(self, report):
            # pytest 9 reports a failing subtest separately and can still mark the
            # test body as passed, so remember it and refuse to call the test a pass.
            if hasattr(report, "context") and report.failed:
                self.subtest_failed.add(report.nodeid)

        def pytest_collectreport(self, report):
            if report.failed:
                text = (report.longreprtext or "collection failed")[-MAX_LOAD_ERROR:]
                self.load_errors.append(f"{report.nodeid}: {text}")

        def finish(self):
            for nodeid in self.subtest_failed:
                self._merge(nodeid, "error", exc_type="SubtestFailed",
                            message="a subtest failed; failfirst does not judge pytest "
                                    "subtests one by one")
            return list(self.units.values())

    return Recorder()


def run_pytest(test_paths, payload):
    import pytest

    recorder = make_pytest_recorder(pytest)
    args = list(test_paths) + ["-p", "no:cacheprovider", "-q"]
    code = int(pytest.main(args, plugins=[recorder]))
    payload["load_errors"].extend(recorder.load_errors)
    if code in (2, 3, 4) and not payload["load_errors"]:
        names = {2: "interrupted", 3: "internal error", 4: "usage error"}
        payload["load_errors"].append(f"pytest exited with code {code} ({names[code]})")
    payload["results"] = recorder.finish()
    payload["tests_run"] = len(payload["results"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--test", action="append", required=True)
    ap.add_argument("--runner", choices=("unittest", "pytest"), default="unittest")
    args = ap.parse_args(argv)

    worktree = os.path.abspath(args.worktree)
    roots = [worktree]
    src = os.path.join(worktree, "src")
    if os.path.isdir(src):
        roots.append(src)
    # Drop this script's directory (sys.path[0]); put the checkout first instead.
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != here]
    for root in reversed(roots):
        sys.path.insert(0, root)
    os.chdir(worktree)

    payload = {
        "load_errors": [],
        "tests_run": 0,
        "results": [],
        "import_check": {"local_modules": []},
        "fatal": None,
    }
    module_names = [module_name(t) for t in args.test]
    skip = {n.split(".")[0] for n in module_names}
    try:
        if args.runner == "pytest":
            run_pytest([t.replace(os.sep, "/") for t in args.test], payload)
        else:
            loader = unittest.TestLoader()
            suite = unittest.TestSuite()
            for name in module_names:
                try:
                    suite.addTests(loader.loadTestsFromName(name))
                except BaseException:
                    payload["load_errors"].append(f"{name}: {traceback.format_exc()}")
            payload["load_errors"].extend(loader.errors)
            failed = [
                t.id()
                for t in _walk(suite)
                if isinstance(t, unittest.loader._FailedTest)
            ]
            if failed and not payload["load_errors"]:
                payload["load_errors"].extend(f"failed to load {tid}" for tid in failed)
            if not payload["load_errors"]:
                result = RecordingResult()
                suite.run(result)
                payload["tests_run"] = result.tests_started
                payload["results"] = result.records
    except BaseException:
        payload["fatal"] = traceback.format_exc()
    try:
        payload["import_check"]["local_modules"] = import_check(roots, worktree, skip)
    except Exception:
        payload["fatal"] = payload["fatal"] or traceback.format_exc()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
