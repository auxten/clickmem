"""Store selection: local record, id rules, durable catalog.

Runs against a temp HOME so the real ~/.clickmem is never touched.
"""
import importlib
import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

# The durable store paths need chdb.durable, which is merged into chDB but not
# yet in a released wheel — skip those until a chdb release ships it.
durable_only = pytest.mark.skipif(
    importlib.util.find_spec("chdb.durable") is None,
    reason="chdb.durable not in this chdb release yet",
)


@pytest.fixture()
def home(monkeypatch):
    d = tempfile.mkdtemp(prefix="clickmem-home-")
    monkeypatch.setenv("HOME", d)
    monkeypatch.delenv("CLICKMEM_DB_PATH", raising=False)
    monkeypatch.delenv("CLICKMEM_DURABLE_ID", raising=False)
    monkeypatch.delenv("CLICKMEM_CH_DATABASE", raising=False)
    import clickmem.stores as stores

    importlib.reload(stores)  # rebind Path.home()-derived module constants
    return Path(d), stores


def test_id_rules(home):
    _, stores = home
    assert stores.suggest_store_id("acme/foo") == "acme-foo"
    assert stores.validate_store_id("acme-foo") == "acme-foo"
    for bad in ("a/b", "a\\b", ".", "..", "_catalog", ""):
        with pytest.raises(ValueError):
            stores.validate_store_id(bad)


def test_local_record_roundtrip(home):
    _, stores = home
    assert stores.active_store_id("durable") is None
    stores.set_active_store("durable", "shawn-main")
    stores.set_active_store("local", "scratch")
    assert stores.active_store_id("durable") == "shawn-main"
    assert stores.active_store_id("local") == "scratch"


def test_config_reads_active_record(home, monkeypatch):
    _, stores = home
    monkeypatch.setenv("CLICKMEM_BACKEND", "durable")
    stores.set_active_store("durable", "picked-store")
    import clickmem.config as config

    cfg = config.get_config(refresh=True)
    assert cfg.durable_id == "picked-store"  # record wins when env is unset
    monkeypatch.setenv("CLICKMEM_DURABLE_ID", "env-store")
    assert config.get_config(refresh=True).durable_id == "env-store"  # env wins


@durable_only
def test_durable_catalog_upsert_and_list(home, monkeypatch):
    _, stores = home
    url = "local:" + tempfile.mkdtemp(prefix="clickmem-cat-")
    monkeypatch.setenv("CLICKMEM_BACKEND", "durable")
    monkeypatch.setenv("CLICKMEM_DURABLE_URL", url)
    import clickmem.config as config

    config.get_config(refresh=True)

    stores.catalog_upsert(url, "shawn-main", {"description": "personal", "memories": 3})
    stores.catalog_upsert(url, "foo-team", {"description": "team"})
    stores.catalog_upsert(url, "shawn-main", {"memories": 5})  # merge, not clobber

    data = stores.catalog_read(url)["stores"]
    assert data["shawn-main"] == {"description": "personal", "memories": 5}
    assert set(data) == {"shawn-main", "foo-team"}

    listing = stores.store_list()
    assert listing["backend"] == "durable"
    ids = {s["id"] for s in listing["stores"]}
    assert ids == {"shawn-main", "foo-team"}


@durable_only
def test_store_new_durable_creates_and_switches(home, monkeypatch):
    _, stores = home
    url = "local:" + tempfile.mkdtemp(prefix="clickmem-new-")
    monkeypatch.setenv("CLICKMEM_BACKEND", "durable")
    monkeypatch.setenv("CLICKMEM_DURABLE_URL", url)
    import clickmem.config as config

    config.get_config(refresh=True)

    res = stores.store_new("acme/foo", description="my store")
    assert res["ok"] and res["id"] == "acme-foo"
    assert stores.active_store_id("durable") == "acme-foo"
    assert "acme-foo" in stores.catalog_read(url)["stores"]

    dup = stores.store_new(store_id="acme-foo")  # already exists → refuse
    assert "error" in dup


