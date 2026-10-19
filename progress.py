"""
progress.py - Dashboard numbers (rule-based calculations, no AI).
"""
from datetime import date

import database as db


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
