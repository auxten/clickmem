"""Memory-store selection: list stores, switch, create.

A *store* is one whole memory database (all tables). Which store a process
opens is recorded per user in ``~/.clickmem/stores.json`` — never in the
repository, so different people can keep different memories for the same
project. The object-storage directory (``_catalog/catalog.json``) is only
consulted on the exception paths: a new machine, a lost local record, or an
explicit switch.

Store ids must be flat names (letters, digits, ``.`` ``_`` ``-``); a leading
underscore is reserved (``_catalog`` is the directory itself).
"""

from __future__ import annotations

import json
import logging
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from clickmem.config import get_config

_log = logging.getLogger(__name__)

_STORES_FILE = Path.home() / ".clickmem" / "stores.json"
_LOCAL_STORES_DIR = Path.home() / ".clickmem" / "stores"
_CATALOG_KEY = "catalog.json"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ---------- local record ---------------------------------------------------

def _read_record() -> dict[str, Any]:
    try:
        return json.loads(_STORES_FILE.read_text())
    except (OSError, ValueError):
        return {}


def active_store_id(backend_choice: str) -> Optional[str]:
    """The store id this user last selected for ``backend_choice``, if any."""
    active = _read_record().get("active")
    if isinstance(active, dict):
        value = active.get(backend_choice)
        if isinstance(value, str) and value:
            return value
    return None


def set_active_store(backend_choice: str, store_id: str) -> None:
    record = _read_record()
    record.setdefault("active", {})[backend_choice] = store_id
    _STORES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STORES_FILE.write_text(json.dumps(record, indent=2))


def validate_store_id(store_id: str) -> str:
    if not _ID_RE.match(store_id or ""):
        raise ValueError(
            f"invalid store id {store_id!r}: use letters/digits/._- and do not "
            f"start with '_' (reserved) — e.g. 'acme/foo' becomes 'acme-foo'"
        )
    return store_id


def suggest_store_id(name: str = "") -> str:
    """A readable default id: the given name or the current repo's project id,
    sanitized to the flat-id rules (``acme/foo`` → ``acme-foo``)."""
    if not name:
        try:
            from clickmem.projects import detect_from_cwd

            name = detect_from_cwd().id or ""
        except Exception:  # noqa: BLE001 — fall through to the generic default
            name = ""
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", name).strip("._-") or "memory"
    return slug


# ---------- directory (durable backend only) --------------------------------

def _catalog_backend(url: str):
    # "_catalog" is a reserved prefix in the namespace: real store ids may not
    # start with "_", so it can never collide with a store's own files.
    from chdb.durable.backends import make_backend

    return make_backend(url, "_catalog")


def catalog_read(url: str) -> dict[str, Any]:
    raw = _catalog_backend(url).get(_CATALOG_KEY)
    if not raw:
        return {"version": 1, "stores": {}}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"version": 1, "stores": {}}
    data.setdefault("stores", {})
    return data


def catalog_upsert(url: str, store_id: str, patch: dict[str, Any]) -> None:
    """Merge ``patch`` into this store's directory entry.

    Concurrency: writers touch disjoint entries, so a conditional-write retry
    always merges cleanly. The directory is a navigation hint — the truth is
    each store's head.json — so on persistent contention we just give up.
    """
    backend = _catalog_backend(url)
    for _ in range(5):
        raw, etag = backend.get_with_etag(_CATALOG_KEY)
        if raw is None:
            body = {"version": 1, "stores": {store_id: patch}}
            if backend.put_if_absent(_CATALOG_KEY, json.dumps(body).encode()):
                return
            continue  # created concurrently; retry as an update
        try:
            data = json.loads(raw)
        except ValueError:
            data = {"version": 1, "stores": {}}
        entry = data.setdefault("stores", {}).setdefault(store_id, {})
        entry.update(patch)
        if backend.replace_if_match(_CATALOG_KEY, json.dumps(data).encode(), etag):
            return
        time.sleep(0.05)
    _log.debug("catalog update for %r gave up after contention", store_id)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def catalog_open_patch(embed_dim: int) -> dict[str, Any]:
    cfg = get_config()
    return {
        "last_opened": _utc_now_iso(),
        "last_writer": f"clickmem@{socket.gethostname()}",
        "embedding": {"model": cfg.embedding_model, "dim": embed_dim},
    }


# ---------- store operations ------------------------------------------------

def store_list() -> dict[str, Any]:
    cfg = get_config()
    choice = (cfg.backend or "local").lower()
    active = active_store_id(choice)
    if choice in ("durable", "dao"):
        if not cfg.durable_url:
            return {"backend": "durable", "error": "CLICKMEM_DURABLE_URL is not set"}
        stores = catalog_read(cfg.durable_url).get("stores", {})
        items = [{"id": sid, **(entry or {})} for sid, entry in sorted(stores.items())]
        return {"backend": "durable", "active": active or cfg.durable_id, "stores": items}
    if choice in ("clickhouse", "ch", "cloud"):
        from clickmem.backend import get_backend

        rows = get_backend().query(
            "SELECT database AS id FROM system.tables WHERE name = 'memories' ORDER BY database"
        )
        return {"backend": "clickhouse", "active": active or cfg.ch_database, "stores": rows}
    # local: the legacy data dir is the store named "default"
    items = [{"id": "default", "path": str(cfg.db_path)}]
    if _LOCAL_STORES_DIR.is_dir():
        items += [
            {"id": p.name, "path": str(p)}
            for p in sorted(_LOCAL_STORES_DIR.iterdir())
            if p.is_dir()
        ]
    return {"backend": "local", "active": active or "default", "stores": items}


def store_use(store_id: str) -> dict[str, Any]:
    validate_store_id(store_id)
    choice = (get_config().backend or "local").lower()
    set_active_store(choice, store_id)
    return {
        "ok": True,
        "backend": choice,
        "active": store_id,
        "note": "restart the clickmem service (or the calling process) to apply",
    }


def store_new(name: str = "", store_id: str = "", description: str = "") -> dict[str, Any]:
    cfg = get_config()
    choice = (cfg.backend or "local").lower()
    sid = validate_store_id(store_id or suggest_store_id(name))

    if choice in ("durable", "dao"):
        if not cfg.durable_url:
            return {"backend": "durable", "error": "CLICKMEM_DURABLE_URL is not set"}
        if sid in catalog_read(cfg.durable_url).get("stores", {}):
            return {"backend": "durable", "error": f"store {sid!r} already exists — "
                                                   f"`store use {sid}` to reuse it, or pick another id"}
        from chdb import durable as cd

        obj = cd.Namespace(cfg.durable_url, owner="clickmem", db=cfg.ch_database).open(sid)
        obj.close()  # cold-create only; first real open bootstraps the schema
        catalog_upsert(cfg.durable_url, sid, {
            "description": description,
            "created_at": _utc_now_iso(),
            **catalog_open_patch(cfg.embedding_dim),
        })
    elif choice in ("clickhouse", "ch", "cloud"):
        from clickmem.backend import get_backend

        get_backend().execute(f"CREATE DATABASE IF NOT EXISTS {sid}")
    else:
        (_LOCAL_STORES_DIR / sid).mkdir(parents=True, exist_ok=True)

    set_active_store(choice, sid)
    return {"ok": True, "backend": choice, "id": sid, "description": description,
            "note": "restart the clickmem service (or the calling process) to apply"}
