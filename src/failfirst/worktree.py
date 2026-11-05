"""Git operations: resolve revisions, manage temporary worktrees, copy test files."""

import os
import shutil
import subprocess


class UsageError(Exception):
    """The user asked for something that cannot be done (bad rev, missing file, ...)."""


def _git(repo, *args, check=True):
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and proc.returncode != 0:
        raise UsageError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def resolve_commit(repo, rev):
    """Resolve any revision to a full commit SHA."""
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False)
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not sha:
        raise UsageError(f"cannot resolve revision {rev!r} to a commit in {repo}")
    return sha


def first_parent(repo, sha):
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^1^{{commit}}", check=False)
    parent = proc.stdout.strip()
    if proc.returncode != 0 or not parent:
        raise UsageError(f"commit {sha} has no parent; pass --before explicitly")
    return parent


def add_worktree(repo, path, sha):
    _git(repo, "worktree", "add", "--detach", path, sha)


def remove_worktree(repo, path):
    """Best effort removal; never raises."""
    _git(repo, "worktree", "remove", "--force", path, check=False)
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)


def prune(repo):
    _git(repo, "worktree", "prune", check=False)


def normalize_test_path(path):
    norm = os.path.normpath(path).replace(os.sep, "/")
    if os.path.isabs(path) or norm.startswith("../") or norm == "..":
        raise UsageError(f"--test must be a path relative to the repo root: {path}")
    if not norm.endswith(".py"):
        raise UsageError(f"--test must be a .py file: {path}")
    return norm


def copy_test_file(after_dir, before_dir, rel):
    """Copy ``rel`` from the after checkout into the before checkout.

    Returns "added" if the file did not exist in before, else "overwritten".
    """
    src = os.path.join(after_dir, rel)
    if not os.path.isfile(src):
        raise UsageError(f"test file not found in the after revision: {rel}")
    dst = os.path.join(before_dir, rel)
    status = "overwritten" if os.path.exists(dst) else "added"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return status
