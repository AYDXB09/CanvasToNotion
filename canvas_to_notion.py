"""Canvas -> Notion assignment sync.

Reads every active Canvas course and its assignments, then archives the old Notion
database with the configured title and builds a fresh one. Runs weekly in GitHub
Actions (see .github/workflows/canvas_to_notion.yml) and can be run by hand.

All Canvas data is read BEFORE Notion is touched, so a Canvas failure leaves your
existing Notion database exactly as it was.
"""
import os
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape

import requests

# ==============================
# CONFIG (environment variables)
# ==============================
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com").rstrip("/")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")

CANVAS_COURSE_IDS_RAW = os.environ.get("CANVAS_COURSE_IDS", "").strip()
CANVAS_COURSE_IDS = {c.strip() for c in CANVAS_COURSE_IDS_RAW.split(",") if c.strip()}

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")
NOTION_DB_TITLE = os.environ.get("NOTION_DB_TITLE", "").strip() or "Canvas Course - Track Assignments"

DUE_DATE_PERIOD_START = os.environ.get("DUE_DATE_PERIOD_START", "").strip()
DUE_DATE_PERIOD_END = os.environ.get("DUE_DATE_PERIOD_END", "").strip()
INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE = os.environ.get(
    "INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE", "false"
).strip().lower() == "true"

NOTION_VERSION = "2022-06-28"
TIMEOUT = 30            # seconds, for every HTTP request
NOTION_RETRIES = 5      # extra attempts when Notion answers 429 (rate limited)


# ==============================
# HELPERS
# ==============================
def get_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def clean_description(html_text):
    """Canvas HTML -> plain text for a Notion text field (max 500 characters).

    Block-level tags and <br> become line breaks so paragraphs and list items stay
    separate; script/style content is dropped; entities (&amp; &eacute; ...) are decoded.
    """
    if not html_text:
        return ""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", html_text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|blockquote)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()[:500]


