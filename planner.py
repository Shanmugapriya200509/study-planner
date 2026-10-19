"""
planner.py - Study goal setup and study-capacity calculation.
All calculations here are plain Python / SQL (rule-based, no AI).
The AI study-plan generation will be added to this file in a later step.
"""
from datetime import date, timedelta

import streamlit as st

import database as db

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
REVISION_TEST_BUFFER = 0.30   # extra 30% time reserved for revision and tests (our assumption)


# ---------------------------------------------------------------- database helpers
def get_goal(user_id):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM study_goals WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def save_goal(user_id, start, target, hours, study_days):
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO study_goals
               (user_id, start_date, target_date, hours_per_day, days_per_week, study_days, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                 start_date = excluded.start_date,
                 target_date = excluded.target_date,
                 hours_per_day = excluded.hours_per_day,
                 days_per_week = excluded.days_per_week,
                 study_days = excluded.study_days,
                 updated_at = CURRENT_TIMESTAMP""",
            (user_id, start.isoformat(), target.isoformat(), hours,
             len(study_days), ",".join(study_days)),
        )


def get_subjects(user_id):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, priority FROM subjects WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def save_priorities(user_id, priority_ids):
    """Chosen subjects get priority 1 (High); all others priority 2 (Normal)."""
    with db.get_conn() as conn:
        conn.execute("UPDATE subjects SET priority = 2 WHERE user_id = ?", (user_id,))
        for sid in priority_ids:
            conn.execute("UPDATE subjects SET priority = 1 WHERE id = ? AND user_id = ?",
                         (sid, user_id))


def get_remaining_workload(user_id):
    """Topics not yet completed and their total estimated minutes."""
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(t.est_minutes), 0) AS minutes
               FROM topics t
               JOIN units u ON t.unit_id = u.id
               JOIN subjects s ON u.subject_id = s.id
               WHERE s.user_id = ? AND t.status != 'Completed'""",
            (user_id,),
        ).fetchone()
    return {"topics": row["n"], "minutes": row["minutes"]}


# ---------------------------------------------------------------- calculations
def count_study_days(start, end, study_days):
    """Number of calendar days between start and end (inclusive) that fall on a study weekday."""
    count = 0
    d = start
    while d <= end:
        if DAYS[d.weekday()] in study_days:
            count += 1
        d += timedelta(days=1)
    return count


def calculate_capacity(goal, workload):
    """Compare available study time with the time the remaining topics need."""
    start = max(date.fromisoformat(goal["start_date"]), date.today())
    target = date.fromisoformat(goal["target_date"])
    study_days = goal["study_days"].split(",") if goal["study_days"] else []

    days_available = count_study_days(start, target, study_days) if target >= start else 0
    capacity_hours = days_available * goal["hours_per_day"]
    required_hours = workload["minutes"] / 60
    required_with_buffer = required_hours * (1 + REVISION_TEST_BUFFER)

    if days_available == 0:
        status = "none"
    elif required_with_buffer <= capacity_hours * 0.85:
        status = "comfortable"
    elif required_with_buffer <= capacity_hours:
        status = "tight"
    else:
        status = "insufficient"

    return {
        "days_available": days_available,
        "capacity_hours": capacity_hours,
        "required_hours": required_hours,
        "required_with_buffer": required_with_buffer,
        "hours_per_day_needed": (required_with_buffer / days_available) if days_available else None,
        "status": status,
    }


# ---------------------------------------------------------------- page
def render_goal(user):
    st.header("🎯 Study Goal Setup")

    subjects = get_subjects(user["id"])
    if not subjects:
        st.warning("Your syllabus is empty. Add subjects on the **Syllabus** page "
                   "or load the demo syllabus from **Profile** first.")
        return

    saved = get_goal(user["id"])
    default_start = date.fromisoformat(saved["start_date"]) if saved else date.today()
    default_target = (date.fromisoformat(saved["target_date"]) if saved
                      else date.today() + timedelta(days=30))
    default_hours = float(saved["hours_per_day"]) if saved else float(user["preferred_hours"])
    default_days = ([d for d in saved["study_days"].split(",") if d in DAYS] if saved
                    else [d for d in user["preferred_days"].split(",") if d in DAYS])

    subject_names = {s["id"]: s["name"] for s in subjects}
    default_priority = [s["id"] for s in subjects if s["priority"] == 1]

    with st.form("goal_form"):
        c1, c2 = st.columns(2)
        start = c1.date_input("Start date", value=default_start)
        target = c2.date_input("Target completion date", value=default_target)
        hours = st.slider("Available study hours per day", 0.5, 12.0, default_hours, 0.5)
        study_days = st.multiselect("Study days (days per week = number selected)",
                                    DAYS, default=default_days)
        priority_ids = st.multiselect("Priority subjects (optional)",
                                      options=list(subject_names.keys()),
                                      default=default_priority,
                                      format_func=lambda i: subject_names[i])
        submitted = st.form_submit_button("💾 Save goal and calculate capacity", type="primary")

    if submitted:
        if target <= start:
            st.error("The target date must be after the start date.")
        elif not study_days:
            st.error("Select at least one study day.")
        else:
            save_goal(user["id"], start, target, hours, study_days)
            save_priorities(user["id"], priority_ids)
            st.success("Goal saved.")

    goal = get_goal(user["id"])
    if not goal:
        st.info("Fill in the form above and click **Save goal** to see your study capacity.")
        return

    # ---- capacity results
    workload = get_remaining_workload(user["id"])
    cap = calculate_capacity(goal, workload)

    st.divider()
    st.subheader("Study capacity")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Subjects", len(subjects))
    m2.metric("Study days per week", goal["days_per_week"])
    m3.metric("Study days available", cap["days_available"])
    m4.metric("Available study hours", f"{cap['capacity_hours']:.1f} h")

    n1, n2, n3 = st.columns(3)
    n1.metric("Topics remaining", workload["topics"])
    n2.metric("Study time needed", f"{cap['required_hours']:.1f} h")
    n3.metric(f"Needed + {int(REVISION_TEST_BUFFER * 100)}% revision/tests",
              f"{cap['required_with_buffer']:.1f} h")

    if cap["capacity_hours"] > 0:
        st.progress(min(cap["required_with_buffer"] / cap["capacity_hours"], 1.0),
                    text="Share of your available time that the remaining syllabus needs")

    if cap["status"] == "none":
        st.error("There are no study days left before your target date. "
                 "Extend the deadline or add more study days.")
    elif cap["status"] == "comfortable":
        st.success("Your available time comfortably covers the remaining syllabus.")
    elif cap["status"] == "tight":
        st.warning("Your available time covers the syllabus, but it is tight. "
                   "Try not to miss sessions.")
    else:
        shortfall = cap["required_with_buffer"] - cap["capacity_hours"]
        st.error(f"Not enough time: you are about {shortfall:.1f} hours short. "
                 f"You would need about {cap['hours_per_day_needed']:.1f} hours per study day, "
                 "or a later deadline, or more study days.")

    st.caption("This is simple arithmetic: study days x hours per day, compared with the "
               "estimated minutes of unfinished topics plus a 30% allowance for revision and "
               "tests. It is an estimate, not a prediction. No AI is used on this page.")
