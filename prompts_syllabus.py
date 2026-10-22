"""
prompts_syllabus.py - Prompt used to read the structure of an uploaded syllabus.
"""

STRUCTURE_SYSTEM = (
    "You read university syllabus text and extract its structure. "
    "You reply with one valid JSON object only: no commentary and no markdown fences. "
    "You copy names from the text. You never add units, topics or subtopics that are not written "
    "in the text."
)


def build_structure_prompt(chunk, part, parts):
    return f"""Extract the structure of this syllabus text.

This is part {part} of {parts} of the uploaded document.

RULES
1. Use ONLY what is written in the text below. Do not add, guess or complete missing topics.
2. Hierarchy: subject (course) -> units (also called modules or chapters) -> topics -> subtopics.
3. Copy unit and topic names as written. You may remove bullets, numbering and hour counts.
4. A topic is a study item such as a concept or a method. Items listed after a colon, or in brackets
   under a topic, are its subtopics.
5. Ignore text books, references, course outcomes, marks, evaluation schemes and other text that is
   not syllabus content.
6. If the text has no syllabus content, return {{"subjects": []}}.
7. If the subject (course) name is not written in this part, use "" for the name.

TEXT START
{chunk}
TEXT END

OUTPUT FORMAT (JSON object only):
{{"subjects": [{{"name": "course title or empty", "units": [{{"name": "unit name", "topics": [{{"name": "topic name", "subtopics": ["subtopic"]}}]}}]}}]}}
"""
