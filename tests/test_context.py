"""Conversation continuity across long histories and explicit omissions."""

import pytest

from rockygpt_brain.context import build_context
from rockygpt_brain.contract import ChatRequest


def conversation(*texts: str, omitted: int = 0) -> ChatRequest:
    return ChatRequest.model_validate({"messages": [
        {"role": "user" if i % 2 == 0 else "assistant", "content": text}
        for i, text in enumerate(texts)
    ], "omittedMessages": omitted})


def test_history_beyond_eight_messages_is_preserved_when_it_fits() -> None:
    texts = ["Financial Aid", *[f"conversation {i}" for i in range(11)], "their phone?"]
    context = build_context(conversation(*texts))
    assert [m.content for m in context.recent_messages] == texts[:-1]
    assert context.latest_message == texts[-1]
    assert context.omitted_messages == 0


def test_client_and_server_omissions_remain_distinct() -> None:
    context = build_context(conversation("old", "answer", "middle", "newer", "latest", omitted=12),
                            history_bytes=6)
    assert [m.content for m in context.recent_messages] == ["newer"]
    assert context.client_omitted_messages == 12
    assert context.server_omitted_messages == 3
    assert context.omitted_messages == 15


def test_utf8_byte_bound_keeps_whole_messages_and_latest_intact() -> None:
    context = build_context(conversation("你好", "hello", "latest"), history_bytes=6)
    assert [m.content for m in context.recent_messages] == ["hello"]
    assert context.server_omitted_messages == 1
    latest = "a" * 16_000
    context = build_context(conversation("old", "reply", latest), history_bytes=0)
    assert context.latest_message == latest
    assert context.recent_messages == ()
    assert context.server_omitted_messages == 2


def test_no_invented_topic_or_entities_and_no_cross_request_memory() -> None:
    previous = build_context(conversation("Registrar", "Financial Aid says hello", "thanks"))
    alone = build_context(conversation("their number?"))
    assert previous.recent_messages[1].role == "assistant"
    assert alone.recent_messages == ()
    assert alone.omitted_messages == 0


def test_negative_context_budget_is_invalid() -> None:
    with pytest.raises(ValueError):
        build_context(conversation("hi"), history_bytes=-1)
