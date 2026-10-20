"""
dashboard.py - Progress dashboard: cards, progress bars, Plotly charts and status messages.
Every number comes from progress.py / planner.py (plain Python and SQL). No AI is used here.
"""
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

import planner
import progress as prog

GREEN, AMBER, RED, GREY, BLUE = "#2e9d5b", "#e0a21a", "#d64545", "#9aa3ad", "#3b82f6"
LEVEL_COLOR = {"Strong": GREEN, "Moderate": AMBER, "Weak": RED}


def _show(fig):
    """Display a Plotly figure (works across Streamlit versions)."""
    try:
        st.plotly_chart(fig, use_container_width=True)
    except TypeError:
        st.plotly_chart(fig, width="stretch")


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# ================================================================ status messages (rules)
def build_messages(s, pace, weekly, perf_rows, improvements, goal, plan, cap):
    if s["total_topics"] == 0:
        return [("info", "Your syllabus is empty. Add subjects on the **Syllabus** page or load "
                         "the demo syllabus from **Profile**.")]
    msgs = []
    remaining = s["total_topics"] - s["done_topics"]

    if remaining == 0:
        msgs.append(("success", "You have completed every topic in your syllabus."))
    elif not goal:
        msgs.append(("info", "Set your study goal (dates and hours) on the **Study Goal** page so "
                             "the app can track your pace."))
    elif plan is None:
        msgs.append(("info", "Generate your study plan on the **Study Plan** page to start "
                             "tracking weekly targets."))
    else:
        ratio = pace["ratio"]
        if ratio is None:
            msgs.append(("info", "Your plan has just started, so there is nothing to compare yet. "
                                 "Tick off each session on the **Study Plan** page as you finish it."))
        elif ratio >= 0.9:
            msgs.append(("success", f"You are on track: you completed {ratio * 100:.0f}% of the "
                                    "study time that was due so far."))
        elif ratio >= 0.6:
            msgs.append(("warning", f"You are slightly behind your target: you completed "
                                    f"{ratio * 100:.0f}% of the study time that was due. Use the "
                                    "**Adaptive Plan** page to spread the missed work over the days left."))
        else:
            msgs.append(("error", f"You are behind your target: you completed only "
                                  f"{ratio * 100:.0f}% of the study time that was due "
                                  f"({_plural(pace['missed'], 'session')} missed). Open the "
                                  "**Adaptive Plan** page and re-plan the remaining syllabus."))

        if weekly["topics_left"] > 0:
            msgs.append(("info", f"You need to complete {_plural(weekly['topics_left'], 'more topic')} "
                                 "this week to stay on plan."))
        elif weekly["target_min"] > 0:
            msgs.append(("success", "You have finished all the topics planned for this week."))

    if goal and remaining:
        days = s["days_remaining"]
        if days == 0:
            msgs.append(("error", f"The target date has arrived and {_plural(remaining, 'topic')} "
                                  "are still unfinished. Extend the deadline on the **Study Goal** page."))
        elif cap and cap["status"] == "insufficient":
            msgs.append(("warning", "At your current study time the remaining syllabus may not fit "
                                    f"before the deadline: you would need about "
                                    f"{cap['hours_per_day_needed']:.1f} hours per study day, or a later deadline."))
        elif cap and cap["status"] == "tight":
            msgs.append(("info", "Your schedule is tight. Try not to miss sessions."))

    if s["quizzes_taken"] == 0:
        msgs.append(("info", "You have not taken a quiz yet. Take one after studying a topic so the "
                             "app can find your weak topics."))
    else:
        weak = [r["topic"] for r in perf_rows if r["classification"] == "Weak"]
        if weak:
            msgs.append(("warning", "Focus more on these weak topics: " + ", ".join(weak[:5]) + "."))
        better = sorted([r for r in improvements if r["change"] >= 10], key=lambda r: -r["change"])[:2]
        for r in better:
            msgs.append(("success", f"Your score in {r['topic']} has improved: "
                                    f"{r['previous']:.0f}% → {r['last']:.0f}%."))
        worse = sorted([r for r in improvements if r["change"] <= -10], key=lambda r: r["change"])[:2]
        for r in worse:
            msgs.append(("warning", f"Your latest score in {r['topic']} dropped: "
                                    f"{r['previous']:.0f}% → {r['last']:.0f}%. Consider revising it."))
    return msgs


