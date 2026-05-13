"""local_or_remote shim: route to RemoteTransport when a server is reachable,
fall through to the local domain modules otherwise.

Both branches are exercised here without spinning up a real server — we stub
:meth:`RemoteTransport.health` / :meth:`RemoteTransport._post` so the probe
and the actual write are observable from the test.
"""

from __future__ import annotations

from clickmem import local_or_remote
from clickmem import transport as transport_mod


def _stub_remote_up(monkeypatch, calls: list[tuple[str, dict]]) -> None:
    """Make every RemoteTransport instance look like a reachable server."""

    def _health(self):
        return {"ok": True, "version": "test", "backend": "local", "embedding_model": "stub"}

    def _post(self, path, body=None):
        calls.append((path, dict(body or {})))
        return {"ok": True, "id": "stub-id"}

    monkeypatch.setattr(transport_mod.RemoteTransport, "health", _health, raising=False)
    monkeypatch.setattr(transport_mod.RemoteTransport, "_post", _post, raising=False)


def _stub_remote_down(monkeypatch) -> None:
    def _health(self):  # noqa: ANN001
        raise ConnectionError("server down (stub)")

    def _post(self, path, body=None):  # noqa: ANN001
        raise AssertionError("must not POST when server is down")

    monkeypatch.setattr(transport_mod.RemoteTransport, "health", _health, raising=False)
    monkeypatch.setattr(transport_mod.RemoteTransport, "_post", _post, raising=False)


def test_event_write_routes_remote_when_server_reachable(monkeypatch, backend):
    local_or_remote.reset()
    calls: list[tuple[str, dict]] = []
    _stub_remote_up(monkeypatch, calls)

    local_or_remote.event_write(
        "agent.test",
        agent="claude_code",
        message="probe-up smoke",
        payload={"foo": "bar"},
    )

    assert any(path == "/v1/events" for path, _ in calls), calls
    posted = next(body for path, body in calls if path == "/v1/events")
    assert posted["kind"] == "agent.test"
    assert posted["agent"] == "claude_code"
    assert posted["payload"] == {"foo": "bar"}

    rows = backend.query("SELECT count() AS c FROM events WHERE kind = 'agent.test'")
    assert int(rows[0]["c"]) == 0


def test_event_write_falls_through_to_local_when_server_down(monkeypatch, backend):
    local_or_remote.reset()
    _stub_remote_down(monkeypatch)

    local_or_remote.event_write(
        "agent.install",
        agent="cursor",
        message="local-only smoke",
        payload={"installed": True},
    )

    rows = backend.query(
        "SELECT count() AS c FROM events WHERE kind = 'agent.install' AND agent = 'cursor'"
    )
    assert int(rows[0]["c"]) == 1


def test_raw_append_routes_remote_when_server_reachable(monkeypatch, backend):
    local_or_remote.reset()
    calls: list[tuple[str, dict]] = []
    _stub_remote_up(monkeypatch, calls)

    res = local_or_remote.raw_append(
        "hello world",
        session_id="sess-1",
        agent="claude_code",
        role="test",
        meta={"clickmem_test": True},
    )

    assert res == {"ok": True, "id": "stub-id"}
    assert any(path == "/v1/raw" for path, _ in calls), calls

    rows = backend.query("SELECT count() AS c FROM raw_transcripts")
    assert int(rows[0]["c"]) == 0


def test_raw_append_falls_through_when_server_down(monkeypatch, backend):
    local_or_remote.reset()
    _stub_remote_down(monkeypatch)

    res = local_or_remote.raw_append(
        "hello local",
        session_id="sess-2",
        agent="claude_code",
        role="test",
        meta={"clickmem_test": True},
    )

    assert res.get("ok") is True
    rows = backend.query("SELECT count() AS c FROM raw_transcripts WHERE session_id = 'sess-2'")
    assert int(rows[0]["c"]) == 1


def test_in_server_process_disables_autoprobe(monkeypatch, backend):
    """If mark_in_server_process() was called, the shim must stay local even
    when a server would otherwise be reachable.
    """
    local_or_remote.reset()
    local_or_remote.mark_in_server_process()

    def _health_should_not_be_called(self):  # noqa: ANN001
        raise AssertionError("probe must not happen inside the server process")

    def _post_should_not_be_called(self, path, body=None):  # noqa: ANN001
        raise AssertionError("must not POST inside the server process")

    monkeypatch.setattr(transport_mod.RemoteTransport, "health", _health_should_not_be_called, raising=False)
    monkeypatch.setattr(transport_mod.RemoteTransport, "_post", _post_should_not_be_called, raising=False)

    local_or_remote.event_write("agent.test", agent="claude_code", message="in-server")

    rows = backend.query(
        "SELECT count() AS c FROM events WHERE kind = 'agent.test' AND agent = 'claude_code'"
    )
    assert int(rows[0]["c"]) == 1


def test_explicit_remote_url_used_without_probe(monkeypatch, backend):
    """CLICKMEM_REMOTE skips the probe — RemoteTransport is used directly."""
    local_or_remote.reset()
    monkeypatch.setenv("CLICKMEM_REMOTE", "http://example.invalid:9527")

    calls: list[tuple[str, dict]] = []

    def _health_not_called(self):  # noqa: ANN001
        raise AssertionError("explicit CLICKMEM_REMOTE must skip the probe")

    def _post(self, path, body=None):  # noqa: ANN001
        calls.append((path, dict(body or {})))
        return {"ok": True}

    monkeypatch.setattr(transport_mod.RemoteTransport, "health", _health_not_called, raising=False)
    monkeypatch.setattr(transport_mod.RemoteTransport, "_post", _post, raising=False)

    local_or_remote.event_write("agent.test", agent="claude_code")
    assert calls and calls[0][0] == "/v1/events"