def parse_canvas_date(d):
    if not d:
        return None
    try:
        return datetime.fromisoformat(d.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def parse_filter_date(value, name):
    """YYYY-MM-DD -> midnight UTC. Blank means 'no filter'; anything else malformed stops the run
    (it used to be ignored silently, which switched the filter off without telling you)."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit(f"{name} must look like 2025-11-20 (YYYY-MM-DD), got {value!r}")


DUE_START_DT = parse_filter_date(DUE_DATE_PERIOD_START, "DUE_DATE_PERIOD_START")
DUE_END_DT = parse_filter_date(DUE_DATE_PERIOD_END, "DUE_DATE_PERIOD_END")


def status_from_canvas(assignment, submission):
    """Map Canvas's own record of *this student's* submission to a Notion status.

    Completed    - submitted, graded or pending review
    Overdue      - not submitted and the due date has passed
    Not Started  - not submitted, due date still ahead (or none)
    In Progress  - only when the submission lookup failed and the due date is ahead

    The assignment-wide `has_submitted_submissions` flag is deliberately ignored:
    it means "*any* student has submitted", not "I have".
    """
    sub_state = (submission or {}).get("workflow_state")
    due_dt = parse_canvas_date(assignment.get("due_at"))
    overdue = bool(due_dt and due_dt < datetime.now(timezone.utc))

    if sub_state in ("graded", "submitted", "pending_review"):
        return "Completed"
    if sub_state == "unsubmitted":
        return "Overdue" if overdue else "Not Started"

    # No usable submission record (the lookup failed): fall back to the due date alone.
    if overdue:
        return "Overdue"
    return "In Progress" if due_dt else "Not Started"


# ==============================
# CANVAS LOGIC
# ==============================
def canvas_headers():
    return {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}


def canvas_get_all(path, params=None):
    """GET a Canvas list endpoint and follow its `Link: rel="next"` pages.

    Canvas returns at most `per_page` items per response; without following the links,
    anything past the first page (courses or assignments) is silently dropped.
    """
    url = f"{CANVAS_BASE_URL}{path}"
    items = []
    while url:
        r = requests.get(url, headers=canvas_headers(), params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items.extend(data if isinstance(data, list) else [data])
        url = r.links.get("next", {}).get("url")
        params = None  # the next-page URL already carries the query string
    return items


def get_canvas_courses():
    courses = canvas_get_all(
        "/api/v1/courses",
        {"enrollment_type": "student", "enrollment_state": "active", "state[]": "available", "per_page": 100},
    )
    if CANVAS_COURSE_IDS:
        courses = [c for c in courses if str(c["id"]) in CANVAS_COURSE_IDS]

    course_map = {}
    for c in courses:
        cid = str(c["id"])
        course_map[cid] = {
            "short_name": c.get("course_code") or c.get("name"),
            "full_name": c.get("name"),
        }
    return course_map


def get_assignments(course_id):
    return canvas_get_all(f"/api/v1/courses/{course_id}/assignments", {"per_page": 100})


def get_submission(course_id, assignment_id):
    """This student's submission record, or {} if Canvas can't provide it
    (status then falls back to the due date - see status_from_canvas)."""
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self"
    try:
        r = requests.get(url, headers=canvas_headers(), timeout=TIMEOUT)
    except requests.RequestException:
        return {}
    if r.status_code != 200:
        return {}
    return r.json()


def due_date_filter_ok(assignment):
    due = parse_canvas_date(assignment.get("due_at"))

    if due is None:
        return INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE

    # Dates are midnight UTC: START is inclusive of that day, END stops at the start of that day.
    if DUE_START_DT and DUE_END_DT:
        return DUE_START_DT <= due <= DUE_END_DT

    if DUE_END_DT and not DUE_START_DT:
        return due <= DUE_END_DT

    if DUE_START_DT and not DUE_END_DT:
        return due >= DUE_START_DT

    return True


# ==============================
# NOTION LOGIC
# ==============================
def notion_request(method, url, **kwargs):
    """Call Notion, waiting and retrying when it answers 429 (rate limited)."""
    for attempt in range(NOTION_RETRIES + 1):
        r = requests.request(method, url, headers=get_headers(), timeout=TIMEOUT, **kwargs)
        if r.status_code != 429 or attempt == NOTION_RETRIES:
            r.raise_for_status()
            return r
        time.sleep(float(r.headers.get("Retry-After", 1)))


def archive_old_db():
    """Archive every database under the parent page whose title matches NOTION_DB_TITLE exactly."""
    cursor = None
    while True:
        url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        data = notion_request("GET", url).json()

        for block in data.get("results", []):
            if block["type"] == "child_database" and block["child_database"].get("title") == NOTION_DB_TITLE:
                print(f"Archiving old DB: {block['id']}")
                notion_request("PATCH", f"https://api.notion.com/v1/databases/{block['id']}", json={"archived": True})

        if not data.get("has_more"):
            return
        cursor = data["next_cursor"]


def create_db():
    schema = {
        # EXACT LEGACY SCHEMA (Version A)
        "Name": {"title": {}},
        "Assignment Updated Date": {"date": {}},
        "Class": {"rich_text": {}},
        "Description": {"rich_text": {}},
        "Due Date": {"date": {}},
        "ID": {"rich_text": {}},
        "Link": {"url": {}},
        "Points": {"number": {}},
        "Score": {"number": {}},
        "Status": {
            "select": {
                "options": [
                    {"name": "Overdue", "color": "yellow"},
                    {"name": "In Progress", "color": "orange"},
                    {"name": "Completed", "color": "green"},
                    {"name": "Not Started", "color": "blue"},
                ]
            }
        },
        "Submitted Date": {"date": {}},
    }

    body = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "title": [{"type": "text", "text": {"content": NOTION_DB_TITLE}}],
        "properties": schema,
    }
    return notion_request("POST", "https://api.notion.com/v1/databases", json=body).json()["id"]


def create_page(db_id, course, a, submission):
    updated = parse_canvas_date(a.get("updated_at"))
    due = parse_canvas_date(a.get("due_at"))
    submitted = parse_canvas_date(submission.get("submitted_at") if submission else None)

    body = {
        "parent": {"database_id": db_id},
        "properties": {
            "Name": {"title": [{"text": {"content": a.get("name", "")}}]},
            "Assignment Updated Date": {"date": {"start": updated.isoformat()} if updated else None},
            "Class": {"rich_text": [{"text": {"content": course["short_name"]}}]},
            "Description": {"rich_text": [{"text": {"content": clean_description(a.get("description", ""))}}]},
            "Due Date": {"date": {"start": due.isoformat()} if due else None},
            "ID": {"rich_text": [{"text": {"content": str(a.get("id"))}}]},
            "Link": {"url": a.get("html_url")},
            "Points": {"number": a.get("points_possible")},
            "Score": {"number": submission.get("score") if submission else None},
            "Status": {"select": {"name": status_from_canvas(a, submission)}},
            "Submitted Date": {"date": {"start": submitted.isoformat()} if submitted else None},
        },
    }
    notion_request("POST", "https://api.notion.com/v1/pages", json=body)


# ==============================
# MAIN
# ==============================
def main():
    missing = [name for name, value in (("CANVAS_API_TOKEN", CANVAS_API_TOKEN), ("NOTION_API_KEY", NOTION_API_KEY),
                                        ("NOTION_PARENT_PAGE_ID", NOTION_PARENT_PAGE_ID)) if not value]
    if missing:
        sys.exit("Missing required setting(s): " + ", ".join(missing))

    # 1) Read EVERYTHING from Canvas first. If this fails, Notion has not been touched.
    course_map = get_canvas_courses()
    if not course_map:
        print("No courses found.")
        return

    work, lookups_failed = [], 0
    for cid, info in course_map.items():
        for a in get_assignments(cid):
            if not due_date_filter_ok(a):
                continue
            submission = get_submission(cid, a["id"])
            lookups_failed += not submission
            work.append((info, a, submission))
    print(f"Read {len(work)} assignment(s) from {len(course_map)} course(s).")
    if lookups_failed:
        print(f"Note: {lookups_failed} submission lookup(s) returned nothing; "
              "those rows use a due-date-based status.")

    # 2) Replace the Notion database.
    archive_old_db()
    db_id = create_db()
    print(f"Created DB {db_id}")

    # 3) Fill it.
    for info, a, submission in work:
        create_page(db_id, info, a, submission)

    print("Sync complete.")


if __name__ == "__main__":
    main()
