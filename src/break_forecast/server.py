"""`bf serve`: a local web UI for watching runs live and replaying recorded ones.

Stdlib only. Binds to 127.0.0.1. The UI is a prebuilt React app shipped in `web_dist/`.

  GET  /api/runs                      recorded runs (newest first) with a short summary
  GET  /api/runs/<id>/events          the run's full event list (events.jsonl, or synthesized from stage files)
  POST /api/live                      start `bf run` on a repo; body {"repo", "offline_model", "no_ai", "fresh_ai"}
  GET  /api/live/<id>/stream          server-sent events: the run's events + console lines as they happen
  POST /api/live/<id>/stop            stop a live run
  GET  /runs/<id>/<file>              files inside a run directory (capsule.html, logs, ...)
"""

from __future__ import annotations

import json
import mimetypes
import re
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from . import events
from .runctx import clean_env, strip_ansi

RUN_ID = re.compile(r"^\d{8}-\d{6}(?:-\d+)?$")
DIST = Path(__file__).resolve().parent / "web_dist"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_BODY = 64 * 1024


def is_local_request(headers: Any) -> bool:
    """True when both the Host and (if sent) the Origin name this machine.

    `POST /api/live` runs a repository's tests, so a web page in the user's browser must not be able to
    reach it: a foreign Origin is refused, and checking Host defeats DNS rebinding onto 127.0.0.1.
    """
    host = urlparse("//" + (headers.get("Host") or "")).hostname or ""
    if host.lower() not in LOCAL_HOSTS:
        return False
    origin = headers.get("Origin")
    return not origin or (urlparse(origin).hostname or "").lower() in LOCAL_HOSTS


