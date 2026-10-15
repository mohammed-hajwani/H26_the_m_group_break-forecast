"""Run events (events.py) and the `bf serve` web server (server.py)."""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from break_forecast import events
from break_forecast.runctx import clean_env, strip_ansi
from break_forecast.server import App, ThreadingHTTPServer, is_local_request, make_handler

RUN = "20261015-000000"
CELL = "upgrade:a@2.0"


def write_run(root: Path, *, capsule: bool = True) -> Path:
    """A minimal recorded run: one dependency, one cell on one Python, one verified patch."""
    d = root / RUN
    d.mkdir(parents=True)
    files = {
        "target.json": {"url": "https://example.invalid/repo", "commit": "abcdef1234"},
        "stage1.json": {"dependencies": [{"name": "a", "classification": "direct", "effective_spec": "==1.0"}],
                        "risk_indicators": [{"dependency": "a", "indicator": "frozen", "detail": "old"}]},
        "stage2.json": {"pythons": {"3.12": {"attempts": [{"name": "as_written", "outcome": "pass",
                                                           "tests": {"counts": {"passed": 3}}}],
                                             "reference": "as_written"}},
                        "pins_reproducible": True},
        "stage4.json": {"cells": [{"id": CELL, "type": "upgrade", "dependency": "a", "reason": "1.0 -> 2.0"}],
                        "resolutions": {f"{CELL}|3.12": {"ok": True, "co_upgrades": []}}},
        "stage5.json": [{"cell": CELL, "python": "3.12", "result": "break",
                         "normal": {"counts": {"passed": 2, "failed": 1}}}],
        "stage67.json": {"diagnoses": [{"cell": CELL, "root_cause": "api_removed", "code_fixable": True}],
                         "repairs": [{"cell": CELL, "verdict": "verified", "verified_attempt": 1, "attempts": [
                             {"attempt": 1, "verdict": "verified",
                              "static": {"no_test_edits": {"ok": True}},
                              "executed": {"3.12": {"rules": {"no_regressions": {"ok": True}}}}}]}]},
    }
    if capsule:
        files["capsule.json"] = {"meta": {}}
        (d / "capsule.html").write_text("<html></html>", encoding="utf-8")
    for name, data in files.items():
        (d / name).write_text(json.dumps(data), encoding="utf-8")
    return d


# ------------------------------------------------------------------------------------------- events


def test_synthesize_replays_stage_files_in_pipeline_order(tmp_path: Path) -> None:
    ev = events.synthesize(write_run(tmp_path))
    assert ev[0]["kind"] == "run" and ev[-1]["kind"] == "end"
    assert all(e.get("source_file") for e in ev), "every event names the file it came from"
    done = [e["stage"] for e in ev if e["kind"] == "stage" and e["status"] == "done"]
    assert done == ["inspect", "baseline", "cells", "matrix", "diagnose", "repair", "capsule"]
    job = next(e for e in ev if e["kind"] == "job")
    assert (job["cell"], job["python"], job["result"]) == (CELL, "3.12", "break")
    patch = next(e for e in ev if e["kind"] == "patch")
    assert patch["rules"] == {"no_regressions": {"3.12": True}}
    assert patch["static"] == {"no_test_edits": True}


