"""Stage 10: assemble, validate and write the Dependency Exit Capsule."""

from __future__ import annotations

import json
import platform
from collections import Counter
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from . import TARGET_PYTHONS, __version__, envs
from .provenance import missing_provenance, now_iso, observed

SECTION_ORDER = [
    "schema_version", "meta", "problem", "manifests", "dependencies", "manifest_drift", "import_map",
    "risk_indicators", "usage_locations", "baseline", "release_notes", "forecast", "cells", "upgrade_matrix",
    "diagnoses",
    "patches", "behavioral_diffs", "upgrade_plan", "replacement_suggestions", "suggested_tests",
    "reproducibility", "limitations",
]


def load_schema() -> dict[str, Any]:
    text = resources.files("break_forecast").joinpath("schema/capsule.schema.json").read_text("utf-8")
    return json.loads(text)


def problem_statement(stage1: dict[str, Any]) -> dict[str, Any]:
    """Deterministic summary of what Stage 1 found (templated from counts, not AI)."""
    labels = Counter(lbl for d in stage1["dependencies"] for lbl in d["classification"])
    indicators = Counter(r["indicator"] for r in stage1["risk_indicators"])
    parts = [f"{len(stage1['dependencies'])} dependencies inspected"]
    for key in ("frozen", "upgradable", "unused", "undeclared"):
        if labels.get(key):
            parts.append(f"{labels[key]} {key}")
    if indicators.get("manifest_drift"):
        parts.append(f"{indicators['manifest_drift']} manifest drift findings")
    return observed(
        source={"kind": "derived", "from": ["dependencies", "risk_indicators"]},
        summary="; ".join(parts) + ".",
        classification_counts=dict(labels),
        indicator_counts=dict(indicators),
        maintenance_task="dependency change testing and repair",
    )


def build(stage1: dict[str, Any], target: dict[str, Any], stages_run: list[str],
          extra_sections: dict[str, Any] | None = None, limitations: list[dict] | None = None,
          commands: list[str] | None = None) -> dict[str, Any]:
    capsule: dict[str, Any] = {
        "schema_version": "1",
        "meta": observed(
            source={"kind": "tool"},
            tool="break-forecast", tool_version=__version__, created_at=now_iso(), target=target,
            target_pythons=list(TARGET_PYTHONS), stages_run=stages_run,
            host={"os": platform.platform(), "tool_python": platform.python_version()},
        ),
        "problem": problem_statement(stage1),
        "manifests": stage1["manifests"],
        "dependencies": stage1["dependencies"],
        "manifest_drift": stage1["manifest_drift"],
        "import_map": stage1["import_map"],
        "risk_indicators": stage1["risk_indicators"],
        "usage_locations": stage1["usage_locations"],
        "reproducibility": observed(
            source={"kind": "tool"},
            commands=commands or [], uv_version=_safe_uv_version(),
        ),
        "limitations": list(limitations or []),
    }
    for err in stage1.get("fetch_errors", []):
        capsule["limitations"].append(observed(source={"kind": "fetch"}, item=err["dependency"],
                                               reason=f"metadata fetch failed: {err['error']}"))
    capsule.update(extra_sections or {})
    return {k: capsule[k] for k in SECTION_ORDER if k in capsule}


def validate(capsule: dict[str, Any]) -> list[str]:
    errors = [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}"
              for e in jsonschema.Draft202012Validator(load_schema()).iter_errors(capsule)]
    errors += [f"{p}: missing or invalid provenance" for p in missing_provenance(capsule)]
    return errors


def write(capsule: dict[str, Any], out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": out_dir / "capsule.json", "md": out_dir / "capsule.md", "html": out_dir / "capsule.html"}
    paths["json"].write_text(json.dumps(capsule, indent=2, default=str), encoding="utf-8")
    paths["md"].write_text(to_markdown(capsule), encoding="utf-8")
    paths["html"].write_text(to_html(capsule), encoding="utf-8")
    return paths


def to_html(capsule: dict[str, Any]) -> str:
    """Self-contained viewer: the template plus the capsule embedded as inert JSON (no network needed)."""
    template = resources.files("break_forecast").joinpath("assets/capsule-viewer.html").read_text("utf-8")
    data = json.dumps(capsule, default=str).replace("</", "<\\/")  # cannot close the <script> element
    return template.replace("/*__CAPSULE_JSON__*/", data)


def to_markdown(c: dict[str, Any]) -> str:
    lines = [f"# Dependency Exit Capsule: {c['meta']['target'].get('url') or c['meta']['target'].get('path')}",
             "", f"_Generated {c['meta']['created_at']} by break-forecast {c['meta']['tool_version']}. "
             "Tags: 🔍 observed · ▶️ executed · 🤖 AI-generated._", "",
             f"**Problem (🔍):** {c['problem']['summary']}", "", "## Dependencies (🔍)", "",
             "| Dependency | Spec | Latest | Last release | Classification |", "|---|---|---|---|---|"]
    for d in c["dependencies"]:
        lines.append(f"| {d['name']} | {d.get('effective_spec') or '-'} | {d.get('latest') or '-'} | "
                     f"{(d.get('last_release_date') or '-')[:10]} | {', '.join(d['classification'])} |")
    if c.get("baseline"):
        b = c["baseline"]
        lines += ["", "## Baseline (▶️ executed)", "",
                  f"Install: `uv pip install {' '.join(b['install_args'])}` · "
                  f"pins reproducible as written: **{b['pins_reproducible']}**", "",
                  "| Python | Attempt | Outcome | Passed/failed/error | Notes |", "|---|---|---|---|---|"]
        for py, entry in b["pythons"].items():
            for att in entry["attempts"]:
                counts = (att.get("tests") or {}).get("counts") or {}
                note = ", ".join(f"{k}<{v}" for k, v in (att.get("cutoffs") or {}).items())
                ref = " (reference)" if att["name"] == entry["reference"] else ""
                lines.append(f"| {py} | {att['name']}{ref} | {att['outcome']} | "
                             f"{counts.get('passed', '-')}/{counts.get('failed', '-')}/{counts.get('error', '-')} | "
                             f"{note} |")
            if entry.get("strict"):
                s = entry["strict"]
                lines.append(f"| {py} | strict (-W error::DeprecationWarning) | {s['outcome']} | "
                             f"{s['counts'].get('passed', 0)}/{s['counts'].get('failed', 0)}/"
                             f"{s['counts'].get('error', 0)} | {(s.get('error_excerpt') or '').replace('|', '/')} |")
    lines += ["", "## Risk indicators (🔍)", "", "| Dependency | Indicator | Detail |", "|---|---|---|"]
    for r in c["risk_indicators"]:
        lines.append(f"| {r['dependency']} | {r['indicator']} | {r['detail'].replace('|', '/')} |")
    lines += ["", "## Usage locations (🔍)", "", "| File:line | API | Kind |", "|---|---|---|"]
    for u in c["usage_locations"]:
        if not u["in_tests"]:
            lines.append(f"| {u['file']}:{u['line']} | `{u['api']}` | {u['kind']} |")
    if c.get("limitations"):
        lines += ["", "## Limitations (🔍)", ""]
        lines += [f"- **{lim.get('item', '')}**: {lim.get('reason', '')}" for lim in c["limitations"]]
    return "\n".join(lines) + "\n"


def _safe_uv_version() -> str | None:
    try:
        return envs.uv_version()
    except Exception:
        return None
