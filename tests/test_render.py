import unittest

from failfirst.cli import render_text

BODY = "tests.test_x.T.test_with_subtests"
PLAIN = "tests.test_x.T.test_plain"


def _side():
    return {"import_check": {"local_modules": []}, "timed_out": False, "tests_run": 1,
            "duration_sec": 0.1}


def _unit(test, subtest, before, after, status, **extra):
    u = {"test": test, "subtest": subtest, "before": before, "after": after,
         "status": status}
    u.update(extra)
    return u


def _report(units):
    return {
        "verdict": "detected",
        "before": {"sha": "a" * 40, "explicit": True},
        "after": {"sha": "b" * 40},
        "tests": [{"path": "tests/test_x.py", "in_before": "added"}],
        "reasons": [],
        "counts": {"detected": 1, "not-detected": 2, "inconclusive": 0},
        "units": units,
        "sides": {"before": _side(), "after": _side()},
    }


class RenderTextTests(unittest.TestCase):
    def setUp(self):
        self.text = render_text(_report([
            _unit(BODY, "(n=1)", "failure", "pass", "detected",
                  evidence="AssertionError: boom"),
            _unit(BODY, None, "subtests_only", "pass", "not-detected"),
            _unit(PLAIN, None, "pass", "pass", "not-detected"),
        ]))

    def test_body_of_test_with_subtests_is_labelled(self):
        self.assertIn(f"[not-detected] {BODY} (test body, outside subTests)", self.text)

    def test_subtests_only_is_shown_as_pass(self):
        self.assertIn("before: pass (subTests reported separately)  after: pass", self.text)
        self.assertNotIn("subtests_only", self.text)

    def test_test_without_subtests_is_not_labelled(self):
        self.assertIn(f"[not-detected] {PLAIN}\n", self.text)
        self.assertNotIn(f"{PLAIN} (test body", self.text)

    def test_subtest_unit_keeps_its_own_label(self):
        self.assertIn(f"[detected] {BODY} (n=1)\n", self.text)


if __name__ == "__main__":
    unittest.main()
