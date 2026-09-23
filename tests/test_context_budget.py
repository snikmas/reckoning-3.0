from __future__ import annotations

import pytest

from reckoning.conversation import (
    DEFAULT_CONTEXT_WINDOW,
    ChannelCapabilities,
    ComposerInput,
    ProviderMessage,
    compose_provider_conversation,
    context_budget,
    select_history_for_budget,
)


def _message(role: str, content: str) -> ProviderMessage:
    return ProviderMessage(role, content)  # type: ignore[arg-type]


def test_known_context_window_uses_configured_allowance() -> None:
    budget = context_budget(2000)

    assert budget.context_window == 2000
    assert budget.request_allowance == 2000 - 500


def test_unknown_context_window_falls_back_to_default() -> None:
    budget = context_budget(None)

    assert budget.context_window is None
    assert budget.request_allowance == 4096 - 1024


def test_negative_context_window_falls_back_to_default() -> None:
    budget = context_budget(-5)

    assert budget.context_window == -5
    assert budget.request_allowance == 4096 - 1024


def test_history_includes_only_recent_complete_turns_when_budget_tight() -> None:
    first_user = "first user message " + "x" * 200
    first_assistant = "first assistant message " + "x" * 200
    second_user = "second user message "
    second_assistant = "second assistant message "
    history = (
        _message("user", first_user),
        _message("assistant", first_assistant),
        _message("user", second_user),
        _message("assistant", second_assistant),
    )
    budget = context_budget(250)
    mandatory = (
        _message("system", "protected contract"),
        _message("system", "identity"),
        _message("system", "persona"),
        _message("user", "current request"),
    )

    selection = select_history_for_budget(budget, history, mandatory)

    assert selection.omitted_turn_count == 1
    assert selection.selected_messages == (
        _message("user", second_user),
        _message("assistant", second_assistant),
    )
    assert selection.estimated_input_tokens > 0
    assert selection.estimator_method == "utf-8-byte-count"


def test_history_restores_chronological_order_for_provider() -> None:
    history = (
        _message("user", "first user"),
        _message("assistant", "first assistant"),
        _message("user", "second user"),
        _message("assistant", "second assistant"),
    )
    budget = context_budget(1000)
    mandatory = (
        _message("system", "protected"),
        _message("system", "identity"),
        _message("system", "persona"),
        _message("user", "current"),
    )

    selection = select_history_for_budget(budget, history, mandatory)

    roles = [message.role for message in selection.selected_messages]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_malformed_or_incomplete_history_is_dropped() -> None:
    history = (
        _message("assistant", "orphan opening"),
        _message("user", "only user"),
        _message("assistant", "paired assistant"),
        _message("assistant", "orphan closing"),
    )
    budget = context_budget(2000)
    mandatory = (
        _message("system", "protected"),
        _message("system", "identity"),
        _message("system", "persona"),
        _message("user", "current"),
    )

    selection = select_history_for_budget(budget, history, mandatory)

    assert selection.selected_messages == (
        _message("user", "only user"),
        _message("assistant", "paired assistant"),
    )


def test_newest_oversized_turn_stops_selection_without_older_turns() -> None:
    history = (
        _message("user", "x"),
        _message("assistant", "short"),
        _message("user", "y" * 5000),
        _message("assistant", "also huge" + "z" * 5000),
    )
    budget = context_budget(1000)
    mandatory = (
        _message("system", "protected"),
        _message("system", "identity"),
        _message("system", "persona"),
        _message("user", "current"),
    )

    selection = select_history_for_budget(budget, history, mandatory)

    # The newest turn cannot fit, so selection stops. It must not skip that
    # turn to reach the small older one, and it must not select half a turn.
    assert selection.selected_messages == ()
    assert selection.omitted_turn_count == 2
    assert selection.selected_turn_count == 0


def test_selection_records_inspectable_budget_evidence() -> None:
    budget = context_budget(2000)
    history = (_message("user", "u"), _message("assistant", "a"))
    mandatory = (_message("system", "s"), _message("user", "current"))

    selection = select_history_for_budget(budget, history, mandatory)

    assert selection.configured_context_window == 2000
    assert selection.effective_context_window == 2000
    assert selection.response_reserve == 500
    assert selection.required_input_tokens == sum(
        len(message.content.encode("utf-8")) + 8 for message in mandatory
    )
    assert selection.selected_turn_count == 1
    assert selection.estimated_input_tokens > 0
    assert selection.estimator_method == "utf-8-byte-count"


def test_required_material_exceeding_allowance_raises() -> None:
    budget = context_budget(50)
    mandatory = (
        _message("system", "a" * 100),
        _message("user", "current"),
    )

    with pytest.raises(RuntimeError, match="exceeds the model context budget"):
        select_history_for_budget(budget, (), mandatory)


def test_current_request_is_never_truncated() -> None:
    current = "current" + "x" * 3000
    history = (
        _message("user", "old"),
        _message("assistant", "old reply"),
    )
    budget = context_budget(1000)
    mandatory = (
        _message("system", "protected"),
        _message("system", "identity"),
        _message("system", "persona"),
        _message("user", current),
    )

    with pytest.raises(RuntimeError, match="exceeds the model context budget"):
        select_history_for_budget(budget, history, mandatory)


