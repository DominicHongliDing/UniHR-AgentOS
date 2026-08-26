import httpx
from copy import deepcopy

from app.models.gateway import BailianModelGateway
from app.settings import Settings


class SequencedResponse:
    def __init__(self, *, failed: bool, content: str = ""):
        self.failed = failed
        self.content = content

    def raise_for_status(self):
        if self.failed:
            request = httpx.Request("POST", "https://example.test/chat/completions")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("model unavailable", request=request, response=response)

    def json(self):
        return {
            "choices": [{"message": {"content": self.content}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 4},
        }


class SequencedClient:
    def __init__(self):
        self.payloads = []
        self.urls = []

    def post(self, _url, **kwargs):
        self.urls.append(_url)
        self.payloads.append(deepcopy(kwargs["json"]))
        return SequencedResponse(
            failed=len(self.payloads) == 1,
            content="降级模型完成任务",
        )


class ResponsesClient:
    def __init__(self):
        self.url = ""
        self.payload = {}

    def post(self, url, **kwargs):
        self.url = url
        self.payload = deepcopy(kwargs["json"])
        return ResponsesResult()


class ResponsesResult:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": "done"}]},
                {"type": "function_call", "call_id": "call-1", "name": "lookup", "arguments": '{"q":"x"}'},
            ],
            "usage": {"input_tokens": 12, "output_tokens": 8},
        }


def test_primary_model_failure_downscales_to_configured_fallback(tmp_path):
    client = SequencedClient()
    gateway = BailianModelGateway(Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "unihr.db",
        model_api_key="test-key",
        primary_model="primary-unavailable",
        primary_fallback_model="fallback-available",
    ), client=client)

    reply = gateway.complete(
        [{"role": "user", "content": "生成表格"}],
        purpose="spreadsheet_plan",
        model="primary-unavailable",
        structured=True,
    )

    assert [item["model"] for item in client.payloads] == [
        "primary-unavailable", "fallback-available",
    ]
    assert reply.model == "fallback-available"
    assert reply.content == "降级模型完成任务"


def test_router_timeout_or_failure_uses_semantic_model_fallback(tmp_path):
    client = SequencedClient()
    gateway = BailianModelGateway(Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "unihr.db",
        model_api_key="test-key",
        router_model="small-router",
        router_fallback_model="stable-router-fallback",
    ), client=client)

    reply = gateway.complete(
        [{"role": "user", "content": "最近有什么值得看的电影？"}],
        purpose="turn_interpretation",
        structured=True,
    )

    assert [item["model"] for item in client.payloads] == [
        "small-router", "stable-router-fallback",
    ]
    assert reply.model == "stable-router-fallback"


def test_qwen38_uses_responses_protocol_and_translates_tools(tmp_path):
    client = ResponsesClient()
    gateway = BailianModelGateway(Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "unihr.db",
        model_api_key="test-key",
        primary_model="qwen3.8-max-preview",
        responses_base_url="https://workspace.example/compatible-mode/v1",
    ), client=client)

    reply = gateway.complete(
        [{"role": "user", "content": "plan"}],
        purpose="document_plan",
        tools=[{"type": "function", "function": {
            "name": "lookup", "description": "look up evidence",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        }}],
    )

    assert client.url.endswith("/responses")
    assert client.payload["model"] == "qwen3.8-max-preview"
    assert client.payload["reasoning"] == {"effort": "high"}
    assert client.payload["tools"][0]["name"] == "lookup"
    assert reply.content == "done"
    assert reply.tool_calls[0]["function"]["arguments"] == {"q": "x"}


def test_unavailable_token_plan_model_is_skipped_after_first_protocol_failure(tmp_path):
    client = SequencedClient()
    gateway = BailianModelGateway(Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "unihr.db",
        model_api_key="test-key",
        primary_model="qwen3.8-max-preview",
        primary_fallback_model="qwen3.7-max",
        primary_last_resort_model="qwen-plus",
    ), client=client)

    first = gateway.complete([{"role": "user", "content": "first"}], purpose="document_plan")
    second = gateway.complete([{"role": "user", "content": "second"}], purpose="document_plan")

    assert first.model == "qwen3.7-max"
    assert second.model == "qwen3.7-max"
    assert [url.endswith("/responses") for url in client.urls] == [True, False, False]
