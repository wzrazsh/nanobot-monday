"""Test Codex CN Provider implementation."""

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.providers.codexcn_provider import CodexCNProvider, _build_headers
from nanobot.providers.base import LLMResponse


def test_codex_cn_provider_init():
    """Test CodexCNProvider initialization."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-123"}):
        provider = CodexCNProvider(default_model="gpt-5.4")
        
        assert provider.api_key == "test-key-123"
        assert provider.default_model == "gpt-5.4"


def test_codex_cn_provider_init_no_key():
    """Test CodexCNProvider initialization without API key."""
    with patch.dict(os.environ, {}, clear=True):
        provider = CodexCNProvider(default_model="gpt-5.4")
        
        assert provider.api_key is None
        assert provider.default_model == "gpt-5.4"


def test_build_headers():
    """Test Codex CN header building."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-api-key-456"}):
        provider = CodexCNProvider()
        
        headers = _build_headers(provider.api_key)
        assert headers["Authorization"] == "Bearer test-api-key-456"
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "text/event-stream"


def test_convert_messages():
    """Test message conversion to Codex Responses format."""
    from nanobot.providers.codexcn_provider import _convert_messages
    
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
    ]
    
    system_prompt, input_items = _convert_messages(messages)
    
    assert system_prompt == "You are a helpful assistant."
    assert len(input_items) == 1
    assert input_items[0]["role"] == "user"
    assert input_items[0]["content"][0]["type"] == "input_text"
    assert input_items[0]["content"][0]["text"] == "Hello"


def test_convert_user_message_string():
    """Test user message conversion with string content."""
    from nanobot.providers.codexcn_provider import _convert_user_message
    
    result = _convert_user_message("Hello World")
    
    assert result["type"] == "message"
    assert result["role"] == "user"
    assert result["content"][0]["type"] == "input_text"
    assert result["content"][0]["text"] == "Hello World"


def test_convert_tools():
    """Test tool conversion."""
    from nanobot.providers.codexcn_provider import _convert_tools
    
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string"}
                    }
                }
            }
        }
    ]
    
    result = _convert_tools(tools)
    
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["name"] == "get_weather"
    assert result[0]["description"] == "Get weather information"


def test_get_default_model():
    """Test get_default_model method."""
    provider = CodexCNProvider(default_model="gpt-5.4")
    
    assert provider.get_default_model() == "gpt-5.4"


@pytest.mark.asyncio
async def test_chat_success():
    """Test successful chat request."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-123"}):
        provider = CodexCNProvider(default_model="gpt-5.4")
        
        messages = [{"role": "user", "content": "Hello"}]
        
        with patch("httpx.AsyncClient") as mock_client_class:
            lines = [
                'data: {"type": "response.output_text.delta", "delta": "Hello!"}',
                "",
                'data: {"type": "response.completed", "response": {"status": "completed"}}',
                "",
                "data: [DONE]",
            ]
            
            class AsyncLineIterator:
                def __init__(self, lines):
                    self.lines = list(lines)
                def __aiter__(self):
                    return self
                async def __anext__(self):
                    if not self.lines:
                        raise StopAsyncIteration
                    return self.lines.pop(0)
            
            class MockResponse:
                status_code = 200
                
                def aiter_lines(self):
                    return AsyncLineIterator(lines)
                
                async def aread(self):
                    return b""
            
            mock_response = MockResponse()
            
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)
            
            from unittest.mock import MagicMock
            mock_stream_manager = MagicMock()
            mock_stream_manager.__aenter__ = AsyncMock(return_value=mock_response)
            mock_stream_manager.__aexit__ = AsyncMock(return_value=None)
            
            mock_client = MagicMock()
            mock_client.stream = MagicMock(return_value=mock_stream_manager)
            
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)
            
            result = await provider.chat(messages)
            
            assert isinstance(result, LLMResponse)
            assert result.content == "Hello!"
            assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_chat_api_error():
    """Test chat request API error handling."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-123"}):
        provider = CodexCNProvider(default_model="gpt-5.4")
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = AsyncMock()
            mock_response.status_code = 401
            mock_response.aread = AsyncMock(return_value=b'{"error": "Unauthorized"}')
            
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)
            
            from unittest.mock import MagicMock
            mock_stream_manager = MagicMock()
            mock_stream_manager.__aenter__ = AsyncMock(return_value=mock_response)
            mock_stream_manager.__aexit__ = AsyncMock(return_value=None)
            
            mock_client = MagicMock()
            mock_client.stream = MagicMock(return_value=mock_stream_manager)
            
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)
            
            messages = [{"role": "user", "content": "Hello"}]
            result = await provider.chat(messages)
            
            assert isinstance(result, LLMResponse)
            assert "Error calling Codex CN" in result.content
            assert result.finish_reason == "error"


if __name__ == "__main__":
    print("Running Codex CN provider tests...")
    
    print("✅ test_codex_cn_provider_init")
    test_codex_cn_provider_init()
    
    print("✅ test_codex_cn_provider_init_no_key")
    test_codex_cn_provider_init_no_key()
    
    print("✅ test_build_headers")
    test_build_headers()
    
    print("✅ test_convert_messages")
    test_convert_messages()
    
    print("✅ test_convert_user_message_string")
    test_convert_user_message_string()
    
    print("✅ test_convert_tools")
    test_convert_tools()
    
    print("✅ test_get_default_model")
    test_get_default_model()
    
    print("\n✅ All tests passed!")
