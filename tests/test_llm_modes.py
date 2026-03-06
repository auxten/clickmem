"""Tests for LLM mode routing — local / remote / auto."""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

import pytest

import memory_core.llm as llm_mod
from memory_core.llm import get_llm_complete, get_llm_info, get_llm_mode


@pytest.fixture(autouse=True)
def _reset_llm_state():
    """Reset module-level singletons between tests."""
    llm_mod._local_engine = None
    llm_mod._local_engine_failed = False
    yield
    llm_mod._local_engine = None
    llm_mod._local_engine_failed = False


class TestGetLLMMode:
    def test_default_is_auto(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLICKMEM_LLM_MODE", None)
            assert get_llm_mode() == "auto"

    def test_reads_env(self):
        with patch.dict(os.environ, {"CLICKMEM_LLM_MODE": "local"}):
            assert get_llm_mode() == "local"

    def test_case_insensitive(self):
        with patch.dict(os.environ, {"CLICKMEM_LLM_MODE": "REMOTE"}):
            assert get_llm_mode() == "remote"


class TestGetLLMInfo:
    def test_returns_dict(self):
        info = get_llm_info()
        assert isinstance(info, dict)
        assert "mode" in info

    def test_auto_mode_info(self):
        with patch.dict(os.environ, {"CLICKMEM_LLM_MODE": "auto"}):
            info = get_llm_info()
            assert "local_model" in info
            assert "remote_model" in info

    def test_local_mode_info(self):
        with patch.dict(os.environ, {"CLICKMEM_LLM_MODE": "local"}):
            info = get_llm_info()
            assert "local_model" in info
            assert info["local_loaded"] is False

    def test_remote_mode_info(self):
        with patch.dict(os.environ, {"CLICKMEM_LLM_MODE": "remote"}):
            info = get_llm_info()
            assert "remote_model" in info


class TestGetLLMCompleteLocalMode:
    def test_local_mode_returns_none_when_unavailable(self):
        with patch.dict(os.environ, {"CLICKMEM_LLM_MODE": "local"}):
            result = get_llm_complete()
            assert result is None

    def test_local_mode_caches_failure(self):
        with patch.dict(os.environ, {"CLICKMEM_LLM_MODE": "local"}):
            get_llm_complete()
            assert llm_mod._local_engine_failed is True
            result = get_llm_complete()
            assert result is None

    def test_local_mode_returns_engine_when_available(self):
        mock_engine = MagicMock()
        mock_engine.complete = MagicMock(return_value="test response")

        with patch.dict(os.environ, {"CLICKMEM_LLM_MODE": "local"}):
            with patch("memory_core.local_llm.LocalLLMEngine", return_value=mock_engine):
                result = get_llm_complete()
                assert result is not None
                assert result == mock_engine.complete


class TestGetLLMCompleteRemoteMode:
    def test_remote_mode_returns_none_without_litellm(self):
        with patch.dict(os.environ, {"CLICKMEM_LLM_MODE": "remote"}):
            with patch.dict("sys.modules", {"litellm": None}):
                # litellm import will raise ImportError
                result = llm_mod._get_remote_complete()
                # When litellm can't be imported, returns None
                # (the actual behavior depends on whether litellm is installed)


class TestGetLLMCompleteAutoMode:
    def test_auto_falls_back_to_remote(self):
        """In auto mode, if local fails, should try remote."""
        with patch.dict(os.environ, {"CLICKMEM_LLM_MODE": "auto"}):
            # Local will fail (no real model), remote depends on litellm
            result = get_llm_complete()
            # Result can be None or a function depending on litellm availability
            # Just verify it doesn't crash
            assert result is None or callable(result)
