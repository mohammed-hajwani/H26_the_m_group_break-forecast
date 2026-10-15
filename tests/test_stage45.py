import json

import pytest

from break_forecast import cells, llm, matrix, testrun


def dep(name, labels, *, latest=None, imports=(), effective=True):
    return {"name": name, "classification": labels, "latest": latest, "import_names": list(imports),
            "declarations": [{"effective": effective}] if effective is not None else []}


STAGE1 = {
    "dependencies": [
        dep("jinja2", ["upgradable"], latest="3.1.6"),
        dep("requests", ["upgradable"], latest="2.34.2"),
        dep("rich", ["current_or_unknown"], latest="15.0.0"),
        dep("six", ["unused"]),
        dep("toml", ["frozen"]),
        dep("appdirs", ["frozen"]),
        dep("certifi", ["undeclared"], imports=["certifi"], effective=None),
    ],
    "risk_indicators": [{"dependency": "toml", "indicator": "frozen", "detail": "no release for 5 years"}],
}
LOCK = {"jinja2": "2.11.3", "markupsafe": "1.1.1", "requests": "2.31.0", "rich": "15.0.0", "six": "1.17.0",
        "toml": "0.10.2", "appdirs": "1.4.4", "certifi": "2023.5.7"}
REPS = {
    "toml": {"id": "replacement:toml", "replacement": "tomllib", "kind": "stdlib", "verified": True},
    "appdirs": {"id": "replacement:appdirs", "replacement": "made-up-dirs", "kind": "pypi", "verified": False,
                "verification": {"detail": "'made-up-dirs' does not exist on PyPI"}},
}


def test_plan_builds_each_cell_type():
    planned = {c.id: c for c in cells.plan(STAGE1, LOCK, replacements=REPS)}
    assert set(planned) == {"upgrade:jinja2@3.1.6", "upgrade:requests@2.34.2", "remove:six", "replace:toml",
                            "replace:appdirs", "declare:certifi", "all-latest"}
    assert planned["upgrade:jinja2@3.1.6"].set_pins == {"jinja2": "3.1.6"}
    assert planned["declare:certifi"].set_pins == {"certifi": "2023.5.7"}
    assert planned["remove:six"].remove == ["six"]
    toml = planned["replace:toml"]
    assert toml.remove == ["toml"] and toml.add_unpinned == [] and toml.expects_code_change
    assert toml.skipped is None
    assert "does not exist on PyPI" in planned["replace:appdirs"].skipped  # unverified AI proposal is not run
    assert planned["all-latest"].set_pins == {"jinja2": "3.1.6", "requests": "2.34.2"}


def test_frozen_without_ai_is_skipped():
    planned = {c.id: c for c in cells.plan(STAGE1, LOCK)}
    assert planned["replace:toml"].skipped.startswith("no replacement target")


def test_exit_plan_cell_combines_upgrades_verified_replacements_and_passing_cleanups():
    planned = [c.to_dict() for c in cells.plan(STAGE1, LOCK, replacements={
        "toml": REPS["toml"],
        "appdirs": {"id": "replacement:appdirs", "replacement": "platformdirs", "kind": "pypi", "verified": True}})]
    matrix_results = [
        {"cell": "remove:six", "result": "pass"}, {"cell": "remove:six", "result": "pass"},
        {"cell": "declare:certifi", "result": "fail"},
        {"cell": "replace:toml", "result": "import-fail"}, {"cell": "replace:appdirs", "result": "import-fail"},
        {"cell": "all-latest", "result": "import-fail"},
    ]
    combo = cells.exit_plan_cell(planned, matrix_results, verified={"replace:toml"})
    assert combo.set_pins == {"jinja2": "3.1.6", "requests": "2.34.2"}
    assert sorted(combo.remove) == ["six", "toml"]          # appdirs patch not verified; certifi failed
    assert combo.add_unpinned == []                         # tomllib is stdlib
    assert "replace:toml" in combo.reason and "replace:appdirs" not in combo.reason
    assert cells.exit_plan_cell(planned, matrix_results, verified=set()).remove == ["six"]


def test_subtree_and_co_upgrades():
    graph = {"jinja2": ["markupsafe"], "markupsafe": ["deep"], "deep": [], "requests": ["urllib3"]}
    assert cells.subtree("jinja2", graph) == {"markupsafe", "deep"}
    cell = cells.Cell("upgrade:jinja2@3.1.6", "upgrade", "jinja2", "", set_pins={"jinja2": "3.1.6"})
    moved = cells.co_upgrades({"jinja2": "2.11.3", "markupsafe": "1.1.1", "pip": "24"},
                              {"jinja2": "3.1.6", "markupsafe": "3.0.4", "new-dep": "1.0"}, cell)
    assert moved == {"markupsafe": ["1.1.1", "3.0.4"], "new-dep": [None, "1.0"]}


def test_parse_lock_and_error_names():
    assert cells.parse_lock("Jinja2==3.1.6\n    # via x\nmarkupsafe==3.0.4\n") == {"jinja2": "3.1.6",
                                                                                   "markupsafe": "3.0.4"}
    err = "Because requests==2.34.2 depends on idna>=3 and you require idna==2.7, unsatisfiable"
    assert cells._direct_names_in_error(err, {"idna", "rich", "requests"}) == {"idna", "requests"}


def test_verify_replacement_stdlib():
    assert cells.verify_replacement({"replacement": "tomllib", "kind": "stdlib"}, offline=True)[0]
    ok, why = cells.verify_replacement({"replacement": "notamodule", "kind": "stdlib"}, offline=True)
    assert not ok and "not a standard-library module" in why


