"""
database.py
Local-first SQLite access layer for the Farm Traceability System.

Design notes:
- SQLite is used because it is a single file, needs no server process,
  and runs comfortably on a Raspberry Pi with no internet connection.
- Every farmer-facing table (farmers, farms, batches, inputs, processes,
  outputs, exporters, verifications) has `synced` + `sync_ts` columns.
  Records are created with synced=0. A background sync job (see sync.py)
  looks for synced=0 rows, pushes them to the central server API when a
  connection is available, and flips them to synced=1.
- `sync_log` is an audit/outbox table: every insert/update writes a
  row with a JSON snapshot and a cryptographically chained SHA-256 hash.
  This provides a tamper-evident Merkle-style audit trail without Web3 gas fees.
- Idempotency keys prevent duplicate operations during offline sync retries.
"""

import sqlite3
import json
import os
import hashlib
import uuid
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "traceability.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
DEVICE_ID = os.environ.get("DEVICE_ID", "raspberrypi-kiosk-01")
GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _run_migrations(conn):
    """Safely apply schema migrations to existing databases without data loss."""
    # Add columns to farms table if not present
    existing_farm_cols = [r["name"] for r in conn.execute("PRAGMA table_info(farms)").fetchall()]
    if "polygon_geojson" not in existing_farm_cols:
        conn.execute("ALTER TABLE farms ADD COLUMN polygon_geojson TEXT")
    if "eudr_compliant" not in existing_farm_cols:
        conn.execute("ALTER TABLE farms ADD COLUMN eudr_compliant INTEGER DEFAULT 1")

    # Add columns to sync_log table if not present
    existing_sync_cols = [r["name"] for r in conn.execute("PRAGMA table_info(sync_log)").fetchall()]
    if "previous_hash" not in existing_sync_cols:
        conn.execute("ALTER TABLE sync_log ADD COLUMN previous_hash TEXT")
    if "event_hash" not in existing_sync_cols:
        conn.execute("ALTER TABLE sync_log ADD COLUMN event_hash TEXT")
    if "idempotency_key" not in existing_sync_cols:
        conn.execute("ALTER TABLE sync_log ADD COLUMN idempotency_key TEXT")

    # Ensure batch_lineage table exists
    conn.execute(
        """CREATE TABLE IF NOT EXISTS batch_lineage (
            lineage_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_batch_id INTEGER NOT NULL,
            child_batch_id  INTEGER NOT NULL,
            quantity_kg     REAL NOT NULL,
            transfer_notes  TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            synced          INTEGER DEFAULT 0,
            sync_ts         TEXT,
            FOREIGN KEY (parent_batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE,
            FOREIGN KEY (child_batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE
        )"""
    )
    conn.commit()


def init_db():
    """Create tables if they don't exist yet and run non-destructive migrations."""
    conn = get_db()
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    _run_migrations(conn)
    conn.commit()
    conn.close()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compute_event_hash(prev_hash, table_name, record_id, operation, payload_json, timestamp):
    """Compute deterministic SHA-256 hash chaining back to previous event."""
    content = f"{prev_hash}:{table_name}:{record_id}:{operation}:{timestamp}:{payload_json}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def queue_sync(conn, table_name, record_id, operation, row_dict, idempotency_key=None):
    """
    Write a cryptographically chained outbox entry so the sync engine knows
    this record needs to travel to the central server next time the device is online.
    """
    idemp_key = idempotency_key or str(uuid.uuid4())

    # Check for duplicate idempotency key (offline replay defense)
    existing = conn.execute(
        "SELECT sync_id FROM sync_log WHERE idempotency_key=?", (idemp_key,)
    ).fetchone()
    if existing:
        return existing["sync_id"]

    # Retrieve predecessor hash
    last_event = conn.execute(
        "SELECT event_hash FROM sync_log WHERE event_hash IS NOT NULL ORDER BY sync_id DESC LIMIT 1"
    ).fetchone()
    prev_hash = last_event["event_hash"] if last_event and last_event["event_hash"] else GENESIS_HASH

    ts = now()
    payload_str = json.dumps(row_dict, default=str)
    event_hash = compute_event_hash(prev_hash, table_name, record_id, operation, payload_str, ts)

    cur = conn.execute(
        """INSERT INTO sync_log (table_name, record_id, operation, payload_json,
                                   device_id, previous_hash, event_hash,
                                   idempotency_key, created_at, sync_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (table_name, record_id, operation, payload_str, DEVICE_ID, prev_hash, event_hash, idemp_key, ts),
    )
    return cur.lastrowid


def insert_and_queue(table, fields: dict, idempotency_key=None):
    """
    Generic helper: insert a row into `table` with the given fields,
    stamp it unsynced, and drop it into the outbox in the same transaction
    so local writes, cryptographic hash chains, and the sync queue never drift apart.
    """
    conn = get_db()
    try:
        cols = list(fields.keys())
        placeholders = ",".join("?" for _ in cols)
        col_list = ",".join(cols)
        cur = conn.execute(
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
            [fields[c] for c in cols],
        )
        record_id = cur.lastrowid
        row = conn.execute(f"SELECT * FROM {table} WHERE rowid = ?", (record_id,)).fetchone()
        queue_sync(conn, table, record_id, "insert", dict(row), idempotency_key=idempotency_key)
        conn.commit()
        return record_id
    finally:
        conn.close()


def verify_audit_chain():
    """
    Verify the cryptographic integrity of the entire offline/online sync chain.
    Returns audit status dictionary.
    """
    conn = get_db()
    logs = conn.execute(
        "SELECT sync_id, table_name, record_id, operation, payload_json, previous_hash, event_hash, created_at FROM sync_log ORDER BY sync_id ASC"
    ).fetchall()
    conn.close()

    if not logs:
        return {"valid": True, "count": 0, "latest_hash": GENESIS_HASH}

    expected_prev = GENESIS_HASH
    for row in logs:
        # If legacy rows without hash exist, chain starts from their creation
        if not row["event_hash"]:
            continue
        if row["previous_hash"] and row["previous_hash"] != expected_prev and expected_prev != GENESIS_HASH:
            return {
                "valid": False,
                "broken_at_id": row["sync_id"],
                "reason": "Hash mismatch in chain link",
            }
        recomputed = compute_event_hash(
            row["previous_hash"],
            row["table_name"],
            row["record_id"],
            row["operation"],
            row["payload_json"],
            row["created_at"],
        )
        if recomputed != row["event_hash"]:
            return {
                "valid": False,
                "broken_at_id": row["sync_id"],
                "reason": "Payload tampering detected",
            }
        expected_prev = row["event_hash"]

    return {
        "valid": True,
        "count": len(logs),
        "latest_hash": expected_prev,
    }


def pending_sync_count():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) c FROM sync_log WHERE sync_status='pending'").fetchone()["c"]
    conn.close()
    return n
