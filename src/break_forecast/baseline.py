"""Stage 2: baseline + historical reconstruction. Results are `executed`.

Attempt 1 installs the pins exactly as written. If that does not install or its tests
do not all pass, attempt 2 reconstructs the era the pins came from: every *transitive*
dependency is limited to the release date of the pinned package that pulls it in (via
uv's --exclude-newer-package), so e.g. MarkupSafe resolves as it would have when
Jinja2 2.11.3 was current. The attempt with the most passing tests becomes the
reference baseline, and both attempts are recorded.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from packaging.utils import canonicalize_name

from . import envs, import_map, pypi, testrun
from .fetch import FetchError
from .provenance import executed, observed
from .runctx import Run, run_cmd

VIA_SINGLE = re.compile(r"^\s+# via (?P<parent>.+)$")
VIA_MULTI = re.compile(r"^\s+#\s{3}(?P<parent>\S.*)$")
PIN_LINE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>\S+)")


def install_sources(stage1: dict[str, Any]) -> list[str]:
    """Requirement sources the repo's own install commands use (requirements files and/or the project)."""
    man = stage1["manifests"]
    files = list(man["effective_files"])
    installs_project = any(c["installs_project"] for c in man["install_commands"])
    extras = sorted({e for c in man["install_commands"] for e in c.get("project_extras", [])})
    sources = [f for f in files if f.endswith(".txt")]
    if installs_project or not sources:
        for f in files:
            if f in ("pyproject.toml", "setup.cfg", "setup.py"):
                sources.append(f".[{','.join(extras)}]" if extras else ".")
                break
    return sources


def install_args(sources: list[str]) -> list[str]:
    args: list[str] = []
    for s in sources:
        args += ["-e", s] if s.startswith(".") else ["-r", s]
    return args


def _project_extras(source: str) -> list[str]:
    m = re.match(r"^\.\[(.*)\]$", source)
    return [e for e in m.group(1).split(",") if e] if m else []


def compile_annotated(repo: Path, sources: list[str], python_version: str, log_dir: Path,
                      extra_args: list[str] | None = None) -> tuple[dict[str, str], dict[str, list[str]], Any]:
    """Resolve with `uv pip compile` and return (pins, parents) from its `# via` annotations."""
    inputs = ["pyproject.toml" if s.startswith(".") else s for s in sources]
    extras = [a for s in sources for e in _project_extras(s) for a in ("--extra", e)]
    res = run_cmd([envs.uv_bin(), "pip", "compile", *inputs, *extras, "--python-version", python_version,
                   "--no-header", *(extra_args or [])],
                  cwd=repo, timeout=300, log_path=log_dir / f"compile-{python_version}.log", env=envs._uv_env())
    if not res.ok:
        return {}, {}, res
    pins, parents = parse_compile(res.stdout)
    return pins, parents, res


