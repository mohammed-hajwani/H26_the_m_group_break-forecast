import { useEffect, useMemo, useState } from "react";
import { Play, Pause, RotateCcw, Radio, Square, ExternalLink, AlertTriangle, X } from "lucide-react";
import { useRun } from "./useRun.js";
import { StageRail, Matrix, Patches, Inspector } from "./components.jsx";

const LINKWATCH = "https://github.com/rishrek/LinkWatch";
const SPEEDS = [1, 2, 4, 8];

function describe(e) {
  if (!e) return "";
  switch (e.kind) {
    case "stage": return `${e.stage} ${e.status === "start" ? "started" : "finished"}`;
    case "dependency": return `found ${e.name} (${e.classification.join(", ")})`;
    case "risk": return `${e.dependency}: ${e.indicator}`;
    case "baseline_attempt": return `baseline ${e.python} ${e.name}: ${e.outcome}`;
    case "replacement": return `${e.dependency} → ${e.replacement} proposed`;
    case "cell": return `planned ${e.id}`;
    case "forecast": return `forecast ${e.cell}: ${e.predicted}`;
    case "probe": return `probe ${e.name}: ${e.usable ? "ready" : "unusable"}`;
    case "job": return `${e.python} ${e.cell}: ${e.result}${e.probe_divergences ? ` · ${e.probe_divergences} behaviour changes` : ""}`;
    case "forecast_score": return `forecast scored ${e.correct}/${e.scored}`;
    case "diagnosis": return `diagnosed ${e.cell}: ${e.root_cause}`;
    case "patch": return `patch ${e.cell} #${e.attempt}: ${e.verdict}`;
    case "repair_done": return `${e.cell}: ${e.verdict}`;
    case "exit_plan": return `exit plan: ${e.verdict}`;
    case "end": return "run complete";
    default: return e.kind;
  }
}

