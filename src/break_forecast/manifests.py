"""Parse every dependency manifest and the install/test commands that reference them."""

from __future__ import annotations

import ast
import configparser
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from . import TARGET_PYTHONS

SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "build", "dist", ".tox",
             ".nox", ".mypy_cache", ".pytest_cache", "site-packages"}
DEV_HINT = re.compile(r"(dev|test|lint|doc|ci)", re.IGNORECASE)
PIP_INSTALL = re.compile(r"pip3?\s+install\b(?P<args>[^\n&;|]*)")
TEST_CMD = re.compile(
    r"(python[\d.]*\s+-m\s+pytest|py\.test|\bpytest\b|nosetests|python[\d.]*\s+-m\s+unittest|\btox\b)"
)


@dataclass
class DeclaredDep:
    name: str
    raw: str
    spec: str
    file: str
    line: int | None
    group: str  # runtime | dev | build | extra:<name>
    markers: str | None = None
    pinned_version: str | None = None
    effective: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Manifests:
    deps: list[DeclaredDep] = field(default_factory=list)
    commented: list[dict[str, Any]] = field(default_factory=list)
    requires_python: list[dict[str, Any]] = field(default_factory=list)
    install_commands: list[dict[str, Any]] = field(default_factory=list)
    test_commands: list[dict[str, Any]] = field(default_factory=list)
    lockfiles: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    effective_files: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


def _pinned(spec: SpecifierSet) -> str | None:
    specs = list(spec)
    if len(specs) == 1 and specs[0].operator in ("==", "===") and "*" not in specs[0].version:
        return specs[0].version
    return None


def _make_dep(raw: str, file: str, line: int | None, group: str) -> DeclaredDep:
    req = Requirement(raw)
    return DeclaredDep(
        name=canonicalize_name(req.name),
        raw=raw.strip(),
        spec=str(req.specifier),
        file=file,
        line=line,
        group=group,
        markers=str(req.marker) if req.marker else None,
        pinned_version=_pinned(req.specifier),
    )


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def walk_files(root: Path, pattern: str) -> list[Path]:
    out = []
    for p in root.rglob(pattern):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        out.append(p)
    return sorted(out)


def parse_requirements(path: Path, root: Path, m: Manifests, group: str | None = None,
                       seen: set[Path] | None = None) -> None:
    seen = seen if seen is not None else set()
    path = path.resolve()
    if path in seen or not path.exists():
        return
    seen.add(path)
    rel = _rel(path, root)
    m.files_read.append(rel)
    group = group or ("dev" if DEV_HINT.search(path.name) else "runtime")
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            body = stripped.lstrip("#").strip()
            try:
                req = Requirement(body)
                m.commented.append({"name": canonicalize_name(req.name), "raw": body, "file": rel,
                                    "line": lineno})
            except InvalidRequirement:
                pass
            continue
        stripped = stripped.split(" #", 1)[0].strip()
        if stripped.startswith(("-r", "--requirement", "-c", "--constraint")):
            target = stripped.split(maxsplit=1)[-1] if " " in stripped else stripped[2:]
            parse_requirements(path.parent / target.strip(), root, m, None, seen)
            continue
        if stripped.startswith("-") or "://" in stripped or stripped.startswith("."):
            continue
        try:
            m.deps.append(_make_dep(stripped, rel, lineno, group))
        except InvalidRequirement as exc:
            m.errors.append({"file": rel, "line": lineno, "error": str(exc)})


def _find_line(text: str, needle: str) -> int | None:
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return None


