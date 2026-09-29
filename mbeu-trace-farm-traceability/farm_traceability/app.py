"""
Farm Traceability System — Flask application
Offline-first traceability for Zimbabwean smallholder farmers.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000  (or http://<raspberry-pi-ip>:5000 on the LAN)
"""

import os
import qrcode
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, abort
from flask_login import (
    login_user, logout_user, login_required, current_user
)

from database import get_db, init_db, insert_and_queue, queue_sync, now, pending_sync_count, verify_audit_chain
from sync import run_sync
from auth import login_manager, find_user_by_username, verify_password, roles_required, seed_default_users, create_user

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-key-change-me")
login_manager.init_app(app)

def mask_phone(phone):
    """Safely mask farmer phone number for public endpoints to prevent PII harvesting."""
    if not phone:
        return "—"
    s = str(phone).strip()
    if len(s) < 7:
        return s[:2] + "****"
    return s[:4] + " *** **" + s[-2:]

# Initialize the database and seed default accounts/exporters on import,
# so this works whether launched with `python app.py`, `flask run`, or a
# production server like gunicorn (which never hits the __main__ guard).
init_db()
seed_default_users()


@app.context_processor
def inject_sync_status():
    # Makes the "N records waiting to sync" badge available on every page,
    # not just the dashboard, without every view function repeating itself.
    return {"pending_sync": pending_sync_count()}


# Endpoints reachable without logging in: the login page itself, and the
# public QR-verification page an exporter scans (that flow must never
# require an account — the whole point is a stranger can verify a batch).
PUBLIC_ENDPOINTS = {"login", "verify_batch", "static", "api_sync_status", "api_audit_status"}


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return
    if not current_user.is_authenticated:
        return redirect(url_for("login", next=request.path))

BASE_DIR = os.path.dirname(__file__)
QR_DIR = os.path.join(BASE_DIR, "static", "qrcodes")
os.makedirs(QR_DIR, exist_ok=True)

# Public base URL a QR code should resolve to. On a Pi kiosk with no
# internet, this defaults to the Pi's LAN address; once the district
# server is live, point it there so exporters anywhere can verify.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000")


# ---------------------------------------------------------------- helpers
def generate_batch_code(district_code="ZWB"):
    """Human-readable + QR-friendly batch code, e.g. ZWB-20260922-0007"""
    conn = get_db()
    today = date.today().strftime("%Y%m%d")
    count_today = conn.execute(
        "SELECT COUNT(*) c FROM batches WHERE batch_code LIKE ?", (f"{district_code}-{today}-%",)
    ).fetchone()["c"]
    conn.close()
    return f"{district_code}-{today}-{count_today + 1:04d}"


def generate_farmer_code(district="GEN"):
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) c FROM farmers").fetchone()["c"]
    conn.close()
    return f"ZW-{district[:3].upper()}-{count + 1:05d}"


def generate_qr(batch_code):
    """Create a QR code pointing at the public verification page for this batch."""
    verify_url = f"{PUBLIC_BASE_URL}/verify/{batch_code}"
    img = qrcode.make(verify_url)
    path = os.path.join(QR_DIR, f"{batch_code}.png")
    img.save(path)
    return f"static/qrcodes/{batch_code}.png"


# ---------------------------------------------------------------- auth
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = find_user_by_username(username)
        if row and verify_password(row, password):
            from auth import User
            login_user(User(row))
            flash(f"Welcome back, {row['username']}", "success")
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        flash("Incorrect username or password", "error")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You've been logged out", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------- users (admin only)
@app.route("/users")
@roles_required("admin")
def users_list():
    conn = get_db()
    users = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template("users.html", users=users)


