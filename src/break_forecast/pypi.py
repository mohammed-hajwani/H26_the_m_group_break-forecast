"""PyPI and GitHub metadata. Facts only: nothing here comes from a model."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from packaging.tags import Tag
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from .fetch import FetchError, get_json, github_headers

GITHUB_RE = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/|$)", re.IGNORECASE)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def release_dates(data: dict[str, Any]) -> dict[str, datetime]:
    """Earliest upload time per release (releases with no files are skipped)."""
    out: dict[str, datetime] = {}
    for ver, files in (data.get("releases") or {}).items():
        times = [_parse_time(f.get("upload_time_iso_8601")) for f in files]
        times = [t for t in times if t]
        if times:
            out[ver] = min(times)
    return out


def _is_final(ver: str) -> bool:
    try:
        v = Version(ver)
    except InvalidVersion:
        return False
    return not (v.is_prerelease or v.is_devrelease)


def wheel_support(files: list[dict[str, Any]], py: str) -> str:
    """'wheel' if a wheel installs on CPython ``py`` (any OS), 'sdist-only', or 'none'."""
    major, minor = (int(x) for x in py.split("."))
    has_sdist = False
    for f in files:
        if f.get("yanked"):
            continue
        name = f.get("filename", "")
        if f.get("packagetype") == "sdist":
            has_sdist = True
            continue
        try:
            _, _, _, tags = parse_wheel_filename(name)
        except InvalidWheelFilename:
            continue
        if any(_tag_ok(t, major, minor) for t in tags):
            return "wheel"
    return "sdist-only" if has_sdist else "none"


def _tag_ok(tag: Tag, major: int, minor: int) -> bool:
    interp, abi = tag.interpreter, tag.abi
    if interp in (f"py{major}", f"py{major}{minor}", "py2.py3"):
        return True
    m = re.fullmatch(r"cp(\d)(\d+)", interp)
    if not m or int(m.group(1)) != major:
        return False
    tag_minor = int(m.group(2))
    if abi == "abi3":
        return tag_minor <= minor
    return tag_minor == minor


def project(name: str, *, offline: bool = False) -> dict[str, Any] | None:
    """Summarise a PyPI project. Returns None if it does not exist on PyPI."""
    url = f"https://pypi.org/pypi/{canonicalize_name(name)}/json"
    data, source = get_json(url, offline=offline)
    if data is None:
        return None
    info = data.get("info") or {}
    dates = release_dates(data)
    finals = sorted((v for v in dates if _is_final(v)), key=Version)
    latest = info.get("version")
    latest_date = dates.get(latest) if latest else None
    last_release = max(dates.values()) if dates else None
    yanked = sorted(
        v for v, files in (data.get("releases") or {}).items() if files and all(f.get("yanked") for f in files)
    )
    project_urls = info.get("project_urls") or {}
    home = info.get("home_page") or ""
    return {
        "name": info.get("name") or name,
        "latest": latest,
        "latest_date": latest_date.isoformat() if latest_date else None,
        "last_release_date": last_release.isoformat() if last_release else None,
        "release_count": len(finals),
        "final_releases": finals,
        "release_dates": {v: d.isoformat() for v, d in dates.items()},
        "yanked_releases": yanked,
        "requires_python": info.get("requires_python"),
        "classifiers": info.get("classifiers") or [],
        "summary": info.get("summary"),
        "project_urls": project_urls,
        "home_page": home,
        "source": source,
    }


def version_files(name: str, version: str, *, offline: bool = False) -> tuple[list[dict[str, Any]], dict]:
    url = f"https://pypi.org/pypi/{canonicalize_name(name)}/{version}/json"
    data, source = get_json(url, offline=offline)
    return ((data or {}).get("urls") or []), source


def github_repo_url(meta: dict[str, Any]) -> str | None:
    candidates = list((meta.get("project_urls") or {}).values()) + [meta.get("home_page") or ""]
    for url in candidates:
        m = GITHUB_RE.search(url or "")
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    return None


def github_status(slug: str, *, offline: bool = False) -> dict[str, Any] | None:
    url = f"https://api.github.com/repos/{slug}"
    try:
        data, source = get_json(url, headers=github_headers(), offline=offline)
    except FetchError as exc:
        return {"error": str(exc), "source": {"url": url}}
    if data is None:
        return None
    return {
        "repo": slug,
        "archived": bool(data.get("archived")),
        "pushed_at": data.get("pushed_at"),
        "open_issues": data.get("open_issues_count"),
        "source": source,
    }


def years_since(iso: str | None, now: datetime | None = None) -> float | None:
    if not iso:
        return None
    now = now or datetime.now(UTC)
    return round((now - datetime.fromisoformat(iso)).days / 365.25, 2)
