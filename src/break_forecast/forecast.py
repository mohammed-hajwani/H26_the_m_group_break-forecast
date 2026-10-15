"""Stage 3: blind forecast. The model predicts each cell's outcome before Stage 5 runs it.

The forecast is written to stage3.json with prompt/response hashes and a timestamp before any
matrix job starts, then scored against the executed results with a confusion matrix.
"""

from __future__ import annotations

from typing import Any

from . import llm, releasenotes
from .provenance import ai_generated, now_iso, observed

OUTCOMES = ("pass", "warn", "break")
SCHEMA = {
    "type": "object",
    "required": ["forecasts"],
    "properties": {"forecasts": {"type": "array", "items": {
        "type": "object", "required": ["cell", "predicted_outcome"],
        "properties": {"cell": {"type": "string"}, "predicted_outcome": {"enum": list(OUTCOMES)},
                       "confidence": {"type": "number"}, "reasoning": {"type": "string"}}}}},
}


def actual_outcome(results: set[str]) -> str | None:
    """Map executed matrix results for one cell (all Pythons) to pass / warn / break."""
    results = {r for r in results if r != "skipped"}
    if not results:
        return None
    if results & {"fail", "import-fail", "install-fail"}:
        return "break"
    if "warn" in results:
        return "warn"
    return "pass"


def collect_notes(cells: list[dict[str, Any]], offline: bool) -> list[dict[str, Any]]:
    out, seen = [], set()
    for c in cells:
        for pkg, target in (c.get("set_pins") or {}).items():
            if (pkg, target) in seen or c["type"] == "declare":
                continue
            seen.add((pkg, target))
            out.append(releasenotes.notes(pkg, c.get("from_version") if c["type"] == "upgrade" else None,
                                          target, offline=offline))
    return out


def _usage_summary(stage1: dict[str, Any]) -> str:
    by_dep: dict[str, set[str]] = {}
    for u in stage1["usage_locations"]:
        if u.get("dependency") and not u["in_tests"]:
            by_dep.setdefault(u["dependency"], set()).add(f"{u['file']}:{u['line']} {u['api']}")
    return "\n".join(f"- {d}: " + "; ".join(sorted(s)[:8]) for d, s in sorted(by_dep.items())) or "(none)"


def _baseline_summary(stage2: dict[str, Any]) -> str:
    lines = []
    for py, e in stage2["pythons"].items():
        rt, st = e.get("reference_tests") or {}, e.get("strict") or {}
        warns = "; ".join(w["message"][:120] for w in rt.get("warnings", []))
        lines.append(f"- Python {py}: {rt.get('outcome')} {rt.get('counts')}; strict run: {st.get('outcome')}; "
                     f"warnings: {warns or 'none'}")
    return "\n".join(lines)


def run_forecast(stage1: dict[str, Any], stage2: dict[str, Any], cells: list[dict[str, Any]],
                 resolutions: dict[str, Any], client: llm.LLM, *, offline: bool = False) -> dict[str, Any]:
    active = [c for c in cells if not c.get("skipped")]
    notes = collect_notes(active, offline)
    cell_lines = []
    for c in active:
        moved = {}
        for key, res in resolutions.items():
            if key.startswith(c["id"] + "|") and res.get("ok"):
                moved.update(res.get("co_upgrades") or {})
            elif key.startswith(c["id"] + "|"):
                moved["(resolver)"] = ["conflict", (res.get("error") or "")[:200]]
        cell_lines.append(f"- {c['id']} ({c['type']}): {c['reason']}; set {c.get('set_pins')}; remove "
                          f"{c.get('remove')}; add {c.get('add_unpinned')}; co-upgrades {moved or 'none'}"
                          + ("; NOTE: needs a code change that has NOT been made yet" if c.get("expects_code_change")
                             else ""))
    note_text = "\n\n".join(f"### {n['dependency']} {n.get('from_version') or ''} -> {n.get('to_version')}\n"
                            f"{n['excerpt'] or '(no release notes found)'}" for n in notes) or "(none)"
    prompt = llm.render_prompt("forecast", baseline=_baseline_summary(stage2), usage=_usage_summary(stage1),
                               cells="\n".join(cell_lines), notes=note_text)
    locked_at = now_iso()
    try:
        data, meta = client.complete_json(prompt, SCHEMA, task="forecast")
        forecasts = {f["cell"]: f for f in data["forecasts"] if f["cell"] in {c["id"] for c in active}}
        error = None
    except llm.LLMError as exc:
        forecasts, meta, error = {}, {}, str(exc)
    return {"locked_at": locked_at, "answered_at": meta.get("at"), "prompt_sha256": meta.get("prompt_sha256"),
            "response_sha256": meta.get("response_sha256"), "model_used": meta.get("model_used"),
            "cached": meta.get("cached"), "error": error, "forecasts": forecasts, "release_notes": notes,
            "cells_forecast": [c["id"] for c in active]}


def score(stage3: dict[str, Any], matrix: list[dict[str, Any]]) -> dict[str, Any]:
    results: dict[str, set[str]] = {}
    for r in matrix:
        results.setdefault(r["cell"], set()).add(r["result"])
    confusion = {p: {a: 0 for a in OUTCOMES} for p in OUTCOMES}
    rows = []
    for cell, f in stage3["forecasts"].items():
        actual = actual_outcome(results.get(cell, set()))
        if actual is None:
            continue
        confusion[f["predicted_outcome"]][actual] += 1
        rows.append({"cell": cell, "predicted": f["predicted_outcome"], "actual": actual,
                     "correct": f["predicted_outcome"] == actual, "confidence": f.get("confidence")})
    n = len(rows)
    correct = sum(r["correct"] for r in rows)
    # Treat "break" as the positive class: did the model warn about the changes that actually broke?
    tp = sum(1 for r in rows if r["predicted"] == "break" and r["actual"] == "break")
    fp = sum(1 for r in rows if r["predicted"] == "break" and r["actual"] != "break")
    fn = sum(1 for r in rows if r["predicted"] != "break" and r["actual"] == "break")
    return {"scored_cells": n, "correct": correct, "accuracy": round(correct / n, 3) if n else None,
            "break_precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "break_recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "confusion_matrix": confusion, "rows": rows,
            "note": f"small sample ({n} cells); read the confusion matrix, not just accuracy"}


def to_capsule(stage3: dict[str, Any], scoring: dict[str, Any] | None) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    lock = {"locked_at": stage3["locked_at"], "answered_at": stage3["answered_at"],
            "prompt_sha256": stage3["prompt_sha256"], "response_sha256": stage3["response_sha256"]}
    for cell, f in stage3["forecasts"].items():
        recs.append(ai_generated(source={"kind": "model", "model_used": stage3["model_used"], **lock},
                                 cell=cell, predicted_outcome=f["predicted_outcome"],
                                 confidence=f.get("confidence"), reasoning=f.get("reasoning")))
    if scoring:
        recs.append(observed(source={"kind": "forecast vs executed matrix"}, kind="forecast_score", **scoring))
    if stage3.get("error"):
        recs.append(observed(source={"kind": "model"}, kind="forecast_error", error=stage3["error"]))
    return recs
