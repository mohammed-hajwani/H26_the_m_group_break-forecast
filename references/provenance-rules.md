# Provenance rules

Every record in the capsule has a `provenance` field with exactly one of these values;
`bf verify-capsule` fails if any record is missing it.

| Tag | Meaning | Examples |
|---|---|---|
| `observed` | Read deterministically from files or public metadata, with a source (file:line, or URL + fetch time) | manifests, PyPI release dates, GitHub archived flag, AST usage sites, release notes, resolver output, probe diffs, forecast score |
| `executed` | Produced by actually installing packages and running the test suite | baseline attempts, matrix results, patch-gate verdicts |
| `ai_generated` | Output of the open-weight model | replacement proposals, forecasts, diagnoses, patch diffs, probe scripts |

1. **The model never supplies facts.** Versions, dates, package existence and compatibility come from
   PyPI, GitHub or uv. A package name proposed by the model is checked on PyPI (or against the standard
   library) and the check is attached as a nested `observed` record.
2. **AI output is never merged into evidence.** A patch is `ai_generated`; its gate verdict is a nested
   `executed` record. A probe script is `ai_generated`; the differences it reveals are `observed`.
3. **The forecast is blind.** It is written to `stage3.json` (with prompt/response SHA-256 and a timestamp)
   before any matrix job starts, and scored afterwards with a confusion matrix.
4. **Every model call is logged** in `runs/<id>/llm/` with prompt, response, the model that actually
   answered, SHA-256 hashes and whether the answer came from cache. Failed calls go to `llm/failures.jsonl`.
5. **Failures are reported, not hidden**: irreproducible pins, unfixable cells, rejected patches, unusable
   probes and skipped AI steps are listed under `limitations`.
