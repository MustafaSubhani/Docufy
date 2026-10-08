"""The OpenAI Responses adapter, fed the SDK's own event and item types."""

from __future__ import annotations

from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseFunctionWebSearch,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseTextDeltaEvent,
    ResponseUsage,
)
from openai.types.responses.response_function_web_search import ActionSearch
from openai.types.responses.response_output_text import AnnotationURLCitation
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from spark_agent.providers import Events, ResponsesAdapter, ToolCall, _rank, is_reasoning


class FakeResponses:
    def __init__(self, streams, refuse=None):
        self.streams = list(streams)
        self.calls: list[dict] = []
        self.refuse = list(refuse or [])

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.refuse:
            raise RuntimeError(self.refuse.pop(0))
        return iter(self.streams.pop(0))


class FakeClient:
    def __init__(self, responses):
        self.responses = responses


def _search():
    return ResponseFunctionWebSearch(
        id="ws_1", status="completed", type="web_search_call",
        action=ActionSearch(type="search", query="uk fleet telematics", sources=[{"type": "url", "url": "https://a.example/x"}]),
    )


def _call():
    return ResponseFunctionToolCall(type="function_call", call_id="call_1", name="create_draft", arguments='{"title": "T", "markdown": "# T"}', id="fc_1", status="completed")


def _message(text):
    return ResponseOutputMessage(
        id="msg_1", type="message", role="assistant", status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[AnnotationURLCitation(type="url_citation", url="https://b.example/y", title="B", start_index=0, end_index=4)])],
    )


def _done(output, status="completed"):
    usage = ResponseUsage(
        input_tokens=1000, output_tokens=200, total_tokens=1200,
        input_tokens_details=InputTokensDetails.model_construct(cached_tokens=600),
        output_tokens_details=OutputTokensDetails.model_construct(reasoning_tokens=80),
    )
    response = Response.model_construct(id="resp_1", status=status, output=output, usage=usage, output_text="")
    return ResponseCompletedEvent(type="response.completed", response=response, sequence_number=99)


def test_a_turn_streams_text_thinking_searches_and_tool_calls():
    first = [
        ResponseReasoningSummaryTextDeltaEvent(type="response.reasoning_summary_text.delta", delta="Plan the proposal", item_id="rs", output_index=0, summary_index=0, sequence_number=1),
        ResponseOutputItemAddedEvent(type="response.output_item.added", item=_search(), output_index=1, sequence_number=2),
        ResponseOutputItemDoneEvent(type="response.output_item.done", item=_search(), output_index=1, sequence_number=3),
        ResponseTextDeltaEvent(type="response.output_text.delta", delta="Drafting.", item_id="msg", output_index=2, content_index=0, logprobs=[], sequence_number=4),
        _done([_search(), _call()]),
    ]
    second = [
        ResponseTextDeltaEvent(type="response.output_text.delta", delta="Done.", item_id="msg", output_index=0, content_index=0, logprobs=[], sequence_number=1),
        _done([_message("Done.")]),
    ]
    responses = FakeResponses([first, second])
    adapter = ResponsesAdapter("gpt-5", client=FakeClient(responses), cache_key="docufy-chat")
    adapter.start("system", [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}], [{"type": "input_text", "text": "Write it"}])
    seen = {"text": [], "think": [], "search": []}
    events = Events(
        text=seen["text"].append,
        thinking=seen["think"].append,
        search=lambda item, status, query: seen["search"].append((status, query)),
    )
    turn = adapter.step([{"name": "create_draft", "description": "d", "parameters": {"type": "object", "properties": {}}}], events, lambda: None)
    sent = responses.calls[0]
    assert sent["tools"][0] == {"type": "web_search", "search_context_size": "medium"}
    assert sent["tools"][1]["name"] == "create_draft" and sent["reasoning"]["summary"] == "auto"
    assert sent["prompt_cache_key"] == "docufy-chat" and sent["include"] == ["web_search_call.action.sources"]
    assert seen["think"] == ["Plan the proposal"] and seen["text"] == ["Drafting."]
    assert seen["search"] == [("searching", ""), ("done", "uk fleet telematics")]
    assert turn.calls == [ToolCall("call_1", "create_draft", {"title": "T", "markdown": "# T"}, '{"title": "T", "markdown": "# T"}')]
    assert turn.citations[0].url == "https://a.example/x"
    assert turn.usage.cached_tokens == 600 and turn.usage.reasoning_tokens == 80 and turn.usage.searches == 1
    replayed = [item for item in adapter.items if isinstance(item, dict) and item.get("type") == "web_search_call"]
    assert replayed and "sources" not in replayed[0]["action"]

    adapter.add_results([(turn.calls[0], "Created draft")])
    assert adapter.items[-1] == {"type": "function_call_output", "call_id": "call_1", "output": "Created draft"}
    final = adapter.step([], events, lambda: None)
    assert final.text == "Done." and not final.calls
    assert final.citations[0].url == "https://b.example/y"


def test_a_refused_parameter_is_dropped_and_the_call_retried():
    responses = FakeResponses([[_done([_message("Hi")])]], refuse=["Error: reasoning.summary is not supported for your organization"])
    adapter = ResponsesAdapter("gpt-5", client=FakeClient(responses))
    adapter.start("s", [], "hello")
    adapter.step([], Events(), lambda: None)
    assert "summary" in responses.calls[0]["reasoning"] and "summary" not in responses.calls[1]["reasoning"]


def test_models_rank_newest_first_and_reasoning_models_are_known():
    names = ["gpt-4.1", "gpt-5", "gpt-5.6-terra", "gpt-5-mini", "gpt-5-2025-08-07"]
    ranked = sorted(names, key=_rank, reverse=True)
    assert ranked[0] == "gpt-5.6-terra" and ranked[-1] == "gpt-4.1"
    assert is_reasoning("openai/gpt-5-mini") and is_reasoning("o4-mini") and not is_reasoning("gpt-4.1")