def parse_pyproject(path: Path, root: Path, m: Manifests) -> None:
    rel = _rel(path, root)
    text = path.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        m.errors.append({"file": rel, "error": str(exc)})
        return
    m.files_read.append(rel)
    project = data.get("project") or {}

    def add(raw: str, group: str) -> None:
        try:
            m.deps.append(_make_dep(raw, rel, _find_line(text, f'"{raw}"'), group))
        except InvalidRequirement as exc:
            m.errors.append({"file": rel, "error": str(exc)})

    for raw in project.get("dependencies") or []:
        add(raw, "runtime")
    for extra, reqs in (project.get("optional-dependencies") or {}).items():
        for raw in reqs:
            add(raw, "dev" if DEV_HINT.search(extra) else f"extra:{extra}")
    for gname, reqs in (data.get("dependency-groups") or {}).items():
        for raw in reqs:
            if isinstance(raw, str):
                add(raw, "dev" if DEV_HINT.search(gname) else f"extra:{gname}")
    for raw in (data.get("build-system") or {}).get("requires") or []:
        add(raw, "build")
    if project.get("requires-python"):
        m.requires_python.append({"file": rel, "line": _find_line(text, "requires-python"),
                                  "spec": project["requires-python"]})


def parse_setup_cfg(path: Path, root: Path, m: Manifests) -> None:
    rel = _rel(path, root)
    cfg = configparser.ConfigParser()
    try:
        cfg.read(path, encoding="utf-8")
    except configparser.Error as exc:
        m.errors.append({"file": rel, "error": str(exc)})
        return
    m.files_read.append(rel)
    if cfg.has_option("options", "install_requires"):
        for raw in cfg.get("options", "install_requires").splitlines():
            if raw.strip():
                m.deps.append(_make_dep(raw.strip(), rel, None, "runtime"))
    if cfg.has_option("options", "python_requires"):
        m.requires_python.append({"file": rel, "line": None,
                                  "spec": cfg.get("options", "python_requires")})
    if cfg.has_section("options.extras_require"):
        for extra, val in cfg.items("options.extras_require"):
            for raw in val.splitlines():
                if raw.strip():
                    group = "dev" if DEV_HINT.search(extra) else f"extra:{extra}"
                    m.deps.append(_make_dep(raw.strip(), rel, None, group))


def parse_setup_py(path: Path, root: Path, m: Manifests) -> None:
    """Static read of literal install_requires lists. setup.py is never executed."""
    rel = _rel(path, root)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        m.errors.append({"file": rel, "error": str(exc)})
        return
    m.files_read.append(rel)
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in ("install_requires", "tests_require"):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                group = "runtime" if node.arg == "install_requires" else "dev"
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        m.deps.append(_make_dep(elt.value, rel, elt.lineno, group))


