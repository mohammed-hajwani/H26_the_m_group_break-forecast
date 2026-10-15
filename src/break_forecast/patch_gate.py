"""Stage 7: evidence-gated patches.

The model proposes search/replace edits (`ai_generated`). A patch is only marked verified
when every gate rule holds. Static rules run before anything executes; executed rules run
the real test suite on every target Python against a patched *copy* of the repo:

static   allowlisted_paths      only existing, non-test, non-config .py files inside the repo
         applies_cleanly        every search text matches exactly once
         syntax_ok              patched files still parse
         no_tamper_patterns     no skip/xfail markers, warning suppression or sys.modules stubs added
         no_broad_except_added  no new bare / `except Exception` / `except BaseException` handlers
         test_tree_unchanged    hash of tests + test config identical before/after
executed no_regressions         nothing that passed in the reference baseline fails
         target_fixed           the cell's original failure (or new warning) is gone
         strict_not_worse       the strict run has no new failures vs the reference strict run
         target_installed       the env really contains the cell's target versions (no quiet re-pin)
pending  probes                 differential probes (Stage 8) - until they run, the best verdict
                                is `verified_tests` rather than `verified`
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import itertools
import json
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from . import diagnose, llm, matrix
from .cells import Cell, slugify
from .manifests import SKIP_DIRS
from .provenance import ai_generated, executed
from .runctx import Run
from .usage import is_test_path

PROTECTED_NAMES = {"conftest.py", "setup.py", "noxfile.py"}
TEST_CONFIG_FILES = ("pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml")
TAMPER_PATTERNS = {
    "skip_or_xfail": re.compile(r"pytest\.(skip|xfail|importorskip)\b|mark\.(skip|skipif|xfail)\b|unittest\.skip"),
    "warning_suppression": re.compile(r"warnings\.(filterwarnings|simplefilter|catch_warnings|resetwarnings)"
                                      r"|PYTHONWARNINGS"),
    "module_stub": re.compile(r"sys\.modules\s*\["),
}
EDIT_SCHEMA = {
    "type": "object",
    "required": ["edits"],
    "properties": {
        "summary": {"type": "string"},
        "edits": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["file", "search", "replace"],
            "properties": {"file": {"type": "string"}, "search": {"type": "string", "minLength": 1},
                           "replace": {"type": "string"}}}},
    },
}


def _rule(ok: bool, detail: str = "") -> dict[str, Any]:
    return {"ok": bool(ok), "detail": detail}


def normalise_path(file: str) -> str | None:
    """Repo-relative POSIX path, or None if it is absolute, has a drive letter or climbs out with '..'."""
    f = file.strip().replace("\\", "/")
    while f.startswith("./"):
        f = f[2:]
    p = PurePosixPath(f)
    if not f or p.is_absolute() or ".." in p.parts or ":" in p.parts[0]:
        return None
    return p.as_posix()


def path_allowed(repo: Path, rel: str | None) -> tuple[bool, str]:
    if rel is None:
        return False, "path escapes the repository"
    p = PurePosixPath(rel)
    if p.suffix != ".py":
        return False, f"{rel}: only Python source files may be edited"
    if p.name in PROTECTED_NAMES or is_test_path(rel):
        return False, f"{rel}: tests, conftest and setup files are protected"
    if any(part in SKIP_DIRS or part.startswith(".") for part in p.parts[:-1]):
        return False, f"{rel}: not a project source directory"
    if not (repo / rel).is_file():
        return False, f"{rel}: file does not exist (new files are not allowed)"
    return True, ""


def broad_handlers(source: str) -> int:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            t = node.type
            if t is None or (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException")):
                count += 1
    return count


def added_lines(old: str, new: str) -> list[str]:
    return [ln[1:] for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
            if ln.startswith("+") and not ln.startswith("+++")]


def apply_edits(repo: Path, edits: list[dict[str, str]]) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    """Apply edits in memory. Returns (original contents, new contents, static rule results)."""
    rules: dict[str, dict[str, Any]] = {}
    originals: dict[str, str] = {}
    contents: dict[str, str] = {}
    bad_paths, unclean = [], []
    for e in edits:
        rel = normalise_path(e.get("file", ""))
        ok, why = path_allowed(repo, rel)
        if not ok:
            bad_paths.append(why)
            continue
        if rel not in contents:
            originals[rel] = (repo / rel).read_text(encoding="utf-8")
            contents[rel] = originals[rel]
        search = e["search"]
        count = contents[rel].count(search)
        if count != 1:
            # Tolerate CRLF/LF differences, nothing else.
            alt = search.replace("\r\n", "\n")
            body = contents[rel].replace("\r\n", "\n")
            if body.count(alt) == 1:
                contents[rel] = body.replace(alt, e["replace"].replace("\r\n", "\n"), 1)
                continue
            unclean.append(f"{rel}: search text found {count} times (must be exactly once)")
            continue
        contents[rel] = contents[rel].replace(search, e["replace"], 1)
    rules["allowlisted_paths"] = _rule(not bad_paths, "; ".join(bad_paths))
    rules["applies_cleanly"] = _rule(not unclean and not bad_paths, "; ".join(unclean))
    syntax = []
    for rel, text in contents.items():
        try:
            ast.parse(text)
        except SyntaxError as exc:
            syntax.append(f"{rel}:{exc.lineno}: {exc.msg}")
    rules["syntax_ok"] = _rule(not syntax, "; ".join(syntax))
    tamper = []
    for rel, text in contents.items():
        for line in added_lines(originals[rel], text):
            for name, pat in TAMPER_PATTERNS.items():
                if pat.search(line):
                    tamper.append(f"{rel}: {name}: {line.strip()[:120]}")
    rules["no_tamper_patterns"] = _rule(not tamper, "; ".join(tamper))
    broad = [f"{rel}: {broad_handlers(originals[rel])} -> {broad_handlers(t)} broad except handlers"
             for rel, t in contents.items() if broad_handlers(t) > broad_handlers(originals[rel])]
    rules["no_broad_except_added"] = _rule(not broad, "; ".join(broad))
    return originals, contents, rules


def unified_diff(originals: dict[str, str], contents: dict[str, str]) -> str:
    parts = []
    for rel in sorted(contents):
        parts.extend(difflib.unified_diff(originals[rel].splitlines(keepends=True),
                                          contents[rel].splitlines(keepends=True),
                                          fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    return "".join(parts)


def test_tree_hash(repo: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(repo.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(repo).as_posix()
        if any(part in SKIP_DIRS for part in p.relative_to(repo).parts):
            continue
        if is_test_path(rel) or p.name in TEST_CONFIG_FILES or p.name == "conftest.py":
            h.update(rel.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def _force_remove(func: Any, path: str, _exc: Any) -> None:
    """rmtree error handler: clear the read-only bit (git pack files on Windows) and retry."""
    import os
    import stat

    os.chmod(path, stat.S_IWRITE)
    func(path)


def materialise(repo: Path, contents: dict[str, str], dest: Path) -> Path:
    """A throwaway copy of the target with ``contents`` written over it (git history is not copied)."""
    if dest.exists():
        shutil.rmtree(dest, onexc=_force_remove)
    shutil.copytree(repo, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git"))
    for rel, text in contents.items():
        (dest / rel).write_text(text, encoding="utf-8")
    return dest


def executed_rules(cell: Cell, rec: dict[str, Any], original: dict[str, Any] | None) -> dict[str, Any]:
    normal = rec.get("normal") or {}
    diff = rec.get("diff_vs_reference") or {}
    loaded = rec.get("result") not in ("import-fail", "install-fail") and normal.get("outcome") not in (
        "import-fail", "error", "timeout", None)
    rules = {"no_regressions": _rule(loaded and not rec.get("confirmed_failing") and not diff.get("missing"),
                                     normal.get("error_excerpt") or ", ".join(rec.get("confirmed_failing") or [])
                                     or (f"missing tests: {len(diff.get('missing') or [])}" if diff.get("missing")
                                         else ""))}
    was = (original or {}).get("result")
    if was == "warn":
        new = rec.get("new_warnings") or []
        rules["target_fixed"] = _rule(loaded and not new, "; ".join(w["message"][:160] for w in new))
    else:
        rules["target_fixed"] = _rule(loaded and rec.get("result") in ("pass", "warn", "flaky"),
                                      f"result {rec.get('result')}")
    rules["strict_not_worse"] = _rule(loaded and not rec.get("strict_regressed"),
                                      (rec.get("strict") or {}).get("error_excerpt") or "")
    want = cell.set_pins
    got = rec.get("installed_target") or {}
    rules["target_installed"] = _rule(all(got.get(k) == v for k, v in want.items()),
                                      f"wanted {want}, installed {got}" if want else "no pinned target")
    pr = rec.get("probes")
    if pr is not None:
        diffs = [f"{x['probe']}::{d['case']}: reference={json.dumps(d['reference'], default=str)[:160]} "
                 f"patched={json.dumps(d['candidate'], default=str)[:160]}"
                 for x in pr["results"] for d in x["divergences"]]
        rules[PROBE_RULE] = _rule(not diffs, "; ".join(diffs[:8]) or f"probes run: {', '.join(pr['probes_run'])}")
    return rules


PROBE_RULE = "behaviour_preserved"
RANK = {"verified": 3, "verified_tests": 2, "rejected": 1, "no_patch": 0}


def verdict_of(static: dict[str, Any], executed_by_py: dict[str, Any]) -> str:
    """verified: every rule incl. probes; verified_tests: every rule except probe divergences; else rejected."""
    if not all(r["ok"] for r in static.values()) or not executed_by_py:
        return "rejected"
    test_ok = all(r["ok"] for e in executed_by_py.values() for n, r in e["rules"].items() if n != PROBE_RULE)
    probe_ok = all(e["rules"].get(PROBE_RULE, {"ok": True})["ok"] for e in executed_by_py.values())
    if test_ok and probe_ok:
        return "verified"
    return "verified_tests" if test_ok else "rejected"


def _patch_files(stage1: dict[str, Any], repo: Path, cell: dict[str, Any], diag: dict[str, Any]) -> list[str]:
    pkgs = diagnose.packages_of(cell)
    files = {u["file"] for u in stage1["usage_locations"] if u.get("dependency") in pkgs and not u["in_tests"]}
    for loc in diag.get("affected_locations") or []:
        rel = normalise_path(str(loc).split(":")[0])
        if path_allowed(repo, rel)[0]:
            files.add(rel)
    return sorted(f for f in files if path_allowed(repo, f)[0])


def _files_block(repo: Path, files: list[str], budget: int = 40000) -> str:
    out, used = [], 0
    for rel in files:
        text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        if used + len(text) > budget:
            break
        used += len(text)
        out.append(f"### {rel}\n```python\n{text}\n```")
    return "\n".join(out)


def _feedback(attempt: dict[str, Any]) -> str:
    head = ("## Your previous attempt passed the tests but CHANGED BEHAVIOUR (differential probes)"
            if attempt.get("verdict") == "verified_tests" else "## Your previous attempt was REJECTED by the gate")
    lines = ["", head, f"Summary: {attempt.get('summary', '')}"]
    for scope, rules in [("static", attempt.get("static", {}))] + [
            (f"Python {py}", r.get("rules", {})) for py, r in (attempt.get("executed") or {}).items()]:
        for name, res in rules.items():
            if not res["ok"]:
                lines.append(f"- [{scope}] {name}: {res['detail'][:600]}")
    lines.append("Propose a corrected set of edits against the CURRENT file contents shown above.\n")
    return "\n".join(lines)


def repair_cell(cell_d: dict[str, Any], diag: dict[str, Any], records: list[dict[str, Any]],
                stage1: dict[str, Any], stage2: dict[str, Any], resolutions: dict[str, Any], repo: Path, run: Run,
                client: llm.LLM, pythons: list[str], *, max_attempts: int = 3, timeout: float = 300,
                progress: Any = None, probe_set: dict[str, Any] | None = None) -> dict[str, Any]:
    cell = Cell(**cell_d)
    by_py = {r["python"]: r for r in records}
    files = _patch_files(stage1, repo, cell_d, diag)
    _, errors = diagnose.evidence_text(records)
    base_hash = test_tree_hash(repo)
    attempts: list[dict[str, Any]] = []
    feedback = ""
    for n in range(1, max_attempts + 1):
        prompt = llm.render_prompt(
            "patch", change=f"{cell.id}: {cell.reason}\nset pins: {cell.set_pins}\nremoved: {cell.remove}\n"
                            f"added: {cell.add_unpinned}\ntarget: {cell.target_version}",
            diagnosis={k: diag.get(k) for k in ("root_cause", "explanation", "fix_strategy")},
            errors=errors, files=_files_block(repo, files) or "(no source files use this package)",
            feedback=feedback)
        attempt: dict[str, Any] = {"attempt": n}
        try:
            data, meta = client.complete_json(prompt, EDIT_SCHEMA, task=f"patch-{slugify(cell.id)}-a{n}")
        except llm.LLMError as exc:
            # No answer from any backend: record it and spend the next attempt on asking again.
            attempt.update(verdict="no_patch", reason=f"model call failed: {exc}", failed_rules=[])
            attempts.append(attempt)
            continue
        attempt.update(model=meta, summary=data.get("summary", ""), edits=data["edits"])
        originals, contents, static = apply_edits(repo, data["edits"])
        attempt["diff"] = unified_diff(originals, contents)
        dest = run.work / "patches" / f"{slugify(cell.id)}-a{n}"
        materialise(repo, contents if static["allowlisted_paths"]["ok"] else {}, dest)
        static["test_tree_unchanged"] = _rule(test_tree_hash(dest) == base_hash, "")
        attempt["static"] = static
        if progress:
            progress(f"{cell.id} attempt {n}: static {'ok' if all(r['ok'] for r in static.values()) else 'FAILED'}")
        if all(r["ok"] for r in static.values()):
            attempt["executed"] = {}
            for py in pythons:
                res = resolutions.get(f"{cell.id}|{py}") or {}
                if not res.get("ok"):
                    attempt["executed"][py] = {"rules": {"resolved": _rule(False, "cell did not resolve")}}
                    continue
                rec = matrix.evaluate(cell, py, res["lock_file"], stage2["pythons"][py], dest, run, rec={},
                                      label=f"patch-{slugify(cell.id)}-a{n}-{py}", log_dir=run.logs / "patches",
                                      timeout=timeout, keep_envs=False, probe_set=probe_set)
                attempt["executed"][py] = {"result": rec.get("result"), "normal": rec.get("normal"),
                                           "strict": rec.get("strict"), "new_warnings": rec.get("new_warnings"),
                                           "confirmed_failing": rec.get("confirmed_failing"),
                                           "probes": rec.get("probes"),
                                           "rules": executed_rules(cell, rec, by_py.get(py))}
                if progress:
                    progress(f"{cell.id} attempt {n} on {py}: {rec.get('result')}")
        attempt["verdict"] = verdict_of(static, attempt.get("executed") or {})
        attempt["failed_rules"] = sorted({name for name, r in static.items() if not r["ok"]} |
                                         {name for e in (attempt.get("executed") or {}).values()
                                          for name, r in e["rules"].items() if not r["ok"]})
        attempts.append(attempt)
        from . import events

        events.emit("patch", **events.patch_payload(cell.id, attempt))
        if attempt["verdict"] == "verified":
            break
        feedback = _feedback(attempt)
    best = max(attempts, key=lambda a: (RANK.get(a.get("verdict"), 0), -a["attempt"]), default=None)
    verdict = best["verdict"] if best else "no_patch"
    return {"cell": cell.id, "files_offered": files, "attempts": attempts, "verdict": verdict,
            "verified_attempt": best["attempt"] if verdict in ("verified", "verified_tests") else None,
            "probes": "run" if probe_set else "no probe set available"}


def combine(verified: list[dict[str, Any]], cell_d: dict[str, Any], stage2: dict[str, Any],
            resolutions: dict[str, Any], repo: Path, run: Run, pythons: list[str], original: list[dict[str, Any]],
            *, timeout: float = 300, probe_set: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply every verified patch together and gate the result on a combined cell (e.g. all-latest)."""
    cell = Cell(**cell_d)
    by_py = {r["python"]: r for r in original}
    # Patches that touch the same file can depend on order (one patch's search text may contain a line
    # another patch rewrites). Try orders until every patch applies cleanly; record the order used.
    order, contents, static, tried = find_order(verified, repo, run)
    originals = {rel: (repo / rel).read_text(encoding="utf-8") for rel in contents}
    dest = materialise(repo, contents, run.work / "patches" / f"{slugify(cell.id)}-combined")
    static["test_tree_unchanged"] = _rule(test_tree_hash(dest) == test_tree_hash(repo), "")
    result: dict[str, Any] = {"cell": cell.id, "combined_from": [p["cell"] for p in order],
                              "orders_tried": tried,
                              "diff": unified_diff(originals, contents), "static": static, "executed": {}}
    if all(r["ok"] for r in static.values()):
        for py in pythons:
            res = resolutions.get(f"{cell.id}|{py}") or {}
            if not res.get("ok"):
                continue
            rec = matrix.evaluate(cell, py, res["lock_file"], stage2["pythons"][py], dest, run, rec={},
                                  label=f"patch-{slugify(cell.id)}-combined-{py}", log_dir=run.logs / "patches",
                                  timeout=timeout, keep_envs=False, probe_set=probe_set)
            result["executed"][py] = {"result": rec.get("result"), "normal": rec.get("normal"),
                                      "strict": rec.get("strict"), "new_warnings": rec.get("new_warnings"),
                                      "probes": rec.get("probes"),
                                      "rules": executed_rules(cell, rec, by_py.get(py))}
    result["verdict"] = verdict_of(static, result["executed"])
    result["failed_rules"] = sorted({n for n, r in static.items() if not r["ok"]} |
                                    {n for e in result["executed"].values() for n, r in e["rules"].items()
                                     if not r["ok"]})
    return result


