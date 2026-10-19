"""
adaptive.py - Adaptive re-planning after quiz results and missed sessions.

Who does what:
  * Reading quiz results / missed sessions, deciding why we re-plan     -> plain Python
  * Re-ordering topics, revision and test sessions, written explanation  -> Gemini LLM
  * Guarantee rules (weak topics always get revision, strong topics none),
    fitting the plan into the available time, dates, saving,
    old-vs-new comparison                                                -> plain Python
  * If the AI fails: a complete rule-based adaptive plan                 -> plain Python
"""
from datetime import date

import pandas as pd
import streamlit as st

import ai_service as ai
import database as db
import planner
import prompts
from quiz import ICON

TYPES = ("Study", "Revision", "Test")


# ================================================================ reading the evidence
def get_adapt_context(user_id, plan):
    """Quiz results taken after the plan was created, and sessions that were missed."""
    with db.get_conn() as conn:
        quizzes = [dict(r) for r in conn.execute(
            """SELECT t.id AS topic_id, t.name AS topic, qr.score, qr.total, qr.percentage,
                      COALESCE(tp.classification, 'Untested') AS level
               FROM quiz_results qr
               JOIN topics t ON qr.topic_id = t.id
               LEFT JOIN topic_performance tp ON tp.topic_id = t.id
               WHERE qr.user_id = ? AND qr.taken_at > ?
               ORDER BY qr.id""", (user_id, plan["created_at"]))]
        missed_rows = conn.execute(
            """SELECT ss.topic_id, t.name AS topic, COUNT(*) AS n,
                      COALESCE(SUM(ss.planned_minutes), 0) AS minutes
               FROM study_sessions ss
               JOIN topics t ON ss.topic_id = t.id
               WHERE ss.plan_id = ? AND ss.status = 'Missed' AND t.status != 'Completed'
               GROUP BY ss.topic_id, t.name""", (plan["id"],)).fetchall()
    missed = {r["topic_id"]: {"topic": r["topic"], "sessions": r["n"], "minutes": r["minutes"]}
              for r in missed_rows}
    return {"quizzes": quizzes, "missed": missed}


def decide_reason(actx):
    has_quiz, has_missed = bool(actx["quizzes"]), bool(actx["missed"])
    if has_quiz and has_missed:
        return "Adapted after quiz results and missed sessions"
    if has_quiz:
        return "Adapted after quiz results"
    if has_missed:
        return "Recalculated after missed sessions"
    return "Re-planned (no new quiz results or missed sessions)"