def test_live_sink_writes_the_same_events_as_a_replay(tmp_path: Path) -> None:
    d = write_run(tmp_path)
    events.open_sink(d)
    try:
        events.emit_file(d, "stage1.json")
        events.emit("end", run=RUN)
    finally:
        events._sink = None  # noqa: SLF001 - leave the module as library use expects it
    rows = [json.loads(x) for x in (d / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    assert [r["kind"] for r in rows] == [e["kind"] for e in events.file_events(d, "stage1.json")] + ["end"]
    # Reopening (a resumed run) continues the sequence instead of restarting it.
    events.open_sink(d)
    try:
        events.emit("ping")
    finally:
        events._sink = None  # noqa: SLF001
    last = json.loads((d / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["seq"] == len(rows) + 1


def test_emit_without_a_sink_is_a_no_op(tmp_path: Path) -> None:
    events._sink = None  # noqa: SLF001
    events.emit("job", cell="x")
    assert not list(tmp_path.iterdir())


# ------------------------------------------------------------------------------------------- server


def test_list_runs_skips_non_runs_and_tallies_results(tmp_path: Path) -> None:
    write_run(tmp_path)
    (tmp_path / "not-a-run").mkdir()
    (tmp_path / "20261015-000001").mkdir()  # started but never inspected
    runs = App(tmp_path, tmp_path).list_runs()
    assert [r["id"] for r in runs] == [RUN]
    assert runs[0]["results"] == {"break": 1}
    assert runs[0]["verdicts"] == {"verified": 1}
    assert runs[0]["commit"] == "abcdef1"


def test_finished_run_is_replayed_from_its_stage_files(tmp_path: Path) -> None:
    d = write_run(tmp_path)
    (d / "events.jsonl").write_text(json.dumps({"seq": 1, "kind": "stale"}) + "\n", encoding="utf-8")
    kinds = [e["kind"] for e in App(tmp_path, tmp_path).events_for(RUN)]
    assert "stale" not in kinds and kinds[0] == "run"


@pytest.mark.parametrize("repo", ["", "  ", "a\nb", "no/such/folder"])
def test_start_live_rejects_bad_repos_before_starting(tmp_path: Path, repo: str) -> None:
    with pytest.raises(ValueError):
        App(tmp_path / "runs", tmp_path).start_live({"repo": repo})


@pytest.mark.parametrize(("headers", "ok"), [
    ({"Host": "127.0.0.1:8770"}, True),
    ({"Host": "localhost:8770", "Origin": "http://localhost:5173"}, True),
    ({"Host": "[::1]:8770"}, True),
    ({"Host": "evil.example:8770"}, False),  # DNS rebinding onto 127.0.0.1
    ({"Host": "127.0.0.1:8770", "Origin": "https://evil.example"}, False),
    ({}, False),
])
def test_is_local_request(headers: dict[str, str], ok: bool) -> None:
    assert is_local_request(headers) is ok


@pytest.fixture
def server(tmp_path: Path):
    write_run(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(tmp_path, tmp_path)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def request(port: int, method: str, path: str, *, body: str | None = None,
            headers: dict[str, str] | None = None) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except json.JSONDecodeError:
        return resp.status, {}


def test_http_runs_and_events(server: int) -> None:
    status, runs = request(server, "GET", "/api/runs")
    assert status == 200 and runs[0]["id"] == RUN
    status, data = request(server, "GET", f"/api/runs/{RUN}/events")
    assert status == 200 and data["run"]["id"] == RUN and data["events"][-1]["kind"] == "end"
    assert request(server, "GET", "/api/runs/..%2F..%2Fetc/events")[0] == 400


def test_http_run_files_cannot_escape_the_run_directory(server: int) -> None:
    assert request(server, "GET", f"/runs/{RUN}/capsule.html")[0] == 200
    assert request(server, "GET", f"/runs/{RUN}/..%2F..%2Fsecret.txt")[0] == 403


def test_http_refuses_cross_site_live_runs(server: int) -> None:
    body = json.dumps({"repo": "https://example.invalid/repo"})
    # A plain form/no-cors POST from another site: no JSON content type.
    assert request(server, "POST", "/api/live", body=body, headers={"Content-Type": "text/plain"})[0] == 415
    # A foreign Origin, even with the right content type.
    status, _ = request(server, "POST", "/api/live", body=body,
                        headers={"Content-Type": "application/json", "Origin": "https://evil.example"})
    assert status == 403
    # A rebound hostname pointing at 127.0.0.1.
    assert request(server, "GET", "/api/runs", headers={"Host": "evil.example"})[0] == 403


def test_http_live_start_reports_bad_repo(server: int) -> None:
    status, data = request(server, "POST", "/api/live", body=json.dumps({"repo": "no/such/folder"}),
                           headers={"Content-Type": "application/json"})
    assert status == 400 and "no such folder" in data["error"]


# ------------------------------------------------------------------------------------------- colour


def test_clean_env_turns_colour_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    env = clean_env()
    assert env["NO_COLOR"] == "1" and "FORCE_COLOR" not in env and "CLICOLOR_FORCE" not in env


def test_strip_ansi() -> None:
    assert strip_ansi("\x1b[2mResolved \x1b[1m5 packages\x1b[0m") == "Resolved 5 packages"
    assert strip_ansi(" \x1b[32m+\x1b[39m \x1b[1mcolorama\x1b[0m\x1b[2m==0.4.4\x1b[0m") == " + colorama==0.4.4"
