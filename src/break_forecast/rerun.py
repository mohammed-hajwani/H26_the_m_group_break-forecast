"""`bf verify-capsule --rerun`: re-execute a capsule's verified results from the capsule alone.

The capsule stores the target (URL + commit), the exact resolved package set of the baseline and of
every cell, and the search/replace edits of every patch. This module rebuilds those environments,
applies the edits to a fresh copy of the target and checks the tests reproduce what was recorded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import envs, testrun
from .cells import slugify
from .patch_gate import apply_edits, materialise
from .runctx import Run, run_cmd


def fetch_target(target: dict[str, Any], run: Run) -> Path:
    dest = run.work / "target"
    if target.get("kind") == "git":
        res = run_cmd(["git", "clone", "--depth", "1", target["url"], str(dest)], timeout=300,
                      log_path=run.logs / "clone.log")
        if not res.ok:
            raise RuntimeError(f"git clone failed: {res.stderr[-400:]}")
        commit = target.get("commit")
        head = run_cmd(["git", "rev-parse", "HEAD"], cwd=dest, timeout=30).stdout.strip()
        if commit and head != commit:
            run_cmd(["git", "fetch", "--depth", "1", "origin", commit], cwd=dest, timeout=300)
            co = run_cmd(["git", "checkout", "--quiet", commit], cwd=dest, timeout=60)
            if not co.ok:
                raise RuntimeError(f"recorded commit {commit} is not available: {co.stderr[-300:]}")
        return dest
    import shutil

    shutil.copytree(target["path"], dest, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
    return dest


def _lock_file(pins: dict[str, str], path: Path) -> Path:
    path.write_text("".join(f"{k}=={v}\n" for k, v in sorted(pins.items()) if k != "pip"), encoding="utf-8")
    return path


def _run_tests(pins: dict[str, str], repo: Path, py: str, run: Run, label: str, timeout: float) -> testrun.TestRun:
    env_dir = run.envs / label
    python = envs.create_env(env_dir, py, run.logs)
    lock = _lock_file(pins, run.root / f"{label}.lock.txt")
    inst = envs.pip_install(python, ["-r", str(lock)], log_path=run.logs / f"{label}-install.log")
    if not inst.ok:
        return testrun.TestRun("normal", "install-fail", error_excerpt=inst.stderr[-400:])
    return testrun.run_tests(python, repo, run.logs, label, timeout=timeout)


def _same(recorded: dict[str, Any] | None, now: testrun.TestRun) -> tuple[bool, str]:
    rec_counts = (recorded or {}).get("counts") or {}
    ok = (recorded or {}).get("outcome") == now.outcome and rec_counts.get("passed") == now.counts.get("passed")
    return ok, f"recorded {(recorded or {}).get('outcome')} {rec_counts.get('passed')} passed; " \
               f"now {now.outcome} {now.counts.get('passed')} passed"


def rerun(capsule: dict[str, Any], run: Run, *, timeout: float = 300, progress: Any = print) -> dict[str, Any]:
    repo = fetch_target(capsule["meta"]["target"], run)
    pythons = capsule["meta"]["target_pythons"]
    checks: list[dict[str, Any]] = []

    # 1. Reference baseline from its recorded resolved set.
    for py, entry in ((capsule.get("baseline") or {}).get("pythons") or {}).items():
        if not entry.get("resolved"):
            continue
        now = _run_tests(entry["resolved"], repo, py, run, f"rerun-baseline-{py}", timeout)
        ok, detail = _same(entry.get("reference_tests"), now)
        checks.append({"item": "reference baseline", "python": py, "reproduced": ok, "detail": detail})
        progress(f"    baseline {py}: {'reproduced' if ok else 'DIFFERENT'} ({detail})")

    # 2. Every selected (best) patch whose gate passed, plus the combined exit plan.
    cells = {c["id"]: c for c in capsule.get("cells") or []}
    patches = [p for p in capsule.get("patches") or [] if p.get("selected")
               and p.get("status") in ("verified", "verified_tests")]
    by_cell = {p["cell"]: p for p in patches if not p.get("combined_from")}
    for p in patches:
        if p.get("combined_from"):
            edit_sets = [by_cell[c]["edits"] for c in p["combined_from"] if c in by_cell]
            resolution = p.get("resolution") or {}
            pins_for = {py: (resolution.get(f"{p['cell']}|{py}") or {}).get("resolved") for py in pythons}
        else:
            edit_sets = [p.get("edits") or []]
            res = (cells.get(p["cell"]) or {}).get("resolution") or {}
            pins_for = {py: (res.get(py) or {}).get("resolved") for py in pythons}
        contents: dict[str, str] = {}
        staging = materialise(repo, {}, run.work / f"stage-{slugify(p['cell'])}")
        problems = []
        for edits in edit_sets:
            _, changed, rules = apply_edits(staging, edits)
            problems += [f"{n}: {r['detail']}" for n, r in rules.items() if not r["ok"]]
            contents.update(changed)
            for rel, text in changed.items():
                (staging / rel).write_text(text, encoding="utf-8")
        patched = materialise(repo, contents, run.work / f"patched-{slugify(p['cell'])}")
        for py in pythons:
            recorded = ((p.get("gate") or {}).get("executed") or {}).get(py) or {}
            if problems or not pins_for.get(py):
                why = "; ".join(problems) or "no resolved package set recorded"
                checks.append({"item": f"patch {p['cell']}", "python": py, "reproduced": False, "detail": why})
                progress(f"    patch {p['cell']} {py}: CANNOT RERUN ({why[:120]})")
                continue
            now = _run_tests(pins_for[py], patched, py, run, f"rerun-{slugify(p['cell'])}-{py}", timeout)
            ok, detail = _same(recorded.get("normal"), now)
            checks.append({"item": f"patch {p['cell']}", "python": py, "reproduced": ok, "detail": detail})
            progress(f"    patch {p['cell']} {py}: {'reproduced' if ok else 'DIFFERENT'} ({detail})")
    return {"target": capsule["meta"]["target"], "checks": checks,
            "all_reproduced": bool(checks) and all(c["reproduced"] for c in checks)}
