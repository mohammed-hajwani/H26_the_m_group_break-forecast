You are forecasting, BEFORE any test is run, what will happen when each dependency change
below is applied to a Python repository and its pytest suite is run on Python 3.12 and 3.13.
Your forecast is locked (hashed and time-stamped) and later scored against the real results,
so give your honest best guess. Use only the evidence below; do not assume facts that are not in it.

## Reference baseline (executed before any change)
{{baseline}}

## How the repository uses its dependencies (observed from the code)
{{usage}}

## Planned changes (observed: resolver output, including forced co-upgrades)
{{cells}}

## Release notes between the current and target versions (fetched from GitHub/PyPI)
{{notes}}

## Outcome categories
- "pass": tests pass and no new warnings appear
- "warn": tests still pass but new warnings appear (e.g. deprecations, version-mismatch warnings)
- "break": tests fail, the suite cannot import, or the install fails

Reply with ONLY a JSON object containing one entry per cell id listed above:
{
  "forecasts": [
    {"cell": "<cell id>", "predicted_outcome": "pass" | "warn" | "break",
     "confidence": <0..1>, "reasoning": "<one or two sentences citing the evidence>"}
  ]
}
