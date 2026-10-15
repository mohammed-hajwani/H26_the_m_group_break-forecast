"""Run events: a JSON-lines stream of what the pipeline is doing, for `bf serve` and anything else.

Live runs append to `runs/<id>/events.jsonl` as they go: each stage emits its events when its
stage file is written, and matrix jobs and patch attempts are emitted one by one as they finish.
For runs recorded before events existed, `synthesize()` rebuilds the same sequence from the stage
files, so a replay shows exactly what the run recorded. Every event names the file it came from.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .provenance import now_iso

_lock = threading.Lock()
_sink: Path | None = None
_seq = 0

STAGES = [
    ("inspect", "Inspect", 1), ("baseline", "Baseline", 2), ("cells", "Plan cells", 4),
    ("forecast", "Blind forecast", 3), ("probes", "Probes", 8), ("matrix", "Matrix", 5),
    ("diagnose", "Diagnose", 6), ("repair", "Patch + gate", 7), ("exit", "Exit plan", 9), ("capsule", "Capsule", 10),
]
FILE_ORDER = ["stage1.json", "stage2.json", "stage4.json", "stage3.json", "probes.json", "stage5.json",
              "stage67.json"]


def open_sink(run_root: Path) -> None:
    global _sink, _seq
    _sink = run_root / "events.jsonl"
    _seq = sum(1 for _ in _sink.open(encoding="utf-8")) if _sink.exists() else 0


def emit(kind: str, **data: Any) -> None:
    """Append one event. A no-op unless a sink is open, so library use stays side-effect free."""
    global _seq
    if _sink is None:
        return
    with _lock:
        _seq += 1
        line = json.dumps({"seq": _seq, "at": now_iso(), "kind": kind, **data}, default=str)
        with _sink.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def stage(name: str, status: str, **data: Any) -> None:
    emit("stage", stage=name, status=status, **data)


def emit_file(root: Path, fname: str, *, skip: tuple[str, ...] = ()) -> None:
    """Emit the events a finished stage file describes (live runs call this right after writing it)."""
    for e in file_events(root, fname):
        kind = e.pop("kind")
        if kind not in skip:
            emit(kind, **e)


def _load(root: Path, name: str) -> Any:
    p = root / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def synthesize(root: Path) -> list[dict[str, Any]]:
    """Rebuild the event sequence of a finished run from its stage files (replay of a recorded run)."""
    target = _load(root, "target.json") or (_load(root, "capsule.json") or {}).get("meta", {}).get("target", {})
    ev: list[dict[str, Any]] = [{"kind": "run", "source_file": "target.json", "target": target, "run": root.name}]
    for fname in FILE_ORDER:
        ev += file_events(root, fname, with_stage_marks=True)
    if (root / "capsule.json").exists():
        ev.append({"kind": "stage", "source_file": "capsule.json", "stage": "capsule", "status": "done",
                   "capsule": f"/runs/{root.name}/capsule.html"})
    ev.append({"kind": "end", "source_file": "capsule.json", "run": root.name})
    return ev


def file_events(root: Path, fname: str, *, with_stage_marks: bool = False) -> list[dict[str, Any]]:
    data = _load(root, fname)
    if data is None:
        return []
    ev: list[dict[str, Any]] = []

    def add(kind: str, **d: Any) -> None:
        ev.append({"kind": kind, "source_file": fname, **d})

    def mark(stage_name: str, status: str, **d: Any) -> None:
        if with_stage_marks:
            add("stage", stage=stage_name, status=status, **d)

    if fname == "stage1.json":
        mark("inspect", "start")
        for d in data["dependencies"]:
            add("dependency", name=d["name"], classification=d["classification"], spec=d.get("effective_spec"),
                latest=d.get("latest"))
        for r in data["risk_indicators"]:
            add("risk", dependency=r["dependency"], indicator=r["indicator"], detail=r["detail"])
        mark("inspect", "done", dependencies=len(data["dependencies"]), risks=len(data["risk_indicators"]))
    elif fname == "stage2.json":
        mark("baseline", "start")
        for py, e in data["pythons"].items():
            for a in e["attempts"]:
                add("baseline_attempt", python=py, name=a["name"], outcome=a["outcome"],
                    counts=(a.get("tests") or {}).get("counts"), cutoffs=a.get("cutoffs"))
            add("baseline_reference", python=py, reference=e.get("reference"),
                strict=(e.get("strict") or {}).get("outcome"), flaky=e.get("flaky_tests"),
                counts=(e.get("reference_tests") or {}).get("counts"))
        mark("baseline", "done", pins_reproducible=data["pins_reproducible"])
    elif fname == "stage4.json":
        mark("cells", "start")
        for name, rep in (data.get("replacements") or {}).items():
            add("replacement", dependency=name, replacement=rep.get("replacement"), replacement_kind=rep.get("kind"),
                verified=rep.get("verified"), model=(rep.get("source") or {}).get("model_used"))
        for c in data["cells"]:
            res = {k.split("|")[1]: {"ok": v["ok"], "co_upgrades": v.get("co_upgrades")}
                   for k, v in data["resolutions"].items() if k.split("|")[0] == c["id"]}
            add("cell", id=c["id"], type=c["type"], dependency=c["dependency"], reason=c["reason"],
                skipped=c.get("skipped"), expects_code_change=c.get("expects_code_change"), resolution=res)
        mark("cells", "done", cells=len(data["cells"]))
    elif fname == "stage3.json":
        mark("forecast", "start")
        for cid, f in data["forecasts"].items():
            add("forecast", cell=cid, predicted=f["predicted_outcome"], confidence=f.get("confidence"),
                reasoning=f.get("reasoning"), model=data.get("model_used"))
        mark("forecast", "done", locked_at=data.get("locked_at"), prompt_sha256=data.get("prompt_sha256"))
    elif fname == "probes.json":
        mark("probes", "start")
        for name, p in data.items():
            add("probe", name=name, usable=p.get("usable"), reason=p.get("reason"), author=p.get("author"),
                checks=p.get("what_it_checks"), cases=len(next(iter((p.get("reference") or {}).values()), {}) or {}))
        mark("probes", "done")
    elif fname == "stage5.json":
        mark("matrix", "start", jobs=len(data))
        for r in data:
            add("job", **job_payload(r))
        score = _load(root, "stage3_score.json")
        if score:
            add("forecast_score", correct=score["correct"], scored=score["scored_cells"],
                break_precision=score["break_precision"], break_recall=score["break_recall"],
                confusion=score["confusion_matrix"])
        mark("matrix", "done")
    elif fname == "stage67.json":
        mark("diagnose", "start")
        for d in data["diagnoses"]:
            add("diagnosis", cell=d["cell"], root_cause=d.get("root_cause"), fixable=d.get("code_fixable"),
                explanation=d.get("explanation"), model=(d.get("source") or {}).get("model_used"))
        mark("diagnose", "done")
        mark("repair", "start")
        for rep in data["repairs"]:
            for att in rep["attempts"]:
                add("patch", **patch_payload(rep["cell"], att))
            add("repair_done", cell=rep["cell"], verdict=rep["verdict"], attempt=rep.get("verified_attempt"))
        mark("repair", "done")
        comb = data.get("combined")
        if comb:
            mark("exit", "start")
            add("exit_plan", **exit_payload(comb))
            mark("exit", "done")
    return ev


def job_payload(r: dict[str, Any]) -> dict[str, Any]:
    normal = r.get("normal") or {}
    pr = r.get("probes") or {}
    return {"cell": r["cell"], "python": r["python"], "result": r["result"],
            "counts": normal.get("counts"), "strict": (r.get("strict") or {}).get("outcome"),
            "error": normal.get("error_excerpt") or r.get("reason"),
            "new_warnings": [w["message"] for w in r.get("new_warnings") or []][:3],
            "probe_divergences": pr.get("divergences", 0),
            "probe_cases": [{"case": f"{x['probe']}::{d['case']}", "before": d["reference"], "after": d["candidate"]}
                            for x in pr.get("results", []) for d in x["divergences"]][:6],
            "co_upgrades": r.get("co_upgrades")}


def patch_payload(cell: str, att: dict[str, Any]) -> dict[str, Any]:
    rules: dict[str, dict[str, bool]] = {}
    for py, e in (att.get("executed") or {}).items():
        for name, rr in (e.get("rules") or {}).items():
            rules.setdefault(name, {})[py] = rr["ok"]
    return {"cell": cell, "attempt": att["attempt"], "verdict": att.get("verdict"), "summary": att.get("summary"),
            "failed_rules": att.get("failed_rules", []),
            "static": {k: v["ok"] for k, v in (att.get("static") or {}).items()},
            "rules": rules, "diff": att.get("diff"), "model": (att.get("model") or {}).get("model_used"),
            "reason": att.get("reason")}


def exit_payload(comb: dict[str, Any]) -> dict[str, Any]:
    return {"verdict": comb["verdict"], "combined_from": comb["combined_from"],
            "orders_tried": comb.get("orders_tried"), "failed_rules": comb["failed_rules"],
            "results": {py: {"result": e.get("result"), "counts": (e.get("normal") or {}).get("counts")}
                        for py, e in (comb.get("executed") or {}).items()},
            "plan": (comb.get("exit_plan") or {}).get("reason")}
