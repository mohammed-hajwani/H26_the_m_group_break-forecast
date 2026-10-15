"""uv-based virtualenv manager. Cross-platform: never assumes bin/ vs Scripts\\."""

from __future__ import annotations

import os
import shutil
import sys
from functools import cache, lru_cache
from pathlib import Path

from . import TARGET_PYTHONS
from .runctx import CmdResult, clean_env, run_cmd


class EnvError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def uv_bin() -> str:
    """Locate the uv executable: PATH first, then the `uv` Python package's bundled binary."""
    found = shutil.which("uv")
    if found:
        return found
    try:
        from uv import find_uv_bin  # type: ignore[import-not-found]

        return find_uv_bin()
    except Exception as exc:  # pragma: no cover - depends on host
        raise EnvError("uv not found. Install it with `pip install uv`.") from exc


def uv_version() -> str:
    res = run_cmd([uv_bin(), "--version"], timeout=30, env=_uv_env())
    return res.stdout.strip()


def _uv_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = clean_env(extra)
    # Keep uv's own cache shared across cells; it is not secret.
    if "UV_CACHE_DIR" in os.environ:
        env["UV_CACHE_DIR"] = os.environ["UV_CACHE_DIR"]
    return env


def check_target_python(version: str) -> None:
    if version not in TARGET_PYTHONS:
        raise EnvError(
            f"target Python {version} is not allowed; only {', '.join(TARGET_PYTHONS)} are supported"
        )


@cache
def find_python(version: str) -> str:
    """Path to a uv-managed interpreter for a target version, installing it if needed."""
    check_target_python(version)
    res = run_cmd([uv_bin(), "python", "find", version], timeout=60, env=_uv_env())
    if not res.ok:
        install = run_cmd([uv_bin(), "python", "install", version], timeout=600, env=_uv_env())
        if not install.ok:
            raise EnvError(f"could not provision Python {version}: {install.stderr[-500:]}")
        res = run_cmd([uv_bin(), "python", "find", version], timeout=60, env=_uv_env())
    path = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
    if not path:
        raise EnvError(f"uv could not find Python {version}")
    return path


def env_python(env_dir: str | Path) -> Path:
    env_dir = Path(env_dir)
    for candidate in (env_dir / "Scripts" / "python.exe", env_dir / "bin" / "python"):
        if candidate.exists():
            return candidate
    raise EnvError(f"no interpreter in {env_dir}")


def create_env(env_dir: str | Path, version: str, log_dir: str | Path | None = None) -> Path:
    py = find_python(version)
    log = Path(log_dir) / f"venv-{Path(env_dir).name}.log" if log_dir else None
    res = run_cmd(
        [uv_bin(), "venv", "--seed", "--python", py, str(env_dir)],
        timeout=120,
        log_path=log,
        env=_uv_env(),
    )
    if not res.ok:
        raise EnvError(f"venv creation failed: {res.stderr[-500:]}")
    return env_python(env_dir)


def pip_install(
    python: str | Path,
    args: list[str],
    *,
    cwd: str | Path | None = None,
    exclude_newer: str | None = None,
    log_path: str | Path | None = None,
    timeout: float = 900,
    dry_run: bool = False,
) -> CmdResult:
    cmd = [uv_bin(), "pip", "install", "--python", str(python)]
    if exclude_newer:
        cmd += ["--exclude-newer", exclude_newer]
    if dry_run:
        cmd.append("--dry-run")
    cmd += args
    return run_cmd(cmd, cwd=cwd, timeout=timeout, log_path=log_path, env=_uv_env())


def freeze(python: str | Path) -> dict[str, str]:
    res = run_cmd([uv_bin(), "pip", "freeze", "--python", str(python)], timeout=60, env=_uv_env())
    pins: dict[str, str] = {}
    for line in res.stdout.splitlines():
        if "==" in line:
            name, _, ver = line.partition("==")
            pins[name.strip().lower()] = ver.strip()
    return pins


def tool_python() -> str:
    return sys.executable
