import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from failfirst import verdict as vd

T = "tests.test_x.Case.test_a"


def rec(outcome, sub=None, test=T, exc=None, msg=None):
    return {"test": test, "subtest": sub, "outcome": outcome,
            "exc_type": exc, "message": msg, "traceback": None}


def side(results, tests_run=None, local=True, **kw):
    mods = [{"name": "pkg", "file": "/w/pkg/__init__.py", "inside_worktree": True}] if local else []
    s = {"timed_out": False, "fatal": None, "load_errors": [],
         "tests_run": len({r["test"] for r in results}) if tests_run is None else tests_run,
         "results": results, "import_check": {"local_modules": mods}}
    s.update(kw)
    return s


FAIL = rec("failure", exc="AssertionError", msg="1 != 2")


class VerdictTests(unittest.TestCase):
    def test_detected(self):
        r = vd.evaluate(side([FAIL]), side([rec("pass")]))
        self.assertEqual(r["verdict"], vd.DETECTED)
        self.assertEqual(r["units"][0]["evidence"], "AssertionError: 1 != 2")

    def test_not_detected(self):
        r = vd.evaluate(side([rec("pass")]), side([rec("pass")]))
        self.assertEqual(r["verdict"], vd.NOT_DETECTED)
        self.assertEqual(r["reasons"], [])

    def test_subtest_mixed_is_detected_with_per_input_detail(self):
        before = side([rec("failure", "(x=1)", exc="AssertionError", msg="bad"),
                       rec("pass", "(x=0)"), rec("subtests_only")])
        after = side([rec("pass", "(x=1)"), rec("pass", "(x=0)"), rec("pass")])
        r = vd.evaluate(before, after)
        self.assertEqual(r["verdict"], vd.DETECTED)
        status = {u["subtest"]: u["status"] for u in r["units"]}
        self.assertEqual(status["(x=1)"], vd.DETECTED)
        self.assertEqual(status["(x=0)"], vd.NOT_DETECTED)
        self.assertEqual(status[None], vd.NOT_DETECTED)  # subtests_only counts as pass

    def test_before_non_assertion_error_is_inconclusive(self):
        r = vd.evaluate(side([rec("error", exc="TypeError", msg="x")]), side([rec("pass")]))
        self.assertEqual(r["verdict"], vd.INCONCLUSIVE)
        self.assertIn("before raised TypeError, not an assertion failure", r["reasons"][0])

    def test_one_error_subtest_spoils_a_detected_result(self):
        before = side([FAIL, rec("error", "(x=2)", exc="KeyError", msg="k")])
        after = side([rec("pass"), rec("pass", "(x=2)")])
        self.assertEqual(vd.evaluate(before, after)["verdict"], vd.INCONCLUSIVE)

    def test_after_not_passing_is_inconclusive(self):
        r = vd.evaluate(side([FAIL]), side([rec("failure", exc="AssertionError")]))
        self.assertEqual(r["verdict"], vd.INCONCLUSIVE)
        self.assertIn("after side did not pass", r["reasons"][0])

    def test_skip_is_inconclusive(self):
        r = vd.evaluate(side([rec("skip")]), side([rec("pass")]))
        self.assertEqual(r["verdict"], vd.INCONCLUSIVE)

    def test_skipped_on_both_sides_is_a_skipped_unit(self):
        r = vd.evaluate(side([rec("skip", msg="needs wcwidth")]),
                        side([rec("skip", msg="needs wcwidth")]))
        self.assertEqual(r["units"][0]["status"], vd.SKIPPED)
        self.assertIn("needs wcwidth", r["units"][0]["reason"])
        self.assertEqual(r["counts"]["skipped"], 1)

    def test_skipped_on_one_side_only_is_inconclusive(self):
        for b, a in (("skip", "pass"), ("pass", "skip")):
            r = vd.evaluate(side([rec(b)]), side([rec(a)]))
            self.assertEqual(r["units"][0]["status"], vd.INCONCLUSIVE, (b, a))
            self.assertEqual(r["verdict"], vd.INCONCLUSIVE, (b, a))
            self.assertEqual(r["counts"]["skipped"], 0)

    def test_detected_with_unrelated_skip_is_detected(self):
        other = "tests.test_x.Case.test_skipped"
        before = side([FAIL, rec("skip", test=other, msg="no wcwidth")])
        after = side([rec("pass"), rec("skip", test=other, msg="no wcwidth")])
        r = vd.evaluate(before, after)
        self.assertEqual(r["verdict"], vd.DETECTED)
        self.assertEqual(r["reasons"], [])
        self.assertEqual((r["counts"]["detected"], r["counts"]["skipped"]), (1, 1))

    def test_not_detected_plus_skip_is_inconclusive(self):
        other = "tests.test_x.Case.test_skipped"
        before = side([rec("pass"), rec("skip", test=other, msg="no wcwidth")])
        after = side([rec("pass"), rec("skip", test=other, msg="no wcwidth")])
        r = vd.evaluate(before, after)
        self.assertEqual(r["verdict"], vd.INCONCLUSIVE)
        self.assertIn("1 test(s) were skipped on both sides; they may be the ones "
                      "that exercise the fix", r["reasons"][0])
        self.assertTrue(any(other in x and "no wcwidth" in x for x in r["reasons"]))

    def test_inconclusive_unit_still_wins_over_detected_and_skipped(self):
        other = "tests.test_x.Case.test_skipped"
        before = side([FAIL, rec("skip", test=other), rec("error", "(x=1)", exc="KeyError")])
        after = side([rec("pass"), rec("skip", test=other), rec("pass", "(x=1)")])
        self.assertEqual(vd.evaluate(before, after)["verdict"], vd.INCONCLUSIVE)

    def test_missing_on_a_side(self):
        r = vd.evaluate(side([FAIL]), side([rec("pass"), rec("pass", test="tests.t.C.other")]))
        self.assertEqual(r["verdict"], vd.INCONCLUSIVE)
        self.assertTrue(any("missing on before side" in x for x in r["reasons"]))

    def test_load_error(self):
        r = vd.evaluate(side([], load_errors=["ImportError: nope"]), side([rec("pass")]))
        self.assertEqual(r["verdict"], vd.INCONCLUSIVE)
        self.assertTrue(any("load error" in x for x in r["reasons"]))

    def test_zero_tests_run(self):
        r = vd.evaluate(side([], tests_run=0), side([], tests_run=0))
        self.assertEqual(r["verdict"], vd.INCONCLUSIVE)
        self.assertTrue(any("no tests were run" in x for x in r["reasons"]))

    def test_timeout(self):
        r = vd.evaluate(side([], timed_out=True), side([rec("pass")]))
        self.assertEqual(r["verdict"], vd.INCONCLUSIVE)
        self.assertTrue(any("timed out" in x for x in r["reasons"]))

    def test_import_check_module_outside_worktree(self):
        before = side([FAIL])
        before["import_check"]["local_modules"][0]["inside_worktree"] = False
        r = vd.evaluate(before, side([rec("pass")]))
        self.assertEqual(r["verdict"], vd.INCONCLUSIVE)
        self.assertTrue(any("outside its worktree" in x for x in r["reasons"]))

    def test_import_check_no_local_module_loaded(self):
        r = vd.evaluate(side([FAIL]), side([rec("pass")], local=False))
        self.assertEqual(r["verdict"], vd.INCONCLUSIVE)
        self.assertTrue(any("no local module was loaded" in x for x in r["reasons"]))

    def test_all_reasons_are_listed(self):
        r = vd.evaluate(side([rec("error", exc="TypeError")], local=False),
                        side([rec("failure", exc="AssertionError")]))
        self.assertEqual(r["verdict"], vd.INCONCLUSIVE)
        text = "\n".join(r["reasons"])
        self.assertIn("no local module was loaded", text)
        self.assertIn("after side did not pass", text)


if __name__ == "__main__":
    unittest.main()
