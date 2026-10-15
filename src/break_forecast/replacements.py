"""Stage 4 (AI part): propose a replacement for each frozen dependency, then verify it.

The proposal is `ai_generated`. Whether the proposed package exists is checked against
PyPI / the standard library and attached as a nested `observed` verification.
"""

from __future__ import annotations

from typing import Any

from . import cells, llm
from .provenance import ai_generated, observed

SCHEMA = {
    "type": "object",
    "required": ["replacement", "kind", "rationale", "migration_outline"],
    "properties": {
        "replacement": {"type": "string", "minLength": 1},
        "kind": {"enum": ["pypi", "stdlib"]},
        "api_compatible": {"type": "boolean"},
        "rationale": {"type": "string"},
        "migration_outline": {"type": "array", "items": {"type": "string"}},
        "api_mapping": {"type": "array"},
        "confidence": {"type": "number"},
    },
}


def frozen_dependencies(stage1: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for d in stage1["dependencies"]
            if "frozen" in d["classification"] and any(x["effective"] for x in d.get("declarations", []))]


def propose(stage1: dict[str, Any], client: llm.LLM, *, offline: bool = False) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for dep in frozen_dependencies(stage1):
        name = dep["name"]
        usage = [f"{u['file']}:{u['line']} {u['api']} ({u['kind']}{', test' if u['in_tests'] else ''})"
                 for u in stage1["usage_locations"] if u.get("dependency") == name]
        reason = "; ".join(r["detail"] for r in stage1["risk_indicators"]
                           if r["dependency"] == name and r["indicator"] in ("frozen", "no_newer_version"))
        metadata = {k: dep.get(k) for k in ("effective_spec", "latest", "last_release_date", "summary",
                                            "requires_python_latest", "github")}
        commands = [c["text"] for c in stage1["manifests"]["install_commands"] + stage1["manifests"]["test_commands"]
                    if name.split("-")[0] in c["text"].lower()]
        prompt = llm.render_prompt("replace_target", dependency=name, reason=reason or "frozen",
                                   metadata=metadata,
                                   usage="\n".join(usage + [f"command: {c}" for c in commands]) or "(none found)")
        rid = f"replacement:{name}"
        try:
            data, meta = client.complete_json(prompt, SCHEMA, task=f"replace-{name}")
        except llm.LLMError as exc:
            out[name] = ai_generated(source={"kind": "model", "error": str(exc)}, id=rid, dependency=name,
                                     replacement=None, verified=False, verification=f"model call failed: {exc}")
            continue
        ok, why = cells.verify_replacement(data, offline)
        out[name] = ai_generated(
            source={"kind": "model", **meta}, id=rid, dependency=name, **data, verified=ok,
            verification=observed(source={"kind": "pypi/stdlib check"}, ok=ok, detail=why),
        )
    return out
