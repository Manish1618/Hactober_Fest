import json

import httpx
import pytest

from datamind.model.adapter import ModelError, OllamaAdapter, ScriptedAdapter
from datamind.model.schemas import SqlDraft
from datamind.settings import ModelSettings


def _adapter(replies):
    """OllamaAdapter whose HTTP calls return the given message contents in order."""
    calls = []
    it = iter(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": next(it)}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaAdapter(ModelSettings(name="gemma3:12b", temperature=0.0), client=client), calls


GOOD = json.dumps({"sql": "SELECT 1", "tables_used": [], "assumptions": ["a"], "confidence": 0.9})


def test_valid_output_is_parsed():
    a, calls = _adapter([GOOD])
    out = a.generate("p", SqlDraft, prompt_version="v1")
    assert out.sql == "SELECT 1" and out.assumptions == ["a"]
    body = calls[0]
    assert body["model"] == "gemma3:12b"
    assert body["options"]["temperature"] == 0.0
    assert body["format"]["properties"]["sql"]  # schema passed for constrained output
    assert body["stream"] is False


def test_code_fenced_output_is_accepted():
    a, _ = _adapter(["```json\n" + GOOD + "\n```"])
    assert a.generate("p", SqlDraft, prompt_version="v1").sql == "SELECT 1"


def test_retries_once_with_the_error_then_succeeds():
    a, calls = _adapter(["not json", GOOD])
    assert a.generate("p", SqlDraft, prompt_version="v1").sql == "SELECT 1"
    assert len(calls) == 2
    assert "did not match" in calls[1]["messages"][-1]["content"]


def test_malformed_twice_raises():
    a, _ = _adapter(["nope", json.dumps({"confidence": 7})])
    with pytest.raises(ModelError, match="malformed"):
        a.generate("p", SqlDraft, prompt_version="v1")


def test_unreachable_endpoint_raises_model_error():
    def boom(request):
        raise httpx.ConnectError("refused")

    a = OllamaAdapter(ModelSettings(), client=httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(ModelError, match="could not be reached"):
        a.generate("p", SqlDraft, prompt_version="v1")


def test_scripted_adapter():
    a = ScriptedAdapter(lambda p, v: {"sql": "SELECT 2"})
    assert a.generate("p", SqlDraft, prompt_version="v1").sql == "SELECT 2"


def _google(handler, **kw):
    from pydantic import SecretStr

    from datamind.model.adapter import GoogleAIAdapter

    settings = ModelSettings(
        provider="google",
        endpoint="https://example.test/v1beta",
        name="gemma-a",
        api_key=SecretStr("k"),
        fallback_models=["gemma-b"],
        **kw,
    )
    a = GoogleAIAdapter(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    a.backoff_seconds = 0
    return a


def _reply(text, thought="thinking about it"):
    return {
        "candidates": [{"content": {"parts": [{"text": thought, "thought": True}, {"text": text}]}}]
    }


def test_google_adapter_drops_thoughts_and_fences():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=_reply('```json\n{"sql": "SELECT 1"}\n```'))

    a = _google(handler)
    assert a.generate("p", SqlDraft, prompt_version="v1").sql == "SELECT 1"
    assert seen[0].headers["x-goog-api-key"] == "k"
    assert "k" not in str(seen[0].url)  # the key never goes in the URL
    assert seen[0].url.path.endswith("/models/gemma-a:generateContent")


def test_google_adapter_retries_busy_then_falls_back():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if "gemma-a" in request.url.path:
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return httpx.Response(200, json=_reply('{"sql": "SELECT 2"}'))

    assert _google(handler).generate("p", SqlDraft, prompt_version="v1").sql == "SELECT 2"
    assert sum("gemma-a" in c for c in calls) == 4 and "gemma-b" in calls[-1]


def test_google_adapter_does_not_retry_a_bad_key():
    def handler(request):
        return httpx.Response(403, json={"error": {"message": "API key not valid"}})

    with pytest.raises(ModelError, match="refused"):
        _google(handler).generate("p", SqlDraft, prompt_version="v1")


def test_google_adapter_needs_a_key():
    from datamind.model.adapter import GoogleAIAdapter

    with pytest.raises(ModelError, match="DATAMIND_MODEL_API_KEY"):
        GoogleAIAdapter(ModelSettings(provider="google"))


def test_api_key_is_hidden_in_settings_output(monkeypatch):
    from datamind.settings import get_settings

    monkeypatch.setenv("DATAMIND_MODEL_PROVIDER", "google")
    monkeypatch.setenv("DATAMIND_MODEL_API_KEY", "secret-value")
    s = get_settings()
    assert s.model.name == "gemma-4-26b-a4b-it"
    assert "secret-value" not in repr(s) and "secret-value" not in str(s.redacted())


def test_google_adapter_turns_thinking_off_by_default():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_reply('{"sql": "SELECT 1"}'))

    _google(handler).generate("p", SqlDraft, prompt_version="v1")
    _google(handler, thinking=True).generate("p", SqlDraft, prompt_version="v1")
    assert "Thinking is disabled" in bodies[0]["systemInstruction"]["parts"][0]["text"]
    assert "systemInstruction" not in bodies[1]


def test_google_adapter_retries_a_hung_call():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("hung", request=request)
        return httpx.Response(200, json=_reply('{"sql": "SELECT 3"}'))

    assert _google(handler).generate("p", SqlDraft, prompt_version="v1").sql == "SELECT 3"
    assert len(calls) == 2
