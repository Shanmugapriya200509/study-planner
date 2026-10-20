# AI-Powered Personalized Study Planner and Progress Monitoring System

A web application that helps a student turn a syllabus and a deadline into a personalised,
adaptive study plan. It explains topics, generates quizzes, finds weak topics from the
student's own quiz results, adapts the remaining plan, and shows progress on a dashboard.

Mini-project by: <your name>, <department>, <college>

## What the system does

1. The student registers and logs in.
2. The student enters the syllabus as Subject, Unit and Topic (or loads the demo syllabus).
3. The student sets a start date, a target date, study hours per day and study days per week.
4. The app calculates the available study capacity.
5. The AI creates a daily and weekly study plan with study, revision and test sessions.
6. The student studies a topic with an AI-generated explanation (Beginner, Intermediate or Advanced).
7. The student takes an AI-generated multiple-choice quiz.
8. The app scores the quiz and classifies the topic as Strong, Moderate or Weak.
9. The adaptive planner rebuilds the remaining plan using quiz results and missed sessions.
10. The dashboard shows progress towards the deadline.

## What is AI and what is not

This project uses a large language model (Google Gemini) through an API. No machine-learning
model is trained in this project.

| Feature | Done by |
|---|---|
| Study plan: topic order, revision and test sessions, short explanation | Gemini (LLM) |
| Topic explanation, examples, exam questions | Gemini (LLM) |
| Quiz questions, options and explanations | Gemini (LLM) |
| Adaptive plan: re-ordering and written explanation of the changes | Gemini (LLM) |
| Study capacity, 30% revision/test allowance, scheduling onto dates | Plain Python |
| Checking AI replies (JSON validation), retries, model fallback | Plain Python |
| Rule-based fallback plan when the AI is unavailable | Plain Python |
| Quiz scoring, percentage, Strong / Moderate / Weak levels | Plain Python (thresholds) |
| Rules that force revision for weak topics and none for strong topics | Plain Python |
| Old-versus-new plan comparison table | Plain Python |
| Dashboard numbers, charts and status messages | Plain Python and SQL |
| Data storage | SQLite |

Level rule: average of the last two quiz scores for a topic. 75% or more is Strong,
50% to 74% is Moderate, below 50% is Weak.

## Technology

Python, Streamlit (user interface), SQLite (database), Google Gemini API (google-genai package),
Plotly (charts), Pandas (tables). Hosted on Streamlit Community Cloud.

## Project structure

    app.py          Entry point, login, profile, page routing
    database.py     SQLite schema, user accounts, demo syllabus
    syllabus.py     Syllabus page: subjects, units, topics, status
    planner.py      Study goal, capacity calculation, AI study plan, plan page
    adaptive.py     Adaptive re-planning and old-versus-new comparison
    learn.py        AI topic explanation page
    quiz.py         AI quiz, scoring, performance analysis
    progress.py     Dashboard calculations (SQL)
    dashboard.py    Dashboard page: cards, Plotly charts, status messages
    ai_service.py   The only module that calls Gemini (retries, model fallback, JSON parsing)
    prompts.py      All prompt templates
    requirements.txt

## Database tables

| Table | Stores |
|---|---|
| users | Account and profile (password stored as a salted hash) |
| subjects, units, topics | The syllabus, with difficulty, estimated minutes and status |
| study_goals | Start date, target date, hours per day, study days |
| study_plans | Each plan version, why it was created, and a summary |
| study_sessions | Planned sessions per date with type, minutes and status |
| quizzes, questions | Generated quizzes and their questions |
| quiz_results, quiz_answers | Scores and the answer given to each question |
| topic_performance | Attempts, average, last score and level per topic |
| progress | Reserved for progress snapshots (not used; the dashboard reads live data) |

All child tables use foreign keys with cascading deletes.

## Setup

### Run on Streamlit Community Cloud

1. Put all files in a public GitHub repository.
2. Open share.streamlit.io, sign in with GitHub and choose Create app.
3. Select the repository, branch main, and main file app.py, then Deploy.
4. Open the app's Settings, then Secrets, and add this line with your own key:

        GEMINI_API_KEY = "your-key-here"

5. Optional: add GEMINI_MODEL = "model-name" to force a specific Gemini model.
   By default the app tries gemini-3.6-flash, gemini-3.5-flash and gemini-3.1-flash-lite in turn.

### Get a Gemini API key

Open aistudio.google.com/apikey, sign in, choose Create API key, and copy it into the Secrets box.
Never put the key in a code file or commit it to GitHub.

### Run on your own computer (optional)

1. Install Python 3.10 or newer.
2. Run: pip install -r requirements.txt
3. Create the file .streamlit/secrets.toml containing: GEMINI_API_KEY = "your-key-here"
4. Run: streamlit run app.py

## Demo data

Profile, then Load demo syllabus, loads Computer Networks with 5 units and 24 topics
(Network Models, Data Link Layer, Network Layer, Transport Layer, Application Layer and Security).
The demo syllabus can be cleared and replaced with the student's own syllabus on the Syllabus page.

## Limitations

- On Streamlit Community Cloud the SQLite file is temporary. Data is lost when the app restarts or sleeps.
- AI explanations and quiz questions can contain mistakes and should be checked against course material.
- The free Gemini tier has rate limits and can be busy; the app then retries and falls back to a rule-based plan.
- Authentication is basic (username and password, no recovery).
- Study time estimates per topic are inputs, not predictions.
- Reminders are in-app messages only; no email or phone notifications.
- Explanations are kept only for the current browser session.

## Future enhancements

Persistent cloud database, email or phone reminders, upload of a syllabus PDF with automatic
topic extraction, spaced-repetition scheduling, question banks verified by teachers,
and multi-student teacher dashboards.
