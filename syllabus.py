"""
syllabus.py - Syllabus page: upload a syllabus file, review what was found, save it, and
edit it (status, difficulty, study time, manual additions).

Reading the file and checking the topics is done by syllabus_ingest.py. Everything here is
limited to the logged-in user. No AI is used for saving, editing or deleting.
"""
import re

import streamlit as st

import database as db
import syllabus_ingest as si

DIFFICULTIES = ["Easy", "Medium", "Hard"]
STATUSES = ["Not Started", "In Progress", "Completed"]
# Screen data that belongs to the old syllabus and must be forgotten when it changes
STALE_KEYS = ("explanations", "active_quiz", "quiz_result", "adapt_result", "plan_notice",
              "syllabus_pending", "confirm_replace")


def clear_stale_state():
    for key in STALE_KEYS:
        st.session_state.pop(key, None)


def _esc(text):
    """Escape characters that Markdown would treat as formatting."""
    return re.sub(r"([\\`*_{}\[\]<>#|~$])", r"\\\1", str(text))


# ---------------------------------------------------------------- database helpers (owner checked)
def _owns_subject(conn, user_id, subject_id):
    return conn.execute("SELECT 1 FROM subjects WHERE id = ? AND user_id = ?",
                        (subject_id, user_id)).fetchone() is not None


def _owns_unit(conn, user_id, unit_id):
    return conn.execute(
        """SELECT 1 FROM units u JOIN subjects s ON u.subject_id = s.id
           WHERE u.id = ? AND s.user_id = ?""", (unit_id, user_id)).fetchone() is not None


def _owns_topic(conn, user_id, topic_id):
    return conn.execute(
        """SELECT 1 FROM topics t JOIN units u ON t.unit_id = u.id
           JOIN subjects s ON u.subject_id = s.id
           WHERE t.id = ? AND s.user_id = ?""", (topic_id, user_id)).fetchone() is not None


def get_tree(user_id):
    """Returns subjects -> units -> topics as nested lists of dicts (this user only)."""
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


def add_unit(user_id, subject_id, name):
    with db.get_conn() as conn:
        if not _owns_subject(conn, user_id, subject_id):
            return
        nxt = conn.execute(
            "SELECT COALESCE(MAX(unit_order), 0) + 1 FROM units WHERE subject_id = ?",
            (subject_id,)).fetchone()[0]
        conn.execute("INSERT INTO units (subject_id, name, unit_order) VALUES (?, ?, ?)",
                     (subject_id, name.strip(), nxt))


def add_topic(user_id, unit_id, name, difficulty, minutes):
    with db.get_conn() as conn:
        if not _owns_unit(conn, user_id, unit_id):
            return
        nxt = conn.execute(
            "SELECT COALESCE(MAX(topic_order), 0) + 1 FROM topics WHERE unit_id = ?",
            (unit_id,)).fetchone()[0]
        conn.execute(
            """INSERT INTO topics (unit_id, name, topic_order, difficulty, est_minutes)
               VALUES (?, ?, ?, ?, ?)""",
            (unit_id, name.strip(), nxt, difficulty, int(minutes)))


def set_status(user_id, topic_id, status):
    with db.get_conn() as conn:
        if not _owns_topic(conn, user_id, topic_id):
            return
        conn.execute(
            """UPDATE topics SET status = ?,
               completed_at = CASE WHEN ? = 'Completed' THEN CURRENT_TIMESTAMP ELSE NULL END
               WHERE id = ?""",
            (status, status, topic_id))


def delete_row(user_id, table, row_id):
    """table is always one of our own fixed names, never user input."""
    with db.get_conn() as conn:
        if table == "subjects" and _owns_subject(conn, user_id, row_id):
            conn.execute("DELETE FROM subjects WHERE id = ?", (row_id,))
        elif table == "units" and _owns_unit(conn, user_id, row_id):
            conn.execute("DELETE FROM units WHERE id = ?", (row_id,))
        elif table == "topics" and _owns_topic(conn, user_id, row_id):
            conn.execute("DELETE FROM topics WHERE id = ?", (row_id,))


def _on_status_change(user_id, topic_id):
    set_status(user_id, topic_id, st.session_state[f"status_{topic_id}"])


