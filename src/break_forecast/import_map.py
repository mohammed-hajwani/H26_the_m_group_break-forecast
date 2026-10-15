"""Map import names to distributions with importlib.metadata.packages_distributions().

Stage 1 runs before any baseline exists, so it installs the declared distributions
(latest versions, --no-deps) into a throwaway metadata env and asks that env. Stage 2
recomputes the map inside the real baseline env.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import envs
from .runctx import run_cmd

MAP_SCRIPT = (
    "import json, importlib.metadata as m;"
    "print(json.dumps({k: sorted(set(v)) for k, v in m.packages_distributions().items()}))"
)


def map_in_env(python: str | Path, log_path: Path | None = None) -> dict[str, list[str]]:
    res = run_cmd([str(python), "-c", MAP_SCRIPT], timeout=60, log_path=log_path)
    if not res.ok:
        raise envs.EnvError(f"packages_distributions failed: {res.stderr[-300:]}")
    raw: dict[str, list[str]] = json.loads(res.stdout.strip().splitlines()[-1])
    return {k: [d.lower() for d in v] for k, v in raw.items()}


def metadata_env_map(dists: list[tuple[str, str | None]], env_dir: Path, log_dir: Path,
                     python_version: str = "3.12") -> tuple[dict[str, list[str]], dict[str, Any]]:
    """Install (name, pinned_version) pairs without dependencies and return (import map, details).

    The pinned version is preferred, because module layout can change between releases
    (e.g. a module dropped in a later version). If it cannot install, the latest is used.
    """
    py = envs.create_env(env_dir, python_version, log_dir)
    wanted = [f"{n}=={v}" if v else n for n, v in dists]
    installed: dict[str, str] = {}
    failed: list[str] = []
    res = envs.pip_install(py, ["--no-deps", *wanted], log_path=log_dir / "import-map-install.log")
    if res.ok:
        installed = {n: (f"=={v}" if v else "latest") for n, v in dists}
    else:
        for (name, ver), spec in zip(dists, wanted, strict=True):  # one bad package must not hide the others
            one = envs.pip_install(py, ["--no-deps", spec], log_path=log_dir / f"import-map-{name}.log")
            if one.ok:
                installed[name] = f"=={ver}" if ver else "latest"
                continue
            if ver:
                latest = envs.pip_install(py, ["--no-deps", name],
                                          log_path=log_dir / f"import-map-{name}-latest.log")
                if latest.ok:
                    installed[name] = "latest (pinned version not installable)"
                    continue
            failed.append(name)
    mapping = map_in_env(py, log_dir / "import-map.log")
    return mapping, {"method": "importlib.metadata.packages_distributions() in a --no-deps metadata env",
                     "python": python_version, "installed": installed, "not_installable": failed}


def resolve(module: str, mapping: dict[str, list[str]]) -> list[str]:
    return mapping.get(module, [])
