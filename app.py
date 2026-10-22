"""
app.py - Streamlit entry point and page routing for My Planner AI.
"""
import streamlit as st

import adaptive
import ai_service as ai
import dashboard
import database as db
import learn
import planner
import quiz
import syllabus

st.set_page_config(
    page_title="My Planner AI | AI-Powered Personalized Study Planner",
    page_icon="📚",
    layout="wide",
    menu_items={"About": "My Planner AI is an AI-powered personalized study planner. "
                         "Upload your syllabus to get an adaptive study plan, topic explanations, "
                         "quizzes and progress tracking."},
)
db.init_db()

LEVELS = ["School", "Diploma", "Undergraduate", "Postgraduate"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
PAGES = ["Dashboard", "Syllabus", "Study Goal", "Study Plan", "Adaptive Plan",
         "Learn a Topic", "Quiz", "Profile", "AI Test"]
PAGES_NEED_SYLLABUS = {"Dashboard", "Study Goal", "Study Plan", "Adaptive Plan",
                       "Learn a Topic", "Quiz"}
NO_SYLLABUS_MESSAGE = ("Please upload your syllabus first to generate your personalized "
                       "study plan and notes.")


# ---------------------------------------------------------------- login / register
def auth_screen():
    st.title("📚 My Planner AI")
    st.caption("An AI-powered personalized study planner: upload your syllabus to get an adaptive "
               "study plan, topic explanations, quizzes and progress tracking.")

    tab_login, tab_register = st.tabs(["Login", "Register"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")
        if submitted:
            user = db.authenticate(username, password)
            if user:
                st.session_state.clear()
                st.session_state["user_id"] = user["id"]
                st.rerun()
            else:
                st.error("Invalid username or password.")

    with tab_register:
        with st.form("register_form"):
            name = st.text_input("Full name")
            new_username = st.text_input("Username (or email)")
            new_password = st.text_input("Password (min 4 characters)", type="password")
            level = st.selectbox("Academic level", LEVELS, index=2)
            hours = st.slider("Preferred study hours per day", 0.5, 8.0, 2.0, 0.5)
            days = st.multiselect("Preferred study days", DAYS, default=DAYS[:5])
            submitted = st.form_submit_button("Create account")
        if submitted:
            if not name.strip() or not new_username.strip() or len(new_password) < 4:
                st.error("Please fill all fields (password at least 4 characters).")
            elif not days:
                st.error("Select at least one study day.")
            else:
                try:
                    uid = db.create_user(name, new_username, new_password, level, hours, days)
                    st.session_state.clear()
                    st.session_state["user_id"] = uid
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))


# ---------------------------------------------------------------- profile
def page_profile(user):
    st.header("👤 Profile")

    with st.form("profile_form"):
        name = st.text_input("Full name", value=user["name"])
        level = st.selectbox(
            "Academic level", LEVELS,
            index=LEVELS.index(user["academic_level"]) if user["academic_level"] in LEVELS else 2,
        )
        hours = st.slider("Preferred study hours per day", 0.5, 8.0,
                          float(user["preferred_hours"]), 0.5)
        days = st.multiselect("Preferred study days", DAYS,
                              default=[d for d in user["preferred_days"].split(",") if d in DAYS])
        saved = st.form_submit_button("Save profile")
    if saved:
        if not days:
            st.error("Select at least one study day.")
        else:
            db.update_profile(user["id"], name, level, hours, days)
            st.success("Profile updated.")
            st.rerun()

    st.divider()
    st.subheader("Your syllabus")
    counts = db.count_syllabus(user["id"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Subjects", counts["subjects"])
    c2.metric("Units", counts["units"])
    c3.metric("Topics", counts["topics"])

    doc = db.get_syllabus_document(user["id"])
    if doc:
        st.write(f"Source file: **{doc['filename']}** ({doc['file_type'].upper()}), "
                 f"uploaded {doc['uploaded_at']} UTC.")
    if counts["topics"] == 0:
        st.info("No syllabus yet. Open the **Syllabus** page to upload one.")
    elif st.button("Clear my syllabus"):
        db.clear_syllabus(user["id"])
        syllabus.clear_stale_state()
        st.warning("Syllabus cleared.")
        st.rerun()


# ---------------------------------------------------------------- AI connection test
def page_ai_test():
    st.header("🔌 AI Connection Test")
    st.write("This page checks that the app can reach the Gemini API. "
             "The key itself is never displayed.")

    if ai.has_api_key():
        st.success("API key found in Secrets.")
    else:
        st.error("API key NOT found. In Streamlit Cloud open your app, then "
                 "**⋮ → Settings → Secrets**, add  GEMINI_API_KEY = \"your-key\"  and Save.")
        return

    prompt = st.text_input("Ask the AI", value="Explain the OSI model in simple words.")
    if st.button("Ask Gemini", type="primary"):
        with st.spinner("Asking Gemini..."):
            try:
                text, model = ai.generate_text(prompt)
                st.success(f"Response received (model used: {model})")
                st.write(text)
            except ai.AIError as e:
                st.error(str(e))

    st.divider()
    st.caption("If the question above fails with a model error, list the models your key can use:")
    if st.button("List models available to my key"):
        try:
            st.write(ai.list_models())
        except ai.AIError as e:
            st.error(str(e))


# ---------------------------------------------------------------- main
def _go_to_syllabus():
    st.session_state["nav_page"] = "Syllabus"


def main():
    if "user_id" not in st.session_state:
        auth_screen()
        return

    user = db.get_user(st.session_state["user_id"])
    if user is None:  # database was reset (normal on Streamlit Cloud after a restart)
        st.session_state.clear()
        st.rerun()

    st.sidebar.title("📚 My Planner AI")
    st.sidebar.write(f"Signed in as **{user['name']}**")
    page = st.sidebar.radio("Navigate", PAGES, key="nav_page")
    if st.sidebar.button("Logout"):
        st.session_state.clear()      # forget everything that belongs to this user
        st.rerun()

    if page in PAGES_NEED_SYLLABUS and db.count_syllabus(user["id"])["topics"] == 0:
        st.header(page)
        st.warning(NO_SYLLABUS_MESSAGE)
        st.button("Go to the Syllabus page", type="primary", on_click=_go_to_syllabus)
        return

    if page == "Dashboard":
        dashboard.render(user)
    elif page == "Syllabus":
        syllabus.render(user)
    elif page == "Study Goal":
        planner.render_goal(user)
    elif page == "Study Plan":
        planner.render_plan(user)
    elif page == "Adaptive Plan":
        adaptive.render(user)
    elif page == "Learn a Topic":
        learn.render(user)
    elif page == "Quiz":
        quiz.render(user)
    elif page == "Profile":
        page_profile(user)
    elif page == "AI Test":
        page_ai_test()


main()
