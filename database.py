"""
database.py - SQLite tables and data-access functions.
Only database operations here; no AI and no screen code.
There is NO default or sample syllabus: syllabus data exists only after a user uploads
a file (or adds topics by hand), and every query is limited to the logged-in user.
"""
import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "study_planner.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    academic_level TEXT DEFAULT 'Undergraduate',
    preferred_hours REAL DEFAULT 2,
    preferred_days TEXT DEFAULT 'Mon,Tue,Wed,Thu,Fri',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    priority INTEGER DEFAULT 2,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    unit_order INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    topic_order INTEGER DEFAULT 1,
    difficulty TEXT DEFAULT 'Medium',
    est_minutes INTEGER DEFAULT 60,
    status TEXT DEFAULT 'Not Started',
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS study_goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    start_date TEXT NOT NULL,
    target_date TEXT NOT NULL,
    hours_per_day REAL NOT NULL,
    days_per_week INTEGER NOT NULL,
    study_days TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS study_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    reason TEXT,
    summary TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS study_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES study_plans(id) ON DELETE CASCADE,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    planned_date TEXT NOT NULL,
    session_type TEXT DEFAULT 'Study',
    planned_minutes INTEGER NOT NULL,
    actual_minutes INTEGER DEFAULT 0,
    status TEXT DEFAULT 'Planned',
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS quizzes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    difficulty TEXT DEFAULT 'Intermediate',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    question_text TEXT NOT NULL,
    option_a TEXT NOT NULL,
    option_b TEXT NOT NULL,
    option_c TEXT NOT NULL,
    option_d TEXT NOT NULL,
    correct_option TEXT NOT NULL,
    explanation TEXT
);

CREATE TABLE IF NOT EXISTS quiz_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    percentage REAL NOT NULL,
    taken_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quiz_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER NOT NULL REFERENCES quiz_results(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    selected_option TEXT,
    is_correct INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_id INTEGER NOT NULL UNIQUE REFERENCES topics(id) ON DELETE CASCADE,
    attempts INTEGER DEFAULT 0,
    avg_percentage REAL DEFAULT 0,
    last_percentage REAL DEFAULT 0,
    classification TEXT DEFAULT 'Untested',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    snapshot_date TEXT NOT NULL,
    completion_pct REAL,
    topics_completed INTEGER,
    avg_quiz_pct REAL,
    study_minutes INTEGER
);

CREATE TABLE IF NOT EXISTS syllabus_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    char_count INTEGER DEFAULT 0,
    raw_text TEXT NOT NULL,
    method TEXT,
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topic_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL UNIQUE REFERENCES topics(id) ON DELETE CASCADE,
    document_id INTEGER REFERENCES syllabus_documents(id) ON DELETE SET NULL,
    subtopics TEXT DEFAULT '[]',
    source_excerpt TEXT DEFAULT ''
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they do not exist."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------- users
def _hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 100_000
    ).hex()
    return f"{salt}${digest}"


def _verify_password(password, stored):
    salt = stored.split("$")[0]
    return secrets.compare_digest(_hash_password(password, salt), stored)


def create_user(name, username, password, academic_level, preferred_hours, preferred_days):
    """Returns the new user id. Raises ValueError if the username is taken."""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO users
                   (name, username, password_hash, academic_level, preferred_hours, preferred_days)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name.strip(), username.strip().lower(), _hash_password(password),
                 academic_level, preferred_hours, ",".join(preferred_days)),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError("That username is already taken.")


