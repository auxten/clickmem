"""Tests for LocalLLMEngine — local model inference.

These tests verify the engine's interface and error handling without
requiring an actual model download. Real model tests are marked 'slow'.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from memory_core.local_llm import LocalLLMEngine, _strip_think_tags


class TestStripThinkTags:
    """Test the <think> tag stripping utility."""

    def test_no_tags(self):
        assert _strip_think_tags("hello world") == "hello world"

    def test_single_tag(self):
        assert _strip_think_tags("<think>reasoning</think>answer") == "answer"

    def test_tag_with_newlines(self):
        text = "<think>\nstep 1\nstep 2\n</think>\n{\"a\": 1}"
        assert _strip_think_tags(text) == '{"a": 1}'

    def test_multiple_tags(self):
        text = "<think>first</think>middle<think>second</think>end"
        assert _strip_think_tags(text) == "middleend"

    def test_empty_tag(self):
        assert _strip_think_tags("<think></think>result") == "result"

    def test_no_tag_passthrough(self):
        text = '{"summary": "test", "tags": ["a"]}'
        assert _strip_think_tags(text) == text


class TestLocalLLMEngineInit:
    """Test engine initialization and configuration."""

    def test_default_model(self):
        engine = LocalLLMEngine()
        assert engine.model_name == "Qwen/Qwen3.5-2B"

    def test_custom_model(self):
        engine = LocalLLMEngine(model_name="my/model")
        assert engine.model_name == "my/model"

    def test_env_model(self):
        with patch.dict(os.environ, {"CLICKMEM_LOCAL_MODEL": "env/model"}):
            engine = LocalLLMEngine()
            assert engine.model_name == "env/model"

    def test_explicit_overrides_env(self):
        with patch.dict(os.environ, {"CLICKMEM_LOCAL_MODEL": "env/model"}):
            engine = LocalLLMEngine(model_name="explicit/model")
            assert engine.model_name == "explicit/model"

    def test_backend_none_before_load(self):
        engine = LocalLLMEngine()
        assert engine.backend == "none"


class TestLocalLLMEngineLoadFailure:
    """Test graceful failure when no backend is available."""

    @patch("memory_core.local_llm.LocalLLMEngine._try_transformers", side_effect=ImportError("no torch"))
    @patch("memory_core.local_llm.LocalLLMEngine._try_mlx", side_effect=ImportError("no mlx"))
    def test_raises_when_no_backend(self, mock_mlx, mock_tf):
        engine = LocalLLMEngine()
        with pytest.raises(RuntimeError, match="No LLM backend available"):
            engine.load()

    def test_complete_before_load_raises(self):
        engine = LocalLLMEngine()
        with pytest.raises(AssertionError, match="load"):
            engine.complete("test")


class TestLocalLLMEngineWithMockBackend:
    """Test the engine with a mocked generation function."""

    def test_complete_returns_stripped_output(self):
        engine = LocalLLMEngine()
        engine._generate_fn = lambda p: '  {"result": true}  '
        engine._backend = "mock"
        assert engine.complete("test") == '{"result": true}'

    def test_complete_strips_think_tags(self):
        engine = LocalLLMEngine()
        engine._generate_fn = lambda p: '<think>reasoning here</think>{"answer": 42}'
        engine._backend = "mock"
        assert engine.complete("test") == '{"answer": 42}'

    def test_backend_property(self):
        engine = LocalLLMEngine()
        engine._generate_fn = lambda p: "ok"
        engine._backend = "mlx"
        assert engine.backend == "mlx"


class TestLocalLLMEngineTryMLX:
    """Test MLX backend loading."""

    def test_mlx_sets_backend(self):
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.return_value = "formatted"

        with patch("memory_core.local_llm.LocalLLMEngine._try_mlx") as mock_try:
            def fake_mlx(self_ref=None):
                engine._generate_fn = lambda p: '{"ok": true}'
                engine._backend = "mlx"
            mock_try.side_effect = fake_mlx

            engine = LocalLLMEngine()
            mock_try(engine)
            assert engine.backend == "mlx"


class TestLocalLLMEngineTryTransformers:
    """Test transformers backend loading."""

    def test_transformers_import_error(self):
        engine = LocalLLMEngine()
        with patch.dict("sys.modules", {"torch": None}):
            with pytest.raises(ImportError):
                engine._try_transformers()
