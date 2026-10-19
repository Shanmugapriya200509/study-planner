"""
quiz.py - AI quiz generation, scoring and performance analysis.

Who does what:
  * Writing questions and explanations            -> Gemini LLM (ai_service.py, prompts.py)
  * Validating the quiz, shuffling options         -> plain Python
  * Scoring, percentage, Strong/Moderate/Weak      -> plain Python (threshold rules, no AI)
  * Storing quizzes, answers, results              -> SQLite
"""
import random
import re

import pandas as pd
import streamlit as st

import ai_service as ai
import database as db
import prompts
from learn import get_topic_context, mark_completed
from syllabus import get_tree

LEVELS = ["Beginner", "Intermediate", "Advanced"]
LETTERS = ["A", "B", "C", "D"]
STRONG_MIN = 75        # average of the last 2 attempts >= 75  -> Strong
MODERATE_MIN = 50      # >= 50 and < 75 -> Moderate, below 50 -> Weak
ICON = {"Strong": "🟢", "Moderate": "🟡", "Weak": "🔴", "Untested": "⚪"}


# ================================================================ rule-based logic
def classify(percentage):
    if percentage >= STRONG_MIN:
        return "Strong"
    if percentage >= MODERATE_MIN:
        return "Moderate"
    return "Weak"


def _strip_prefix(option):
    """Remove leading 'A)' / 'B.' style labels the AI sometimes adds."""
    return re.sub(r"^\s*[A-Da-d][\)\.:]\s+", "", option).strip()


def validate_quiz(data, n):
    """Check the AI reply, shuffle each question's options. Returns a list of clean
    questions or raises ValueError."""
    raw = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise ValueError("the reply has no 'questions' list")

    clean, seen = [], set()
    for q in raw:
        try:
            text = str(q["question"]).strip()
            options = [_strip_prefix(str(o)) for o in q["options"]]
            correct = int(q["correct_index"])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or text.lower() in seen:
            continue
        if len(options) != 4 or any(not o for o in options):
            continue
        if len({o.lower() for o in options}) != 4 or not 0 <= correct <= 3:
            continue
        order = [0, 1, 2, 3]
        random.shuffle(order)          # stops the correct answer always sitting at the same place
        clean.append({
            "question": text,
            "options": [options[i] for i in order],
            "correct": LETTERS[order.index(correct)],
            "explanation": str(q.get("explanation", "")).strip(),
        })
        seen.add(text.lower())

    if len(clean) < max(3, int(n * 0.8)):
        raise ValueError("too few valid questions in the reply")
    return clean[:n]


def generate_quiz(ctx, n, level):
    """Calls Gemini, validates, retries once. Raises ai.AIError if no usable quiz."""
    prompt = prompts.build_quiz_prompt(ctx, n, level)
    for _attempt in range(2):
        data = ai.generate_json(prompt, system=prompts.QUIZ_SYSTEM, temperature=0.5)
        try:
            return validate_quiz(data, n)
        except ValueError:
            continue
    raise ai.AIError("The AI did not return a usable quiz twice. Please click Generate again.")


