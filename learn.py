"""
learn.py - AI topic explanation page.

Gemini (LLM) writes the explanation. Plain Python checks that the reply is complete and
retries once if it is not. The explanation is kept only for the current browser session
(it is not stored in the database).
"""
import streamlit as st

import ai_service as ai
import database as db
import prompts
from syllabus import get_tree

LEVELS = ["Beginner", "Intermediate", "Advanced"]


# ---------------------------------------------------------------- database helpers
def get_topic_context(topic_id):
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT t.id AS topic_id, t.name AS topic, t.difficulty, t.status,
                      u.name AS unit, s.name AS subject,
                      COALESCE(tp.classification, 'Untested') AS performance
               FROM topics t
               JOIN units u ON t.unit_id = u.id
               JOIN subjects s ON u.subject_id = s.id
               LEFT JOIN topic_performance tp ON tp.topic_id = t.id
               WHERE t.id = ?""",
            (topic_id,),
        ).fetchone()
    return dict(row) if row else None


def mark_in_progress(topic_id):
    with db.get_conn() as conn:
        conn.execute("UPDATE topics SET status = 'In Progress' "
                     "WHERE id = ? AND status = 'Not Started'", (topic_id,))


def mark_completed(topic_id):
    with db.get_conn() as conn:
        conn.execute("UPDATE topics SET status = 'Completed', completed_at = CURRENT_TIMESTAMP "
                     "WHERE id = ?", (topic_id,))


# ---------------------------------------------------------------- AI + validation
def _as_list(value):
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def validate_explanation(data):
    """Returns a clean dict or raises ValueError if the AI reply is incomplete."""
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    out = {
        "simple_explanation": str(data.get("simple_explanation", "")).strip(),
        "key_concepts": _as_list(data.get("key_concepts")),
        "important_points": _as_list(data.get("important_points")),
        "example": str(data.get("example", "")).strip(),
        "summary": str(data.get("summary", "")).strip(),
        "exam_questions": _as_list(data.get("exam_questions")),
    }
    if len(out["simple_explanation"]) < 40 or not out["key_concepts"] or not out["summary"]:
        raise ValueError("the reply is incomplete")
    return out


def explain_topic(ctx, level):
    """Calls Gemini; retries once if the reply is incomplete. Raises ai.AIError on failure."""
    prompt = prompts.build_explain_prompt(ctx, level)
    for _attempt in range(2):
        data = ai.generate_json(prompt, system=prompts.EXPLAIN_SYSTEM, temperature=0.4)
        try:
            return validate_explanation(data)
        except ValueError:
            continue
    raise ai.AIError("The AI reply was incomplete twice. Please click the button again.")


# ---------------------------------------------------------------- page
def _show(e):
    st.subheader("Simple explanation")
    st.write(e["simple_explanation"])

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Key concepts")
        for item in e["key_concepts"]:
            st.markdown(f"- {item}")
    with c2:
        st.subheader("Important points")
        for item in e["important_points"]:
            st.markdown(f"- {item}")

    if e["example"]:
        st.subheader("Example")
        st.info(e["example"])

    st.subheader("Summary")
    st.success(e["summary"])

    if e["exam_questions"]:
        st.subheader("Possible exam questions")
        for i, q in enumerate(e["exam_questions"], start=1):
            st.markdown(f"{i}. {q}")


def render(user):
    st.header("🧠 Learn a Topic")

    tree = [s for s in get_tree(user["id"]) if any(u["topics"] for u in s["units"])]
    if not tree:
        st.warning("You have no topics yet. Add them on the **Syllabus** page "
                   "or load the demo syllabus from **Profile**.")
        return
    if not ai.has_api_key():
        st.error("Gemini API key not found. Add GEMINI_API_KEY in Streamlit Cloud: "
                 "Settings -> Secrets.")
        return

    subjects = {s["id"]: s for s in tree}
    c1, c2, c3 = st.columns(3)
    sid = c1.selectbox("Subject", list(subjects), format_func=lambda i: subjects[i]["name"])
    units = {u["id"]: u for u in subjects[sid]["units"] if u["topics"]}
    uid = c2.selectbox("Unit", list(units), format_func=lambda i: units[i]["name"])
    topics = {t["id"]: t for t in units[uid]["topics"]}
    tid = c3.selectbox("Topic", list(topics),
                       format_func=lambda i: f"{topics[i]['name']}  ({topics[i]['status']})")
    level = st.radio("Explanation level", LEVELS, index=1, horizontal=True)

    cache = st.session_state.setdefault("explanations", {})
    key = (tid, level)

    label = "🔄 Regenerate explanation" if key in cache else "✨ Explain topic"
    if st.button(label, type="primary"):
        ctx = get_topic_context(tid)
        with st.spinner("Gemini is preparing your explanation..."):
            try:
                cache[key] = explain_topic(ctx, level)
                mark_in_progress(tid)
                st.rerun()
            except ai.AIError as e:
                st.error(str(e))

    if key in cache:
        st.divider()
        _show(cache[key])
        st.divider()
        a1, a2 = st.columns(2)
        if a1.button("✅ Mark this topic as completed"):
            mark_completed(tid)
            st.success("Topic marked as completed.")
            st.rerun()
        a2.caption("When you feel ready, take a quiz on this topic (the quiz page comes in the next step).")
        st.caption("This explanation was written by an AI model and may contain mistakes. "
                   "Check important details against your textbook or class notes.")
    else:
        st.info("Choose a topic and level, then click **Explain topic**.")
