You are diagnosing why a dependency change broke (or degraded) a Python repository.
Everything below was observed by a tool that actually installed the packages and ran the
test suite. Do not invent versions, dates or APIs that are not supported by this evidence.

## The change that was tested
{{change}}

## Result on each Python version (executed)
{{results}}

## Errors, failing tests and new warnings (executed)
{{errors}}

## Where the repository uses the affected package(s) (observed, with source)
{{usage}}

## Package metadata (observed from PyPI)
{{metadata}}

## Task
Explain the root cause and say whether a source-code change in the repository (not in tests,
not in dependency manifests) can fix it.

Root-cause categories:
- api_removed: the code uses an API the new version removed
- api_changed: the API still exists but its signature/behaviour/config changed
- behavior_change: same API, different results
- python_incompat: the package or code does not support this Python version
- transitive_break: another package in the environment is incompatible with the change
- env_issue: installation/resolution/environment problem, not a code problem
- unknown

Reply with ONLY a JSON object:
{
  "root_cause": "<one category above>",
  "explanation": "<2-4 sentences grounded in the evidence>",
  "evidence": ["<quote or paraphrase of the specific error/warning lines you relied on>"],
  "affected_locations": ["<file:line from the usage list>"],
  "code_fixable": true or false,
  "fix_strategy": "<what source change would fix it, or why no code change can>",
  "co_upgrade_needed": ["<package that must change together, only if the evidence names it>"],
  "confidence": <number between 0 and 1>
}
