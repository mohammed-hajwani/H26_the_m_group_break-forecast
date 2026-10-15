---
name: break-forecast
description: >-
  Tests dependency changes in a Python repository and repairs what breaks, with execution
  evidence. Inspects manifests and code to find frozen, outdated, unused and undeclared
  dependencies; rebuilds a working baseline when old pins no longer install; runs every
  upgrade, replacement, removal and declaration on Python 3.12 and 3.13 (normal and
  warnings-as-errors); uses an open-weight Gemma 4 model to propose replacements, diagnose
  failures and write patches that are only accepted by a test-running, tamper-checking gate;
  and writes a Dependency Exit Capsule (JSON, Markdown, HTML). Use when a maintainer wants to
  know which dependency upgrades are safe, migrate off an unmaintained package, or prepare a
  verified dependency-upgrade plan.
license: Apache-2.0
compatibility: >-
  Needs uv (Python 3.12 and 3.13 are provisioned through it), git, network access to PyPI and
  GitHub, and an open-weight model endpoint (Gemma 4 via the Gemini API with BF_API_KEY, or a
  local Ollama server). Target repositories must use pytest.
metadata:
  author: break-forecast contributors
  version: "0.1.0"
  model: gemma-4-31b-it (primary), gemma-4-26b-a4b-it, gemma4:12b-it-qat (local)
---

# break-forecast

Evidence-gated dependency change testing for Python repositories. Everything the tool reports
is tagged `observed` (read from files/PyPI), `executed` (came from running installs and tests)
or `ai_generated` (model output). AI output is a proposal until an executed check confirms it.

## Before you start

1. Make sure `uv` is installed (`uv --version`). If not: `pip install uv`.
2. Configure a model. Copy `.env.example` to `.env` and set `BF_API_KEY` (Google AI Studio key
   for Gemma 4), or run Ollama locally with `ollama pull gemma4:12b-it-qat`.
3. Check the model answers: `uv run scripts/bf.py llm-check`.
   Without any model, add `--no-ai` to `run`/`matrix`: replacement and repair steps are then skipped
   and reported as such.

## Run the whole pipeline (most common)

```bash
uv run scripts/bf.py run <git-url-or-local-path>
```

This clones/copies the target (the original is never modified), then runs:
inspect → baseline (+ reconstruction) → plan cells → matrix → diagnose → gated patches → capsule.
It prints a run directory such as `runs/20261015-130256/`. Open `capsule.html` in that folder.

Useful options: `--python 3.12|3.13|both`, `--only <dep> ...`, `--max-repairs N`, `--workers N`,
`--offline-model` (local model only), `--no-ai`, `--fresh-ai` (ignore cached model answers).

## Run stages separately

| Command | Stages | Output |
|---|---|---|
| `bf.py scan <repo>` | 1 inspect | risk indicators, usage map, manifest drift |
| `bf.py baseline <repo>` | 1-2 | reference baseline, reconstruction if pins are not reproducible |
| `bf.py matrix --resume <run>` | 4-5 | cells, resolver co-upgrades, matrix |
| `bf.py repair --resume <run>` | 6-7 | diagnoses, patches with gate verdicts |
| `bf.py capsule <run>` | 10 | re-render `capsule.html` / `capsule.md` |
| `bf.py verify-capsule <run>/capsule.json` | – | schema + provenance check |
| `bf.py serve --open` | – | local web UI on 127.0.0.1:8770: replay recorded runs or watch a live one |

## How to report results to the user

1. Lead with the matrix: which changes pass, warn (tests pass but new warnings) or break.
2. For each patch, quote the gate verdict, not the model's summary. Only `verified_tests` patches
   passed every rule on every Python. `rejected` patches list the rules they failed.
3. Say which items were judged not fixable in code and why (these are in `limitations`).
4. Never describe an `ai_generated` field as verified unless its gate/verification sub-record says so.

## Rules the tool enforces (do not work around them)

- Model patches may only edit existing non-test `.py` files. Tests, `conftest.py`, test config and
  dependency manifests are protected; dependency changes are applied by the tool itself.
- Patches that add skip/xfail markers, silence warnings, stub modules via `sys.modules` or add
  broad `except` blocks are rejected, as are patches that make any previously passing test fail.
- Target environments are Python 3.12 and 3.13 only.

See [references/gate-rules.md](references/gate-rules.md) for every gate rule,
[references/provenance-rules.md](references/provenance-rules.md) for the tagging rules and
[references/capsule-schema.md](references/capsule-schema.md) for the capsule layout.

## Edge cases

- **Pins no longer install or pass**: the baseline is rebuilt with each transitive dependency limited
  to the release date of the pinned package that requires it; both attempts are kept in the capsule.
- **Model API overloaded (HTTP 500/503)**: calls retry with back-off, then fall back from the 31B to the
  26B model, then to local Ollama. Successful answers are cached by prompt hash, so re-runs are cheap.
- **No model available**: AI steps are recorded as skipped with the reason; nothing is invented.
- **Repository without pytest tests**: baseline reports `no-tests`; the matrix still records install results.
