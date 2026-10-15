"""Command-line entry point: `bf <command> <repo-or-url>`."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import TARGET_PYTHONS, __version__, baseline, capsule, events, inspect_repo, target
from .runctx import Run


def _pythons(choice: str) -> list[str]:
    return list(TARGET_PYTHONS) if choice == "both" else [choice]


def _stage1(args: argparse.Namespace) -> tuple[Run, Path, dict, dict]:
    run = Run.create(args.runs_dir)
    events.open_sink(run.root)
    print(f"[bf] run directory: {run.root}")
    events.emit("run", run=run.root.name, target={"repo": args.repo})
    events.stage("inspect", "start")
    repo, target_info = target.acquire(args.repo, run)
    print(f"[bf] stage 1: inspecting {target_info.get('url') or target_info.get('path')}")
    run.write_json("target.json", target_info)
    events.emit("run", run=run.root.name, target=target_info)
    stage1 = inspect_repo.inspect(repo, run, offline=args.offline_cache)
    run.write_json("stage1.json", stage1)
    events.emit_file(run.root, "stage1.json")
    events.stage("inspect", "done", dependencies=len(stage1["dependencies"]),
                 risks=len(stage1["risk_indicators"]))
    return run, repo, target_info, stage1


def _finish(run: Run, cap: dict) -> int:
    errors = capsule.validate(cap)
    paths = capsule.write(cap, run.root)
    print(f"[bf] capsule: {paths['json']}")
    if errors:
        print("[bf] capsule validation FAILED:", *errors[:20], sep="\n  ")
        return 1
    print("[bf] capsule validates against the schema; every record has provenance")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    run, _, target_info, stage1 = _stage1(args)
    cap = capsule.build(stage1, target_info, ["inspect"], commands=[f"bf scan {args.repo}"])
    _print_scan_summary(cap)
    return _finish(run, cap)


def cmd_baseline(args: argparse.Namespace) -> int:
    run, repo, target_info, stage1 = _stage1(args)
    pythons = _pythons(args.python)
    print(f"[bf] stage 2: baseline on Python {', '.join(pythons)}")
    result = baseline.run_baseline(repo, stage1, run, pythons, offline=args.offline_cache, timeout=args.timeout)
    run.write_json("stage2.json", result)
    record, limitations = baseline.to_capsule(result)
    cap = capsule.build(stage1, target_info, ["inspect", "baseline"], extra_sections={"baseline": record},
                        limitations=limitations, commands=[f"bf baseline {args.repo} --python {args.python}"])
    _print_baseline_summary(result)
    return _finish(run, cap)


def _print_baseline_summary(result: dict) -> None:
    print(f"[bf] install: uv pip install {' '.join(result['install_args'])}")
    for py, entry in result["pythons"].items():
        for att in entry["attempts"]:
            counts = (att.get("tests") or {}).get("counts")
            extra = f" cutoffs={att['cutoffs']}" if att.get("cutoffs") else ""
            print(f"    {py} {att['name']:<13} {att['outcome']:<13} {counts or ''}{extra}")
        if entry["reference"]:
            print(f"    {py} reference={entry['reference']} flaky={entry['flaky_tests'] or 'none'} "
                  f"strict={entry['strict']['outcome']} {entry['strict']['counts']}")
            if entry["strict"].get("error_excerpt"):
                print(f"       strict run: {entry['strict']['error_excerpt'][:160]}")
            for w in entry["reference_tests"]["warnings"]:
                print(f"       warning: {w['category']}: {w['message'][:90]}")
    print(f"[bf] pins reproducible as written: {result['pins_reproducible']}")


def _stages12(args: argparse.Namespace) -> tuple[Run, Path, dict, dict, dict]:
    """Run stages 1-2, or reuse them from an earlier run directory with --resume."""
    if args.resume:
        run = Run(Path(args.resume).resolve())
        stage1 = json.loads((run.root / "stage1.json").read_text(encoding="utf-8"))
        stage2 = json.loads((run.root / "stage2.json").read_text(encoding="utf-8"))
        from . import assemble

        target_info = assemble.target_info(run)
        events.open_sink(run.root)
        events.emit("resume", run=run.root.name, target=target_info)
        print(f"[bf] resuming run {run.root} (stages 1-2 reused)")
        return run, run.work / "target", target_info, stage1, stage2
    run, repo, target_info, stage1 = _stage1(args)
    pythons = _pythons(args.python)
    print(f"[bf] stage 2: baseline on Python {', '.join(pythons)}")
    events.stage("baseline", "start", pythons=pythons)
    stage2 = baseline.run_baseline(repo, stage1, run, pythons, offline=args.offline_cache, timeout=args.timeout)
    run.write_json("stage2.json", stage2)
    events.emit_file(run.root, "stage2.json")
    events.stage("baseline", "done", pins_reproducible=stage2["pins_reproducible"])
    _print_baseline_summary(stage2)
    return run, repo, target_info, stage1, stage2


def cmd_matrix(args: argparse.Namespace) -> int:
    from . import cells, envs, llm, matrix, replacements

    run, repo, target_info, stage1, stage2 = _stages12(args)
    args._run = run
    pythons = [p for p in _pythons(args.python) if p in stage2["pythons"] and stage2["pythons"][p]["reference"]]
    if not pythons:
        print("[bf] no usable baseline; cannot run the matrix")
        return 1

    # --- Stage 4: plan + resolve -------------------------------------------------
    events.stage("cells", "start")
    reps: dict = {}
    if not args.no_ai and replacements.frozen_dependencies(stage1):
        print("[bf] stage 4: asking the model for replacement targets for frozen dependencies")
        client = llm.LLM(run.root / "llm", offline=args.offline_model, use_cache=not args.fresh_ai)
        reps = replacements.propose(stage1, client, offline=args.offline_cache)
        for name, rep in reps.items():
            status = "verified" if rep.get("verified") else "REJECTED"
            print(f"    {name} -> {rep.get('replacement')} ({rep.get('kind')}, {status}; "
                  f"model={rep['source'].get('model_used')})")
    ref_lock = stage2["pythons"][pythons[0]]["resolved"]
    planned = cells.plan(stage1, ref_lock, targets=args.targets, offline=args.offline_cache,
                         only=args.only, replacements=reps)
    direct = {d["name"] for d in stage1["dependencies"] if any(x["effective"] for x in d.get("declarations", []))}
    out_dir = cells.plan_run_dir(run)
    resolutions: dict = {}
    print(f"[bf] stage 4: {len(planned)} cells; resolving on {', '.join(pythons)}")
    for py in pythons:
        ref = stage2["pythons"][py]
        graph = cells.dependency_graph(envs.env_python(ref["reference_env"]), run.logs / f"graph-{py}.log")
        for cell in planned:
            if cell.skipped:
                continue
            res = cells.resolve(cell, py, ref["resolved"], direct, graph, out_dir)
            resolutions[(cell.id, py)] = res
            moved = ", ".join(f"{k} {a}->{b}" for k, (a, b) in res.get("co_upgrades", {}).items())
            print(f"    {py} {cell.id:<34} {'resolved' if res['ok'] else 'CONFLICT'}"
                  f"{' via ' + res['attempts'][-1]['level'] if res['ok'] and len(res['attempts']) > 1 else ''}"
                  f"{'; co-upgrades: ' + moved if moved else ''}")
    for cell in planned:
        if cell.skipped:
            print(f"    skipped {cell.id}: {cell.skipped}")
    run.write_json("stage4.json", {"cells": [c.to_dict() for c in planned], "replacements": reps,
                                   "resolutions": {f"{k[0]}|{k[1]}": v for k, v in resolutions.items()}})
    events.emit_file(run.root, "stage4.json")
    events.stage("cells", "done", cells=len(planned))

    # --- Stage 3: blind forecast, locked to disk before any matrix job runs -------
    stage3 = None
    if not args.no_ai:
        from . import forecast

        events.stage("forecast", "start")
        print("[bf] stage 3: blind forecast (model sees evidence + release notes, never test results)")
        client = llm.LLM(run.root / "llm", offline=args.offline_model, use_cache=not args.fresh_ai)
        stage3 = forecast.run_forecast(stage1, stage2, [c.to_dict() for c in planned],
                                       {f"{k[0]}|{k[1]}": v for k, v in resolutions.items()}, client,
                                       offline=args.offline_cache)
        run.write_json("stage3.json", stage3)
        for cid, f in stage3["forecasts"].items():
            print(f"    {cid:<34} predicted {f['predicted_outcome']:<5} ({f.get('confidence')})")
        if stage3["error"]:
            print(f"    forecast unavailable: {stage3['error'][:200]}")
        events.emit_file(run.root, "stage3.json")
        events.stage("forecast", "done", locked_at=stage3["locked_at"], prompt_sha256=stage3["prompt_sha256"],
                     error=stage3["error"])

    # --- Stage 8 (setup): write probes and record reference behaviour ---------------
    from . import probes

    changed = sorted({p for c in planned if not c.skipped for p in [*c.set_pins, *c.remove, *c.add_unpinned]})
    print(f"[bf] stage 8: writing differential probes for {len(changed)} changed packages")
    events.stage("probes", "start", packages=changed)
    probe_client = None if args.no_ai else llm.LLM(run.root / "llm", offline=args.offline_model,
                                                  use_cache=not args.fresh_ai)
    probe_set = probes.generate(stage1, stage2, repo, changed, probe_client, run.root, pythons)
    run.write_json("probes.json", probe_set)
    events.emit_file(run.root, "probes.json")
    events.stage("probes", "done")
    for name, p in probe_set.items():
        print(f"    probe {name:<16} {'ready' if p.get('usable') else 'UNUSABLE: ' + str(p.get('reason'))[:120]}"
              f"{' (' + str(len(next(iter(p['reference'].values()), {}))) + ' cases)' if p.get('usable') else ''}")

    # --- Stage 5: execute ----------------------------------------------------------
    print(f"[bf] stage 5: executing {len(planned) * len(pythons)} jobs with {args.workers} workers")
    events.stage("matrix", "start", jobs=len(planned) * len(pythons))

    def on_job(r: dict) -> None:
        events.emit("job", **events.job_payload(r))
        print(f"    done {r['python']} {r['cell']}: {r['result']}"
              + (f" ({r['probes']['divergences']} probe divergences)"
                 if (r.get("probes") or {}).get("divergences") else ""))

    results = matrix.execute(planned, resolutions, stage2["pythons"], repo, run, pythons, workers=args.workers,
                             timeout=args.timeout, keep_envs=args.keep_envs, probe_set=probe_set, progress=on_job)
    run.write_json("stage5.json", results)
    print("[bf] matrix (result, normal passed/failed, strict outcome):")
    for line in matrix.grid(results, pythons):
        print("    " + line)
    if stage3 and stage3["forecasts"]:
        from . import forecast

        scoring = forecast.score(stage3, results)
        run.write_json("stage3_score.json", scoring)
        print(f"[bf] forecast scored: {scoring['correct']}/{scoring['scored_cells']} correct; "
              f"break precision {scoring['break_precision']}, recall {scoring['break_recall']}")
        events.emit("forecast_score", correct=scoring["correct"], scored=scoring["scored_cells"],
                    break_precision=scoring["break_precision"], break_recall=scoring["break_recall"],
                    confusion=scoring["confusion_matrix"])
    events.stage("matrix", "done")

    from . import assemble

    (run.root / "stage67.json").unlink(missing_ok=True)  # a new matrix invalidates earlier repairs
    rc = _finish(run, assemble.build(run, target_info, [" ".join(["bf", *sys.argv[1:]])]))
    if args.no_ai or getattr(args, "command", "") != "run":
        events.stage("capsule", "done", capsule=f"/runs/{run.root.name}/capsule.html")
        events.emit("end", run=run.root.name)
    return rc


def cmd_repair(args: argparse.Namespace) -> int:
    """Stages 6-7 on an existing matrix run: diagnose failing cells, then gate model patches."""
    from . import assemble, diagnose, llm, patch_gate

    run = Run(Path(args.resume).resolve())
    stage1 = json.loads((run.root / "stage1.json").read_text(encoding="utf-8"))
    stage2 = json.loads((run.root / "stage2.json").read_text(encoding="utf-8"))
    stage4 = json.loads((run.root / "stage4.json").read_text(encoding="utf-8"))
    stage5 = json.loads((run.root / "stage5.json").read_text(encoding="utf-8"))
    repo = run.work / "target"
    pythons = sorted({r["python"] for r in stage5})
    probes_path = run.root / "probes.json"
    probe_set = json.loads(probes_path.read_text(encoding="utf-8")) if probes_path.exists() else None
    if probe_set is None:
        print("[bf] note: no probes.json in this run; patches can reach at most `verified_tests`")
    client = llm.LLM(run.root / "llm", offline=args.offline_model, use_cache=not args.fresh_ai)
    events.open_sink(run.root)
    todo = diagnose.cells_to_diagnose(stage4["cells"], stage5)
    if args.only:
        todo = [(c, r) for c, r in todo if c["dependency"] in args.only or c["id"] in args.only]
    print(f"[bf] stage 6: diagnosing {len(todo)} cells")
    events.stage("diagnose", "start", cells=[c["id"] for c, _ in todo])
    diagnoses = []
    for cell, recs in todo:
        d = diagnose.diagnose_cell(cell, recs, stage1, repo, client)
        diagnoses.append(d)
        events.emit("diagnosis", cell=d["cell"], root_cause=d.get("root_cause"), fixable=d.get("code_fixable"),
                    explanation=d.get("explanation"), model=d["source"].get("model_used"))
        print(f"    {cell['id']:<28} {d['root_cause']:<16} fixable={d['code_fixable']} "
              f"(model={d['source'].get('model_used', '-')}) {d['explanation'][:110]}")
    events.stage("diagnose", "done")

    print(f"[bf] stage 7: patching code-fixable cells (max {args.max_repairs} attempts each)")
    events.stage("repair", "start")
    repairs = []
    by_cell = {c["id"]: (c, r) for c, r in todo}
    for d in diagnoses:
        if not d.get("code_fixable") or d["cell"] == "all-latest":
            continue
        cell, recs = by_cell[d["cell"]]
        rep = patch_gate.repair_cell(cell, d, recs, stage1, stage2, stage4["resolutions"], repo, run, client,
                                     pythons, max_attempts=args.max_repairs, timeout=args.timeout,
                                     progress=lambda msg: print(f"    {msg}"), probe_set=probe_set)
        repairs.append(rep)
        events.emit("repair_done", cell=rep["cell"], verdict=rep["verdict"], attempt=rep.get("verified_attempt"))
        for att in rep["attempts"]:
            print(f"    {rep['cell']} attempt {att['attempt']}: {att['verdict']}"
                  + (f" failed rules: {', '.join(att['failed_rules'])}" if att.get("failed_rules") else ""))
    events.stage("repair", "done")

    # With --only, merge into the earlier repair results instead of discarding the other cells.
    previous = run.root / "stage67.json"
    if args.only and previous.exists():
        old = json.loads(previous.read_text(encoding="utf-8"))
        redone = {d["cell"] for d in diagnoses}
        diagnoses = [d for d in old["diagnoses"] if d["cell"] not in redone] + diagnoses
        repairs = [r for r in old["repairs"] if r["cell"] not in redone] + repairs
        print(f"[bf] merged with earlier results: {len(repairs)} repaired cells in total")

    combined, combo = None, None
    verified = [r for r in repairs if r["verdict"] in ("verified", "verified_tests")]
    if verified:
        from . import cells, envs

        combo = cells.exit_plan_cell(stage4["cells"], stage5, {r["cell"] for r in verified})
    if combo:
        events.stage("exit", "start", plan=combo.reason)
        print(f"[bf] stage 7: gating the whole exit plan with all verified patches: {combo.reason}")
        direct = {d["name"] for d in stage1["dependencies"]
                  if any(x["effective"] for x in d.get("declarations", []))}
        combo_res = {}
        for py in pythons:
            ref = stage2["pythons"][py]
            graph = cells.dependency_graph(envs.env_python(ref["reference_env"]))
            combo_res[f"{combo.id}|{py}"] = cells.resolve(combo, py, ref["resolved"], direct, graph,
                                                          cells.plan_run_dir(run))
        all_latest_recs = [r for r in stage5 if r["type"] == "all-latest"]
        combined = patch_gate.combine(verified, combo.to_dict(), stage2, combo_res, repo, run, pythons,
                                      all_latest_recs or [{"python": py, "result": "fail"} for py in pythons],
                                      timeout=args.timeout, probe_set=probe_set)
        combined["exit_plan"] = combo.to_dict()
        combined["resolution"] = {k: {"ok": v["ok"], "co_upgrades": v.get("co_upgrades"), "error": v.get("error"),
                                      "resolved": v.get("resolved")} for k, v in combo_res.items()}
        for py, e in combined["executed"].items():
            print(f"    {py}: {e['result']} {(e.get('normal') or {}).get('counts')}")
        print(f"    exit plan: {combined['verdict']}"
              + (f" failed rules: {', '.join(combined['failed_rules'])}" if combined["failed_rules"] else ""))
        events.emit("exit_plan", **events.exit_payload(combined))
        events.stage("exit", "done")
    run.write_json("stage67.json", {"diagnoses": diagnoses, "repairs": repairs, "combined": combined})
    rc = _finish(run, assemble.build(run, assemble.target_info(run), [" ".join(["bf", *sys.argv[1:]])]))
    events.stage("capsule", "done", capsule=f"/runs/{run.root.name}/capsule.html")
    events.emit("end", run=run.root.name)
    return rc


def _serve(args: argparse.Namespace) -> int:
    from . import server

    server.serve(Path(args.runs_dir), Path(args.project_dir).resolve(), port=args.port, open_browser=args.open)
    return 0


def cmd_capsule(args: argparse.Namespace) -> int:
    """Re-render capsule.md and capsule.html from a run directory's capsule.json."""
    src = Path(args.run_dir) / "capsule.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    out = Path(args.output) if args.output else Path(args.run_dir)
    paths = capsule.write(data, out)
    print(f"[bf] wrote {paths['html']} and {paths['md']}")
    errors = capsule.validate(data)
    if errors:
        print("[bf] note: capsule has validation errors:", *errors[:10], sep="\n  ")
        return 1
    return 0