def _tr(outcome, tests, warnings=()):
    return testrun.TestRun("normal", outcome, tests=dict(tests), warnings=list(warnings))


REF = _tr("pass", {"a": "passed", "b": "passed"},
          [{"category": "DeprecationWarning", "message": "old", "head": "old"}])
REF_STRICT = testrun.TestRun("strict", "import-fail")


@pytest.mark.parametrize("normal,confirmed,expected", [
    (_tr("pass", {"a": "passed", "b": "passed"}, REF.warnings), [], "pass"),
    (_tr("fail", {"a": "passed", "b": "failed"}, REF.warnings), ["b"], "fail"),
    (_tr("import-fail", {}), [], "import-fail"),
    (_tr("pass", {"a": "passed", "b": "passed"},
         [*REF.warnings, {"category": "UserWarning", "message": "Valid config keys have changed in V2: x",
                          "head": "Valid config keys have changed in V2:"}]), [], "warn"),
])
def test_classify(normal, confirmed, expected):
    result, detail = matrix.classify(normal, REF_STRICT, REF, REF_STRICT, confirmed)
    assert result == expected
    if expected == "warn":
        assert detail["new_warnings"][0]["category"] == "UserWarning"


def test_resolved_warnings_only_when_suite_ran():
    _, detail = matrix.classify(_tr("import-fail", {}), REF_STRICT, REF, REF_STRICT, [])
    assert detail["resolved_warnings"] == []
    _, detail = matrix.classify(_tr("pass", {"a": "passed", "b": "passed"}), REF_STRICT, REF, REF_STRICT, [])
    assert detail["resolved_warnings"][0]["message"] == "old"


def test_extract_json_tolerates_fences_and_prose():
    assert llm.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.extract_json('Sure! {"a": [1, 2]} hope that helps') == {"a": [1, 2]}


def test_backends_without_key_use_only_local(monkeypatch):
    for k in ("BF_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(k, "")
    assert [b.name for b in llm.backends()] == ["fallback"]
    monkeypatch.setenv("BF_API_KEY", "dummy")
    assert [b.name for b in llm.backends()] == ["primary", "secondary", "fallback"]
    assert [b.name for b in llm.backends(offline=True)] == ["fallback"]


def test_local_ollama_uses_native_api_with_context_window(monkeypatch):
    b = llm.Backend("fallback", "gemma4:12b-it-qat", "http://localhost:11434/v1", None)
    assert b.kind == "ollama"
    sent = {}

    def fake_post(url, body, headers, timeout):
        sent.update(url=url, body=body)
        return {"message": {"content": '{"ok": true}'}, "done_reason": "stop"}

    monkeypatch.setattr(llm, "_post", fake_post)
    assert llm._call(b, "P", True, 10) == '{"ok": true}'
    assert sent["url"] == "http://localhost:11434/api/chat"
    assert sent["body"]["options"]["num_ctx"] >= 16384 and sent["body"]["format"] == "json"
    assert sent["body"]["think"] is False


def test_empty_local_answer_is_a_failure_not_an_answer(monkeypatch):
    b = llm.Backend("fallback", "gemma4:12b-it-qat", "http://localhost:11434/v1", None)
    monkeypatch.setattr(llm, "_post", lambda *a, **k: {"message": {"content": ""}, "done_reason": "length"})
    with pytest.raises(llm.LLMError, match="empty response"):
        llm._call(b, "P", True, 10)


def test_cache_hit_from_a_model_outside_todays_chain(monkeypatch, tmp_path):
    monkeypatch.setenv("BF_CACHE_DIR", str(tmp_path))
    client = llm.LLM(tmp_path / "log", offline=True)            # chain: local model only
    hosted = llm.Backend("cache-only", llm.DEFAULT_MODEL, llm.DEFAULT_ENDPOINT, None)
    path = client._cache_path(hosted, "PROMPT", True)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"meta": {"model_used": llm.DEFAULT_MODEL}, "response": '{"x": 3}'}), "utf-8")
    monkeypatch.setattr(llm, "_call", lambda *a, **k: pytest.fail("network must not be used"))
    data, meta = client.complete_json("PROMPT")
    assert data == {"x": 3} and meta["model_used"] == llm.DEFAULT_MODEL and meta["cached"]


def test_log_file_names_are_filesystem_safe(monkeypatch, tmp_path):
    monkeypatch.setenv("BF_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "_call", lambda *a, **k: '{"x": 1}')
    client = llm.LLM(tmp_path / "log", use_cache=False)
    client.complete_json("P", task="diagnose-upgrade:pydantic@2.14.1")
    [path] = (tmp_path / "log").iterdir()
    assert path.name == "001-diagnose-upgrade-pydantic-2.14.1.json"


def test_cached_answer_is_reused_without_network(monkeypatch, tmp_path):
    monkeypatch.setenv("BF_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("BF_API_KEY", "dummy")
    client = llm.LLM(tmp_path / "log")
    path = client._cache_path(client.backends[0], "PROMPT", True)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"meta": {"model_used": "gemma-4-31b-it"}, "response": '{"x": 1}'}), "utf-8")
    monkeypatch.setattr(llm, "_call", lambda *a, **k: pytest.fail("network must not be used"))
    data, meta = client.complete_json("PROMPT")
    assert data == {"x": 1} and meta["cached"] is True
    fresh = llm.LLM(tmp_path / "log", use_cache=False)
    monkeypatch.setattr(llm, "_call", lambda *a, **k: '{"x": 2}')
    assert fresh.complete_json("PROMPT")[0] == {"x": 2}