def find_order(verified: list[dict[str, Any]], repo: Path, run: Run,
               limit: int = 120) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, Any], int]:
    """First order (of at most ``limit``) in which every patch applies cleanly; else the given order."""
    first: tuple[list, dict, dict] | None = None
    tried = 0
    for perm in itertools.islice(itertools.permutations(verified), limit):
        tried += 1
        contents, static = _apply_sequence(list(perm), repo, run)
        if first is None:
            first = (list(perm), contents, static)
        if static.get("applies_cleanly", {"ok": True})["ok"]:
            return list(perm), contents, static, tried
    assert first is not None
    return first[0], first[1], first[2], tried


def _apply_sequence(patches: list[dict[str, Any]], repo: Path, run: Run) -> tuple[dict[str, str], dict[str, Any]]:
    """Apply verified patches one after another on a staging copy, so each sees the previous ones."""
    staging = materialise(repo, {}, run.work / "patches" / "combined-staging")
    contents: dict[str, str] = {}
    static: dict[str, Any] = {}
    for p in patches:
        att = p["attempts"][p["verified_attempt"] - 1]
        _, changed, rules = apply_edits(staging, att["edits"])
        for name, r in rules.items():
            prev = static.get(name)
            if prev is None or prev["ok"]:
                static[name] = r if r["ok"] else _rule(False, f"{p['cell']}: {r['detail']}")
        contents.update(changed)
        for rel, text in changed.items():
            (staging / rel).write_text(text, encoding="utf-8")
    return contents, static


