# Mbeu Trace — Offline-First Farm Traceability & Compliance Platform

> **Decentralized, tamper-evident supply chain tracking for smallholder farmers.**  
> Features offline QR generation, EUDR deforestation-free plot verification, Directed Acyclic Graph (DAG) batch splitting, cryptographic SHA-256 Merkle audit chaining, and privacy-preserving PII masking.

"Mbeu" is the Shona word for seed / grain.

---

## 1. Why This Architecture?

Most smallholder farming communities in Zimbabwe and sub-Saharan Africa operate with intermittent or non-existent internet connectivity. Traditional cloud-first ERPs crash or lose data in the field.

**Mbeu Trace is designed from the ground up to be 100% functional offline:**

- **Local SQLite Engine**: Runs on a single self-contained database file on a laptop, tablet, or Raspberry Pi kiosk with zero external server dependencies.
- **Embedded Web UI**: Served via Flask locally on the device screen or over a local Wi-Fi hotspot to field tablets.
- **Instant Edge QR Generation**: Generates high-density vector QR code PNGs locally at the moment of harvest or packaging.
- **Outbox Sync Pattern**: Every write is committed to local tables and queued in `sync_log`. When an agent travels to a town with Wi-Fi or 3G, queued changes sync upstream to central cloud servers with conflict-free idempotency.

---

## 2. Advanced Features Built Into This Release

### 🌲 1. EUDR Deforestation Compliance & Geolocation
* **European Union Deforestation Regulation (EUDR)** mandates that agricultural imports into the EU must prove they were not grown on land deforested after Dec 31, 2020.
* **Implementation**: The `farms` table stores closed **GeoJSON boundary polygons** (`polygon_geojson`) and an `eudr_compliant` verification flag.
* **Interactive Leaflet Map**: The public verification view renders OpenStreetMap satellite boundaries showing the farm plot and legal cadastral coordinates.

### ✂️ 2. Batch Splitting & Merging (Lineage DAG)
* Real agricultural supply chains are not 1:1 flat tables. Farmers pool crops into bulk washing station lots, and bulk lots are split into export grades (Grade A, Grade B, Peaberry).
* **Implementation**: A **Directed Acyclic Graph (DAG)** modeled in the `batch_lineage` table (`parent_batch_id`, `child_batch_id`, `quantity_kg`, `transfer_notes`).
* **Automated Balance Tracking**: Splitting a batch creates a child lot with its own QR code and automatically deducts the allocated weight from the parent lot balance.

### 🛡️ 3. Tamper-Proof Cryptographic Audit Trail (SHA-256)
* Guarantees supply chain integrity without expensive Web3 gas fees or internet dependencies.
* **Implementation**: Each state transition in `sync_log` is cryptographically chained to its predecessor:
  $$\text{event\_hash} = \text{SHA-256}(\text{previous\_hash} : \text{table} : \text{record\_id} : \text{operation} : \text{timestamp} : \text{payload})$$
* **Audit API**: Mathematical integrity can be validated in real time via `GET /api/audit-status` or from within the Python engine via `database.verify_audit_chain()`.

### 🔒 4. PII Privacy Protection (Phone Masking)
* To prevent automated web scrapers and unauthorized brokers from harvesting smallholder farmers' personal phone numbers from public QR codes, the public certificate automatically sanitizes contact data (`+263 77 *** **77`) via `mask_phone()`.

### ⚡ 5. Offline Idempotency
* Every sync operation carries a client-generated UUIDv4 `idempotency_key`. If intermittent 2G network drops cause retried HTTP packets, the central ingestion engine deduplicates them safely with zero double-counting.

---

## 3. Project Structure

