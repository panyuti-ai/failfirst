# failfirst

Run the test a fix commit ships against the code *before* the fix, and report whether it actually fails there.

## Why

When a human or an AI agent hands you "bug fixed, new test is green", the missing question is whether that test ever touched the bug. A test that also passes on the unfixed code proves nothing about the fix. failfirst copies the test file from the fixed revision into the unfixed one, runs both sides in separate processes, and compares the results test by test (and subTest by subTest).

## Results

| Result | Meaning | Exit code |
| --- | --- | --- |
| `detected` | Before the fix the test fails with an assertion failure (`AssertionError` / `failureException`); after the fix it passes. | 0 |
| `not-detected` | Passes on both sides. It does not touch the bug. | 1 |
| `inconclusive` | Anything else: a non-assertion error before the fix (`TypeError`, `AttributeError`, `ImportError`, ...), tests not found or not loadable, no test ran, the fixed side does not pass, timeout, a skip, or the code under test was loaded from outside that revision's checkout. | 2 |

Usage or tool errors exit with 3. The guiding rule: when in doubt, say `inconclusive` rather than report false evidence as `detected`.

## Install

```
pip install -e .
```

Python 3.10+, standard library only.

### Windows

- If you have no Python yet (typing `python --version` in PowerShell only shows a Microsoft Store prompt), run `winget install Python.Python.3.13`, then close and reopen PowerShell.
- Install with `py -m pip install -e .`
- If the `failfirst` command is not found, run it as `py -m failfirst.cli ...` instead.
- Tested: the python-slugify PR #204 acceptance run below was done by hand on Windows (Python 3.13) and gave the same result as on Linux: `detected`, 10 detected / 7 not-detected / 0 inconclusive.
- Known limitation: `--timeout` does not work on Windows. The timeout handling uses `os.killpg` (and `signal.SIGKILL`), which do not exist on Windows, so when a side hits the timeout the call raises `AttributeError`. It is not caught by the `OSError` fallback, so it reaches the generic handler in `main`: failfirst prints `failfirst: internal error: AttributeError: ...` and exits with code 3, not `inconclusive` (exit 2). The temporary worktrees are still removed, but the timed-out test process is not killed and may keep running. This is documented only, not fixed.

## Usage

```
failfirst --repo PATH --after REV [--before REV] --test PATH [--test PATH ...]
          [--json] [--keep] [--timeout SEC] [--python EXE]
```

- `--before` defaults to the first parent of `--after`.
- `--test` is relative to the repo root and may be repeated.
- `--json` prints machine-readable output; `--keep` keeps the temporary worktrees; `--timeout` is per side (default 300 s).

Your working tree and branch are never touched: failfirst uses temporary detached `git worktree`s and removes them afterwards.

## Example: python-slugify PR #204

```
failfirst --repo slugify --after b4d515d --test tests/test_modern_word_boundary.py
```

```
failfirst verdict: detected

before: 866401ea6b346d7575cf941bc73ad32a362e34aa  (first parent of after)
after:  b4d515d48e87399cbe93b940322baf4a39babf7a

Test files copied from after into before:
  tests/test_modern_word_boundary.py: did not exist in before (added by the change)

Import check (where the code under test was loaded from):
  before: slugify -> /tmp/failfirst-14339ogw/before/slugify/__init__.py (inside its worktree)
  after: slugify -> /tmp/failfirst-14339ogw/after/slugify/__init__.py (inside its worktree)

Run summary:
  before: 2 test(s) run in 0.09s
  after: 2 test(s) run in 0.06s

Per test / subTest (17 units: 10 detected, 7 not-detected, 0 inconclusive):
  [detected] …ModernWordBoundaryTests.test_replacement_delimiters_are_budgeted_and_preserved (replacement='one--two--three', separator='-', limit=13, save_order=False)
      before: failure  after: pass
      evidence (before): AssertionError: 'one-two-three' != 'one--two'
  [detected] …ModernWordBoundaryTests.test_replacement_delimiters_are_budgeted_and_preserved (replacement='one--two--three', separator='-', limit=13, save_order=True)
      before: failure  after: pass
      evidence (before): AssertionError: 'one-two-three' != 'one--two'
  ... (8 more detected units) ...
  [not-detected] …ModernWordBoundaryTests.test_replacement_delimiters_are_budgeted_and_preserved (replacement='one--two--three', separator='', limit=8, save_order=False)
      before: pass  after: pass
      passes on both sides: this unit does not tell before and after apart
  [not-detected] …ModernWordBoundaryTests.test_replacement_delimiters_are_budgeted_and_preserved (replacement='one--two--three', separator='', limit=8, save_order=True)
      before: pass  after: pass
      passes on both sides: this unit does not tell before and after apart
  ... (remaining not-detected units) ...
```

Exit code 0. 10 of the 12 subTests of `test_replacement_delimiters_are_budgeted_and_preserved` hit the bug (the two with an empty separator pass before the fix too), while `test_skipped_words_keep_the_existing_order_policy` passes on both sides and only protects existing behavior. The import check shows each side loaded `slugify` from its own worktree.

## Limitations

- unittest only in v1.
- Only the files given with `--test` are copied. If the fix PR also changed a helper or `conftest.py`, the run may be `inconclusive`.
- Dependencies are not installed per revision; tests run in the current Python environment (`--python`).
- It only distinguishes assertion failure from other errors. It does not judge whether the assertion corresponds to the bug that was reported; that still needs a human.

## Prior art

SWE-bench also evaluates fixes with tests that fail before and pass after the change.

## License

MIT
