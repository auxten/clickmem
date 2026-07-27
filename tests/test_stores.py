"""Store selection: local record, id rules, durable catalog.

Runs against a temp HOME so the real ~/.clickmem is never touched.
"""
import importlib
import json
import tempfile
from pathlib import Path

import pytest


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


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