# ================================================================ charts
def chart_status_donut(counts, pct):
    fig = go.Figure(go.Pie(
        labels=["Completed", "In Progress", "Not Started"],
        values=[counts["Completed"], counts["In Progress"], counts["Not Started"]],
        hole=0.62, sort=False, marker=dict(colors=[GREEN, AMBER, GREY]),
        textinfo="value"))
    fig.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10),
                      annotations=[dict(text=f"{pct:.0f}%", x=0.5, y=0.5, showarrow=False,
                                        font=dict(size=28))])
    return fig


def chart_subjects(rows):
    fig = go.Figure(go.Bar(
        x=[r["pct"] for r in rows], y=[r["name"] for r in rows], orientation="h",
        marker_color=BLUE, text=[f"{r['done']}/{r['total']}" for r in rows],
        textposition="auto"))
    fig.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10),
                      xaxis=dict(range=[0, 100], title="% of topics completed"),
                      yaxis=dict(autorange="reversed"))
    return fig


def chart_units(rows):
    df = pd.DataFrame(rows)
    df["label"] = df["unit"].str.split(":").str[0]
    fig = px.bar(df, x="label", y="pct", color="subject", barmode="group",
                 hover_data={"unit": True, "done": True, "total": True, "label": False},
                 labels={"pct": "% completed", "label": "Unit"}, range_y=[0, 100])
    fig.update_layout(height=340, margin=dict(t=10, b=10, l=10, r=10))
    return fig


def chart_quiz_trend(history):
    fig = go.Figure(go.Scatter(
        x=list(range(1, len(history) + 1)), y=[h["percentage"] for h in history],
        mode="lines+markers", marker=dict(size=9, color=BLUE), line=dict(color=BLUE),
        text=[h["topic"] for h in history],
        hovertemplate="Attempt %{x}: %{text}<br>%{y:.0f}%<extra></extra>"))
    fig.add_hline(y=75, line_dash="dot", line_color=GREEN, annotation_text="Strong (75%)")
    fig.add_hline(y=50, line_dash="dot", line_color=RED, annotation_text="Weak (below 50%)")
    fig.update_layout(height=320, margin=dict(t=10, b=10, l=10, r=10),
                      xaxis=dict(title="Quiz attempt", dtick=1),
                      yaxis=dict(title="Score (%)", range=[0, 105]))
    return fig


def chart_topic_levels(rows):
    fig = go.Figure(go.Bar(
        x=[r["last_percentage"] for r in rows], y=[r["topic"] for r in rows], orientation="h",
        marker_color=[LEVEL_COLOR.get(r["classification"], GREY) for r in rows],
        text=[f"{r['last_percentage']:.0f}%" for r in rows], textposition="auto",
        customdata=[[r["classification"], r["attempts"]] for r in rows],
        hovertemplate="%{y}<br>Last score: %{x:.0f}%<br>Level: %{customdata[0]}"
                      "<br>Attempts: %{customdata[1]}<extra></extra>"))
    fig.update_layout(height=max(260, 42 * len(rows) + 60),
                      margin=dict(t=10, b=10, l=10, r=10),
                      xaxis=dict(range=[0, 100], title="Last quiz score (%)"),
                      yaxis=dict(autorange="reversed"))
    return fig


def chart_weekly(series):
    labels = [f"Week of {w['week_start'].strftime('%d %b')}" for w in series]
    fig = go.Figure([
        go.Bar(name="Planned", x=labels, y=[round(w["planned_h"], 1) for w in series],
               marker_color=GREY),
        go.Bar(name="Completed", x=labels, y=[round(w["completed_h"], 1) for w in series],
               marker_color=GREEN)])
    fig.update_layout(barmode="group", height=320, margin=dict(t=10, b=10, l=10, r=10),
                      yaxis=dict(title="Hours"))
    return fig


