"""Cached HTTP fetches. Every response is stored with its URL and fetch time, so any
`observed` fact built from it is traceable."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from . import __version__
from .provenance import now_iso
from .runctx import cache_root

USER_AGENT = f"break-forecast/{__version__} (+https://agentskills.io)"


class FetchError(RuntimeError):
    pass


def get_json(
    url: str, *, headers: dict[str, str] | None = None, offline: bool = False
) -> tuple[Any, dict[str, Any]]:
    """Return (data, source). ``data`` is None for HTTP 404. ``source`` is {url, fetched_at, cached}."""
    key = hashlib.sha256(url.encode()).hexdigest()[:32]
    path = cache_root() / "http" / f"{key}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        return cached["body"], {"url": url, "fetched_at": cached["fetched_at"], "cached": True}
    if offline:
        raise FetchError(f"offline and not cached: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            body = None
        else:
            raise FetchError(f"{url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise FetchError(f"{url}: {exc}") from exc
    fetched_at = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "fetched_at": fetched_at, "body": body}), "utf-8")
    return body, {"url": url, "fetched_at": fetched_at, "cached": False}


def get_text(url: str, *, offline: bool = False, limit: int = 400_000) -> tuple[str | None, dict[str, Any]]:
    """Fetch a text/HTML page (cached like get_json). HTML is reduced to plain text."""
    key = hashlib.sha256(("text:" + url).encode()).hexdigest()[:32]
    path = cache_root() / "http" / f"{key}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        return cached["body"], {"url": url, "fetched_at": cached["fetched_at"], "cached": True}
    if offline:
        raise FetchError(f"offline and not cached: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read(limit).decode("utf-8", errors="replace")
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raw, ctype = None, ""
        else:
            raise FetchError(f"{url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise FetchError(f"{url}: {exc}") from exc
    body = html_to_text(raw) if raw and ("html" in ctype or raw.lstrip().startswith("<")) else raw
    fetched_at = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "fetched_at": fetched_at, "body": body}), "utf-8")
    return body, {"url": url, "fetched_at": fetched_at, "cached": False}


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