def test_store_new_local_creates_switches_and_config_follows(home, monkeypatch):
    _, stores = home
    monkeypatch.setenv("CLICKMEM_BACKEND", "local")
    monkeypatch.delenv("CLICKMEM_DB_PATH", raising=False)
    import clickmem.config as config

    res = stores.store_new("acme/foo")
    assert res["ok"] and res["id"] == "acme-foo"
    assert stores.active_store_id("local") == "acme-foo"
    # the store directory exists and config now points db_path at it
    cfg = config.get_config(refresh=True)
    assert cfg.db_path.name == "acme-foo"
    assert cfg.db_path.is_dir()


def test_store_list_local_shows_default_and_created(home, monkeypatch):
    _, stores = home
    monkeypatch.setenv("CLICKMEM_BACKEND", "local")
    import clickmem.config as config

    config.get_config(refresh=True)
    stores.store_new(store_id="alpha")
    stores.store_new(store_id="beta")
    listing = stores.store_list()
    assert listing["backend"] == "local"
    ids = {s["id"] for s in listing["stores"]}
    assert {"default", "alpha", "beta"} <= ids
    assert listing["active"] == "beta"  # last created is active


def test_store_use_rejects_bad_id_before_recording(home, monkeypatch):
    _, stores = home
    monkeypatch.setenv("CLICKMEM_BACKEND", "local")
    with pytest.raises(ValueError):
        stores.store_use("a/b")
    assert stores.active_store_id("local") is None  # nothing recorded on rejection


def test_store_use_success_records(home, monkeypatch):
    _, stores = home
    monkeypatch.setenv("CLICKMEM_BACKEND", "local")
    res = stores.store_use("myproj")
    assert res["ok"] and res["active"] == "myproj" and res["backend"] == "local"
    assert stores.active_store_id("local") == "myproj"


def test_suggest_store_id_fallbacks(home, monkeypatch):
    _, stores = home
    # empty name → derived from the current repo's project id (a non-empty slug)
    s = stores.suggest_store_id("")
    assert s and "/" not in s
    # project detection failing → generic default
    import clickmem.projects as projects

    def _boom(*a, **k):
        raise RuntimeError("not a git repo")

    monkeypatch.setattr(projects, "detect_from_cwd", _boom)
    assert stores.suggest_store_id("") == "memory"


def _recording_backend():
    class Rec:
        def __init__(self):
            self.executed, self.queried = [], []

        def query(self, sql):
            self.queried.append(sql)
            return [{"id": "clickmem"}, {"id": "proj"}]

        def execute(self, sql):
            self.executed.append(sql)

    return Rec()


def test_clickhouse_store_list_and_new(home, monkeypatch):
    """The clickhouse branch builds the right SQL and dispatches to the backend
    (validated with a recording stand-in — no server needed for the branch)."""
    _, stores = home
    monkeypatch.setenv("CLICKMEM_BACKEND", "clickhouse")
    monkeypatch.setenv("CLICKMEM_CH_DATABASE", "clickmem")
    import clickmem.backend as backend_mod
    import clickmem.config as config

    config.get_config(refresh=True)
    rec = _recording_backend()
    monkeypatch.setattr(backend_mod, "get_backend", lambda *a, **k: rec)

    listing = stores.store_list()
    assert listing["backend"] == "clickhouse"
    assert "system.tables" in rec.queried[0] and "memories" in rec.queried[0]
    assert {s["id"] for s in listing["stores"]} == {"clickmem", "proj"}

    res = stores.store_new(store_id="mydb")
    assert res["ok"] and res["id"] == "mydb"
    assert rec.executed == ["CREATE DATABASE IF NOT EXISTS mydb"]
    assert stores.active_store_id("clickhouse") == "mydb"


