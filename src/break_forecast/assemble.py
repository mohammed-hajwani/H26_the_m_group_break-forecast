"""Build the capsule from whatever stage outputs exist in a run directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import baseline, capsule, patch_gate
from .provenance import executed, observed
from .runctx import Run


def _load(run: Run, name: str) -> Any:
    path = run.root / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _resolution_summary(res: dict | None) -> dict | None:
    if not res:
        return None
    return {"ok": res["ok"], "levels_tried": [a["level"] for a in res["attempts"]],
            "co_upgrades": res.get("co_upgrades"), "freed_direct_pins": res.get("freed_direct_pins"),
            "lock_file": res.get("lock_file"), "resolved": res.get("resolved"), "error": res.get("error")}


def build(run: Run, target_info: dict[str, Any], commands: list[str]) -> dict[str, Any]:
    stage1 = _load(run, "stage1.json")
    stage2 = _load(run, "stage2.json")
    stage4 = _load(run, "stage4.json")
    stage5 = _load(run, "stage5.json")
    stage67 = _load(run, "stage67.json")
    stages = ["inspect"]
    extra: dict[str, Any] = {}
    limitations: list[dict[str, Any]] = []
    if stage2:
        stages.append("baseline")
        extra["baseline"], lims = baseline.to_capsule(stage2)
        limitations += lims
    if stage4:
        stages.append("cells")
        pythons = sorted({k.split("|")[1] for k in stage4["resolutions"]})
        extra["cells"] = []
        for c in stage4["cells"]:
            c = dict(c)
            c["resolution"] = {py: _resolution_summary(stage4["resolutions"].get(f"{c['id']}|{py}")) for py in pythons}
            extra["cells"].append(observed(source={"kind": "stage1 classification + uv resolver"}, **c))
        extra["replacement_suggestions"] = list(stage4.get("replacements", {}).values())
    stage3 = _load(run, "stage3.json")
    if stage3:
        from . import forecast

        stages.append("forecast")
        extra["forecast"] = forecast.to_capsule(stage3, _load(run, "stage3_score.json"))
        extra["release_notes"] = stage3.get("release_notes", [])
    if stage5:
        stages.append("matrix")
        extra["upgrade_matrix"] = [executed(source={"kind": "uv + pytest", "log": (r.get("normal") or {}).get("log")},
                                            **r) for r in stage5]
        for r in stage5:
            if r["result"] == "skipped":
                limitations.append(observed(source={"kind": "matrix"}, item=f"{r['cell']} on {r['python']}",
                                            reason=r["reason"]))
    if stage67:
        stages += ["diagnose", "repair"]
        diags, patches = patch_gate.to_capsule(stage67["diagnoses"], stage67["repairs"], stage67.get("combined"))
        extra["diagnoses"], extra["patches"] = diags, patches
        for rep in stage67["repairs"]:
            last = rep["attempts"][-1] if rep["attempts"] else {}
            if rep["verdict"] == "verified_tests":
                limitations.append(observed(
                    source={"kind": "patch gate"}, item=f"patch for {rep['cell']}",
                    reason="tests pass on every target Python, but differential probes show behaviour changes "
                           "(see behavioral_diffs); review before adopting"))
            elif rep["verdict"] != "verified":
                limitations.append(observed(
                    source={"kind": "patch gate"}, item=f"patch for {rep['cell']}",
                    reason=f"not verified after {len(rep['attempts'])} attempt(s): "
                           f"{', '.join(last.get('failed_rules') or []) or last.get('reason', 'no patch')}"))
        comb = stage67.get("combined")
        if comb and comb["verdict"] != "verified":
            limitations.append(observed(source={"kind": "patch gate"}, item="exit plan (all changes together)",
                                        reason=f"{comb['verdict']}: {', '.join(comb['failed_rules']) or 'see gate'}"))
        for d in stage67["diagnoses"]:
            if not d.get("code_fixable") and not d.get("failed"):
                limitations.append(observed(source={"kind": "diagnosis"}, item=d["cell"],
                                            reason="model judged this not fixable by a source change: "
                                                   + d.get("fix_strategy", "")[:300]))
    probe_set = _load(run, "probes.json")
    if probe_set:
        from . import probes

        stages.append("probes")
        extra["behavioral_diffs"] = probes.to_capsule(probe_set, stage5 or [],
                                                      (stage67 or {}).get("repairs", []))
        for name, p in probe_set.items():
            if not p.get("usable"):
                limitations.append(observed(source={"kind": "probe"}, item=f"probe {name}",
                                            reason=f"not usable, so not run: {str(p.get('reason'))[:300]}"))
    return capsule.build(stage1, target_info, stages, extra_sections=extra, limitations=limitations,
                         commands=commands)


def target_info(run: Run) -> dict[str, Any]:
    """Where the run's target came from: target.json (written at Stage 1), else an earlier capsule, else
    the cloned working copy's git remote and commit."""
    saved = _load(run, "target.json")
    if saved:
        return saved
    cap = _load(run, "capsule.json")
    if cap:
        return cap["meta"]["target"]
    from .runctx import run_cmd

    repo = run.work / "target"
    url = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo, timeout=30)
    head = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo, timeout=30)
    if url.ok:
        return {"kind": "git", "url": url.stdout.strip(), "commit": head.stdout.strip() if head.ok else None}
    return {"kind": "local", "path": str(Path(repo))}
