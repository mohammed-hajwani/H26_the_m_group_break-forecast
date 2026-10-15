"""Run directory, shared cache and subprocess helper."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)
# Launchers (IDEs, dev-server panes) often set these; uv and pip then colour piped
# output, and the ANSI codes break every parser that reads it.
FORCE_COLOR_ENV = ("FORCE_COLOR", "CLICOLOR_FORCE", "PY_COLORS", "UV_FORCE_COLOR")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def cache_root() -> Path:
    root = Path(os.environ.get("BF_CACHE_DIR") or Path.cwd() / ".bf-cache")
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class Run:
    root: Path

    @classmethod
    def create(cls, base: str | Path = "runs") -> Run:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        root = Path(base).resolve() / stamp
        n = 1
        while root.exists():
            root = Path(base).resolve() / f"{stamp}-{n}"
            n += 1
        for sub in ("logs", "envs", "work"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        return cls(root)

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def envs(self) -> Path:
        return self.root / "envs"

    @property
    def work(self) -> Path:
        return self.root / "work"

    def write_json(self, name: str, data: Any) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return path


@dataclass
class CmdResult:
    cmd: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    log_path: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def summary(self) -> dict[str, Any]:
        return {
            "cmd": self.cmd,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "duration_s": round(self.duration_s, 2),
            "log": self.log_path,
        }


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for running untrusted target code: no secrets, no inherited venv."""
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    for k in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX", *FORCE_COLOR_ENV):
        env.pop(k, None)
    env["NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    if extra:
        env.update(extra)
    return env


def run_cmd(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    timeout: float = 300,
    log_path: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> CmdResult:
    """Run a command (never via a shell) with a timeout, capturing output to a log file."""
    started = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env if env is not None else clean_env(),
        )
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        rc = None
        out = _text(exc.stdout)
        err = _text(exc.stderr) + f"\n[break-forecast] timed out after {timeout}s"
    except FileNotFoundError as exc:
        rc, out, err = 127, "", str(exc)
    out, err = strip_ansi(out or ""), strip_ansi(err or "")
    result = CmdResult([str(c) for c in cmd], rc, out, err, time.monotonic() - started, timed_out)
    if log_path:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"$ {' '.join(result.cmd)}\n# cwd={cwd} rc={rc} timed_out={timed_out} "
            f"duration={result.duration_s:.2f}s\n\n--- stdout ---\n{out}\n--- stderr ---\n{err}\n",
            encoding="utf-8",
        )
        result.log_path = str(log_path)
    return result


def _text(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data
