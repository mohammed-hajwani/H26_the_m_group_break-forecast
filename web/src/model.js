// Fold a run's event list into the state the screen draws. Pure: the same events always give the same
// picture, so scrubbing a replay is just folding a shorter prefix.

export const STAGES = [
  { id: "inspect", n: 1, label: "Inspect", prov: "observed" },
  { id: "baseline", n: 2, label: "Baseline", prov: "executed" },
  { id: "cells", n: 4, label: "Plan cells", prov: "observed" },
  { id: "forecast", n: 3, label: "Blind forecast", prov: "ai" },
  { id: "probes", n: 8, label: "Probes", prov: "ai" },
  { id: "matrix", n: 5, label: "Matrix", prov: "executed" },
  { id: "diagnose", n: 6, label: "Diagnose", prov: "ai" },
  { id: "repair", n: 7, label: "Patch + gate", prov: "executed" },
  { id: "exit", n: 9, label: "Exit plan", prov: "executed" },
  { id: "capsule", n: 10, label: "Capsule", prov: "observed" },
];

export const PYTHONS = ["3.12", "3.13"];

// Map executed results onto the forecast's three outcomes.
export function outcomeOf(result) {
  if (!result || result === "skipped") return null;
  if (["fail", "import-fail", "install-fail"].includes(result)) return "break";
  if (result === "warn") return "warn";
  return "pass";
}

export function emptyModel() {
  return {
    run: null, target: null, stages: {}, stageInfo: {}, deps: [], risks: [], baseline: [], baselineRef: {},
    replacements: {}, cells: [], cellIndex: {}, forecast: {}, forecastLock: null, probes: [], jobs: {},
    score: null, diagnoses: {}, patches: {}, repairs: {}, exitPlan: null, capsule: null, ended: false,
    lastCell: null, lastKind: null,
  };
}

export function fold(events, upto = events.length) {
  const m = emptyModel();
  for (let i = 0; i < upto; i++) apply(m, events[i]);
  return m;
}

export function apply(m, e) {
  m.lastKind = e.kind;
  switch (e.kind) {
    case "run":
    case "resume":
      m.run = e.run || m.run;
      if (e.target && (e.target.url || e.target.path || e.target.repo)) m.target = e.target;
      break;
    case "stage":
      m.stages[e.stage] = e.status === "start" ? "running" : "done";
      m.stageInfo[e.stage] = { ...(m.stageInfo[e.stage] || {}), ...e };
      if (e.stage === "capsule" && e.capsule) m.capsule = e.capsule;
      break;
    case "dependency":
      m.deps.push(e);
      break;
    case "risk":
      m.risks.push(e);
      break;
    case "baseline_attempt":
      m.baseline.push(e);
      break;
    case "baseline_reference":
      m.baselineRef[e.python] = e;
      break;
    case "replacement":
      m.replacements[e.dependency] = e;
      break;
    case "cell":
      if (!(e.id in m.cellIndex)) {
        m.cellIndex[e.id] = m.cells.length;
        m.cells.push(e);
      }
      break;
    case "forecast":
      m.forecast[e.cell] = e;
      m.lastCell = e.cell;
      break;
    case "probe":
      m.probes.push(e);
      break;
    case "job":
      m.jobs[`${e.cell}|${e.python}`] = e;
      m.lastCell = e.cell;
      break;
    case "forecast_score":
      m.score = e;
      break;
    case "diagnosis":
      m.diagnoses[e.cell] = e;
      m.lastCell = e.cell;
      break;
    case "patch":
      (m.patches[e.cell] = m.patches[e.cell] || []).push(e);
      m.lastCell = e.cell;
      break;
    case "repair_done":
      m.repairs[e.cell] = e;
      break;
    case "exit_plan":
      m.exitPlan = e;
      break;
    case "end":
      m.ended = true;
      break;
    default:
      break;
  }
  if (e.stage && e.status === "done" && e.stage === "forecast" && e.locked_at) {
    m.forecastLock = { at: e.locked_at, sha: e.prompt_sha256 };
  }
  return m;
}

// Worst executed outcome across Pythons for a cell, or null while any Python is still pending.
export function cellOutcome(m, cellId) {
  const outs = PYTHONS.map((py) => m.jobs[`${cellId}|${py}`]).filter(Boolean);
  if (outs.length < PYTHONS.length) return null;
  const mapped = outs.map((j) => outcomeOf(j.result)).filter(Boolean);
  if (!mapped.length) return "skipped";
  if (mapped.includes("break")) return "break";
  if (mapped.includes("warn")) return "warn";
  return "pass";
}

export function stageCount(m, id) {
  switch (id) {
    case "inspect": return m.deps.length ? `${m.deps.length} deps · ${m.risks.length} risks` : "";
    case "baseline": {
      const refs = Object.values(m.baselineRef);
      if (!refs.length) return "";
      const c = refs[0].counts || {};
      return `${c.passed ?? "?"}/${(c.passed || 0) + (c.failed || 0) + (c.error || 0)} reference`;
    }
    case "cells": return m.cells.length ? `${m.cells.length} changes` : "";
    case "forecast": return Object.keys(m.forecast).length ? `${Object.keys(m.forecast).length} locked` : "";
    case "probes": {
      if (!m.probes.length) return "";
      return `${m.probes.filter((p) => p.usable).length}/${m.probes.length} usable`;
    }
    case "matrix": {
      const n = Object.keys(m.jobs).length;
      const total = m.stageInfo.matrix?.jobs || m.cells.length * PYTHONS.length;
      return n ? `${n}/${total} runs` : "";
    }
    case "diagnose": return Object.keys(m.diagnoses).length ? `${Object.keys(m.diagnoses).length} cells` : "";
    case "repair": {
      const tries = Object.values(m.patches).reduce((a, l) => a + l.length, 0);
      return tries ? `${tries} attempts` : "";
    }
    case "exit": return m.exitPlan ? m.exitPlan.verdict.replace("_", " ") : "";
    default: return "";
  }
}