@app.route("/users/new", methods=["GET", "POST"])
@roles_required("admin")
def user_new():
    conn = get_db()
    farmers = conn.execute("SELECT farmer_id, full_name, farmer_code FROM farmers ORDER BY full_name").fetchall()
    exporters = conn.execute("SELECT exporter_id, company_name FROM exporters ORDER BY company_name").fetchall()
    conn.close()
    if request.method == "POST":
        f = request.form
        role = f.get("role")
        create_user(
            username=f.get("username").strip(),
            password=f.get("password"),
            role=role,
            farmer_id=f.get("farmer_id") or None if role == "farmer" else None,
            exporter_id=f.get("exporter_id") or None if role == "exporter" else None,
        )
        flash(f"Account created for {f.get('username')} ({role})", "success")
        return redirect(url_for("users_list"))
    return render_template("user_form.html", farmers=farmers, exporters=exporters)


# ---------------------------------------------------------------- dashboard
@app.route("/")
@login_required
def dashboard():
    conn = get_db()

    # Farmers see their own record and batches only, not the whole system.
    if current_user.role == "farmer" and current_user.farmer_id:
        conn.close()
        return redirect(url_for("farmer_detail", farmer_id=current_user.farmer_id))

    stats = {
        "farmers": conn.execute("SELECT COUNT(*) c FROM farmers").fetchone()["c"],
        "batches": conn.execute("SELECT COUNT(*) c FROM batches").fetchone()["c"],
        "exporters": conn.execute("SELECT COUNT(*) c FROM exporters").fetchone()["c"],
        "verified": conn.execute(
            "SELECT COUNT(*) c FROM verifications WHERE verification_status='verified'"
        ).fetchone()["c"],
    }
    by_status = conn.execute(
        "SELECT status, COUNT(*) n FROM batches GROUP BY status"
    ).fetchall()
    recent_batches = conn.execute(
        """SELECT b.*, f.full_name AS farmer_name, c.crop_name
           FROM batches b
           JOIN farmers f ON f.farmer_id = b.farmer_id
           JOIN crops c ON c.crop_id = b.crop_id
           ORDER BY b.created_at DESC LIMIT 8"""
    ).fetchall()
    conn.close()
    return render_template(
        "dashboard.html",
        stats=stats,
        by_status=by_status,
        recent_batches=recent_batches,
        pending_sync=pending_sync_count(),
    )