# ================================================================ database
def save_quiz(user_id, topic_id, level, questions):
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO quizzes (user_id, topic_id, difficulty) VALUES (?, ?, ?)",
                           (user_id, topic_id, level))
        quiz_id = cur.lastrowid
        conn.executemany(
            """INSERT INTO questions
               (quiz_id, question_text, option_a, option_b, option_c, option_d,
                correct_option, explanation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(quiz_id, q["question"], *q["options"], q["correct"], q["explanation"])
             for q in questions])
    return quiz_id


def load_quiz(quiz_id):
    with db.get_conn() as conn:
        quiz = conn.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
        if quiz is None:
            return None
        rows = conn.execute("SELECT * FROM questions WHERE quiz_id = ? ORDER BY id",
                            (quiz_id,)).fetchall()
    questions = [{
        "id": r["id"], "question_text": r["question_text"],
        "options": [r["option_a"], r["option_b"], r["option_c"], r["option_d"]],
        "correct_option": r["correct_option"], "explanation": r["explanation"] or "",
    } for r in rows]
    return {"id": quiz["id"], "topic_id": quiz["topic_id"],
            "difficulty": quiz["difficulty"], "questions": questions}


def _update_performance(conn, user_id, topic_id):
    """Recalculate and store the topic's performance after a new quiz result."""
    prev = conn.execute("SELECT classification FROM topic_performance WHERE topic_id = ?",
                        (topic_id,)).fetchone()
    pcts = [r["percentage"] for r in conn.execute(
        "SELECT percentage FROM quiz_results WHERE user_id = ? AND topic_id = ? ORDER BY id",
        (user_id, topic_id))]
    average = sum(pcts) / len(pcts)
    last = pcts[-1]
    recent = pcts[-2:]
    level = classify(sum(recent) / len(recent))
    conn.execute(
        """INSERT INTO topic_performance
           (user_id, topic_id, attempts, avg_percentage, last_percentage, classification, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(topic_id) DO UPDATE SET
             attempts = excluded.attempts,
             avg_percentage = excluded.avg_percentage,
             last_percentage = excluded.last_percentage,
             classification = excluded.classification,
             updated_at = CURRENT_TIMESTAMP""",
        (user_id, topic_id, len(pcts), average, last, level))
    return {"classification": level, "previous": prev["classification"] if prev else None,
            "attempts": len(pcts), "average": average}


def submit_quiz(user_id, quiz, answers):
    """Score the quiz, store the result and answers, update topic performance."""
    details, correct_count = [], 0
    for q in quiz["questions"]:
        chosen = answers.get(q["id"])
        ok = chosen == q["correct_option"]
        correct_count += 1 if ok else 0
        details.append({"id": q["id"], "question": q["question_text"], "options": q["options"],
                        "chosen": chosen, "correct": q["correct_option"], "ok": ok,
                        "explanation": q["explanation"]})
    total = len(quiz["questions"])
    percentage = correct_count / total * 100

    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO quiz_results (quiz_id, user_id, topic_id, score, total, percentage)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (quiz["id"], user_id, quiz["topic_id"], correct_count, total, percentage))
        result_id = cur.lastrowid
        conn.executemany(
            """INSERT INTO quiz_answers (result_id, question_id, selected_option, is_correct)
               VALUES (?, ?, ?, ?)""",
            [(result_id, d["id"], d["chosen"], 1 if d["ok"] else 0) for d in details])
        perf = _update_performance(conn, user_id, quiz["topic_id"])

    return {"topic_id": quiz["topic_id"], "score": correct_count, "total": total,
            "percentage": percentage, "details": details, "perf": perf}


