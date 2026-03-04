"""Mock LLM completion function for testing.

Routes responses based on prompt keywords. Records all calls for verification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class LLMCall:
    """Record of a single LLM call."""

    prompt: str
    response: str


class MockLLMComplete:
    """Mock LLM that returns canned responses based on prompt keywords.

    Usage:
        mock_llm = MockLLMComplete()
        result = mock_llm("Extract memories from this conversation...")
        assert mock_llm.call_count > 0
    """

    def __init__(self, custom_routes: dict[str, str] | None = None):
        self.calls: list[LLMCall] = []
        self._custom_routes = custom_routes or {}

    def __call__(self, prompt: str) -> str:
        response = self._route(prompt)
        self.calls.append(LLMCall(prompt=prompt, response=response))
        return response

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_call(self) -> LLMCall | None:
        return self.calls[-1] if self.calls else None

    def reset(self) -> None:
        self.calls.clear()

    def _route(self, prompt: str) -> str:
        prompt_lower = prompt.lower()

        # Check custom routes first
        for keyword, response in self._custom_routes.items():
            if keyword.lower() in prompt_lower:
                return response

        # Default keyword routing
        if "extract" in prompt_lower:
            return self._extract_response()
        elif "compress" in prompt_lower:
            return self._compress_response()
        elif "promote" in prompt_lower:
            return self._promote_response()
        elif "review" in prompt_lower:
            return self._review_response()
        elif "working" in prompt_lower:
            return self._working_response()
        elif "emergency" in prompt_lower:
            return self._emergency_response()
        else:
            return self._default_response()

    @staticmethod
    def _extract_response() -> str:
        return json.dumps([
            {
                "content": "User discussed project architecture decisions",
                "layer": "episodic",
                "category": "decision",
                "tags": ["architecture"],
                "entities": ["User"],
            },
            {
                "content": "User prefers functional programming style",
                "layer": "semantic",
                "category": "preference",
                "tags": ["coding-style"],
                "entities": ["User"],
            },
        ])

    @staticmethod
    def _compress_response() -> str:
        return json.dumps({
            "summary": "Monthly summary: Multiple architecture decisions were made including adopting microservices pattern and gRPC for inter-service communication.",
            "category": "decision",
            "tags": ["architecture", "microservices", "grpc"],
            "entities": ["User"],
        })

    @staticmethod
    def _promote_response() -> str:
        return json.dumps({
            "should_promote": True,
            "content": "User consistently works with microservices architecture",
            "category": "knowledge",
            "tags": ["architecture", "microservices"],
        })

    @staticmethod
    def _review_response() -> str:
        return json.dumps({
            "stale_ids": [],
            "updates": [],
        })

    @staticmethod
    def _working_response() -> str:
        return "User is working on memory system integration, last discussed HNSW configuration."

    @staticmethod
    def _emergency_response() -> str:
        return json.dumps([
            {
                "content": "Critical context before compaction: user was debugging vector search performance",
                "layer": "episodic",
                "category": "event",
                "tags": ["debugging", "performance"],
                "entities": ["User"],
            },
        ])

    @staticmethod
    def _default_response() -> str:
        return json.dumps({"status": "ok"})
