"""
sync.py
Offline-first synchronization engine.

How it works on a real deployment:
- The Raspberry Pi runs this Flask app locally (no internet needed) — a
  farmer or extension agent registers, records inputs/processes/outputs,
  and prints/scans QR codes entirely offline, writing to the local
  SQLite file.
- Every write is also queued in `sync_log` (see database.insert_and_queue).
- When the Pi gets internet (wifi at a trading centre, a 3G dongle, or a
  periodic trip to town with the SD card / a USB relay), this module's
  `run_sync()` is called — manually via the dashboard "Sync now" button,
  or automatically on a cron/systemd timer (e.g. every 15 minutes).
- `run_sync()` walks pending sync_log rows in order, POSTs each payload to
  the central server's ingestion API, marks it synced on success, and
  keeps it pending (to retry later) on failure — so nothing is lost if
  connectivity drops mid-sync.
- Because this is a demo/offline environment, CENTRAL_SERVER_URL is not
  reachable, so `push_to_server()` is stubbed to simulate success. Point
  CENTRAL_SERVER_URL at a real endpoint (e.g. a Django/FastAPI service on
  a district or national server) to make this live.
"""

import os
import time
import requests
from database import get_db, now

CENTRAL_SERVER_URL = os.environ.get("CENTRAL_SERVER_URL", "https://traceability.example.zw/api/ingest")
SIMULATE = os.environ.get("SYNC_SIMULATE", "1") == "1"  # no real network in this sandbox


def push_to_server(record):
    """
    Send one outbox record to the central server.
    Returns True on success, False on failure (network down, 5xx, etc.)
    """
    if SIMULATE:
        # Simulated: pretend every push succeeds instantly, as it would
        # once this Pi has a working internet connection.
        time.sleep(0.02)
        return True
    try:
        resp = requests.post(CENTRAL_SERVER_URL, json=record, timeout=5)
        return resp.status_code in (200, 201)
    except requests.exceptions.RequestException:
        return False


def run_sync(limit=200):
    """
    Process up to `limit` pending sync_log rows.
    Returns a summary dict: {"synced": n, "failed": n, "remaining": n}
    """
    conn = get_db()
    pending = conn.execute(
        "SELECT * FROM sync_log WHERE sync_status='pending' ORDER BY created_at LIMIT ?",
        (limit,),
    ).fetchall()

    synced, failed = 0, 0
    for row in pending:
        record = {
            "table": row["table_name"],
            "record_id": row["record_id"],
            "operation": row["operation"],
            "payload": row["payload_json"],
            "device_id": row["device_id"],
            "previous_hash": row["previous_hash"],
            "event_hash": row["event_hash"],
            "idempotency_key": row["idempotency_key"],
        }
        ok = push_to_server(record)
        if ok:
            conn.execute(
                "UPDATE sync_log SET sync_status='synced', synced_at=? WHERE sync_id=?",
                (now(), row["sync_id"]),
            )
            # Mark the source record itself as synced too, if it has that column
            table = row["table_name"]
            try:
                conn.execute(
                    f"UPDATE {table} SET synced=1, sync_ts=? WHERE rowid=?",
                    (now(), row["record_id"]),
                )
            except Exception:
                pass  # table without synced/sync_ts columns — fine, sync_log is the source of truth
            synced += 1
        else:
            conn.execute(
                "UPDATE sync_log SET sync_status='pending' WHERE sync_id=?",
                (row["sync_id"],),
            )
            failed += 1

    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) c FROM sync_log WHERE sync_status='pending'"
    ).fetchone()["c"]
    conn.close()
    return {"synced": synced, "failed": failed, "remaining": remaining}