# ================================================================ page
def render(user):
    uid = user["id"]
    st.header(f"📊 Dashboard - {user['name']}")

    plan = planner.get_active_plan(uid)
    if plan:
        planner.mark_missed(plan["id"])
    goal = planner.get_goal(uid)

    s = prog.get_summary(uid)
    if s["total_topics"] == 0:
        st.info("Your syllabus is empty. Go to **Profile** and click **Load demo syllabus**, "
                "or build your own on the **Syllabus** page.")
        return

    counts = prog.status_counts(uid)
    pace = prog.pace(uid)
    weekly = prog.weekly_stats(uid)
    hours = prog.study_hours(uid)
    perf_rows = prog.topic_performance_rows(uid)
    improvements = prog.improvement_rows(uid)
    cap = (planner.calculate_capacity(goal, planner.get_remaining_workload(uid))
           if goal else None)

    if goal:
        st.caption(f"Target date: {goal['target_date']} · "
                   f"{s['days_remaining']} day(s) remaining")

    # ---- status messages
    for level, text in build_messages(s, pace, weekly, perf_rows, improvements, goal, plan, cap):
        getattr(st, level)(text)

    # ---- overall progress bar + cards
    st.write(f"**Overall syllabus completion: {s['completion_pct']:.0f}%**")
    st.progress(min(s["completion_pct"] / 100, 1.0))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Topics completed", f"{s['done_topics']} / {s['total_topics']}")
    c2.metric("Topics remaining", s["total_topics"] - s["done_topics"])
    c3.metric("Units completed", f"{s['done_units']} / {s['total_units']}")
    c4.metric("Days remaining",
              s["days_remaining"] if s["days_remaining"] is not None else "Not set")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Average quiz score",
              f"{s['avg_quiz_pct']:.0f}%" if s["avg_quiz_pct"] is not None else "No quiz yet")
    c6.metric("Quizzes taken", s["quizzes_taken"])
    c7.metric("Planned study hours", f"{hours['planned_min'] / 60:.1f} h")
    c8.metric("Completed study hours", f"{hours['completed_min'] / 60:.1f} h")

    # ---- weekly target
    if weekly["target_min"] > 0:
        st.write(f"**This week ({weekly['week_start'].strftime('%d %b')} – "
                 f"{weekly['week_end'].strftime('%d %b')})**")
        st.progress(min(weekly["done_min"] / weekly["target_min"], 1.0),
                    text=f"Weekly target: {weekly['done_min'] / 60:.1f} of "
                         f"{weekly['target_min'] / 60:.1f} hours completed")
    else:
        st.caption("No study sessions are planned for this week yet.")

    # ---- weak / strong topics
    if perf_rows:
        weak = [r for r in perf_rows if r["classification"] == "Weak"]
        moderate = [r for r in perf_rows if r["classification"] == "Moderate"]
        strong = [r for r in perf_rows if r["classification"] == "Strong"]
        w1, w2, w3 = st.columns(3)
        with w1:
            st.markdown("##### 🔴 Weak topics")
            for r in weak:
                st.write(f"{r['topic']} ({r['last_percentage']:.0f}%)")
            if not weak:
                st.caption("None")
        with w2:
            st.markdown("##### 🟡 Moderate topics")
            for r in moderate:
                st.write(f"{r['topic']} ({r['last_percentage']:.0f}%)")
            if not moderate:
                st.caption("None")
        with w3:
            st.markdown("##### 🟢 Strong topics")
            for r in strong:
                st.write(f"{r['topic']} ({r['last_percentage']:.0f}%)")
            if not strong:
                st.caption("None")

    # ---- charts
    st.divider()
    tab1, tab2, tab3 = st.tabs(["📚 Syllabus progress", "🧪 Quiz performance", "🗓️ Weekly study"])

    with tab1:
        left, right = st.columns(2)
        with left:
            st.markdown("**Topic status**")
            _show(chart_status_donut(counts, s["completion_pct"]))
        with right:
            st.markdown("**Subject-wise completion**")
            _show(chart_subjects(prog.subject_progress(uid)))
        st.markdown("**Unit-wise completion**")
        _show(chart_units(prog.unit_progress(uid)))

    with tab2:
        history = prog.quiz_history(uid)
        if not history:
            st.info("No quiz results yet. Take a quiz on the **Quiz** page.")
        else:
            left, right = st.columns(2)
            with left:
                st.markdown("**Quiz score trend**")
                _show(chart_quiz_trend(history))
            with right:
                st.markdown("**Latest score per topic**")
                _show(chart_topic_levels(perf_rows))

    with tab3:
        series = prog.weekly_series(uid)
        if not series:
            st.info("No study plan yet. Generate one on the **Study Plan** page.")
        else:
            st.markdown("**Planned vs completed study hours per week**")
            _show(chart_weekly(series))

    st.caption("All numbers on this page are calculated from your saved data using plain Python and "
               "SQL. Weak, moderate and strong levels come from your quiz scores (average of the "
               "last 2 attempts). Planned hours = completed hours + the time still planned. No AI is "
               "used on this page, and the messages are simple rules, not predictions.")