def to_capsule(diag_records: list[dict[str, Any]], repairs: list[dict[str, Any]],
               combined: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Patch records: the edits/diff are ai_generated, each gate verdict is an executed sub-record."""
    patches = []
    for rep in repairs:
        for att in rep["attempts"]:
            patches.append(ai_generated(
                source={"kind": "model", **{k: v for k, v in (att.get("model") or {}).items()
                                            if k in ("model_used", "prompt_sha256", "response_sha256", "cached")}},
                cell=rep["cell"], attempt=att["attempt"], summary=att.get("summary"), diff=att.get("diff"),
                edits=att.get("edits"), selected=att["attempt"] == rep.get("verified_attempt"),
                files_offered=rep["files_offered"], status=att.get("verdict"),
                gate=executed(source={"kind": "patch gate"}, static=att.get("static"),
                              executed=_slim(att.get("executed")), failed_rules=att.get("failed_rules", []),
                              verdict=att.get("verdict"), reason=att.get("reason"), probes=rep.get("probes")),
            ))
    if combined:
        patches.append(ai_generated(
            source={"kind": "combination of verified model patches"}, cell=combined["cell"], attempt=1,
            summary=f"all verified patches ({', '.join(combined['combined_from'])}) applied together on: "
                    f"{(combined.get('exit_plan') or {}).get('reason', combined['cell'])}",
            diff=combined["diff"], status=combined["verdict"], combined_from=combined["combined_from"],
            exit_plan=combined.get("exit_plan"), resolution=combined.get("resolution"), selected=True,
            gate=executed(source={"kind": "patch gate"}, static=combined["static"],
                          executed=_slim(combined["executed"]), failed_rules=combined["failed_rules"],
                          verdict=combined["verdict"], probes="run"),
        ))
    return diag_records, patches


def _slim(executed_by_py: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep probe divergence details out of the gate record (they live in behavioral_diffs)."""
    if not executed_by_py:
        return executed_by_py
    out = {}
    for py, e in executed_by_py.items():
        e = dict(e)
        if e.get("probes"):
            e["probes"] = {"probes_run": e["probes"]["probes_run"], "divergences": e["probes"]["divergences"]}
        out[py] = e
    return out
