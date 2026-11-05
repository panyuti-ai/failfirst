# failfirst

Run the test a fix commit ships against the code *before* the fix, and report whether it actually fails there.

## Why

When a human or an AI agent hands you "bug fixed, new test is green", the missing question is whether that test ever touched the bug. A test that also passes on the unfixed code proves nothing about the fix. failfirst copies the test file from the fixed revision into the unfixed one, runs both sides in separate processes, and compares the results test by test (and subTest by subTest). It runs `unittest` and `pytest` tests.

## Results

| Result | Meaning | Exit code |
| --- | --- | --- |
| `detected` | Before the fix the test fails with an assertion failure (`AssertionError` / `failureException`); after the fix it passes. | 0 |
| `not-detected` | Passes on both sides. It does not touch the bug. | 1 |
| `inconclusive` | Anything else: a non-assertion error before the fix (`TypeError`, `AttributeError`, `ImportError`, ...), tests not found or not loadable, no test ran, the fixed side does not pass, timeout, a skip on only one side, tests skipped on both sides when nothing else was detected (see Skipped tests), or the code under test was loaded from outside that revision's checkout. | 2 |

Usage or tool errors exit with 3. The guiding rule: when in doubt, say `inconclusive` rather than report false evidence as `detected`.

## Install

```
pip install -e .
```

Python 3.10+, standard library only. The pytest runner needs pytest installed in the Python that runs the tests (`--python`); failfirst itself does not import it.

### Windows

- If you have no Python yet (typing `python --version` in PowerShell only shows a Microsoft Store prompt), run `winget install Python.Python.3.13`, then close and reopen PowerShell.
- Install with `py -m pip install -e .`
- If the `failfirst` command is not found, run it as `py -m failfirst.cli ...` instead.
- Tested: the python-slugify PR #204 acceptance run below was done by hand on Windows (Python 3.13) and gave the same result as on Linux: `detected`, 10 detected / 7 not-detected / 0 inconclusive.
- Known limitation: `--timeout` does not work on Windows. The timeout handling uses `os.killpg` (and `signal.SIGKILL`), which do not exist on Windows, so when a side hits the timeout the call raises `AttributeError`. It is not caught by the `OSError` fallback, so it reaches the generic handler in `main`: failfirst prints `failfirst: internal error: AttributeError: ...` and exits with code 3, not `inconclusive` (exit 2). The timed-out test process is not killed and may keep running; failfirst still tries to remove the temporary worktrees, but files that process holds open may be left behind. This is based on reading the code, not on a Windows test run, and is documented only, not fixed.

## Usage

```
failfirst --repo PATH --after REV [--before REV] --test PATH [--test PATH ...]
          [--runner {auto,unittest,pytest}] [--show-all]
          [--json] [--keep] [--timeout SEC] [--python EXE]
```

- `--before` defaults to the first parent of `--after`.
- `--test` is relative to the repo root and may be repeated.
- `--runner` picks the test runner. The default, `auto`, reads each test file in the after revision: a file that mentions `unittest.TestCase` or `(TestCase)` and does not mention `import pytest` / `from pytest` uses unittest, anything else uses pytest. If the files disagree, pytest is used (it also runs unittest tests) and the report says so. The runner actually used is shown in the report and in the JSON (`runner`).
- With pytest, failfirst runs `pytest <test files> -p no:cacheprovider -q` inside each worktree and keeps the project's own pytest configuration (`addopts` etc.). Every test node id is one unit, so each `parametrize` case is judged on its own. Before running, it checks that `--python` can `import pytest`; if not it exits with 3 and tells you to install pytest or pass `--python`.
- The text report lists every detected, inconclusive and skipped unit, but only counts the not-detected ones; add `--show-all` to list them too. `--json` always contains all units.
- `--json` prints machine-readable output; `--keep` keeps the temporary worktrees; `--timeout` is per side (default 300 s).

Your working tree and branch are never touched: failfirst uses temporary detached `git worktree`s and removes them afterwards.

## Skipped tests

A test that is skipped is not a pass. A unit that is skipped on both sides gets its own status, `skipped` (with the skip reason), and:

- a skip on only one side is `inconclusive` (the two sides did not run the same thing);
- if some other unit is `detected` and nothing else is inconclusive, the result is `detected` and the skipped units are listed next to it, so an unrelated skip does not hide a real detection;
- if nothing is detected and some units were skipped on both sides, the result is `inconclusive`: `N test(s) were skipped on both sides; they may be the ones that exercise the fix`.

Why: in python-tabulate commit 032dc0d, the two tests added by the fix (`test_wrap_wide_char_no_column_overflow`, `test_wrap_wide_char_narrower_than_char_width`) need the optional package `wcwidth`, which was not installed, so they were skipped. The other 13 tests pass on both sides. Reporting `not-detected` would say "the fix has no test that notices it", and a pass would be false. The honest answer is that nothing ran that could tell:

