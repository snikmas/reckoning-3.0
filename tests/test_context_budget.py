from __future__ import annotations

import pytest

from reckoning.conversation import (
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


def test_individual_oversized_turn_is_omitted_not_truncated() -> None:
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

    assert selection.selected_messages == (
        _message("user", "x"),
        _message("assistant", "short"),
    )
    assert selection.omitted_turn_count == 1


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
    assert roles[:7] == [
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
    assert "[CONFIRMED CONTEXT]" in contents[4]
    assert contents[5] == "perm"
    assert "[RETRIEVED CONTEXT]" in contents[6]
    assert "tool" in contents[7]
    assert contents[-1] == "current"
