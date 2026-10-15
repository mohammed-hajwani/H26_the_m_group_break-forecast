"""Stage 8: differential probes - catch behaviour changes the test suite does not cover.

* Generic probe (written by the tool): imports every module of the target's own packages and
  captures each console entry point's `--help` output.
* AI probes (written by the model, one per changed dependency used in source): call the repo's
  own functions at that dependency's usage sites with varied inputs and print JSON.

Each probe runs in the reference env on the original code (`reference` output) and later in a
cell's env (matrix) or on a patched copy (patch gate). Outputs are normalised and compared;
divergences are `observed` facts, while the probe code itself is `ai_generated`.
"""

from __future__ import annotations

import ast
import json
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from . import diagnose, envs, llm
from .provenance import ai_generated, observed
from .runctx import clean_env, run_cmd

SCHEMA = {"type": "object", "required": ["probe"],
          "properties": {"probe": {"type": "string", "minLength": 20}, "cases": {"type": "array"},
                         "what_it_checks": {"type": "string"}}}
HEX = re.compile(r"0x[0-9a-fA-F]{6,}")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)?")
TMP_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/][^\s'\"]*?(?:tmp|temp|Temp)[\\/][^\s'\"]*", re.IGNORECASE)

GENERIC_TEMPLATE = r'''
import contextlib, importlib, io, json, pkgutil, sys
results = {}
for pkg in PACKAGES:
    try:
        mod = importlib.import_module(pkg)
    except Exception as e:
        results["import " + pkg] = {"error": type(e).__name__ + ": " + str(e)[:200]}
        continue
    results["import " + pkg] = "ok"
    for info in pkgutil.walk_packages(getattr(mod, "__path__", []), pkg + "."):
        try:
            importlib.import_module(info.name)
            results["import " + info.name] = "ok"
        except Exception as e:
            results["import " + info.name] = {"error": type(e).__name__ + ": " + str(e)[:200]}
for name, target in ENTRY_POINTS.items():
    mod_name, _, attr = target.partition(":")
    out = io.StringIO()
    code = None
    try:
        func = importlib.import_module(mod_name)
        for part in attr.split("."):
            func = getattr(func, part)
        sys.argv = [name, "--help"]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                func()
            except SystemExit as e:
                code = e.code
        results["cli " + name + " --help"] = {"exit": code, "output": out.getvalue()}
    except Exception as e:
        results["cli " + name + " --help"] = {"error": type(e).__name__ + ": " + str(e)[:200]}
print(json.dumps(results, sort_keys=True, default=repr))
'''


def generic_probe(repo: Path, local_packages: list[str]) -> str:
    entry_points: dict[str, str] = {}
    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        try:
            entry_points = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("scripts", {})
        except tomllib.TOMLDecodeError:
            entry_points = {}
    pkgs = [p for p in local_packages if (repo / p / "__init__.py").exists() or (repo / "src" / p).exists()]
    return f"PACKAGES = {pkgs!r}\nENTRY_POINTS = {entry_points!r}\n" + GENERIC_TEMPLATE


def normalise(value: Any, repo: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: normalise(v, repo) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [normalise(v, repo) for v in value]
    if isinstance(value, str):
        if repo:
            value = value.replace(repo, "<repo>").replace(repo.replace("\\", "/"), "<repo>")
        value = HEX.sub("0x…", value)
        value = TIMESTAMP.sub("<timestamp>", value)
        return TMP_PATH.sub("<tmp>", value)
    return value


def run_probe(code: str, python: str | Path, repo: Path, log_path: Path | None = None,
              timeout: float = 90) -> dict[str, Any]:
    """Run a probe script; returns {"ok", "output"|"error"}."""
    with tempfile.TemporaryDirectory(prefix="bf-probe-") as tmp:
        script = Path(tmp) / "probe.py"
        script.write_text(code, encoding="utf-8")
        res = run_cmd([str(python), str(script)], cwd=repo, timeout=timeout, log_path=log_path,
                      env=clean_env({"PYTHONPATH": str(repo), "PYTHONHASHSEED": "0"}))
    lines = [ln for ln in res.stdout.strip().splitlines() if ln.strip()]
    if not res.ok or not lines:
        return {"ok": False, "error": (res.stderr or res.stdout or "no output")[-800:]}
    try:
        return {"ok": True, "output": normalise(json.loads(lines[-1]), str(repo))}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"last line is not JSON: {exc}: {lines[-1][:200]}"}


