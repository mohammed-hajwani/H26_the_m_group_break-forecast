"""AST scan of the target repo: imports, API usage sites, private-API use and monkeypatching."""

from __future__ import annotations

import ast
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .manifests import SKIP_DIRS

STDLIB = set(sys.stdlib_module_names) | {"__future__"}


@dataclass
class Usage:
    module: str          # top-level import name, e.g. "requests"
    file: str
    line: int
    api: str             # e.g. "requests.utils._parse_content_type_header" or "toml.load"
    kind: str            # import | from_import | attribute | call | monkeypatch
    in_tests: bool
    private: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UsageScan:
    usages: list[Usage] = field(default_factory=list)
    local_modules: set[str] = field(default_factory=set)
    files_scanned: list[str] = field(default_factory=list)
    parse_errors: list[dict[str, Any]] = field(default_factory=list)

    def third_party_modules(self) -> set[str]:
        return {u.module for u in self.usages}

    def by_module(self) -> dict[str, list[Usage]]:
        out: dict[str, list[Usage]] = {}
        for u in self.usages:
            out.setdefault(u.module, []).append(u)
        return out


def _is_private(dotted: str) -> bool:
    return any(p.startswith("_") and not (p.startswith("__") and p.endswith("__")) for p in dotted.split("."))


def _dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def python_files(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*.py"):
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel_parts[:-1]):
            continue
        out.append(p)
    return sorted(out)


def local_modules(root: Path, files: list[Path]) -> set[str]:
    names = set()
    for p in files:
        rel = p.relative_to(root).parts
        top = rel[0]
        if top in ("src", "lib") and len(rel) > 1:
            top = rel[1]
        names.add(top.removesuffix(".py"))
    return names


def is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return (any(p in ("tests", "test", "testing") for p in parts[:-1])
            or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py")


def scan(root: Path) -> UsageScan:
    root = root.resolve()
    files = python_files(root)
    result = UsageScan(local_modules=local_modules(root, files))
    for path in files:
        rel = path.relative_to(root).as_posix()
        result.files_scanned.append(rel)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=rel)
        except SyntaxError as exc:
            result.parse_errors.append({"file": rel, "error": str(exc)})
            continue
        _scan_tree(tree, rel, is_test_path(rel), result)
    return result


def _third_party(top: str, local: set[str]) -> bool:
    return top not in STDLIB and top not in local and not top.startswith("_")


def _scan_tree(tree: ast.AST, rel: str, in_tests: bool, out: UsageScan) -> None:
    aliases: dict[str, str] = {}  # local name -> dotted origin ("requests", "requests.Session")

    def add(module_path: str, line: int, kind: str) -> None:
        top = module_path.split(".")[0]
        if _third_party(top, out.local_modules):
            out.usages.append(Usage(top, rel, line, module_path, kind, in_tests, _is_private(module_path)))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[(a.asname or a.name).split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
                add(a.name, node.lineno, "import")
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                full = f"{node.module}.{a.name}"
                aliases[a.asname or a.name] = full
                add(full, node.lineno, "from_import")

    # Calls and monkeypatches first; a plain attribute read is only recorded when it is
    # not already covered by a call/monkeypatch or a longer chain on the same line.
    covered: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    resolved = _resolve(_dotted(target), aliases)
                    if resolved:
                        add(resolved, node.lineno, "monkeypatch")
                        covered.add((node.lineno, resolved))
        elif isinstance(node, ast.Call):
            resolved = _resolve(_dotted(node.func), aliases)
            if resolved:
                add(resolved, node.lineno, "call")
                covered.add((node.lineno, resolved))
    reads: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            resolved = _resolve(_dotted(node), aliases)
            if resolved and "." in resolved:
                reads.add((node.lineno, resolved))
    chains = covered | reads
    for line, api in sorted(reads):
        if (line, api) in covered:
            continue
        if any(ln == line and other.startswith(api + ".") for ln, other in chains):
            continue
        add(api, line, "attribute")


def _resolve(dotted: str | None, aliases: dict[str, str]) -> str | None:
    if not dotted:
        return None
    head, _, rest = dotted.partition(".")
    origin = aliases.get(head)
    if not origin:
        return None
    return f"{origin}.{rest}" if rest else origin
