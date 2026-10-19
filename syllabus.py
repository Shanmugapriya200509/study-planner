"""
syllabus.py - Syllabus Management page: Subject -> Unit -> Topic.
Database operations here are plain SQL; no AI is used on this page.
"""
import streamlit as st

import database as db

DIFFICULTIES = ["Easy", "Medium", "Hard"]
STATUSES = ["Not Started", "In Progress", "Completed"]


# ---------------------------------------------------------------- database helpers
def get_tree(user_id):
    """Returns subjects -> units -> topics as nested lists of dicts."""
    with db.get_conn() as conn:
        subjects = [dict(r) for r in conn.execute(
            "SELECT * FROM subjects WHERE user_id = ? ORDER BY id", (user_id,))]
        for s in subjects:
            s["units"] = [dict(r) for r in conn.execute(
                "SELECT * FROM units WHERE subject_id = ? ORDER BY unit_order, id", (s["id"],))]
            for u in s["units"]:
                u["topics"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM topics WHERE unit_id = ? ORDER BY topic_order, id", (u["id"],))]
    return subjects


def add_subject(user_id, name):
    with db.get_conn() as conn:
        conn.execute("INSERT INTO subjects (user_id, name, priority) VALUES (?, ?, 2)",
                     (user_id, name.strip()))


def add_unit(subject_id, name):
    with db.get_conn() as conn:
        nxt = conn.execute(
            "SELECT COALESCE(MAX(unit_order), 0) + 1 FROM units WHERE subject_id = ?",
            (subject_id,)).fetchone()[0]
        conn.execute("INSERT INTO units (subject_id, name, unit_order) VALUES (?, ?, ?)",
                     (subject_id, name.strip(), nxt))


def add_topic(unit_id, name, difficulty, minutes):
    with db.get_conn() as conn:
        nxt = conn.execute(
            "SELECT COALESCE(MAX(topic_order), 0) + 1 FROM topics WHERE unit_id = ?",
            (unit_id,)).fetchone()[0]
        conn.execute(
            """INSERT INTO topics (unit_id, name, topic_order, difficulty, est_minutes)
               VALUES (?, ?, ?, ?, ?)""",
            (unit_id, name.strip(), nxt, difficulty, int(minutes)))


def set_status(topic_id, status):
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE topics SET status = ?,
               completed_at = CASE WHEN ? = 'Completed' THEN CURRENT_TIMESTAMP ELSE NULL END
               WHERE id = ?""",
            (status, status, topic_id))


def delete_row(table, row_id):
    """table is always one of our own fixed names, never user input."""
    assert table in ("subjects", "units", "topics")
    with db.get_conn() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))


def _on_status_change(topic_id):
    set_status(topic_id, st.session_state[f"status_{topic_id}"])


# ---------------------------------------------------------------- page
def render(user):
    st.header("📖 Syllabus Management")
    st.caption("Build your syllabus as Subject → Unit → Topic, and mark each topic's status.")

    # ---- add subject
    with st.form("add_subject_form", clear_on_submit=True):
        subject_name = st.text_input("New subject name", placeholder="e.g. Operating Systems")
        submitted = st.form_submit_button("➕ Add subject")
    if submitted:
        if subject_name.strip():
            add_subject(user["id"], subject_name)
            st.rerun()
        else:
            st.error("Please enter a subject name.")

    tree = get_tree(user["id"])
    if not tree:
        st.info("No subjects yet. Add one above, or load the demo syllabus from **Profile**.")
        return

    # ---- show syllabus
    for s in tree:
        all_topics = [t for u in s["units"] for t in u["topics"]]
        done = sum(1 for t in all_topics if t["status"] == "Completed")
        with st.expander(f"📘 {s['name']}  ({done}/{len(all_topics)} topics completed)",
                         expanded=True):

            for u in s["units"]:
                with st.container(border=True):
                    h1, h2 = st.columns([8, 1])
                    h1.markdown(f"**{u['name']}**")
                    if h2.button("🗑", key=f"del_unit_{u['id']}", help="Delete this unit"):
                        delete_row("units", u["id"])
                        st.rerun()

                    u_done = sum(1 for t in u["topics"] if t["status"] == "Completed")
                    if u["topics"]:
                        st.progress(u_done / len(u["topics"]),
                                    text=f"{u_done} of {len(u['topics'])} topics completed")

                    for t in u["topics"]:
                        c1, c2, c3, c4 = st.columns([4, 2, 2.5, 0.8])
                        c1.write(t["name"])
                        c2.caption(f"{t['difficulty']} · {t['est_minutes']} min")
                        c3.selectbox(
                            "Status", STATUSES,
                            index=STATUSES.index(t["status"]),
                            key=f"status_{t['id']}",
                            label_visibility="collapsed",
                            on_change=_on_status_change,
                            args=(t["id"],),
                        )
                        if c4.button("🗑", key=f"del_topic_{t['id']}", help="Delete this topic"):
                            delete_row("topics", t["id"])
                            st.rerun()

                    with st.form(f"add_topic_{u['id']}", clear_on_submit=True):
                        f1, f2, f3 = st.columns([3, 1.5, 1.5])
                        t_name = f1.text_input("New topic name", key=f"tn_{u['id']}")
                        t_diff = f2.selectbox("Difficulty", DIFFICULTIES, index=1,
                                              key=f"td_{u['id']}")
                        t_min = f3.number_input("Study minutes", min_value=15, max_value=240,
                                                value=60, step=15, key=f"tm_{u['id']}")
                        topic_submitted = st.form_submit_button("➕ Add topic to this unit")
                    if topic_submitted:
                        if t_name.strip():
                            add_topic(u["id"], t_name, t_diff, t_min)
                            st.rerun()
                        else:
                            st.error("Please enter a topic name.")

            # ---- add unit to this subject
            with st.form(f"add_unit_{s['id']}", clear_on_submit=True):
                unit_name = st.text_input("New unit name", key=f"un_{s['id']}",
                                          placeholder="e.g. Unit 6: Wireless Networks")
                unit_submitted = st.form_submit_button("➕ Add unit to this subject")
            if unit_submitted:
                if unit_name.strip():
                    add_unit(s["id"], unit_name)
                    st.rerun()
                else:
                    st.error("Please enter a unit name.")

            if st.button("🗑 Delete this whole subject", key=f"del_subject_{s['id']}"):
                delete_row("subjects", s["id"])
                st.rerun()
