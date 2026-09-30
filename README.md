# Farmtrace
# Mbeu Trace — Offline-First Farm Traceability & Compliance Platform

> **Decentralized, tamper-evident supply chain tracking for smallholder farmers.**  
> Features offline QR generation, EUDR deforestation-free plot verification, Directed Acyclic Graph (DAG) batch splitting, cryptographic SHA-256 Merkle audit chaining, and privacy-preserving PII masking.

"Mbeu" is the Shona word for seed / grain.

---





---

## 1. Advanced Features Built Into This Release

###  1. EUDR Deforestation Compliance & Geolocation
* **European Union Deforestation Regulation (EUDR)** mandates that agricultural imports into the EU must prove they were not grown on land deforested after Dec 31, 2020.
* **Implementation**: The `farms` table stores closed **GeoJSON boundary polygons** (`polygon_geojson`) and an `eudr_compliant` verification flag.
* **Interactive Leaflet Map**: The public verification view renders OpenStreetMap satellite boundaries showing the farm plot and legal cadastral coordinates.

###  2. Batch Splitting & Merging (Lineage DAG)
* Real agricultural supply chains are not 1:1 flat tables. Farmers pool crops into bulk washing station lots, and bulk lots are split into export grades (Grade A, Grade B, Peaberry).
* **Implementation**: A **Directed Acyclic Graph (DAG)** modeled in the `batch_lineage` table (`parent_batch_id`, `child_batch_id`, `quantity_kg`, `transfer_notes`).
* **Automated Balance Tracking**: Splitting a batch creates a child lot with its own QR code and automatically deducts the allocated weight from the parent lot balance.

###  3. Tamper-Proof Cryptographic Audit Trail (SHA-256)
* Guarantees supply chain integrity without expensive Web3 gas fees or internet dependencies.
* **Implementation**: Each state transition in `sync_log` is cryptographically chained to its predecessor:
  $$\text{event\_hash} = \text{SHA-256}(\text{previous\_hash} : \text{table} : \text{record\_id} : \text{operation} : \text{timestamp} : \text{payload})$$
* **Audit API**: Mathematical integrity can be validated in real time via `GET /api/audit-status` or from within the Python engine via `database.verify_audit_chain()`.

###  4. PII Privacy Protection (Phone Masking)
* To prevent automated web scrapers and unauthorized brokers from harvesting smallholder farmers' personal phone numbers from public QR codes, the public certificate automatically sanitizes contact data (`+263 77 *** **77`) via `mask_phone()`.

###  5. Offline Idempotency
* Every sync operation carries a client-generated UUIDv4 `idempotency_key`. If intermittent 2G network drops cause retried HTTP packets, the central ingestion engine deduplicates them safely with zero double-counting.

---

## 2. Project Structure

```
mbeu-trace-farm-traceability/
├── run.bat                 # One-click Windows launch script
├── run.ps1                 # One-click PowerShell launch script
├── README.md               # Root technical overview & documentation
├── .venv/                  # Virtual environment with pre-installed dependencies
└── farm_traceability/      # Core Flask application
    ├── app.py              # Routes, batch splitting, PII masking, auth, APIs
    ├── auth.py             # Authentication & RBAC: scrypt hashing, role decorators
    ├── database.py         # SQLite layer, schema migrations, SHA-256 cryptographic engine
    ├── sync.py             # Offline-first sync engine (outbox pattern with crypto payload)
    ├── schema.sql          # Complete relational schema (farms, batches, batch_lineage, sync_log)
    ├── requirements.txt    # Python dependencies (Flask, Flask-Login, qrcode, Pillow, requests)
    ├── static/             # Design system, photography, and generated QR PNGs
    ├── templates/          # Jinja2 templates (dashboard, batch detail, verify certificate...)
    └── instance/
        └── traceability.db # Local SQLite database file (auto-migrated on startup)
```

---

## 3. API Reference

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/verify/<batch_code>` | `GET` | Public | Public certificate rendered upon scanning product QR code |
| `/verify/<batch_code>` | `POST` | Exporter / Admin | Records official export inspection decision |
| `/batches/<id>/split` | `POST` | Agent / Admin | Splits batch into child lot, recording DAG lineage |
| `/api/sync-status` | `GET` | Public | Returns count of pending offline records waiting to sync |
| `/api/audit-status` | `GET` | Public | Returns mathematical integrity status of SHA-256 chain |

---

## 4. Database Schema Overview

```
farmers ──< farms (EUDR Polygons) ──< batches >── crops
                                         │  ▲
                                         │  └── batch_lineage (DAG splitting & merging)
                                         ├──< inputs        (seed, fertilizer, pesticide...)
                                         ├──< processes     (planting, weeding, harvesting, drying...)
                                         ├──< outputs       (graded/packaged quantities, post-harvest)
                                         └──< verifications >── exporters

sync_log   — outbox & cryptographic SHA-256 hash chain (tamper-evident Merkle ledger)
users      — login accounts (admin / agent / farmer / exporter roles)
```

---

4. **The Live QR Scan "Magic Moment"**:
   - Open the verification certificate by scanning or clicking the QR code.
   - Show the **interactive Leaflet map** rendering the farm coordinates.
   - Highlight the **`🌲 EUDR Verified Deforestation-Free`** badge, the **SHA-256 tamper-proof chain seal**, and the **privacy-masked farmer phone number**.
