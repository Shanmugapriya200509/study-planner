"""
progress.py - Dashboard numbers (rule-based calculations and SQL, no AI).

Sessions of an old plan that were replaced and never completed are ignored, so
re-planning does not double count. Completed sessions of old plans still count.
"""
from datetime import date, timedelta

import database as db

SESSION_FILTER = "(p.is_active = 1 OR ss.status = 'Completed')"


# ================================================================ syllabus progress
def get_summary(user_id):
    """Returns a dict of numbers for the dashboard."""
    with db.get_conn() as conn:
        t = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN t.status = 'Completed' THEN 1 ELSE 0 END) AS done
               FROM topics t
               JOIN units u ON t.unit_id = u.id
               JOIN subjects s ON u.subject_id = s.id
               WHERE s.user_id = ?""",
            (user_id,),
        ).fetchone()

        unit_rows = conn.execute(
            """SELECT u.id, COUNT(t.id) AS n,
                      SUM(CASE WHEN t.status = 'Completed' THEN 1 ELSE 0 END) AS d
               FROM units u
               JOIN subjects s ON u.subject_id = s.id
               LEFT JOIN topics t ON t.unit_id = u.id
               WHERE s.user_id = ?
               GROUP BY u.id""",
            (user_id,),
        ).fetchall()

        quiz = conn.execute(
            "SELECT AVG(percentage) AS avg_pct, COUNT(*) AS n FROM quiz_results WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        goal = conn.execute(
            "SELECT target_date FROM study_goals WHERE user_id = ?", (user_id,)
        ).fetchone()

    total_topics = t["total"] or 0
    done_topics = t["done"] or 0
    total_units = len(unit_rows)
    done_units = sum(1 for r in unit_rows if r["n"] > 0 and (r["d"] or 0) == r["n"])

    days_remaining = None
    if goal:
        target = date.fromisoformat(goal["target_date"])
        days_remaining = max((target - date.today()).days, 0)

    return {
        "total_topics": total_topics,
        "done_topics": done_topics,
        "total_units": total_units,
        "done_units": done_units,
        "completion_pct": (done_topics / total_topics * 100) if total_topics else 0.0,
        "avg_quiz_pct": quiz["avg_pct"],          # None if no quiz taken yet
        "quizzes_taken": quiz["n"],
        "days_remaining": days_remaining,         # None if no deadline set yet
    }


def status_counts(user_id):
    out = {"Completed": 0, "In Progress": 0, "Not Started": 0}
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT t.status AS status, COUNT(*) AS n
               FROM topics t
               JOIN units u ON t.unit_id = u.id
               JOIN subjects s ON u.subject_id = s.id
               WHERE s.user_id = ? GROUP BY t.status""", (user_id,)).fetchall()
    for r in rows:
        out[r["status"]] = r["n"]
    return out


def subject_progress(user_id):
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT s.id, s.name, COUNT(t.id) AS total,
                      SUM(CASE WHEN t.status = 'Completed' THEN 1 ELSE 0 END) AS done
               FROM subjects s
               LEFT JOIN units u ON u.subject_id = s.id
               LEFT JOIN topics t ON t.unit_id = u.id
               WHERE s.user_id = ?
               GROUP BY s.id ORDER BY s.id""", (user_id,)).fetchall()
    out = []
    for r in rows:
        done = r["done"] or 0
        out.append({"name": r["name"], "total": r["total"], "done": done,
                    "pct": (done / r["total"] * 100) if r["total"] else 0.0})
    return out


def unit_progress(user_id):
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT s.name AS subject, u.name AS unit, COUNT(t.id) AS total,
                      SUM(CASE WHEN t.status = 'Completed' THEN 1 ELSE 0 END) AS done
               FROM units u
               JOIN subjects s ON u.subject_id = s.id
               LEFT JOIN topics t ON t.unit_id = u.id
               WHERE s.user_id = ?
               GROUP BY u.id ORDER BY s.id, u.unit_order, u.id""", (user_id,)).fetchall()
    out = []
    for r in rows:
        done = r["done"] or 0
        out.append({"subject": r["subject"], "unit": r["unit"], "total": r["total"],
                    "done": done, "pct": (done / r["total"] * 100) if r["total"] else 0.0})
    return out