def test_composer_includes_only_selected_history() -> None:
    first_user = "first user message " + "x" * 400
    first_assistant = "first assistant message " + "x" * 400
    second_user = "second user message "
    second_assistant = "second assistant message "
    history = (
        _message("user", first_user),
        _message("assistant", first_assistant),
        _message("user", second_user),
        _message("assistant", second_assistant),
    )
    conversation, selection = compose_provider_conversation(
        ComposerInput(
            protected_contract="protected",
            product_identity="identity",
            persona_expression="persona",
            current_request="current",
            history=history,
            context_window=1200,
        )
    )

    assert selection.omitted_turn_count == 1
    assert conversation.messages[-3] == _message("user", second_user)
    assert conversation.messages[-2] == _message("assistant", second_assistant)
    assert conversation.messages[-1] == _message("user", "current")


def test_composer_omitted_history_notice_is_not_in_provider_messages() -> None:
    first_user = "first user message " + "x" * 400
    first_assistant = "first assistant message " + "x" * 400
    second_user = "second user message "
    second_assistant = "second assistant message "
    history = (
        _message("user", first_user),
        _message("assistant", first_assistant),
        _message("user", second_user),
        _message("assistant", second_assistant),
    )
    conversation, _selection = compose_provider_conversation(
        ComposerInput(
            protected_contract="protected",
            product_identity="identity",
            persona_expression="persona",
            current_request="current",
            history=history,
            context_window=1200,
        )
    )

    text = "\n".join(message.content for message in conversation.messages)
    assert "omitted" not in text.casefold()
    assert "Limited context" not in text


def test_composer_instructs_plain_text_for_non_html_channels() -> None:
    conversation, _selection = compose_provider_conversation(
        ComposerInput(
            protected_contract="protected",
            product_identity="identity",
            persona_expression="persona",
            current_request="current",
            channel_capabilities=ChannelCapabilities(accepts_html=False),
        )
    )

    assert any(
        "Do not return HTML" in message.content for message in conversation.messages
    )


def test_composer_does_not_add_plain_text_instruction_for_html_channel() -> None:
    conversation, _selection = compose_provider_conversation(
        ComposerInput(
            protected_contract="protected",
            product_identity="identity",
            persona_expression="persona",
            current_request="current",
            channel_capabilities=ChannelCapabilities(accepts_html=True),
        )
    )

    assert not any(
        "Do not return HTML" in message.content for message in conversation.messages
    )


def test_unknown_limit_uses_conservative_fallback_for_exact_outbound_messages() -> None:
    history = (
        _message("user", "old question"),
        _message("assistant", "old answer"),
    )
    conversation, selection = compose_provider_conversation(
        ComposerInput(
            protected_contract="protected",
            product_identity="identity",
            persona_expression="persona",
            current_request="current request",
            history=history,
            context_window=None,
        )
    )

    assert selection.configured_context_window is None
    assert selection.effective_context_window == DEFAULT_CONTEXT_WINDOW
    assert conversation.messages == (
        _message("system", "protected"),
        _message("system", "identity"),
        _message("system", "persona"),
        _message("user", "old question"),
        _message("assistant", "old answer"),
        _message("user", "current request"),
    )


def test_multilingual_history_is_bounded_as_a_contiguous_suffix() -> None:
    history = (
        _message("user", "Первый вопрос " + "я" * 300),
        _message("assistant", "Первый ответ " + "о" * 300),
        _message("user", "第二个问题 " + "问" * 300),
        _message("assistant", "第二个回答 " + "答" * 300),
        _message("user", "Final question"),
        _message("assistant", "Final answer"),
    )
    budget = context_budget(700)
    mandatory = (
        _message("system", "protected"),
        _message("system", "identity"),
        _message("system", "persona"),
        _message("user", "current"),
    )

    selection = select_history_for_budget(budget, history, mandatory)

    assert selection.selected_messages == (
        _message("user", "Final question"),
        _message("assistant", "Final answer"),
    )
    assert selection.omitted_turn_count == 2
    assert selection.selected_turn_count == 1
    assert selection.estimated_input_tokens <= budget.request_allowance


def test_composer_required_instruction_ordering() -> None:
    conversation, _selection = compose_provider_conversation(
        ComposerInput(
            protected_contract="protected",
            product_identity="identity",
            persona_expression="persona",
            current_request="current",
            confirmed_records=("record",),
            permissions=("perm",),
            retrieved_context=("ctx",),
            available_connectors=("tool",),
            channel_capabilities=ChannelCapabilities(accepts_html=False),
        )
    )

    roles = [message.role for message in conversation.messages]
    contents = [message.content for message in conversation.messages]
    assert roles[:8] == [
        "system",
        "system",
        "system",
        "system",
        "system",
        "user",
        "system",
        "user",
    ]
    assert contents[0] == "protected"
    assert contents[1] == "identity"
    assert contents[2] == "persona"
    assert "Do not return HTML" in contents[3]
    assert "untrusted data" in contents[4].casefold()
    assert "[CONFIRMED CONTEXT]" in contents[5]
    assert contents[6] == "perm"
    assert "[RETRIEVED CONTEXT]" in contents[7]
    assert "tool" in contents[8]
    assert contents[-1] == "current"
