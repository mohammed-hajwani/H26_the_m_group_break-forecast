"""Open-weight model client: Gemma 4 on the Gemini API first, local Ollama as fallback.

Every call is logged (prompt, response, hashes, which model answered) to the run
directory. API keys are sent in headers only and never written to logs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

from .provenance import now_iso

DEFAULT_MODEL = "gemma-4-31b-it"
DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_SECONDARY_MODEL = "gemma-4-26b-a4b-it"
DEFAULT_FALLBACK_MODEL = "gemma4:12b-it-qat"
DEFAULT_FALLBACK_ENDPOINT = "http://localhost:11434/v1"
LOCAL_TIMEOUT = float(os.environ.get("BF_LOCAL_TIMEOUT", 300))
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
RETRYABLE = {408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


def load_dotenv(path: Path | None = None) -> None:
    """Read KEY=VALUE lines from .env without overriding variables that are already set."""
    path = path or Path.cwd() / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Backend:
    name: str  # "primary" | "secondary" | "fallback"
    model: str
    endpoint: str
    api_key: str | None

    @property
    def kind(self) -> str:
        if "generativelanguage.googleapis.com" in self.endpoint:
            return "gemini"
        if ":11434" in self.endpoint or os.environ.get("BF_FALLBACK_API") == "ollama":
            return "ollama"  # native API: lets us set the context window, which /v1 cannot
        return "openai"

    def describe(self) -> dict[str, str]:
        return {"role": self.name, "model": self.model, "endpoint": self.endpoint, "api": self.kind}


def all_backends() -> list[Backend]:
    """Every configured backend, whether or not it is usable (for health checks).

    Order: primary (hosted 31B), secondary (hosted 26B MoE on the same API, which often has
    capacity when the 31B is overloaded), then the local fallback. Set BF_SECONDARY_MODEL=""
    to disable the secondary.
    """
    load_dotenv()
    key = os.environ.get("BF_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    endpoint = os.environ.get("BF_MODEL_ENDPOINT", DEFAULT_ENDPOINT)
    out = [Backend("primary", os.environ.get("BF_MODEL", DEFAULT_MODEL), endpoint, key)]
    secondary = os.environ.get("BF_SECONDARY_MODEL", DEFAULT_SECONDARY_MODEL)
    if secondary and secondary.lower() not in ("none", "off") and secondary != out[0].model:
        out.append(Backend("secondary", secondary, endpoint, key))
    out.append(Backend("fallback", os.environ.get("BF_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL),
                       os.environ.get("BF_FALLBACK_ENDPOINT", DEFAULT_FALLBACK_ENDPOINT),
                       os.environ.get("BF_FALLBACK_API_KEY")))
    return out


def backends(offline: bool = False) -> list[Backend]:
    """Backends to try in order. Hosted ones are skipped when offline or no API key is set."""
    usable = []
    for b in all_backends():
        hosted = b.name != "fallback"
        if hosted and (offline or (b.kind == "gemini" and not b.api_key)):
            continue
        usable.append(b)
    return usable


def _post(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _call_gemini(b: Backend, prompt: str, json_mode: bool, timeout: float) -> str:
    url = f"{b.endpoint.rstrip('/')}/models/{b.model}:generateContent"
    config: dict[str, Any] = {"temperature": 0}
    if json_mode:
        config["responseMimeType"] = "application/json"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}
    headers = {"x-goog-api-key": b.api_key or ""}
    try:
        data = _post(url, body, headers, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 400 and json_mode:  # model may not support JSON mode; ask in the prompt instead
            body["generationConfig"].pop("responseMimeType")
            data = _post(url, body, headers, timeout)
        else:
            raise
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise LLMError(f"empty response: {json.dumps(data)[:300]}")
    return text


def _call_openai(b: Backend, prompt: str, json_mode: bool, timeout: float) -> str:
    url = f"{b.endpoint.rstrip('/')}/chat/completions"
    body: dict[str, Any] = {"model": b.model, "temperature": 0,
                            "messages": [{"role": "user", "content": prompt}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {b.api_key}"} if b.api_key else {}
    try:
        data = _post(url, body, headers, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 400 and json_mode:
            body.pop("response_format")
            data = _post(url, body, headers, timeout)
        else:
            raise
    text = data["choices"][0]["message"]["content"]
    if not text:
        raise LLMError("empty response")
    return text


def _call_ollama(b: Backend, prompt: str, json_mode: bool, timeout: float) -> str:
    """Ollama's native chat API. Its default 4k-token context silently truncates long prompts (the
    OpenAI-compatible endpoint cannot change it), so the window is set explicitly; thinking is off."""
    base = b.endpoint.rstrip("/").removesuffix("/v1")
    body: dict[str, Any] = {
        "model": b.model, "stream": False, "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0, "num_ctx": int(os.environ.get("BF_LOCAL_NUM_CTX", 16384)),
                    # a runaway answer is cut off (then rejected as invalid JSON) instead of running for minutes
                    "num_predict": int(os.environ.get("BF_LOCAL_MAX_TOKENS", 2048))},
    }
    if json_mode:
        body["format"] = "json"
    try:
        data = _post(f"{base}/api/chat", body, {}, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 400 and "think" in body:  # older servers / models without a thinking switch
            body.pop("think")
            data = _post(f"{base}/api/chat", body, {}, timeout)
        else:
            raise
    text = (data.get("message") or {}).get("content") or ""
    if not text:
        raise LLMError(f"empty response (done_reason={data.get('done_reason')})")
    return text


def _call(b: Backend, prompt: str, json_mode: bool, timeout: float) -> str:
    fn = {"gemini": _call_gemini, "ollama": _call_ollama}.get(b.kind, _call_openai)
    return fn(b, prompt, json_mode, timeout)


def _error_body(exc: urllib.error.HTTPError) -> str:
    try:
        data = json.loads(exc.read().decode("utf-8"))
        return str((data.get("error") or {}).get("message") or data)
    except Exception:
        return ""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating ```json fences and leading prose."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = min((i for i in (candidate.find("{"), candidate.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        end = max(candidate.rfind("}"), candidate.rfind("]"))
        return json.loads(candidate[start:end + 1])


def render_prompt(name: str, **variables: Any) -> str:
    template = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in variables.items():
        rendered = value if isinstance(value, str) else json.dumps(value, indent=2, default=str)
        template = template.replace("{{" + key + "}}", rendered)
    return template


class LLM:
    def __init__(self, log_dir: Path | None = None, *, offline: bool = False,
                 timeout: float = float(os.environ.get("BF_MODEL_TIMEOUT", 90)),
                 retries: int = int(os.environ.get("BF_MODEL_RETRIES", 3)), use_cache: bool = True) -> None:
        self.backends = backends(offline)
        self.log_dir = log_dir
        self.timeout = timeout
        self.retries = retries
        self.use_cache = use_cache
        self.calls = 0

    def _cache_path(self, b: Backend, prompt: str, json_mode: bool) -> Path:
        from .runctx import cache_root

        key = sha(f"{b.model}\n{b.endpoint}\n{json_mode}\n{prompt}")[:40]
        return cache_root() / "llm" / f"{key}.json"

    def complete(self, prompt: str, *, json_mode: bool = False, task: str = "call") -> tuple[str, dict[str, Any]]:
        """Try each backend in order. A cached answer from an earlier identical call (same model,
        prompt and mode; temperature 0) is reused unless the client was created with use_cache=False."""
        if self.use_cache:
            # Any Gemma model's earlier answer to this exact prompt counts, even if that model is not in
            # today's chain (e.g. the 31B was taken out while overloaded). meta records which model answered.
            known = list(self.backends)
            for model in (DEFAULT_MODEL, DEFAULT_SECONDARY_MODEL):
                if all(b.model != model for b in known):
                    known.append(Backend("cache-only", model, DEFAULT_ENDPOINT, None))
            for b in known:
                path = self._cache_path(b, prompt, json_mode)
                if path.exists():
                    cached = json.loads(path.read_text(encoding="utf-8"))
                    meta = {**cached["meta"], "cached": True, "cache_file": str(path)}
                    self._log(task, prompt, cached["response"], meta)
                    return cached["response"], meta
        errors: list[str] = []
        for b in self.backends:
            for attempt in range(1, self.retries + 1):
                started = time.monotonic()
                # A local model is slow but does not overload: give it time to finish long answers.
                timeout = self.timeout if b.name != "fallback" else max(self.timeout, LOCAL_TIMEOUT)
                try:
                    text = _call(b, prompt, json_mode, timeout)
                except urllib.error.HTTPError as exc:
                    body = _error_body(exc)
                    errors.append(f"{b.name}/{b.model}: HTTP {exc.code} {body[:200]}".strip())
                    self._log_failure(task, prompt, b, errors[-1])
                    if exc.code in RETRYABLE and attempt < self.retries:
                        time.sleep(min(5 * 2 ** (attempt - 1), 60))  # 5s, 10s, 20s ...
                        continue
                    break
                except (urllib.error.URLError, TimeoutError, OSError, LLMError, KeyError) as exc:
                    errors.append(f"{b.name}/{b.model}: {type(exc).__name__}: {exc}")
                    self._log_failure(task, prompt, b, errors[-1])
                    # Timeouts are transient like 5xx; a refused connection (no local server) is not.
                    transient = isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
                    if transient and attempt < self.retries:
                        time.sleep(min(5 * 2 ** (attempt - 1), 60))
                        continue
                    break
                meta = {"model_used": b.model, "backend": b.describe(), "fallback_used": b.name != "primary",
                        "attempt": attempt, "prompt_sha256": sha(prompt), "response_sha256": sha(text),
                        "duration_s": round(time.monotonic() - started, 2), "at": now_iso(),
                        "earlier_errors": errors, "cached": False}
                self._log(task, prompt, text, meta)
                path = self._cache_path(b, prompt, json_mode)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"meta": meta, "response": text}), encoding="utf-8")
                return text, meta
        raise LLMError("all model backends failed: " + "; ".join(errors))

    def complete_json(self, prompt: str, schema: dict[str, Any] | None = None, *,
                      task: str = "call") -> tuple[Any, dict[str, Any]]:
        """Ask for JSON, validate it against ``schema`` and retry once with the error if invalid."""
        text, meta = self.complete(prompt, json_mode=True, task=task)
        for retry in (False, True):
            try:
                data = extract_json(text)
                if schema:
                    jsonschema.validate(data, schema)
                meta["schema_retry"] = retry
                return data, meta
            except (json.JSONDecodeError, jsonschema.ValidationError, ValueError) as exc:
                if retry:
                    raise LLMError(f"invalid JSON from model after retry: {exc}") from exc
                fix = (f"{prompt}\n\nYour previous reply was invalid ({str(exc)[:300]}). "
                       "Reply again with ONLY valid JSON matching the requested format.")
                text, meta = self.complete(fix, json_mode=True, task=f"{task}-retry")
        raise LLMError("unreachable")

    def _log_failure(self, task: str, prompt: str, b: Backend, error: str) -> None:
        if not self.log_dir:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.log_dir / "failures.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": now_iso(), "task": task, "backend": b.describe(), "error": error,
                                 "prompt_sha256": sha(prompt)}) + "\n")

    def _log(self, task: str, prompt: str, response: str, meta: dict[str, Any]) -> None:
        self.calls += 1
        if not self.log_dir:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # ':' in a Windows file name silently creates an NTFS alternate data stream, so sanitise.
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", task).strip("-")
        path = self.log_dir / f"{self.calls:03d}-{safe}.json"
        path.write_text(json.dumps({"meta": meta, "prompt": prompt, "response": response}, indent=2), "utf-8")