def cmd_verify_capsule(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.capsule).read_text(encoding="utf-8"))
    errors = capsule.validate(data)
    if errors:
        print("INVALID", *errors[:50], sep="\n  ")
        return 1
    print("valid: schema ok, every record has provenance")
    if not args.rerun:
        return 0
    from . import rerun

    run = Run.create(args.runs_dir)
    print(f"[bf] re-running the capsule's verified results in {run.root}")
    result = rerun.rerun(data, run, timeout=args.timeout)
    run.write_json("rerun.json", result)
    n_ok = sum(c["reproduced"] for c in result["checks"])
    print(f"[bf] reproduced {n_ok}/{len(result['checks'])} recorded results"
          + ("" if result["all_reproduced"] else " - see rerun.json for the differences"))
    return 0 if result["all_reproduced"] else 2


def cmd_run(args: argparse.Namespace) -> int:
    """Whole pipeline in one command: stages 1-2, 4, 3, 8 (setup), 5, then 6-7, then the capsule."""
    args.resume = None
    rc = cmd_matrix(args)
    run = getattr(args, "_run", None)
    if run is None:
        return rc or 1
    if args.no_ai:
        print("[bf] --no-ai: stages 3, 6, 7 and the AI probes were skipped (recorded in the capsule)")
        return rc
    args.resume = str(run.root)
    rc = cmd_repair(args)
    print(f"[bf] done. Open {run.root / 'capsule.html'}")
    return rc


