"""Stage 1: Inspect. Deterministic, no AI. Every output record is tagged `observed`."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from . import TARGET_PYTHONS, import_map, manifests, pypi, usage
from .fetch import FetchError
from .provenance import file_source, observed
from .runctx import Run

FROZEN_YEARS = 3.0
STALE_YEARS = 2.0
DEPRECATION_TEXT = re.compile(r"(deprecated|unmaintained|no longer maintained|abandoned|end[- ]of[- ]life)",
                              re.IGNORECASE)
INACTIVE = "Development Status :: 7 - Inactive"


def inspect(repo: Path, run: Run, *, offline: bool = False) -> dict[str, Any]:
    repo = repo.resolve()
    m = manifests.collect(repo)
    scan = usage.scan(repo)

    declared: dict[str, list[manifests.DeclaredDep]] = defaultdict(list)
    for d in m.deps:
        if d.group != "build":
            declared[d.name].append(d)

    # --- PyPI / GitHub metadata -------------------------------------------------
    meta: dict[str, dict[str, Any] | None] = {}
    fetch_errors: list[dict[str, Any]] = []
    for name in sorted(declared):
        try:
            meta[name] = pypi.project(name, offline=offline)
        except FetchError as exc:
            meta[name] = None
            fetch_errors.append({"dependency": name, "error": str(exc)})

    # --- import name -> distribution ---------------------------------------------
    pairs = [(n, _effective_decl(decls).pinned_version) for n, decls in sorted(declared.items())
             if meta.get(n)]
    mapping, map_details = import_map.metadata_env_map(pairs, run.envs / "metadata-3.12", run.logs)
    imported = scan.third_party_modules()
    module_to_dists: dict[str, dict[str, Any]] = {}
    for mod in sorted(imported):
        dists = import_map.resolve(mod, mapping)
        method = "packages_distributions"
        if not dists:
            # Not provided by any declared distribution: look for a same-named PyPI project.
            try:
                match = pypi.project(mod, offline=offline)
            except FetchError:
                match = None
            dists = [match["name"].lower()] if match else []
            method = "pypi_name_match" if match else "unresolved"
        module_to_dists[mod] = {"distributions": dists, "method": method}

    by_module = scan.by_module()
    dist_usages: dict[str, list[usage.Usage]] = defaultdict(list)
    for mod, info in module_to_dists.items():
        for dist in info["distributions"]:
            dist_usages[_canon(dist)].extend(by_module.get(mod, []))

    command_text = " ".join(c["text"] for c in m.test_commands + m.install_commands).lower()

    dependencies: list[dict[str, Any]] = []
    risk: list[dict[str, Any]] = []
    for name, decls in sorted(declared.items()):
        eff = _effective_decl(decls)
        info = meta.get(name)
        uses = dist_usages.get(name, [])
        src_uses = [u for u in uses if not u.in_tests]
        labels: list[str] = []
        dep = {
            "name": name,
            "declarations": [{"file": d.file, "line": d.line, "spec": d.spec or "(any)", "group": d.group,
                              "effective": d.effective} for d in decls],
            "effective_spec": eff.spec or "(any)",
            "effective_file": eff.file,
            "group": eff.group,
            "pinned_version": eff.pinned_version,
            "import_names": sorted(m_ for m_, i in module_to_dists.items()
                                   if name in {_canon(x) for x in i["distributions"]}),
            "usage_sites": {"source": len(src_uses), "tests": len(uses) - len(src_uses),
                            "files": sorted({u.file for u in uses})},
        }
        src = {"url": info["source"]["url"], "fetched_at": info["source"]["fetched_at"]} if info else None
        if info is None:
            labels.append("not_on_pypi")
            risk.append(_risk(name, "not_on_pypi", "No project with this name on PyPI", src))
        else:
            dep.update({
                "latest": info["latest"],
                "latest_date": info["latest_date"],
                "last_release_date": info["last_release_date"],
                "years_since_last_release": pypi.years_since(info["last_release_date"]),
                "requires_python_latest": info["requires_python"],
                "summary": info["summary"],
            })
            _metadata_risks(name, eff, info, dep, labels, risk, src, offline)
        # usage-based classification
        group_is_dev = eff.group != "runtime"
        if not uses and not (group_is_dev and _mentioned(name, command_text)):
            labels.append("unused")
            risk.append(_risk(name, "unused", "Declared but never imported"
                              + ("" if not group_is_dev else " or referenced by a test/install command"),
                              {"files_scanned": len(scan.files_scanned)}))
        private = sorted({u.api for u in uses if u.private})
        if private:
            risk.append(_risk(name, "private_api_use", f"Uses private API: {', '.join(private)}",
                              [file_source(u.file, u.line) for u in uses if u.private]))
        patches = [u for u in uses if u.kind == "monkeypatch"]
        if patches:
            risk.append(_risk(name, "monkeypatch", "Assigns to attributes of this package at runtime: "
                              + ", ".join(sorted({u.api for u in patches})),
                              [file_source(u.file, u.line) for u in patches]))
        if len({u.file for u in src_uses}) >= 3:
            risk.append(_risk(name, "wide_usage", f"Used in {len({u.file for u in src_uses})} source files",
                              None))
        if "frozen" not in labels and "unused" not in labels and "not_on_pypi" not in labels:
            labels.append("upgradable" if dep.get("upgrade_available") else "current_or_unknown")
        dep["classification"] = labels
        dependencies.append(observed(source=src, **dep))

    # --- undeclared imports ------------------------------------------------------
    declared_names = set(declared)
    for mod, info in sorted(module_to_dists.items()):
        dists = [_canon(d) for d in info["distributions"]]
        if dists and any(d in declared_names for d in dists):
            continue
        sites = by_module.get(mod, [])
        if all(u.in_tests for u in sites) and any(d in ("pytest",) for d in dists):
            continue
        dist = dists[0] if dists else None
        dependencies.append(observed(
            source={"mapping_method": info["method"]},
            name=dist or mod, declarations=[], effective_spec=None, pinned_version=None,
            import_names=[mod], classification=["undeclared"],
            usage_sites={"source": sum(not u.in_tests for u in sites),
                         "tests": sum(u.in_tests for u in sites), "files": sorted({u.file for u in sites})},
        ))
        risk.append(_risk(dist or mod, "undeclared",
                          f"Imported as `{mod}` but not declared in any manifest "
                          f"(distribution found via {info['method']})",
                          [file_source(u.file, u.line) for u in sites]))

    usage_locations = [observed(source=file_source(u.file, u.line), dependency=_dist_for(u.module, module_to_dists),
                                **u.to_dict()) for u in scan.usages]
    drift = [observed(source={"kind": "manifests", "files": m.files_read}, **d) for d in manifests.drift(m)]
    for c in m.commented:
        drift.append(observed(source=file_source(c["file"], c["line"]), kind="commented_out_requirement",
                              dependency=c["name"], raw=c["raw"]))
    for d in drift:
        risk.append(_risk(d.get("dependency") or "(project)", "manifest_drift", _drift_text(d), d["source"]))

    return {
        "manifests": observed(
            source={"kind": "repo", "path": str(repo)},
            files_read=sorted(set(m.files_read)),
            effective_files=m.effective_files,
            lockfiles=m.lockfiles,
            requires_python=m.requires_python,
            install_commands=m.install_commands,
            test_commands=m.test_commands,
            parse_errors=m.errors,
        ),
        "dependencies": dependencies,
        "manifest_drift": drift,
        "import_map": [observed(source=map_details, import_name=k, **v) for k, v in module_to_dists.items()],
        "usage_locations": usage_locations,
        "risk_indicators": risk,
        "fetch_errors": fetch_errors,
        "scan": {"files_scanned": scan.files_scanned, "local_modules": sorted(scan.local_modules),
                 "parse_errors": scan.parse_errors},
    }


def _metadata_risks(name: str, eff: manifests.DeclaredDep, info: dict[str, Any], dep: dict[str, Any],
                    labels: list[str], risk: list[dict[str, Any]], src: Any, offline: bool) -> None:
    years = dep["years_since_last_release"]
    pinned = eff.pinned_version
    latest = info["latest"]
    frozen_reasons = []
    if years is not None and years >= FROZEN_YEARS:
        frozen_reasons.append(f"no release for {years} years (last {info['last_release_date'][:10]})")
    elif years is not None and years >= STALE_YEARS:
        risk.append(_risk(name, "stale", f"Last release {years} years ago", src))
    if INACTIVE in info["classifiers"]:
        frozen_reasons.append("PyPI classifier 'Development Status :: 7 - Inactive'")
    if info["summary"] and DEPRECATION_TEXT.search(info["summary"]):
        frozen_reasons.append(f"PyPI summary says: {info['summary']!r}")
    slug = pypi.github_repo_url(info)
    if slug:
        gh = pypi.github_status(slug, offline=offline)
        if gh and not gh.get("error"):
            dep["github"] = {k: gh[k] for k in ("repo", "archived", "pushed_at")}
            if gh["archived"]:
                frozen_reasons.append(f"GitHub repository {slug} is archived")
    if pinned and latest:
        dep["upgrade_available"] = _newer(latest, pinned)
        if not dep["upgrade_available"]:
            risk.append(_risk(name, "no_newer_version", f"Pinned {pinned} is already the latest release", src))
        if pinned in info["yanked_releases"]:
            risk.append(_risk(name, "pinned_version_yanked", f"{pinned} is yanked on PyPI", src))
        try:
            files, vsrc = pypi.version_files(name, pinned, offline=offline)
            support = {py: pypi.wheel_support(files, py) for py in TARGET_PYTHONS}
            dep["pinned_wheels"] = support
            for py, s in support.items():
                if s != "wheel":
                    risk.append(_risk(name, "no_wheel_for_target",
                                      f"{name}=={pinned} has no wheel for Python {py} ({s})",
                                      {"url": vsrc["url"], "fetched_at": vsrc["fetched_at"]}))
        except FetchError:
            pass
        if dep["upgrade_available"]:
            gap = _major_gap(pinned, latest)
            risk.append(_risk(name, "outdated_pin",
                              f"Pinned {pinned}, latest {latest}" + (f" ({gap} major versions behind)"
                                                                     if gap else ""), src))
    elif not pinned:
        dep["upgrade_available"] = None
        risk.append(_risk(name, "unpinned", f"Spec {eff.spec or '(any)'} - resolved version decided at install time",
                          file_source(eff.file, eff.line)))
    if frozen_reasons:
        labels.append("frozen")
        risk.append(_risk(name, "frozen", "; ".join(frozen_reasons), src))


def _effective_decl(decls: list[manifests.DeclaredDep]) -> manifests.DeclaredDep:
    ranked = sorted(decls, key=lambda d: (not d.effective, d.group != "runtime", d.file))
    return ranked[0]


def _risk(dependency: str, indicator: str, detail: str, source: Any) -> dict[str, Any]:
    return observed(source=source, dependency=dependency, indicator=indicator, detail=detail)


def _newer(a: str, b: str) -> bool:
    try:
        return Version(a) > Version(b)
    except InvalidVersion:
        return a != b


def _major_gap(pinned: str, latest: str) -> int:
    try:
        return max(0, Version(latest).major - Version(pinned).major)
    except InvalidVersion:
        return 0


def _canon(name: str) -> str:
    from packaging.utils import canonicalize_name

    return canonicalize_name(name)


def _mentioned(name: str, text: str) -> bool:
    variants = {name, name.replace("-", "_")}
    return any(re.search(rf"\b{re.escape(v)}", text) for v in variants)


def _dist_for(module: str, module_to_dists: dict[str, dict[str, Any]]) -> str | None:
    dists = module_to_dists.get(module, {}).get("distributions") or []
    return _canon(dists[0]) if dists else None


def _drift_text(d: dict[str, Any]) -> str:
    kind = d["kind"]
    if kind == "spec_mismatch":
        decl = "; ".join(f"{x['file']}: {x['spec']}" for x in d["declarations"])
        return ("Conflicting specs - " if d["conflicting"] else "Different specs - ") + decl
    if kind == "missing_in_manifest":
        return f"Declared in {d['declared_in']} but not in {d['missing_from']}"
    if kind == "requires_python_excludes_targets":
        return f"{d['file']} requires-python {d['spec']} excludes {', '.join(d['excluded_targets'])}"
    if kind == "requires_python_wider_than_targets":
        return f"{d['file']} requires-python {d['spec']} claims support beyond tested targets"
    if kind == "commented_out_requirement":
        return f"Commented-out requirement {d['raw']!r}"
    return kind
