"""Direct typed-image conformance (unified spec §§3.3–3.5), without inference.

These tests do not implement experimental media refs or tool-post acceptance.
"""
import base64
import json
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest, ImageBlock, Message, TextBlock
from unified_llm.errors import ConfigurationError
from unified_llm.types import (
    ContentKind, ContentPart, FinishReason, Message as ULMMessage,
    Response, Role, StreamEvent, StreamEventType, Usage,
)

from amplifier_module_loop_agent.unified_provider_adapter import UnifiedProviderAdapter

# Known static PNG, not a screenshot or proof of model perception.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6aWQAAAAASUVORK5CYII=")


def image(mime="image/png"):
    return ImageBlock(source={"type": "base64", "media_type": mime,
                              "data": base64.b64encode(PNG).decode()})


class RecordingClient:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return Response(id="fake", model="fake", provider="fake",
                        message=ULMMessage(role=Role.ASSISTANT, content=[ContentPart(kind=ContentKind.TEXT, text="done")]),
                        finish_reason=FinishReason(reason="stop"), usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))

    async def stream(self, request):
        self.requests.append(request)
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, delta="done")


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("mime", ["image/png", "image/jpeg", "image/webp", "image/gif"])
async def test_direct_image_order_bytes_mime_through_public_paths(streaming, mime):
    client = RecordingClient()
    adapter = UnifiedProviderAdapter(provider_name="anthropic", model="fake", client=client)
    request = ChatRequest(messages=[Message(role="user", content=[
        TextBlock(text="before"), image(mime), TextBlock(text="after"), image(mime)])])
    if streaming:
        assert [chunk async for chunk in adapter.stream(request)] == [{"content": "done"}]
    else:
        assert (await adapter.complete(request)).content[0].text == "done"
    parts = client.requests[0].messages[0].content
    assert [part.kind for part in parts] == [ContentKind.TEXT, ContentKind.IMAGE, ContentKind.TEXT, ContentKind.IMAGE]
    assert [parts[0].text, parts[2].text] == ["before", "after"]
    assert parts[1].image.data == parts[3].image.data == PNG
    assert parts[1].image.media_type == mime
    assert request.messages[0].content[1].source == image(mime).source


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_actual_vendor_request_serializer_receives_inline_pixels(provider):
    from unified_llm.adapters.anthropic import AnthropicAdapter
    from unified_llm.adapters.openai import OpenAIAdapter
    from unified_llm.adapters.gemini import GeminiAdapter

    # No SDK construction, HTTP or credentials; execute real pure wire builders.
    bridge = UnifiedProviderAdapter(provider_name=provider, model="fake", client=object())
    request = bridge._translate_request(ChatRequest(messages=[Message(role="user", content=[
        TextBlock(text="before"), image(), TextBlock(text="after")])]))
    cls = {"anthropic": AnthropicAdapter, "openai": OpenAIAdapter, "gemini": GeminiAdapter}[provider]
    wire = cls.__new__(cls)._translate_request(request)
    serialized = json.dumps(wire)
    assert base64.b64encode(PNG).decode() in serialized
    assert "image/png" in serialized
    assert serialized.index("before") < serialized.index(base64.b64encode(PNG).decode()) < serialized.index("after")


@pytest.mark.parametrize("source", [
    {"type": "base64", "media_type": "image/png", "data": "private-invalid@@"},
    {"type": "base64", "media_type": "text/plain", "data": "cHJpdmF0ZQ=="},
    {"type": "base64", "media_type": "image/", "data": "cHJpdmF0ZQ=="},
    {"type": "base64", "media_type": "image/not-real", "data": "cHJpdmF0ZQ=="},
    {"type": "base64", "media_type": "image/heic", "data": "cHJpdmF0ZQ=="},
    {"type": "base64", "media_type": "image/png", "data": ""},
    {"type": "url", "url": "file:///private/path"},
    {"type": "url", "url": "/private/path"},
    {"type": "url", "url": "https://example.test/private", "data": "private"},
    {"type": "experimental.media.v1", "sha256": "private"},
])
@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_invalid_sources_fail_before_transport_without_payload_in_error(source, streaming):
    client = RecordingClient()
    adapter = UnifiedProviderAdapter(provider_name="openai", model="fake", client=client)
    request = ChatRequest(messages=[Message(role="user", content=[ImageBlock(source=source)])])
    with pytest.raises(ConfigurationError) as error:
        if streaming:
            [part async for part in adapter.stream(request)]
        else:
            await adapter.complete(request)
    assert "private" not in str(error.value)
    assert client.requests == []


def test_url_and_detail_preserved_without_io():
    adapter = UnifiedProviderAdapter(provider_name="openai", model="fake", client=object())
    source = {"type": "url", "url": "https://example.test/sentinel.png", "detail": "high"}
    translated = adapter._translate_content([ImageBlock(source=source)])[0].image
    assert translated.url == source["url"] and translated.detail == "high"
    assert translated.data is None


@pytest.mark.parametrize("role", ["tool", "system", "assistant", "developer"])
def test_unsupported_image_positions_refuse_not_stringify(role):
    adapter = UnifiedProviderAdapter(provider_name="openai", model="fake", client=object())
    with pytest.raises(ConfigurationError, match="no pixels sent"):
        adapter._translate_request(ChatRequest(messages=[Message(
            role=role, tool_call_id="call" if role == "tool" else None, content=[image()])]))


def test_unknown_typed_content_refuses_without_repr():
    adapter = UnifiedProviderAdapter(provider_name="openai", model="fake", client=object())
    with pytest.raises(ConfigurationError) as error:
        adapter._translate_content([SimpleNamespace(type="unknown", data="private-payload")])
    assert "private-payload" not in str(error.value)


def test_tool_text_blocks_are_text_not_python_repr():
    adapter = UnifiedProviderAdapter(provider_name="openai", model="fake", client=object())
    msg = adapter._translate_message(Message(role="tool", tool_call_id="call", content=[
        TextBlock(text="one"), TextBlock(text="two")]))
    assert msg.content[0].tool_result.content == "onetwo"


def test_reference_shaped_text_remains_text():
    adapter = UnifiedProviderAdapter(provider_name="openai", model="fake", client=object())
    text = '{"experimental.media.v1":{"refs":[{"sha256":"not-authority"}]}}'
    msg = adapter._translate_message(Message(role="user", content=text))
    assert msg.content[0].text == text and msg.content[0].image is None
