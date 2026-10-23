#!/usr/bin/env python3
"""
Lazy Syllabus Planner
---------------------
Drop your course syllabi (PDF, TXT or MD) into a folder. A local open-weight
model (via Ollama) pulls out every deadline, and plain code works backwards
from each one to tell you the *least* you need to do this week to stay on time.

Nothing leaves your machine. No API key. Works offline once the model is pulled.

    python planner.py syllabi/
    python planner.py syllabi/ --model llama3.1:8b --today 2026-10-20
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from xml.etree import ElementTree
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_HOST = "http://localhost:11434"
CHUNK_CHARS = 6000          # small models read short chunks more reliably
CHUNK_OVERLAP = 400
CACHE_FILE = ".planner_cache.json"
CACHE_VERSION = "5"         # bump when extraction/validation logic changes
GUARDS = True               # --raw turns off the checks below, to measure what they add

KINDS = ["exam", "quiz", "project", "essay", "lab", "homework", "reading", "presentation", "other"]

# How many days before the due date a lazy-but-on-time person should start,
# by effort (1 = trivial, 5 = huge). Exams always get at least a few days.
LEAD_DAYS = {1: 0, 2: 1, 3: 3, 4: 5, 5: 8}
MIN_EXAM_LEAD = 4

# Rough hours of real work by effort level. A guess, used only to show which weeks are heavy.
HOURS = {1: 0.5, 2: 1.5, 3: 4, 4: 8, 5: 15}

SCHEMA = {
    "type": "object",
    "properties": {
        "course": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": KINDS},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "due_time": {"type": "string", "description": "HH:MM 24h, or empty"},
                    "effort": {"type": "integer", "minimum": 1, "maximum": 5},
                    "weight_percent": {"type": "number"},
                    "evidence": {"type": "string"},
                },
                "required": ["title", "kind", "due_date", "effort", "evidence"],
            },
        },
    },
    "required": ["course", "items"],
}

SYSTEM_PROMPT = """You extract deadlines from course syllabi. Be precise and never invent anything.

Return JSON with:
- "course": the course code and/or name (e.g. "BIO 101 Intro Biology").
- "items": every graded or due thing that has a specific date: assignments, homework,
  essays, projects, labs, quizzes, exams, presentations, required readings with a date.

For each item:
- "title": short and specific, e.g. "Lab 3 report", "Midterm exam", "Essay 2 draft".
- "kind": one of exam, quiz, project, essay, lab, homework, reading, presentation, other.
- "due_date": YYYY-MM-DD. If the year is missing, infer it from the term/semester and
  today's date given below. If a weekday is given with a date, trust the date.
- "due_time": "HH:MM" in 24h if a time is stated near it (11pm -> "23:00", 9:00am -> "09:00",
  noon -> "12:00", 7-9pm -> "19:00"), otherwise "".
- "effort": 1 (5-minute task) to 5 (multi-week project or final exam).
- "weight_percent": percent of the grade if stated, otherwise 0.
- "evidence": copy the exact phrase from the text that states this deadline (max 120 chars).

