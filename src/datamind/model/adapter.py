"""Model adapter (FR-H2): one interface, Gemma variant and endpoint chosen by configuration.

Gemma only drafts and phrases. It never executes anything and never sees credentials.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from datamind.settings import ModelSettings

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger("datamind")


class ModelError(Exception):
    """The model was unreachable or kept returning output of the wrong shape."""


class ModelAdapter(Protocol):
    name: str

    def generate(self, prompt: str, schema: type[T], *, prompt_version: str) -> T: ...


def _parse(text: str, schema: type[T]) -> T:
    text = text.strip()
    if "```" in text:  # fenced reply: keep what is inside the first fence
        inner = text.split("```")[1]
        text = inner[inner.find("{") :] if "{" in inner else inner
    if not text.startswith("{") and "{" in text:
        text = text[text.find("{") : text.rfind("}") + 1]
    return schema.model_validate(json.loads(text))


def _correction(content: str, error: str) -> list[dict]:
    return [
        {"role": "assistant", "content": content},
        {
            "role": "user",
            "content": f"That reply did not match the required JSON shape: {error[:500]}. "
            "Reply again with JSON only.",
        },
    ]


class OllamaAdapter:
    """Talks to an Ollama server's /api/chat with constrained JSON output."""

    def __init__(self, settings: ModelSettings, client: httpx.Client | None = None):
        self.settings = settings
        self.name = settings.name
        self._client = client or httpx.Client(timeout=settings.timeout_seconds)

    def _chat(self, messages: list[dict], schema: type[BaseModel]) -> str:
        payload = {
            "model": self.settings.name,
            "messages": messages,
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {
                "temperature": self.settings.temperature,
                "num_predict": self.settings.max_tokens,
            },
        }
        url = self.settings.endpoint.rstrip("/") + "/api/chat"
        try:
            resp = self._client.post(url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ModelError(f"The model endpoint could not be reached: {e}") from e
        return resp.json().get("message", {}).get("content", "")

    def generate(self, prompt: str, schema: type[T], *, prompt_version: str) -> T:
        messages = [{"role": "user", "content": prompt}]
        last_error = ""
        for _attempt in range(2):  # one retry with the validation error (EC-11)
            content = self._chat(messages, schema)
            try:
                return _parse(content, schema)
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = str(e)
                messages += _correction(content, last_error)
        raise ModelError(f"The model returned malformed output twice: {last_error[:200]}")

    def ping(self) -> str:
        """Short round trip used by the smoke test."""
        url = self.settings.endpoint.rstrip("/") + "/api/tags"
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ModelError(f"The model endpoint could not be reached: {e}") from e
        names = [m.get("name") for m in resp.json().get("models", [])]
        if self.settings.name not in names:
            raise ModelError(
                f"Model {self.settings.name!r} is not pulled on {self.settings.endpoint}. "
                f"Available: {', '.join(n for n in names if n) or 'none'}"
            )
        return f"{self.settings.name} available at {self.settings.endpoint}"


NO_THINKING = "Answer immediately. Do not think or reason before answering; output only the JSON."


class GoogleAIAdapter:
    """Hosted Gemma through the Gemini API (generativelanguage.googleapis.com).

    Prompts and the result rows shown to the model leave the machine (NFR-15). Gemma on this
    API has no JSON mode, so the reply is parsed and retried once like the Ollama path.
    Busy (429/503) replies are retried with backoff, then the fallback models are tried.
    """

    RETRY_STATUS = {429, 500, 503}

    def __init__(self, settings: ModelSettings, client: httpx.Client | None = None):
        if not settings.api_key.get_secret_value():
            raise ModelError("DATAMIND_MODEL_API_KEY is not set.")
        self.settings = settings
        self.name = settings.name
        self._models = [settings.name, *settings.fallback_models]
        self._client = client or httpx.Client(timeout=settings.timeout_seconds)
        self.backoff_seconds = 2.0

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.settings.api_key.get_secret_value()}

    def _call(self, model: str, messages: list[dict]) -> httpx.Response:
        payload = {
            "contents": [
                {
                    "role": "model" if m["role"] == "assistant" else "user",
                    "parts": [{"text": m["content"]}],
                }
                for m in messages
            ],
            "generationConfig": {
                "temperature": self.settings.temperature,
                "maxOutputTokens": self.settings.max_tokens,
            },
        }
        if not self.settings.thinking:
            # Gemma on this API thinks before every reply unless told not to; skipping it cut
            # a SQL draft from about 60 s to about 5 s with the same SQL in testing.
            payload["systemInstruction"] = {"parts": [{"text": NO_THINKING}]}
        url = f"{self.settings.endpoint.rstrip('/')}/models/{model}:generateContent"
        try:
            return self._client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as e:
            raise ModelError(f"The model endpoint could not be reached: {type(e).__name__}") from e

    def _chat(self, messages: list[dict]) -> str:
        last = ""
        for model in self._models:
            for attempt in range(3):
                resp = self._call(model, messages)
                if resp.status_code == 200:
                    if model != self.name:
                        log.info("used fallback model %s", model)
                    return _text(resp.json())
                last = f"{resp.status_code} {_error_message(resp)}"
                if resp.status_code not in self.RETRY_STATUS:
                    raise ModelError(f"The model endpoint refused the request: {last}")
                time.sleep(self.backoff_seconds * (2**attempt))
        raise ModelError(f"The hosted model is busy: {last}")

    def generate(self, prompt: str, schema: type[T], *, prompt_version: str) -> T:
        messages = [{"role": "user", "content": prompt}]
        last_error = ""
        for _attempt in range(2):
            content = self._chat(messages)
            try:
                return _parse(content, schema)
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = str(e)
                messages += _correction(content, last_error)
        raise ModelError(f"The model returned malformed output twice: {last_error[:200]}")

    def ping(self) -> str:
        url = f"{self.settings.endpoint.rstrip('/')}/models?pageSize=200"
        try:
            resp = self._client.get(url, headers=self._headers())
        except httpx.HTTPError as e:
            raise ModelError(f"The model endpoint could not be reached: {type(e).__name__}") from e
        if resp.status_code != 200:
            raise ModelError(f"The model endpoint refused the key: {resp.status_code}")
        names = [m.get("name", "").removeprefix("models/") for m in resp.json().get("models", [])]
        if self.settings.name not in names:
            gemma = ", ".join(n for n in names if "gemma" in n) or "none"
            raise ModelError(
                f"Model {self.settings.name!r} is not available. Gemma models: {gemma}"
            )
        return f"{self.settings.name} available on the hosted Gemini API"


