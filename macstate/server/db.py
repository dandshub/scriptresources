"""SQLite storage. One shared connection guarded by a re-entrant lock; FastAPI
runs sync endpoints in a thread pool, so every access goes through the lock."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id       INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    pw_hash  TEXT NOT NULL,
    role     TEXT NOT NULL DEFAULT 'admin'      -- admin | technician
);

CREATE TABLE IF NOT EXISTS tenants (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE,
    enrollment_key     TEXT NOT NULL UNIQUE,
    onboarding_enabled INTEGER NOT NULL DEFAULT 1,
    notes              TEXT NOT NULL DEFAULT '',
    created_at         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS computers (
    id             INTEGER PRIMARY KEY,
    tenant_id      INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    serial         TEXT NOT NULL DEFAULT '',
    model          TEXT NOT NULL DEFAULT '',
    os_version     TEXT NOT NULL DEFAULT '',
    arch           TEXT NOT NULL DEFAULT '',
    agent_version  TEXT NOT NULL DEFAULT '',
    console_user   TEXT NOT NULL DEFAULT '',
    ip_address     TEXT NOT NULL DEFAULT '',
    token_hash     TEXT NOT NULL UNIQUE,
    inventory_json TEXT NOT NULL DEFAULT '{}',
    inventory_at   INTEGER,
    last_seen      INTEGER,
    onboarded      INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_computers_tenant ON computers(tenant_id);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS computer_tags (
    computer_id INTEGER NOT NULL REFERENCES computers(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (computer_id, tag_id)
);

-- Software library. detection_type: app | pkg | script.
-- installer_type: pkg | dmg | zip | script.
CREATE TABLE IF NOT EXISTS software (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    publisher           TEXT NOT NULL DEFAULT '',
    description         TEXT NOT NULL DEFAULT '',
    detection_type      TEXT NOT NULL DEFAULT 'app',
    detection_value     TEXT NOT NULL DEFAULT '',
    detection_script    TEXT NOT NULL DEFAULT '',
    installer_type      TEXT NOT NULL DEFAULT 'pkg',
    download_url        TEXT NOT NULL DEFAULT '',   -- rolling "latest" URL
    install_script      TEXT NOT NULL DEFAULT '',
    uninstall_script    TEXT NOT NULL DEFAULT '',
    post_install_script TEXT NOT NULL DEFAULT '',
    created_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS software_versions (
    id          INTEGER PRIMARY KEY,
    software_id INTEGER NOT NULL REFERENCES software(id) ON DELETE CASCADE,
    version     TEXT NOT NULL,
    url         TEXT NOT NULL,
    sha256      TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    UNIQUE (software_id, version)
);

-- Maintenance tasks: desired-state configuration scripts.
-- test_script exits 0 when compliant; set_script remediates.
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT NOT NULL DEFAULT '',
    test_script     TEXT NOT NULL DEFAULT '',
    set_script      TEXT NOT NULL DEFAULT '',
    parameters_json TEXT NOT NULL DEFAULT '{}',
    created_at      INTEGER NOT NULL
);

-- item_type: software | task. target_type: all | tenant | tag | computer.
-- desired (software): installed | latest | version | uninstalled | ignore
-- desired (task):     enforce | monitor | ignore
CREATE TABLE IF NOT EXISTS deployments (
    id              INTEGER PRIMARY KEY,
    item_type       TEXT NOT NULL,
    item_id         INTEGER NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       INTEGER,
    desired         TEXT NOT NULL,
    version         TEXT NOT NULL DEFAULT '',
    parameters_json TEXT NOT NULL DEFAULT '{}',
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id   INTEGER,
    time        TEXT NOT NULL,            -- HH:MM, server local time
    days        TEXT NOT NULL DEFAULT '0123456',  -- 0=Mon .. 6=Sun
    mode        TEXT NOT NULL DEFAULT 'apply',    -- apply | detect
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_run    TEXT NOT NULL DEFAULT '',          -- YYYY-MM-DD
    created_at  INTEGER NOT NULL
);

-- kind: manual | scheduled | onboarding | adhoc
-- status: pending | running | succeeded | failed | cancelled
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY,
    computer_id INTEGER NOT NULL REFERENCES computers(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    mode        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    started_at  INTEGER,
    updated_at  INTEGER,
    finished_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_sessions_computer ON sessions(computer_id, status);

CREATE TABLE IF NOT EXISTS session_actions (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    item_type       TEXT NOT NULL,     -- software | task | script
    item_id         INTEGER,
    name            TEXT NOT NULL,
    desired         TEXT NOT NULL,
    target_version  TEXT NOT NULL DEFAULT '',
    spec_json       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    detected_before TEXT NOT NULL DEFAULT '',
    detected_after  TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL DEFAULT '',
    started_at      INTEGER,
    finished_at     INTEGER
);
CREATE INDEX IF NOT EXISTS ix_actions_session ON session_actions(session_id);

CREATE TABLE IF NOT EXISTS session_logs (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    action_id  INTEGER,
    ts         INTEGER NOT NULL,
    line       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_logs_session ON session_logs(session_id);

-- NinjaOne mirror (rebuilt on every sync).
CREATE TABLE IF NOT EXISTS ninja_orgs (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    synced_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ninja_devices (
    id              INTEGER PRIMARY KEY,
    organization_id INTEGER,
    name            TEXT NOT NULL,
    serial          TEXT NOT NULL DEFAULT '',
    node_class      TEXT NOT NULL DEFAULT '',
    os_name         TEXT NOT NULL DEFAULT '',
    offline         INTEGER NOT NULL DEFAULT 0,
    last_contact    INTEGER,
    computer_id     INTEGER,
    synced_at       INTEGER NOT NULL
);

-- Last known state of each deployed item on each computer.
CREATE TABLE IF NOT EXISTS computer_state (
    computer_id INTEGER NOT NULL REFERENCES computers(id) ON DELETE CASCADE,
    item_type   TEXT NOT NULL,
    item_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    detected    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL,
    compliant   INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (computer_id, item_type, item_id)
);
"""


def now() -> int:
    return int(time.time())


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


class DB:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.executescript(SCHEMA)
            self._migrate()

    # Columns added after the first release; ALTER TABLE for existing databases.
    _COLUMNS = {
        "tenants": {"ninja_org_id": "INTEGER"},
        "computers": {"ninja_device_id": "INTEGER"},
    }

    def _migrate(self) -> None:
        for table, cols in self._COLUMNS.items():
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def all(self, sql: str, args: tuple | list = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple | list = ()) -> dict | None:
        with self.lock:
            row = self.conn.execute(sql, args).fetchone()
            return dict(row) if row else None

    def scalar(self, sql: str, args: tuple | list = ()) -> Any:
        with self.lock:
            row = self.conn.execute(sql, args).fetchone()
            return row[0] if row else None

    def run(self, sql: str, args: tuple | list = ()) -> int:
        """Execute a write; returns lastrowid."""
        with self.lock:
            return self.conn.execute(sql, args).lastrowid

    @contextmanager
    def tx(self) -> Iterator["DB"]:
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    # -- settings ------------------------------------------------------------
    def get_setting(self, key: str) -> str | None:
        return self.scalar("SELECT value FROM settings WHERE key=?", (key,))

    def set_setting(self, key: str, value: str) -> None:
        self.run(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


db = DB(config.DB_PATH)