export default function App() {
  const run = useRun();
  const { model, mode, runs, runId, events, playhead } = run;
  const [showLive, setShowLive] = useState(false);
  const [repo, setRepo] = useState(LINKWATCH);
  const [opts, setOpts] = useState({ offline_model: false, no_ai: false, fresh_ai: false });
  const [picked, setPicked] = useState(null); // a row the user clicked; otherwise follow the action

  const selected = picked ?? model.lastCell;
  useEffect(() => setPicked(null), [runId]);

  // Deep links: ?run=<id> replays a run, &at=<n> opens it paused at event n, &live=1 follows a live run.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const id = q.get("run");
    if (!id) return;
    if (q.get("live")) run.follow(id); else run.openReplay(id, q.get("at"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the address bar pointing at what is on screen, so a view can be shared or reopened.
  useEffect(() => {
    if (!runId) return;
    const q = new URLSearchParams({ run: runId });
    if (mode === "live") q.set("live", "1");
    window.history.replaceState(null, "", `?${q}`);
  }, [runId, mode]);

  const current = runs.find((r) => r.id === runId);
  const recorded = useMemo(() => runs.filter((r) => r.has_capsule || r.live), [runs]);

  const openRun = (id) => {
    if (!id) return;
    const r = runs.find((x) => x.id === id);
    if (r?.live) run.follow(id); else run.openReplay(id);
  };

  const start = async (e) => {
    e.preventDefault();
    if (await run.startLive({ repo, ...opts })) setShowLive(false);
  };

  const lastEvent = events[playhead - 1];
  const target = model.target?.url || model.target?.path || current?.target;
  const commit = model.target?.commit?.slice(0, 7) || current?.commit;

  const emptyActions = (
    <>
      {recorded[0] && (
        <button className="btn primary" type="button" onClick={() => run.openReplay(recorded[0].id)}>
          <Play size={15} /> Replay latest run
        </button>
      )}
      <button className="btn" type="button" onClick={() => setShowLive(true)}><Radio size={15} /> Run live…</button>
    </>
  );

  return (
    <div className="app">
      <header>
        <div className="topbar">
          <div className="brand"><b>break-forecast</b><span>dependency changes, proven</span></div>
          <label className="visually-hidden" htmlFor="run-pick">Run</label>
          <select id="run-pick" className="select" value={runId || ""} onChange={(e) => openRun(e.target.value)}>
            <option value="" disabled>{recorded.length ? "Choose a recorded run…" : runId ? "Loading runs…" : "No recorded runs yet"}</option>
            {runId && !recorded.some((r) => r.id === runId) && <option value={runId}>{runId}</option>}
            {recorded.map((r) => (
              <option key={r.id} value={r.id}>
                {r.live ? "LIVE · " : ""}{r.id} · {(r.target || "").replace(/^https?:\/\/(www\.)?github\.com\//, "")}
                {r.exit_plan ? ` · exit plan ${r.exit_plan.replace("_", " ")}` : ""}
              </option>
            ))}
          </select>
          <button className="btn" type="button" aria-expanded={showLive} onClick={() => setShowLive((v) => !v)}>
            <Radio size={15} /> Run live…
          </button>

          {mode === "replay" && (
            <div className="transport" aria-label="Replay controls">
              {run.playing
                ? <button className="btn iconbtn" type="button" onClick={run.pause} aria-label="Pause"><Pause size={16} /></button>
                : <button className="btn iconbtn" type="button" onClick={run.play} aria-label="Play"><Play size={16} /></button>}
              <button className="btn iconbtn" type="button" onClick={run.restart} aria-label="Restart replay"><RotateCcw size={15} /></button>
              <input className="scrub" type="range" min={0} max={events.length} value={playhead}
                onChange={(e) => run.seek(Number(e.target.value))} aria-label="Replay position" />
              <div className="seg" role="group" aria-label="Replay speed">
                {SPEEDS.map((s) => (
                  <button key={s} type="button" aria-pressed={run.speed === s} onClick={() => run.setSpeed(s)}>{s}×</button>
                ))}
              </div>
            </div>
          )}
          {mode === "live" && run.liveState === "running" && (
            <div className="transport">
              <button className="btn danger" type="button" onClick={run.stopLive}><Square size={14} /> Stop run</button>
            </div>
          )}
          {model.capsule && (
            <a className="btn" href={model.capsule} target="_blank" rel="noreferrer">Open capsule <ExternalLink size={14} /></a>
          )}
        </div>

        {showLive && (
          <form className="livepanel" onSubmit={start}>
            <input className="input" value={repo} onChange={(e) => setRepo(e.target.value)} aria-label="Repository"
              placeholder="Git URL or local path of a Python repository" required />
            <label className="check"><input type="checkbox" checked={opts.offline_model}
              onChange={(e) => setOpts({ ...opts, offline_model: e.target.checked })} /> local model only</label>
            <label className="check"><input type="checkbox" checked={opts.no_ai}
              onChange={(e) => setOpts({ ...opts, no_ai: e.target.checked })} /> skip AI steps</label>
            <button className="btn primary" type="submit" disabled={run.loading}>
              <Play size={15} /> {run.loading ? "Starting…" : "Start run"}
            </button>
          </form>
        )}

        <div className="status" role="status">
          {mode === "idle" && <><span className="dot" /><span>Pick a recorded run to replay, or run break-forecast live on a repository. Cached model answers make repeat runs fast.</span></>}
          {mode === "replay" && (
            <>
              <span className={`dot ${playhead >= events.length ? "done" : "replay"}`} />
              <span className="label">Replay of recorded run</span>
              <span className="mono">{runId}</span>
              {target && <span>· {target.replace(/^https?:\/\//, "")}{commit ? ` @ ${commit}` : ""}</span>}
              <span>· event {playhead}/{events.length}</span>
              <span className="tick">{describe(lastEvent)}</span>
            </>
          )}
          {mode === "live" && (
            <>
              <span className={`dot ${run.liveState === "running" ? "live" : "done"}`} />
              <span className="label">{run.liveState === "running" ? "Live run" : run.liveState === "finished" ? "Live run finished" : "Live run stopped"}</span>
              <span className="mono">{runId}</span>
              <span className="tick">{run.logs[run.logs.length - 1] || describe(lastEvent) || "starting…"}</span>
            </>
          )}
        </div>
      </header>

      {run.error && (
        <div className="error" role="alert">
          <AlertTriangle size={16} />
          <span style={{ flex: 1 }}>{run.error}</span>
          <button className="btn ghost iconbtn" type="button" onClick={() => run.setError(null)} aria-label="Dismiss"><X size={14} /></button>
        </div>
      )}

      <main className="work">
        <StageRail model={model} />
        <div className="center">
          <Matrix model={model} selected={selected} onSelect={setPicked} hasRun={mode !== "idle"} emptyActions={emptyActions} />
          <Patches model={model} onSelect={setPicked} />
        </div>
        {mode !== "idle" && <Inspector model={model} cellId={selected} logs={run.logs} mode={mode} />}
      </main>
    </div>
  );
}
