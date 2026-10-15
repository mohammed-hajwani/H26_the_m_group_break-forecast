from break_forecast import forecast, releasenotes
from break_forecast.capsule import validate


def test_actual_outcome_takes_the_worst_python():
    assert forecast.actual_outcome({"pass", "flaky"}) == "pass"
    assert forecast.actual_outcome({"pass", "warn"}) == "warn"
    assert forecast.actual_outcome({"warn", "import-fail"}) == "break"
    assert forecast.actual_outcome({"install-fail"}) == "break"
    assert forecast.actual_outcome({"skipped"}) is None


STAGE3 = {
    "locked_at": "2026-10-15T08:00:00+00:00", "answered_at": "2026-10-15T08:00:30+00:00",
    "prompt_sha256": "p", "response_sha256": "r", "model_used": "gemma-4-31b-it", "error": None,
    "forecasts": {
        "upgrade:jinja2@3.1.6": {"cell": "upgrade:jinja2@3.1.6", "predicted_outcome": "break", "confidence": 0.6},
        "upgrade:pydantic@2.14.1": {"cell": "upgrade:pydantic@2.14.1", "predicted_outcome": "break"},
        "upgrade:setuptools@84.0.0": {"cell": "upgrade:setuptools@84.0.0", "predicted_outcome": "break"},
        "remove:six": {"cell": "remove:six", "predicted_outcome": "pass"},
        "replace:appdirs": {"cell": "replace:appdirs", "predicted_outcome": "pass"},
    },
}
MATRIX = [
    {"cell": "upgrade:jinja2@3.1.6", "result": "pass"}, {"cell": "upgrade:jinja2@3.1.6", "result": "pass"},
    {"cell": "upgrade:pydantic@2.14.1", "result": "warn"},
    {"cell": "upgrade:setuptools@84.0.0", "result": "import-fail"},
    {"cell": "remove:six", "result": "pass"},
    {"cell": "replace:appdirs", "result": "skipped"},
]


def test_score_builds_confusion_matrix_and_break_metrics():
    s = forecast.score(STAGE3, MATRIX)
    assert s["scored_cells"] == 4 and s["correct"] == 2            # skipped cell is not scored
    assert s["confusion_matrix"]["break"] == {"pass": 1, "warn": 1, "break": 1}
    assert s["confusion_matrix"]["pass"]["pass"] == 1
    assert s["break_precision"] == round(1 / 3, 3) and s["break_recall"] == 1.0


def test_forecast_capsule_records_keep_lock_and_provenance():
    recs = forecast.to_capsule(STAGE3, forecast.score(STAGE3, MATRIX))
    ai = [r for r in recs if r["provenance"] == "ai_generated"]
    assert len(ai) == 5 and all(r["source"]["prompt_sha256"] == "p" for r in ai)
    assert [r["kind"] for r in recs if r["provenance"] == "observed"] == ["forecast_score"]
    cap = {"schema_version": "1", "forecast": recs}
    assert not [e for e in validate(cap) if e.startswith("forecast")]


def test_changelog_section_is_sliced_between_versions(monkeypatch):
    text = "Changelog\n\n3.1.0\n- new thing\n\n3.0.0\n- removed soft_unicode\n\n2.11.3\n- old fix\n"
    monkeypatch.setattr(releasenotes, "get_text", lambda url, offline=False: (text, {"url": url, "fetched_at": "t"}))
    got = releasenotes.from_changelog("https://x/changes", releasenotes._v("2.11.3"), releasenotes._v("3.1.0"),
                                      False, 2500)
    assert "removed soft_unicode" in got["excerpt"] and "old fix" not in got["excerpt"]
