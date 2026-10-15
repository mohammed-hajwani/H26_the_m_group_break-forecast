# Dependency Exit Capsule layout

Files written to `runs/<id>/`: `capsule.json` (validated against
`src/break_forecast/schema/capsule.schema.json`), `capsule.md` (summary) and `capsule.html` (self-contained viewer).

| Section | Provenance | Content |
|---|---|---|
| `meta` | observed | tool version, target (URL + commit), Python versions, stages run, host |
| `problem` | observed | counts-based summary of what Stage 1 found |
| `manifests` | observed | files read, effective install files, install/test commands |
| `dependencies` | observed | per dependency: specs per manifest, pin, latest, release dates, classification |
| `manifest_drift` | observed | disagreements between manifests, commented-out requirements |
| `import_map` | observed | import name → distribution (`packages_distributions()`) |
| `risk_indicators` | observed | frozen, stale, outdated pin, unused, undeclared, private API, monkeypatch, no wheel, drift |
| `usage_locations` | observed | file, line, API, kind (import / call / attribute / monkeypatch) |
| `baseline` | executed | attempts (as written / reconstructed), cutoffs, reference tests, strict run, resolved set |
| `release_notes` | observed | fetched release-note excerpts between current and target versions |
| `forecast` | ai_generated + observed | locked predictions per cell; `forecast_score` record with confusion matrix |
| `cells` | observed | planned changes with per-Python resolution (co-upgrades, resolved package set) |
| `upgrade_matrix` | executed | cell × Python: result, normal/strict runs, per-test diff, new warnings, probe divergences |
| `replacement_suggestions` | ai_generated (+ nested observed check) | proposed replacement, migration outline, API mapping |
| `diagnoses` | ai_generated | root cause, explanation, evidence cited, fixability |
| `patches` | ai_generated (+ nested executed gate) | diff, search/replace edits, verdict, every gate rule per Python |
| `behavioral_diffs` | ai_generated probes + observed diffs | probe code, and divergences found in the matrix and gate |
| `reproducibility` | observed | commands, uv version |
| `limitations` | observed | everything not verified, not fixable or not run |

`bf verify-capsule capsule.json --rerun` uses `meta.target`, `baseline.pythons.*.resolved`,
`cells[].resolution.*.resolved` and `patches[].edits` to re-execute the verified results from the capsule alone.
