"""Model adapter (FR-H2): one interface, Gemma variant and endpoint chosen by configuration.

Gemma only drafts and phrases. It never executes anything and never sees credentials.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from datamind.settings import ModelSettings

T = TypeVar("T", bound=BaseModel)


class ModelError(Exception):
    """The model was unreachable or kept returning output of the wrong shape."""


class ModelAdapter(Protocol):
    name: str

    def generate(self, prompt: str, schema: type[T], *, prompt_version: str) -> T: ...


def _parse(text: str, schema: type[T]) -> T:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    return schema.model_validate(json.loads(text))


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
                messages += [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": f"That reply did not match the required JSON shape: "
                        f"{last_error[:500]}. Reply again with JSON only.",
                    },
                ]
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
    if settings.provider == "scripted":
        if scripted is None:
            raise ModelError("The scripted provider needs a responder.")
        return ScriptedAdapter(scripted)
    raise ModelError(f"Unknown model provider {settings.provider!r}")
