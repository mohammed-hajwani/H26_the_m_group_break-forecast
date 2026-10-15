You are helping a maintainer replace a Python dependency that is no longer maintained.
You only propose; a tool will verify your proposal and test it. Do not invent facts:
the metadata below was fetched from PyPI and the usage sites were found by parsing the code.

## Dependency to replace
{{dependency}}

## Why it is considered frozen (observed)
{{reason}}

## PyPI metadata (observed)
{{metadata}}

## How the repository uses it (observed, file:line and API)
{{usage}}

## Target Python versions
3.12 and 3.13

## Task
Propose ONE replacement. Prefer, in order: a standard-library module available on Python 3.12+,
then the officially recommended successor package, then a widely used maintained package.
If the dependency is only used by tooling (for example a test runner) and an already-declared
package does the same job, propose that package.

Reply with ONLY a JSON object:
{
  "replacement": "<PyPI distribution name, or standard-library module name>",
  "kind": "pypi" or "stdlib",
  "api_compatible": true if the code can switch with no source changes, else false,
  "rationale": "<one or two sentences>",
  "migration_outline": ["<step>", "..."],
  "api_mapping": [{"old": "<old API used above>", "new": "<new API>", "note": "<behaviour difference, if any>"}],
  "confidence": <number between 0 and 1>
}
