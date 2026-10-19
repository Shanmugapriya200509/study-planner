"""
database.py - SQLite tables and data-access functions.
Only database operations here; no AI and no screen code.
"""
import hashlib
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
"""

# (topic name, difficulty, estimated minutes)
DEMO_SUBJECT = "Computer Networks"
DEMO_SYLLABUS = {
    "Unit 1: Network Models and Fundamentals": [
        ("Network Types and Topologies", "Easy", 45),
        ("OSI Model", "Medium", 60),
        ("TCP/IP Model", "Medium", 60),
        ("Protocols and Standards", "Easy", 45),
    ],
    "Unit 2: Data Link Layer": [
        ("Framing", "Easy", 45),
        ("Error Detection", "Hard", 75),
        ("Error Correction", "Hard", 75),
        ("Flow Control", "Medium", 60),
        ("Multiple Access Protocols", "Hard", 75),
    ],
    "Unit 3: Network Layer": [
        ("IPv4 Addressing", "Medium", 60),
        ("Subnetting and CIDR", "Hard", 90),
        ("Routing Algorithms", "Hard", 75),
        ("Routing Protocols (RIP, OSPF)", "Hard", 75),
        ("IPv6 and ICMP", "Medium", 60),
    ],
    "Unit 4: Transport Layer": [
        ("UDP", "Easy", 45),
        ("TCP Connection Management", "Medium", 60),
        ("TCP Flow Control", "Medium", 60),
        ("Congestion Control", "Hard", 75),
        ("Sockets", "Easy", 45),
    ],
    "Unit 5: Application Layer and Security": [
        ("DNS", "Medium", 60),
        ("HTTP and HTTPS", "Easy", 45),
        ("Email Protocols (SMTP, POP3, IMAP)", "Easy", 45),
        ("DHCP", "Easy", 45),
        ("Network Security Basics", "Medium", 60),
    ],
}


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
    """Delete all subjects (units, topics, sessions, quizzes cascade) and plans."""
    with get_conn() as conn:
        conn.execute("DELETE FROM subjects WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM study_plans WHERE user_id = ?", (user_id,))


def seed_demo_syllabus(user_id):
    """Replace the user's syllabus with the demo Computer Networks syllabus."""
    clear_syllabus(user_id)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO subjects (user_id, name, priority) VALUES (?, ?, 1)",
            (user_id, DEMO_SUBJECT),
        )
        subject_id = cur.lastrowid
        for u_order, (unit_name, topics) in enumerate(DEMO_SYLLABUS.items(), start=1):
            cur = conn.execute(
                "INSERT INTO units (subject_id, name, unit_order) VALUES (?, ?, ?)",
                (subject_id, unit_name, u_order),
            )
            unit_id = cur.lastrowid
            for t_order, (t_name, diff, minutes) in enumerate(topics, start=1):
                conn.execute(
                    """INSERT INTO topics (unit_id, name, topic_order, difficulty, est_minutes)
                       VALUES (?, ?, ?, ?, ?)""",
                    (unit_id, t_name, t_order, diff, minutes),
                )


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
