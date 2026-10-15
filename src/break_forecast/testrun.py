"""Run the target's test suite in an env and parse per-test results from JUnit XML."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .runctx import CmdResult, clean_env, run_cmd

WARNING_LINE = re.compile(r"^\s*(?P<loc>\S+?):(?P<line>\d+): (?P<cat>\w*Warning): (?P<msg>.+)$", re.MULTILINE)


@dataclass
class TestRun:
    mode: str                         # normal | strict
    outcome: str                      # pass | fail | import-fail | no-tests | timeout | error
    tests: dict[str, str] = field(default_factory=dict)   # nodeid -> passed|failed|error|skipped
    counts: dict[str, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)  # nodeid -> short message
    warnings: list[dict[str, str]] = field(default_factory=list)
    command: dict[str, Any] = field(default_factory=dict)
    error_excerpt: str | None = None  # why the suite could not run (e.g. conftest import error)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def passed(self) -> set[str]:
        return {t for t, o in self.tests.items() if o == "passed"}


def pytest_cmd(python: str | Path, junit: Path, strict: bool) -> list[str]:
    cmd = [str(python), "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rfE",
           f"--junitxml={junit}", "-o", "junit_family=xunit1"]
    if strict:
        cmd += ["-W", "error::DeprecationWarning"]
    return cmd


def run_tests(python: str | Path, repo: Path, out_dir: Path, label: str, *, strict: bool = False,
              timeout: float = 300, select: list[str] | None = None) -> TestRun:
    mode = "strict" if strict else "normal"
    junit = out_dir / f"{label}-{mode}.junit.xml"
    junit.unlink(missing_ok=True)
    res = run_cmd(pytest_cmd(python, junit, strict) + list(select or []), cwd=repo, timeout=timeout,
                  log_path=out_dir / f"{label}-{mode}.log", env=clean_env({"PYTHONPATH": str(repo)}))
    return parse_result(res, junit, mode)


def parse_result(res: CmdResult, junit: Path, mode: str) -> TestRun:
    run = TestRun(mode=mode, outcome="error", command=res.summary())
    if junit.exists():
        _parse_junit(junit, run)
    run.warnings = parse_warnings(res.stdout)
    run.counts = {k: sum(1 for o in run.tests.values() if o == k) for k in ("passed", "failed", "error", "skipped")}
    output = f"{res.stdout}\n{res.stderr}"
    if res.timed_out:
        run.outcome = "timeout"
    elif not run.tests and res.returncode == 5:
        run.outcome = "no-tests"
    elif not run.tests:
        run.error_excerpt = error_excerpt(output)
        loading = re.search(r"(ImportError while loading conftest|Error while loading conftest|"
                            r"ModuleNotFoundError|ImportError)", output)
        run.outcome = "import-fail" if loading else "error"
    elif run.counts["error"] and all(o in ("error", "skipped") for o in run.tests.values()):
        run.outcome = "import-fail"
    elif _is_collection_error(run):
        run.outcome = "import-fail"
    elif run.counts["failed"] or run.counts["error"]:
        run.outcome = "fail"
    else:
        run.outcome = "pass"
    return run


def error_excerpt(output: str) -> str | None:
    """The first line explaining why pytest could not run, plus the final `E ` error line."""
    lines = output.splitlines()
    head = next((ln.strip() for ln in lines if "while loading conftest" in ln or ln.startswith("ERROR")), None)
    err = next((ln.strip()[1:].strip() for ln in reversed(lines) if ln.startswith("E ")), None)
    parts = [p for p in (head, err) if p]
    return " | ".join(parts)[:500] if parts else (lines[-1][:500] if lines else None)


def _is_collection_error(run: TestRun) -> bool:
    return any("collection failure" in msg.lower() or "importerror" in msg.lower()
               for nid, msg in run.failures.items() if run.tests.get(nid) == "error")


def _parse_junit(path: Path, run: TestRun) -> None:
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        cls, name = case.get("classname", ""), case.get("name", "")
        file = case.get("file")
        nodeid = _nodeid(cls, name, file)
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            el = case.find(tag)
            if el is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                message = (el.get("message") or (el.text or "")).strip().splitlines()[0:1]
                message = message[0][:300] if message else ""
                break
        run.tests[nodeid] = outcome
        if outcome in ("failed", "error"):
            run.failures[nodeid] = message


def _nodeid(classname: str, name: str, file: str | None) -> str:
    if file:
        return f"{file.replace(chr(92), '/')}::{name}"  # same node ids on Windows and Linux
    if not classname:
        return name
    parts = classname.split(".")
    # tests.test_mod or tests.test_mod.TestClass -> tests/test_mod.py[::TestClass]::name
    for i in range(len(parts), 0, -1):
        if parts[i - 1].startswith("test") or i == len(parts):
            mod = "/".join(parts[:i]) + ".py"
            rest = parts[i:]
            return "::".join([mod, *rest, name])
    return f"{classname}::{name}"


def parse_warnings(stdout: str) -> list[dict[str, str]]:
    if "warnings summary" not in stdout:
        return []
    section = stdout.split("warnings summary", 1)[1].split("-- Docs:", 1)[0]
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for m in WARNING_LINE.finditer(section):
        # Multi-line warnings continue on following lines at the same indent; pytest then prints
        # the triggering source line indented deeper, which ends the message.
        indent = len(m.group(0)) - len(m.group(0).lstrip())
        extra = []
        for line in section[m.end():].splitlines()[1:8]:
            if not line.strip() or len(line) - len(line.lstrip()) != indent or WARNING_LINE.match(line):
                break
            extra.append(line.strip())
        head = m.group("msg").strip()
        key = (m.group("cat"), head)
        seen.setdefault(key, {"category": key[0], "message": " ".join([head, *extra])[:600], "head": head,
                              "location": f"{Path(m.group('loc')).name}:{m.group('line')}"})
    return list(seen.values())


def norm_id(nodeid: str) -> str:
    """Node ids compare equal across OS path separators (and across runs recorded either way)."""
    return nodeid.replace("\\", "/")


def diff_tests(reference: TestRun, other: TestRun) -> dict[str, list[str]]:
    """Per-test changes from reference to other."""
    ref = {norm_id(k): v for k, v in reference.tests.items()}
    new = {norm_id(k): v for k, v in other.tests.items()}
    return {
        "newly_failing": sorted(t for t in ref if ref[t] == "passed" and new.get(t) in ("failed", "error")),
        "newly_passing": sorted(t for t in new if new[t] == "passed" and ref.get(t) in ("failed", "error")),
        "missing": sorted(t for t in ref if t not in new),
        "added": sorted(t for t in new if t not in ref),
    }
