from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from libs.core.config import env_str
from libs.core.artifacts import persist_artifact


_DB_LOCK = threading.RLock()
_DB_CONN: sqlite3.Connection | None = None


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> str:
    path = env_str("APP_DB_PATH", "/tmp/aiapi_tool.db")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def get_connection() -> sqlite3.Connection:
    global _DB_CONN
    with _DB_LOCK:
        if _DB_CONN is None:
            _DB_CONN = sqlite3.connect(get_db_path(), check_same_thread=False)
            _DB_CONN.row_factory = sqlite3.Row
            _DB_CONN.execute("PRAGMA journal_mode=WAL;")
            _DB_CONN.execute("PRAGMA synchronous=NORMAL;")
            init_schema(_DB_CONN)
        return _DB_CONN


@contextmanager
def db_cursor() -> Iterator[sqlite3.Cursor]:
    conn = get_connection()
    with _DB_LOCK:
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def init_schema(conn: sqlite3.Connection | None = None):
    conn = conn or get_connection()
    cursor = conn.cursor()
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS task_records (
            store_name TEXT NOT NULL,
            task_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (store_name, task_id)
        );

        CREATE TABLE IF NOT EXISTS task_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_name TEXT NOT NULL,
            task_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mail_accounts (
            account_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            address TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        );

        CREATE TABLE IF NOT EXISTS proxy_leases (
            proxy_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            released_at TEXT
        );

        CREATE TABLE IF NOT EXISTS session_tokens (
            session_id TEXT PRIMARY KEY,
            site TEXT NOT NULL,
            identity_subject TEXT,
            identity_user_id TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS service_results (
            result_id TEXT PRIMARY KEY,
            result_type TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            site TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY,
            task_store_name TEXT NOT NULL,
            task_id TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            artifact_name TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );


        CREATE TABLE IF NOT EXISTS worker_heartbeats (
            service_name TEXT NOT NULL,
            worker_name TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (service_name, worker_name)
        );


        CREATE TABLE IF NOT EXISTS callback_events (
            event_id TEXT PRIMARY KEY,
            service_name TEXT NOT NULL,
            task_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            delivery_status TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            last_error TEXT,
            delivered_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );


        CREATE TABLE IF NOT EXISTS mail_provider_settings (
            provider_name TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS proxy_pools (
            pool_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS proxy_pool_entries (
            proxy_entry_id TEXT PRIMARY KEY,
            pool_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    cursor.close()


class SQLiteTaskStore:
    def __init__(self, store_name: str):
        self.store_name = store_name
        get_connection()

    def create_task(self, task_id: str, payload: dict[str, Any]):
        now = utcnow_iso()
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO task_records (store_name, task_id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.store_name, task_id, json.dumps(payload), now, now),
            )

    def update_task(self, task_id: str, **fields: Any):
        payload = self.get_task(task_id)
        if not payload:
            raise KeyError(task_id)
        payload.update(fields)
        now = utcnow_iso()
        with db_cursor() as cur:
            cur.execute(
                """
                UPDATE task_records
                SET payload_json = ?, updated_at = ?
                WHERE store_name = ? AND task_id = ?
                """,
                (json.dumps(payload), now, self.store_name, task_id),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with db_cursor() as cur:
            cur.execute(
                "SELECT payload_json FROM task_records WHERE store_name = ? AND task_id = ?",
                (self.store_name, task_id),
            )
            row = cur.fetchone()
            return json.loads(row["payload_json"]) if row else None

    def list_tasks(
        self,
        *,
        status: str | None = None,
        state: str | None = None,
        site: str | None = None,
        project_id: str | None = None,
        include_all: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json FROM task_records WHERE store_name = ? ORDER BY updated_at DESC", (self.store_name,))
            rows = [json.loads(row["payload_json"]) for row in cur.fetchall()]
        if not include_all:
            rows = [row for row in rows if project_id and row.get("project_id") == project_id]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if state:
            rows = [row for row in rows if row.get("state") == state]
        if site:
            rows = [row for row in rows if row.get("site") == site]
        return rows[:limit]

    def add_event(self, task_id: str, event: dict[str, Any]):
        now = utcnow_iso()
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO task_events (store_name, task_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (self.store_name, task_id, json.dumps(event), now),
            )

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        with db_cursor() as cur:
            cur.execute(
                "SELECT payload_json FROM task_events WHERE store_name = ? AND task_id = ? ORDER BY event_id ASC",
                (self.store_name, task_id),
            )
            rows = cur.fetchall()
            return [json.loads(row["payload_json"]) for row in rows]

    def set_artifacts(self, task_id: str, artifacts: list[dict[str, Any]]):
        task = self.get_task(task_id) or {}
        artifact_store = SQLiteArtifactStore(self.store_name)
        stored_artifacts = artifact_store.replace_for_task(task_id, artifacts, project_id=task.get("project_id"))
        self.update_task(task_id, artifacts=stored_artifacts)

    def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        payload = self.get_task(task_id)
        return list(payload.get("artifacts", [])) if payload else []


    def claim_next_task(self, worker_name: str) -> dict[str, Any] | None:
        conn = get_connection()
        with _DB_LOCK:
            cur = conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                cur.execute(
                    "SELECT task_id, payload_json FROM task_records WHERE store_name = ? ORDER BY created_at ASC",
                    (self.store_name,),
                )
                rows = cur.fetchall()
                now = utcnow_iso()
                for row in rows:
                    payload = json.loads(row["payload_json"])
                    if payload.get("status") != "queued":
                        continue
                    if payload.get("cancel_requested"):
                        continue
                    payload["status"] = "running"
                    payload["state"] = "worker_claimed"
                    payload["worker_name"] = worker_name
                    payload["updated_at"] = now
                    cur.execute(
                        """
                        UPDATE task_records
                        SET payload_json = ?, updated_at = ?
                        WHERE store_name = ? AND task_id = ?
                        """,
                        (json.dumps(payload), now, self.store_name, row["task_id"]),
                    )
                    conn.commit()
                    return payload
                conn.commit()
                return None
            except sqlite3.OperationalError as exc:
                conn.rollback()
                if "locked" in str(exc).lower():
                    return None
                raise
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()


class SQLiteMailAccountStore:
    def __init__(self):
        get_connection()

    def save(self, account_payload: dict[str, Any]):
        payload = dict(account_payload)
        payload.setdefault("status", "active")
        now = utcnow_iso()
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO mail_accounts (account_id, provider, address, payload_json, status, created_at, updated_at, deleted_at)
                VALUES (?, ?, ?, ?, ?,
                    COALESCE((SELECT created_at FROM mail_accounts WHERE account_id = ?), ?),
                    ?,
                    NULL)
                """,
                (
                    payload["account_id"],
                    payload["provider"],
                    payload["address"],
                    json.dumps(payload),
                    payload.get("status", "active"),
                    payload["account_id"],
                    now,
                    now,
                ),
            )

    def get(self, account_id: str) -> dict[str, Any] | None:
        with db_cursor() as cur:
            cur.execute(
                "SELECT payload_json FROM mail_accounts WHERE account_id = ? AND deleted_at IS NULL",
                (account_id,),
            )
            row = cur.fetchone()
            return json.loads(row["payload_json"]) if row else None

    def mark_deleted(self, account_id: str):
        now = utcnow_iso()
        with db_cursor() as cur:
            cur.execute(
                "UPDATE mail_accounts SET deleted_at = ?, status = ?, updated_at = ? WHERE account_id = ?",
                (now, "deleted", now, account_id),
            )

    def list(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
        include_all: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json FROM mail_accounts WHERE deleted_at IS NULL ORDER BY updated_at DESC")
            rows = [json.loads(row["payload_json"]) for row in cur.fetchall()]
        if not include_all:
            rows = [row for row in rows if project_id and row.get("project_id") == project_id]
        if provider:
            rows = [row for row in rows if row.get("provider") == provider]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows[:limit]


class SQLiteProxyLeaseStore:
    def __init__(self):
        get_connection()

    def save(self, lease_payload: dict[str, Any], status: str = "leased"):
        payload = dict(lease_payload)
        payload["status"] = status
        payload["released_at"] = utcnow_iso() if status == "released" else None
        now = utcnow_iso()
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO proxy_leases (proxy_id, provider, payload_json, status, created_at, updated_at, released_at)
                VALUES (?, ?, ?, ?,
                    COALESCE((SELECT created_at FROM proxy_leases WHERE proxy_id = ?), ?),
                    ?,
                    CASE WHEN ? = 'released' THEN ? ELSE NULL END)
                """,
                (
                    payload["proxy_id"],
                    payload["provider"],
                    json.dumps(payload),
                    status,
                    payload["proxy_id"],
                    now,
                    now,
                    status,
                    now,
                ),
            )

    def get(self, proxy_id: str) -> dict[str, Any] | None:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json FROM proxy_leases WHERE proxy_id = ?", (proxy_id,))
            row = cur.fetchone()
            return json.loads(row["payload_json"]) if row else None

    def mark_released(self, proxy_id: str):
        payload = self.get(proxy_id)
        if not payload:
            return
        self.save(payload, status="released")

    def list(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
        include_all: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json FROM proxy_leases ORDER BY updated_at DESC")
            rows = [json.loads(row["payload_json"]) for row in cur.fetchall()]
        if not include_all:
            rows = [row for row in rows if project_id and row.get("project_id") == project_id]
        if provider:
            rows = [row for row in rows if row.get("provider") == provider]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows[:limit]


class SQLiteSessionStore:
    def __init__(self):
        get_connection()

    def save(
        self,
        session_id: str,
        site: str,
        payload: dict[str, Any],
        identity_subject: str | None = None,
        identity_user_id: str | None = None,
        project_id: str | None = None,
    ):
        stored_payload = dict(payload)
        if project_id:
            stored_payload["project_id"] = project_id
        now = utcnow_iso()
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO session_tokens (session_id, site, identity_subject, identity_user_id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM session_tokens WHERE session_id = ?), ?), ?)
                """,
                (
                    session_id,
                    site,
                    identity_subject,
                    identity_user_id,
                    json.dumps(stored_payload),
                    session_id,
                    now,
                    now,
                ),
            )

    def get(self, session_id: str) -> dict[str, Any] | None:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json FROM session_tokens WHERE session_id = ?", (session_id,))
            row = cur.fetchone()
            return json.loads(row["payload_json"]) if row else None

    def list(
        self,
        *,
        site: str | None = None,
        identity_subject: str | None = None,
        project_id: str | None = None,
        include_all: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json, site, identity_subject FROM session_tokens ORDER BY updated_at DESC")
            rows = [json.loads(row["payload_json"]) for row in cur.fetchall()]
        if not include_all:
            rows = [row for row in rows if project_id and row.get("project_id") == project_id]
        if site:
            rows = [row for row in rows if row.get("site") == site]
        if identity_subject:
            rows = [row for row in rows if row.get("identity", {}).get("external_subject") == identity_subject]
        return rows[:limit]


class SQLiteArtifactStore:
    def __init__(self, store_name: str):
        self.store_name = store_name
        get_connection()

    def replace_for_task(self, task_id: str, artifacts: list[dict[str, Any]], project_id: str | None = None):
        now = utcnow_iso()
        stored: list[dict[str, Any]] = []
        with db_cursor() as cur:
            cur.execute(
                "DELETE FROM artifacts WHERE task_store_name = ? AND task_id = ?",
                (self.store_name, task_id),
            )
            for index, artifact in enumerate(artifacts):
                stored_artifact = persist_artifact(self.store_name, task_id, artifact, index)
                if project_id:
                    stored_artifact["project_id"] = project_id
                artifact_id = stored_artifact["artifact_id"]
                cur.execute(
                    """
                    INSERT INTO artifacts (artifact_id, task_store_name, task_id, artifact_type, artifact_name, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        self.store_name,
                        task_id,
                        stored_artifact.get("type", "unknown"),
                        stored_artifact.get("name", artifact_id),
                        json.dumps(stored_artifact),
                        now,
                        now,
                    ),
                )
                stored.append(stored_artifact)
        return stored

    def list_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with db_cursor() as cur:
            cur.execute(
                "SELECT payload_json FROM artifacts WHERE task_store_name = ? AND task_id = ? ORDER BY artifact_id ASC",
                (self.store_name, task_id),
            )
            rows = cur.fetchall()
            return [json.loads(row["payload_json"]) for row in rows]

    def get_by_name(self, task_id: str, artifact_name: str) -> dict[str, Any] | None:
        with db_cursor() as cur:
            cur.execute(
                "SELECT payload_json FROM artifacts WHERE task_store_name = ? AND task_id = ? AND artifact_name = ? LIMIT 1",
                (self.store_name, task_id, artifact_name),
            )
            row = cur.fetchone()
            return json.loads(row["payload_json"]) if row else None


class SQLiteResultStore:
    def __init__(self):
        get_connection()

    def save(self, result_type: str, owner_id: str, site: str, payload: dict[str, Any], project_id: str | None = None):
        stored_payload = dict(payload)
        if project_id:
            stored_payload["project_id"] = project_id
        now = utcnow_iso()
        result_id = f"{result_type}:{owner_id}"
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO service_results (result_id, result_type, owner_id, site, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM service_results WHERE result_id = ?), ?), ?)
                """,
                (result_id, result_type, owner_id, site, json.dumps(stored_payload), result_id, now, now),
            )

    def get(self, result_type: str, owner_id: str) -> dict[str, Any] | None:
        result_id = f"{result_type}:{owner_id}"
        with db_cursor() as cur:
            cur.execute("SELECT payload_json FROM service_results WHERE result_id = ?", (result_id,))
            row = cur.fetchone()
            return json.loads(row["payload_json"]) if row else None

    def list(
        self,
        *,
        result_type: str,
        site: str | None = None,
        project_id: str | None = None,
        include_all: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json, site FROM service_results WHERE result_type = ? ORDER BY updated_at DESC", (result_type,))
            rows = [json.loads(row["payload_json"]) for row in cur.fetchall()]
        if not include_all:
            rows = [row for row in rows if project_id and row.get("project_id") == project_id]
        if site:
            rows = [row for row in rows if row.get("site") == site]
        return rows[:limit]


class SQLiteWorkerHeartbeatStore:
    def __init__(self):
        get_connection()

    def touch(self, service_name: str, worker_name: str, payload: dict[str, Any] | None = None):
        now = utcnow_iso()
        stored_payload = dict(payload or {})
        stored_payload["service_name"] = service_name
        stored_payload["worker_name"] = worker_name
        stored_payload["updated_at"] = now
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO worker_heartbeats (service_name, worker_name, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (service_name, worker_name, json.dumps(stored_payload), now),
            )
        return stored_payload

    def get(self, service_name: str, worker_name: str) -> dict[str, Any] | None:
        with db_cursor() as cur:
            cur.execute(
                "SELECT payload_json FROM worker_heartbeats WHERE service_name = ? AND worker_name = ?",
                (service_name, worker_name),
            )
            row = cur.fetchone()
            return json.loads(row["payload_json"]) if row else None

    def list(self, *, service_name: str | None = None) -> list[dict[str, Any]]:
        with db_cursor() as cur:
            if service_name:
                cur.execute(
                    "SELECT payload_json FROM worker_heartbeats WHERE service_name = ? ORDER BY updated_at DESC",
                    (service_name,),
                )
            else:
                cur.execute("SELECT payload_json FROM worker_heartbeats ORDER BY updated_at DESC")
            rows = cur.fetchall()
            return [json.loads(row["payload_json"]) for row in rows]


class SQLiteCallbackEventStore:
    def __init__(self):
        get_connection()

    def create_or_get(self, event_id: str, service_name: str, task_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.get(event_id)
        if existing:
            return existing
        now = utcnow_iso()
        record = {
            "event_id": event_id,
            "service_name": service_name,
            "task_id": task_id,
            "event_type": event_type,
            "payload": payload,
            "delivery_status": "pending",
            "attempts": 0,
            "last_error": None,
            "delivered_at": None,
            "created_at": now,
            "updated_at": now,
        }
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO callback_events (event_id, service_name, task_id, event_type, payload_json, delivery_status, attempts, last_error, delivered_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    service_name,
                    task_id,
                    event_type,
                    json.dumps(record),
                    record["delivery_status"],
                    record["attempts"],
                    record["last_error"],
                    record["delivered_at"],
                    now,
                    now,
                ),
            )
        return self.get(event_id) or record

    def _save(self, record: dict[str, Any]):
        now = utcnow_iso()
        record["updated_at"] = now
        with db_cursor() as cur:
            cur.execute(
                """
                UPDATE callback_events
                SET payload_json = ?, delivery_status = ?, attempts = ?, last_error = ?, delivered_at = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (
                    json.dumps(record),
                    record.get("delivery_status", "pending"),
                    int(record.get("attempts", 0)),
                    record.get("last_error"),
                    record.get("delivered_at"),
                    now,
                    record["event_id"],
                ),
            )

    def get(self, event_id: str) -> dict[str, Any] | None:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json FROM callback_events WHERE event_id = ?", (event_id,))
            row = cur.fetchone()
            return json.loads(row["payload_json"]) if row else None

    def claim(self, event_id: str) -> dict[str, Any] | None:
        conn = get_connection()
        with _DB_LOCK:
            cur = conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                cur.execute("SELECT payload_json FROM callback_events WHERE event_id = ?", (event_id,))
                row = cur.fetchone()
                if not row:
                    conn.commit()
                    return None
                record = json.loads(row["payload_json"])
                if record.get("delivery_status") in {"delivered", "in_progress"}:
                    conn.commit()
                    return None
                record["delivery_status"] = "in_progress"
                now = utcnow_iso()
                record["updated_at"] = now
                cur.execute(
                    """
                    UPDATE callback_events
                    SET payload_json = ?, delivery_status = ?, updated_at = ?
                    WHERE event_id = ?
                    """,
                    (json.dumps(record), record["delivery_status"], now, event_id),
                )
                conn.commit()
                return record
            except sqlite3.OperationalError as exc:
                conn.rollback()
                if "locked" in str(exc).lower():
                    return None
                raise
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()

    def mark_attempt_failed(self, event_id: str, attempts: int, error: str):
        record = self.get(event_id)
        if not record:
            return
        record["attempts"] = attempts
        record["last_error"] = error
        record["delivery_status"] = "failed"
        self._save(record)

    def mark_delivered(self, event_id: str, attempts: int):
        record = self.get(event_id)
        if not record:
            return
        record["attempts"] = attempts
        record["last_error"] = None
        record["delivery_status"] = "delivered"
        record["delivered_at"] = utcnow_iso()
        self._save(record)

    def list(self, *, service_name: str | None = None, delivery_status: str | None = None) -> list[dict[str, Any]]:
        with db_cursor() as cur:
            if service_name:
                cur.execute(
                    "SELECT payload_json FROM callback_events WHERE service_name = ? ORDER BY updated_at DESC",
                    (service_name,),
                )
            else:
                cur.execute("SELECT payload_json FROM callback_events ORDER BY updated_at DESC")
            rows = [json.loads(row["payload_json"]) for row in cur.fetchall()]
        if delivery_status:
            rows = [row for row in rows if row.get("delivery_status") == delivery_status]
        return rows