def performance_table(user_id):
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT s.name AS subject, u.name AS unit, t.name AS topic,
                      tp.attempts, tp.avg_percentage, tp.last_percentage, tp.classification
               FROM topic_performance tp
               JOIN topics t ON tp.topic_id = t.id
               JOIN units u ON t.unit_id = u.id
               JOIN subjects s ON u.subject_id = s.id
               WHERE tp.user_id = ?
               ORDER BY tp.last_percentage ASC""", (user_id,)).fetchall()
    return [dict(r) for r in rows]


# ================================================================ page pieces
def _show_quiz_form(user, quiz):
    ctx = get_topic_context(quiz["topic_id"])
    questions = quiz["questions"]
    st.subheader(f"{ctx['topic']} · {quiz['difficulty']} · {len(questions)} questions")
    st.caption("Answer every question, then submit. Correct answers are shown only after you submit.")

    with st.form("quiz_form"):
        for i, q in enumerate(questions, start=1):
            st.markdown(f"**Q{i}. {q['question_text']}**")
            st.radio(
                "Choose one", LETTERS, index=None, key=f"q_{q['id']}",
                format_func=lambda L, q=q: f"{L}. {q['options'][LETTERS.index(L)]}",
                label_visibility="collapsed")
        submitted = st.form_submit_button("✅ Submit quiz", type="primary")

    if submitted:
        answers = {q["id"]: st.session_state.get(f"q_{q['id']}") for q in questions}
        if any(v is None for v in answers.values()):
            st.warning("Please answer every question before submitting.")
            return
        st.session_state["quiz_result"] = submit_quiz(user["id"], quiz, answers)
        st.session_state.pop("active_quiz", None)
        st.rerun()


def _show_result(res):
    ctx = get_topic_context(res["topic_id"])
    perf = res["perf"]
    st.subheader(f"Result: {ctx['topic']}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Score", f"{res['score']} / {res['total']}")
    m2.metric("Percentage", f"{res['percentage']:.0f}%")
    m3.metric("Correct answers", res["score"])
    m4.metric("Incorrect answers", res["total"] - res["score"])

    level = perf["classification"]
    text = (f"{ICON[level]} **{ctx['topic']}: {level}** "
            f"(based on the average of your last 2 attempts; attempts so far: {perf['attempts']}).")
    if level == "Strong":
        st.success(text + " Your next plan can reduce repeated revision for this topic.")
    elif level == "Moderate":
        st.warning(text + " Your next plan keeps a short revision session for this topic.")
    else:
        st.error(text + " Your next plan will give this topic extra revision time.")
    if perf["previous"] and perf["previous"] != level:
        st.info(f"Your level for this topic changed from {perf['previous']} to {level}.")
    st.caption("Open **Study Plan** and click **Re-plan remaining syllabus** so the plan reflects "
               "this result.")

    st.markdown("#### Review your answers")
    for i, d in enumerate(res["details"], start=1):
        mark = "✅" if d["ok"] else "❌"
        with st.expander(f"{mark} Q{i}. {d['question'][:90]}"):
            def opt(letter):
                return f"{letter}. {d['options'][LETTERS.index(letter)]}"
            st.write(f"**Your answer:** {opt(d['chosen'])}")
            st.write(f"**Correct answer:** {opt(d['correct'])}")
            if d["explanation"]:
                st.info(d["explanation"])

    b1, b2 = st.columns(2)
    if b1.button("🔁 Take another quiz"):
        st.session_state.pop("quiz_result", None)
        st.rerun()
    if ctx["status"] != "Completed" and b2.button("✅ Mark this topic as completed"):
        mark_completed(res["topic_id"])
        st.success("Topic marked as completed.")
        st.rerun()


def _show_performance_table(user_id):
    rows = performance_table(user_id)
    if not rows:
        return
    st.divider()
    st.subheader("Topic-wise performance")
    df = pd.DataFrame(rows)
    df["classification"] = df["classification"].map(lambda c: f"{ICON.get(c, '')} {c}")
    df["avg_percentage"] = df["avg_percentage"].round(0).astype(int).astype(str) + "%"
    df["last_percentage"] = df["last_percentage"].round(0).astype(int).astype(str) + "%"
    df.columns = ["Subject", "Unit", "Topic", "Attempts", "Average", "Last score", "Level"]
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("Level rule: average of the last 2 attempts. 75% or more = Strong, "
               "50-74% = Moderate, below 50% = Weak.")


# ================================================================ page
def render(user):
    st.header("📝 Quiz")

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
    tid = c3.selectbox("Topic", list(topics), format_func=lambda i: topics[i]["name"])

    d1, d2 = st.columns(2)
    n = d1.slider("Number of questions", 5, 10, 5)
    level = d2.radio("Difficulty", LEVELS, index=1, horizontal=True)

    if st.button("✨ Generate quiz", type="primary"):
        ctx = get_topic_context(tid)
        with st.spinner("Gemini is writing your quiz..."):
            try:
                questions = generate_quiz(ctx, n, level)
                st.session_state["active_quiz"] = save_quiz(user["id"], tid, level, questions)
                st.session_state.pop("quiz_result", None)
                st.rerun()
            except ai.AIError as e:
                st.error(str(e))

    st.divider()
    result = st.session_state.get("quiz_result")
    quiz_id = st.session_state.get("active_quiz")
    if result:
        _show_result(result)
    elif quiz_id:
        quiz = load_quiz(quiz_id)
        if quiz is None:
            st.session_state.pop("active_quiz", None)
            st.info("That quiz is no longer available. Generate a new one.")
        else:
            _show_quiz_form(user, quiz)
    else:
        st.info("Choose a topic, then click **Generate quiz**.")

    _show_performance_table(user["id"])

    st.caption("Questions are written by an AI model and may contain mistakes. "
               "Scoring and the Strong/Moderate/Weak level are calculated by the app, not by AI.")
