# break-forecast

**Evidence-gated dependency change testing and repair for Python repositories — an Agent Skill + CLI
powered by the open-weight Gemma 4 model.**

> *We predict which dependency changes will break your repo, then run them to prove it, and only call a
> fix verified if the evidence says so.*

Hackathon track: **Track 2 – Maintenance Agent Skill** · License: **Apache-2.0** ·
Target repository: [rishrek/LinkWatch](https://github.com/rishrek/LinkWatch)

---

## 1. Problem

Open-source projects pin dependencies that quietly rot: pins stop installing, packages are abandoned,
manifests drift apart, and an upgrade breaks code in ways the tests may not even notice. Maintainers usually
find out only after something fails, and AI suggestions alone cannot be trusted to fix it.

## 2. Solution overview

`break-forecast` runs one maintenance task end to end — **testing and repairing dependency changes** — and
writes a **Dependency Exit Capsule** in which every field is tagged:

| Tag | Meaning |
|---|---|
| 🔍 `observed` | read from files, PyPI, GitHub or the resolver, with its source |
| ▶️ `executed` | came from actually installing packages and running the test suite |
| 🤖 `ai_generated` | output of the model — a proposal until an executed check confirms it |

```
1 Inspect ─► 2 Baseline (+era reconstruction) ─► 4 Plan cells (resolver) ─► 3 Blind forecast ─► 8 Probe setup
      ─► 5 Matrix (3.12 + 3.13, normal + strict) ─► 6 Diagnose ─► 7 Patch + evidence gate ─► 9 Exit plan ─► 10 Capsule
```

| Stage | What happens | AI? | Provenance |
|---|---|---|---|
| 1 Inspect | parse every manifest + Makefile/Dockerfile/CI; PyPI & GitHub metadata; AST usage map; import→distribution map; classify each dep as upgradable / frozen / unused / undeclared | – | observed |
| 2 Baseline | install the pins as written on 3.12 and 3.13; if that fails, **reconstruct the era**: cap each transitive dependency at the release date of the pinned package that needs it; run tests twice (flakes) + a strict `-W error::DeprecationWarning` run | – | executed |
| 4 Plan cells | one cell per change (`upgrade`, `replace`, `remove`, `declare`, `all-latest`); resolve each with `uv pip compile`, freeing packages only as far as the resolver needs, and record the forced **co-upgrades** | replacement target | observed (+ AI) |
| 3 Blind forecast | Gemma predicts pass / warn / break for every cell from evidence + fetched release notes; prediction is **hashed and time-stamped before any test runs** | ✓ | ai_generated |
| 8 Probes | tool-written generic probe (imports, CLI `--help`) + Gemma-written probes that call the repo's own functions; reference output recorded | ✓ | ai_generated code, observed output |
| 5 Matrix | every cell × Python × (normal, strict) in a fresh env; per-test diff vs baseline; new warnings; probe diff | – | executed |
| 6 Diagnose | root cause + whether a source change can fix it | ✓ | ai_generated |
| 7 Patch + gate | Gemma writes search/replace edits; the **gate** (11 rules) decides; failures fed back for up to 3 attempts | ✓ | ai_generated diff, executed verdict |
| 9 Exit plan | all upgrades + verified replacements + passing cleanups + all verified patches, resolved and gated together | – | executed |
| 10 Capsule | `capsule.json` (schema-validated), `capsule.html` viewer, `capsule.md`; `verify-capsule --rerun` re-executes it | – | mixed, tagged |

## Quick start (Windows)

```bat
git clone https://github.com/mohammed-hajwani/H26_the_m_group_break-forecast break-forecast
cd break-forecast
run.bat
```

`run.bat` creates the environment on first use (needs [uv](https://docs.astral.sh/uv/)), installs Python 3.12 and
3.13 for the target environments, and opens the web UI at http://127.0.0.1:8770/. The recorded LinkWatch run in
`examples/runs/` is ready to replay there. Other commands go straight through to the CLI:

```bat
run.bat run https://github.com/rishrek/LinkWatch
run.bat run tests\fixtures\mini-repo --no-ai
run.bat verify-capsule runs\20261015-151158\capsule.json
```

For the AI steps, copy `.env.example` to `.env` and add your Google AI Studio key (`BF_API_KEY`).

## 3. Setup

Requirements: **Python 3.11+**, **git**, **[uv](https://docs.astral.sh/uv/)**, internet access to PyPI/GitHub, and a model
(Gemma 4 API key or a local Ollama). Target environments are always **Python 3.12 and 3.13** (provisioned by uv).

```bash
git clone https://github.com/mohammed-hajwani/H26_the_m_group_break-forecast break-forecast
cd break-forecast
pip install uv                      # if uv is not installed yet
uv python install 3.12 3.13
uv venv .venv --python 3.12
uv pip install --python .venv -e ".[dev]"
cp .env.example .env                # then put your Google AI Studio key in BF_API_KEY
```

Optional local fallback model (needs a recent Ollama):

```bash
ollama pull gemma4:12b-it-qat
```

Check the models answer:

```bash
bf llm-check
```

## 4. Usage

Whole pipeline on the organizer's repository (one command):

```bash
bf run https://github.com/rishrek/LinkWatch
```

The run directory (e.g. `runs/20261015-143203/`) then contains `capsule.html` — open it in a browser.

| Command | Stages |
|---|---|
| `bf scan <repo>` | 1 – risk indicators, usage map, manifest drift (no execution) |
| `bf baseline <repo>` | 1–2 |
| `bf matrix <repo>` / `bf matrix --resume <run>` | 1–5 + probe setup |
| `bf repair --resume <run>` | 6–7 + exit plan |
| `bf capsule <run>` | re-render `capsule.html` / `capsule.md` |
| `bf verify-capsule <run>/capsule.json [--rerun]` | schema + provenance check; `--rerun` rebuilds the envs and re-runs the verified results |

Useful options: `--python 3.12|3.13|both`, `--only <dep> …`, `--max-repairs N`, `--workers N`, `--no-ai`,
`--offline-model` (local model only), `--fresh-ai` (ignore cached model answers), `--offline-cache`.

**Web UI:** `bf serve --open` starts a local UI on http://127.0.0.1:8770/. It replays any recorded run, showing the
stages ticking, forecast ghosts being overwritten by executed results, and patches passing or failing the gate. It
can also start a live run and stream it as it happens (cached model answers make reruns fast). The UI is prebuilt
into `src/break_forecast/web_dist/` (React 18.3.1 MIT, GSAP 3.13.0 under the standard no-charge license, bundled
locally, so it works offline; lucide-react ISC). Node is needed only to rebuild it: `cd web && npm ci && npm run build`.
The server binds to 127.0.0.1 only and refuses requests from other sites (Host/Origin check, JSON-only POST).

As an **Agent Skill**, the repository root is the skill (`SKILL.md` + `scripts/` + `references/` + `assets/`):
`uv run scripts/bf.py run <repo>` works without installing the package (PEP 723 inline dependencies).
Clone it into a folder named `break-forecast` so the folder matches the skill `name`.

## 5. Architecture

```
src/break_forecast/
  cli.py            commands, stage orchestration          assemble.py   capsule from stage outputs
  inspect_repo.py   Stage 1                                 manifests.py  every manifest + install/test commands
  usage.py          AST usage scan                          import_map.py packages_distributions()
  pypi.py, fetch.py PyPI/GitHub facts, cached with URL+time releasenotes.py release notes between versions
  baseline.py       Stage 2 + era reconstruction            envs.py       uv envs (no bin/ vs Scripts\ guessing)
  cells.py          Stage 4 planner + resolver levels       replacements.py AI replacement proposals + PyPI check
  forecast.py       Stage 3 blind forecast + scoring        probes.py     Stage 8 differential probes
  matrix.py         Stage 5 executor + classification       testrun.py    pytest runner + JUnit parsing
  diagnose.py       Stage 6                                 patch_gate.py Stage 7 gate + repair loop + exit plan
  capsule.py        Stage 10 writer + schema validation     rerun.py      verify-capsule --rerun
  llm.py            Gemma client: 31B → 26B → local, retries, cache, prompt logs
  prompts/          versioned prompt files                  schema/       capsule JSON schema
  assets/           self-contained HTML viewer
SKILL.md · scripts/bf.py · references/ (gate rules, provenance rules, capsule layout) · tests/ (+ fixtures/mini-repo)
```

Every run writes `runs/<timestamp>/` with `stage*.json`, logs of every install and test run, the resolved lock
of every cell, every prompt/response pair (`llm/`), the patched copies and the capsule.

## 6. Dependencies

| Package | Why | License |
|---|---|---|
| `packaging` | requirement/version parsing | Apache-2.0 OR BSD-2-Clause |
| `jsonschema` | capsule + model-output validation | MIT |
| `uv` | environments, resolution, era-limited installs | MIT OR Apache-2.0 |
| `pytest`, `ruff` (dev) | tests, lint | MIT |

Everything else is the Python standard library. The target repository's own dependencies are installed only
inside throwaway environments.

## 7. AI model details

| Field | Value |
|---|---|
| Model name | **Gemma 4** (open-weight, Google DeepMind) |
| Versions used | `gemma-4-31b-it` (primary), `gemma-4-26b-a4b-it` (secondary, MoE), `gemma4:12b-it-qat` (local fallback) |
| Model source | Google AI Studio / Gemini API (`generativelanguage.googleapis.com`); Ollama library for the local build |
| Access method | REST `generateContent` with an API key in the `x-goog-api-key` header; Ollama's native `/api/chat` for the local model. Configured with `BF_MODEL`, `BF_MODEL_ENDPOINT`, `BF_API_KEY`, `BF_SECONDARY_MODEL`, `BF_FALLBACK_MODEL`, `BF_FALLBACK_ENDPOINT` |
| License / terms | Gemma 4 weights: **Apache-2.0** ([Google Open Source Blog](https://opensource.googleblog.com/2026/03/gemma-4-expanding-the-gemmaverse-with-apache-20.html)); hosted use is subject to the Gemini API terms of service. Gemma is free of charge on the Gemini API free tier ([pricing](https://ai.google.dev/gemini-api/docs/pricing)) |
| Role in the project | proposes replacements for frozen packages · blind forecast of every change · writes behaviour probes · diagnoses failures · writes code patches. It **never** supplies facts (versions, dates, package existence) and its output is never accepted without an executed check |
| Settings | temperature 0; JSON output validated against a schema with one retry; every call logged with prompt/response SHA-256 and the model that actually answered; answers cached by prompt hash. Local Ollama is called through its native API with a 16k context (`BF_LOCAL_NUM_CTX`) and a 2,048-token answer cap (`BF_LOCAL_MAX_TOKENS`). Tuning: `BF_MODEL_TIMEOUT` (hosted, default 90 s), `BF_LOCAL_TIMEOUT` (default 300 s), `BF_MODEL_RETRIES` (default 3), `BF_SECONDARY_MODEL=none` to skip the second hosted model, `--offline-model` to use only the local model |

## 8. Original contribution

Built for this hackathon — not a prompt on top of an existing agent:

- **Era reconstruction** of non-reproducible pins (transitive cut-off dates derived from the pinned parents).
- **Resolver-guided cell planning** with staged freeing and co-upgrade recording (no brute-force pairs).
- **Evidence gate** for AI patches: 6 static + 5 executed rules incl. tamper detection and behaviour probes, run on two Pythons, with feedback-driven retries.
- **Differential behaviour probes** that catch changes the test suite misses.
- **Blind, hash-locked forecast** scored against reality.
- **Re-runnable Dependency Exit Capsule** with strict provenance tagging, JSON schema and HTML viewer.

## 9. Results on LinkWatch

Real run on the organizer repository, commit `f52f393`, on Windows 11 with Python 3.12.15 and 3.13.16.
Every number below is read from that run's capsule (`docs/sample-capsule/capsule.json`).

**Stage 1 – found without hints** (🔍): 15 dependencies, 27 risk indicators, 48 usage sites in source.
3 frozen (`toml` 0.10.2 – no release for 6 years, `appdirs` 1.4.4, `nose` 1.3.7), 2 unused (`six`, `idna`),
1 undeclared (`certifi`), a private-API import (`requests.utils._parse_content_type_header`), a monkeypatch of
`requests.Session.request`, and a conflicting pin (`pydantic>=2` in pyproject vs `==1.10.13` in requirements).

**Stage 2 – baseline** (▶️): pins as written pass **9/10** tests on both Pythons (`Jinja2 2.11.3` cannot import
`soft_unicode` from today's MarkupSafe). Era reconstruction (e.g. `markupsafe` capped at 2021-02-01) gives **10/10**.
The strict run cannot even load the suite (`pkg_resources` deprecation; on 3.13 also pydantic v1's `typing._eval_type`).

**Stage 3 – blind forecast** (🤖, scored ▶️): **8/12** cells predicted correctly before any test ran;
break precision 0.6, recall 0.75 (it missed that `setuptools` 84 removes `pkg_resources`).

**Stage 5 – matrix** (▶️, 24 runs): 12 pass · 4 warn · 8 break.

| Change | Result (3.12 = 3.13) | Evidence |
|---|---|---|
| jinja2 3.1.6 (+ markupsafe co-upgrade), requests 2.34.2, remove six, remove idna, declare certifi, nose → pytest | ✅ pass | 10/10 |
| pydantic 1.10 → 2.14 | ⚠️ warn | tests pass, but **probes show a whitespace-only site name is now accepted** (`anystr_strip_whitespace` silently ignored) |
| chardet 5 → 7.6 | ⚠️ warn | requests' version warning; probes: a Latin-1 page is now detected as `Windows-1250` |
| setuptools → 84 | ❌ import-fail | `No module named 'pkg_resources'` |
| toml → tomllib, appdirs → platformdirs | ❌ import-fail | code still imports the old package (needs a patch) |
| all upgrades at latest | ❌ import-fail | `pkg_resources` again |

**Stage 7 – evidence-gated patches** (🤖 diff, ▶️ verdict):

| Cell | Verdict | Notes |
|---|---|---|
| appdirs → platformdirs | ✅ **verified** | attempt 1; tests + strict run + probes identical |
| setuptools 84 | ✅ **verified** | attempt 1 **rejected** by the gate (broke the tests); attempt 2 `importlib.resources.files()` passes everything |
| pydantic 2 | 🟡 verified_tests | V2 validators + `str_strip_whitespace`: blank names are rejected again; remaining probe differences are pydantic 2's error-message wording |
| toml → tomllib | 🟡 verified_tests | only difference: malformed TOML raises `TOMLDecodeError` with a different message |
| chardet 7 | — not code-fixable | diagnosed as a transitive break (requests' supported range); reported under limitations |
| **exit plan** (all upgrades + replacements + cleanups + all 4 patches) | 🟡 verified_tests | **10/10 on 3.12 and 3.13** (was import-fail); 4 patch orders tried to apply them cleanly; remaining differences listed in the capsule |

**Reproducibility** (▶️): `bf verify-capsule docs/sample-capsule/capsule.json --rerun` re-cloned the target at the
recorded commit, rebuilt every environment from the capsule's resolved package sets, re-applied the stored edits and
**reproduced 12/12 recorded results** (baseline + 4 patches + exit plan, on 3.12 and 3.13).

**Models that actually answered** (recorded per call): `gemma-4-31b-it`, `gemma-4-26b-a4b-it` (Gemini API) and
`gemma4:12b-it-qat` (local Ollama). During this run the hosted API returned many HTTP 500/503s and timeouts, so
the later stages ran on the local model via the fallback chain; 2 of 9 probes (jinja2, requests) were unusable
(syntax errors in the model's script) and are listed as limitations.

**Second repository** (`tests/fixtures/mini-repo`, pyproject + CI workflow, different packages):
`bf run tests/fixtures/mini-repo` finds the frozen `docopt` (2014), unused `colorama`, and that the old `pytz`
uses the deprecated `datetime.utcfromtimestamp()`; 6/6 tests pass and upgrading `pytz` fixes the strict run.

## 10. Testing

```bash
python -m pytest        # unit tests, incl. every gate rule and probe/forecast logic
ruff check src tests
bf run tests/fixtures/mini-repo --no-ai   # second, differently structured repo (pyproject + CI workflow)
```

## 11. Known limitations

- Targets must use **pytest**; other runners are not detected yet.
- Probes avoid network code; code paths that only run against live servers are covered by the tests alone.
- The hosted Gemma endpoint is frequently overloaded (HTTP 500/503, timeouts). The tool retries, falls back
  31B → 26B → local Ollama and caches answers, but a run can still record an AI step as `no_patch`/skipped.
- The local fallback needs an Ollama version that supports Gemma 4.
- Forecast accuracy is measured on a small number of cells per repo; read the confusion matrix, not a single %.
- A `verified` patch is verified **for the existing tests and the probes**, not proven correct in general;
  the capsule says exactly what was checked.
- Security vulnerabilities are out of scope (as defined by the organizers).

## 12. License

Apache-2.0 — see [LICENSE](LICENSE). Third-party licenses: see §6 and §7.