def authenticate(username, password):
    """Returns the user row if credentials are valid, else None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
        ).fetchone()
    if row and _verify_password(password, row["password_hash"]):
        return row
    return None


def get_user(user_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def update_profile(user_id, name, academic_level, preferred_hours, preferred_days):
    with get_conn() as conn:
        conn.execute(
            """UPDATE users SET name=?, academic_level=?, preferred_hours=?, preferred_days=?
               WHERE id=?""",
            (name.strip(), academic_level, preferred_hours, ",".join(preferred_days), user_id),
        )


# ---------------------------------------------------------------- syllabus
def clear_syllabus(user_id):
    """Delete this user's syllabus (units, topics, sessions, quizzes cascade), plans and
    uploaded documents."""
    with get_conn() as conn:
        conn.execute("DELETE FROM study_plans WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM subjects WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM syllabus_documents WHERE user_id = ?", (user_id,))


def save_uploaded_syllabus(user_id, filename, file_type, text, method, structure):
    """Replace the user's syllabus with the structure read from an uploaded file.
    structure = {"subjects": [{"name", "units": [{"name", "topics": [{"name", "subtopics", "excerpt"}]}]}]}
    Difficulty and study minutes are not in a syllabus file, so topics start as Medium / 60 min
    and the student can change them on the Syllabus page."""
    with get_conn() as conn:
        conn.execute("DELETE FROM study_plans WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM subjects WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM syllabus_documents WHERE user_id = ?", (user_id,))
        cur = conn.execute(
            """INSERT INTO syllabus_documents (user_id, filename, file_type, char_count, raw_text, method)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, filename, file_type, len(text), text, method))
        doc_id = cur.lastrowid
        for s in structure["subjects"]:
            cur = conn.execute("INSERT INTO subjects (user_id, name, priority) VALUES (?, ?, 2)",
                               (user_id, s["name"]))
            subject_id = cur.lastrowid
            for u_order, u in enumerate(s["units"], start=1):
                cur = conn.execute(
                    "INSERT INTO units (subject_id, name, unit_order) VALUES (?, ?, ?)",
                    (subject_id, u["name"], u_order))
                unit_id = cur.lastrowid
                for t_order, t in enumerate(u["topics"], start=1):
                    cur = conn.execute(
                        "INSERT INTO topics (unit_id, name, topic_order) VALUES (?, ?, ?)",
                        (unit_id, t["name"], t_order))
                    conn.execute(
                        """INSERT INTO topic_details (topic_id, document_id, subtopics, source_excerpt)
                           VALUES (?, ?, ?, ?)""",
                        (cur.lastrowid, doc_id,
                         json.dumps(t.get("subtopics", []), ensure_ascii=False),
                         t.get("excerpt", "")))
    return doc_id


def get_syllabus_document(user_id):
    """The most recent uploaded file of this user (without its full text), or None."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT id, filename, file_type, char_count, method, uploaded_at
               FROM syllabus_documents WHERE user_id = ? ORDER BY id DESC LIMIT 1""",
            (user_id,)).fetchone()
    return dict(row) if row else None


def get_topic_detail(user_id, topic_id):
    """Subtopics and the file excerpt for one topic, only if the topic belongs to this user."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT td.subtopics, td.source_excerpt, sd.filename
               FROM topic_details td
               JOIN topics t ON td.topic_id = t.id
               JOIN units u ON t.unit_id = u.id
               JOIN subjects s ON u.subject_id = s.id
               LEFT JOIN syllabus_documents sd ON td.document_id = sd.id
               WHERE td.topic_id = ? AND s.user_id = ?""", (topic_id, user_id)).fetchone()
    if row is None:
        return None
    try:
        subtopics = json.loads(row["subtopics"] or "[]")
    except ValueError:
        subtopics = []
    return {"subtopics": subtopics, "excerpt": row["source_excerpt"] or "",
            "filename": row["filename"]}


def get_topic_details_map(user_id):
    """{topic_id: [subtopics]} for all of this user's topics that have subtopics."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT td.topic_id, td.subtopics
               FROM topic_details td
               JOIN topics t ON td.topic_id = t.id
               JOIN units u ON t.unit_id = u.id
               JOIN subjects s ON u.subject_id = s.id
               WHERE s.user_id = ?""", (user_id,)).fetchall()
    out = {}
    for r in rows:
        try:
            items = json.loads(r["subtopics"] or "[]")
        except ValueError:
            items = []
        if items:
            out[r["topic_id"]] = items
    return out


def update_topic_settings(user_id, topic_id, difficulty, minutes):
    """Change difficulty and estimated study minutes of one of this user's topics."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE topics SET difficulty = ?, est_minutes = ?
               WHERE id = ? AND unit_id IN (
                   SELECT u.id FROM units u JOIN subjects s ON u.subject_id = s.id
                   WHERE s.user_id = ?)""",
            (difficulty, int(minutes), topic_id, user_id))


def count_syllabus(user_id):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM subjects WHERE user_id = :u) AS subjects,
                 (SELECT COUNT(*) FROM units un JOIN subjects s ON un.subject_id = s.id
                    WHERE s.user_id = :u) AS units,
                 (SELECT COUNT(*) FROM topics t JOIN units un ON t.unit_id = un.id
                    JOIN subjects s ON un.subject_id = s.id WHERE s.user_id = :u) AS topics""",
            {"u": user_id},
        ).fetchone()
    return dict(row)