def parse_compile(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    pins: dict[str, str] = {}
    parents: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        m = PIN_LINE.match(line)
        if m:
            current = canonicalize_name(m.group("name"))
            pins[current] = m.group("version")
            parents[current] = []
            continue
        if current is None:
            continue
        single = VIA_SINGLE.match(line)
        multi = VIA_MULTI.match(line)
        parent = (single.group("parent") if single else multi.group("parent") if multi else None)
        if parent and parent.strip() not in ("", "via"):
            parent = parent.strip()
            parents[current].append(parent if parent.startswith("-r") or "(" in parent
                                    else canonicalize_name(parent))
    return pins, parents


def transitive_cutoffs(pins: dict[str, str], parents: dict[str, list[str]], direct: set[str],
                       pin_dates: dict[str, str | None]) -> dict[str, str]:
    """Cutoff date for each transitive package = latest cutoff among the packages that require it.

    A directly pinned package's cutoff is its release date + 1 day. An unpinned direct
    dependency has no cutoff, and neither does anything it pulls in.
    """
    memo: dict[str, str | None] = {}

    def cutoff(pkg: str, stack: frozenset[str]) -> str | None:
        if pkg in memo:
            return memo[pkg]
        if pkg in direct:
            memo[pkg] = pin_dates.get(pkg)
            return memo[pkg]
        dates: list[str] = []
        for parent in parents.get(pkg, []):
            if parent.startswith("-r") or "(" in parent:  # required directly by a requirements file
                memo[pkg] = None
                return None
            if parent in stack:
                continue
            d = cutoff(parent, stack | {pkg})
            if d is None:
                memo[pkg] = None
                return None
            dates.append(d)
        memo[pkg] = max(dates) if dates else None
        return memo[pkg]

    out: dict[str, str] = {}
    for pkg in pins:
        if pkg in direct:
            continue
        d = cutoff(pkg, frozenset())
        if d:
            out[pkg] = d
    return out


def pin_release_dates(pins: dict[str, str], offline: bool) -> dict[str, str | None]:
    dates: dict[str, str | None] = {}
    for name, version in pins.items():
        try:
            meta = pypi.project(name, offline=offline)
        except FetchError:
            meta = None
        released = (meta or {}).get("release_dates", {}).get(version)
        dates[name] = ((datetime.fromisoformat(released) + timedelta(days=1)).date().isoformat()
                       if released else None)
    return dates


def _attempt(name: str, repo: Path, run: Run, py_version: str, args: list[str], tests_need_pytest: bool,
             extra_install: list[str], timeout: float, details: dict[str, Any]) -> dict[str, Any]:
    env_dir = run.envs / f"baseline-{py_version}-{name}"
    py = envs.create_env(env_dir, py_version, run.logs)
    log_dir = run.logs / "baseline"
    inst = envs.pip_install(py, [*extra_install, *args], cwd=repo,
                            log_path=log_dir / f"{py_version}-{name}-install.log")
    record: dict[str, Any] = {"name": name, "python": py_version, "env": str(env_dir), "python_path": str(py),
                              "install": {**inst.summary(), "ok": inst.ok,
                                          "error_tail": inst.stderr[-1500:] if not inst.ok else None},
                              **details}
    if not inst.ok:
        record["outcome"] = "install-fail"
        return record
    # Check what is really installed rather than trusting the manifests: the repo's own install
    # command may not pull in the test runner (e.g. it lives in an extra that is not installed).
    has_pytest = run_cmd([str(py), "-c", "import pytest"], timeout=60).ok
    if tests_need_pytest or not has_pytest:
        if not has_pytest:
            envs.pip_install(py, ["pytest"], log_path=log_dir / f"{py_version}-{name}-pytest.log")
            record["tool_added"] = ["pytest"]
    tr = testrun.run_tests(py, repo, log_dir, f"{py_version}-{name}", timeout=timeout)
    record["tests"] = tr.to_dict()
    record["outcome"] = tr.outcome
    return record


def _passed(att: dict[str, Any]) -> int:
    return (att.get("tests") or {}).get("counts", {}).get("passed", -1)


def run_baseline(repo: Path, stage1: dict[str, Any], run: Run, py_versions: list[str], *,
                 offline: bool = False, timeout: float = 300) -> dict[str, Any]:
    sources = install_sources(stage1)
    args = install_args(sources)
    direct_pins = {d["name"]: d["pinned_version"] for d in stage1["dependencies"]
                   if d.get("pinned_version") and any(x["effective"] for x in d.get("declarations", []))}
    direct_names = {d["name"] for d in stage1["dependencies"]
                    if any(x["effective"] for x in d.get("declarations", []))}
    tests_need_pytest = "pytest" not in direct_names
    pin_dates = pin_release_dates(direct_pins, offline)

    per_python: dict[str, Any] = {}
    for py_version in py_versions:
        envs.check_target_python(py_version)
        attempts = [_attempt("as_written", repo, run, py_version, args, tests_need_pytest, [], timeout,
                             {"method": "install pins exactly as written"})]
        if attempts[0]["outcome"] != "pass":
            pins, parents, comp = compile_annotated(repo, sources, py_version, run.logs / "baseline")
            if pins:
                cutoffs = transitive_cutoffs(pins, parents, direct_names, pin_dates)
                extra = [a for pkg, d in sorted(cutoffs.items()) for a in ("--exclude-newer-package", f"{pkg}={d}")]
                attempts.append(_attempt(
                    "reconstructed", repo, run, py_version, args, tests_need_pytest, extra, timeout,
                    {"method": "transitive dependencies limited to the release date of the pinned package "
                               "that requires them (uv --exclude-newer-package)",
                     "cutoffs": cutoffs, "pin_release_dates": pin_dates,
                     "unconstrained_transitives": sorted(p for p in pins if p not in direct_names
                                                         and p not in cutoffs)}))
            else:
                attempts.append({"name": "reconstructed", "python": py_version, "outcome": "not-attempted",
                                 "reason": "pins could not be resolved, so dependency parents are unknown",
                                 "compile": comp.summary() if comp else None})
        installed = [a for a in attempts if a["outcome"] not in ("install-fail", "not-attempted")]
        ref = max(installed, key=_passed) if installed else None  # max() keeps the first on ties
        entry: dict[str, Any] = {"attempts": attempts, "pins_reproducible": attempts[0]["outcome"] == "pass",
                                 "reference": ref["name"] if ref else None}
        if ref:
            entry.update(_characterise_reference(repo, run, py_version, ref, timeout))
        per_python[py_version] = entry

    return {
        "install_sources": sources,
        "install_args": args,
        "test_command": "python -m pytest" + (" (pytest added by tool)" if tests_need_pytest else ""),
        "pins_reproducible": all(p["pins_reproducible"] for p in per_python.values()),
        "pythons": per_python,
    }


def _characterise_reference(repo: Path, run: Run, py_version: str, ref: dict[str, Any],
                            timeout: float) -> dict[str, Any]:
    py = ref["python_path"]
    log_dir = run.logs / "baseline"
    first = testrun.TestRun(**ref["tests"])
    second = testrun.run_tests(py, repo, log_dir, f"{py_version}-{ref['name']}-rerun", timeout=timeout)
    flaky = sorted(t for t in set(first.tests) | set(second.tests) if first.tests.get(t) != second.tests.get(t))
    strict = testrun.run_tests(py, repo, log_dir, f"{py_version}-{ref['name']}", strict=True, timeout=timeout)
    resolved = envs.freeze(py)
    lock = run.root / f"baseline-{py_version}.lock.txt"
    lock.write_text("".join(f"{k}=={v}\n" for k, v in sorted(resolved.items())), encoding="utf-8")
    try:
        imap = import_map.map_in_env(py, log_dir / f"{py_version}-import-map.log")
    except envs.EnvError:
        imap = {}
    return {"reference_tests": first.to_dict(), "flaky_tests": flaky, "strict": strict.to_dict(),
            "strict_vs_normal": testrun.diff_tests(first, strict), "resolved": resolved,
            "lock_file": str(lock), "reference_env": ref["env"], "import_map": imap}


def to_capsule(result: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (baseline record, limitation records)."""
    limitations: list[dict[str, Any]] = []
    for py, entry in result["pythons"].items():
        if not entry["pins_reproducible"]:
            first = entry["attempts"][0]
            why = first.get("install", {}).get("error_tail") or _fail_text(first)
            limitations.append(observed(source={"kind": "baseline", "python": py}, item=f"pins on Python {py}",
                                        reason=f"Pins as written are not reproducible: {why}"))
        if entry["reference"] is None:
            limitations.append(observed(source={"kind": "baseline", "python": py}, item=f"baseline on Python {py}",
                                        reason="No attempt installed; later stages have no reference"))
        elif entry["reference_tests"]["outcome"] != "pass":
            limitations.append(observed(
                source={"kind": "baseline", "python": py}, item=f"baseline on Python {py}",
                reason=f"Reference baseline still has failing tests: {sorted(entry['reference_tests']['failures'])}"))
        if entry.get("flaky_tests"):
            limitations.append(observed(source={"kind": "baseline", "python": py}, item="flaky tests",
                                        reason=f"Outcome changed between two identical runs: {entry['flaky_tests']}"))
    return executed(source={"kind": "uv + pytest", "commands": result["install_args"]}, **result), limitations


def _fail_text(att: dict[str, Any]) -> str:
    tests = att.get("tests") or {}
    fails = tests.get("failures") or {}
    if fails:
        return "; ".join(f"{t}: {m}" for t, m in list(fails.items())[:3])
    return att.get("outcome", "unknown")