def scan_commands(root: Path, m: Manifests) -> None:
    """Find pip install and test commands in Makefile, Dockerfile, tox.ini and CI configs."""
    candidates = [root / "Makefile", root / "tox.ini", root / "noxfile.py", root / "justfile"]
    candidates += walk_files(root, "Dockerfile*")
    candidates += sorted((root / ".github" / "workflows").glob("*.y*ml")) if (root / ".github").exists() else []
    candidates += [root / ".gitlab-ci.yml", root / ".circleci" / "config.yml"]
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        rel = _rel(path, root)
        m.files_read.append(rel)
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for match in PIP_INSTALL.finditer(line):
                args = match.group("args").split()
                req_files = [args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-r", "--requirement")]
                project_args = [a.strip("'\"") for a in args if a.strip("'\"") == "." or
                                a.strip("'\"").startswith((".[", "-e.", "-e.["))]
                installs_project = bool(project_args) or "-e" in args and "." in args
                extras = sorted({e.strip() for a in project_args for e in
                                 (re.search(r"\[(.*)\]", a).group(1).split(",") if "[" in a else []) if e.strip()})
                m.install_commands.append({"file": rel, "line": lineno, "text": line.strip(),
                                           "requirement_files": req_files,
                                           "installs_project": installs_project, "project_extras": extras})
            if TEST_CMD.search(line) and "pip" not in line:
                m.test_commands.append({"file": rel, "line": lineno, "text": line.strip()})


def collect(root: Path) -> Manifests:
    root = root.resolve()
    m = Manifests()
    seen: set[Path] = set()
    for path in sorted(root.glob("*requirements*.txt")) + sorted(root.glob("requirements/*.txt")):
        parse_requirements(path, root, m, None, seen)
    if (root / "pyproject.toml").exists():
        parse_pyproject(root / "pyproject.toml", root, m)
    if (root / "setup.cfg").exists():
        parse_setup_cfg(root / "setup.cfg", root, m)
    if (root / "setup.py").exists():
        parse_setup_py(root / "setup.py", root, m)
    for lock in ("poetry.lock", "uv.lock", "Pipfile.lock", "pdm.lock", "requirements.lock"):
        if (root / lock).exists():
            m.lockfiles.append(lock)
    scan_commands(root, m)
    m.effective_files = effective_files(root, m)
    for dep in m.deps:
        dep.effective = dep.file in m.effective_files and dep.group != "build"
    return m


def effective_files(root: Path, m: Manifests) -> list[str]:
    """The manifests that the repo's own install commands actually use."""
    files: list[str] = []
    for cmd in m.install_commands:
        base = (root / cmd["file"]).parent
        for f in cmd["requirement_files"]:
            p = (base / f).resolve()
            if not p.exists():
                p = (root / f).resolve()
            if p.exists():
                files.append(_rel(p, root))
        if cmd["installs_project"]:
            for name in ("pyproject.toml", "setup.cfg", "setup.py"):
                if (root / name).exists():
                    files.append(name)
                    break
    if not files:
        if (root / "requirements.txt").exists():
            files.append("requirements.txt")
        elif (root / "pyproject.toml").exists():
            files.append("pyproject.toml")
    # Requirement files pulled in with -r from an effective file are effective too.
    out: list[str] = []
    for f in files:
        if f not in out:
            out.append(f)
    return out


def drift(m: Manifests) -> list[dict[str, Any]]:
    """Disagreements between manifests, as plain dicts (caller tags provenance)."""
    findings: list[dict[str, Any]] = []
    runtime_like = [d for d in m.deps if d.group in ("runtime", "dev")]
    by_name: dict[str, list[DeclaredDep]] = {}
    for d in runtime_like:
        by_name.setdefault(d.name, []).append(d)
    for name, decls in sorted(by_name.items()):
        specs = {(d.file, d.spec) for d in decls}
        distinct = {d.spec for d in decls}
        if len(distinct) > 1:
            findings.append({
                "kind": "spec_mismatch",
                "dependency": name,
                "conflicting": _specs_conflict(decls),
                "declarations": [{"file": f, "spec": s or "(any)"} for f, s in sorted(specs)],
            })
    runtime_files = sorted({d.file for d in m.deps if d.group == "runtime"})
    if len(runtime_files) > 1:
        sets = {f: {d.name for d in m.deps if d.group == "runtime" and d.file == f} for f in runtime_files}
        for f, names in sets.items():
            for other, other_names in sets.items():
                for name in sorted(names - other_names):
                    findings.append({"kind": "missing_in_manifest", "dependency": name,
                                     "declared_in": f, "missing_from": other})
    for rp in m.requires_python:
        try:
            spec = SpecifierSet(rp["spec"])
        except Exception:
            continue
        excluded = [v for v in TARGET_PYTHONS if Version(v) not in spec]
        lower = [s.version for s in spec if s.operator in (">=", ">", "~=")]
        if excluded:
            findings.append({"kind": "requires_python_excludes_targets", "file": rp["file"],
                             "spec": rp["spec"], "excluded_targets": excluded})
        elif lower and Version(lower[0]) < Version(TARGET_PYTHONS[0]):
            findings.append({"kind": "requires_python_wider_than_targets", "file": rp["file"],
                             "spec": rp["spec"], "targets": list(TARGET_PYTHONS),
                             "note": "declares support for Python versions outside the tested targets"})
    return findings


def _specs_conflict(decls: list[DeclaredDep]) -> bool:
    """True if one declaration pins a version that another declaration's specifier rejects."""
    for a in decls:
        if not a.pinned_version:
            continue
        for b in decls:
            if b is a or not b.spec:
                continue
            if Version(a.pinned_version) not in SpecifierSet(b.spec):
                return True
    return False
