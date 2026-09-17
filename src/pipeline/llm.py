"""
Language Model using Ollama.
"""

from typing import Optional

import ollama

from config import config
from src.pipeline.utils import Result, logger


class OllamaLLM:
    """Ollama chat completion client."""

    def __init__(self, host: Optional[str] = None, model: Optional[str] = None):
        self.host = host or config.llm.host
        self.model = model or config.llm.model
        self.client = ollama.Client(host=self.host)
        self._system_prompt = config.llm.system_prompt

    def check_connection(self) -> Result:
        """Verify Ollama is reachable and model exists."""
        try:
            models = self.client.list()
            model_names = [m["name"] for m in models.get("models", [])]
            if self.model not in model_names:
                return Result.fail(
                    f"Model {self.model} not found. Available: {model_names}"
                )
            return Result.ok(None)
        except Exception as e:
            return Result.fail(f"Ollama connection failed: {e}")

    def chat(self, prompt: str, system_prompt: Optional[str] = None) -> Result:
        """
        Send prompt to Ollama and get response.
        Args:
            prompt: User input text
            system_prompt: Optional override for system prompt
        Returns:
            Result with response text in data field
        """
        import time

        start = time.perf_counter()

        try:
            messages = [
                {"role": "system", "content": system_prompt or self._system_prompt},
                {"role": "user", "content": prompt},
            ]

            response = self.client.chat(
                model=self.model, messages=messages, options={"num_ctx": 8192}
            )

            text = response.get("message", {}).get("content", "").strip()
            duration_ms = (time.perf_counter() - start) * 1000

            logger.info(
                f"LLM response: '{text[:100]}...' (duration_ms={duration_ms:.1f})"
            )
            return Result.ok(text, duration_ms=duration_ms)

        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(f"Ollama chat error: {e}")
            return Result.fail(str(e), duration_ms=duration_ms)


def chat(prompt: str, system_prompt: Optional[str] = None) -> Result:
    """Convenience function for direct chat."""
    llm = OllamaLLM()
    return llm.chat(prompt, system_prompt)
