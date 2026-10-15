"""Stage 5: execute every resolved cell on every target Python, normal + strict runs.

Each (cell, Python) job gets a fresh env built from the cell's resolved lock, runs the
suite twice (normal, and with -W error::DeprecationWarning), and is compared test by
test with the reference baseline. Tests that newly fail are re-run on their own once;
if they then pass they are reported as flaky instead of failing.
"""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from . import envs, testrun
from .cells import Cell, slugify
from .runctx import Run

ORDER = ["pass", "warn", "flaky", "fail", "import-fail", "install-fail", "skipped"]


def _warn_map(run: testrun.TestRun) -> dict[tuple[str, str], dict[str, str]]:
    """Warnings keyed by (category, first line); the full message is kept as the value."""
    return {(w["category"], w.get("head", w["message"])): w for w in run.warnings}


def classify(normal: testrun.TestRun, strict: testrun.TestRun, ref_normal: testrun.TestRun,
             ref_strict: testrun.TestRun, confirmed_failing: list[str]) -> tuple[str, dict[str, Any]]:
    cur, ref = _warn_map(normal), _warn_map(ref_normal)
    new_warnings = [cur[k] for k in sorted(set(cur) - set(ref))]
    # Warnings can only be "resolved" if the suite actually ran.
    gone_warnings = [ref[k] for k in sorted(set(ref) - set(cur))] if normal.tests else []
    strict_diff = testrun.diff_tests(ref_strict, strict)
    strict_regressed = bool(strict_diff["newly_failing"]) or (
        ref_strict.outcome != "import-fail" and strict.outcome == "import-fail")
    strict_improved = bool(strict_diff["newly_passing"]) or (
        ref_strict.outcome == "import-fail" and strict.outcome not in ("import-fail", "error"))
    detail = {"new_warnings": [{"category": w["category"], "message": w["message"]} for w in new_warnings],
              "resolved_warnings": [{"category": w["category"], "message": w["message"]} for w in gone_warnings],
              "strict_regressed": strict_regressed, "strict_improved": strict_improved}
    if normal.outcome == "import-fail" and ref_normal.outcome != "import-fail":
        return "import-fail", detail
    if normal.outcome in ("timeout", "error") and ref_normal.outcome not in ("timeout", "error"):
        return "fail", detail
    if confirmed_failing:
        return "fail", detail
    if strict_regressed or new_warnings:
        return "warn", detail
    return "pass", detail


def run_job(cell: Cell, py: str, resolution: dict[str, Any] | None, reference: dict[str, Any], repo: Path,
            run: Run, *, timeout: float, keep_envs: bool, probe_set: dict[str, Any] | None = None) -> dict[str, Any]:
    rec: dict[str, Any] = {"cell": cell.id, "type": cell.type, "dependency": cell.dependency, "python": py,
                           "target_version": cell.target_version}
    if cell.skipped:
        return {**rec, "result": "skipped", "reason": cell.skipped}
    if not resolution or not resolution["ok"]:
        return {**rec, "result": "install-fail", "stage": "resolver",
                "reason": (resolution or {}).get("error", "not resolved")}
    rec["co_upgrades"] = resolution.get("co_upgrades", {})
    rec["lock_file"] = resolution["lock_file"]
    return evaluate(cell, py, resolution["lock_file"], reference, repo, run, rec=rec,
                    label=f"{slugify(cell.id)}-{py}", log_dir=run.logs / "matrix",
                    timeout=timeout, keep_envs=keep_envs, probe_set=probe_set)


