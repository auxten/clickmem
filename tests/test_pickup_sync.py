from __future__ import annotations

from clickmem.adapters.base import RawSession
from clickmem.pickup_sync import build_run, sanitize_transcript


def test_sanitize_redacts_secrets_and_keeps_recent_context():
    raw = "token=very-secret-token\n" + ("old context\n" * 100) + "final decision"
    cleaned = sanitize_transcript(raw, max_chars=300)

    assert "very-secret-token" not in cleaned
    assert "[REDACTED]" in cleaned
    assert "transcript truncated" in cleaned
    assert cleaned.endswith("final decision")


def test_build_run_allows_zero_candidates(monkeypatch):
    class FakeAdapter:
        def detect(self):
            return True

        def iter_raw_sessions(self, since=None):
            yield RawSession(
                session_id="s1",
                agent="codex",
                text="user: investigate a transient CI failure\nassistant: it was a network timeout",
                cwd="",
                project_id="chdb-io/chdb",
                meta={"path": "/tmp/session.jsonl"},
            )

    monkeypatch.setattr("clickmem.pickup_sync.adapters.get", lambda name: FakeAdapter())
    payload = build_run(
        since=0,
        agent_names=["codex"],
        summarizer_name="fake",
        summarize=lambda prompt: {"candidates": []},
        max_session_chars=10_000,
    )

    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["candidates"] == []
    assert payload["prompt_version"] == "pickup-v1"
