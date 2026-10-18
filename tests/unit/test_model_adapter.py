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
