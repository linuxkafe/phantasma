"""
Language Model using Ollama.

Provides OllamaLLM class for chat completion and a convenience chat() function.
"""

import time
from typing import Optional

import ollama

from config import config
from src.pipeline.utils import Result, logger


class OllamaLLM:
    """Ollama chat completion client.

    Wraps ollama.Client with connection checking, timing, and structured logging.

    Attributes:
        host: Ollama server URL.
        model: Model name to use.
        client: Underlying ollama.Client instance.
        _system_prompt: Default system prompt from config.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        fallback_host: Optional[str] = None,
        fallback_model: Optional[str] = None,
    ):
        """Initialize Ollama client.

        Args:
            host: Ollama server URL. Defaults to config.llm.host.
            model: Model name. Defaults to config.llm.model.
            fallback_host: Fallback server URL. Defaults to config.llm.host_fallback.
            fallback_model: Fallback model name. Defaults to config.llm.model_fallback.
        """
        self.host = host or config.llm.host
        self.model = model or config.llm.model
        self.fallback_host = fallback_host or config.llm.host_fallback
        self.fallback_model = fallback_model or config.llm.model_fallback
        self.client = ollama.Client(host=self.host)
        self._fallback_client = ollama.Client(host=self.fallback_host)
        self._system_prompt = config.llm.system_prompt

    def check_connection(self) -> Result:
        """Verify Ollama is reachable and model exists.

        Falls back to the fallback host when the primary is unreachable.

        Returns:
            Result.ok(None) if healthy, Result.fail(error) if not.
        """
        errors = []
        for label, client, model in (
            ("primary", self.client, self.model),
            ("fallback", self._fallback_client, self.fallback_model),
        ):
            if self.fallback_host == self.host and label == "fallback":
                break
            try:
                models = client.list()
                model_names = [m["name"] for m in models.get("models", [])]
                if model not in model_names:
                    errors.append(f"{model} not found on {client.host}")
                    logger.warning(f"Ollama {label}: {errors[-1]}")
                    continue
                if label == "fallback":
                    logger.info(f"Ollama connected via fallback host: {client.host}")
                return Result.ok(None)
            except Exception as e:
                errors.append(str(e))
                logger.warning(f"Ollama {label} unreachable ({client.host}): {e}")
        return Result.fail(
            "Ollama connection failed (primary " + self.host + ", fallback "
            + self.fallback_host + "): " + "; ".join(errors)
        )

    def chat(self, prompt: str, system_prompt: Optional[str] = None) -> Result:
        """Send prompt to Ollama and get response.

        Attempts the primary host; on failure retries the fallback host once.

        Args:
            prompt: User input text.
            system_prompt: Optional override for system prompt.
                If None, uses config.llm.system_prompt.

        Returns:
            Result.ok(response_text) with response, or Result.fail(error).
            Result.duration_ms contains API call time.
        """
        start = time.perf_counter()

        messages = [
            {"role": "system", "content": system_prompt or self._system_prompt},
            {"role": "user", "content": prompt},
        ]

        attempts = [("primary", self.client, self.model)]
        if self.fallback_host != self.host:
            attempts.append(("fallback", self._fallback_client, self.fallback_model))

        errors = []
        for label, client, model in attempts:
            try:
                response = client.chat(
                    model=model, messages=messages, options={"num_ctx": 8192}
                )
                text = response.get("message", {}).get("content", "").strip()
                duration_ms = (time.perf_counter() - start) * 1000
                if label == "fallback":
                    logger.info(
                        f"LLM served by fallback host {client.host} ({model}) "
                        f"duration_ms={duration_ms:.1f}"
                    )
                logger.info(
                    f"LLM response: '{text[:100]}...' (duration_ms={duration_ms:.1f})"
                )
                return Result.ok(text, duration_ms=duration_ms)
            except Exception as e:
                errors.append(str(e))
                logger.warning(f"Ollama {label} chat failed ({client.host}): {e}")

        duration_ms = (time.perf_counter() - start) * 1000
        last_error = "all Ollama hosts failed: " + "; ".join(errors)
        logger.error(f"Ollama chat error: {last_error}")
        return Result.fail(last_error, duration_ms=duration_ms)


def chat(prompt: str, system_prompt: Optional[str] = None) -> Result:
    """Convenience function for direct chat.

    Creates OllamaLLM instance and calls chat(). Use for one-off requests.
    For repeated use, instantiate OllamaLLM once and reuse.

    Args:
        prompt: User input text.
        system_prompt: Optional system prompt override.

    Returns:
        Result with response text in data field.
    """
    llm = OllamaLLM()
    return llm.chat(prompt, system_prompt)
