"""
prompts.py - All prompt templates sent to Gemini.
Keeping them here means the wording can be changed without touching the app logic.
"""
import json

PLAN_SYSTEM = (
    "You are a careful study-planning assistant for college students. "
    "You reply with one valid JSON object only: no commentary and no markdown fences. "
    "You never promise results; you only organise the student's time."
)


def build_plan_prompt(ctx):
    """ctx: dict made by planner.generate_plan (dates, capacity and the unfinished topics)."""
    topics_json = json.dumps(ctx["topics"], ensure_ascii=False, separators=(",", ":"))
    return f"""Create a personalised study plan.

STUDENT CONSTRAINTS
- Today: {ctx['today']}. Deadline: {ctx['target']}.
- Study days available: {ctx['days']}. Minutes per study day: {ctx['minutes_per_day']}.
- Total minutes available: {ctx['capacity']}.
- The sum of ALL item minutes MUST NOT exceed {ctx['limit']}.

TOPICS STILL TO STUDY (JSON)
{topics_json}
Field meanings: subject_priority 1 = high priority subject, 2 = normal.
performance is Strong, Moderate, Weak or Untested (from past quizzes); last_quiz_pct may be null.

PLANNING RULES
1. Every topic gets exactly one "Study" item, with minutes close to est_minutes
   (shorter for Easy topics, up to 25% longer for Hard topics).
2. Keep topics of the same unit together and in the order given. Subjects with
   subject_priority 1 come first.
3. Add a "Revision" item (20 to 45 minutes) for topics whose performance is Weak or Moderate,
   and for Hard topics that are Untested. Do NOT add Revision for Strong topics.
4. Weak topics get the most attention: more minutes, and revision soon after study.
5. After the last topic of each unit add one "Test" item (15 to 30 minutes), using the
   topic_id of the hardest topic of that unit.
6. If time is short, drop Revision and Test items first, then shorten Study items.
7. Use only topic_id values from the list above. Minutes are multiples of 5, between 15 and 120.
8. List items in the exact order the student should do them.

OUTPUT FORMAT (JSON object only):
{{"summary": "2 to 3 sentences explaining the plan, mentioning weak topics and any time shortage",
  "items": [
    {{"topic_id": 1, "session_type": "Study", "minutes": 60, "reason": "at most 10 words"}}
  ]}}
"""