def evaluate(cell: Cell, py: str, lock_file: str, reference: dict[str, Any], repo: Path, run: Run, *,
             rec: dict[str, Any], label: str, log_dir: Path, timeout: float, keep_envs: bool,
             probe_set: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build an env from ``lock_file``, run normal + strict tests in ``repo`` and compare with the reference.

    Used by the matrix (original code) and by the patch gate (patched copy of the code). When a probe set
    is given, the Stage 8 probes for the cell's packages also run in this env before it is removed.
    """
    slug = label
    env_dir = run.envs / f"cell-{slug}"
    try:
        python = envs.create_env(env_dir, py, log_dir)
        inst = envs.pip_install(python, ["-r", lock_file], cwd=repo, log_path=log_dir / f"{slug}-install.log")
        rec["install"] = inst.summary()
        if not inst.ok:
            return {**rec, "result": "install-fail", "stage": "install", "reason": inst.stderr[-1500:]}
        frozen = envs.freeze(python)
        rec["installed"] = {k: frozen.get(k) for k in [*cell.set_pins, *cell.remove, *cell.add_unpinned]}
        rec["installed_target"] = {k: frozen.get(k) for k in cell.set_pins}
        normal = testrun.run_tests(python, repo, log_dir, slug, timeout=timeout)
        strict = testrun.run_tests(python, repo, log_dir, slug, strict=True, timeout=timeout)
        ref_normal = testrun.TestRun(**reference["reference_tests"])
        ref_strict = testrun.TestRun(**reference["strict"])
        diff = testrun.diff_tests(ref_normal, normal)
        flaky: list[str] = []
        confirmed = diff["newly_failing"]
        if confirmed:
            rerun = testrun.run_tests(python, repo, log_dir, f"{slug}-rerun", timeout=timeout, select=confirmed)
            flaky = sorted(t for t in confirmed if rerun.tests.get(t) == "passed")
            confirmed = sorted(t for t in confirmed if t not in flaky)
        result, detail = classify(normal, strict, ref_normal, ref_strict, confirmed)
        if result == "pass" and flaky:
            result = "flaky"
        if probe_set and normal.outcome not in ("import-fail", "error", "timeout"):
            from . import probes

            pkgs = sorted({*cell.set_pins, *cell.remove, *cell.add_unpinned})
            rec["probes"] = probes.check(pkgs, probe_set, python, repo, py, log_dir, slug)
            if rec["probes"]["divergences"] and result in ("pass", "flaky"):
                result = "warn"  # tests pass, but observable behaviour changed
                detail["behaviour_changed"] = True
        rec.update({
            "result": result,
            "normal": _brief(normal), "strict": _brief(strict),
            "diff_vs_reference": diff, "confirmed_failing": confirmed, "flaky": flaky,
            "failures": {t: normal.failures.get(t) for t in confirmed},
            **detail,
        })
        return rec
    finally:
        if not keep_envs:
            shutil.rmtree(env_dir, ignore_errors=True)


def _brief(tr: testrun.TestRun) -> dict[str, Any]:
    return {"outcome": tr.outcome, "counts": tr.counts, "error_excerpt": tr.error_excerpt,
            "warnings": tr.warnings, "log": tr.command.get("log")}


def execute(cells: list[Cell], resolutions: dict[tuple[str, str], dict[str, Any]],
            references: dict[str, dict[str, Any]], repo: Path, run: Run, pythons: list[str], *,
            workers: int = 3, timeout: float = 300, keep_envs: bool = False,
            progress: Any = None, probe_set: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    jobs = [(c, py) for c in cells for py in pythons]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(run_job, c, py, resolutions.get((c.id, py)), references[py], repo, run,
                               timeout=timeout, keep_envs=keep_envs, probe_set=probe_set): (c, py)
                   for c, py in jobs}
        for fut in as_completed(futures):
            c, py = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:  # a crash in one job must not lose the others
                rec = {"cell": c.id, "type": c.type, "dependency": c.dependency, "python": py,
                       "result": "install-fail", "stage": "tool-error", "reason": f"{type(exc).__name__}: {exc}"}
            results.append(rec)
            if progress:
                progress(rec)
    order = {c.id: i for i, c in enumerate(cells)}
    results.sort(key=lambda r: (order.get(r["cell"], 999), r["python"]))
    return results


def grid(results: list[dict[str, Any]], pythons: list[str]) -> list[str]:
    """Plain-text grid: one row per cell, `normal/strict` per Python."""
    rows: dict[str, dict[str, dict[str, Any]]] = {}
    for r in results:
        rows.setdefault(r["cell"], {})[r["python"]] = r
    width = max((len(c) for c in rows), default=10)
    lines = [f"{'cell':<{width}}  " + "  ".join(f"{py:<34}" for py in pythons)]
    for cell, by_py in rows.items():
        cols = []
        for py in pythons:
            r = by_py.get(py, {})
            strict = (r.get("strict") or {}).get("outcome", "-")
            counts = (r.get("normal") or {}).get("counts") or {}
            pf = f"{counts.get('passed', '-')}p/{counts.get('failed', 0) + counts.get('error', 0)}f" if counts else ""
            cols.append(f"{r.get('result', '-'):<12} {pf:<7} strict:{strict:<11}"[:34])
        lines.append(f"{cell:<{width}}  " + "  ".join(f"{c:<34}" for c in cols))
    return lines


def worst(results: list[dict[str, Any]]) -> str:
    return max((r["result"] for r in results), key=lambda x: ORDER.index(x) if x in ORDER else 0, default="pass")