Skip items that repeat on a schedule ("every Friday", "weekly"), items with no date
(e.g. "participation"), office hours, class meeting times and
holidays/no-class days. If nothing has a date, return an empty items list."""


# --------------------------------------------------------------------------- data

@dataclass
class Deadline:
    course: str
    title: str
    kind: str
    due: date
    due_time: str
    effort: int
    weight: float
    evidence: str
    source: str

    @property
    def start(self) -> date:
        lead = LEAD_DAYS.get(self.effort, 2)
        if self.kind == "exam":
            lead = max(lead, MIN_EXAM_LEAD)
        return self.due - timedelta(days=lead)

    @property
    def key(self) -> tuple:
        return (norm(self.course), norm(self.title), self.due)


# --------------------------------------------------------------------------- text

def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def read_docx(path: Path) -> str:
    """Plain text from a Word file, with no extra libraries (a .docx is a zip of XML)."""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    lines = []
    for para in root.iter(ns + "p"):
        text = "".join(t.text or "" for t in para.iter(ns + "t"))
        if text.strip():
            lines.append(text)
    return "\n".join(lines)


def read_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return read_docx(path)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader  # imported lazily so --help works without it
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="replace")


def chunks(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    if len(text) <= CHUNK_CHARS:
        return [text]
    out, i = [], 0
    while i < len(text):
        end = min(len(text), i + CHUNK_CHARS)
        # prefer to cut at a line break so a deadline isn't split in half
        cut = text.rfind("\n", i + CHUNK_CHARS // 2, end)
        if cut == -1 or end == len(text):
            cut = end
        out.append(text[i:cut])
        if cut >= len(text):
            break
        i = max(cut - CHUNK_OVERLAP, i + 1)
    return out


GENERIC = {"due", "the", "and", "for", "final", "draft", "exam", "quiz", "report", "class", "week"}


def evidence_found(evidence: str, title: str, text: str) -> bool:
    """Guard against hallucinations: the quoted phrase must really be in the syllabus,
    and the item's distinctive words must appear there too."""
    ev, src = norm(evidence), norm(text)
    if len(ev) < 3:
        return False
    if ev not in src:
        # allow small copy slips (dropped words, merged table cells): almost every
        # character of the quote must line up with the source, in order
        sm = difflib.SequenceMatcher(None, ev, src, autojunk=False)
        matched = sum(b.size for b in sm.get_matching_blocks() if b.size >= 3)
        if matched / len(ev) < 0.9:
            return False
    src_words = set(src.split())
    distinctive = [w for w in norm(title).split() if len(w) > 2 and w not in GENERIC]
    if distinctive:
        hits = sum(1 for w in distinctive if w in src_words or w.rstrip("s") in src_words)
        if hits / len(distinctive) < 0.6:
            return False
    return True


def neighborhood(evidence: str, text: str, before: int = 120, after: int = 120) -> str | None:
    """The stretch of syllabus text around where the quoted evidence sits."""
    low, ev = text.lower(), evidence.lower()
    if not ev:
        return None
    sm = difflib.SequenceMatcher(None, low, ev, autojunk=False)
    m = sm.find_longest_match(0, len(low), 0, len(ev))
    if m.size < min(len(ev), 8):
        return None
    begin = m.a - m.b
    return text[max(0, begin - before): min(len(text), begin + len(ev) + after)]


def date_near_evidence(due: date, evidence: str, text: str) -> bool:
    """The due date must actually be written next to the quoted evidence. This catches the
    model reading the wrong table row, or inventing dates for recurring items."""
    if (due.month, due.day) in dates_in(evidence):
        return True
    near = neighborhood(evidence, text)
    return near is not None and (due.month, due.day) in dates_in(near)


RECURRING_RE = re.compile(r"\bevery\b|\bweekly\b|\beach (?:week|mon|tue|wed|thu|fri)", re.IGNORECASE)
TIME_RE = re.compile(
    r"(?:(?P<a>\d{1,2})(?::(?P<am>\d{2}))?\s*(?:-|–|to)\s*)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)\b|\b(?P<noon>noon)\b|\b(?P<mid>midnight)\b",
    re.IGNORECASE,
)


def time_near(evidence: str, text: str) -> str:
    """Fallback when the model leaves the time blank: read it from the quoted evidence and the
    rest of its own line. Deliberately narrow: a time from a neighbouring row would be wrong
    more often than a blank one. The first time mentioned (start of a range) wins."""
    near = neighborhood(evidence, text, before=0, after=60)
    if not near:
        return ""
    if "\n" in evidence:
        near = near[:len(evidence)]
    else:
        near = near.split("\n")[0]
        if not re.search(r"[.!?]\s*$", evidence):  # finish this sentence, but never read into the next one
            end = re.search(r"[.!?](?:\s|$)", near[len(evidence):])
            near = near[:len(evidence) + (end.end() if end else len(near))]
        else:
            near = near[:len(evidence)]
    m = TIME_RE.search(near)
    if not m:
        return ""
    if m.group("noon"):
        return "12:00"
    if m.group("mid"):
        return "23:59"
    ap = m.group("ap").lower().replace(".", "")
    start_h, start_m = (m.group("a"), m.group("am")) if m.group("a") else (m.group("h"), m.group("m"))
    h, mi = int(start_h), int(start_m or 0)
    if h > 12 or mi > 59:
        return ""
    if ap == "pm" and h != 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


BLANKET_RE = re.compile(r"\b(all|each|any|dates? (?:below|listed)|unless)\b", re.IGNORECASE)
DEADLINE_WORD_RE = re.compile(r"\b(due|close[sd]?|deadline|submitted)\b", re.IGNORECASE)
BLANKET_KINDS = {"homework", "lab", "essay", "project"}