def cmd_llm_check(args: argparse.Namespace) -> int:
    """Call each configured model backend on its own and report whether it answers correctly."""
    from . import llm

    status = 0
    chosen = [b for b in llm.all_backends() if b.name == "fallback" or not args.offline]
    for backend in chosen:
        client = llm.LLM(Path(args.runs_dir) / "llm-check")
        client.backends = [backend]
        label = f"{backend.name}: {backend.model} via {backend.endpoint}"
        if backend.kind == "gemini" and not backend.api_key:
            print(f"[bf] {label} -> SKIPPED (no BF_API_KEY / GEMINI_API_KEY set)")
            continue
        try:
            data, meta = client.complete_json(llm.render_prompt("llm_check"), task="llm-check")
            ok = isinstance(data, dict) and data.get("answer") == 391
            print(f"[bf] {label} -> {'OK' if ok else 'ANSWERED BUT WRONG'} "
                  f"({meta['duration_s']}s) reply={json.dumps(data)}")
            status |= 0 if ok else 1
        except llm.LLMError as exc:
            print(f"[bf] {label} -> FAILED: {exc}")
            status = 1
    return status


def _print_scan_summary(cap: dict) -> None:
    print(f"[bf] {cap['problem']['summary']}")
    width = max(len(d["name"]) for d in cap["dependencies"]) if cap["dependencies"] else 10
    for d in cap["dependencies"]:
        print(f"    {d['name']:<{width}}  {d.get('effective_spec') or '-'!s:<12} "
              f"latest={d.get('latest') or '-'!s:<10} {', '.join(d['classification'])}")
    counts = Counter(r["indicator"] for r in cap["risk_indicators"])
    print("[bf] risk indicators: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bf", description="break-forecast: evidence-gated dependency change testing")
    p.add_argument("--version", action="version", version=f"break-forecast {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Stage 1: inspect manifests, usage and risk indicators")
    scan.add_argument("repo", help="local path or git URL of the target repository")
    scan.add_argument("--runs-dir", default="runs")
    scan.add_argument("--offline-cache", action="store_true", help="use only cached PyPI/GitHub data")
    scan.set_defaults(func=cmd_scan)

    base = sub.add_parser("baseline", help="Stages 1-2: inspect, then build the reference baseline")
    base.add_argument("repo")
    base.add_argument("--runs-dir", default="runs")
    base.add_argument("--offline-cache", action="store_true")
    base.add_argument("--python", choices=["3.12", "3.13", "both"], default="both")
    base.add_argument("--timeout", type=float, default=300, help="per test-run timeout in seconds")
    base.set_defaults(func=cmd_baseline)

    def matrix_options(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--runs-dir", default="runs")
        sp.add_argument("--offline-cache", action="store_true", help="use only cached PyPI/GitHub data")
        sp.add_argument("--offline-model", action="store_true", help="use only the local fallback model")
        sp.add_argument("--no-ai", action="store_true", help="skip all model calls (recorded as skipped)")
        sp.add_argument("--fresh-ai", action="store_true", help="ignore cached model answers and ask again")
        sp.add_argument("--python", choices=["3.12", "3.13", "both"], default="both")
        sp.add_argument("--targets", choices=["latest", "same-major", "both"], default="latest")
        sp.add_argument("--only", nargs="+", help="limit to these dependencies")
        sp.add_argument("--workers", type=int, default=3)
        sp.add_argument("--timeout", type=float, default=300, help="per test-run timeout in seconds")
        sp.add_argument("--keep-envs", action="store_true", help="keep each cell's virtualenv after running")

    run_p = sub.add_parser("run", help="the whole pipeline in one command (stages 1-10)")
    run_p.add_argument("repo", help="git URL or local path of the target repository")
    matrix_options(run_p)
    run_p.add_argument("--max-repairs", type=int, default=3)
    run_p.set_defaults(func=cmd_run)

    mat = sub.add_parser("matrix", help="Stages 1-5 and probe setup: plan cells, forecast, run the matrix")
    mat.add_argument("repo", nargs="?", default=None)
    mat.add_argument("--resume", metavar="RUN_DIR", help="reuse stages 1-2 from an earlier `bf baseline` run")
    matrix_options(mat)
    mat.set_defaults(func=cmd_matrix)

    rep = sub.add_parser("repair", help="Stages 6-7: diagnose failing cells and gate model-written patches")
    rep.add_argument("--resume", metavar="RUN_DIR", required=True, help="a run directory with a finished matrix")
    rep.add_argument("--max-repairs", type=int, default=3)
    rep.add_argument("--only", nargs="+", help="limit to these dependencies or cell ids")
    rep.add_argument("--offline-model", action="store_true")
    rep.add_argument("--fresh-ai", action="store_true")
    rep.add_argument("--timeout", type=float, default=300)
    rep.set_defaults(func=cmd_repair)

    srv = sub.add_parser("serve", help="local web UI: watch live runs and replay recorded ones")
    srv.add_argument("--port", type=int, default=8770)
    srv.add_argument("--runs-dir", default="runs")
    srv.add_argument("--open", action="store_true", help="open the browser")
    srv.add_argument("--project-dir", default=".", help="folder with .env; relative repo paths resolve here")
    srv.set_defaults(func=lambda a: _serve(a))

    cap = sub.add_parser("capsule", help="Stage 10: re-render capsule.html / capsule.md from a run's capsule.json")
    cap.add_argument("run_dir")
    cap.add_argument("-o", "--output", help="output directory (default: the run directory)")
    cap.set_defaults(func=cmd_capsule)

    check = sub.add_parser("llm-check", help="check that the primary and fallback models answer")
    check.add_argument("--offline", action="store_true", help="only check the local fallback model")
    check.add_argument("--runs-dir", default="runs")
    check.set_defaults(func=cmd_llm_check)

    verify = sub.add_parser("verify-capsule", help="validate a capsule; --rerun re-executes its verified results")
    verify.add_argument("capsule")
    verify.add_argument("--rerun", action="store_true", help="rebuild envs from the capsule and re-run the tests")
    verify.add_argument("--runs-dir", default="runs")
    verify.add_argument("--timeout", type=float, default=300)
    verify.set_defaults(func=cmd_verify_capsule)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # show progress live even when piped to a log
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
