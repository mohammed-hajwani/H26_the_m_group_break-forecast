# Patch gate rules

Implementation: `src/break_forecast/patch_gate.py` · tests: `tests/test_patch_gate.py`, `tests/test_probes.py`.

Every model-written patch goes through these rules on **every** target Python (3.12 and 3.13).
Failed rules are fed back to the model for the next attempt (default 3 attempts); the best attempt is kept.

## Verdicts

| Verdict | Meaning |
|---|---|
| `verified` | every static rule, every executed rule **and** `behaviour_preserved` hold |
| `verified_tests` | all rules hold except `behaviour_preserved`: tests pass but probes show behaviour changed |
| `rejected` | any static or executed test rule failed |
| `no_patch` | no model backend answered |

## Static rules (before anything runs)

| Rule | Rejects |
|---|---|
| `allowlisted_paths` | tests, `conftest.py`, `setup.py`, non-`.py` files (manifests, config), new files, absolute paths, `..` escapes |
| `applies_cleanly` | a `search` snippet that matches 0 or more than 1 times |
| `syntax_ok` | patched files that no longer parse |
| `no_tamper_patterns` | *added* lines with `pytest.skip/xfail/importorskip`, skip markers, `warnings.filterwarnings/simplefilter/catch_warnings`, `sys.modules[...]` stubs |
| `no_broad_except_added` | more bare / `except Exception` / `except BaseException` handlers than before |
| `test_tree_unchanged` | any change to the hash of test files, `conftest.py` and test config (`pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`) |

## Executed rules (patched copy of the repo, fresh env from the cell's resolved lock)

| Rule | Requires |
|---|---|
| `no_regressions` | the suite loads; no test that passed in the reference baseline fails (re-run once to rule out flakes); no test disappears |
| `target_fixed` | the cell's original problem is gone: `fail`/`import-fail` → suite passes; `warn` → its new warnings are gone |
| `strict_not_worse` | the `-W error::DeprecationWarning` run has no new failures vs the reference strict run |
| `target_installed` | the env really contains the cell's target versions (no quiet downgrade or re-pin) |
| `behaviour_preserved` | the differential probes for the changed packages give the same normalised output as on the original code with the original dependencies |

## Combined check (exit plan)

After individual repairs, every verified patch is applied together on the **exit plan** cell
(all upgrades at latest + verified replacements + passing removals/declarations), resolved as one
environment and gated with the same rules.
