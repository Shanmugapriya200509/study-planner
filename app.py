"""
app.py - Streamlit entry point: login/register, dashboard, profile.
Other pages are placeholders until later steps.
"""
import streamlit as st

import database as db
import progress as prog
import syllabus
import planner

st.set_page_config(page_title="AI Study Planner", page_icon="📚", layout="wide")
db.init_db()

LEVELS = ["School", "Diploma", "Undergraduate", "Postgraduate"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------- login / register
def auth_screen():
    st.title("📚 AI-Powered Personalized Study Planner")
    st.caption("Plan. Study. Test. Adapt. Track.")

    tab_login, tab_register = st.tabs(["Login", "Register"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")
        if submitted:
            user = db.authenticate(username, password)
            if user:
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
                    st.session_state["user_id"] = uid
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))


# ---------------------------------------------------------------- dashboard
def page_dashboard(user):
    st.header(f"📊 Dashboard - {user['name']}")
    s = prog.get_summary(user["id"])

    if s["total_topics"] == 0:
        st.info("Your syllabus is empty. Go to **Profile** and click "
                "**Load demo syllabus** to try the app.")

    st.write(f"**Overall syllabus completion: {s['completion_pct']:.0f}%**")
    st.progress(min(s["completion_pct"] / 100, 1.0))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Topics completed", f"{s['done_topics']} / {s['total_topics']}")
    c2.metric("Units completed", f"{s['done_units']} / {s['total_units']}")
    c3.metric("Days remaining",
              s["days_remaining"] if s["days_remaining"] is not None else "Not set")
    c4.metric("Average quiz score",
              f"{s['avg_quiz_pct']:.0f}%" if s["avg_quiz_pct"] is not None else "No quiz yet")

    st.caption("Charts, weak/strong topics and weekly targets will be added in later steps.")


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
    st.subheader("Syllabus data")
    counts = db.count_syllabus(user["id"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Subjects", counts["subjects"])
    c2.metric("Units", counts["units"])
    c3.metric("Topics", counts["topics"])

    st.write("Load the demo **Computer Networks** syllabus (5 units, 24 topics). "
             "This replaces your current syllabus.")
    b1, b2 = st.columns(2)
    if b1.button("Load demo syllabus", type="primary"):
        db.seed_demo_syllabus(user["id"])
        st.success("Demo syllabus loaded.")
        st.rerun()
    if b2.button("Clear my syllabus"):
        db.clear_syllabus(user["id"])
        st.warning("Syllabus cleared.")
        st.rerun()


def page_placeholder(title, coming_in):
    st.header(title)
    st.info(f"This page will be built in {coming_in}.")


# ---------------------------------------------------------------- main
def main():
    if "user_id" not in st.session_state:
        auth_screen()
        return

    user = db.get_user(st.session_state["user_id"])
    if user is None:  # database was reset (normal on Streamlit Cloud after a restart)
        del st.session_state["user_id"]
        st.rerun()

    st.sidebar.title("📚 Study Planner")
    st.sidebar.write(f"Signed in as **{user['name']}**")
    page = st.sidebar.radio(
        "Navigate",
        ["Dashboard", "Syllabus", "Study Goal", "Study Plan",
         "Learn a Topic", "Quiz", "Profile"],
    )
    if st.sidebar.button("Logout"):
        del st.session_state["user_id"]
        st.rerun()

    if page == "Dashboard":
        page_dashboard(user)
    elif page == "Profile":
        page_profile(user)
    elif page == "Syllabus":
        syllabus.render(user)
    elif page == "Study Goal":
        planner.render_goal(user)
    elif page == "Study Plan":
        page_placeholder("🗓️ AI Study Plan", "a later step")
    elif page == "Learn a Topic":
        page_placeholder("🧠 AI Topic Explanation", "Day 2")
    elif page == "Quiz":
        page_placeholder("📝 Quiz", "Day 2")


main()