```
farm_traceability/
├── app.py              # Flask application: routes, batch splitting, PII masking, auth, APIs
├── auth.py             # Authentication & RBAC: scrypt hashing, Flask-Login, role decorators
├── database.py         # SQLite layer, automatic schema migrations, SHA-256 cryptographic engine
├── sync.py             # Offline-first synchronization engine (outbox pattern with crypto payload)
├── schema.sql          # Complete relational schema (farms, batches, batch_lineage, sync_log)
├── requirements.txt    # Python dependencies (Flask, Flask-Login, qrcode, Pillow, requests)
├── static/
│   ├── css/style.css   # Custom typography and responsive design system
│   ├── images/         # High-resolution hero imagery
│   └── qrcodes/        # Edge-generated QR code PNGs (one per batch)
├── templates/          # Jinja2 templates
│   ├── base.html       # Base layout with real-time pending sync badge
│   ├── dashboard.html  # Operational overview and stage breakdown
│   ├── batch_detail.html # Batch lifecycle, Lineage DAG viewer, lot splitting form
│   ├── verify.html     # Public QR scan certificate with Leaflet map and trust badges
│   ├── farmer_form.html# Farmer registration with EUDR polygon capture
│   └── sync.html       # Outbox inspection and manual sync trigger
└── instance/
    └── traceability.db # Local SQLite database file (auto-migrated on startup)
```

---

## 4. User Roles & Security Matrix

All administrative routes require login. The public `/verify/<batch_code>` certificate is accessible to anyone scanning a physical QR code, but recording an official inspection sign-off requires authentication.

| Role | Permissions |
|---|---|
| **admin** | Full system access: manage users, farmers, batches, exporters, split lots, run sync |
| **agent** | Field operator: register farmers with EUDR plots, create batches, split lots, log inputs/processes/outputs, run sync |
| **farmer** | Read-only access restricted strictly to their own profile and produce batches |
| **exporter** | Access to marketplace directory and ability to submit verified trade inspections |

**Default Seeded Accounts**:
- `admin` / `admin123`
- `agent` / `agent123`

---

## 5. Quickstart & Running the Application

### Option A: Using Startup Scripts (Recommended)
From the project root:
```powershell
# In PowerShell:
.\run.ps1

# Or in Windows Command Prompt / Double-click:
run.bat
```

### Option B: Manual Execution
```bash
# Navigate to the application directory:
cd farm_traceability

# Run with virtual environment:
..\.venv\Scripts\python.exe app.py
```

Open your browser to: **`http://localhost:5000`**

---

## 6. API Reference

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/verify/<batch_code>` | `GET` | Public | Public certificate rendered upon scanning product QR code |
| `/verify/<batch_code>` | `POST` | Exporter / Admin | Records official export inspection decision |
| `/batches/<id>/split` | `POST` | Agent / Admin | Splits batch into child lot, recording DAG lineage |
| `/api/sync-status` | `GET` | Public | Returns count of pending offline records waiting to sync |
| `/api/audit-status` | `GET` | Public | Returns mathematical integrity status of SHA-256 chain |

---

## 7. Database Entity Relationship Overview

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

## 8. Hackathon 3-Minute Demo Script

1. **Dashboard & Offline Badge**:
   - Point out the real-time **Sync Status badge** in the top navigation bar showing offline resilience.
2. **EUDR Farmer Plot**:
   - Navigate to **Farmers** and open grower **Tapiwa Moyo**. Show the cadastral plot boundary coordinates and the EUDR compliance certification.
3. **Produce Batch & DAG Splitting**:
   - Navigate to **Batches** and open batch **`ZWB-20260922-0001`**.
   - Show the **Batch Lineage DAG** and the **`🛡️ Cryptographic Audit Verified`** indicator.
   - Demonstrate the **"✂️ Split this batch into a new graded lot"** form to create a child export lot with automated parent deduction.
4. **The Live QR Scan "Magic Moment"**:
   - Open the verification certificate by scanning or clicking the QR code.
   - Show the **interactive Leaflet map** rendering the farm coordinates.
   - Highlight the **`🌲 EUDR Verified Deforestation-Free`** badge, the **SHA-256 tamper-proof chain seal**, and the **privacy-masked farmer phone number**.
