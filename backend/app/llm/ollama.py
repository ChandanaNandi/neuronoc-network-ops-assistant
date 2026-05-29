"""Thin client for the Ollama HTTP API.

Phase 6 only needs one operation: prompt -> structured JSON response.
The client is intentionally narrow - all failure modes (unreachable,
non-2xx, empty body, non-JSON content) collapse into a single
`OllamaUnavailableError` so callers can implement a clean fallback.
"""

from __future__ import annotations

import json

import httpx

from app.core.config import settings


class OllamaUnavailableError(Exception):
    """Raised when the Ollama daemon cannot satisfy a generate request.

    Covers: network errors, timeouts, non-2xx status, missing/empty
    response field, and malformed JSON in the response.
    """


def generate_ollama_json(
    prompt: str,
    model: str | None = None,
    timeout_seconds: int = 60,
) -> dict:
    """Call Ollama `/api/generate` with `format=json` and parse the response.

    Returns the parsed JSON dict. Raises `OllamaUnavailableError` on any
    failure - the caller is expected to fall back gracefully.
    """
    model = model or settings.OLLAMA_MODEL
    base = settings.OLLAMA_BASE_URL.rstrip("/")
    url = f"{base}/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
    }

    try:
        response = httpx.post(url, json=payload, timeout=timeout_seconds)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OllamaUnavailableError(
            f"Ollama request to {url} failed: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise OllamaUnavailableError(
            f"Ollama returned a non-JSON envelope: {exc}"
        ) from exc

    raw = body.get("response")
    if not raw or not isinstance(raw, str):
        raise OllamaUnavailableError(
            "Ollama response envelope is missing a non-empty 'response' string"
        )

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaUnavailableError(
            f"Ollama 'response' field is not valid JSON: {exc}"
        ) from exc
