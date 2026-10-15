Write a small Python "characterization probe" script for a repository. A tool will run the
SAME script twice - once with the old dependency set and once with the new one - and compare
the printed output to detect behaviour changes the test suite does not cover.

## Dependency whose behaviour we want to pin down
{{dependency}}

## Where the repository's own code uses it (observed)
{{usage}}

## Full source of those repository modules
{{files}}
{{feedback}}
## Requirements for the script
- Import and call ONLY the repository's own functions/classes shown above (e.g. `from pkg.module import func`).
  Do NOT import `{{dependency_import}}` or any other third-party package directly: after a migration the
  dependency may be replaced, and the same probe must still run.
- Exercise each usage site with 4-8 varied inputs, including edge cases (empty values, whitespace,
  unusual encodings, malformed input, boundary values) that are relevant to what the dependency does.
- No network access. Use `tempfile.mkdtemp()` for any files you need and build inputs in memory.
  If a function needs an object such as an HTTP response, create a minimal stand-in class.
- Deterministic: no random values, no current time in the output.
- Wrap EACH case in try/except Exception and record `{"error": type(e).__name__ + ": " + str(e)}` for it.
- Convert results to JSON-friendly values (str/int/float/bool/None/list/dict); use repr() for other objects
  and str() for paths.
- At the end print exactly ONE line: `print(json.dumps(results, sort_keys=True, default=repr))`
  where `results` maps a short case name to the case's result.

Reply with ONLY a JSON object:
{"probe": "<the complete Python script>", "cases": ["<case name>", "..."], "what_it_checks": "<one sentence>"}
