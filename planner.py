"""
planner.py - Study goal setup, capacity calculation and study-plan generation.

Who does what:
  * Capacity, scheduling onto dates, validating/trimming the AI output, fallback plan
        -> plain Python (rule-based)
  * Choosing topic order, revision and test sessions, short explanation
        -> Gemini LLM, through ai_service.py and prompts.py
"""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import ai_service as ai
import database as db
import prompts

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
REVISION_TEST_BUFFER = 0.30   # extra 30% time reserved for revision and tests (our assumption)
DIFF_RANK = {"Easy": 1, "Medium": 2, "Hard": 3}
VALID_TYPES = {"Study", "Revision", "Test"}
ICONS = {"Study": "📖", "Revision": "🔁", "Test": "📝"}


# ================================================================ goal: database helpers
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


# ================================================================ goal: calculations
def study_dates(start, end, study_days):
    """Calendar dates from start to end (inclusive) that fall on a chosen study weekday."""
    dates = []
    d = start
    while d <= end:
        if DAYS[d.weekday()] in study_days:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def count_study_days(start, end, study_days):
    return len(study_dates(start, end, study_days))


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


# ================================================================ goal: page
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


# ================================================================ plan: data access
def get_planning_topics(user_id):
    """Unfinished topics with subject priority and past quiz performance (if any)."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT t.id AS topic_id, t.name AS topic, t.difficulty, t.est_minutes, t.status,
                      t.topic_order, u.id AS unit_id, u.name AS unit, u.unit_order,
                      s.id AS subject_id, s.name AS subject, s.priority,
                      COALESCE(tp.classification, 'Untested') AS performance,
                      tp.last_percentage AS last_pct
               FROM topics t
               JOIN units u ON t.unit_id = u.id
               JOIN subjects s ON u.subject_id = s.id
               LEFT JOIN topic_performance tp ON tp.topic_id = t.id
               WHERE s.user_id = ? AND t.status != 'Completed'
               ORDER BY s.priority, s.id, u.unit_order, u.id, t.topic_order, t.id""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_active_plan(user_id):
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM study_plans WHERE user_id = ? AND is_active = 1
               ORDER BY version DESC LIMIT 1""", (user_id,)).fetchone()
    return dict(row) if row else None


def get_plan_sessions(plan_id):
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT ss.id, ss.topic_id, ss.planned_date, ss.session_type, ss.planned_minutes,
                      ss.status, t.name AS topic, u.name AS unit, s.name AS subject
               FROM study_sessions ss
               JOIN topics t ON ss.topic_id = t.id
               JOIN units u ON t.unit_id = u.id
               JOIN subjects s ON u.subject_id = s.id
               WHERE ss.plan_id = ?
               ORDER BY ss.planned_date, ss.id""", (plan_id,)).fetchall()
    return [dict(r) for r in rows]


def mark_missed(plan_id):
    """Sessions dated before today that are still 'Planned' become 'Missed'."""
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE study_sessions SET status = 'Missed'
               WHERE plan_id = ? AND status = 'Planned' AND planned_date < ?""",
            (plan_id, date.today().isoformat()))


def save_plan(user_id, reason, summary, sessions):
    with db.get_conn() as conn:
        conn.execute("UPDATE study_plans SET is_active = 0 WHERE user_id = ?", (user_id,))
        version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM study_plans WHERE user_id = ?",
            (user_id,)).fetchone()[0]
        cur = conn.execute(
            """INSERT INTO study_plans (user_id, version, reason, summary, is_active)
               VALUES (?, ?, ?, ?, 1)""", (user_id, version, reason, summary))
        plan_id = cur.lastrowid
        conn.executemany(
            """INSERT INTO study_sessions
               (plan_id, topic_id, planned_date, session_type, planned_minutes)
               VALUES (?, ?, ?, ?, ?)""",
            [(plan_id, s["topic_id"], s["date"], s["session_type"], s["minutes"])
             for s in sessions])
    return plan_id, version


# ================================================================ plan: rule-based logic
def _item(topic_id, session_type, minutes, reason):
    return {"topic_id": topic_id, "session_type": session_type,
            "minutes": int(minutes), "reason": reason}


