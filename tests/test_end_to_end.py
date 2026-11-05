import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

from failfirst import cli  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(textwrap.dedent(text))


def git(root, *args):
    return subprocess.run(GIT + list(args), cwd=root, check=True, capture_output=True, text=True)


def make_repo(root, fixed_tests):
    """Commit 1: buggy package. Commit 2: fix + the given test files."""
    git(root, "init", "-q")
    write(root, "buggy/__init__.py", """\
        def double(x):
            return x + x if x != 3 else 7   # bug: double(3) is wrong
        """)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "buggy")
    write(root, "buggy/__init__.py", """\
        def double(x):
            return x + x

        def triple(x):
            return 3 * x
        """)
    for rel, text in fixed_tests.items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fix")


ASSERT_TEST = """\
    import unittest
    from buggy import double

    class T(unittest.TestCase):
        def test_double(self):
            for x in (1, 2, 3):
                with self.subTest(x=x):
                    self.assertEqual(double(x), 2 * x)
    """
GUARD_TEST = """\
    import unittest
    from buggy import double

    class T(unittest.TestCase):
        def test_double(self):
            self.assertEqual(double(2), 4)
    """
ATTR_TEST = """\
    import unittest
    from buggy import triple

    class T(unittest.TestCase):
        def test_triple(self):
            self.assertEqual(triple(2), 6)
    """


class EndToEnd(unittest.TestCase):
    def run_cli(self, tests, args):
        with tempfile.TemporaryDirectory() as root:
            make_repo(root, tests)
            before = git(root, "branch", "--show-current").stdout
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--repo", root, "--after", "HEAD"] + args)
            # the user's repo must be left clean: no leftover worktrees
            wl = git(root, "worktree", "list").stdout.strip().splitlines()
            self.assertEqual(len(wl), 1, wl)
            self.assertEqual(git(root, "branch", "--show-current").stdout, before)
            return code, out.getvalue(), err.getvalue()

    def test_assertion_bug_is_detected(self):
        code, out, _ = self.run_cli({"tests/test_fix.py": ASSERT_TEST},
                                    ["--test", "tests/test_fix.py"])
        self.assertEqual(code, 0, out)
        self.assertIn("verdict: detected", out)
        self.assertIn("added by the change", out)

    def test_json_output(self):
        code, out, _ = self.run_cli({"tests/test_fix.py": ASSERT_TEST},
                                    ["--test", "tests/test_fix.py", "--json"])
        data = json.loads(out)
        self.assertEqual((code, data["verdict"], data["exit_code"]), (0, "detected", 0))
        self.assertEqual(data["counts"]["detected"], 1)
        self.assertEqual(data["counts"]["not-detected"], 3)  # x=1, x=2 and the test body

    def test_guard_test_is_not_detected(self):
        code, out, _ = self.run_cli({"tests/test_fix.py": GUARD_TEST},
                                    ["--test", "tests/test_fix.py"])
        self.assertEqual(code, 1, out)
        self.assertIn("verdict: not-detected", out)

    def test_missing_function_before_is_inconclusive(self):
        code, out, _ = self.run_cli({"tests/test_fix.py": ATTR_TEST},
                                    ["--test", "tests/test_fix.py"])
        self.assertEqual(code, 2, out)
        self.assertIn("verdict: inconclusive", out)
        self.assertIn("ImportError", out)  # `from buggy import triple` fails to import

    def test_attribute_error_is_inconclusive(self):
        test = """\
            import unittest
            import buggy

            class T(unittest.TestCase):
                def test_triple(self):
                    self.assertEqual(buggy.triple(2), 6)
            """
        code, out, _ = self.run_cli({"tests/test_fix.py": test}, ["--test", "tests/test_fix.py"])
        self.assertEqual(code, 2, out)
        self.assertIn("before raised AttributeError, not an assertion failure", out)

    def test_missing_test_file_is_usage_error(self):
        code, _, err = self.run_cli({"tests/test_fix.py": GUARD_TEST},
                                    ["--test", "tests/test_nope.py"])
        self.assertEqual(code, 3)
        self.assertIn("not found in the after revision", err)

    def test_overwritten_test_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            git(root, "init", "-q")
            write(root, "buggy/__init__.py", "def double(x):\n    return x * 3\n")
            write(root, "tests/test_fix.py", GUARD_TEST)
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "buggy")
            write(root, "buggy/__init__.py", "def double(x):\n    return x * 2\n")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "fix")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(["--repo", root, "--after", "HEAD", "--test", "tests/test_fix.py"])
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("overwritten", out.getvalue())

    def test_bad_arguments_exit_3(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self._exit_code(["--repo", "x"]), 3)

    def _exit_code(self, argv):
        try:
            return cli.main(argv)
        except SystemExit as exc:
            return exc.code


if __name__ == "__main__":
    unittest.main()
