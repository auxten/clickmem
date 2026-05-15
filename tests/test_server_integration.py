"""Integration tests covering every endpoint group on the FastAPI app.

Each test uses the per-test backend / app fixtures so there is no shared state.
"""

from __future__ import annotations

import json

import pytest


# ---------- Health / Stats ------------------------------------------------


async def test_health_ok(client):
    r = await client.get("/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["backend"] == "local"


async def test_stats_overview_zero_state(client):
    r = await client.get("/v1/stats/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["pinned"] == 0


# ---------- Memories ------------------------------------------------------


async def test_memory_roundtrip(client):
    r = await client.post(
        "/v1/memories",
        json={"content": "principle of small commits", "kind": "principle", "project_id": "p1"},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "added"
    mid = payload["id"]

    r = await client.get(f"/v1/memories/{mid}")
    assert r.status_code == 200
    assert r.json()["content"] == "principle of small commits"

    r = await client.patch(
        f"/v1/memories/{mid}",
        json={"content": "principle: commit small, commit often"},
    )
    assert r.status_code == 200
    assert r.json()["status"] in ("edited", "merged")

    r = await client.get(f"/v1/memories/{mid}/history")
    assert r.status_code == 200
    versions = [h["version"] for h in r.json()]
    assert versions == sorted(versions)
    assert len(versions) >= 2

    r = await client.delete(f"/v1/memories/{mid}", params={"reason": "stale"})
    assert r.status_code == 200
    assert r.json()["status"] == "contracted"


async def test_memory_list_filters(client):
    await client.post("/v1/memories", json={"content": "first one", "project_id": "p1"})
    await client.post("/v1/memories", json={"content": "second", "project_id": "p2"})
    r = await client.get("/v1/memories", params={"project_id": "p2"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1


async def test_memory_get_not_found(client):
    r = await client.get("/v1/memories/does-not-exist")
    assert r.status_code == 404


# ---------- Recall --------------------------------------------------------


async def test_recall_endpoint_with_score(client):
    await client.post(
        "/v1/memories",
        json={"content": "alpha bravo recall fixture", "project_id": "p1", "privacy": "public"},
    )
    r = await client.post(
        "/v1/recall",
        json={"query": "alpha bravo recall fixture", "project_id": "p1", "limit": 5},
    )
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert hits and hits[0]["project_id"] == "p1"


async def test_recall_trace_endpoint(client):
    await client.post(
        "/v1/memories",
        json={"content": "trace fixture content", "project_id": "p1", "privacy": "public"},
    )
    r = await client.post(
        "/v1/recall/trace",
        json={"query": "trace fixture content", "project_id": "p1", "limit": 5},
    )
    assert r.status_code == 200
    payload = r.json()
    assert "candidates" in payload
    assert "hits" in payload


# ---------- Conflicts -----------------------------------------------------


async def test_conflicts_endpoint(client):
    r = await client.get("/v1/conflicts")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ---------- Blacklist -----------------------------------------------------


async def test_blacklist_crud(client):
    r = await client.post(
        "/v1/blacklist", json={"pattern": "forbidden-1", "scope": "global"}
    )
    assert r.status_code == 200
    bid = r.json()["id"]
    r = await client.get("/v1/blacklist")
    assert any(b["id"] == bid for b in r.json())
    r = await client.delete(f"/v1/blacklist/{bid}")
    assert r.status_code == 200


# ---------- Raw -----------------------------------------------------------


async def test_raw_landing_then_get_raw(client):
    r = await client.post(
        "/v1/raw",
        json={
            "text": "session log message",
            "session_id": "session-1",
            "agent": "claude_code",
        },
    )
    assert r.status_code == 200
    rj = r.json()
    assert rj["ok"]
    r = await client.get("/v1/get_raw", params={"session_id": "session-1"})
    assert r.status_code == 200
    rows = r.json()
    assert any("session log message" in row["text"] for row in rows)
    r = await client.get("/v1/get-raw", params={"session_id": "session-1"})
    assert r.status_code == 200


# ---------- Projects ------------------------------------------------------


async def test_projects_endpoints(client):
    r = await client.post(
        "/v1/projects/link", json={"a": "pa", "b": "pb", "reason": "share"}
    )
    assert r.status_code == 200
    body = r.json()
    assert "pb" in body["a"]["allowed_cross_refs"]
    r = await client.get("/v1/projects")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    assert {"pa", "pb"}.issubset(ids)


# ---------- Agents --------------------------------------------------------


async def test_agents_listing_lists_installed_agents_with_host(client):
    r = await client.get("/v1/agents")
    assert r.status_code == 200
    rows = r.json()
    names = [a["name"] for a in rows]
    assert rows
    assert "generic" in names
    assert all(a["installed"] is True for a in rows)
    assert all(a["host"] for a in rows)


async def test_agents_activity_returns_buckets(client):
    r = await client.get("/v1/agents/cursor/activity", params={"hours": 1})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_agent_install_does_not_import_existing_docs(client, monkeypatch):
    """Installing an adapter wires hooks only; existing docs import is explicit."""
    from clickmem import server as server_mod

    def fail_import(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("agent install must not import existing docs")

    monkeypatch.setattr(server_mod.import_docs_mod, "run_for_adapter", fail_import)
    monkeypatch.setattr(
        server_mod.agents_mod,
        "install",
        lambda name, server_url="": {
            "ok": True,
            "installed": True,
            "agent": name,
            "imported": False,
            "message": "hooks installed",
        },
    )

    r = await client.post("/v1/agents/cursor/install")
    assert r.status_code == 200
    body = r.json()
    assert body["installed"] is True
    assert body["imported"] is False


# ---------- Phase 10 backend gap fixes -----------------------------------


async def test_dashboard_spa_fallback_for_unknown_path(client):
    """Hard-refresh on /dashboard/<anything> must serve the SPA index when built."""
    from pathlib import Path
    import clickmem

    dist = Path(clickmem.__file__).resolve().parent / "dashboard" / "dist"
    if not (dist / "index.html").is_file():
        pytest.skip("dashboard build not present")

    r = await client.get("/dashboard/memories")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


async def test_imports_run_endpoint(client, tmp_path, monkeypatch):
    """POST /v1/imports/{name}/run dispatches through the named adapter."""
    # Build a stand-in AGENTS.md inside a temporary git repo so import_docs
    # has a real file to walk, then override the cursor adapter's
    # iter_doc_paths so it reports that file.
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    (repo / "AGENTS.md").write_text(
        "## Bootstrapping\n\n- always check git status first\n- avoid force-push to main\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "AGENTS.md"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-m", "agents", "-q"],
        cwd=str(repo),
        check=True,
    )

    from clickmem.adapters import cursor as cursor_mod
    monkeypatch.setattr(cursor_mod, "iter_doc_paths", lambda: [repo / "AGENTS.md"])

    r = await client.post("/v1/imports/cursor/run", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["started"] is True
    assert body["name"] == "cursor"
    assert body["files_scanned"] == 1
    assert body["accepted"] >= 1


async def test_imports_run_unknown_adapter(client):
    r = await client.post("/v1/imports/bogus/run", json={})
    assert r.status_code == 404


async def test_agents_all_activity(client):
    """The /v1/agents/_all/activity?hours=24 aggregate endpoint."""
    # Generate at least one event so the bucket has something to count.
    await client.post(
        "/v1/memories",
        json={"content": "activity feeder one", "project_id": "p1"},
    )
    r = await client.get("/v1/agents/_all/activity", params={"hours": 24})
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert all("count" in row for row in rows)
