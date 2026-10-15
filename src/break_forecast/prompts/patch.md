You are fixing a Python repository so that it works after a dependency change.
A tool will apply your edits to a copy of the repository and only accept them if the
existing test suite passes on Python 3.12 AND 3.13, nothing that passed before fails,
and every rule below holds. Rejected edits are reported publicly, so do not try to game the checks.

## The dependency change (applied by the tool, not by you)
{{change}}

## Diagnosis (AI-generated, may be wrong; trust the evidence over it)
{{diagnosis}}

## Evidence from running the tests (executed)
{{errors}}

## Files you may edit (full current contents)
{{files}}
{{feedback}}
## Rules (enforced automatically)
- Edit ONLY the files listed above. Never edit tests, conftest.py, test configuration,
  requirements files, pyproject.toml, setup.cfg or setup.py. The tool changes dependencies itself.
- Do not skip, xfail or disable tests; do not add pytest markers.
- Do not silence warnings (no warnings.filterwarnings / simplefilter / catch_warnings).
- Do not stub modules via sys.modules, and do not wrap code in broad `except Exception` / bare `except`.
- Do not pin or re-install old versions; make the code work with the NEW dependency set.
- Keep behaviour the same for callers (same function names, signatures and return values).
- Prefer the smallest correct change. Use only APIs that exist in the new dependency set.

## Output format
Reply with ONLY a JSON object. Each edit replaces one exact, unique snippet of the current file
(copy the `search` text verbatim, including indentation; include enough lines to make it unique):
{
  "summary": "<one sentence describing the change>",
  "edits": [
    {"file": "<path exactly as listed above>", "search": "<exact existing text>", "replace": "<new text>"}
  ]
}
