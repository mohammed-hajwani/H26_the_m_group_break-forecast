import sys
import textwrap
from pathlib import Path

import pytest

from break_forecast import patch_gate, probes


def test_normalise_removes_run_specific_noise():
    value = {"b": "<obj at 0x7f3a2b1c9d80>", "a": ["2026-10-15T08:00:01+00:00", "C:/repo/x/data.toml"],
             "t": "/tmp/bf-123/sites.toml"}
    out = probes.normalise(value, repo="C:/repo/x")
    assert list(out) == ["a", "b", "t"]
    assert out["b"] == "<obj at 0x…>" and out["a"] == ["<timestamp>", "<repo>/data.toml"]
    assert out["t"] == "<tmp>"


def test_compare_lists_changed_and_missing_cases():
    assert probes.compare({"x": 1, "y": "a"}, {"x": 1, "y": "b", "z": 2}) == [
        {"case": "y", "reference": "a", "candidate": "b"},
        {"case": "z", "reference": "<missing>", "candidate": 2}]


@pytest.mark.parametrize("code,problem", [
    ("import toml\nprint(1)", "imports the dependency directly: toml"),
    ("from toml.decoder import loads\n", "imports the dependency directly: toml.decoder"),
    ("import socket\n", "network module imported: socket"),
    ("def f(:\n", "syntax error"),
])
def test_static_problems(code, problem):
    assert any(problem in p for p in probes.static_problems(code, {"toml"}))


def test_static_problems_allows_repo_imports():
    assert probes.static_problems("from pkg.config import load\nimport json, tempfile\n", {"toml"}) == []


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "core.py").write_text("def strip(s):\n    return s.strip()\n", encoding="utf-8")
    (tmp_path / "pkg" / "cli.py").write_text(textwrap.dedent("""\
        import sys
        def main():
            if "--help" in sys.argv:
                print("usage: pkg [--help]")
                raise SystemExit(0)
    """), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="pkg"\n[project.scripts]\npkg = "pkg.cli:main"\n',
                                             encoding="utf-8")
    return tmp_path


def test_generic_probe_imports_modules_and_captures_help(repo):
    code = probes.generic_probe(repo, ["pkg"])
    res = probes.run_probe(code, sys.executable, repo)
    assert res["ok"], res
    out = res["output"]
    assert out["import pkg.core"] == "ok"
    assert out["cli pkg --help"] == {"exit": 0, "output": "usage: pkg [--help]\n"}


def test_ai_style_probe_detects_behaviour_change(repo):
    probe = textwrap.dedent("""\
        import json
        from pkg.core import strip
        results = {}
        for name, value in {"spaces": "  a  ", "tabs": "\\ta\\t"}.items():
            try:
                results[name] = strip(value)
            except Exception as e:
                results[name] = {"error": type(e).__name__ + ": " + str(e)}
        print(json.dumps(results, sort_keys=True, default=repr))
    """)
    before = probes.run_probe(probe, sys.executable, repo)["output"]
    (repo / "pkg" / "core.py").write_text("def strip(s):\n    return s.strip(' ')\n", encoding="utf-8")
    after = probes.run_probe(probe, sys.executable, repo)["output"]
    assert probes.compare(before, after) == [{"case": "tabs", "reference": "a", "candidate": "\ta\t"}]


def test_crashing_probe_is_reported_not_hidden(repo):
    res = probes.run_probe("raise RuntimeError('boom')\n", sys.executable, repo)
    assert not res["ok"] and "boom" in res["error"]


def _exec(rules_ok=True, probe_ok=True):
    rules = {"no_regressions": {"ok": rules_ok, "detail": ""}, "behaviour_preserved": {"ok": probe_ok, "detail": ""}}
    return {"3.12": {"rules": rules}, "3.13": {"rules": rules}}


def test_verdict_levels():
    static_ok = {"allowlisted_paths": {"ok": True, "detail": ""}}
    assert patch_gate.verdict_of(static_ok, _exec()) == "verified"
    assert patch_gate.verdict_of(static_ok, _exec(probe_ok=False)) == "verified_tests"
    assert patch_gate.verdict_of(static_ok, _exec(rules_ok=False)) == "rejected"
    assert patch_gate.verdict_of({"allowlisted_paths": {"ok": False, "detail": "x"}}, _exec()) == "rejected"
    assert patch_gate.verdict_of(static_ok, {}) == "rejected"


def test_probe_rule_reports_divergences():
    from break_forecast.cells import Cell

    rec = {"result": "pass", "normal": {"outcome": "pass"}, "diff_vs_reference": {}, "confirmed_failing": [],
           "new_warnings": [], "strict": {}, "installed_target": {},
           "probes": {"probes_run": ["generic", "pydantic"], "divergences": 1,
                      "results": [{"probe": "pydantic", "divergences": [
                          {"case": "padded name", "reference": "Python", "candidate": "  Python  "}]}]}}
    rules = patch_gate.executed_rules(Cell("c", "upgrade", "pydantic", ""), rec, {"result": "warn"})
    assert not rules["behaviour_preserved"]["ok"]
    assert "padded name" in rules["behaviour_preserved"]["detail"]