class SQLiteMailProviderSettingsStore:
    def __init__(self):
        get_connection()

    def save(self, provider_name: str, payload: dict[str, Any]):
        now = utcnow_iso()
        stored_payload = dict(payload)
        stored_payload["provider_name"] = provider_name
        stored_payload["updated_at"] = now
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO mail_provider_settings (provider_name, payload_json, updated_at)
                VALUES (?, ?, ?)
                """,
                (provider_name, json.dumps(stored_payload), now),
            )
        return stored_payload

    def get(self, provider_name: str) -> dict[str, Any] | None:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json FROM mail_provider_settings WHERE provider_name = ?", (provider_name,))
            row = cur.fetchone()
            return json.loads(row["payload_json"]) if row else None

    def list(self) -> list[dict[str, Any]]:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json FROM mail_provider_settings ORDER BY provider_name ASC")
            rows = cur.fetchall()
            return [json.loads(row["payload_json"]) for row in rows]

    def ensure_defaults(self, providers: list[str]):
        existing = {item.get("provider_name") for item in self.list()}
        for provider_name in providers:
            if provider_name in existing:
                continue
            self.save(provider_name, {"provider_name": provider_name, "enabled": True})


class SQLiteProxyPoolStore:
    def __init__(self):
        get_connection()

    def save_pool(self, payload: dict[str, Any]):
        now = utcnow_iso()
        stored_payload = dict(payload)
        stored_payload.setdefault("status", "enabled")
        pool_id = stored_payload["pool_id"]
        stored_payload["updated_at"] = now
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO proxy_pools (pool_id, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, COALESCE((SELECT created_at FROM proxy_pools WHERE pool_id = ?), ?), ?)
                """,
                (pool_id, stored_payload["status"], json.dumps(stored_payload), pool_id, now, now),
            )
        return stored_payload

    def get_pool(self, pool_id: str) -> dict[str, Any] | None:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json FROM proxy_pools WHERE pool_id = ?", (pool_id,))
            row = cur.fetchone()
            return json.loads(row["payload_json"]) if row else None

    def list_pools(self) -> list[dict[str, Any]]:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json FROM proxy_pools ORDER BY updated_at DESC")
            rows = cur.fetchall()
            return [json.loads(row["payload_json"]) for row in rows]

    def delete_pool(self, pool_id: str):
        with db_cursor() as cur:
            cur.execute("DELETE FROM proxy_pool_entries WHERE pool_id = ?", (pool_id,))
            cur.execute("DELETE FROM proxy_pools WHERE pool_id = ?", (pool_id,))

    def save_entry(self, payload: dict[str, Any]):
        now = utcnow_iso()
        stored_payload = dict(payload)
        stored_payload.setdefault("status", "enabled")
        entry_id = stored_payload["proxy_entry_id"]
        stored_payload["updated_at"] = now
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO proxy_pool_entries (proxy_entry_id, pool_id, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM proxy_pool_entries WHERE proxy_entry_id = ?), ?), ?)
                """,
                (
                    entry_id,
                    stored_payload["pool_id"],
                    stored_payload["status"],
                    json.dumps(stored_payload),
                    entry_id,
                    now,
                    now,
                ),
            )
        return stored_payload

    def get_entry(self, proxy_entry_id: str) -> dict[str, Any] | None:
        with db_cursor() as cur:
            cur.execute("SELECT payload_json FROM proxy_pool_entries WHERE proxy_entry_id = ?", (proxy_entry_id,))
            row = cur.fetchone()
            return json.loads(row["payload_json"]) if row else None

    def list_entries(self, *, pool_id: str | None = None) -> list[dict[str, Any]]:
        with db_cursor() as cur:
            if pool_id:
                cur.execute("SELECT payload_json FROM proxy_pool_entries WHERE pool_id = ? ORDER BY updated_at DESC", (pool_id,))
            else:
                cur.execute("SELECT payload_json FROM proxy_pool_entries ORDER BY updated_at DESC")
            rows = cur.fetchall()
            return [json.loads(row["payload_json"]) for row in rows]

    def delete_entry(self, proxy_entry_id: str):
        with db_cursor() as cur:
            cur.execute("DELETE FROM proxy_pool_entries WHERE proxy_entry_id = ?", (proxy_entry_id,))
