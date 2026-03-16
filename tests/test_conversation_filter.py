"""Tests for conversation filter module."""

from memory_core.conversation_filter import (
    extract_user_messages,
    filter_conversation,
)


class TestFilterConversation:

    def test_user_messages_kept_fully(self):
        messages = [
            {"role": "user", "content": "Please fix the bug in auth.py"},
        ]
        result = filter_conversation(messages)
        assert "fix the bug in auth.py" in result

    def test_agent_messages_condensed(self):
        long_code = "```python\n" + "\n".join(f"line {i}" for i in range(20)) + "\n```"
        messages = [
            {"role": "user", "content": "Show me code"},
            {"role": "assistant", "content": f"Sure, I'll help! Here's the code:\n{long_code}"},
        ]
        result = filter_conversation(messages)
        assert "[code block" in result
        assert "line 15" not in result

    def test_filler_stripped(self):
        messages = [
            {"role": "assistant", "content": "Sure, I'll help you with that. The error is in line 5."},
        ]
        result = filter_conversation(messages)
        assert "Sure, I'll help" not in result
        assert "error" in result.lower() or "line 5" in result

    def test_max_total_chars(self):
        messages = [
            {"role": "user", "content": "x" * 20000},
        ]
        result = filter_conversation(messages)
        assert len(result) <= 16100  # 16000 limit + prefix overhead

    def test_empty_messages(self):
        result = filter_conversation([])
        assert result == ""

    def test_empty_content_skipped(self):
        messages = [
            {"role": "user", "content": ""},
            {"role": "user", "content": "Real message"},
        ]
        result = filter_conversation(messages)
        assert "Real message" in result


class TestExtractUserMessages:

    def test_basic_extraction(self):
        raw = "user: Hello world\nassistant: Hi there\nuser: How are you?"
        result = extract_user_messages(raw)
        assert "Hello world" in result
        assert "How are you" in result
        assert "Hi there" not in result

    def test_multiline_user(self):
        raw = "user: Line 1\nLine 2\nassistant: Response"
        result = extract_user_messages(raw)
        assert "Line 1" in result
        assert "Line 2" in result

    def test_empty_input(self):
        result = extract_user_messages("")
        assert result == ""