def blanket_time(text: str) -> str:
    """A syllabus-wide rule such as "All written work is due by 11:59 pm on the date listed"
    gives a time to every item that doesn't state its own."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        if RECURRING_RE.search(sentence):
            continue  # "every Friday by 5pm" is one item's rule, not the whole syllabus's
        if DEADLINE_WORD_RE.search(sentence) and BLANKET_RE.search(sentence):
            t = time_near(sentence, sentence)
            if t:
                return t
    return ""


# --------------------------------------------------------------------------- model

def ollama_chat(host: str, model: str, system: str, user: str, timeout: int = 600) -> dict:
    payload = {
        "model": model,
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": 0, "num_ctx": 8192},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if e.code == 404 and "not found" in detail:
            sys.exit(f"Model '{model}' isn't downloaded yet. Run:  ollama pull {model}")
        sys.exit(f"Ollama returned an error ({e.code}): {detail}")
    except urllib.error.URLError:
        sys.exit(f"Can't reach Ollama at {host}. Is it running? (start the Ollama app, or run: ollama serve)")
    content = body.get("message", {}).get("content", "")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"course": "", "items": []}


def extract(path: Path, text: str, host: str, model: str, today: date, log) -> list[Deadline]:
    found: list[Deadline] = []
    course_name = ""
    parts = chunks(text)
    for n, part in enumerate(parts, 1):
        log(f"   reading part {n}/{len(parts)}...")
        user = (
            f"Today's date: {today.isoformat()}\n"
            f"File name: {path.name}\n"
            + (f"Course (from earlier part): {course_name}\n" if course_name else "")
            + f"\n--- SYLLABUS TEXT ---\n{part}\n--- END ---"
        )
        data = ollama_chat(host, model, SYSTEM_PROMPT, user)
        course_name = course_name or (data.get("course") or "").strip()
        for raw in data.get("items", []) or []:
            d = validate(raw, course_name or path.stem, text, path.name, today, log)
            if d:
                found.append(d)

    # Small models sometimes skip rows. Re-ask about any dated line nothing covers.
    leftovers = missed_snippets(text, found) if GUARDS else ""
    if leftovers:
        log("   double-checking lines with dates that weren't picked up...")
        user = (
            f"Today's date: {today.isoformat()}\n"
            f"Course: {course_name or path.stem}\n"
            "These lines from the syllabus mention dates. Extract any deadlines in them that "
            "match the rules (it is fine to return an empty list if none are deadlines).\n"
            f"\n--- LINES ---\n{leftovers}\n--- END ---"
        )
        data = ollama_chat(host, model, SYSTEM_PROMPT, user)
        before = len(found)
        gaps = uncovered_dates(text, found)
        for raw in data.get("items", []) or []:
            d = validate(raw, course_name or path.stem, text, path.name, today, log)
            # only accept what fills a real gap: the date must be one nothing covered yet
            if d and (d.due.month, d.due.day) in gaps and not any(same_thing(d, f) for f in found):
                found.append(d)
        if len(found) > before:
            log(f"   recovered {len(found) - before} more")
    return found


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_RE = re.compile(
    r"\b(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?P<day>\d{1,2})(?!\d)"
    r"|\b(?P<m>1[0-2]|0?[1-9])/(?P<d>3[01]|[12]\d|0?[1-9])(?:/\d{2,4})?\b",
    re.IGNORECASE,
)


def dates_in(line: str) -> set[tuple[int, int]]:
    out = set()
    for m in DATE_RE.finditer(line):
        if m.group("mon"):
            mon, day = MONTHS[m.group("mon").lower()[:3]], int(m.group("day"))
        else:
            mon, day = int(m.group("m")), int(m.group("d"))
        if 1 <= day <= 31:
            out.add((mon, day))
    return out


def uncovered_dates(text: str, found: list[Deadline]) -> set[tuple[int, int]]:
    covered = {(d.due.month, d.due.day) for d in found}
    return {x for line in text.splitlines() for x in dates_in(line)} - covered


def missed_snippets(text: str, found: list[Deadline]) -> str:
    """Lines mentioning a date that no extracted deadline covers, with a little context."""
    covered = {(d.due.month, d.due.day) for d in found}
    lines = [l for l in text.splitlines() if l.strip()]
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if dates_in(line) - covered:
            keep.update(range(max(0, i - 3), min(len(lines), i + 4)))
    return "\n".join(lines[i] for i in sorted(keep))[:CHUNK_CHARS]


def validate(raw: dict, course: str, text: str, source: str, today: date, log) -> Deadline | None:
    title = str(raw.get("title", "")).strip()
    if not title:
        return None
    try:
        due = date.fromisoformat(str(raw.get("due_date", "")).strip()[:10])
    except ValueError:
        log(f"   skipped '{title}': unreadable date {raw.get('due_date')!r}")
        return None
    if abs((due - today).days) > 400:
        log(f"   skipped '{title}': date {due} is far from this term")
        return None
    evidence = str(raw.get("evidence", "")).strip()
    if GUARDS and not evidence_found(evidence, title, text):
        log(f"   skipped '{title}': couldn't find it in the syllabus (possible hallucination)")
        return None
    if GUARDS and RECURRING_RE.search(neighborhood(evidence, text, 80, 80) or evidence):
        log(f"   skipped '{title}': it repeats (\"every ...\"), so one date would be wrong")
        return None
    if GUARDS and not date_near_evidence(due, evidence, text):
        log(f"   skipped '{title}': {due:%b %d} isn't written near it in the syllabus")
        return None
    kind = raw.get("kind") if raw.get("kind") in KINDS else "other"
    try:
        effort = min(5, max(1, int(raw.get("effort", 2))))
    except (TypeError, ValueError):
        effort = 2
    due_time = str(raw.get("due_time") or "").strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", due_time):
        due_time = ""
    if GUARDS:
        # A time has to come from the syllabus: its own sentence, or a syllabus-wide rule.
        own = time_near(evidence, text)
        if own:
            due_time = own
        elif due_time and due_time != blanket_time(text):
            due_time = ""
        if not due_time and kind in BLANKET_KINDS:
            due_time = blanket_time(text)
    try:
        weight = float(raw.get("weight_percent") or 0)
    except (TypeError, ValueError):
        weight = 0.0
    return Deadline(course, title, kind, due, due_time, effort, weight, evidence[:160], source)


def same_thing(a: Deadline, b: Deadline) -> bool:
    if a.source != b.source or a.due != b.due:
        return False
    if a.kind == b.kind or norm(a.evidence) == norm(b.evidence):
        return True
    wa, wb = set(norm(a.title).split()), set(norm(b.title).split())
    return bool(wa and wb) and (wa <= wb or wb <= wa)


def dedupe(items: list[Deadline]) -> list[Deadline]:
    """Merge the same deadline found twice ("Presentation" and "Group presentation")."""
    kept: list[Deadline] = []
    for d in sorted(items, key=lambda d: -len(d.title)):  # prefer the more descriptive title
        if not any(same_thing(d, k) for k in kept):
            kept.append(d)
    return sorted(kept, key=lambda d: (d.due, d.due_time or "99", d.course))


# --------------------------------------------------------------------------- cache

def load_cache(folder: Path) -> dict:
    try:
        return json.loads((folder / CACHE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(folder: Path, cache: dict) -> None:
    try:
        (folder / CACHE_FILE).write_text(json.dumps(cache, indent=1, default=str), encoding="utf-8")
    except OSError:
        pass


def load_done(folder: Path) -> list[str]:
    try:
        return [l.strip().lower() for l in (folder / "done.txt").read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")]
    except OSError:
        return []


def mark_done(folder: Path, phrase: str) -> None:
    with open(folder / "done.txt", "a", encoding="utf-8") as f:
        f.write(phrase.strip() + "\n")


def drop_done(items: list[Deadline], done: list[str]) -> list[Deadline]:
    """Finished items leave the plan. A line in done.txt matches any title that contains it."""
    def hit(phrase: str, d: Deadline) -> bool:
        return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", f"{d.course} {d.title}".lower()) is not None
    return [d for d in items if not any(hit(x, d) for x in done)]


def to_dict(d: Deadline) -> dict:
    out = asdict(d)
    out["due"] = d.due.isoformat()
    return out


def from_dict(x: dict) -> Deadline:
    x = dict(x)
    x["due"] = date.fromisoformat(x["due"])
    return Deadline(**x)


# --------------------------------------------------------------------------- planning

def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def fmt_day(d: date) -> str:
    return d.strftime("%a %b ") + str(d.day)


def label(d: Deadline) -> str:
    t = f" {d.due_time}" if d.due_time else ""
    w = f", {d.weight:g}% of grade" if d.weight else ""
    return f"**{d.course}**: {d.title} (due {fmt_day(d.due)}{t}{w})"


def build_plan(items: list[Deadline], today: date, weeks: int) -> str:
    horizon = monday(today) + timedelta(weeks=weeks)
    lines = [
        "# Your lazy plan",
        "",
        f"_Generated {today.isoformat()} from {len({d.source for d in items})} syllabus file(s). "
        "Start dates are the latest you can begin and still finish comfortably._",
        "",
    ]
    overdue = [d for d in items if d.due < today]
    upcoming = [d for d in items if d.due >= today]

    wk = monday(today)
    while wk < horizon:
        end = wk + timedelta(days=6)
        due_now = [d for d in upcoming if wk <= d.due <= end]
        start_now = [d for d in upcoming if wk <= max(d.start, today) <= end and d.due > end]
        title = "This week" if wk == monday(today) else f"Week of {fmt_day(wk)}"
        hours = sum(HOURS.get(d.effort, 2) for d in due_now)
        lines.append(f"## {title}" + (f"  (about {hours:g} h of work due)" if hours else ""))
        if not due_now and not start_now:
            lines.append("- Nothing. Genuinely nothing. Go lie in a hammock. 🦥")
        for d in due_now:
            begin = max(d.start, today)
            hint = "" if d.start >= d.due else f", start by {fmt_day(begin)}"
            lines.append(f"- [ ] Finish {label(d)}{hint}")
        for d in start_now:
            lines.append(f"- [ ] Start {label(d)}, begin by {fmt_day(max(d.start, today))}")
        lines.append("")
        wk += timedelta(weeks=1)

    later = [d for d in upcoming if d.due >= horizon and max(d.start, today) >= horizon]
    if later:
        lines.append("## Later (not your problem yet)")
        lines += [f"- {label(d)}" for d in later]
        lines.append("")
    if overdue:
        lines.append("## Already past")
        lines += [f"- {label(d)}" for d in overdue]
        lines.append("")
    return "\n".join(lines)


def morning_note(items: list[Deadline], today: date) -> str:
    """Two lines you can read half asleep."""
    soon = [d for d in items if today <= d.due <= today + timedelta(days=2)]
    week = [d for d in items if today <= d.due <= today + timedelta(days=7)]
    if soon:
        first = soon[0]
        head = (f"{len(soon)} due in the next 2 days, first: {first.course}: {first.title} "
                f"({fmt_day(first.due)}).")
    else:
        head = "Nothing due in the next 2 days."
    hours = sum(HOURS.get(d.effort, 2) for d in week)
    tail = f"Next 7 days: {len(week)} due, about {hours:g} h of work." if week else "Next 7 days: nothing due. Go nap. 🦥"
    return f"{head}\n{tail}\n"


def summary(items: list[Deadline], today: date) -> str:
    week = [d for d in items if today <= d.due <= today + timedelta(days=7)]
    starts = [d for d in items if d.due > today + timedelta(days=7) and d.start <= today + timedelta(days=7)]
    if not week and not starts:
        return "Next 7 days: nothing due, nothing to start. Go nap. 🦥"
    out = [f"Next 7 days: {len(week)} thing(s) due, {len(starts)} to start."]
    for d in week:
        out.append(f"  DUE   {fmt_day(d.due):<11} {d.course}: {d.title}")
    for d in starts:
        out.append(f"  START {fmt_day(max(d.start, today)):<11} {d.course}: {d.title} (due {fmt_day(d.due)})")
    return "\n".join(out)


# --------------------------------------------------------------------------- calendar

def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    # RFC 5545: lines longer than 75 octets are folded with CRLF + space
    out, b = [], line.encode()
    while len(b) > 75:
        cut = 75
        while (b[cut] & 0xC0) == 0x80:  # don't split a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode())
        b = b[cut:]
    out.append(b.decode())
    return "\r\n ".join(out)


def build_ics(items: list[Deadline]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//lazy-planner//EN",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:Lazy Planner"]
    for d in items:
        uid = uuid.uuid5(uuid.NAMESPACE_URL, "lazy-planner:" + "|".join(map(str, d.key)))
        lead = (d.due - d.start).days
        desc = f"Start by {d.start.isoformat()}. From {d.source}: \"{d.evidence}\""
        lines += ["BEGIN:VEVENT", f"UID:{uid}@lazy-planner", f"DTSTAMP:{stamp}"]
        if d.due_time:
            hh, mm = d.due_time.split(":")
            lines.append(f"DTSTART:{d.due.strftime('%Y%m%d')}T{hh}{mm}00")
            lines.append("DURATION:PT15M")
        else:
            lines.append(f"DTSTART;VALUE=DATE:{d.due.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{(d.due + timedelta(days=1)).strftime('%Y%m%d')}")
        lines.append(f"SUMMARY:{ics_escape(f'{d.course}: {d.title} due')}")
        lines.append(f"DESCRIPTION:{ics_escape(desc)}")
        lines.append(f"CATEGORIES:{d.kind.upper()}")
        if lead > 0:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY",
                      f"DESCRIPTION:{ics_escape(f'Time to start: {d.title}')}",
                      f"TRIGGER:-P{lead}D", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


# --------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Turn syllabi into a lazy, on-time weekly plan using a local model.")
    ap.add_argument("folder", type=Path, help="folder with your syllabus PDFs / .docx / .txt / .md files")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model (default {DEFAULT_MODEL})")
    ap.add_argument("--host", default=DEFAULT_HOST, help="Ollama server URL")
    ap.add_argument("--out", type=Path, help="where to write plan.md and deadlines.ics (default: the folder)")
    ap.add_argument("--today", type=date.fromisoformat, default=date.today(), help="pretend today is YYYY-MM-DD")
    ap.add_argument("--weeks", type=int, default=4, help="how many weeks to plan in detail (default 4)")
    ap.add_argument("--fresh", action="store_true", help="ignore the cache and re-read every file")
    ap.add_argument("--quiet", action="store_true", help="only print the summary")
    ap.add_argument("--done", action="append", metavar="TEXT",
                    help='mark something finished, e.g. --done "HW 7" (it leaves the plan; edit done.txt to undo)')
    ap.add_argument("--raw", action="store_true", help="trust the model blindly (no evidence check, no second pass)")
    args = ap.parse_args(argv)
    global GUARDS
    GUARDS = not args.raw

    folder: Path = args.folder
    if not folder.is_dir():
        sys.exit(f"Not a folder: {folder}")
    out = args.out or folder
    out.mkdir(parents=True, exist_ok=True)
    log = (lambda *_: None) if args.quiet else (lambda m: print(m, flush=True))

    files = sorted(p for p in folder.iterdir()
                   if p.suffix.lower() in {".pdf", ".docx", ".txt", ".md"} and p.name not in ("plan.md", "morning.txt", "done.txt"))
    if not files:
        sys.exit(f"No .pdf, .docx, .txt or .md files in {folder}")

    cache = {} if args.fresh else load_cache(folder)
    all_items: list[Deadline] = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        ck = f"v{CACHE_VERSION}|{path.name}|{digest}|{args.model}|{'raw' if args.raw else 'guarded'}"
        if ck in cache:
            log(f"📄 {path.name}: unchanged, using saved results")
            all_items += [from_dict(x) for x in cache[ck]]
            continue
        try:
            text = read_text(path)
        except Exception as e:  # a broken PDF shouldn't stop the others
            log(f"📄 {path.name}: couldn't read this file ({e}); skipping")
            continue
        if len(text.strip()) < 40:
            log(f"📄 {path.name}: no text found (is it a scanned image?); skipping")
            continue
        log(f"📄 {path.name}: reading with {args.model} (locally)...")
        items = extract(path, text, args.host, args.model, args.today, log)
        log(f"   found {len(items)} deadline(s)")
        cache[ck] = [to_dict(d) for d in items]
        all_items += items
    save_cache(folder, cache)

    for phrase in args.done or []:
        mark_done(folder, phrase)
        log(f"✔ marked done: {phrase}")
    items = drop_done(dedupe(all_items), load_done(folder))
    (out / "plan.md").write_text(build_plan(items, args.today, args.weeks), encoding="utf-8")
    (out / "deadlines.ics").write_text(build_ics(items), encoding="utf-8", newline="")
    (out / "deadlines.json").write_text(json.dumps([to_dict(d) for d in items], indent=2), encoding="utf-8")
    (out / "morning.txt").write_text(morning_note(items, args.today), encoding="utf-8")

    print()
    print(summary(items, args.today))
    print(f"\n✅ {len(items)} deadline(s) → {out / 'plan.md'}  and  {out / 'deadlines.ics'}")
    print(f"   Morning note: {out / 'morning.txt'}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # emoji on Windows terminals
    sys.exit(main())
