"""Stage 4: plan cells and resolve each one with uv before anything is executed.

A cell is one dependency change, tested on its own against the reference baseline:

* upgrade  - bump X to a newer release
* replace  - drop a frozen X for Y (Y proposed by the model, verified to exist)
* remove   - drop an unused X
* declare  - add an imported-but-undeclared X explicitly

Each cell starts from the reference lock (every package pinned as in the baseline).
If the resolver rejects that, packages are freed step by step - first X's own
dependency subtree, then all transitive packages, then the direct pins the resolver
names - and whatever moved is recorded as the cell's co-upgrade group.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from . import envs, pypi
from .fetch import FetchError
from .runctx import Run, run_cmd

GRAPH_SCRIPT = r"""
import json, re, importlib.metadata as m
g = {}
for d in m.distributions():
    name = re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower()
    reqs = []
    for r in d.requires or []:
        if "extra ==" in r.replace(" ", " "):
            continue
        mm = re.match(r"\s*([A-Za-z0-9._-]+)", r)
        if mm:
            reqs.append(re.sub(r"[-_.]+", "-", mm.group(1)).lower())
    g[name] = sorted(set(reqs))
print(json.dumps(g))
"""
SEED_PACKAGES = {"pip"}


@dataclass
class Cell:
    id: str
    type: str                     # upgrade | replace | remove | declare | all-latest
    dependency: str
    reason: str
    set_pins: dict[str, str] = field(default_factory=dict)   # package -> exact version to require
    remove: list[str] = field(default_factory=list)
    add_unpinned: list[str] = field(default_factory=list)    # added at latest compatible
    from_version: str | None = None
    target_version: str | None = None
    expects_code_change: bool = False
    replacement_ref: str | None = None
    skipped: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _newer(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        return Version(a) > Version(b)
    except InvalidVersion:
        return False


def latest_same_major(name: str, current: str, offline: bool) -> str | None:
    meta = pypi.project(name, offline=offline) or {}
    try:
        major = Version(current).major
    except InvalidVersion:
        return None
    candidates = [v for v in meta.get("final_releases", []) if Version(v).major == major and _newer(v, current)]
    return max(candidates, key=Version) if candidates else None


def plan(stage1: dict[str, Any], reference_lock: dict[str, str], *, targets: str = "latest",
         offline: bool = False, only: list[str] | None = None,
         replacements: dict[str, dict[str, Any]] | None = None) -> list[Cell]:
    """Build the cell list from Stage 1 classifications and the reference lock."""
    cells: list[Cell] = []
    replacements = replacements or {}
    upgradable_all: dict[str, str] = {}
    for dep in stage1["dependencies"]:
        name = dep["name"]
        if only and name not in only:
            continue
        labels = dep["classification"]
        current = reference_lock.get(name)
        declared = any(d["effective"] for d in dep.get("declarations", []))
        if "undeclared" in labels:
            version = current or dep.get("latest")
            cells.append(Cell(f"declare:{name}", "declare", name,
                              f"imported as {', '.join(dep['import_names'])} but not declared",
                              set_pins={name: version} if version else {},
                              add_unpinned=[] if version else [name], target_version=version))
            continue
        if not declared:
            continue
        if "unused" in labels:
            cells.append(Cell(f"remove:{name}", "remove", name, "declared but never imported",
                              remove=[name], from_version=current))
            continue
        if "frozen" in labels:
            rep = replacements.get(name)
            cell = Cell(f"replace:{name}", "replace", name, "frozen: " + _frozen_reason(stage1, name),
                        remove=[name], from_version=current, expects_code_change=True)
            if not rep:
                cell.skipped = "no replacement target (run without --no-ai to have the model propose one)"
            elif not rep.get("verified"):
                ver = rep.get("verification")
                detail = ver.get("detail") if isinstance(ver, dict) else ver
                cell.replacement_ref = rep.get("id")
                cell.skipped = f"proposed replacement {rep.get('replacement')!r} failed verification: {detail}"
            else:
                cell.replacement_ref = rep["id"]
                cell.target_version = rep["replacement"]
                if rep["kind"] == "pypi" and canonicalize_name(rep["replacement"]) not in reference_lock:
                    cell.add_unpinned = [canonicalize_name(rep["replacement"])]
                cell.expects_code_change = rep["kind"] != "pypi" or rep.get("api_compatible") is not True
            cells.append(cell)
            continue
        latest = dep.get("latest")
        if current and _newer(latest, current):
            wanted = []
            if targets in ("latest", "both"):
                wanted.append(latest)
            if targets in ("same-major", "both"):
                same = latest_same_major(name, current, offline)
                if same and same not in wanted:
                    wanted.append(same)
            for target in wanted:
                cells.append(Cell(f"upgrade:{name}@{target}", "upgrade", name, f"{current} -> {target}",
                                  set_pins={name: target}, from_version=current, target_version=target))
            upgradable_all[name] = latest
    if len(upgradable_all) > 1:
        cells.append(Cell("all-latest", "all-latest", "*", "every upgradable dependency at its latest release",
                          set_pins=dict(upgradable_all)))
    return cells


def exit_plan_cell(cells: list[dict[str, Any]], matrix: list[dict[str, Any]], verified: set[str]) -> Cell | None:
    """One cell holding the whole migration: every upgrade at latest, every replacement whose patch was
    verified, and every removal/declaration that passed on all Pythons. Its patch is all verified patches."""
    results: dict[str, set[str]] = {}
    for r in matrix:
        results.setdefault(r["cell"], set()).add(r["result"])
    combo = Cell("exit-plan", "combined", "*", "")
    parts = []
    for c in cells:
        ok_everywhere = results.get(c["id"], set()) <= {"pass", "warn", "flaky"} and results.get(c["id"])
        if c["type"] == "all-latest":
            combo.set_pins.update(c["set_pins"])
            parts.append("all upgrades at latest")
        elif c["type"] == "replace" and c["id"] in verified:
            combo.remove += c["remove"]
            combo.add_unpinned += c["add_unpinned"]
            parts.append(c["id"])
        elif c["type"] in ("remove", "declare") and ok_everywhere:
            combo.remove += c["remove"]
            combo.set_pins.update(c["set_pins"])
            parts.append(c["id"])
        elif c["type"] == "upgrade" and c["id"] in verified and not any(x["type"] == "all-latest" for x in cells):
            combo.set_pins.update(c["set_pins"])
            parts.append(c["id"])
    if not parts:
        return None
    combo.reason = " + ".join(parts)
    return combo


def _frozen_reason(stage1: dict[str, Any], name: str) -> str:
    for r in stage1["risk_indicators"]:
        if r["dependency"] == name and r["indicator"] == "frozen":
            return r["detail"]
    return "frozen"


def dependency_graph(python: str | Path, log_path: Path | None = None) -> dict[str, list[str]]:
    res = run_cmd([str(python), "-c", GRAPH_SCRIPT], timeout=60, log_path=log_path)
    if not res.ok:
        return {}
    return json.loads(res.stdout.strip().splitlines()[-1])


def subtree(root: str, graph: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(graph.get(root, []))
    while stack:
        pkg = stack.pop()
        if pkg in seen or pkg == root:
            continue
        seen.add(pkg)
        stack.extend(graph.get(pkg, []))
    return seen


def _write_in(path: Path, pins: dict[str, str], unpinned: list[str]) -> None:
    lines = [f"{k}=={v}" for k, v in sorted(pins.items())] + sorted(unpinned)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_lock(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9._-]+)==(\S+)", line)
        if m:
            out[canonicalize_name(m.group(1))] = m.group(2)
    return out


def resolve(cell: Cell, py: str, reference_lock: dict[str, str], direct: set[str],
            graph: dict[str, list[str]], out_dir: Path) -> dict[str, Any]:
    """Resolve a cell for one Python version, freeing packages only as far as the resolver needs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(cell.id)
    base = {k: v for k, v in reference_lock.items() if k not in SEED_PACKAGES}
    changed = set(cell.set_pins) | set(cell.remove)
    roots = changed | set(cell.add_unpinned)
    target_tree = set().union(*(subtree(r, graph) for r in roots)) if roots else set()
    transitive = {k for k in base if k not in direct}
    # A removed package's own dependencies are never pinned: the resolver keeps them only if
    # something else still needs them.
    removed_tree = set().union(*(subtree(r, graph) for r in cell.remove)) if cell.remove else set()
    levels: list[tuple[str, set[str]]] = [
        ("locked", removed_tree),
        ("freed changed package's dependencies", target_tree),
        ("freed all transitive packages", target_tree | transitive),
    ]
    attempts: list[dict[str, Any]] = []
    freed_direct: set[str] = set()
    for level_no in range(len(levels) + 1):
        if level_no < len(levels):
            label, freed = levels[level_no]
        else:
            named = _direct_names_in_error(attempts[-1]["error"], direct - changed)
            if not named:
                break
            freed_direct = named
            label, freed = f"freed direct pins named by resolver: {', '.join(sorted(named))}", \
                target_tree | transitive | named
        pins = {k: v for k, v in base.items() if k not in changed and k not in freed}
        pins.update(cell.set_pins)
        # Freed transitive packages are simply left out (pulled in again only if still required);
        # freed direct dependencies stay as unpinned requirements.
        unpinned = sorted(((freed & direct) - changed) | set(cell.add_unpinned))
        req_in = out_dir / f"{slug}-{py}-L{level_no}.in"
        _write_in(req_in, pins, unpinned)
        res = run_cmd([envs.uv_bin(), "pip", "compile", str(req_in), "--python-version", py, "--no-header",
                       "--no-annotate"], timeout=300, log_path=out_dir / f"{slug}-{py}-L{level_no}.log",
                      env=envs._uv_env())
        attempt = {"level": label, "input": str(req_in), "ok": res.ok,
                   "error": None if res.ok else _resolver_error(res.stderr)}
        attempts.append(attempt)
        if res.ok:
            lock = parse_lock(res.stdout)
            lock_path = out_dir / f"{slug}-{py}.lock.txt"
            lock_path.write_text(res.stdout, encoding="utf-8")
            return {"ok": True, "lock_file": str(lock_path), "resolved": lock, "attempts": attempts,
                    "co_upgrades": co_upgrades(reference_lock, lock, cell),
                    "freed_direct_pins": sorted(freed_direct)}
    return {"ok": False, "attempts": attempts, "error": attempts[-1]["error"] if attempts else "not attempted"}


