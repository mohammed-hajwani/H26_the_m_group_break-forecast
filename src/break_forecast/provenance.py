"""Provenance-tagged records.

Every item that ends up in the capsule is a dict carrying a ``provenance`` key, so
observed facts, executed results and AI output can never be mixed up silently.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

Provenance = Literal["observed", "executed", "ai_generated"]
PROVENANCES: tuple[str, ...] = ("observed", "executed", "ai_generated")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def record(provenance: Provenance, source: Any = None, **fields: Any) -> dict[str, Any]:
    """Build a capsule record. ``source`` says where the fact came from (URL, file:line, command)."""
    if provenance not in PROVENANCES:
        raise ValueError(f"unknown provenance {provenance!r}")
    rec: dict[str, Any] = dict(fields)
    rec["provenance"] = provenance
    if source is not None:
        rec["source"] = source
    return rec


def observed(source: Any = None, **fields: Any) -> dict[str, Any]:
    return record("observed", source, **fields)


def executed(source: Any = None, **fields: Any) -> dict[str, Any]:
    return record("executed", source, **fields)


def ai_generated(source: Any = None, **fields: Any) -> dict[str, Any]:
    return record("ai_generated", source, **fields)


def file_source(path: str, line: int | None = None) -> dict[str, Any]:
    return {"kind": "file", "path": path, "line": line}


def missing_provenance(capsule: dict[str, Any]) -> list[str]:
    """Return JSON paths of capsule records that lack a valid provenance tag.

    A record is any dict directly inside a top-level section list, or a top-level
    section that is itself a dict. ``meta`` and ``reproducibility`` are records too.
    """
    problems: list[str] = []
    for section, value in capsule.items():
        if section in ("schema_version",):
            continue
        items = value if isinstance(value, list) else [value]
        for i, item in enumerate(items):
            path = f"{section}[{i}]" if isinstance(value, list) else section
            if not isinstance(item, dict) or item.get("provenance") not in PROVENANCES:
                problems.append(path)
    return problems