```
failfirst verdict: inconclusive

runner: pytest (auto-selected)
...
Why this is inconclusive:
  - 5 test(s) were skipped on both sides; they may be the ones that exercise the fix
  - test/test_textwrapper.py::test_wrap_wide_char_multiword: skipped on both sides: test_wrap_wide_char is skipped
  - test/test_textwrapper.py::test_wrap_wide_char_longword: skipped on both sides: test_wrap_wide_char_longword is skipped
  - test/test_textwrapper.py::test_wrap_mixed_string: skipped on both sides: test_wrap_wide_char is skipped
  - test/test_textwrapper.py::test_wrap_wide_char_no_column_overflow: skipped on both sides: test_wrap_wide_char_no_column_overflow is skipped
  - test/test_textwrapper.py::test_wrap_wide_char_narrower_than_char_width: skipped on both sides: test_wrap_wide_char_narrower_than_char_width is skipped

Per test / subTest (18 units: 0 detected, 13 not-detected, 0 inconclusive, 5 skipped):
  ...
```

Install `wcwidth` into the Python you pass with `--python` and run again to get a real answer.

## Example: python-slugify PR #204

```
failfirst --repo slugify --after b4d515d --test tests/test_modern_word_boundary.py
```

```
failfirst verdict: detected

runner: unittest (auto-selected)
before: 866401ea6b346d7575cf941bc73ad32a362e34aa  (first parent of after)
after:  b4d515d48e87399cbe93b940322baf4a39babf7a

Test files copied from after into before:
  tests/test_modern_word_boundary.py: did not exist in before (added by the change)

Import check (where the code under test was loaded from):
  before: slugify -> /tmp/failfirst-_85wd_15/before/slugify/__init__.py (inside its worktree)
  after: slugify -> /tmp/failfirst-_85wd_15/after/slugify/__init__.py (inside its worktree)

Run summary:
  before: 2 test(s) run in 0.06s
  after: 2 test(s) run in 0.06s

Per test / subTest (17 units: 10 detected, 7 not-detected, 0 inconclusive, 0 skipped):
  [detected] tests.test_modern_word_boundary.ModernWordBoundaryTests.test_replacement_delimiters_are_budgeted_and_preserved (replacement='one--two--three', separator='-', limit=13, save_order=False)
      before: failure  after: pass
      evidence (before): AssertionError: 'one-two-three' != 'one--two'
  ... (9 more detected units) ...
  7 not-detected unit(s) not listed: they pass on both sides and do not tell before and after apart. Use --show-all to list them.
```

Exit code 0 (unittest runner, chosen by `auto`). 10 of the 12 subTests of `test_replacement_delimiters_are_budgeted_and_preserved` hit the bug (the two with an empty separator pass before the fix too), while `test_skipped_words_keep_the_existing_order_policy` passes on both sides and only protects existing behavior (it is one of the 7 not-detected units, listed with `--show-all`). The import check shows each side loaded `slugify` from its own worktree.

## Example: python-tabulate (pytest)

All three runs use `--runner auto`, which picks pytest here, and show the import check loading `tabulate` from each side's own worktree.

| Command | Result | Units |
| --- | --- | --- |
| `--after e6a24aa --test test/test_regression.py` | `detected`, exit 0 | 1 detected (`test_github_escape_pipe_character`), 36 not-detected, 0 inconclusive, 5 skipped |
| `--after 3aa568c --test test/test_output.py --test test/test_regression.py` | `detected`, exit 0 | 3 detected (`test_asciidoc`, `test_asciidoc_headerless`, `test_asciidoc_without_trailing_whitespace`), 211 not-detected, 0 inconclusive, 26 skipped |
| `--after 032dc0d --test test/test_textwrapper.py` | `inconclusive`, exit 2 | 0 detected, 13 not-detected, 0 inconclusive, 5 skipped (`wcwidth` not installed; see Skipped tests) |

The skips in the first two runs are tests that need optional packages (`wcwidth`, numpy, pandas) or are skipped unconditionally, and they are skipped identically on both sides, so they are listed but do not change the detection.

## Limitations

- Supported runners are unittest and pytest.
- pytest subtests (the `subtests` fixture, or `unittest` `subTest` run under pytest 9) are not judged one by one: a test with a failed subtest is recorded as an error, so the run is `inconclusive`.
- Projects that must be installed before they work (for example a pytest plugin that is loaded through entry points) do not run correctly on either side with failfirst today; the result is `inconclusive`. failfirst does not install dependencies for each revision.
- `conftest.py` is not copied: only the files given with `--test` are.
- Only the files given with `--test` are copied. If the fix PR also changed a helper or a `conftest.py`, the run may be `inconclusive`.
- Dependencies are not installed per revision; tests run in the current Python environment (`--python`).
- It only distinguishes assertion failure from other errors. It does not judge whether the assertion corresponds to the bug that was reported; that still needs a human.

## Prior art

SWE-bench also evaluates fixes with tests that fail before and pass after the change.

## License

MIT