# ---------------------------------------------------------------- farmers
@app.route("/farmers")
def farmers_list():
    if current_user.role == "farmer":
        return redirect(url_for("farmer_detail", farmer_id=current_user.farmer_id))
    conn = get_db()
    q = request.args.get("q", "").strip()
    if q:
        farmers = conn.execute(
            """SELECT * FROM farmers
               WHERE full_name LIKE ? OR farmer_code LIKE ? OR district LIKE ?
               ORDER BY registration_date DESC""",
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        farmers = conn.execute("SELECT * FROM farmers ORDER BY registration_date DESC").fetchall()
    conn.close()
    return render_template("farmers.html", farmers=farmers, q=q)


@app.route("/farmers/new", methods=["GET", "POST"])
@roles_required("admin", "agent")
def farmer_new():
    if request.method == "POST":
        f = request.form
        farmer_code = generate_farmer_code(f.get("district", "GEN"))
        fields = {
            "farmer_code": farmer_code,
            "national_id": f.get("national_id"),
            "full_name": f.get("full_name"),
            "phone": f.get("phone"),
            "gender": f.get("gender"),
            "date_of_birth": f.get("date_of_birth") or None,
            "village": f.get("village"),
            "ward": f.get("ward"),
            "district": f.get("district"),
            "province": f.get("province"),
            "gps_lat": f.get("gps_lat") or None,
            "gps_lon": f.get("gps_lon") or None,
            "cooperative": f.get("cooperative"),
            "registered_by": f.get("registered_by", "self-registered"),
            "registration_date": now(),
            "synced": 0,
        }
        farmer_id = insert_and_queue("farmers", fields)

        # Optional: register their first farm/plot in the same form
        if f.get("farm_name") or f.get("size_hectares") or f.get("polygon_geojson"):
            insert_and_queue("farms", {
                "farmer_id": farmer_id,
                "farm_name": f.get("farm_name"),
                "size_hectares": f.get("size_hectares") or None,
                "soil_type": f.get("soil_type"),
                "water_source": f.get("water_source"),
                "gps_lat": f.get("gps_lat") or None,
                "gps_lon": f.get("gps_lon") or None,
                "polygon_geojson": f.get("polygon_geojson") or None,
                "eudr_compliant": 1 if f.get("eudr_compliant") != "0" else 0,
                "synced": 0,
            })

        flash(f"Farmer registered: {farmer_code}", "success")
        return redirect(url_for("farmer_detail", farmer_id=farmer_id))
    return render_template("farmer_form.html")


@app.route("/farmers/<int:farmer_id>")
def farmer_detail(farmer_id):
    if current_user.role == "farmer" and current_user.farmer_id != farmer_id:
        abort(403)
    conn = get_db()
    farmer = conn.execute("SELECT * FROM farmers WHERE farmer_id=?", (farmer_id,)).fetchone()
    farms = conn.execute("SELECT * FROM farms WHERE farmer_id=?", (farmer_id,)).fetchall()
    batches = conn.execute(
        """SELECT b.*, c.crop_name FROM batches b JOIN crops c ON c.crop_id=b.crop_id
           WHERE farmer_id=? ORDER BY created_at DESC""", (farmer_id,)
    ).fetchall()
    conn.close()
    if not farmer:
        flash("Farmer not found", "error")
        return redirect(url_for("farmers_list"))
    return render_template("farmer_detail.html", farmer=farmer, farms=farms, batches=batches)


# ---------------------------------------------------------------- batches
@app.route("/batches")
def batches_list():
    conn = get_db()
    status = request.args.get("status", "")
    where = []
    params = []
    if status:
        where.append("b.status=?")
        params.append(status)
    if current_user.role == "farmer":
        where.append("b.farmer_id=?")
        params.append(current_user.farmer_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    batches = conn.execute(
        f"""SELECT b.*, f.full_name AS farmer_name, c.crop_name
            FROM batches b JOIN farmers f ON f.farmer_id=b.farmer_id
            JOIN crops c ON c.crop_id=b.crop_id
            {clause} ORDER BY b.created_at DESC""", params
    ).fetchall()
    conn.close()
    return render_template("batches.html", batches=batches, status=status)


@app.route("/batches/new", methods=["GET", "POST"])
@roles_required("admin", "agent")
def batch_new():
    conn = get_db()
    if request.method == "POST":
        f = request.form
        batch_code = generate_batch_code()
        fields = {
            "batch_code": batch_code,
            "farmer_id": f.get("farmer_id"),
            "farm_id": f.get("farm_id") or None,
            "crop_id": f.get("crop_id"),
            "planting_date": f.get("planting_date") or None,
            "expected_harvest_date": f.get("expected_harvest_date") or None,
            "status": "planted",
            "is_organic": 1 if f.get("is_organic") else 0,
            "notes": f.get("notes"),
            "created_at": now(),
            "synced": 0,
        }
        batch_id = insert_and_queue("batches", fields)

        # Generate & attach QR code right away
        qr_path = generate_qr(batch_code)
        conn2 = get_db()
        conn2.execute("UPDATE batches SET qr_code_path=? WHERE batch_id=?", (qr_path, batch_id))
        conn2.commit()
        conn2.close()

        flash(f"Batch {batch_code} created with QR code", "success")
        return redirect(url_for("batch_detail", batch_id=batch_id))

    farmers = conn.execute("SELECT farmer_id, full_name, farmer_code FROM farmers ORDER BY full_name").fetchall()
    crops = conn.execute("SELECT crop_id, crop_name, variety FROM crops ORDER BY crop_name").fetchall()
    preselect_farmer = request.args.get("farmer_id")
    conn.close()
    return render_template("batch_form.html", farmers=farmers, crops=crops, preselect_farmer=preselect_farmer)


@app.route("/batches/<int:batch_id>")
def batch_detail(batch_id):
    conn = get_db()
    batch = conn.execute(
        """SELECT b.*, f.full_name AS farmer_name, f.farmer_code, f.district, f.phone AS farmer_phone,
                  c.crop_name, c.variety
           FROM batches b
           JOIN farmers f ON f.farmer_id=b.farmer_id
           JOIN crops c ON c.crop_id=b.crop_id
           WHERE b.batch_id=?""", (batch_id,)
    ).fetchone()
    inputs = conn.execute("SELECT * FROM inputs WHERE batch_id=? ORDER BY application_date", (batch_id,)).fetchall()
    processes = conn.execute("SELECT * FROM processes WHERE batch_id=? ORDER BY process_date", (batch_id,)).fetchall()
    outputs = conn.execute("SELECT * FROM outputs WHERE batch_id=? ORDER BY output_date", (batch_id,)).fetchall()
    verifications = conn.execute(
        """SELECT v.*, e.company_name FROM verifications v
           LEFT JOIN exporters e ON e.exporter_id=v.exporter_id
           WHERE v.batch_id=? ORDER BY verification_date DESC""", (batch_id,)
    ).fetchall()
    # Lineage: Parent batches (if this lot was merged/split from others) and child lots
    parents = conn.execute(
        """SELECT bl.*, b.batch_code, b.status, b.quality_grade
           FROM batch_lineage bl
           JOIN batches b ON b.batch_id = bl.parent_batch_id
           WHERE bl.child_batch_id = ?""", (batch_id,)
    ).fetchall()
    children = conn.execute(
        """SELECT bl.*, b.batch_code, b.status, b.quality_grade
           FROM batch_lineage bl
           JOIN batches b ON b.batch_id = bl.child_batch_id
           WHERE bl.parent_batch_id = ?""", (batch_id,)
    ).fetchall()

    farm = None
    if batch and batch["farm_id"]:
        farm = conn.execute("SELECT * FROM farms WHERE farm_id=?", (batch["farm_id"],)).fetchone()

    audit_status = verify_audit_chain()
    conn.close()

    if not batch:
        flash("Batch not found", "error")
        return redirect(url_for("batches_list"))
    if current_user.role == "farmer" and current_user.farmer_id != batch["farmer_id"]:
        abort(403)
    return render_template(
        "batch_detail.html", batch=batch, inputs=inputs, processes=processes,
        outputs=outputs, verifications=verifications, parents=parents, children=children,
        farm=farm, audit_status=audit_status
    )


@app.route("/batches/<int:batch_id>/split", methods=["POST"])
@roles_required("admin", "agent")
def batch_split(batch_id):
    """
    Split a batch into a child batch (e.g. separating Grade A export from local grade),
    recording the parent-child relationship in the batch_lineage DAG.
    """
    conn = get_db()
    parent = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
    if not parent:
        conn.close()
        flash("Parent batch not found", "error")
        return redirect(url_for("batches_list"))

    f = request.form
    try:
        split_qty = float(f.get("split_quantity_kg", 0))
    except (ValueError, TypeError):
        split_qty = 0

    split_notes = f.get("split_notes", "").strip() or f"Lot split from {parent['batch_code']}"
    new_grade = f.get("quality_grade", parent["quality_grade"])

    if split_qty <= 0:
        conn.close()
        flash("Split quantity must be greater than 0 kg", "error")
        return redirect(url_for("batch_detail", batch_id=batch_id))

    current_qty = parent["quantity_kg"] or 0
    if current_qty > 0 and split_qty > current_qty:
        conn.close()
        flash(f"Split quantity ({split_qty} kg) cannot exceed available parent quantity ({current_qty} kg)", "error")
        return redirect(url_for("batch_detail", batch_id=batch_id))

    # 1. Create child batch
    child_code = generate_batch_code()
    child_fields = {
        "batch_code": child_code,
        "farmer_id": parent["farmer_id"],
        "farm_id": parent["farm_id"],
        "crop_id": parent["crop_id"],
        "planting_date": parent["planting_date"],
        "expected_harvest_date": parent["expected_harvest_date"],
        "harvest_date": parent["harvest_date"],
        "quantity_kg": split_qty,
        "quality_grade": new_grade,
        "status": "ready_for_sale",
        "is_organic": parent["is_organic"],
        "notes": f"Child lot split from {parent['batch_code']}: {split_notes}",
        "created_at": now(),
        "synced": 0,
    }
    child_id = insert_and_queue("batches", child_fields)
    qr_path = generate_qr(child_code)
    conn2 = get_db()
    conn2.execute("UPDATE batches SET qr_code_path=? WHERE batch_id=?", (qr_path, child_id))
    conn2.commit()
    conn2.close()

    # 2. Record lineage in DAG
    insert_and_queue("batch_lineage", {
        "parent_batch_id": batch_id,
        "child_batch_id": child_id,
        "quantity_kg": split_qty,
        "transfer_notes": split_notes,
        "created_at": now(),
        "synced": 0,
    })

    # 3. Deduct allocated quantity from parent batch
    new_parent_qty = max(0.0, current_qty - split_qty)
    conn.execute("UPDATE batches SET quantity_kg=? WHERE batch_id=?", (new_parent_qty, batch_id))
    row = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
    queue_sync(conn, "batches", batch_id, "update", dict(row))
    conn.commit()
    conn.close()

    flash(f"Batch successfully split into child lot: {child_code} ({split_qty} kg)", "success")
    return redirect(url_for("batch_detail", batch_id=child_id))


@app.route("/batches/<int:batch_id>/status", methods=["POST"])
@roles_required("admin", "agent")
def batch_update_status(batch_id):
    new_status = request.form.get("status")
    conn = get_db()
    conn.execute("UPDATE batches SET status=? WHERE batch_id=?", (new_status, batch_id))
    row = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
    queue_sync(conn, "batches", batch_id, "update", dict(row))
    conn.commit()
    conn.close()
    flash(f"Batch status updated to {new_status}", "success")
    return redirect(url_for("batch_detail", batch_id=batch_id))


# ---------------------------------------------------------------- inputs / processes / outputs
@app.route("/batches/<int:batch_id>/inputs", methods=["POST"])
@roles_required("admin", "agent")
def add_input(batch_id):
    f = request.form
    insert_and_queue("inputs", {
        "batch_id": batch_id,
        "input_type": f.get("input_type"),
        "input_name": f.get("input_name"),
        "quantity": f.get("quantity") or None,
        "unit": f.get("unit"),
        "supplier": f.get("supplier"),
        "cost_usd": f.get("cost_usd") or None,
        "application_date": f.get("application_date") or None,
        "applied_by": f.get("applied_by"),
        "synced": 0,
    })
    flash("Input recorded", "success")
    return redirect(url_for("batch_detail", batch_id=batch_id))


@app.route("/batches/<int:batch_id>/processes", methods=["POST"])
@roles_required("admin", "agent")
def add_process(batch_id):
    f = request.form
    insert_and_queue("processes", {
        "batch_id": batch_id,
        "process_type": f.get("process_type"),
        "description": f.get("description"),
        "process_date": f.get("process_date") or None,
        "performed_by": f.get("performed_by"),
        "gps_lat": f.get("gps_lat") or None,
        "gps_lon": f.get("gps_lon") or None,
        "synced": 0,
    })

    # Harvesting a batch flips its status automatically
    if f.get("process_type") == "harvesting":
        conn = get_db()
        conn.execute(
            "UPDATE batches SET status='harvested', harvest_date=? WHERE batch_id=?",
            (f.get("process_date") or now(), batch_id),
        )
        conn.commit()
        conn.close()

    flash("Process recorded", "success")
    return redirect(url_for("batch_detail", batch_id=batch_id))


@app.route("/batches/<int:batch_id>/outputs", methods=["POST"])
@roles_required("admin", "agent")
def add_output(batch_id):
    f = request.form
    insert_and_queue("outputs", {
        "batch_id": batch_id,
        "output_type": f.get("output_type"),
        "quantity_kg": f.get("quantity_kg") or None,
        "packaging_type": f.get("packaging_type"),
        "storage_location": f.get("storage_location"),
        "output_date": f.get("output_date") or None,
        "synced": 0,
    })
    conn = get_db()
    total = conn.execute(
        "SELECT COALESCE(SUM(quantity_kg),0) t FROM outputs WHERE batch_id=?", (batch_id,)
    ).fetchone()["t"]
    conn.execute(
        "UPDATE batches SET quantity_kg=?, status='ready_for_sale' WHERE batch_id=?",
        (total, batch_id),
    )
    conn.commit()
    conn.close()
    flash("Output recorded", "success")
    return redirect(url_for("batch_detail", batch_id=batch_id))


# ---------------------------------------------------------------- exporters & verification
@app.route("/exporters")
def exporters_list():
    conn = get_db()
    exporters = conn.execute("SELECT * FROM exporters ORDER BY company_name").fetchall()
    conn.close()
    return render_template("exporters.html", exporters=exporters)


@app.route("/exporters/new", methods=["GET", "POST"])
@roles_required("admin", "agent")
def exporter_new():
    if request.method == "POST":
        f = request.form
        insert_and_queue("exporters", {
            "company_name": f.get("company_name"),
            "contact_person": f.get("contact_person"),
            "phone": f.get("phone"),
            "email": f.get("email"),
            "license_number": f.get("license_number"),
            "country": f.get("country", "Zimbabwe"),
            "registration_date": now(),
            "synced": 0,
        })
        flash("Exporter registered", "success")
        return redirect(url_for("exporters_list"))
    return render_template("exporter_form.html")


@app.route("/verify/<batch_code>", methods=["GET", "POST"])
def verify_batch(batch_code):
    """
    Public page a QR code scan lands on. An exporter/inspector can view the
    full farm-to-batch trail and, if logged in as an exporter, log a
    verification decision (verified / flagged / rejected).
    """
    conn = get_db()
    batch = conn.execute(
        """SELECT b.*, f.full_name AS farmer_name, f.farmer_code, f.district, f.province,
                  f.phone AS farmer_phone, c.crop_name, c.variety
           FROM batches b
           JOIN farmers f ON f.farmer_id=b.farmer_id
           JOIN crops c ON c.crop_id=b.crop_id
           WHERE b.batch_code=?""", (batch_code,)
    ).fetchone()

    if not batch:
        conn.close()
        return render_template("verify.html", batch=None, batch_code=batch_code)

    inputs = conn.execute("SELECT * FROM inputs WHERE batch_id=? ORDER BY application_date", (batch["batch_id"],)).fetchall()
    processes = conn.execute("SELECT * FROM processes WHERE batch_id=? ORDER BY process_date", (batch["batch_id"],)).fetchall()
    outputs = conn.execute("SELECT * FROM outputs WHERE batch_id=? ORDER BY output_date", (batch["batch_id"],)).fetchall()
    exporters = conn.execute("SELECT exporter_id, company_name FROM exporters ORDER BY company_name").fetchall()

    # Lineage and farm info
    parents = conn.execute(
        """SELECT bl.*, b.batch_code, b.status, b.quality_grade
           FROM batch_lineage bl
           JOIN batches b ON b.batch_id = bl.parent_batch_id
           WHERE bl.child_batch_id=?""", (batch["batch_id"],)
    ).fetchall()
    children = conn.execute(
        """SELECT bl.*, b.batch_code, b.status, b.quality_grade
           FROM batch_lineage bl
           JOIN batches b ON b.batch_id = bl.child_batch_id
           WHERE bl.parent_batch_id=?""", (batch["batch_id"],)
    ).fetchall()

    farm = None
    if batch["farm_id"]:
        farm = conn.execute("SELECT * FROM farms WHERE farm_id=?", (batch["farm_id"],)).fetchone()

    audit_status = verify_audit_chain()

    if request.method == "POST":
        # SECURITY CONTROL: Ensure only logged-in authorized inspectors/exporters/admins can mutate batch status
        if not current_user.is_authenticated or current_user.role not in ("admin", "agent", "exporter"):
            conn.close()
            flash("You must be logged in as an authorized exporter or agent to submit an official trade verification.", "error")
            return redirect(url_for("login", next=request.path))

        f = request.form
        insert_and_queue("verifications", {
            "batch_id": batch["batch_id"],
            "exporter_id": f.get("exporter_id") or (current_user.exporter_id if current_user.role == "exporter" else None),
            "verifier_name": f.get("verifier_name") or current_user.username,
            "verification_date": now(),
            "verification_status": f.get("verification_status", "verified"),
            "price_agreed_usd": f.get("price_agreed_usd") or None,
            "notes": f.get("notes"),
            "synced": 0,
        })
        new_status = "exported" if f.get("verification_status") == "verified" else batch["status"]
        conn.execute("UPDATE batches SET status=? WHERE batch_id=?", (new_status, batch["batch_id"]))
        conn.commit()
        conn.close()
        flash("Verification successfully recorded and cryptographically logged.", "success")
        return redirect(url_for("verify_batch", batch_code=batch_code))

    conn.close()
    return render_template(
        "verify.html", batch=batch, inputs=inputs, processes=processes,
        outputs=outputs, exporters=exporters, batch_code=batch_code,
        masked_phone=mask_phone(batch["farmer_phone"]),
        parents=parents, children=children, farm=farm, audit_status=audit_status
    )


# ---------------------------------------------------------------- sync
@app.route("/sync", methods=["GET", "POST"])
def sync_page():
    result = None
    if request.method == "POST":
        if current_user.role not in ("admin", "agent"):
            abort(403)
        result = run_sync()
    conn = get_db()
    log = conn.execute("SELECT * FROM sync_log ORDER BY created_at DESC LIMIT 50").fetchall()
    pending = conn.execute("SELECT COUNT(*) c FROM sync_log WHERE sync_status='pending'").fetchone()["c"]
    synced = conn.execute("SELECT COUNT(*) c FROM sync_log WHERE sync_status='synced'").fetchone()["c"]
    conn.close()
    return render_template("sync.html", log=log, pending=pending, synced=synced, result=result)


@app.route("/api/sync-status")
def api_sync_status():
    return jsonify({"pending": pending_sync_count()})


@app.route("/api/audit-status")
def api_audit_status():
    return jsonify(verify_audit_chain())


# Seed a couple of demo exporters if none exist yet
_conn = get_db()
if _conn.execute("SELECT COUNT(*) c FROM exporters").fetchone()["c"] == 0:
    for name, contact, phone, email, lic in [
        ("Mutare Fresh Produce Exports", "T. Chikwanha", "+263 77 123 4567", "trade@mutarefresh.co.zw", "EXP-ZW-0091"),
        ("Harare AgriTrade International", "N. Moyo", "+263 71 987 6543", "buying@harareagritrade.com", "EXP-ZW-0034"),
    ]:
        _conn.execute(
            """INSERT INTO exporters (company_name, contact_person, phone, email, license_number, registration_date, synced)
               VALUES (?,?,?,?,?,?,1)""",
            (name, contact, phone, email, lic, now()),
        )
    _conn.commit()
_conn.close()


# ---------------------------------------------------------------- startup
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