class Live:
    """A `bf run` subprocess and the console lines it printed."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self.proc = proc
        self.run_id: str | None = None
        self.lines: deque[tuple[int, str]] = deque(maxlen=4000)
        self.n = 0
        self.ready = threading.Event()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            line = strip_ansi(line.rstrip("\n"))
            self.n += 1
            self.lines.append((self.n, line))
            m = re.search(r"run directory: .*[\\/](\d{8}-\d{6}(?:-\d+)?)\s*$", line)
            if m and not self.run_id:
                self.run_id = m.group(1)
                self.ready.set()
        self.ready.set()

    @property
    def running(self) -> bool:
        return self.proc.poll() is None


class App:
    def __init__(self, runs_dir: Path, project_dir: Path) -> None:
        self.runs_dir = runs_dir.resolve()
        self.project_dir = project_dir.resolve()
        self.live: dict[str, Live] = {}

    # ---------------------------------------------------------------- recorded runs
    def list_runs(self) -> list[dict[str, Any]]:
        out = []
        if not self.runs_dir.exists():
            return out
        for d in sorted(self.runs_dir.iterdir(), reverse=True):
            if not d.is_dir() or not RUN_ID.match(d.name) or not (d / "stage1.json").exists():
                continue
            out.append(self.summary(d))
        return out

    def summary(self, d: Path) -> dict[str, Any]:
        def load(n: str) -> Any:
            p = d / n
            try:
                return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
            except (OSError, json.JSONDecodeError):
                return None

        target = load("target.json") or ((load("capsule.json") or {}).get("meta") or {}).get("target") or {}
        s5 = load("stage5.json") or []
        s67 = load("stage67.json") or {}
        verdicts: dict[str, int] = {}
        for r in s67.get("repairs", []):
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        results: dict[str, int] = {}
        for r in s5:
            results[r["result"]] = results.get(r["result"], 0) + 1
        stages = [n for n in ("stage1.json", "stage2.json", "stage4.json", "stage3.json", "probes.json",
                              "stage5.json", "stage67.json", "capsule.json") if (d / n).exists()]
        live = self.live.get(d.name)
        return {"id": d.name, "target": target.get("url") or target.get("path") or target.get("repo"),
                "commit": (target.get("commit") or "")[:7], "stages": stages, "results": results,
                "verdicts": verdicts, "has_capsule": (d / "capsule.html").exists(),
                "has_events": (d / "events.jsonl").exists(), "live": bool(live and live.running),
                "exit_plan": (s67.get("combined") or {}).get("verdict")}

    def events_for(self, run_id: str) -> list[dict[str, Any]]:
        d = self.runs_dir / run_id
        live = self.live.get(run_id)
        jsonl = d / "events.jsonl"
        finished = (d / "capsule.json").exists() and not (live and live.running)
        if jsonl.exists() and not finished:
            return [json.loads(x) for x in jsonl.read_text(encoding="utf-8").splitlines() if x.strip()]
        # A finished run is replayed from its stage files: they are the record the capsule was built from.
        return events.synthesize(d)

    # ---------------------------------------------------------------- live runs
    def start_live(self, body: dict[str, Any]) -> dict[str, Any]:
        repo = str(body.get("repo") or "").strip()
        if not repo or any(c in repo for c in "\r\n\0"):
            raise ValueError("repo must be a git URL or a local path")
        from .target import is_url

        if not is_url(repo):
            # Relative paths mean "relative to the project bf serve was started for", not to wherever the
            # server process happens to run. Check before starting, so a typo fails here, not mid-run.
            path = Path(repo)
            path = path if path.is_absolute() else self.project_dir / path
            if not path.is_dir():
                raise ValueError(f"{repo}: no such folder (looked in {path})")
            repo = str(path.resolve())
        cmd = [sys.executable, "-m", "break_forecast.cli", "run", repo, "--runs-dir", str(self.runs_dir)]
        for flag in ("offline_model", "no_ai", "fresh_ai"):
            if body.get(flag):
                cmd.append("--" + flag.replace("_", "-"))
        env = clean_env({"PYTHONUNBUFFERED": "1"})
        # The model key lives in .env, which llm.py loads itself; secrets are not passed on the command line.
        proc = subprocess.Popen(cmd, cwd=self.project_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", env=env)
        live = Live(proc)
        live.ready.wait(timeout=60)
        if not live.run_id:
            proc.terminate()
            tail = "\n".join(line for _, line in list(live.lines)[-15:])
            raise RuntimeError(f"run did not start:\n{tail}")
        self.live[live.run_id] = live
        return {"run": live.run_id}

    def stop_live(self, run_id: str) -> bool:
        live = self.live.get(run_id)
        if live and live.running:
            live.proc.terminate()
            return True
        return False


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "break-forecast"

        def log_message(self, fmt: str, *args: Any) -> None:  # keep the console for the run output
            pass

        # ------------------------------------------------------------ helpers
        def _json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path) -> None:
            if not path.is_file():
                self._json({"error": "not found"}, 404)
                return
            data = path.read_bytes()
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _safe_child(self, base: Path, rel: str) -> Path | None:
            p = (base / rel).resolve()
            return p if p == base or base in p.parents else None

        # ------------------------------------------------------------ GET
        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            url = urlparse(self.path)
            parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
            if not is_local_request(self.headers):
                return self._json({"error": "forbidden host or origin"}, 403)
            try:
                if parts[:2] == ["api", "runs"] and len(parts) == 2:
                    return self._json(app.list_runs())
                if parts[:2] == ["api", "runs"] and len(parts) == 4 and parts[3] == "events":
                    if not RUN_ID.match(parts[2]):
                        return self._json({"error": "bad run id"}, 400)
                    return self._json({"run": app.summary(app.runs_dir / parts[2]),
                                       "events": app.events_for(parts[2])})
                if parts[:2] == ["api", "live"] and len(parts) == 4 and parts[3] == "stream":
                    return self._stream(parts[2])
                if parts and parts[0] == "runs" and len(parts) >= 3 and RUN_ID.match(parts[1]):
                    target = self._safe_child(app.runs_dir / parts[1], "/".join(parts[2:]))
                    return self._file(target) if target else self._json({"error": "forbidden"}, 403)
                # The single-page app: real files, otherwise index.html
                rel = "/".join(parts) or "index.html"
                target = self._safe_child(DIST, rel)
                if target and target.is_file():
                    return self._file(target)
                return self._file(DIST / "index.html")
            except (BrokenPipeError, ConnectionResetError):
                return

        def _stream(self, run_id: str) -> None:
            if not RUN_ID.match(run_id):
                return self._json({"error": "bad run id"}, 400)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            jsonl = app.runs_dir / run_id / "events.jsonl"
            sent_events = 0
            sent_lines = 0
            idle = 0
            try:
                while True:
                    live = app.live.get(run_id)
                    progressed = False
                    if jsonl.exists():
                        rows = jsonl.read_text(encoding="utf-8").splitlines()
                        for row in rows[sent_events:]:
                            if row.strip():
                                self.wfile.write(f"event: run\ndata: {row}\n\n".encode())
                                progressed = True
                        sent_events = len(rows)
                    if live:
                        for n, line in list(live.lines):
                            if n > sent_lines:
                                self.wfile.write(f"event: log\ndata: {json.dumps({'n': n, 'line': line})}\n\n".encode())
                                sent_lines = n
                                progressed = True
                    if live is None or not live.running:
                        code = live.proc.returncode if live else None
                        self.wfile.write(f"event: closed\ndata: {json.dumps({'exit': code})}\n\n".encode())
                        self.wfile.flush()
                        return
                    if not progressed:
                        idle += 1
                        if idle % 30 == 0:  # keep proxies and the browser from dropping a quiet stream
                            self.wfile.write(b": ping\n\n")
                    else:
                        idle = 0
                    self.wfile.flush()
                    time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        # ------------------------------------------------------------ POST
        def do_POST(self) -> None:  # noqa: N802 - stdlib API
            parts = [p for p in urlparse(self.path).path.strip("/").split("/") if p]
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY:
                self.close_connection = True
                return self._json({"error": "bad Content-Length"}, 413)
            # Read the body even when refusing: answering with it unread makes Windows reset the connection.
            raw = self.rfile.read(length) if length else b""
            if not is_local_request(self.headers):
                return self._json({"error": "forbidden host or origin"}, 403)
            # A JSON content type cannot be sent cross-site without a CORS preflight, which this server never
            # approves; together with the Origin check this keeps other sites from starting runs.
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                return self._json({"error": "Content-Type must be application/json"}, 415)
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "invalid JSON"}, 400)
            if not isinstance(body, dict):
                return self._json({"error": "body must be a JSON object"}, 400)
            try:
                if parts == ["api", "live"]:
                    return self._json(app.start_live(body), 201)
                if len(parts) == 4 and parts[:2] == ["api", "live"] and parts[3] == "stop" and RUN_ID.match(parts[2]):
                    return self._json({"stopped": app.stop_live(parts[2])})
                return self._json({"error": "not found"}, 404)
            except (ValueError, RuntimeError) as exc:
                return self._json({"error": str(exc)}, 400)

    return Handler


def serve(runs_dir: Path, project_dir: Path, port: int = 8770, open_browser: bool = False) -> None:
    if not (DIST / "index.html").exists():
        raise SystemExit("The web UI is not built: run `npm install && npm run build` in web/ first.")
    app = App(runs_dir, project_dir)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}/"
    print(f"[bf] serving the break-forecast UI at {url}  (runs: {app.runs_dir})  Ctrl+C to stop")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for live in app.live.values():
            if live.running:
                live.proc.terminate()
        httpd.server_close()