def rule_based_items(topics):
    """Fallback plan (no AI): syllabus order, priority subjects first, revision for
    weak / moderate / hard topics, and one test at the end of each unit."""
    unit_ids, by_unit = [], {}
    for t in topics:
        if t["unit_id"] not in by_unit:
            by_unit[t["unit_id"]] = []
            unit_ids.append(t["unit_id"])
        by_unit[t["unit_id"]].append(t)

    items = []
    for uid in unit_ids:
        group = by_unit[uid]
        for t in group:
            items.append(_item(t["topic_id"], "Study", t["est_minutes"], "Core study session"))
        for t in group:
            if t["performance"] == "Weak":
                items.append(_item(t["topic_id"], "Revision", 45, "Weak in earlier quiz"))
            elif t["performance"] == "Moderate":
                items.append(_item(t["topic_id"], "Revision", 30, "Moderate in earlier quiz"))
            elif t["performance"] == "Untested" and t["difficulty"] == "Hard":
                items.append(_item(t["topic_id"], "Revision", 30, "Hard topic"))
        hardest = max(group, key=lambda t: DIFF_RANK.get(t["difficulty"], 2))
        items.append(_item(hardest["topic_id"], "Test", 20, f"Unit test: {hardest['unit']}"))
    return items


def validate_ai_items(data, topics):
    """Check the AI reply. Returns (items, summary) or raises ValueError."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("the reply has no 'items' list")
    valid_ids = {t["topic_id"] for t in topics}
    clean = []
    for raw in data["items"]:
        try:
            tid = int(raw["topic_id"])
            stype = str(raw["session_type"]).strip().capitalize()
            minutes = int(raw["minutes"])
        except (KeyError, TypeError, ValueError):
            continue
        if tid not in valid_ids or stype not in VALID_TYPES:
            continue
        minutes = max(15, min(120, int(round(minutes / 5) * 5)))
        clean.append(_item(tid, stype, minutes, str(raw.get("reason", ""))[:80]))
    if len(clean) < max(1, len(topics) // 2):
        raise ValueError("too few usable sessions in the reply")
    return clean, str(data.get("summary", "")).strip()[:600]


def ensure_all_topics_have_study(items, topics):
    have = {i["topic_id"] for i in items if i["session_type"] == "Study"}
    for t in topics:
        if t["topic_id"] not in have:
            items.append(_item(t["topic_id"], "Study", t["est_minutes"],
                               "Added so every topic is covered"))
    return items


def fit_to_capacity(items, limit):
    """Trim items so total minutes <= limit. Returns (items, trimmed_flag)."""
    items = [dict(i) for i in items]

    def total():
        return sum(i["minutes"] for i in items)

    trimmed = False
    for kind in ("Revision", "Test"):
        while total() > limit:
            idx = [k for k, i in enumerate(items) if i["session_type"] == kind]
            if not idx:
                break
            items.pop(idx[-1])
            trimmed = True
    if total() > limit:
        factor = limit / total()
        for i in items:
            i["minutes"] = max(15, int(i["minutes"] * factor // 5) * 5)
        trimmed = True
        while total() > limit and items:
            items.pop()
    return items, trimmed


def schedule_items(items, dates, minutes_per_day):
    """Place items, in order, onto the study dates. A long item may continue on the next day."""
    sessions, di, left = [], 0, minutes_per_day
    for it in items:
        remaining = it["minutes"]
        while remaining > 0 and di < len(dates):
            if left < 15 and remaining > left:
                di += 1
                left = minutes_per_day
                continue
            chunk = min(remaining, left)
            sessions.append({"topic_id": it["topic_id"], "session_type": it["session_type"],
                             "minutes": chunk, "date": dates[di].isoformat()})
            remaining -= chunk
            left -= chunk
            if left < 5:
                di += 1
                left = minutes_per_day
    return sessions


# ================================================================ plan: generation
def generate_plan(user_id, reason="Initial plan", use_ai=True):
    """Builds and saves a new active plan. Returns a result dict. Raises ValueError for
    problems the student can fix (no goal, nothing to plan, no days left)."""
    goal = get_goal(user_id)
    if not goal:
        raise ValueError("Set your study goal first on the Study Goal page.")
    topics = get_planning_topics(user_id)
    if not topics:
        raise ValueError("There are no unfinished topics to plan.")

    start = max(date.fromisoformat(goal["start_date"]), date.today())
    target = date.fromisoformat(goal["target_date"])
    study_days = goal["study_days"].split(",") if goal["study_days"] else []
    dates = study_dates(start, target, study_days) if target >= start else []
    if not dates:
        raise ValueError("No study days are left before the target date.")
    minutes_per_day = int(round(goal["hours_per_day"] * 60))
    capacity = len(dates) * minutes_per_day
    limit = int(capacity * 0.95)

    warnings, items, summary, source = [], None, "", "Rule-based"

    if use_ai and ai.has_api_key():
        ctx = {
            "today": date.today().isoformat(), "target": goal["target_date"],
            "days": len(dates), "minutes_per_day": minutes_per_day,
            "capacity": capacity, "limit": limit,
            "topics": [{
                "topic_id": t["topic_id"], "subject": t["subject"], "unit": t["unit"],
                "topic": t["topic"], "difficulty": t["difficulty"],
                "est_minutes": t["est_minutes"], "status": t["status"],
                "subject_priority": t["priority"], "performance": t["performance"],
                "last_quiz_pct": t["last_pct"],
            } for t in topics],
        }
        try:
            data = ai.generate_json(prompts.build_plan_prompt(ctx), system=prompts.PLAN_SYSTEM)
            items, summary = validate_ai_items(data, topics)
            source = "AI (Gemini)"
        except ai.AIError as e:
            warnings.append(f"AI unavailable: {e} A rule-based plan was used instead.")
        except ValueError as e:
            warnings.append(f"The AI reply could not be used ({e}). "
                            "A rule-based plan was used instead.")
    elif use_ai:
        warnings.append("No Gemini API key found, so a rule-based plan was used.")

    if items is None:
        items = rule_based_items(topics)
        summary = ("Rule-based plan: priority subjects first, then syllabus order, with revision "
                   "for hard or weak topics and a short test after each unit.")

    items = ensure_all_topics_have_study(items, topics)
    items, trimmed = fit_to_capacity(items, limit)
    if trimmed:
        warnings.append("Your available time is short, so some revision/test sessions were "
                        "removed or shortened. Consider extending the deadline or adding hours.")
    if not items:
        raise ValueError("Not enough study time to schedule anything.")

    sessions = schedule_items(items, dates, minutes_per_day)
    scheduled = {s["topic_id"] for s in sessions if s["session_type"] == "Study"}
    missing = [t["topic"] for t in topics if t["topic_id"] not in scheduled]
    if missing:
        warnings.append(f"{len(missing)} topic(s) did not fit before the deadline: "
                        + ", ".join(missing[:6]) + ("..." if len(missing) > 6 else ""))

    plan_id, version = save_plan(user_id, reason, f"[{source}] {summary}", sessions)
    return {"plan_id": plan_id, "version": version, "source": source,
            "sessions": len(sessions), "warnings": warnings}


# ================================================================ plan: page
def _on_session_toggle(session_id, topic_id, planned_minutes, session_type):
    done = bool(st.session_state[f"sess_{session_id}"])
    status = "Completed" if done else "Planned"
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE study_sessions SET status = ?, actual_minutes = ?,
               completed_at = CASE WHEN ? = 'Completed' THEN CURRENT_TIMESTAMP ELSE NULL END
               WHERE id = ?""",
            (status, planned_minutes if done else 0, status, session_id))
        if session_type != "Study":
            return
        plan_id = conn.execute("SELECT plan_id FROM study_sessions WHERE id = ?",
                               (session_id,)).fetchone()[0]
        statuses = [r["status"] for r in conn.execute(
            """SELECT status FROM study_sessions
               WHERE plan_id = ? AND topic_id = ? AND session_type = 'Study'""",
            (plan_id, topic_id))]
        current = conn.execute("SELECT status FROM topics WHERE id = ?",
                               (topic_id,)).fetchone()["status"]
        new = current
        if statuses and all(s == "Completed" for s in statuses):
            new = "Completed"
        elif current == "Completed":
            new = "In Progress"
        elif current == "Not Started" and any(s == "Completed" for s in statuses):
            new = "In Progress"
        conn.execute(
            """UPDATE topics SET status = ?,
               completed_at = CASE WHEN ? = 'Completed' THEN CURRENT_TIMESTAMP ELSE NULL END
               WHERE id = ?""", (new, new, topic_id))