def compare(reference: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for case in sorted(set(reference) | set(candidate)):
        a, b = reference.get(case, "<missing>"), candidate.get(case, "<missing>")
        if a != b:
            out.append({"case": case, "reference": a, "candidate": b})
    return out


def static_problems(code: str, forbidden_imports: set[str]) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error line {exc.lineno}: {exc.msg}"]
    problems = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for n in names:
            if n.split(".")[0] in forbidden_imports:
                problems.append(f"imports the dependency directly: {n}")
            if n.split(".")[0] in ("socket", "http", "urllib") and n != "urllib.parse":
                problems.append(f"network module imported: {n}")
    return problems


def generate(stage1: dict[str, Any], stage2: dict[str, Any], repo: Path, deps: list[str], client: llm.LLM | None,
             run_root: Path, pythons: list[str], *, max_tries: int = 2) -> dict[str, Any]:
    """Write probes and record their reference output on every Python. Returns the probe set."""
    log_dir = run_root / "logs" / "probes"
    ref_python = {py: envs.env_python(stage2["pythons"][py]["reference_env"]) for py in pythons}
    probe_set: dict[str, Any] = {}

    local = stage1["scan"]["local_modules"]
    code = generic_probe(repo, local)
    probe_set["generic"] = _with_reference("generic", code, ref_python, repo, log_dir,
                                           meta={"author": "tool", "what_it_checks": "module imports and CLI --help"})
    if client is None:
        return probe_set
    import_names = {d["name"]: d.get("import_names") or [] for d in stage1["dependencies"]}
    for dep in deps:
        usage = diagnose.usage_context(stage1, repo, [dep])
        if usage.startswith("(the package is not imported"):
            continue
        files = sorted({u["file"] for u in stage1["usage_locations"] if u.get("dependency") == dep
                        and not u["in_tests"]})
        sources = "\n".join(f"### {f}\n```python\n{(repo / f).read_text(encoding='utf-8')}\n```" for f in files)
        forbidden = set(import_names.get(dep) or [dep.replace("-", "_")])
        feedback = ""
        for attempt in range(1, max_tries + 1):
            prompt = llm.render_prompt("probe", dependency=dep, usage=usage, files=sources, feedback=feedback,
                                       dependency_import=", ".join(sorted(forbidden)))
            try:
                data, meta = client.complete_json(prompt, SCHEMA, task=f"probe-{dep}-a{attempt}")
            except llm.LLMError as exc:
                probe_set[dep] = {"usable": False, "reason": f"model call failed: {exc}", "author": "model"}
                break
            problems = static_problems(data["probe"], forbidden)
            entry = _with_reference(dep, data["probe"], ref_python, repo, log_dir,
                                    meta={"author": "model", "model": meta, "what_it_checks":
                                          data.get("what_it_checks"), "cases": data.get("cases")},
                                    problems=problems)
            probe_set[dep] = entry
            if entry["usable"]:
                break
            feedback = ("\n## Your previous probe could not be used\n" + entry["reason"][:1200] +
                        "\nWrite a corrected probe.\n")
    return probe_set


def _with_reference(name: str, code: str, ref_python: dict[str, Path], repo: Path, log_dir: Path, *,
                    meta: dict[str, Any], problems: list[str] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "code": code, **meta, "reference": {}, "usable": True}
    if problems:
        entry.update(usable=False, reason="; ".join(problems))
        return entry
    for py, python in ref_python.items():
        res = run_probe(code, python, repo, log_dir / f"{name}-reference-{py}.log")
        if not res["ok"]:
            entry.update(usable=False, reason=f"probe failed in the reference env on {py}: {res['error']}")
            return entry
        entry["reference"][py] = res["output"]
    return entry


def probes_for(packages: list[str], probe_set: dict[str, Any]) -> list[dict[str, Any]]:
    out = [probe_set["generic"]] if probe_set.get("generic", {}).get("usable") else []
    out += [probe_set[p] for p in packages if p in probe_set and probe_set[p].get("usable")]
    return out


def check(packages: list[str], probe_set: dict[str, Any], python: str | Path, repo: Path, py: str,
          log_dir: Path, label: str) -> dict[str, Any]:
    """Run the relevant probes in a candidate env/repo and diff against the reference outputs."""
    results = []
    for p in probes_for(packages, probe_set):
        res = run_probe(p["code"], python, repo, log_dir / f"{label}-probe-{p['name']}.log")
        if not res["ok"]:
            results.append({"probe": p["name"], "ran": False, "error": res["error"],
                            "divergences": [{"case": "(probe crashed)", "reference": "ran",
                                             "candidate": res["error"][-300:]}]})
            continue
        results.append({"probe": p["name"], "ran": True,
                        "divergences": compare(p["reference"].get(py, {}), res["output"])})
    return {"probes_run": [r["probe"] for r in results], "results": results,
            "divergences": sum(len(r["divergences"]) for r in results)}


def to_capsule(probe_set: dict[str, Any], matrix: list[dict[str, Any]], repairs: list[dict[str, Any]]) -> list:
    recs: list[dict[str, Any]] = []
    for name, p in probe_set.items():
        maker = ai_generated if p.get("author") == "model" else observed
        recs.append(maker(source={"kind": "probe", "author": p.get("author"),
                                  "model_used": (p.get("model") or {}).get("model_used")},
                          kind="probe", probe=name, what_it_checks=p.get("what_it_checks"), usable=p.get("usable"),
                          reason=p.get("reason"), code=p.get("code"), cases=p.get("cases")))
    for r in matrix:
        pr = r.get("probes")
        if pr:
            recs.append(observed(source={"kind": "probe diff", "cell": r["cell"], "python": r["python"]},
                                 kind="matrix_probe_diff", cell=r["cell"], python=r["python"],
                                 probes_run=pr["probes_run"], divergences=pr["divergences"],
                                 details=[x for x in pr["results"] if x["divergences"]]))
    for rep in repairs:
        for att in rep.get("attempts", []):
            for py, e in (att.get("executed") or {}).items():
                pr = e.get("probes")
                if pr:
                    recs.append(observed(source={"kind": "probe diff", "cell": rep["cell"], "python": py},
                                         kind="patch_probe_diff", cell=rep["cell"], attempt=att["attempt"],
                                         python=py, probes_run=pr["probes_run"], divergences=pr["divergences"],
                                         details=[x for x in pr["results"] if x["divergences"]]))
    return recs
