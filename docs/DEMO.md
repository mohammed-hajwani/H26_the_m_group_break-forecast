# Demo script (about 3 minutes) and submission checklist

## Before the judges arrive

1. `bf llm-check` — confirm at least one Gemma backend answers (the hosted API is often overloaded; retry or use the local model).
2. Have a **finished run** open in a browser tab: `runs/<id>/capsule.html` (serve it with
   `python -m http.server 8765 --directory runs` and open `http://localhost:8765/<id>/capsule.html`).
3. Have a terminal ready in the project folder with the venv active.
4. Keep the **screen recording** (fallback video) one click away.

## Live demo

| Time | Show | Say |
|---|---|---|
| 0:00 | Terminal: `bf run https://github.com/rishrek/LinkWatch` starting | "One command, the organizer's repo, nothing prepared by hand." |
| 0:20 | Stage 1 + 2 output scrolling | "It found frozen, unused and undeclared deps on its own — and the pins as written fail a test; it rebuilt the era they came from." |
| 0:45 | Switch to the finished `capsule.html` → **Matrix** | "Every change, on 3.12 and 3.13, normal and warnings-as-errors. Green passes, amber passes with new warnings or changed behaviour, red breaks." |
| 1:05 | Click the **pydantic** cell | "Tests pass — but here is the renamed config key and the V1 validator deprecation the tests never notice." |
| 1:25 | **Forecast** section | "Gemma predicted every cell before any test ran — locked with a hash — and here is how right it was." |
| 1:45 | **Patches** → a rejected attempt | "This is the gate rejecting an AI patch and telling the model which rule failed." |
| 2:05 | **Patches** → a verified patch + its diff | "Accepted only after tests on both Pythons, the strict run and the behaviour probes." |
| 2:25 | **Behaviour probes** | "Probes call LinkWatch's own functions before and after; differences are facts, not opinions." |
| 2:40 | Terminal: `bf verify-capsule runs/<id>/capsule.json --rerun` (or show a finished rerun) | "Anyone can re-run the evidence from the capsule alone." |
| 2:55 | **Limitations** | "And we report what we could not fix." |

## If something fails live

- Model overloaded → say so; the capsule records it as a limitation; switch to the recorded run.
- No network → `bf scan` and `bf baseline` with `--offline-cache` still work from the cache; show the finished capsule.
- Anything else → play the screen recording.

## Submission checklist

- [ ] Public GitHub repository named **`break-forecast`** (the skill `name` must match the folder)
- [ ] `LICENSE` (Apache-2.0) present; third-party licenses listed in README §6–7
- [ ] `README.md`: problem & solution, setup, usage, architecture, dependencies, AI model details, limitations, original contribution, results
- [ ] `SKILL.md` + `scripts/` + `references/` (Agent Skills format)
- [ ] `.env` **not** committed (only `.env.example`); no `runs/`, `.venv/`, `.bf-cache/`
- [ ] One finished capsule from the organizer repo exported to `docs/sample-capsule/` (json + html + md) as evidence
- [ ] Slides: `deck/break-forecast-submission.pptx` (6 slides on the official template)
- [ ] Screen recording of `bf run` + the capsule (fallback)
- [ ] Team name, members and college filled in on slide 1