# ================================================================ quiz performance
def quiz_history(user_id):
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT qr.id, t.name AS topic, qr.percentage
               FROM quiz_results qr JOIN topics t ON qr.topic_id = t.id
               WHERE qr.user_id = ? ORDER BY qr.id""", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def topic_performance_rows(user_id):
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT t.name AS topic, u.name AS unit, tp.classification,
                      tp.last_percentage, tp.avg_percentage, tp.attempts
               FROM topic_performance tp
               JOIN topics t ON tp.topic_id = t.id
               JOIN units u ON t.unit_id = u.id
               WHERE tp.user_id = ?
               ORDER BY tp.last_percentage ASC""", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def improvement_rows(user_id):
    """For topics with at least 2 quiz attempts: previous score, latest score, change."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT qr.topic_id, t.name AS topic, qr.percentage
               FROM quiz_results qr JOIN topics t ON qr.topic_id = t.id
               WHERE qr.user_id = ? ORDER BY qr.id""", (user_id,)).fetchall()
    by_topic = {}
    for r in rows:
        by_topic.setdefault(r["topic_id"], {"topic": r["topic"], "scores": []})["scores"].append(
            r["percentage"])
    out = []
    for d in by_topic.values():
        if len(d["scores"]) >= 2:
            out.append({"topic": d["topic"], "previous": d["scores"][-2], "last": d["scores"][-1],
                        "change": d["scores"][-1] - d["scores"][-2]})
    return out


# ================================================================ study time
def study_hours(user_id):
    """Completed minutes over all plans, and total planned = completed + still to do
    in the active plan."""
    with db.get_conn() as conn:
        done = conn.execute(
            """SELECT COALESCE(SUM(ss.actual_minutes), 0)
               FROM study_sessions ss JOIN study_plans p ON ss.plan_id = p.id
               WHERE p.user_id = ? AND ss.status = 'Completed'""", (user_id,)).fetchone()[0]
        remaining = conn.execute(
            """SELECT COALESCE(SUM(ss.planned_minutes), 0)
               FROM study_sessions ss JOIN study_plans p ON ss.plan_id = p.id
               WHERE p.user_id = ? AND p.is_active = 1 AND ss.status != 'Completed'""",
            (user_id,)).fetchone()[0]
    return {"completed_min": done, "planned_min": done + remaining}


def week_bounds(today=None):
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


def weekly_stats(user_id, today=None):
    """Planned vs completed study time for the current calendar week (Monday to Sunday)."""
    monday, sunday = week_bounds(today)
    with db.get_conn() as conn:
        rows = conn.execute(
            f"""SELECT ss.planned_minutes, ss.status, ss.session_type, ss.topic_id,
                       t.status AS topic_status
                FROM study_sessions ss
                JOIN study_plans p ON ss.plan_id = p.id
                JOIN topics t ON ss.topic_id = t.id
                WHERE p.user_id = ? AND ss.planned_date BETWEEN ? AND ?
                  AND {SESSION_FILTER}""",
            (user_id, monday.isoformat(), sunday.isoformat())).fetchall()
    target = sum(r["planned_minutes"] for r in rows)
    done = sum(r["planned_minutes"] for r in rows if r["status"] == "Completed")
    topics_left = {r["topic_id"] for r in rows
                   if r["session_type"] == "Study" and r["status"] != "Completed"
                   and r["topic_status"] != "Completed"}
    return {"target_min": target, "done_min": done, "topics_left": len(topics_left),
            "week_start": monday, "week_end": sunday}


def weekly_series(user_id):
    """Planned vs completed hours for every week that has sessions."""
    with db.get_conn() as conn:
        rows = conn.execute(
            f"""SELECT ss.planned_date, ss.planned_minutes, ss.status
                FROM study_sessions ss JOIN study_plans p ON ss.plan_id = p.id
                WHERE p.user_id = ? AND {SESSION_FILTER}
                ORDER BY ss.planned_date""", (user_id,)).fetchall()
    weeks = {}
    for r in rows:
        d = date.fromisoformat(r["planned_date"])
        start = d - timedelta(days=d.weekday())
        w = weeks.setdefault(start, {"planned": 0, "completed": 0})
        w["planned"] += r["planned_minutes"]
        if r["status"] == "Completed":
            w["completed"] += r["planned_minutes"]
    return [{"week_start": k, "planned_h": v["planned"] / 60, "completed_h": v["completed"] / 60}
            for k, v in sorted(weeks.items())]


def pace(user_id, today=None):
    """Compare study time that was due (dated before today, or marked missed) with what
    was completed. ratio is None when nothing was due yet."""
    today = today or date.today()
    with db.get_conn() as conn:
        rows = conn.execute(
            f"""SELECT ss.planned_minutes, ss.status
                FROM study_sessions ss JOIN study_plans p ON ss.plan_id = p.id
                WHERE p.user_id = ? AND (ss.planned_date < ? OR ss.status = 'Missed')
                  AND {SESSION_FILTER}""",
            (user_id, today.isoformat())).fetchall()
    due = sum(r["planned_minutes"] for r in rows)
    done = sum(r["planned_minutes"] for r in rows if r["status"] == "Completed")
    missed = sum(1 for r in rows if r["status"] != "Completed")
    return {"due_min": due, "done_min": done, "missed": missed,
            "ratio": (done / due) if due else None}