# ---------------------------------------------------------------- upload section
def _show_pending(user, pending):
    result = pending["result"]
    struct = result["structure"]
    counts = si.count_structure(struct)

    st.markdown("#### What was found in your file")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Subjects", counts["subjects"])
    c2.metric("Units", counts["units"])
    c3.metric("Topics", counts["topics"])
    c4.metric("Subtopics", counts["subtopics"])
    st.caption(f"Read by: {result['method']}. Only names that are written in your file are kept.")
    for w in result["warnings"]:
        st.warning(w)
    if result["dropped"]:
        with st.expander(f"{len(result['dropped'])} item(s) removed because they are not written "
                         "in your file"):
            for d in result["dropped"][:50]:
                st.markdown(f"- {_esc(d)}")

    for s in struct["subjects"]:
        with st.expander(f"📘 {s['name']}", expanded=True):
            for u in s["units"]:
                st.markdown(f"**{_esc(u['name'])}**")
                for t in u["topics"]:
                    line = f"- {_esc(t['name'])}"
                    if t["subtopics"]:
                        line += " — " + "; ".join(_esc(x) for x in t["subtopics"])
                    st.markdown(line)

    existing = db.count_syllabus(user["id"])["topics"]
    confirmed = True
    if existing:
        st.warning(f"You already have {existing} topic(s). Saving replaces your current syllabus, "
                   "study plan, quiz results and topic levels.")
        confirmed = st.checkbox("Yes, replace my current syllabus", key="confirm_replace")
    if st.button("✅ Save this syllabus", type="primary", disabled=not confirmed):
        db.save_uploaded_syllabus(user["id"], pending["filename"], pending["ext"],
                                  pending["text"], result["method"], struct)
        clear_stale_state()
        st.session_state["syllabus_saved"] = (
            f"Syllabus saved: {counts['topics']} topic(s) in {counts['units']} unit(s). "
            "Next, open Study Goal to set your dates.")
        st.rerun()


def _upload_section(user):
    st.subheader("1. Upload your syllabus")
    st.caption("Supported files: PDF, DOCX or TXT (text-based, up to 5 MB). "
               "Scanned images are not supported.")

    doc = db.get_syllabus_document(user["id"])
    if doc:
        st.success(f"Current syllabus file: **{doc['filename']}** ({doc['file_type'].upper()}), "
                   f"uploaded {doc['uploaded_at']} UTC, read by {doc['method']}.")

    uploaded = st.file_uploader("Choose your syllabus file", key="syllabus_file")
    if uploaded is None:
        st.session_state.pop("syllabus_pending", None)
        return

    try:
        text, info = si.extract_text(uploaded.name, uploaded.getvalue())
    except si.SyllabusError as e:
        st.session_state.pop("syllabus_pending", None)
        st.error(str(e))
        return

    pages = f" from {info['pages']} page(s)" if info["pages"] else ""
    st.caption(f"Read {info['chars']:,} characters{pages} from {uploaded.name}.")
    if info["truncated"]:
        st.warning(f"The file is long, so only the first {si.MAX_CHARS:,} characters were used.")
    with st.expander("Preview the text that was read"):
        st.text(text[:1500])

    use_ai = st.checkbox("Use AI (Gemini) to find units and topics", value=True,
                         key="syllabus_use_ai",
                         help="Untick to use only the simple rule-based reader.")
    if st.button("🔍 Analyse syllabus", type="primary"):
        with st.spinner("Reading your syllabus... (the AI can take 10-30 seconds)"):
            try:
                result = si.analyse_syllabus(text, uploaded.name, use_ai)
            except si.SyllabusError as e:
                st.session_state.pop("syllabus_pending", None)
                st.error(str(e))
                return
        st.session_state["syllabus_pending"] = {
            "filename": uploaded.name, "ext": info["ext"], "text": text, "result": result}

    pending = st.session_state.get("syllabus_pending")
    if pending and pending["filename"] == uploaded.name:
        _show_pending(user, pending)


