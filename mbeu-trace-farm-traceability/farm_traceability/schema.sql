-- ============================================================
-- FARM TRACEABILITY SYSTEM — SQLite Schema
-- Offline-first design for Zimbabwean smallholder farmers
-- Every farmer/agent-owned table carries `synced` + `sync_ts`
-- so the local SQLite file can work fully offline and later
-- reconcile with a central server when connectivity returns.
-- ============================================================

PRAGMA foreign_keys = ON;

-- ---------- USERS & ROLES ----------
CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('admin','agent','farmer','exporter')),
    farmer_id       INTEGER,               -- linked if role = farmer
    exporter_id     INTEGER,               -- linked if role = exporter
    created_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (farmer_id) REFERENCES farmers(farmer_id),
    FOREIGN KEY (exporter_id) REFERENCES exporters(exporter_id)
);

-- ---------- FARMERS ----------
CREATE TABLE IF NOT EXISTS farmers (
    farmer_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_code     TEXT UNIQUE NOT NULL,   -- e.g. ZW-MSV-00231 (district-based human readable code)
    national_id     TEXT UNIQUE,
    full_name       TEXT NOT NULL,
    phone           TEXT,
    gender          TEXT CHECK (gender IN ('male','female','other')),
    date_of_birth   TEXT,
    village         TEXT,
    ward            TEXT,
    district        TEXT,
    province        TEXT,
    gps_lat         REAL,
    gps_lon         REAL,
    cooperative     TEXT,                  -- farmer group / cooperative name, if any
    photo_path      TEXT,
    registered_by   TEXT,                  -- extension agent / device id that captured record
    registration_date TEXT DEFAULT (datetime('now')),
    synced          INTEGER DEFAULT 0,      -- 0 = pending, 1 = synced to central server
    sync_ts         TEXT
);

-- ---------- FARMS (a farmer may have multiple fields/plots) ----------
CREATE TABLE IF NOT EXISTS farms (
    farm_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id       INTEGER NOT NULL,
    farm_name       TEXT,
    size_hectares   REAL,
    soil_type       TEXT,
    water_source    TEXT,                  -- rainfed / borehole / river / dam
    gps_lat         REAL,
    gps_lon         REAL,
    polygon_geojson TEXT,                  -- EUDR compliant closed polygon coordinates
    eudr_compliant  INTEGER DEFAULT 1,     -- 1 = certified deforestation-free plot
    synced          INTEGER DEFAULT 0,
    sync_ts         TEXT,
    FOREIGN KEY (farmer_id) REFERENCES farmers(farmer_id) ON DELETE CASCADE
);

-- ---------- CROPS (reference table) ----------
CREATE TABLE IF NOT EXISTS crops (
    crop_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    crop_name       TEXT NOT NULL,          -- maize, groundnuts, sorghum, tobacco, paprika...
    variety         TEXT,
    typical_cycle_days INTEGER
);

-- ---------- PRODUCE BATCHES (the core traceable unit) ----------
CREATE TABLE IF NOT EXISTS batches (
    batch_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_code      TEXT UNIQUE NOT NULL,   -- human + QR code, e.g. ZWB-2026-0917-0001
    farmer_id       INTEGER NOT NULL,
    farm_id         INTEGER,
    crop_id         INTEGER NOT NULL,
    planting_date   TEXT,
    expected_harvest_date TEXT,
    harvest_date    TEXT,
    quantity_kg     REAL,                   -- filled in at harvest / output stage
    quality_grade   TEXT CHECK (quality_grade IN ('A','B','C','Ungraded')) DEFAULT 'Ungraded',
    status          TEXT NOT NULL DEFAULT 'planted'
                    CHECK (status IN ('planted','growing','harvested','processed',
                                       'ready_for_sale','sold','exported','rejected')),
    is_organic      INTEGER DEFAULT 0,
    qr_code_path    TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    synced          INTEGER DEFAULT 0,
    sync_ts         TEXT,
    FOREIGN KEY (farmer_id) REFERENCES farmers(farmer_id),
    FOREIGN KEY (farm_id)   REFERENCES farms(farm_id),
    FOREIGN KEY (crop_id)   REFERENCES crops(crop_id)
);

-- ---------- INPUTS applied to a batch (seed, fertilizer, pesticide, water) ----------
CREATE TABLE IF NOT EXISTS inputs (
    input_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        INTEGER NOT NULL,
    input_type      TEXT NOT NULL CHECK (input_type IN ('seed','fertilizer','pesticide','herbicide','water','other')),
    input_name       TEXT NOT NULL,
    quantity        REAL,
    unit            TEXT,                    -- kg, L, g, sachets
    supplier        TEXT,
    cost_usd        REAL,
    application_date TEXT,
    applied_by      TEXT,
    synced          INTEGER DEFAULT 0,
    sync_ts         TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE
);

