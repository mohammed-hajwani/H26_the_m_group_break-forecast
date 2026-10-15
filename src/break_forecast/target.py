"""Get a private working copy of the target repo. The original is never modified."""

from __future__ import annotations

import shutil
from pathlib import Path

from .manifests import SKIP_DIRS
from .runctx import Run, run_cmd


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "git@")) or value.endswith(".git")


def acquire(repo: str, run: Run) -> tuple[Path, dict]:
    dest = run.work / "target"
    if is_url(repo):
        res = run_cmd(["git", "clone", "--depth", "1", repo, str(dest)], timeout=300,
                      log_path=run.logs / "clone.log")
        if not res.ok:
            raise RuntimeError(f"git clone failed: {res.stderr[-500:]}")
        head = run_cmd(["git", "rev-parse", "HEAD"], cwd=dest, timeout=30).stdout.strip()
        return dest, {"kind": "git", "url": repo, "commit": head}
    src = Path(repo).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"{repo} is not a directory or git URL")
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*SKIP_DIRS, "runs", ".bf-cache"))
    head = run_cmd(["git", "rev-parse", "HEAD"], cwd=src, timeout=30)
    return dest, {"kind": "local", "path": str(src), "commit": head.stdout.strip() if head.ok else None}
