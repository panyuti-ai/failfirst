"""End to end runs of the pytest runner against small throw-away git repos.

These need a Python that can import pytest (the interpreter running the tests).
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
sys.path.insert(0, HERE)

from failfirst import cli  # noqa: E402
from test_end_to_end import git, make_repo  # noqa: E402

try:
    import pytest  # noqa: F401
    HAVE_PYTEST = True
except ImportError:
    HAVE_PYTEST = False

# buggy.double(3) is wrong before the fix; buggy.triple does not exist before it.
PARAM_TEST = """\
    import pytest
    from buggy import double

    @pytest.mark.parametrize("x", [1, 2, 3])
    def test_double(x):
        assert double(x) == 2 * x
    """
GUARD_TEST = """\
    import pytest
    from buggy import double

    def test_double_two():
        assert double(2) == 4
    """
MISSING_FUNCTION_TEST = """\
    import buggy

    def test_triple():
        assert buggy.triple(2) == 6
    """
SKIP_BOTH_TEST = """\
    import pytest
    from buggy import double

    def test_needs_optional_package():
        pytest.importorskip("failfirst_no_such_optional_package")
        assert double(3) == 6

    def test_double_two():
        assert double(2) == 4
    """
SKIP_AND_DETECTED_TEST = """\
    import pytest
    from buggy import double

    def test_needs_optional_package():
        pytest.skip("optional package missing")

    def test_double_three():
        assert double(3) == 6
    """
UNITTEST_STYLE_TEST = """\
    import unittest
    from buggy import double

    class T(unittest.TestCase):
        def test_double_three(self):
            self.assertEqual(double(3), 6)
    """
MIXED_TESTS = {
    "tests/test_a.py": UNITTEST_STYLE_TEST,
    "tests/test_b.py": PARAM_TEST,
}


class PytestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE_PYTEST:
            raise unittest.SkipTest("pytest is not installed for this interpreter")

    def run_cli(self, tests, args, extra=("--json",)):
        with tempfile.TemporaryDirectory() as root:
            make_repo(root, tests)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--repo", root, "--after", "HEAD", "--python", sys.executable]
                                + list(args) + list(extra))
            wl = git(root, "worktree", "list").stdout.strip().splitlines()
            self.assertEqual(len(wl), 1, wl)  # no leftover worktrees
            return code, out.getvalue(), err.getvalue()

    def run_json(self, tests, args):
        code, out, err = self.run_cli(tests, args)
        return code, json.loads(out), out

    def test_parametrize_partly_detected(self):
        code, data, raw = self.run_json({"tests/test_fix.py": PARAM_TEST},
                                        ["--test", "tests/test_fix.py"])
        self.assertEqual((code, data["verdict"]), (0, "detected"), raw)
        self.assertEqual(data["runner"], "pytest")
        status = {u["test"]: u["status"] for u in data["units"]}
        self.assertEqual(status, {
            "tests/test_fix.py::test_double[1]": "not-detected",
            "tests/test_fix.py::test_double[2]": "not-detected",
            "tests/test_fix.py::test_double[3]": "detected",
        })
        self.assertTrue(all(u["subtest"] is None for u in data["units"]))
        detected = [u for u in data["units"] if u["status"] == "detected"][0]
        self.assertTrue(detected["evidence"].startswith("AssertionError"), detected["evidence"])
        # each side imported the code under test from its own worktree
        for side in ("before", "after"):
            mods = data["sides"][side]["import_check"]["local_modules"]
            self.assertEqual([m["name"] for m in mods], ["buggy"])
            self.assertTrue(all(m["inside_worktree"] for m in mods))

    def test_guard_test_is_not_detected(self):
        code, data, raw = self.run_json({"tests/test_fix.py": GUARD_TEST},
                                        ["--test", "tests/test_fix.py"])
        self.assertEqual((code, data["verdict"]), (1, "not-detected"), raw)

    def test_missing_function_before_is_inconclusive(self):
        code, data, raw = self.run_json({"tests/test_fix.py": MISSING_FUNCTION_TEST},
                                        ["--test", "tests/test_fix.py"])
        self.assertEqual((code, data["verdict"]), (2, "inconclusive"), raw)
        self.assertTrue(any("before raised AttributeError, not an assertion failure" in r
                            for r in data["reasons"]), data["reasons"])

    def test_skipped_on_both_sides_is_inconclusive_without_detection(self):
        code, data, raw = self.run_json({"tests/test_fix.py": SKIP_BOTH_TEST},
                                        ["--test", "tests/test_fix.py"])
        self.assertEqual((code, data["verdict"]), (2, "inconclusive"), raw)
        self.assertEqual(data["counts"]["skipped"], 1)
        self.assertEqual(data["counts"]["not-detected"], 1)
        text = "\n".join(data["reasons"])
        self.assertIn("1 test(s) were skipped on both sides", text)
        self.assertIn("test_needs_optional_package", text)

    def test_skipped_does_not_spoil_a_detection(self):
        code, out, _ = self.run_cli({"tests/test_fix.py": SKIP_AND_DETECTED_TEST},
                                    ["--test", "tests/test_fix.py"], extra=())
        self.assertEqual(code, 0, out)
        self.assertIn("verdict: detected", out)
        self.assertIn("1 skipped", out)
        self.assertIn("optional package missing", out)

    def test_auto_selects_pytest_and_reports_it(self):
        code, out, _ = self.run_cli({"tests/test_fix.py": PARAM_TEST},
                                    ["--test", "tests/test_fix.py"], extra=())
        self.assertEqual(code, 0, out)
        self.assertIn("runner: pytest (auto-selected)", out)

    def test_auto_with_mixed_files_uses_pytest_and_says_so(self):
        code, data, raw = self.run_json(MIXED_TESTS, ["--test", "tests/test_a.py",
                                                      "--test", "tests/test_b.py"])
        self.assertEqual((code, data["runner"]), (0, "pytest"), raw)
        self.assertIn("different runners", data["runner_note"])
        self.assertTrue(any(u["test"].startswith("tests/test_a.py::T::test_double_three")
                            and u["status"] == "detected" for u in data["units"]), raw)

    def test_auto_selects_unittest_for_unittest_files(self):
        code, data, raw = self.run_json({"tests/test_fix.py": UNITTEST_STYLE_TEST},
                                        ["--test", "tests/test_fix.py"])
        self.assertEqual((code, data["runner"]), (0, "unittest"), raw)

    def test_explicit_runner_overrides_auto(self):
        code, data, raw = self.run_json({"tests/test_fix.py": UNITTEST_STYLE_TEST},
                                        ["--test", "tests/test_fix.py", "--runner", "pytest"])
        self.assertEqual((code, data["runner"], data["runner_requested"]), (0, "pytest", "pytest"), raw)

    def test_show_all_lists_not_detected_units(self):
        args = ["--test", "tests/test_fix.py"]
        _, short, _ = self.run_cli({"tests/test_fix.py": PARAM_TEST}, args, extra=())
        _, full, _ = self.run_cli({"tests/test_fix.py": PARAM_TEST}, args, extra=("--show-all",))
        self.assertNotIn("[not-detected]", short)
        self.assertIn("2 not-detected unit(s) not listed", short)
        self.assertIn("[not-detected] tests/test_fix.py::test_double[1]", full)


class PytestMissing(unittest.TestCase):
    def test_pytest_not_installed_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as root:
            make_repo(root, {"tests/test_fix.py": PARAM_TEST})
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--repo", root, "--after", "HEAD", "--test", "tests/test_fix.py",
                                 "--runner", "pytest", "--python", "/nonexistent/python"])
            self.assertEqual(code, 3)
            self.assertIn("pytest is not installed in /nonexistent/python; "
                          "install it or pass --python", err.getvalue())
            self.assertEqual(len(git(root, "worktree", "list").stdout.strip().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