def render_plan(user):
    st.header("🗓️ AI Study Plan")

    goal = get_goal(user["id"])
    if not goal:
        st.warning("Set your study goal first on the **Study Goal** page.")
        return
    if not get_subjects(user["id"]):
        st.warning("Your syllabus is empty. Load the demo syllabus from **Profile**.")
        return

    notice = st.session_state.pop("plan_notice", None)
    if notice:
        st.success(f"Plan version {notice['version']} created with {notice['sessions']} sessions "
                   f"(source: {notice['source']}).")
        for w in notice["warnings"]:
            st.warning(w)

    plan = get_active_plan(user["id"])
    use_ai = st.checkbox("Use AI (Gemini) to build the plan", value=True,
                         help="Untick to use only the rule-based planner.")
    label = "🔄 Re-plan remaining syllabus" if plan else "✨ Generate study plan"
    if st.button(label, type="primary"):
        reason = "Re-planned remaining syllabus" if plan else "Initial plan"
        with st.spinner("Building your plan... (the AI can take 10-30 seconds)"):
            try:
                result = generate_plan(user["id"], reason, use_ai)
            except ValueError as e:
                st.error(str(e))
                return
        st.session_state["plan_notice"] = result
        st.rerun()

    if not plan:
        st.info("No plan yet. Click **Generate study plan**.")
        return

    mark_missed(plan["id"])
    sessions = get_plan_sessions(plan["id"])
    if not sessions:
        st.info("This plan has no sessions.")
        return

    planned_min = sum(s["planned_minutes"] for s in sessions)
    done_min = sum(s["planned_minutes"] for s in sessions if s["status"] == "Completed")
    missed = sum(1 for s in sessions if s["status"] == "Missed")

    st.divider()
    st.caption(f"Plan version {plan['version']} · {plan['reason']}")
    st.info(plan["summary"])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Sessions", len(sessions))
    m2.metric("Planned hours", f"{planned_min / 60:.1f} h")
    m3.metric("Completed hours", f"{done_min / 60:.1f} h")
    m4.metric("Missed sessions", missed)
    if missed:
        st.warning(f"You have {missed} missed session(s). Click **Re-plan remaining syllabus** "
                   "to spread the unfinished topics over the days that are left.")

    first = date.fromisoformat(sessions[0]["planned_date"])
    weeks = {}
    for s in sessions:
        d = date.fromisoformat(s["planned_date"])
        weeks.setdefault((d - first).days // 7 + 1, {}).setdefault(d, []).append(s)

    week_numbers = sorted(weeks)
    tabs = st.tabs([f"Week {w}" for w in week_numbers])
    for tab, w in zip(tabs, week_numbers):
        with tab:
            for d in sorted(weeks[w]):
                day_sessions = weeks[w][d]
                st.markdown(f"**{d.strftime('%A, %d %b %Y')}** · "
                            f"{sum(x['planned_minutes'] for x in day_sessions)} min")
                for s in day_sessions:
                    miss = "⚠️ missed · " if s["status"] == "Missed" else ""
                    st.checkbox(
                        f"{ICONS.get(s['session_type'], '')} {miss}{s['session_type']}: "
                        f"{s['topic']} ({s['unit']}) - {s['planned_minutes']} min",
                        value=(s["status"] == "Completed"),
                        key=f"sess_{s['id']}",
                        on_change=_on_session_toggle,
                        args=(s["id"], s["topic_id"], s["planned_minutes"], s["session_type"]),
                    )

    with st.expander("Table view"):
        df = pd.DataFrame(sessions)[["planned_date", "session_type", "subject", "unit",
                                     "topic", "planned_minutes", "status"]]
        df.columns = ["Date", "Type", "Subject", "Unit", "Topic", "Minutes", "Status"]
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.caption("Ordering, revision and test sessions come from the AI (or the rule-based fallback "
               "if the AI is unavailable). Dates, time limits, validation and saving are plain "
               "Python. Tick a session when you finish it; when all Study sessions of a topic "
               "are ticked, the topic is marked Completed.")
