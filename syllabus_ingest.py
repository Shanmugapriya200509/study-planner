"""
syllabus_ingest.py - Reading an uploaded syllabus file and turning it into
subject -> unit -> topic -> subtopic.

Who does what:
  * Reading PDF / DOCX / TXT files, cleaning the text, error messages   -> plain Python
  * Finding the structure (units, topics, subtopics)                     -> Gemini (LLM), or
                                                                           rule-based parser if the AI fails
  * Checking that every topic really appears in the uploaded text        -> plain Python
  * Finding the part of the file that describes each topic               -> plain Python
Nothing in this module contains a default or sample syllabus.
"""
import io
import re

MAX_FILE_MB = 5
MIN_CHARS = 40
MAX_CHARS = 60000          # about 15 pages of text
CHUNK_CHARS = 10000        # size of one piece sent to the AI
SUPPORTED = ("pdf", "docx", "txt")


class SyllabusError(Exception):
    """A problem the student can fix; the message is shown on screen."""


# ================================================================ reading files
def _clean(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = text.replace("\x0c", "\n").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_pdf(data):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                pass
            if reader.is_encrypted:
                raise SyllabusError("This PDF is password-protected. Remove the password and "
                                    "upload it again.")
        pages = [(p.extract_text() or "") for p in reader.pages]
    except SyllabusError:
        raise
    except ImportError:
        raise SyllabusError("The PDF reader (the 'pypdf' package) is not installed on the server. "
                            "Add the line pypdf to requirements.txt on GitHub, then reboot the app.")
    except Exception as e:
        raise SyllabusError(f"This PDF could not be read ({type(e).__name__}). It may be damaged. "
                            "Please upload a valid PDF, DOCX or TXT file.")
    return "\n".join(pages), len(pages)


def _read_docx(data):
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        doc = Document(io.BytesIO(data))
        lines = []
        for child in doc.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                lines.append(Paragraph(child, doc).text)
            elif tag == "tbl":
                for row in Table(child, doc).rows:
                    cells = []
                    for cell in row.cells:
                        t = cell.text.strip()
                        if t and (not cells or cells[-1] != t):   # merged cells repeat their text
                            cells.append(t)
                    lines.append(" | ".join(cells))
    except ImportError:
        raise SyllabusError("The DOCX reader (the 'python-docx' package) is not installed on the "
                            "server. Add the line python-docx to requirements.txt on GitHub, then "
                            "reboot the app.")
    except Exception as e:
        raise SyllabusError(f"This DOCX file could not be read ({type(e).__name__}). It may be "
                            "damaged, or it may be an old .doc file. Save it as .docx and upload "
                            "it again.")
    return "\n".join(lines), None


def _read_txt(data):
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(enc), None
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("latin-1", errors="replace"), None


def extract_text(filename, data):
    """Returns (clean_text, info). Raises SyllabusError with a clear message."""
    name = (filename or "").strip()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in SUPPORTED:
        raise SyllabusError(f"'.{ext or 'unknown'}' files are not supported. Please upload a PDF, "
                            "DOCX or TXT file.")
    if not data:
        raise SyllabusError("The uploaded file is empty. Please upload a valid syllabus file.")
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise SyllabusError(f"The file is larger than {MAX_FILE_MB} MB. Please upload a smaller file.")

    if ext == "pdf":
        raw, pages = _read_pdf(data)
    elif ext == "docx":
        raw, pages = _read_docx(data)
    else:
        raw, pages = _read_txt(data)

    text = _clean(raw)
    if len(text) < MIN_CHARS:
        extra = (" If this is a scanned PDF or an image, the text cannot be read because this app "
                 "has no OCR. Upload a text-based PDF, DOCX or TXT file.") if ext == "pdf" else ""
        raise SyllabusError("No readable text was found in this file." + extra)

    truncated = False
    if len(text) > MAX_CHARS:
        cut = text.rfind("\n", 0, MAX_CHARS)
        text = text[:cut if cut > MAX_CHARS // 2 else MAX_CHARS]
        truncated = True
    return text, {"ext": ext, "chars": len(text), "pages": pages, "truncated": truncated}


# ================================================================ helpers
def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _stem(filename):
    base = (filename or "Uploaded syllabus").rsplit(".", 1)[0]
    base = re.sub(r"[_\-]+", " ", base).strip()
    return (base[:80] or "Uploaded syllabus").title()


def _split_top(s, seps):
    """Split on separators that are not inside brackets."""
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch in seps and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip(" .\t-") for p in parts if p.strip(" .\t-")]


# ================================================================ rule-based parser (no AI)
UNIT_RE = re.compile(
    r"^\s*((?:unit|module|chapter)\s*[-:.]?\s*(?:[ivxlcdm]{1,6}|\d{1,2}))\b[\s:.\-–—]*(.*)$",
    re.I)
STOP_RE = re.compile(
    r"^\s*(text\s*books?|references?|suggested reading|course outcomes?|outcomes?|total\b|"
    r"lecture\s+periods|tutorial\s+periods|practical\s+periods|"
    r"evaluation|assessment|web resources|e-resources)", re.I)
NOISE_RE = re.compile(r"^\s*(?:CO\s?\d+[\s,]*)+$", re.I)                      # lines like 'CO1' or 'CO2 CO3'
PERIODS_RE = re.compile(r"^\s*(?:periods?|hours?|hrs?)\s*[:\-]?\s*\d+\s*$", re.I)
PERIODS_TAIL_RE = re.compile(r"\s*\b(?:periods?|hours?|hrs?)\s*[:\-]?\s*\d+\s*$", re.I)
DASH_SPLIT_RE = re.compile(r"\s+[-–—]\s*|\s*[-–—]\s+")                 # ' - ' 'search- ' ' –Causal' (not 'Mini-max')
CODE_RE = re.compile(r"^[A-Z]{2,5}\s?\d{2,4}[A-Z]?$")                      # a course code such as CS225
HOURS_RE = re.compile(
    r"[\(\[]?\s*\d+\s*(?:hours?|hrs?|periods?|lectures?)\s*[\)\]]?", re.I)
BULLET_RE = re.compile(r"^\s*(?:[-–—•*·●▪■□]|\(?\d{1,2}[.)]|\(?[a-z][.)])\s+")


def _unit_label(raw):
    """'UNIT  ii' -> 'Unit II', 'module-3' -> 'Module 3'."""
    m = re.match(r"(unit|module|chapter)\s*[-:.]?\s*(\S+)", raw.strip(), re.I)
    return f"{m.group(1).capitalize()} {m.group(2).upper()}" if m else raw.strip()


def _guess_subject(text):
    for line in text.split("\n")[:40]:
        m = re.match(r"^\s*(?:course|subject|paper)\s*(?:title|name)?\s*[:\-–]\s*(.+)$", line, re.I)
        if m:
            return m.group(1).strip(" .")[:80]
        m = re.match(r"^\s*syllabus\s+(?:for|of)\s*[:\-–]?\s*(.+)$", line, re.I)
        if m:
            return m.group(1).strip(" .")[:80]
    lines = [ln.strip() for ln in text.split("\n")[:80]]
    for i, ln in enumerate(lines[:-1]):
        nxt = lines[i + 1]
        if CODE_RE.match(ln) and len(nxt) >= 4 and re.search(r"[A-Za-z]{3}", nxt) and not nxt.isdigit():
            return nxt[:80]
    return ""


def _clean_title(title):
    title = HOURS_RE.sub("", title)
    title = PERIODS_TAIL_RE.sub("", title)
    return title.strip(" :.-–—|")


def _topics_from_text(text, seen, topics):
    """Split one paragraph into topics. Separators: ';', ',' and ' - ' (a dash with a space).
    'Name: a, b, c' becomes a topic with subtopics."""
    def add(name, subs=()):
        name = name.strip(" .:-,;")[:120]
        if len(name) < 3 or _norm(name) in seen:
            return
        seen.add(_norm(name))
        topics.append({"name": name, "subtopics": [s[:120] for s in subs][:12]})

    for segment in DASH_SPLIT_RE.split(text):
        for piece in _split_top(segment, ";"):
            if ":" in piece:
                head, tail = piece.split(":", 1)
                heads, items = _split_top(head, ","), _split_top(tail, ",;")
                if heads and items:
                    for h in heads[:-1]:
                        add(h)
                    add(heads[-1], items)
                    continue
            for part in _split_top(piece, ","):
                add(part)


def _join_wrapped(lines):
    """Join lines that were only wrapped by the page layout into one paragraph."""
    longest = max((len(x) for x in lines), default=0)
    out = []
    for ln in lines:
        if out and not BULLET_RE.match(ln):
            prev = out[-1]
            wrapped = (len(prev) >= max(50, int(longest * 0.85)) and prev[-1] not in ".;:?!")
            if ln[:1].islower() or prev.endswith((",", ":")) or wrapped:
                out[-1] = prev + " " + ln
                continue
        out.append(ln)
    return out


def _topics_from_lines(lines):
    topics, seen = [], set()
    for paragraph in _join_wrapped(lines):
        paragraph = BULLET_RE.sub("", HOURS_RE.sub("", paragraph)).strip(" |")
        if len(paragraph) >= 3:
            _topics_from_text(paragraph, seen, topics)
    return topics


def parse_with_rules(text, filename=""):
    """Simple parser for syllabi written as 'Unit 1 / Module 2 / Chapter 3' headings followed
    by topics. Only text that is really in the file is used. Returns a structure dict."""
    units, cur_title, body = [], None, []
    state = {"need_title": False}

    def close():
        if cur_title is not None:
            topics = _topics_from_lines(body)
            if topics:
                units.append({"name": cur_title, "topics": topics})

    collecting = False
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = UNIT_RE.match(line)
        if m:
            close()
            label = _unit_label(m.group(1))
            title = _clean_title(m.group(2))
            cur_title = f"{label}: {title}" if title else label
            body, collecting = [], True
            state["need_title"] = not title          # the title may be on the next line
            continue
        if STOP_RE.match(line):
            close()
            cur_title, body, collecting = None, [], False
            continue
        if not collecting or NOISE_RE.match(line) or PERIODS_RE.match(line):
            continue
        if state["need_title"] and not body and len(line) <= 100 and " - " not in line:
            cur_title = f"{cur_title}: {_clean_title(line)}"
            state["need_title"] = False
            continue
        state["need_title"] = False
        body.append(line)
    close()

    subject = _guess_subject(text) or _stem(filename)
    return {"subjects": [{"name": subject, "units": units}] if units else []}


# ================================================================ checking, merging, counting
def _is_grounded(name, norm_text, tokens):
    n = _norm(name)
    if not n:
        return False
    if n in norm_text:
        return True
    words = [w for w in n.split() if len(w) > 2]
    if not words:
        return False
    return sum(1 for w in words if w in tokens) / len(words) >= 0.8


def merge_structures(parts):
    """Combine the structures returned for the separate pieces of a long document."""
    subjects, order, last = {}, [], None
    for struct in parts:
        for s in struct.get("subjects", []) or []:
            name = str(s.get("name", "")).strip()
            key = _norm(name) or last or "untitled"
            last = key
            sub = subjects.get(key)
            if sub is None:
                sub = subjects[key] = {"name": name, "units": {}, "order": []}
                order.append(key)
            if name and not sub["name"]:
                sub["name"] = name
            for u in s.get("units", []) or []:
                uname = str(u.get("name", "")).strip() or "Topics"
                ukey = _norm(uname)
                unit = sub["units"].get(ukey)
                if unit is None:
                    unit = sub["units"][ukey] = {"name": uname, "topics": {}, "order": []}
                    sub["order"].append(ukey)
                for t in u.get("topics", []) or []:
                    tname = str(t.get("name", "")).strip()
                    if not tname:
                        continue
                    tkey = _norm(tname)
                    top = unit["topics"].get(tkey)
                    if top is None:
                        top = unit["topics"][tkey] = {"name": tname, "subtopics": []}
                        unit["order"].append(tkey)
                    for st in t.get("subtopics", []) or []:
                        st = str(st).strip()
                        if st and _norm(st) not in {_norm(x) for x in top["subtopics"]}:
                            top["subtopics"].append(st)
    out = []
    for skey in order:
        sub = subjects[skey]
        units = []
        for ukey in sub["order"]:
            unit = sub["units"][ukey]
            units.append({"name": unit["name"],
                          "topics": [unit["topics"][k] for k in unit["order"]]})
        out.append({"name": sub["name"], "units": units})
    return {"subjects": out}


def ground_structure(struct, text, filename=""):
    """Keep only topics and subtopics that really appear in the uploaded text.
    Returns (clean_structure, dropped_names)."""
    norm_text = _norm(text)
    tokens = set(norm_text.split())
    dropped, subjects = [], []
    for s in struct.get("subjects", []):
        name = (s.get("name") or "").strip()
        if not name or not _is_grounded(name, norm_text, tokens):
            name = _stem(filename)
        units = []
        for u in s.get("units", []):
            topics = []
            for t in u.get("topics", []):
                if not _is_grounded(t["name"], norm_text, tokens):
                    dropped.append(t["name"])
                    continue
                subs = []
                for st in t.get("subtopics", []):
                    if _is_grounded(st, norm_text, tokens):
                        subs.append(st[:120])
                    else:
                        dropped.append(st)
                topics.append({"name": t["name"][:120], "subtopics": subs[:12]})
            if topics:
                units.append({"name": u["name"][:150], "topics": topics})
        if units:
            subjects.append({"name": name[:100], "units": units})
    return {"subjects": subjects}, dropped


def count_structure(struct):
    subjects = units = topics = subtopics = 0
    for s in struct.get("subjects", []):
        subjects += 1
        for u in s["units"]:
            units += 1
            for t in u["topics"]:
                topics += 1
                subtopics += len(t["subtopics"])
    return {"subjects": subjects, "units": units, "topics": topics, "subtopics": subtopics}


def find_context(text, topic_name, width=900):
    """The part of the uploaded text around the place where the topic is written ('' if not found)."""
    words = topic_name.split()
    if not words:
        return ""
    pattern = re.compile(r"\s+".join(re.escape(w) for w in words), re.I)
    m = pattern.search(text)
    if not m:
        return ""
    start = max(0, m.start() - 200)
    return text[start:m.end() + width].strip()


def attach_excerpts(struct, text):
    for s in struct.get("subjects", []):
        for u in s["units"]:
            for t in u["topics"]:
                t["excerpt"] = find_context(text, t["name"])
    return struct


# ================================================================ analysis (AI first, rules as fallback)
def _chunks(text):
    chunks, cur, size = [], [], 0
    for line in text.split("\n"):
        starts_unit = bool(UNIT_RE.match(line))
        if cur and (size + len(line) > CHUNK_CHARS or (starts_unit and size > CHUNK_CHARS // 2)):
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def analyse_syllabus(text, filename, use_ai=True):
    """Returns {"structure", "method", "dropped", "warnings"}.
    Raises SyllabusError if no units/topics can be found in the file."""
    import ai_service as ai            # imported here so reading files never needs the AI module
    import prompts_syllabus as ps

    warnings, structure, method, dropped = [], None, "Rule-based", []

    if use_ai and ai.has_api_key():
        try:
            parts = _chunks(text)
            results = [ai.generate_json(ps.build_structure_prompt(c, i + 1, len(parts)),
                                        system=ps.STRUCTURE_SYSTEM, temperature=0.1)
                       for i, c in enumerate(parts)]
            merged = merge_structures([r for r in results if isinstance(r, dict)])
            grounded, dropped = ground_structure(merged, text, filename)
            if count_structure(grounded)["topics"] > 0:
                structure, method = grounded, "AI (Gemini)"
            else:
                warnings.append("The AI did not find any topics, so the rule-based reader was used.")
        except ai.AIError as e:
            warnings.append(f"AI unavailable: {e} The rule-based reader was used instead.")
    elif use_ai:
        warnings.append("No Gemini API key found, so the rule-based reader was used.")

    if structure is None:
        structure = parse_with_rules(text, filename)
        dropped = []
        if count_structure(structure)["topics"] == 0:
            raise SyllabusError(
                "No units or topics could be found in this file. Make sure the syllabus is written "
                "as text with headings such as 'Unit 1', 'Module 2' or 'Chapter 3' followed by "
                "topics, then upload it again.")

    return {"structure": attach_excerpts(structure, text), "method": method,
            "dropped": dropped, "warnings": warnings}
