"""Tests for LLM (Ollama) component."""

from unittest.mock import MagicMock, patch

from src.pipeline.llm import OllamaLLM, chat
from src.pipeline.utils import Result


class TestOllamaLLM:
    """Test OllamaLLM chat completion client."""

    @patch("src.pipeline.llm.ollama.Client")
    def test_init_uses_config_defaults(self, mock_client_class):
        """Test init uses config defaults when no args provided."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        with patch("src.pipeline.llm.config") as mock_config:
            mock_config.llm.host = "http://localhost:11434"
            mock_config.llm.host_fallback = "http://backup:11434"
            mock_config.llm.model = "llama3:8b"
            mock_config.llm.model_fallback = "qwen3:8b"
            mock_config.llm.system_prompt = "Test prompt"

            llm = OllamaLLM()

            assert llm.host == "http://localhost:11434"
            assert llm.model == "llama3:8b"
            assert llm.fallback_host == "http://backup:11434"
            assert llm.fallback_model == "qwen3:8b"
            assert llm._system_prompt == "Test prompt"
            mock_client_class.assert_any_call(host="http://localhost:11434")
            mock_client_class.assert_any_call(host="http://backup:11434")

    @patch("src.pipeline.llm.ollama.Client")
    def test_init_accepts_overrides(self, mock_client_class):
        """Test init accepts host/model overrides."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        llm = OllamaLLM(
            host="http://remote:11434",
            model="custom-model",
            fallback_host="http://backup:11434",
            fallback_model="fallback-model",
        )

        assert llm.host == "http://remote:11434"
        assert llm.model == "custom-model"
        assert llm.fallback_host == "http://backup:11434"
        assert llm.fallback_model == "fallback-model"

    @patch("src.pipeline.llm.ollama.Client")
    def test_check_connection_success(self, mock_client_class):
        """Test check_connection returns ok when model exists."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "llama3:8b"}]}
        mock_client_class.return_value = mock_client

        llm = OllamaLLM(model="llama3:8b")
        result = llm.check_connection()

        assert result.success is True

    @patch("src.pipeline.llm.ollama.Client")
    def test_check_connection_model_missing(self, mock_client_class):
        """Test check_connection fails when model not found."""
        mock_client = MagicMock()
        mock_client.list.return_value = {"models": [{"name": "other-model"}]}
        mock_client_class.return_value = mock_client

        llm = OllamaLLM(model="llama3:8b")
        result = llm.check_connection()

        assert result.success is False
        assert "not found" in result.error

    @patch("src.pipeline.llm.ollama.Client")
    def test_check_connection_exception(self, mock_client_class):
        """Test check_connection fails on connection error."""
        mock_client = MagicMock()
        mock_client.list.side_effect = Exception("Connection refused")
        mock_client_class.return_value = mock_client

        llm = OllamaLLM()
        result = llm.check_connection()

        assert result.success is False
        assert "Connection refused" in result.error

    @patch("src.pipeline.llm.ollama.Client")
    def test_chat_success(self, mock_client_class):
        """Test chat returns response text on success."""
        mock_client = MagicMock()
        mock_client.chat.return_value = {"message": {"content": "Hello, how can I help?"}}
        mock_client_class.return_value = mock_client

        with patch("src.pipeline.llm.config") as mock_config:
            mock_config.llm.host = "http://localhost:11434"
            mock_config.llm.host_fallback = "http://backup:11434"
            mock_config.llm.model = "escolhido-por-medicao:4b"
            mock_config.llm.model_fallback = "escolhido-por-medicao:4b"
            mock_config.llm.system_prompt = "Test prompt"

            llm = OllamaLLM()
            result = llm.chat("Hello")

            assert result.success is True
            assert result.data == "Hello, how can I help?"
            assert result.duration_ms > 0

            # Verify call arguments
            call_args = mock_client.chat.call_args
            # The configured model, not a literal. It asserted "llama3.1:8b"
            # until 2026-10-03, and the swap to gemma3:4b then failed in prod:
            # the test pinned the model NAME, so any change of model broke a
            # plumbing test while the plumbing stayed identical. What is worth
            # asserting is that the configured value is the one sent -- the
            # property that survives a model swap. The stub value above is
            # deliberately not a real model name, so a hardcoded default in the
            # code cannot pass this by accident.
            assert call_args.kwargs["model"] == "escolhido-por-medicao:4b"
            assert call_args.kwargs["options"]["num_ctx"] == 8192
            messages = call_args.kwargs["messages"]
            assert len(messages) == 2
            assert messages[0]["role"] == "system"
            assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hello"

    @patch("src.pipeline.llm.ollama.Client")
    def test_chat_uses_custom_system_prompt(self, mock_client_class):
        """Test chat uses custom system prompt when provided."""
        mock_client = MagicMock()
        mock_client.chat.return_value = {"message": {"content": "Response"}}
        mock_client_class.return_value = mock_client

        llm = OllamaLLM()
        _result = llm.chat("Hello", system_prompt="Custom system")

        call_kwargs = mock_client.chat.call_args.kwargs
        messages = call_kwargs["messages"]
        assert messages[0]["content"] == "Custom system"

    @patch("src.pipeline.llm.ollama.Client")
    def test_chat_handles_exception(self, mock_client_class):
        """Test chat returns Result.fail on exception."""
        mock_client = MagicMock()
        mock_client.chat.side_effect = Exception("Model timeout")
        mock_client_class.return_value = mock_client

        llm = OllamaLLM()
        result = llm.chat("Hello")

        assert result.success is False
        assert "Model timeout" in result.error

    @patch("src.pipeline.llm.ollama.Client")
    def test_chat_includes_duration(self, mock_client_class):
        """Test chat includes duration_ms in result."""
        mock_client = MagicMock()
        mock_client.chat.return_value = {"message": {"content": "Response"}}
        mock_client_class.return_value = mock_client

        llm = OllamaLLM()
        result = llm.chat("Hello")

        assert result.duration_ms > 0

    @patch("src.pipeline.llm.ollama.Client")
    def test_chat_falls_back_when_primary_fails(self, mock_client_class):
        """Test chat retries the fallback host when the primary fails."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.chat.side_effect = [
            Exception("primary down"),
            {"message": {"content": "fallback answer"}},
        ]

        llm = OllamaLLM(
            host="http://primary:11434",
            model="primary-model",
            fallback_host="http://fallback:11434",
            fallback_model="fallback-model",
        )
        result = llm.chat("Obrigado.")

        assert result.success is True
        assert result.data == "fallback answer"
        # fallback model used on the second attempt
        calls = mock_client.chat.call_args_list
        assert len(calls) == 2
        assert calls[1].kwargs["model"] == "fallback-model"

    @patch("src.pipeline.llm.ollama.Client")
    def test_check_connection_uses_fallback_host(self, mock_client_class):
        """Test check_connection falls back to the backup host."""
        mock_client = MagicMock()
        mock_client_class.side_effect = [
            mock_client,  # primary client
            mock_client,  # fallback client
        ]
        mock_client.list.return_value = {"models": [{"name": "fallback-model"}]}

        llm = OllamaLLM(
            host="http://primary:11434",
            model="primary-model",
            fallback_host="http://fallback:11434",
            fallback_model="fallback-model",
        )
        result = llm.check_connection()

        assert result.success is True


class TestChatConvenienceFunction:
    """Test chat() convenience function."""

    @patch("src.pipeline.llm.OllamaLLM")
    def test_chat_delegates_to_class(self, mock_llm_class):
        """Test convenience function creates OllamaLLM and calls chat."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = Result.ok("Response", duration_ms=50.0)
        mock_llm_class.return_value = mock_llm

        result = chat("Hello", system_prompt="Custom")

        assert result.success is True
        assert result.data == "Response"
        mock_llm.chat.assert_called_once_with("Hello", "Custom")
