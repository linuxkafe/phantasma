"""Regression: a cache hit must still step the FlyBrain.

`_respond_with_llm` used to return on a cache hit BEFORE calling
`self._fly_brain.step(...)`, so repeated turns never reached the brain and
`flybrain_state` stayed empty. These tests pin the step to every turn.
"""

from unittest.mock import MagicMock, patch

import assistant


def _assistant_with_mock_brain():
    a = assistant.PhantasmaPipeline.__new__(assistant.PhantasmaPipeline)
    a._fly_brain = MagicMock()
    a.logger = MagicMock()
    return a


def test_cache_hit_still_steps_flybrain():
    a = _assistant_with_mock_brain()
    with (
        patch.object(assistant, "get_cached_response", return_value="resposta em cache"),
        patch.object(assistant, "sanitize_llm_context"),
        patch.object(assistant, "retrieve_from_rag", return_value=[]),
    ):
        out = a._respond_with_llm("olá")

    assert out == "resposta em cache"
    assert a._fly_brain.step.call_count == 1, (
        "a cache hit must still step the FlyBrain: it is a real conversation "
        "turn, and skipping it starves the brain of turns"
    )
    kwargs = a._fly_brain.step.call_args.kwargs
    assert 0.0 <= kwargs["topic_angle_deg"] < 360.0
    assert kwargs["novelty"] == 0.5
    assert kwargs["reward"] == 0.0


class _OllamaDown(Exception):
    pass


class _DownClient:
    """A stand-in for ollama.Client whose host is unreachable."""

    def __init__(self, *args, **kwargs):
        pass

    def chat(self, *args, **kwargs):
        raise _OllamaDown("host down")


def test_cache_miss_also_steps_exactly_once():
    a = _assistant_with_mock_brain()
    with (
        patch.object(assistant, "get_cached_response", return_value=None),
        patch.object(assistant, "sanitize_llm_context", return_value=""),
        patch.object(assistant, "retrieve_from_rag", return_value=[]),
        # The point of this test is the step count, not the LLM. It used to
        # dial the real Ollama host, which stopped answering on 2026-09-29;
        # the suite then hung in network time, minutes after the test itself
        # had already made its assertion.
        patch("ollama.Client", _DownClient),
    ):
        a._respond_with_llm("pergunta nova")
    assert a._fly_brain.step.call_count == 1, (
        "the non-cached path must still step exactly once -- moving the step "
        "must not double-step the miss path"
    )