def co_upgrades(reference: dict[str, str], resolved: dict[str, str], cell: Cell) -> dict[str, list[str | None]]:
    """Packages that moved besides the ones the cell changes on purpose."""
    intended = set(cell.set_pins) | set(cell.remove) | set(cell.add_unpinned)
    moved: dict[str, list[str | None]] = {}
    for pkg in sorted((set(reference) | set(resolved)) - SEED_PACKAGES):
        if pkg in intended:
            continue
        before, after = reference.get(pkg), resolved.get(pkg)
        if before != after:
            moved[pkg] = [before, after]
    return moved


def _resolver_error(stderr: str) -> str:
    lines = [ln for ln in stderr.splitlines() if ln.strip() and not ln.startswith("Using ")]
    return "\n".join(lines)[-1500:]


def _direct_names_in_error(error: str | None, candidates: set[str]) -> set[str]:
    if not error:
        return set()
    text = error.lower()
    return {c for c in candidates if re.search(rf"\b{re.escape(c)}\b", text)}


def slugify(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", text).strip("-")


def is_stdlib(module: str) -> bool:
    return module.split(".")[0] in sys.stdlib_module_names


def verify_replacement(rep: dict[str, Any], offline: bool) -> tuple[bool, str]:
    """Hard Rule 2: a model-proposed package name is only used once PyPI (or the stdlib) confirms it."""
    name = rep.get("replacement") or ""
    if rep.get("kind") == "stdlib":
        return (is_stdlib(name), "stdlib module exists in Python 3.12" if is_stdlib(name)
                else f"{name!r} is not a standard-library module")
    try:
        meta = pypi.project(name, offline=offline)
    except FetchError as exc:
        return False, f"PyPI lookup failed: {exc}"
    if not meta:
        return False, f"{name!r} does not exist on PyPI"
    return True, f"exists on PyPI (latest {meta['latest']}, {meta['source']['url']})"


def plan_run_dir(run: Run) -> Path:
    path = run.root / "cells"
    path.mkdir(parents=True, exist_ok=True)
    return path