@durable_only
def test_catalog_upsert_over_corrupt_and_giveup(home, monkeypatch):
    _, stores = home
    from chdb.durable.backends import make_backend

    url = "local:" + tempfile.mkdtemp(prefix="clickmem-cg-")
    make_backend(url, "_catalog").put(stores._CATALOG_KEY, b"garbage{ not json")
    # upsert over a corrupt existing file: parse fails, entry map is reset, write succeeds
    stores.catalog_upsert(url, "a", {"v": 1})
    assert stores.catalog_read(url)["stores"]["a"] == {"v": 1}

    # give-up path: every conditional write is refused → 5 retries, then drop
    real = make_backend(url, "_catalog")

    class AlwaysFail:
        def __getattr__(self, n):
            return getattr(real, n)

        def replace_if_match(self, *a, **k):
            return None

    monkeypatch.setattr(stores, "_catalog_backend", lambda u: AlwaysFail())
    stores.catalog_upsert(url, "b", {"v": 2})  # must not raise
    assert "b" not in stores.catalog_read(url)["stores"]  # nothing landed


@durable_only
def test_catalog_cold_create_loses_race(home, monkeypatch):
    """Two processes cold-create the catalog at once: our put_if_absent is
    refused, so we fall through to the update path and both entries survive."""
    _, stores = home
    from chdb.durable.backends import make_backend

    url = "local:" + tempfile.mkdtemp(prefix="clickmem-coldrace-")
    real = make_backend(url, "_catalog")

    class RaceCreate:
        def __getattr__(self, n):
            return getattr(real, n)

        def put_if_absent(self, key, data):
            # a competing writer creates the file first → our create is refused
            real.put_if_absent(
                key, json.dumps({"version": 1, "stores": {"other": {"v": 0}}}).encode())
            return None

    monkeypatch.setattr(stores, "_catalog_backend", lambda u: RaceCreate())
    stores.catalog_upsert(url, "a", {"v": 1})  # cold create refused → retry as update
    final = stores.catalog_read(url)["stores"]
    assert final["a"] == {"v": 1} and final["other"] == {"v": 0}


def test_durable_ops_error_without_url(home, monkeypatch):
    _, stores = home
    monkeypatch.setenv("CLICKMEM_BACKEND", "durable")
    monkeypatch.delenv("CLICKMEM_DURABLE_URL", raising=False)
    import clickmem.config as config

    config.get_config(refresh=True)
    assert "error" in stores.store_list()
    assert "error" in stores.store_new("x")


@durable_only
def test_catalog_read_tolerates_corrupt_json(home):
    _, stores = home
    url = "local:" + tempfile.mkdtemp(prefix="clickmem-corrupt-")
    stores._catalog_backend(url).put(stores._CATALOG_KEY, b"{ not valid json")
    data = stores.catalog_read(url)  # must not raise
    assert data == {"version": 1, "stores": {}}


@durable_only
def test_catalog_retry_on_stale_etag(home, monkeypatch):
    """A concurrent update to a different entry invalidates our etag; the
    read-modify-write retries and both entries survive (disjoint merge)."""
    _, stores = home
    url = "local:" + tempfile.mkdtemp(prefix="clickmem-race-")
    real = stores._catalog_backend(url)
    stores.catalog_upsert(url, "a", {"v": 1})  # seed the file

    class RaceOnce:
        """Delegate to the real backend, but on the first replace_if_match land
        a competing write for a *different* entry first, so our etag is stale."""
        def __init__(self, inner):
            self._inner = inner
            self._raced = False

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def replace_if_match(self, key, data, etag):
            if not self._raced:
                self._raced = True
                # competing writer commits entry "b" under the current etag
                cur, cur_etag = self._inner.get_with_etag(key)
                doc = json.loads(cur)
                doc["stores"]["b"] = {"v": 2}
                self._inner.replace_if_match(key, json.dumps(doc).encode(), cur_etag)
            return self._inner.replace_if_match(key, data, etag)  # our etag now stale → None → retry

    monkeypatch.setattr(stores, "_catalog_backend", lambda u: RaceOnce(real))
    stores.catalog_upsert(url, "a", {"v": 9})  # first attempt fenced, retry succeeds

    final = stores.catalog_read(url)["stores"]
    assert final["a"] == {"v": 9}          # our update landed after retry
    assert final["b"] == {"v": 2}          # the competing write was not clobbered


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
