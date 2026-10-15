"""Fetch release notes between two versions (observed, with source URL). Never recalled by a model."""

from __future__ import annotations

import re
from typing import Any

from packaging.version import InvalidVersion, Version

from . import pypi
from .fetch import FetchError, get_json, get_text, github_headers
from .provenance import observed

VERSION_IN_TAG = re.compile(r"(\d+(?:\.\d+)+(?:[a-z]+\d+)?(?:\.post\d+)?)")
CHANGELOG_KEY = re.compile(r"change|release|history|news|what'?s.?new", re.IGNORECASE)


def _v(text: str) -> Version | None:
    m = VERSION_IN_TAG.search(text or "")
    try:
        return Version(m.group(1)) if m else None
    except InvalidVersion:
        return None


def _in_range(v: Version | None, lo: Version | None, hi: Version | None) -> bool:
    return v is not None and (lo is None or v > lo) and (hi is None or v <= hi)


def from_github(slug: str, lo: Version | None, hi: Version | None, offline: bool, limit: int) -> dict | None:
    url = f"https://api.github.com/repos/{slug}/releases?per_page=100"
    try:
        data, src = get_json(url, headers=github_headers(), offline=offline)
    except FetchError:
        return None
    if not isinstance(data, list):
        return None
    picked = [r for r in data if _in_range(_v(r.get("tag_name") or r.get("name") or ""), lo, hi)]
    if not picked:
        return None
    picked.sort(key=lambda r: _v(r.get("tag_name") or r.get("name")))
    text = "\n\n".join(f"## {r.get('tag_name')}\n{(r.get('body') or '').strip()}" for r in reversed(picked))
    return {"url": src["url"], "fetched_at": src["fetched_at"], "excerpt": _clip(text, limit),
            "releases": [r.get("tag_name") for r in picked]}


def from_changelog(url: str, lo: Version | None, hi: Version | None, offline: bool, limit: int) -> dict | None:
    try:
        text, src = get_text(url, offline=offline)
    except FetchError:
        return None
    if not text:
        return None
    # Keep the slice from the newest wanted version heading down to the old version's heading.
    start = _find_version(text, hi) if hi else 0
    end = _find_version(text, lo, after=start) if lo else -1
    if start < 0:
        start = 0
    section = text[start:end if end and end > start else None]
    return {"url": src["url"], "fetched_at": src["fetched_at"], "excerpt": _clip(section, limit)}


def _find_version(text: str, v: Version, after: int = 0) -> int:
    for cand in {str(v), v.base_version}:
        m = re.search(rf"(?m)^[^\n]{{0,40}}\b{re.escape(cand)}\b", text[after:])
        if m:
            return after + m.start()
    return -1


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\n[... truncated ...]"


def notes(name: str, from_version: str | None, to_version: str | None, *, offline: bool = False,
          limit: int = 2500) -> dict[str, Any]:
    """Release notes for (from, to], as an observed record. `excerpt` is None when nothing was found."""
    try:
        meta = pypi.project(name, offline=offline) or {}
    except FetchError:
        meta = {}
    lo = _v(from_version or "")
    hi = _v(to_version or "") or _v(meta.get("latest") or "")
    found = None
    slug = pypi.github_repo_url(meta) if meta else None
    if slug:
        found = from_github(slug, lo, hi, offline, limit)
    if not found:
        for key, url in (meta.get("project_urls") or {}).items():
            if CHANGELOG_KEY.search(key) and url:
                found = from_changelog(url, lo, hi, offline, limit)
                if found:
                    break
    if not found:
        return observed(source={"kind": "release notes", "searched": ["github releases", "changelog url"]},
                        dependency=name, from_version=from_version, to_version=to_version, excerpt=None)
    return observed(source={"url": found["url"], "fetched_at": found["fetched_at"]}, dependency=name,
                    from_version=from_version, to_version=to_version, excerpt=found["excerpt"],
                    releases=found.get("releases"))
