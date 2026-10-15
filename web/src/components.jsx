import { useRef } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import {
  Check, X, Circle, CircleDot, Loader2, CheckCircle2, ExternalLink, Minus, Clock,
} from "lucide-react";
import { STAGES, PYTHONS, cellOutcome, outcomeOf, stageCount } from "./model.js";

gsap.registerPlugin(useGSAP);

const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const PROV_LABEL = { observed: "observed", executed: "executed", ai: "AI-generated" };
export const Tag = ({ kind }) => <span className={`tag ${kind}`}>{PROV_LABEL[kind]}</span>;

const RESULT_LABEL = {
  pass: "pass", warn: "warn", flaky: "flaky", fail: "fail", "import-fail": "won't import",
  "install-fail": "won't install", skipped: "skipped", break: "break",
};

// Shorten at a word boundary, never mid-word.
function clip(text, n) {
  if (!text || text.length <= n) return text || "";
  const cut = text.slice(0, n);
  return `${cut.slice(0, cut.lastIndexOf(" ") > 0 ? cut.lastIndexOf(" ") : n)}…`;
}

function countsText(c) {
  if (!c) return "";
  const total = (c.passed || 0) + (c.failed || 0) + (c.error || 0);
  return `${c.passed ?? 0}/${total} tests`;
}

