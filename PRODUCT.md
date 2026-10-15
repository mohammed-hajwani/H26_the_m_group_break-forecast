# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React + Vite single-page app for the `bf serve` frontend (user's choice). GSAP is bundled locally through npm so the
UI works with no internet. The built assets ship inside the Python package so `bf serve` (Python stdlib HTTP server)
needs no Node at run time; Node is only needed to rebuild the UI. The rest of break-forecast is a Python 3.11+ CLI.

## Users

Primary: open-source Python maintainers who keep a repository's dependencies healthy. They run break-forecast on their
own repos and use the frontend as a daily tool to start runs, watch them progress, and review what broke, what the AI
proposed and what the gate accepted. Secondary (confirmed context, not the design target): hackathon judges watching a
demo of the same tool.

## Product Purpose

Test dependency changes (upgrades, replacements, removals, declarations) on Python 3.12 and 3.13 with real installs and
test runs, repair breakage with open-weight Gemma 4 patches, and only call a fix verified when execution evidence says
so. Output is a re-runnable Dependency Exit Capsule. The frontend makes a run observable while it happens and lets a
maintainer replay recorded or cached runs.

## Positioning

Evidence over claims: every fact is tagged observed, executed or AI-generated; AI patches pass an 11-rule gate (tamper
checks, no regressions, strict warnings run, behaviour probes) before they count; the blind forecast is hash-locked
before tests run; capsules re-execute from themselves (`bf verify-capsule --rerun`).

## Operating Context

Runs locally on a maintainer's laptop (Windows or Linux) next to a terminal. A full live run takes minutes (env builds,
test runs, model calls; the hosted model is often overloaded, so a local Ollama fallback is common). Cached model
answers make re-runs faster. Each run writes `runs/<timestamp>/` with stage JSON files, logs and `capsule.html`.

## Capabilities and Constraints

- Pipeline stages: 1 inspect, 2 baseline (+ era reconstruction), 4 plan cells, 3 blind forecast, 8 probes,
  5 matrix (cell × Python × normal/strict), 6 diagnose, 7 patch + gate, 9 exit plan, 10 capsule.
- Results vocabulary: pass, warn, fail, import-fail, install-fail, skipped, flaky; patch verdicts verified,
  verified_tests, rejected, no_patch.
- Provenance vocabulary is fixed: observed, executed, ai_generated.
- The frontend must show real data only: live runs stream actual events; replays read recorded run files and must be
  labelled as replays.
- Local only (127.0.0.1); no accounts, no telemetry.

## Brand Commitments

Name `break-forecast` (lowercase). Provenance labels and the meaning of observed / executed / AI-generated are fixed
across CLI, capsule and UI.

## Evidence on Hand

Real recorded run of github.com/rishrek/LinkWatch at `runs/20261015-151158/` and `docs/sample-capsule/` (15 deps,
24 matrix runs, forecast 8/12, 4 gated patches, exit plan 10/10 on both Pythons, rerun 12/12). Second fixture repo at
`tests/fixtures/mini-repo`. No users, testimonials or adoption numbers exist; none may be invented.

## Product Principles

1. Evidence first: never show an AI claim as a fact; provenance is always visible.
2. Honest failure: rejected patches, unusable probes and limitations are first-class, not hidden.
3. Observable progress: a long run must always show what it is doing and why it is waiting.
4. Reproducible: everything shown can be traced to a file in the run directory.
