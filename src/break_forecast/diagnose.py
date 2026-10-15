"""Stage 6: diagnose failing / degraded matrix cells with the model. Output is `ai_generated`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import llm
from .provenance import ai_generated

ROOT_CAUSES = ["api_removed", "api_changed", "behavior_change", "python_incompat", "transitive_break",
               "env_issue", "unknown"]
SCHEMA = {
    "type": "object",
    "required": ["root_cause", "explanation", "code_fixable", "fix_strategy"],
    "properties": {
        "root_cause": {"enum": ROOT_CAUSES},
        "explanation": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "affected_locations": {"type": "array", "items": {"type": "string"}},
        "code_fixable": {"type": "boolean"},
        "fix_strategy": {"type": "string"},
        "co_upgrade_needed": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
}
NEEDS_DIAGNOSIS = ("fail", "import-fail", "warn", "install-fail")


def snippet(repo: Path, rel: str, line: int, context: int = 3) -> str:
    try:
        lines = (repo / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    lo, hi = max(0, line - 1 - context), min(len(lines), line + context)
    return "\n".join(f"{i + 1:>4}{'>' if i + 1 == line else ' '} {lines[i]}" for i in range(lo, hi))


def packages_of(cell: dict[str, Any]) -> list[str]:
    return sorted({*cell.get("set_pins", {}), *cell.get("remove", []), *cell.get("add_unpinned", [])})


def usage_context(stage1: dict[str, Any], repo: Path, packages: list[str], limit: int = 12) -> str:
    sites = [u for u in stage1["usage_locations"] if u.get("dependency") in packages and not u["in_tests"]]
    seen: set[tuple[str, int]] = set()
    blocks = []
    for u in sites:
        key = (u["file"], u["line"])
        if key in seen:
            continue
        seen.add(key)
        blocks.append(f"### {u['file']}:{u['line']}  `{u['api']}` ({u['kind']})\n```python\n"
                      f"{snippet(repo, u['file'], u['line'])}\n```")
        if len(blocks) >= limit:
            break
    return "\n".join(blocks) or "(the package is not imported by source files)"


def evidence_text(records: list[dict[str, Any]]) -> tuple[str, str]:
    results, errors = [], []
    for r in records:
        normal = r.get("normal") or {}
        results.append(f"- Python {r['python']}: {r['result']} (normal: {normal.get('outcome', '-')}, "
                       f"strict: {(r.get('strict') or {}).get('outcome', '-')})")
        if r.get("reason"):
            errors.append(f"[{r['python']}] {r['reason'][:1500]}")
        if normal.get("error_excerpt"):
            errors.append(f"[{r['python']}] suite could not load: {normal['error_excerpt']}")
        for test, msg in (r.get("failures") or {}).items():
            errors.append(f"[{r['python']}] FAILED {test}: {msg}")
        for w in r.get("new_warnings") or []:
            errors.append(f"[{r['python']}] new {w['category']}: {w['message']}")
        strict = r.get("strict") or {}
        if strict.get("error_excerpt") and strict.get("error_excerpt") != normal.get("error_excerpt"):
            errors.append(f"[{r['python']}] strict run (-W error::DeprecationWarning): {strict['error_excerpt']}")
    # The same message on both Pythons only needs to be shown once.
    unique = list(dict.fromkeys(e.split("] ", 1)[-1] for e in errors))
    return "\n".join(results), "\n".join(f"- {e}" for e in unique) or "(none)"


def diagnose_cell(cell: dict[str, Any], records: list[dict[str, Any]], stage1: dict[str, Any], repo: Path,
                  client: llm.LLM) -> dict[str, Any]:
    pkgs = packages_of(cell)
    results, errors = evidence_text(records)
    meta = {d["name"]: {k: d.get(k) for k in ("effective_spec", "latest", "summary", "requires_python_latest")}
            for d in stage1["dependencies"] if d["name"] in pkgs}
    change = (f"{cell['id']}: {cell['reason']}\nset pins: {cell.get('set_pins')}\nremoved: {cell.get('remove')}\n"
              f"added: {cell.get('add_unpinned')}\nco-upgrades forced by the resolver: "
              f"{records[0].get('co_upgrades') if records else {}}")
    if cell.get("type") == "replace" and cell.get("target_version"):
        target = cell["target_version"]
        kind = "a Python standard-library module (nothing to install)" if not cell.get("add_unpinned") \
            else "a PyPI package installed by the tool"
        change += (f"\nINTENDED MIGRATION: {cell['dependency']} is being replaced by `{target}`, which is {kind}. "
                   "The dependency change is already applied; source code that still imports the old package must "
                   "be migrated to the replacement.")
    prompt = llm.render_prompt("diagnose", change=change, results=results, errors=errors,
                               usage=usage_context(stage1, repo, pkgs), metadata=meta)
    try:
        data, meta_call = client.complete_json(prompt, SCHEMA, task=f"diagnose-{cell['id']}")
    except llm.LLMError as exc:
        return ai_generated(source={"kind": "model", "error": str(exc)}, cell=cell["id"], root_cause="unknown",
                            explanation=f"model call failed: {exc}", code_fixable=False, fix_strategy="",
                            failed=True)
    return ai_generated(source={"kind": "model", **meta_call}, cell=cell["id"],
                        observed_results={r["python"]: r["result"] for r in records}, **data)


def cells_to_diagnose(cells: list[dict[str, Any]], matrix: list[dict[str, Any]]) -> list[tuple[dict, list[dict]]]:
    by_cell: dict[str, list[dict[str, Any]]] = {}
    for r in matrix:
        by_cell.setdefault(r["cell"], []).append(r)
    out = []
    for c in cells:
        recs = by_cell.get(c["id"], [])
        if any(r["result"] in NEEDS_DIAGNOSIS for r in recs):
            out.append((c, recs))
    return out
