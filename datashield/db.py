import sqlite3
from pathlib import Path

from flask import current_app, g


COMPANY_FIELDS = (
    "name",
    "legal_form",
    "inn",
    "ogrn",
    "address",
    "website",
    "email",
    "phone",
    "director",
    "responsible",
    "processing_purposes",
    "data_categories",
    "processors_list",
    "storage_period",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS company (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL DEFAULT '',
    legal_form TEXT NOT NULL DEFAULT '',
    inn TEXT NOT NULL DEFAULT '',
    ogrn TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    director TEXT NOT NULL DEFAULT '',
    responsible TEXT NOT NULL DEFAULT '',
    processing_purposes TEXT NOT NULL DEFAULT '',
    data_categories TEXT NOT NULL DEFAULT '',
    processors_list TEXT NOT NULL DEFAULT '',
    storage_period TEXT NOT NULL DEFAULT '',
    updated_at TEXT
);
INSERT OR IGNORE INTO company (id) VALUES (1);

CREATE TABLE IF NOT EXISTS answers (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL CHECK (value IN (0, 1, 2))
);

CREATE TABLE IF NOT EXISTS requirement_progress (
    key TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'done')),
    note TEXT NOT NULL DEFAULT '',
    due_date TEXT NOT NULL DEFAULT '',
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    systems TEXT NOT NULL DEFAULT '',
    data_categories TEXT NOT NULL DEFAULT '',
    subjects_count INTEGER NOT NULL DEFAULT 0 CHECK (subjects_count >= 0),
    special_data INTEGER NOT NULL DEFAULT 0 CHECK (special_data IN (0, 1)),
    risk TEXT NOT NULL CHECK (risk IN ('low', 'medium', 'high')),
    status TEXT NOT NULL CHECK (status IN ('new', 'investigating', 'contained', 'resolved')),
    rkn_notified_at TEXT NOT NULL DEFAULT '',
    gdpr_notified_at TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_detected ON incidents(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    database = current_app.config["DATABASE"]
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    db = get_db()
    db.executescript(SCHEMA)
    columns = {row["name"] for row in db.execute("PRAGMA table_info(incidents)")}
    for name, default in (
        ("rkn_required", "unknown"),
        ("gdpr_required", "unknown"),
        ("gdpr_aware_at", ""),
        ("rkn_final_at", ""),
    ):
        if name not in columns:
            db.execute(
                f"ALTER TABLE incidents ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'"
            )
    db.commit()


def get_company():
    return dict(get_db().execute("SELECT * FROM company WHERE id = 1").fetchone())


def get_answers():
    return {
        row["key"]: row["value"]
        for row in get_db().execute("SELECT key, value FROM answers")
    }


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