-- ---------- PROCESSES / FARM ACTIVITIES on a batch ----------
CREATE TABLE IF NOT EXISTS processes (
    process_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        INTEGER NOT NULL,
    process_type    TEXT NOT NULL CHECK (process_type IN
                    ('land_prep','planting','weeding','irrigation','pest_control',
                     'harvesting','drying','sorting','packaging','storage','transport')),
    description     TEXT,
    process_date    TEXT,
    performed_by    TEXT,
    gps_lat         REAL,
    gps_lon         REAL,
    synced          INTEGER DEFAULT 0,
    sync_ts         TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE
);

-- ---------- OUTPUTS produced from a batch (post-harvest handling) ----------
CREATE TABLE IF NOT EXISTS outputs (
    output_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        INTEGER NOT NULL,
    output_type     TEXT CHECK (output_type IN ('raw','dried','shelled','graded','packaged')),
    quantity_kg     REAL,
    packaging_type  TEXT,                    -- bag, crate, bulk
    storage_location TEXT,
    output_date     TEXT,
    synced          INTEGER DEFAULT 0,
    sync_ts         TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE
);

-- ---------- EXPORTERS / BUYERS ----------
CREATE TABLE IF NOT EXISTS exporters (
    exporter_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name    TEXT NOT NULL,
    contact_person  TEXT,
    phone           TEXT,
    email           TEXT,
    license_number  TEXT,
    country         TEXT DEFAULT 'Zimbabwe',
    registration_date TEXT DEFAULT (datetime('now')),
    synced          INTEGER DEFAULT 0,
    sync_ts         TEXT
);

-- ---------- VERIFICATIONS (exporter/inspector scans a batch QR) ----------
CREATE TABLE IF NOT EXISTS verifications (
    verification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        INTEGER NOT NULL,
    exporter_id     INTEGER,
    verifier_name   TEXT,
    verification_date TEXT DEFAULT (datetime('now')),
    verification_status TEXT CHECK (verification_status IN ('verified','flagged','rejected')) DEFAULT 'verified',
    price_agreed_usd REAL,
    gps_lat         REAL,
    gps_lon         REAL,
    notes           TEXT,
    synced          INTEGER DEFAULT 0,
    sync_ts         TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id),
    FOREIGN KEY (exporter_id) REFERENCES exporters(exporter_id)
);

-- ---------- BATCH LINEAGE (DAG for batch splitting and merging) ----------
CREATE TABLE IF NOT EXISTS batch_lineage (
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
);

-- ---------- SYNC LOG (offline-first change queue / tamper-proof cryptographic audit trail) ----------
CREATE TABLE IF NOT EXISTS sync_log (
    sync_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name      TEXT NOT NULL,
    record_id       INTEGER NOT NULL,
    operation       TEXT NOT NULL CHECK (operation IN ('insert','update','delete')),
    payload_json    TEXT,                    -- full row snapshot, queued for the server
    device_id       TEXT,                    -- which Raspberry Pi / kiosk captured it
    previous_hash   TEXT,                    -- SHA-256 chain link to preceding event
    event_hash      TEXT,                    -- SHA-256(prev_hash + table + record_id + op + payload + ts)
    idempotency_key TEXT UNIQUE,             -- Prevents duplicate execution on offline retries
    created_at      TEXT DEFAULT (datetime('now')),
    sync_status     TEXT NOT NULL DEFAULT 'pending' CHECK (sync_status IN ('pending','synced','failed')),
    synced_at       TEXT
);

-- ---------- Helpful indexes ----------
CREATE INDEX IF NOT EXISTS idx_batches_farmer ON batches(farmer_id);
CREATE INDEX IF NOT EXISTS idx_batches_status ON batches(status);
CREATE INDEX IF NOT EXISTS idx_inputs_batch ON inputs(batch_id);
CREATE INDEX IF NOT EXISTS idx_processes_batch ON processes(batch_id);
CREATE INDEX IF NOT EXISTS idx_outputs_batch ON outputs(batch_id);
CREATE INDEX IF NOT EXISTS idx_verifications_batch ON verifications(batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_lineage_parent ON batch_lineage(parent_batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_lineage_child ON batch_lineage(child_batch_id);
CREATE INDEX IF NOT EXISTS idx_synclog_status ON sync_log(sync_status);

-- ---------- Seed reference data ----------
INSERT OR IGNORE INTO crops (crop_id, crop_name, variety, typical_cycle_days) VALUES
    (1, 'Maize', 'ZM523 (drought-tolerant)', 120),
    (2, 'Groundnuts', 'Nyanda', 110),
    (3, 'Sorghum', 'Macia', 100),
    (4, 'Cotton', 'SC Compact', 160),
    (5, 'Paprika', 'Papri King', 150),
    (6, 'Sugar Beans', 'Gloria', 90);
