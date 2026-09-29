"""
auth.py
Login system for the Farm Traceability System.

Roles:
- admin   — full access: manage farmers, batches, exporters, users, sync.
- agent   — day-to-day kiosk operator: register farmers, log batches/
            inputs/processes/outputs, run sync. Cannot manage user accounts.
- farmer  — sees only their own profile and their own batches.
- exporter— sees the batches available to buy and their own verification
            history. (Scanning a QR code and verifying a batch never
            requires login — that public flow is unchanged — this role
            is for an exporter who wants to browse from the dashboard too.)

Passwords are hashed with Werkzeug's scrypt-based hasher (a Flask
dependency already, so no extra crypto library is needed). Sessions are
managed with Flask-Login, which just stores the user id in the signed
session cookie — nothing exotic, and it keeps working fine offline on
a LAN with no internet, since it needs no external service.
"""

from flask_login import LoginManager, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from flask import abort
from flask_login import current_user

from database import get_db, now

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "error"


class User(UserMixin):
    def __init__(self, row):
        self.id = row["user_id"]
        self.username = row["username"]
        self.role = row["role"]
        self.farmer_id = row["farmer_id"]
        self.exporter_id = row["exporter_id"]

    @property
    def display_role(self):
        return self.role.capitalize()


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return User(row) if row else None


def find_user_by_username(username):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return row


def verify_password(row, password):
    return check_password_hash(row["password_hash"], password)


def create_user(username, password, role, farmer_id=None, exporter_id=None):
    conn = get_db()
    conn.execute(
        """INSERT INTO users (username, password_hash, role, farmer_id, exporter_id, created_at)
           VALUES (?,?,?,?,?,?)""",
        (username, generate_password_hash(password), role, farmer_id, exporter_id, now()),
    )
    conn.commit()
    conn.close()


def roles_required(*roles):
    """Route decorator: aborts 403 unless the logged-in user has one of the given roles."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def seed_default_users():
    """Create the first admin/agent accounts if the users table is empty."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    conn.close()
    if count == 0:
        create_user("admin", "admin123", "admin")
        create_user("agent", "agent123", "agent")