// ---------------------------------------------------------------------------- stage rail
export function StageRail({ model }) {
  return (
    <aside className="panel rail" aria-label="Pipeline stages">
      <div className="panel-h"><h2>Pipeline</h2></div>
      <ol className="stages">
        {STAGES.map((s) => {
          const st = model.stages[s.id];
          const cls = st === "running" ? "running" : st === "done" ? "done" : "";
          return (
            <li key={s.id} className={`stage ${cls}`} aria-current={st === "running" ? "step" : undefined}>
              <span className="ic" aria-hidden="true">
                {st === "running" ? <Loader2 size={15} className="spin" />
                  : st === "done" ? <CheckCircle2 size={15} color="var(--pass)" />
                    : <Circle size={13} />}
              </span>
              <span className="name"><span className="n">{s.n}</span>{s.label}</span>
              <span className="count">{stageCount(model, s.id) || (st === "running" ? "working…" : "")}</span>
            </li>
          );
        })}
      </ol>
      <div className="legend">
        <div><Tag kind="observed" /> read from files and PyPI</div>
        <div><Tag kind="executed" /> installs and test runs</div>
        <div><Tag kind="ai" /> Gemma 4 output</div>
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------- matrix
// Strict (-W error::DeprecationWarning) only matters when it differs from the baseline's strict run.
function strictNote(job, refStrict) {
  if (!job?.strict || !refStrict || job.strict === refStrict) return "";
  return job.strict === "pass" ? " · strict now passes" : " · strict now fails";
}

function ResultSlot({ job, predicted, refStrict }) {
  const ref = useRef(null);
  const seen = useRef({ mounted: false, had: Boolean(job) });
  useGSAP(() => {
    // The final state is pure CSS (result shown, ghost hidden). The flip only plays when a result arrives
    // while someone is watching: not on first paint, deep links or hidden tabs, and never for reduced motion.
    const arrived = seen.current.mounted && !seen.current.had && Boolean(job);
    seen.current = { mounted: true, had: Boolean(job) };
    if (!arrived || reducedMotion() || document.hidden) return;
    const q = gsap.utils.selector(ref);
    // Signature move: the locked forecast ghost gives way and the executed result flips in.
    gsap.timeline()
      .fromTo(q(".ghost"), { autoAlpha: 1, scale: 1 },
        { autoAlpha: 0, scale: 0.92, filter: "blur(2px)", duration: 0.22, ease: "power2.in" })
      .from(q(".result"),
        { autoAlpha: 0, yPercent: 45, rotateX: -75, transformPerspective: 400, filter: "blur(3px)",
          duration: 0.6, ease: "expo.out" }, "-=0.04")
      .fromTo(q(".flash"), { opacity: 0.7 }, { opacity: 0, duration: 1.1, ease: "power2.out" }, "<");
  }, { scope: ref, dependencies: [job?.result, job?.cell], revertOnUpdate: true });

  const r = job?.result;
  return (
    <div className={`slot${job ? " has-result" : ""}`} ref={ref}>
      {predicted ? (
        <span className="chip ghost" title="Gemma's locked forecast; replaced when the tests finish">
          {predicted}?<small>forecast</small>
        </span>
      ) : !job ? <span className="chip empty"><Minus size={14} /></span> : null}
      {job && (
        <>
          <span className="flash" style={{ background: `var(--${outcomeOf(r) === "break" ? "fail" : outcomeOf(r) || "skip"}-bg)` }} />
          <span className={`chip result ${r}`}>
            {RESULT_LABEL[r] || r}
            <small>{job.counts ? countsText(job.counts) : "not run"}{strictNote(job, refStrict)}</small>
          </span>
        </>
      )}
    </div>
  );
}

export function Matrix({ model, selected, onSelect, hasRun, emptyActions }) {
  const cells = model.cells;
  if (!hasRun || !cells.length) {
    return (
      <section className="panel" aria-label="Matrix">
        <div className="panel-h"><h2>Forecast → result matrix</h2></div>
        {hasRun ? (
          <div className="empty-state">
            <h3>Planning the changes…</h3>
            <p>The matrix fills in once Stage 4 has planned the cells. Stages 1 and 2 (inspection and the baseline) run first.</p>
          </div>
        ) : (
          <div className="empty-state">
            <h3>Watch a dependency upgrade get tested</h3>
            <p>
              Replay a recorded run to see each change land: Gemma's forecast appears first as a ghost, then the real
              test result on Python 3.12 and 3.13 replaces it. Or start a live run on any Python repository.
            </p>
            <div className="row">{emptyActions}</div>
          </div>
        )}
      </section>
    );
  }
  const locked = model.forecastLock;
  return (
    <section className="panel" aria-label="Matrix">
      <div className="panel-h">
        <h2>Forecast → result matrix</h2>
        <span className="sub">
          {locked ? <>forecast locked {locked.at?.slice(11, 19)} · sha {locked.sha?.slice(0, 8)}</> : "awaiting forecast"}
        </span>
      </div>
      <div className="matrix-wrap">
        <table className="matrix">
          <thead>
            <tr>
              <th>Change</th>
              <th>Forecast <Tag kind="ai" /></th>
              {PYTHONS.map((py) => <th key={py} className="py">Python {py} <Tag kind="executed" /></th>)}
              <th>Called it?</th>
            </tr>
          </thead>
          <tbody>
            {cells.map((c) => {
              const f = model.forecast[c.id];
              const out = cellOutcome(model, c.id);
              const verdict = !f ? null : out == null ? "wait" : out === f.predicted ? "hit" : "miss";
              return (
                <tr key={c.id} aria-selected={selected === c.id} onClick={() => onSelect(c.id)}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(c.id)} tabIndex={0}>
                  <td>
                    <div className="change">
                      <span className="id">{c.id}</span>
                      <span className="why">{c.skipped ? `skipped: ${c.skipped}` : c.reason}</span>
                    </div>
                  </td>
                  <td>{f ? <span className="chip forecast">{f.predicted}<small>{Math.round((f.confidence ?? 0) * 100)}% sure</small></span>
                    : <span className="chip empty"><Clock size={14} /></span>}</td>
                  {PYTHONS.map((py) => (
                    <td key={py}><ResultSlot job={model.jobs[`${c.id}|${py}`]} predicted={f?.predicted}
                      refStrict={model.baselineRef[py]?.strict} /></td>
                  ))}
                  <td>
                    {verdict === "hit" && <span className="verdict hit"><Check size={14} /> called</span>}
                    {verdict === "miss" && <span className="verdict miss"><X size={14} /> missed ({out})</span>}
                    {verdict === "wait" && <span className="verdict wait"><CircleDot size={13} /> pending</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {model.score && (
        <div className="scoreline">
          <span>Forecast <b>{model.score.correct}/{model.score.scored}</b> correct</span>
          <span>break precision <b>{model.score.break_precision ?? "–"}</b></span>
          <span>break recall <b>{model.score.break_recall ?? "–"}</b></span>
          <span>{Object.values(model.jobs).filter((j) => j.probe_divergences).length} runs with behaviour changes the tests missed</span>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------- patches
const VERDICT_LABEL = { verified: "verified", verified_tests: "tests verified", rejected: "rejected", no_patch: "no answer" };

export function Patches({ model, onSelect }) {
  const cells = Object.keys(model.patches);
  const diagnosed = Object.values(model.diagnoses);
  if (!cells.length && !diagnosed.length && !model.exitPlan) return null;
  return (
    <section className="panel" aria-label="Patches and gate">
      <div className="panel-h">
        <h2>Patches and the evidence gate</h2>
        <span className="sub"><Tag kind="ai" /> diff · <Tag kind="executed" /> verdict</span>
      </div>
      <div className="matrix-wrap">
      <table className="patches">
        <thead><tr><th>Change</th><th>Attempts</th><th>Final</th><th>Gate result (or diagnosis)</th></tr></thead>
        <tbody>
          {diagnosed.filter((d) => !d.fixable).map((d) => (
            <tr key={`d-${d.cell}`} onClick={() => onSelect(d.cell)} style={{ cursor: "pointer" }}>
              <td className="mono">{d.cell}</td>
              <td colSpan={2}><span className="att no_patch">not code-fixable</span> <Tag kind="ai" /></td>
              <td className="rules"><Tag kind="ai" /> diagnosis, no patch: {d.root_cause?.replace("_", " ")}: {clip(d.explanation, 140)}</td>
            </tr>
          ))}
          {cells.map((cell) => {
            const list = model.patches[cell];
            const last = list[list.length - 1];
            const final = model.repairs[cell]?.verdict;
            return (
              <tr key={cell} onClick={() => onSelect(cell)} style={{ cursor: "pointer" }}>
                <td className="mono">{cell}</td>
                <td>
                  <div className="attempts">
                    {list.map((a) => (
                      <span key={a.attempt} className={`att ${a.verdict}`} title={a.summary || ""}>
                        {a.verdict === "verified" ? <Check size={12} /> : a.verdict === "rejected" ? <X size={12} /> : <Minus size={12} />}
                        #{a.attempt} {VERDICT_LABEL[a.verdict] || a.verdict}
                      </span>
                    ))}
                  </div>
                </td>
                <td>{final ? <span className={`att ${final}`}>{VERDICT_LABEL[final]}</span> : <span className="verdict wait"><Loader2 size={13} className="spin" /> judging</span>}</td>
                <td className="rules">{last.failed_rules?.length ? `failed: ${last.failed_rules.join(", ")}` : "every rule passed on 3.12 and 3.13"}</td>
              </tr>
            );
          })}
          {model.exitPlan && (
            <tr className="exitrow">
              <td><b>Exit plan</b></td>
              <td className="rules">{model.exitPlan.plan}</td>
              <td><span className={`att ${model.exitPlan.verdict}`}>{VERDICT_LABEL[model.exitPlan.verdict]}</span></td>
              <td className="rules">
                {Object.entries(model.exitPlan.results || {}).map(([py, r]) => `${py}: ${countsText(r.counts)}`).join(" · ")}
                {model.exitPlan.failed_rules?.length ? ` · open: ${model.exitPlan.failed_rules.join(", ")}` : ""}
                {model.capsule && <> · <a href={model.capsule} target="_blank" rel="noreferrer">capsule <ExternalLink size={11} /></a></>}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------- inspector
export function Inspector({ model, cellId, logs, mode }) {
  const cell = cellId ? model.cells.find((c) => c.id === cellId) : null;
  return (
    <aside className="panel inspector" aria-label="Evidence">
      <div className="panel-h"><h2>{cell ? "Evidence" : "Run"}</h2>{cell && <span className="sub mono">{cell.id}</span>}</div>
      {cell ? <CellEvidence model={model} cell={cell} /> : <RunEvidence model={model} />}
      {mode === "live" && (
        <div className="sec">
          <h3>Console</h3>
          <div className="console" aria-live="off">{logs.slice(-60).join("\n") || "waiting for output…"}</div>
        </div>
      )}
    </aside>
  );
}

function RunEvidence({ model }) {
  const refs = Object.values(model.baselineRef);
  const attempts = model.baseline;
  return (
    <>
      <div className="sec">
        <h3>Target <Tag kind="observed" /></h3>
        <p className="mono">{model.target?.url || model.target?.path || model.target?.repo || "–"}</p>
        {model.target?.commit && <p className="mono" style={{ color: "var(--faint)" }}>commit {model.target.commit.slice(0, 10)}</p>}
      </div>
      {model.deps.length > 0 && (
        <div className="sec">
          <h3>Found without hints <Tag kind="observed" /></h3>
          <div className="kv">
            {["frozen", "upgradable", "unused", "undeclared"].map((k) => (
              <FragmentKV key={k} k={k} v={model.deps.filter((d) => d.classification.includes(k)).map((d) => d.name).join(", ") || "none"} />
            ))}
          </div>
        </div>
      )}
      {attempts.length > 0 && (
        <div className="sec">
          <h3>Baseline <Tag kind="executed" /></h3>
          <div className="kv">
            {attempts.map((a, i) => (
              <FragmentKV key={i} k={`${a.python} ${a.name === "as_written" ? "as written" : "rebuilt"}`}
                v={`${RESULT_LABEL[a.outcome] || a.outcome} · ${countsText(a.counts)}`} />
            ))}
            {refs.map((r) => <FragmentKV key={`s${r.python}`} k={`${r.python} strict`} v={RESULT_LABEL[r.strict] || r.strict} />)}
          </div>
        </div>
      )}
      {Object.keys(model.replacements).length > 0 && (
        <div className="sec">
          <h3>Replacements proposed <Tag kind="ai" /></h3>
          <div className="kv">
            {Object.values(model.replacements).map((r) => (
              <FragmentKV key={r.dependency} k={r.dependency} v={`→ ${r.replacement} (${r.replacement_kind}${r.verified ? ", exists" : ", rejected"})`} />
            ))}
          </div>
        </div>
      )}
      {model.probes.length > 0 && (
        <div className="sec">
          <h3>Probes</h3>
          <div className="kv">
            {model.probes.map((p) => <FragmentKV key={p.name} k={p.name} v={p.usable ? `${p.cases} cases` : "unusable"} />)}
          </div>
        </div>
      )}
      <div className="sec"><p style={{ color: "var(--faint)" }}>Select a row in the matrix to see its evidence.</p></div>
    </>
  );
}

const FragmentKV = ({ k, v }) => (<><span>{k}</span><span>{v}</span></>);

function CellEvidence({ model, cell }) {
  const f = model.forecast[cell.id];
  const d = model.diagnoses[cell.id];
  const patches = model.patches[cell.id] || [];
  const moved = Object.values(cell.resolution || {})[0]?.co_upgrades || {};
  return (
    <>
      <div className="sec">
        <h3>Change <Tag kind="observed" /></h3>
        <p>{cell.reason}</p>
        {Object.keys(moved).length > 0 && (
          <p style={{ fontSize: 12, color: "var(--muted)" }}>
            also moves: {Object.entries(moved).map(([k, [a, b]]) => `${k} ${a ?? "∅"}→${b ?? "∅"}`).join(", ")}
          </p>
        )}
      </div>
      {f && (
        <div className="sec">
          <h3>Forecast <Tag kind="ai" /></h3>
          <p><b>{f.predicted}</b> · {Math.round((f.confidence ?? 0) * 100)}% sure{f.model ? ` · ${f.model}` : ""}</p>
          {f.reasoning && <p style={{ color: "var(--muted)", fontSize: 12.5 }}>{f.reasoning}</p>}
        </div>
      )}
      {PYTHONS.map((py) => {
        const j = model.jobs[`${cell.id}|${py}`];
        if (!j) return null;
        return (
          <div className="sec" key={py}>
            <h3>Python {py} <Tag kind="executed" /></h3>
            <div className="kv">
              <span>result</span><span><span className={`chip result ${j.result}`} style={{ height: 22, minWidth: 0 }}>{RESULT_LABEL[j.result] || j.result}</span></span>
              <span>tests</span><span>{countsText(j.counts) || "–"}</span>
              <span>strict</span><span>{RESULT_LABEL[j.strict] || j.strict || "–"}
                {model.baselineRef[py]?.strict && <span style={{ color: "var(--faint)" }}> (baseline: {RESULT_LABEL[model.baselineRef[py].strict] || model.baselineRef[py].strict})</span>}</span>
            </div>
            {j.error && <pre className="ex">{j.error}</pre>}
            {j.new_warnings?.length > 0 && <pre className="ex">{j.new_warnings.join("\n")}</pre>}
            {j.probe_cases?.length > 0 && (
              <div>
                <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>Behaviour changed (probes, observed):</p>
                {j.probe_cases.map((pc) => (
                  <pre className="ex" key={pc.case} style={{ marginBottom: 4 }}>
                    {pc.case}{"\n"}<span className="diff-before">before {JSON.stringify(pc.before)}</span>{"\n"}<span className="diff-after">after  {JSON.stringify(pc.after)}</span>
                  </pre>
                ))}
              </div>
            )}
          </div>
        );
      })}
      {d && (
        <div className="sec">
          <h3>Diagnosis <Tag kind="ai" /></h3>
          <p><b>{d.root_cause?.replace("_", " ")}</b> · {d.fixable ? "fixable in code" : "not fixable in code"}</p>
          <p style={{ color: "var(--muted)", fontSize: 12.5 }}>{d.explanation}</p>
        </div>
      )}
      {patches.map((p) => (
        <div className="sec" key={p.attempt}>
          <h3>Patch attempt {p.attempt} <Tag kind="ai" /> <span className={`att ${p.verdict}`}>{VERDICT_LABEL[p.verdict] || p.verdict}</span></h3>
          {p.summary && <p style={{ fontSize: 12.5 }}>{p.summary}</p>}
          {p.failed_rules?.length > 0 && <p className="rules">gate: failed {p.failed_rules.join(", ")}</p>}
          {p.reason && <p className="rules">{p.reason}</p>}
          {p.diff && (
            <details><summary>diff ({p.diff.split("\n").length} lines)</summary><pre className="ex">{p.diff}</pre></details>
          )}
        </div>
      ))}
    </>
  );
}
