"""
prompts.py - All prompt templates sent to Gemini.
Keeping them here means the wording can be changed without touching the app logic.
"""
import json

# ================================================================ study plan
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


# ================================================================ topic explanation
EXPLAIN_SYSTEM = (
    "You are a patient, accurate tutor for college students. "
    "You reply with one valid JSON object only: no commentary and no markdown fences. "
    "You never invent facts, statistics, references or quotations. "
    "If you are unsure about a detail, leave it out instead of guessing."
)

LEVEL_GUIDE = {
    "Beginner": ("Use very simple words and everyday analogies. Assume no prior knowledge "
                 "and define every technical term the first time it appears."),
    "Intermediate": ("Assume the student knows the basics of the subject. Be precise and "
                     "explain how and why things work."),
    "Advanced": ("Be rigorous and detailed. Include edge cases, trade-offs, limitations and "
                 "common exam traps."),
}


def build_explain_prompt(ctx, level):
    """ctx: subject, unit, topic, difficulty, performance."""
    weak_note = ""
    if ctx.get("performance") == "Weak":
        weak_note = ("\nThe student scored poorly on this topic in an earlier quiz, so point out "
                     "the most common misunderstandings and correct them gently.")
    return f"""Explain one syllabus topic to a student.

Subject: {ctx['subject']}
Unit: {ctx['unit']}
Topic: {ctx['topic']}
Explanation level: {level}. {LEVEL_GUIDE.get(level, '')}{weak_note}

Stay strictly on this topic, within the subject above.

OUTPUT FORMAT (JSON object only, all fields required):
{{"simple_explanation": "a clear explanation in 2 to 4 short paragraphs",
  "key_concepts": ["Term: one-sentence meaning", "... 4 to 6 items"],
  "important_points": ["a point to remember", "... 4 to 6 items"],
  "example": "one concrete worked example or scenario, easy to follow",
  "summary": "2 to 3 sentences",
  "exam_questions": ["a likely exam question", "... exactly 4 items, a mix of short and long answer"]}}
"""
