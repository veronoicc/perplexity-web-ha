"""Smoke test for perplexity_web integration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_EMAIL, CONF_NAME

from custom_components.perplexity_web.api import (
    AskResult,
    AuthClient,
    PerplexityAuthError,
    PerplexityClient,
    SearchConfigItem,
)
from custom_components.perplexity_web.const import (
    CONF_MODEL_PREFERENCE,
    CONF_PROMPT,
    CONF_REASONING,
    CONF_SEARCH_FOCUS,
    CONF_SESSION_TOKEN,
    DOMAIN,
)


def test_search_config_item() -> None:
    """Test model selection logic in SearchConfigItem."""
    item = SearchConfigItem(
        label="Sonar",
        description="Sonar search model",
        subscription_tier="free",
        non_reasoning_model="sonar",
        reasoning_model="sonar-reasoning",
    )
    assert item.best_model(use_reasoning=True) == "sonar-reasoning"
    assert item.best_model(use_reasoning=False) == "sonar"

    # Fallback when one variant is None
    item_only_reasoning = SearchConfigItem(
        label="R1",
        description="R1 model",
        reasoning_model="deepseek-r1",
    )
    assert item_only_reasoning.best_model(use_reasoning=False) == "deepseek-r1"
    assert item_only_reasoning.best_model(use_reasoning=True) == "deepseek-r1"
    print("test_search_config_item: PASSED")


async def test_auth_client() -> None:
    """Test AuthClient methods."""
    session = MagicMock()
    session.closed = False
    # 1. CSRF Token
    resp_csrf = MagicMock()
    resp_csrf.status = 200
    resp_csrf.json = AsyncMock(return_value={"csrfToken": "test-csrf-123"})
    resp_csrf.__aenter__ = AsyncMock(return_value=resp_csrf)
    resp_csrf.__aexit__ = AsyncMock(return_value=None)
    session.get = MagicMock(return_value=resp_csrf)

    auth_client = AuthClient(session)
    csrf = await auth_client.get_csrf_token()
    assert csrf == "test-csrf-123"

    # 2. Send OTP
    resp_send = MagicMock()
    resp_send.status = 200
    resp_send.__aenter__ = AsyncMock(return_value=resp_send)
    resp_send.__aexit__ = AsyncMock(return_value=None)
    session.post = MagicMock(return_value=resp_send)

    await auth_client.send_email_otp("test@example.com", csrf)

    # 3. Verify OTP via cookies
    resp_otp = MagicMock()
    resp_otp.status = 200
    resp_cookie = MagicMock()
    resp_cookie.value = "session-secret-token"
    resp_otp.cookies = {"__Secure-next-auth.session-token": resp_cookie}
    resp_otp.__aenter__ = AsyncMock(return_value=resp_otp)
    resp_otp.__aexit__ = AsyncMock(return_value=None)
    session.cookie_jar = []
    session.post = MagicMock(return_value=resp_otp)

    token = await auth_client.verify_otp("test@example.com", "123456", csrf)
    assert token == "session-secret-token"

    # 4. Verify OTP failure
    resp_err = MagicMock()
    resp_err.status = 401
    resp_err.text = AsyncMock(return_value="Unauthorized")
    resp_err.__aenter__ = AsyncMock(return_value=resp_err)
    resp_err.__aexit__ = AsyncMock(return_value=None)
    session.post = MagicMock(return_value=resp_err)

    try:
        await auth_client.verify_otp("test@example.com", "999999", csrf)
        assert False, "Should have raised PerplexityAuthError"
    except PerplexityAuthError:
        pass

    print("test_auth_client: PASSED")


async def test_perplexity_client() -> None:
    """Test PerplexityClient methods."""
    session = MagicMock()

    # 1. Profile
    resp_profile = MagicMock()
    resp_profile.status = 200
    resp_profile.json = AsyncMock(
        return_value={
            "user": {
                "id": "u1",
                "email": "test@example.com",
                "subscription_tier": "pro",
            }
        }
    )
    resp_profile.__aenter__ = AsyncMock(return_value=resp_profile)
    resp_profile.__aexit__ = AsyncMock(return_value=None)

    # 2. Models config
    resp_models = MagicMock()
    resp_models.status = 200
    resp_models.json = AsyncMock(
        return_value={
            "search_config": [
                {
                    "label": "Sonar Free",
                    "description": "Basic model",
                    "subscription_tier": "free",
                    "non_reasoning_model": "sonar-free",
                },
                {
                    "label": "Sonar Pro",
                    "description": "Pro model",
                    "subscription_tier": "pro",
                    "non_reasoning_model": "sonar-pro",
                },
                {
                    "label": "Max Model",
                    "description": "Max only",
                    "subscription_tier": "max",
                    "non_reasoning_model": "sonar-max",
                },
            ]
        }
    )
    resp_models.__aenter__ = AsyncMock(return_value=resp_models)
    resp_models.__aexit__ = AsyncMock(return_value=None)

    # Alternate get responses: first profile, then models
    session.get = MagicMock(side_effect=[resp_profile, resp_models])

    client = PerplexityClient(session, "test-token")
    models = await client.get_available_models()
    # "pro" user should see "free" and "pro", but not "max"
    labels = [m.label for m in models]
    assert "Sonar Free" in labels
    assert "Sonar Pro" in labels
    assert "Max Model" not in labels

    # 3. Ask complete (SSE parsing)
    sse_lines = [
        b'data: {"blocks": [{"markdown_block": '
        b'{"chunks": ["Hello, "], "chunk_starting_offset": 0}}]}\n',
        b'data: {"blocks": [{"markdown_block": '
        b'{"chunks": ["world!"], "chunk_starting_offset": 1}}]}\n',
        b'data: {"blocks": [{"web_result_block": '
        b'{"web_results": [{"name": "Wiki", "url": "https://wiki.org"}]}}]}\n',
        b"data: [DONE]\n",
    ]

    async def sse_iter():
        for line in sse_lines:
            yield line

    resp_ask = MagicMock()
    resp_ask.status = 200
    resp_ask.content = sse_iter()
    resp_ask.__aenter__ = AsyncMock(return_value=resp_ask)
    resp_ask.__aexit__ = AsyncMock(return_value=None)

    session.post = MagicMock(return_value=resp_ask)

    res: AskResult = await client.ask_complete("Hi")
    assert res.text == "Hello, world!"
    assert len(res.sources) == 1
    assert res.sources[0]["name"] == "Wiki"

    print("test_perplexity_client: PASSED")


async def test_conversation_entity() -> None:
    """Test PerplexityWebConversationEntity."""
    from homeassistant.components.conversation import ConversationInput
    from homeassistant.core import Context

    from custom_components.perplexity_web.conversation import (
        PerplexityWebConversationEntity,
    )

    entry = MagicMock()
    entry.entry_id = "test_entry_1"
    entry.title = "Perplexity (test@example.com)"
    entry.options = {
        CONF_PROMPT: "You are a concise assistant. Home: {{ ha_name }}.",
        CONF_MODEL_PREFERENCE: "sonar",
        CONF_SEARCH_FOCUS: "internet",
        CONF_REASONING: False,
    }

    hass = MagicMock()
    hass.data = {}
    hass.config.location_name = "SweetHome"
    hass.auth.async_get_user = AsyncMock(return_value=None)

    client = MagicMock(spec=PerplexityClient)
    client.ask_complete = AsyncMock(
        return_value=AskResult(text="Paris is the capital.", sources=[])
    )

    entity = PerplexityWebConversationEntity(entry, client)
    entity.hass = hass

    # Test single-turn
    user_input = ConversationInput(
        text="What is the capital of France?",
        context=Context(),
        conversation_id=None,
        device_id=None,
        language="en",
        agent_id="test_agent",
    )

    result = await entity.async_process(user_input)
    assert result.response.speech["plain"]["speech"] == "Paris is the capital."
    assert result.conversation_id is not None
    cid = result.conversation_id

    # Verify history recorded
    assert cid in entity.history
    assert len(entity.history[cid]) == 2
    assert entity.history[cid][0]["role"] == "user"
    assert entity.history[cid][1]["role"] == "assistant"

    # Test multi-turn follow-up
    client.ask_complete = AsyncMock(
        return_value=AskResult(text="About 2.1 million people.", sources=[])
    )

    follow_up = ConversationInput(
        text="What is its population?",
        context=Context(),
        conversation_id=cid,
        device_id=None,
        language="en",
        agent_id="test_agent",
    )

    result2 = await entity.async_process(follow_up)
    assert result2.response.speech["plain"]["speech"] == "About 2.1 million people."
    assert result2.conversation_id == cid

    # Verify client received formatted multi-turn query
    call_query = client.ask_complete.call_args[0][0]
    assert "System: You are a concise assistant. Home: SweetHome." in call_query
    assert "User: What is the capital of France?" in call_query
    assert "Assistant: Paris is the capital." in call_query
    assert "User: What is its population?" in call_query

    print("test_conversation_entity: PASSED")


async def test_config_flow() -> None:
    """Test PerplexityConfigFlow."""
    from homeassistant.const import CONF_EMAIL

    from custom_components.perplexity_web.config_flow import PerplexityConfigFlow

    hass = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    hass.config_entries.async_entry_for_domain_unique_id = MagicMock(return_value=None)
    flow = PerplexityConfigFlow()
    flow.hass = hass
    flow.context = {}
    mock_session = MagicMock()
    mock_session.closed = False
    with patch(
        "custom_components.perplexity_web.config_flow.async_get_clientsession",
        return_value=mock_session,
    ):
        # 1. Step user - show form
        res1 = await flow.async_step_user(user_input=None)
        assert res1["type"] == "form"
        assert res1["step_id"] == "user"

        # 2. Step user - submit email
        with patch(
            "custom_components.perplexity_web.config_flow.AuthClient"
        ) as mock_auth_cls:
            mock_auth = MagicMock()
            mock_auth.get_csrf_token = AsyncMock(return_value="csrf-token-abc")
            mock_auth.send_email_otp = AsyncMock()
            mock_auth_cls.return_value = mock_auth

            res2 = await flow.async_step_user(
                user_input={CONF_EMAIL: "user@example.com"}
            )
            assert res2["type"] == "form"
            assert res2["step_id"] == "otp"
            assert flow._email == "user@example.com"
            assert flow._csrf_token == "csrf-token-abc"

        # 3. Step OTP - submit code
        with patch(
            "custom_components.perplexity_web.config_flow.AuthClient"
        ) as mock_auth_cls:
            mock_auth = MagicMock()
            mock_auth.verify_otp = AsyncMock(return_value="final-session-token-xyz")
            mock_auth_cls.return_value = mock_auth

            flow.async_set_unique_id = AsyncMock()
            flow._abort_if_unique_id_configured = MagicMock()

            res3 = await flow.async_step_otp(user_input={"otp": "654321"})
            assert res3["type"] == "create_entry"
            assert res3["title"] == "Perplexity (user@example.com)"
            assert res3["data"][CONF_SESSION_TOKEN] == "final-session-token-xyz"
    # 4. Duplicate account aborts
    flow2 = PerplexityConfigFlow()
    flow2.hass = hass
    flow2.context = {}
    hass.config_entries.async_entry_for_domain_unique_id = MagicMock(
        return_value=MagicMock()
    )
    try:
        await flow2.async_step_user(user_input={CONF_EMAIL: "user@example.com"})
        assert False, "Should abort on duplicate email"
    except Exception as e:
        assert "already_configured" in str(e)
    print("test_config_flow: PASSED")


async def test_init_setup_and_unload() -> None:
    """Test __init__.py async_setup_entry and async_unload_entry."""
    from custom_components.perplexity_web import async_setup_entry, async_unload_entry

    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    entry = MagicMock()
    entry.entry_id = "test_entry_init"
    entry.data = {CONF_SESSION_TOKEN: "tok123"}
    entry.async_on_unload = MagicMock()

    with patch(
        "custom_components.perplexity_web.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value = MagicMock()

        # Setup
        ok = await async_setup_entry(hass, entry)
        assert ok is True
        assert DOMAIN in hass.data
        assert entry.entry_id in hass.data[DOMAIN]
        assert isinstance(hass.data[DOMAIN][entry.entry_id], PerplexityClient)
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()

        # Unload
        unload_ok = await async_unload_entry(hass, entry)
        assert unload_ok is True
        assert entry.entry_id not in hass.data[DOMAIN]
        hass.config_entries.async_unload_platforms.assert_awaited_once()

    print("test_init_setup_and_unload: PASSED")


async def test_subentry_flow_and_conversation() -> None:
    """Test PerplexitySubentryFlowHandler and subentry conversation entity."""
    from custom_components.perplexity_web.config_flow import (
        PerplexitySubentryFlowHandler,
    )
    from custom_components.perplexity_web.conversation import (
        PerplexityWebConversationEntity,
    )

    hass = MagicMock()
    entry1 = MagicMock()
    entry1.entry_id = "entry_1"
    entry1.data = {CONF_EMAIL: "primary@example.com", CONF_SESSION_TOKEN: "tok_1"}

    entry2 = MagicMock()
    entry2.entry_id = "entry_2"
    entry2.data = {CONF_EMAIL: "secondary@example.com", CONF_SESSION_TOKEN: "tok_2"}

    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_entries = MagicMock(return_value=[entry1, entry2])

    entry = entry1

    handler = PerplexitySubentryFlowHandler()
    handler.hass = hass
    handler.source = "user"
    handler._get_entry = MagicMock(return_value=entry)
    handler.async_create_entry = MagicMock(
        side_effect=lambda title, data: {
            "type": "create_entry",
            "title": title,
            "data": data,
        }
    )

    mock_session = MagicMock()
    mock_session.closed = False

    with (
        patch(
            "custom_components.perplexity_web.config_flow.async_get_clientsession",
            return_value=mock_session,
        ),
        patch(
            "custom_components.perplexity_web.config_flow.PerplexityClient"
        ) as mock_client_cls,
    ):
        mock_client = MagicMock()
        mock_client.get_available_models = AsyncMock(return_value=[])
        mock_client_cls.return_value = mock_client

        res = await handler.async_step_set_options(
            user_input={
                "account": "entry_2",
                CONF_NAME: "Research Specialist",
                CONF_PROMPT: "Be rigorous and cite sources.",
                CONF_MODEL_PREFERENCE: "sonar",
                CONF_SEARCH_FOCUS: "scholar",
                CONF_REASONING: True,
            }
        )
        assert handler.handler == "entry_2"
        assert res["type"] == "create_entry"
        assert res["title"] == "Research Specialist"
        assert res["data"][CONF_PROMPT] == "Be rigorous and cite sources."
        assert res["data"][CONF_SEARCH_FOCUS] == "scholar"

    subentry = MagicMock(
        subentry_id="sub_789",
        subentry_type="conversation",
        title="Research Specialist",
        data=res["data"],
    )
    client = MagicMock(spec=PerplexityClient)
    entity = PerplexityWebConversationEntity(entry, client, subentry=subentry)
    assert entity._attr_unique_id == "entry_1_sub_789"
    assert entity._attr_name == "Research Specialist"
    assert entity._options[CONF_PROMPT] == "Be rigorous and cite sources."
    assert entity._options[CONF_SEARCH_FOCUS] == "scholar"
    print("test_subentry_flow_and_conversation: PASSED")


async def main() -> None:
    """Run all smoke tests."""
    test_search_config_item()
    await test_auth_client()
    await test_perplexity_client()
    await test_conversation_entity()
    await test_config_flow()
    await test_init_setup_and_unload()
    await test_subentry_flow_and_conversation()
    print("ALL TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    asyncio.run(main())
