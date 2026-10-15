import textwrap
from pathlib import Path

import pytest

from break_forecast import patch_gate as gate
from break_forecast.cells import Cell

SRC = textwrap.dedent("""\
    import toml


    def load_settings(path="sites.toml"):
        data = toml.load(path)
        return data
""")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "config.py").write_text(SRC, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_config.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (tmp_path / "tests" / "conftest.py").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("toml==0.10.2\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("hi\n", encoding="utf-8")
    return tmp_path


GOOD = [
    {"file": "pkg/config.py", "search": "import toml\n", "replace": "import tomllib\n"},
    {"file": "pkg/config.py", "search": "    data = toml.load(path)\n",
     "replace": "    with open(path, \"rb\") as fh:\n        data = tomllib.load(fh)\n"},
]


def failed(rules):
    return sorted(name for name, r in rules.items() if not r["ok"])


def test_good_patch_passes_all_static_rules(repo):
    originals, contents, rules = gate.apply_edits(repo, GOOD)
    assert failed(rules) == []
    assert "tomllib.load(fh)" in contents["pkg/config.py"]
    diff = gate.unified_diff(originals, contents)
    assert diff.startswith("--- a/pkg/config.py") and "+import tomllib" in diff


@pytest.mark.parametrize("file", ["tests/test_config.py", "tests/conftest.py", "pyproject.toml",
                                  "requirements.txt", "notes.md", "../outside.py", "C:/abs/x.py",
                                  "/etc/x.py", "pkg/new_module.py"])
def test_disallowed_paths_are_rejected(repo, file):
    _, _, rules = gate.apply_edits(repo, [{"file": file, "search": "x", "replace": "y"}])
    assert "allowlisted_paths" in failed(rules)


def test_backslash_and_dot_paths_are_normalised(repo):
    _, contents, rules = gate.apply_edits(repo, [{"file": ".\\pkg\\config.py", "search": "import toml\n",
                                                  "replace": "import tomllib\n"}])
    assert failed(rules) == [] and "pkg/config.py" in contents


@pytest.mark.parametrize("search", ["not in file", "toml"])  # 0 matches, several matches
def test_search_must_match_exactly_once(repo, search):
    _, _, rules = gate.apply_edits(repo, [{"file": "pkg/config.py", "search": search, "replace": "x"}])
    assert "applies_cleanly" in failed(rules)


def test_syntax_errors_are_rejected(repo):
    _, _, rules = gate.apply_edits(repo, [{"file": "pkg/config.py", "search": "    return data\n",
                                           "replace": "    return data)\n"}])
    assert "syntax_ok" in failed(rules)


@pytest.mark.parametrize("added", [
    "import pytest\npytest.skip('nope', allow_module_level=True)\n",
    "import warnings\nwarnings.filterwarnings('ignore')\n",
    "import warnings\nwarnings.simplefilter('ignore')\n",
    "import sys, types\nsys.modules['toml'] = types.ModuleType('toml')\n",
])
def test_tamper_patterns_are_rejected(repo, added):
    _, _, rules = gate.apply_edits(repo, [{"file": "pkg/config.py", "search": "import toml\n",
                                           "replace": added + "import toml\n"}])
    assert "no_tamper_patterns" in failed(rules)


def test_existing_tamper_text_is_not_blamed_on_the_patch(repo):
    (repo / "pkg" / "config.py").write_text("import warnings\nwarnings.simplefilter('default')\n" + SRC, "utf-8")
    _, _, rules = gate.apply_edits(repo, GOOD)
    assert "no_tamper_patterns" not in failed(rules)


@pytest.mark.parametrize("handler", ["except Exception:", "except:", "except BaseException:"])
def test_new_broad_except_is_rejected(repo, handler):
    replace = f"    try:\n        data = toml.load(path)\n    {handler}\n        data = {{}}\n"
    _, _, rules = gate.apply_edits(repo, [{"file": "pkg/config.py", "search": "    data = toml.load(path)\n",
                                           "replace": replace}])
    assert "no_broad_except_added" in failed(rules)


def test_narrow_except_is_allowed(repo):
    replace = "    try:\n        data = toml.load(path)\n    except FileNotFoundError:\n        data = {}\n"
    _, _, rules = gate.apply_edits(repo, [{"file": "pkg/config.py", "search": "    data = toml.load(path)\n",
                                           "replace": replace}])
    assert failed(rules) == []


def test_test_tree_hash_detects_test_and_config_changes(repo, tmp_path):
    base = gate.test_tree_hash(repo)
    copy = gate.materialise(repo, {}, tmp_path / "copy")
    assert gate.test_tree_hash(copy) == base
    (copy / "pkg" / "config.py").write_text("changed", encoding="utf-8")  # source changes do not count
    assert gate.test_tree_hash(copy) == base
    (copy / "tests" / "test_config.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    assert gate.test_tree_hash(copy) != base
    copy2 = gate.materialise(repo, {}, tmp_path / "copy2")
    (copy2 / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts='-k not slow'\n", "utf-8")
    assert gate.test_tree_hash(copy2) != base


def test_materialise_never_touches_the_original(repo, tmp_path):
    _, contents, _ = gate.apply_edits(repo, GOOD)
    dest = gate.materialise(repo, contents, tmp_path / "patched")
    assert "tomllib" in (dest / "pkg" / "config.py").read_text(encoding="utf-8")
    assert (repo / "pkg" / "config.py").read_text(encoding="utf-8") == SRC


def _repair(cell, edits):
    return {"cell": cell, "verified_attempt": 1, "attempts": [{"attempt": 1, "edits": edits}]}


def test_find_order_resolves_patch_order_conflicts(repo, tmp_path_factory):
    from break_forecast.runctx import Run

    tmp_path = tmp_path_factory.mktemp("runs-outside-repo")

    (repo / "pkg" / "config.py").write_text("import toml\nfrom pydantic import validator\n", encoding="utf-8")
    pydantic_fix = _repair("upgrade:pydantic", [{"file": "pkg/config.py", "search": "from pydantic import validator",
                                                 "replace": "from pydantic import field_validator"}])
    toml_fix = _repair("replace:toml", [{"file": "pkg/config.py",
                                         "search": "import toml\nfrom pydantic import validator",
                                         "replace": "import tomllib\nfrom pydantic import validator"}])
    run = Run.create(tmp_path / "runs")
    order, contents, static, tried = gate.find_order([pydantic_fix, toml_fix], repo, run)
    assert [p["cell"] for p in order] == ["replace:toml", "upgrade:pydantic"] and tried == 2
    assert static["applies_cleanly"]["ok"]
    assert contents["pkg/config.py"] == "import tomllib\nfrom pydantic import field_validator\n"


CELL = Cell("upgrade:pydantic@2.14.1", "upgrade", "pydantic", "", set_pins={"pydantic": "2.14.1"})


def rec(result="pass", *, confirmed=(), missing=(), new_warnings=(), strict_regressed=False, installed="2.14.1",
        outcome="pass"):
    return {"result": result, "normal": {"outcome": outcome, "error_excerpt": None},
            "diff_vs_reference": {"missing": list(missing)}, "confirmed_failing": list(confirmed),
            "new_warnings": list(new_warnings), "strict_regressed": strict_regressed,
            "strict": {"error_excerpt": None}, "installed_target": {"pydantic": installed}}


def test_executed_rules_all_pass():
    rules = gate.executed_rules(CELL, rec(), {"result": "import-fail"})
    assert failed(rules) == []


def test_executed_rules_catch_regressions_and_missing_tests():
    assert "no_regressions" in failed(gate.executed_rules(CELL, rec("fail", confirmed=["t1"]), {"result": "fail"}))
    assert "no_regressions" in failed(gate.executed_rules(CELL, rec(missing=["t2"]), {"result": "fail"}))


def test_import_fail_is_not_fixed():
    rules = gate.executed_rules(CELL, rec("import-fail", outcome="import-fail"), {"result": "import-fail"})
    assert {"no_regressions", "target_fixed"} <= set(failed(rules))


def test_warn_cell_needs_its_warning_gone():
    w = [{"category": "UserWarning", "message": "Valid config keys have changed in V2"}]
    rules = gate.executed_rules(CELL, rec("warn", new_warnings=w), {"result": "warn"})
    assert failed(rules) == ["target_fixed"]


def test_strict_regression_and_quiet_downgrade_are_caught():
    assert "strict_not_worse" in failed(gate.executed_rules(CELL, rec(strict_regressed=True), {"result": "fail"}))
    assert "target_installed" in failed(gate.executed_rules(CELL, rec(installed="1.10.13"), {"result": "fail"}))