# ---------------------------------------------------------------- saved syllabus section
def _show_syllabus(user):
    st.subheader("2. Your syllabus")
    tree = get_tree(user["id"])
    if not tree:
        st.info("You have no syllabus yet. Upload a file above.")
        return
    sub_map = db.get_topic_details_map(user["id"])

    for s in tree:
        all_topics = [t for u in s["units"] for t in u["topics"]]
        done = sum(1 for t in all_topics if t["status"] == "Completed")
        with st.expander(f"📘 {s['name']}  ({done}/{len(all_topics)} topics completed)",
                         expanded=True):

            for u in s["units"]:
                with st.container(border=True):
                    h1, h2 = st.columns([8, 1])
                    h1.markdown(f"**{_esc(u['name'])}**")
                    if h2.button("🗑", key=f"del_unit_{u['id']}", help="Delete this unit"):
                        delete_row(user["id"], "units", u["id"])
                        clear_stale_state()
                        st.rerun()

                    u_done = sum(1 for t in u["topics"] if t["status"] == "Completed")
                    if u["topics"]:
                        st.progress(u_done / len(u["topics"]),
                                    text=f"{u_done} of {len(u['topics'])} topics completed")

                    for t in u["topics"]:
                        c1, c2, c3, c4 = st.columns([4, 2, 2.5, 0.8])
                        c1.write(t["name"])
                        if sub_map.get(t["id"]):
                            c1.caption("Subtopics: " + "; ".join(sub_map[t["id"]]))
                        c2.caption(f"{t['difficulty']} · {t['est_minutes']} min")
                        c3.selectbox(
                            "Status", STATUSES,
                            index=STATUSES.index(t["status"]),
                            key=f"status_{t['id']}",
                            label_visibility="collapsed",
                            on_change=_on_status_change,
                            args=(user["id"], t["id"]),
                        )
                        if c4.button("🗑", key=f"del_topic_{t['id']}", help="Delete this topic"):
                            delete_row(user["id"], "topics", t["id"])
                            clear_stale_state()
                            st.rerun()

                    if u["topics"]:
                        with st.expander("⚙️ Edit difficulty and study time for this unit"):
                            with st.form(f"edit_unit_{u['id']}"):
                                for t in u["topics"]:
                                    e1, e2, e3 = st.columns([4, 2, 2])
                                    e1.write(t["name"])
                                    e2.selectbox(
                                        "Difficulty", DIFFICULTIES,
                                        index=(DIFFICULTIES.index(t["difficulty"])
                                               if t["difficulty"] in DIFFICULTIES else 1),
                                        key=f"ed_{t['id']}", label_visibility="collapsed")
                                    e3.number_input(
                                        "Minutes", min_value=15, max_value=240,
                                        value=min(max(int(t["est_minutes"]), 15), 240),
                                        step=15, key=f"em_{t['id']}", label_visibility="collapsed")
                                edited = st.form_submit_button("💾 Save changes")
                            if edited:
                                for t in u["topics"]:
                                    db.update_topic_settings(
                                        user["id"], t["id"],
                                        st.session_state[f"ed_{t['id']}"],
                                        st.session_state[f"em_{t['id']}"])
                                st.rerun()

                    with st.form(f"add_topic_{u['id']}", clear_on_submit=True):
                        f1, f2, f3 = st.columns([3, 1.5, 1.5])
                        t_name = f1.text_input("Add a topic by hand", key=f"tn_{u['id']}")
                        t_diff = f2.selectbox("Difficulty", DIFFICULTIES, index=1,
                                              key=f"td_{u['id']}")
                        t_min = f3.number_input("Study minutes", min_value=15, max_value=240,
                                                value=60, step=15, key=f"tm_{u['id']}")
                        topic_submitted = st.form_submit_button("➕ Add topic to this unit")
                    if topic_submitted:
                        if t_name.strip():
                            add_topic(user["id"], u["id"], t_name, t_diff, t_min)
                            st.rerun()
                        else:
                            st.error("Please enter a topic name.")

            with st.form(f"add_unit_{s['id']}", clear_on_submit=True):
                unit_name = st.text_input("Add a unit by hand", key=f"un_{s['id']}")
                unit_submitted = st.form_submit_button("➕ Add unit to this subject")
            if unit_submitted:
                if unit_name.strip():
                    add_unit(user["id"], s["id"], unit_name)
                    st.rerun()
                else:
                    st.error("Please enter a unit name.")

            if st.button("🗑 Delete this whole subject", key=f"del_subject_{s['id']}"):
                delete_row(user["id"], "subjects", s["id"])
                clear_stale_state()
                st.rerun()


# ---------------------------------------------------------------- page
def render(user):
    st.header("📖 Syllabus")
    st.caption("Upload your own syllabus. My Planner AI works only with the syllabus you provide.")

    saved = st.session_state.pop("syllabus_saved", None)
    if saved:
        st.success(saved)

    _upload_section(user)
    st.divider()
    _show_syllabus(user)

    with st.expander("➕ Add a subject by hand (optional)"):
        with st.form("add_subject_form", clear_on_submit=True):
            subject_name = st.text_input("New subject name")
            submitted = st.form_submit_button("Add subject")
        if submitted:
            if subject_name.strip():
                add_subject(user["id"], subject_name)
                st.rerun()
            else:
                st.error("Please enter a subject name.")