def simulate_missed(plan_id, n=3):
    """DEMO HELPER: marks the next n planned sessions as missed so that the
    missed-session logic can be shown without waiting for real days to pass."""
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE study_sessions SET status = 'Missed' WHERE id IN (
                 SELECT id FROM study_sessions WHERE plan_id = ? AND status = 'Planned'
                 ORDER BY planned_date, id LIMIT ?)""", (plan_id, n))


# ================================================================ rule-based guarantees
def _round5(minutes, cap=120):
    return max(15, min(cap, int(round(minutes / 5) * 5)))


def _insert_revision(items, topic_id, minutes, reason):
    pos = None
    for k, it in enumerate(items):
        if it["topic_id"] == topic_id and it["session_type"] == "Study":
            pos = k
    new = planner._item(topic_id, "Revision", minutes, reason)
    if pos is None:
        items.append(new)
    else:
        items.insert(pos + 1, new)


def enforce_rules(items, topics, scale):
    """Guarantees applied to every plan, whatever the AI returned:
       - Strong topics: no Revision
       - Weak topics: always at least one Revision (45 min)
       - Moderate topics: always at least one Revision (30 min)
    scale=True (rule-based path only) also changes Study minutes:
       Strong x0.7, Weak x1.2."""
    perf = {t["topic_id"]: t["performance"] for t in topics}
    out = []
    for it in items:
        it = dict(it)
        level = perf.get(it["topic_id"], "Untested")
        if it["session_type"] == "Revision" and level == "Strong":
            continue
        if scale and it["session_type"] == "Study":
            if level == "Strong":
                it["minutes"] = _round5(it["minutes"] * 0.7)
            elif level == "Weak":
                it["minutes"] = _round5(it["minutes"] * 1.2)
        out.append(it)

    have = {i["topic_id"] for i in out if i["session_type"] == "Revision"}
    for t in topics:
        if t["topic_id"] in have:
            continue
        if t["performance"] == "Weak":
            _insert_revision(out, t["topic_id"], 45, "Weak in recent quiz")
        elif t["performance"] == "Moderate":
            _insert_revision(out, t["topic_id"], 30, "Moderate in recent quiz")
    return out


def priority_first(items, topics, missed):
    """Rule-based ordering: weak topics first, then topics with missed sessions, then the rest.
    sorted() is stable, so the syllabus order is kept inside each group."""
    perf = {t["topic_id"]: t["performance"] for t in topics}

    def group(item):
        if item["session_type"] == "Test":
            return 2
        if perf.get(item["topic_id"]) == "Weak":
            return 0
        if item["topic_id"] in missed:
            return 1
        return 2

    return sorted(items, key=group)


# ================================================================ old vs new comparison
def snapshot(plan_id):
    """Remaining (not completed) minutes per topic in a plan, split by session type."""
    out = {}
    for s in planner.get_plan_sessions(plan_id):
        if s["status"] == "Completed":
            continue
        d = out.setdefault(s["topic_id"], {"topic": s["topic"], "Study": 0, "Revision": 0, "Test": 0})
        d[s["session_type"]] += s["planned_minutes"]
    return out


def _parts(d):
    if not d:
        return 0, 0, 0
    return d["Study"], d["Revision"], d["Test"]


def diff_plans(before, after, perf):
    rows = []
    for tid in set(before) | set(after):
        b, a = before.get(tid), after.get(tid)
        name = (a or b)["topic"]
        o_study, o_rev, o_test = _parts(b)
        n_study, n_rev, n_test = _parts(a)
        old_total = o_study + o_rev + o_test
        new_total = n_study + n_rev + n_test

        notes = []
        if a is None:
            notes.append("completed since last plan" if tid not in perf
                         else "did not fit before the deadline")
        elif b is None:
            notes.append("new in this plan")
        else:
            if n_rev > o_rev:
                notes.append("more revision")
            elif n_rev < o_rev:
                notes.append("less revision")
            if n_study > o_study:
                notes.append("longer study")
            elif n_study < o_study:
                notes.append("shorter study")
            if n_test != o_test:
                notes.append("test changed")

        if new_total == old_total and not notes:
            continue
        level = perf.get(tid, "Completed")
        rows.append({"Topic": name, "Level": f"{ICON.get(level, '')} {level}",
                     "Old plan (min)": old_total, "New plan (min)": new_total,
                     "Change (min)": new_total - old_total, "What changed": ", ".join(notes)})
    rows.sort(key=lambda r: -abs(r["Change (min)"]))
    return rows


# ================================================================ generation
def adapt_plan(user_id, use_ai=True):
    """Builds and saves a new active plan from the remaining topics, using quiz results and
    missed sessions. Returns a result dict. Raises ValueError for problems the student can fix."""
    plan = planner.get_active_plan(user_id)
    if plan is None:
        raise ValueError("Create a study plan first on the Study Plan page.")
    goal = planner.get_goal(user_id)
    if not goal:
        raise ValueError("Set your study goal first on the Study Goal page.")
    topics = planner.get_planning_topics(user_id)
    if not topics:
        raise ValueError("There are no unfinished topics left to plan. Well done!")

    start = max(date.fromisoformat(goal["start_date"]), date.today())
    target = date.fromisoformat(goal["target_date"])
    study_days = goal["study_days"].split(",") if goal["study_days"] else []
    dates = planner.study_dates(start, target, study_days) if target >= start else []
    if not dates:
        raise ValueError("No study days are left before the target date. "
                         "Extend the deadline on the Study Goal page.")
    minutes_per_day = int(round(goal["hours_per_day"] * 60))
    capacity = len(dates) * minutes_per_day
    limit = int(capacity * 0.95)

    planner.mark_missed(plan["id"])
    actx = get_adapt_context(user_id, plan)
    reason = decide_reason(actx)
    before = snapshot(plan["id"])
    perf = {t["topic_id"]: t["performance"] for t in topics}

    warnings, items, summary, source = [], None, "", "Rule-based"

    if use_ai and ai.has_api_key():
        ctx = {
            "today": date.today().isoformat(), "target": goal["target_date"],
            "days": len(dates), "minutes_per_day": minutes_per_day,
            "capacity": capacity, "limit": limit,
            "missed_total": sum(m["sessions"] for m in actx["missed"].values()),
            "previous_summary": (plan["summary"] or "")[:300],
            "quizzes": [{"topic": q["topic"], "percentage": round(q["percentage"]),
                         "level": q["level"]} for q in actx["quizzes"]][-12:],
            "topics": [{
                "topic_id": t["topic_id"], "subject": t["subject"], "unit": t["unit"],
                "topic": t["topic"], "difficulty": t["difficulty"],
                "est_minutes": t["est_minutes"], "status": t["status"],
                "subject_priority": t["priority"], "performance": t["performance"],
                "last_quiz_pct": t["last_pct"],
                "missed_sessions": actx["missed"].get(t["topic_id"], {}).get("sessions", 0),
            } for t in topics],
        }
        try:
            data = ai.generate_json(prompts.build_adapt_prompt(ctx), system=prompts.ADAPT_SYSTEM)
            items, summary = planner.validate_ai_items(data, topics)
            items = enforce_rules(items, topics, scale=False)
            source = "AI (Gemini)"
        except ai.AIError as e:
            warnings.append(f"AI unavailable: {e} A rule-based adaptive plan was used instead.")
        except ValueError as e:
            warnings.append(f"The AI reply could not be used ({e}). "
                            "A rule-based adaptive plan was used instead.")
    elif use_ai:
        warnings.append("No Gemini API key found, so a rule-based plan was used.")

    if items is None:
        items = planner.rule_based_items(topics)
        items = enforce_rules(items, topics, scale=True)
        items = priority_first(items, topics, actx["missed"])
        summary = ("Rule-based adaptation: topics where you scored low were moved to the front with "
                   "longer study time and a revision session, topics with missed sessions came "
                   "next, and topics where you scored well got shorter study time and no "
                   "repeated revision.")

    items = planner.ensure_all_topics_have_study(items, topics)
    items, trimmed = planner.fit_to_capacity(items, limit)
    if trimmed:
        warnings.append("Your available time is short, so some revision/test sessions were "
                        "removed or shortened. Consider extending the deadline or adding hours.")
    if not items:
        raise ValueError("Not enough study time to schedule anything.")

    sessions = planner.schedule_items(items, dates, minutes_per_day)
    scheduled = {s["topic_id"] for s in sessions if s["session_type"] == "Study"}
    missing = [t["topic"] for t in topics if t["topic_id"] not in scheduled]
    if missing:
        warnings.append(f"{len(missing)} topic(s) did not fit before the deadline: "
                        + ", ".join(missing[:6]) + ("..." if len(missing) > 6 else ""))

    plan_id, version = planner.save_plan(user_id, reason, f"[{source}] {summary}", sessions)
    after = snapshot(plan_id)
    return {"plan_id": plan_id, "version": version, "source": source, "reason": reason,
            "summary": summary, "warnings": warnings, "sessions": len(sessions),
            "changes": diff_plans(before, after, perf)}


# ================================================================ page
def show_changes(res):
    st.success(f"Plan version {res['version']} created with {res['sessions']} sessions "
               f"({res['reason']}; source: {res['source']}).")
    for w in res["warnings"]:
        st.warning(w)
    if res["summary"]:
        st.info(res["summary"])

    st.subheader("What changed")
    if res["changes"]:
        st.dataframe(pd.DataFrame(res["changes"]), use_container_width=True, hide_index=True)
    else:
        st.write("The new plan has the same remaining minutes per topic as the old one.")
    st.caption("The table is calculated by the app by comparing the remaining planned minutes of "
               "the old and new plan, so its numbers are exact. The text above is written by the "
               "AI as an explanation. Open **Study Plan** to see the new schedule.")


def render(user):
    st.header("🔄 Adaptive Study Plan")

    plan = planner.get_active_plan(user["id"])
    if plan is None:
        st.warning("Create a study plan first on the **Study Plan** page.")
        return

    planner.mark_missed(plan["id"])
    topics = planner.get_planning_topics(user["id"])
    actx = get_adapt_context(user["id"], plan)
    weak = [t for t in topics if t["performance"] == "Weak"]
    strong = [t for t in topics if t["performance"] == "Strong"]
    missed_total = sum(m["sessions"] for m in actx["missed"].values())

    st.subheader("What the system found since your last plan")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("New quiz results", len(actx["quizzes"]))
    c2.metric("Missed sessions", missed_total)
    c3.metric("Weak topics (unfinished)", len(weak))
    c4.metric("Strong topics (unfinished)", len(strong))

    if weak:
        st.error("🔴 Weak: " + ", ".join(f"{t['topic']} ({(t['last_pct'] or 0):.0f}%)"
                                         for t in weak) + " → more time and revision.")
    if strong:
        st.success("🟢 Strong: " + ", ".join(t["topic"] for t in strong)
                   + " → shorter study time, no repeated revision.")
    if actx["missed"]:
        st.warning("⚠️ Missed: " + ", ".join(f"{m['topic']} ({m['sessions']})"
                                            for m in actx["missed"].values())
                   + " → rescheduled before untouched topics.")
    if actx["quizzes"]:
        qdf = pd.DataFrame([{"Topic": q["topic"], "Score": f"{q['score']} / {q['total']}",
                             "Percentage": f"{q['percentage']:.0f}%",
                             "Level": f"{ICON.get(q['level'], '')} {q['level']}"}
                            for q in actx["quizzes"]])
        st.dataframe(qdf, use_container_width=True, hide_index=True)

    st.caption(f"Reason for adapting: {decide_reason(actx)}")
    if not actx["quizzes"] and not actx["missed"]:
        st.info("Nothing has changed since your last plan. Take a quiz on the **Quiz** page "
                "first, so there is something to adapt to.")

    use_ai = st.checkbox("Use AI (Gemini) to adapt the plan", value=True, key="adapt_use_ai",
                         help="Untick to use only the rule-based adaptation.")
    if st.button("🔄 Adapt my study plan", type="primary"):
        with st.spinner("Adapting your plan... (the AI can take 10-30 seconds)"):
            try:
                res = adapt_plan(user["id"], use_ai)
            except ValueError as e:
                st.error(str(e))
                return
        st.session_state["adapt_result"] = res
        st.rerun()

    with st.expander("Demo helper: simulate missed sessions"):
        st.write("Real sessions only become 'missed' after their date has passed. For a demonstration "
                 "you can mark the next 3 planned sessions as missed, then adapt the plan.")
        if st.button("Mark next 3 planned sessions as missed"):
            simulate_missed(plan["id"], 3)
            st.rerun()

    res = st.session_state.get("adapt_result")
    if res:
        st.divider()
        show_changes(res)
