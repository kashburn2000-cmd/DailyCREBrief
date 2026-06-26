"""Thin Gemini REST client (free tier, no SDK, no billing).

Uses the public Generative Language REST API directly so the only dependency is
``requests``. Implements exponential backoff (1s, 2s, 4s, 8s) on the transient errors the free
tier returns — HTTP 429 (rate limit), 503 (model overloaded) and 500 (internal)
— and honors a server ``Retry-After`` header when present.

Endpoint shape (verified against ai.google.dev/gemini-api/docs):

    POST https://generativelanguage.googleapis.com/{API_VERSION}/models/{model}:generateContent
    header: x-goog-api-key: <GEMINI_API_KEY>
    body:   {"contents":[{"parts":[{"text": "..."}]}], "generationConfig": {...}}

The model name is injected by the caller (from GEMINI_MODEL), never hardcoded
into the URL, so swapping models needs no code change.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("cre_brief.gemini")

API_BASE = "https://generativelanguage.googleapis.com"
API_VERSION = "v1beta"
_TIMEOUT = 90
_BACKOFF_SCHEDULE = (1, 2, 4, 8)  # seconds; one final attempt follows the last sleep
_MAX_BACKOFF = 30                 # cap when honoring a server Retry-After header
RETRY_STATUS = {429, 500, 503}


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parse a Retry-After header value expressed in seconds (ignore HTTP-date form)."""
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class GeminiError(RuntimeError):
    """Raised when Gemini cannot produce a usable response after retries."""


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        session: Optional[requests.Session] = None,
        sleeper=time.sleep,
    ):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        self.api_key = api_key
        self.model = model
        self.session = session or requests.Session()
        self._sleep = sleeper  # injectable for tests

    # ------------------------------------------------------------------
    def _url(self) -> str:
        return f"{API_BASE}/{API_VERSION}/models/{self.model}:generateContent"

    def _build_body(
        self,
        prompt: str,
        system: Optional[str],
        temperature: float,
        response_schema: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        # Gemini 3.x Flash models "think" by default, consuming output tokens
        # before the visible answer; budget generously so structured JSON is not
        # truncated to a MAX_TOKENS finish with empty parts.
        generation_config: Dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": 8192,
        }
        if response_schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = response_schema

        body: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return body

    # ------------------------------------------------------------------
    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.3,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Return the model's text output, retrying transient errors with backoff."""
        body = self._build_body(prompt, system, temperature, response_schema)
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        last_error: Optional[str] = None
        attempts = len(_BACKOFF_SCHEDULE) + 1
        for attempt in range(attempts):
            server_retry_after: Optional[float] = None
            try:
                resp = self.session.post(
                    self._url(), headers=headers, json=body, timeout=_TIMEOUT
                )
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                log.warning("Gemini request failed (attempt %d/%d): %s", attempt + 1, attempts, exc)
            else:
                if resp.status_code == 200:
                    # A 200 with a non-JSON body (e.g. a proxy/error HTML page or
                    # a truncated response) raises ValueError, NOT a
                    # RequestException — convert it to GeminiError so callers
                    # degrade gracefully instead of crashing the whole run.
                    try:
                        payload = resp.json()
                    except ValueError as exc:
                        raise GeminiError(
                            f"Gemini returned 200 with a non-JSON body: {exc}; "
                            f"body={resp.text[:300]}"
                        )
                    return self._extract_text(payload)
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code not in RETRY_STATUS:
                    # Non-transient (e.g. 400 bad request, 403 bad key) — fail fast.
                    raise GeminiError(f"Gemini call failed, not retryable. {last_error}")
                server_retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                log.warning(
                    "Gemini transient error (attempt %d/%d): %s",
                    attempt + 1, attempts, last_error,
                )

            if attempt < len(_BACKOFF_SCHEDULE):
                # Honor a server-provided Retry-After if it asks us to wait longer.
                delay = max(_BACKOFF_SCHEDULE[attempt], server_retry_after or 0)
                delay = min(delay, _MAX_BACKOFF)
                log.info("Backing off %ss before Gemini retry", delay)
                self._sleep(delay)

        raise GeminiError(f"Gemini call failed after {attempts} attempts. Last error: {last_error}")

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_text(payload: Dict[str, Any]) -> str:
        """Pull text from candidates[0].content.parts[*].text, with guards."""
        candidates: List[Dict[str, Any]] = payload.get("candidates") or []
        if not candidates:
            feedback = payload.get("promptFeedback", {})
            raise GeminiError(f"Gemini returned no candidates (promptFeedback={feedback})")

        candidate = candidates[0]
        finish = candidate.get("finishReason")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts).strip()
        if not text:
            raise GeminiError(f"Gemini returned an empty response (finishReason={finish})")
        return text

    # ------------------------------------------------------------------
    def generate_json(
        self,
        prompt: str,
        response_schema: Dict[str, Any],
        system: Optional[str] = None,
        temperature: float = 0.3,
    ) -> Any:
        """Like :meth:`generate` but parse the JSON response, with a salvage fallback."""
        raw = self.generate(
            prompt, system=system, temperature=temperature, response_schema=response_schema
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            salvaged = _salvage_json(raw)
            if salvaged is not None:
                log.warning("Gemini JSON needed salvaging (stray text around the object)")
                return salvaged
            raise GeminiError(f"Gemini did not return valid JSON: {raw[:300]}")


def _salvage_json(raw: str) -> Optional[Any]:
    """Best-effort recovery if the model wraps JSON in prose or code fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