def _text(body: dict) -> str:
    """Answer text without the model's thinking parts."""
    candidates = body.get("candidates") or [{}]
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _error_message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("error", {}).get("message", ""))[:200]
    except ValueError:
        return resp.text[:200]


class ScriptedAdapter:
    """Offline stand-in for Gemma: returns canned replies. For tests and the phase 0 demo only.

    `responder` receives the rendered prompt and the prompt version, and returns the reply as a
    dict. The version tells it which task is being asked (understand, draft SQL, answer).
    """

    def __init__(self, responder: Callable[[str, str], dict], name: str = "scripted-stub"):
        self.name = name
        self._responder = responder

    def generate(self, prompt: str, schema: type[T], *, prompt_version: str) -> T:
        try:
            return schema.model_validate(self._responder(prompt, prompt_version))
        except KeyError as e:
            raise ModelError(f"No scripted reply: {e.args[0] if e.args else ''}") from e
        except ValidationError as e:
            raise ModelError(f"Scripted reply has the wrong shape: {e}") from e


def build_adapter(settings: ModelSettings, scripted: Callable[[str, str], dict] | None = None):
    if settings.provider == "ollama":
        return OllamaAdapter(settings)
    if settings.provider == "google":
        return GoogleAIAdapter(settings)
    if settings.provider == "scripted":
        if scripted is None:
            raise ModelError("The scripted provider needs a responder.")
        return ScriptedAdapter(scripted)
    raise ModelError(f"Unknown model provider {settings.provider!r}")
