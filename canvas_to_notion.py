import os
import json
import requests
import re
from html import unescape
from datetime import datetime, timezone

# ==============================
# CONFIG
# ==============================
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")

CANVAS_COURSE_IDS_RAW = os.environ.get("CANVAS_COURSE_IDS", "").strip()
CANVAS_COURSE_IDS = {c.strip() for c in CANVAS_COURSE_IDS_RAW.split(",") if c.strip()}

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")
NOTION_DB_TITLE = "Canvas Course - Track Assignments"

# Date filters  
DUE_DATE_PERIOD_START = os.environ.get("DUE_DATE_PERIOD_START", "").strip()
DUE_DATE_PERIOD_END = os.environ.get("DUE_DATE_PERIOD_END", "").strip()
INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE = os.environ.get(
    "INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE", "false"
).lower() == "true"

NOTION_VERSION = "2022-06-28"


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
    text = unescape(text).replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()[:500]


def parse_canvas_date(d):
    if not d:
        return None
    try:
        return datetime.fromisoformat(d.replace("Z", "+00:00"))
    except:
        return None


def parse_filter_date(d):
    if not d:
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except:
        return None


DUE_START_DT = parse_filter_date(DUE_DATE_PERIOD_START)
DUE_END_DT = parse_filter_date(DUE_DATE_PERIOD_END)


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
def get_canvas_courses():
    url = (
        f"{CANVAS_BASE_URL}/api/v1/courses"
        "?enrollment_type=student"
        "&enrollment_state=active"
        "&state[]=available"
        "&per_page=100"
    )
    headers = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}

    r = requests.get(url, headers=headers)
    r.raise_for_status()
    courses = r.json()

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
    headers = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments?per_page=100"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json()


def get_submission(course_id, assignment_id):
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self"
    headers = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return {}
    return r.json()


def due_date_filter_ok(assignment):
    due = parse_canvas_date(assignment.get("due_at"))

    if due is None:
        return INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE

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
def archive_old_db():
    """Archive old DB titled exactly the same."""
    url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children?page_size=50"
    r = requests.get(url, headers=get_headers())
    r.raise_for_status()

    for block in r.json().get("results", []):
        if block["type"] == "child_database":
            title = block["child_database"].get("title")
            if title == NOTION_DB_TITLE:
                db_id = block["id"]
                print(f"Archiving old DB: {db_id}")
                patch = {
                    "archived": True
                }
                r2 = requests.patch(
                    f"https://api.notion.com/v1/databases/{db_id}",
                    headers=get_headers(),
                    json=patch,
                )
                r2.raise_for_status()


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

    url = "https://api.notion.com/v1/databases"
    r = requests.post(url, headers=get_headers(), json=body)
    r.raise_for_status()
    return r.json()["id"]


def create_page(db_id, course, a, submission):
    updated = parse_canvas_date(a.get("updated_at"))
    due = parse_canvas_date(a.get("due_at"))
    submitted = parse_canvas_date(submission.get("submitted_at") if submission else None)

    description_clean = clean_description(a.get("description", ""))

    status = status_from_canvas(a, submission)

    body = {
        "parent": {"database_id": db_id},
        "properties": {
            "Name": {"title": [{"text": {"content": a.get("name", "")}}]},
            "Assignment Updated Date": {"date": {"start": updated.isoformat()} if updated else None},
            "Class": {"rich_text": [{"text": {"content": course["short_name"]}}]},
            "Description": {"rich_text": [{"text": {"content": description_clean}}]},
            "Due Date": {"date": {"start": due.isoformat()} if due else None},
            "ID": {"rich_text": [{"text": {"content": str(a.get("id"))}}]},
            "Link": {"url": a.get("html_url")},
            "Points": {"number": a.get("points_possible")},
            "Score": {"number": submission.get("score") if submission else None},
            "Status": {"select": {"name": status}},
            "Submitted Date": {"date": {"start": submitted.isoformat()} if submitted else None},
        },
    }

    r = requests.post("https://api.notion.com/v1/pages", headers=get_headers(), json=body)
    r.raise_for_status()


# ==============================
# MAIN
# ==============================
def main():
    if not NOTION_API_KEY or not NOTION_PARENT_PAGE_ID:
        raise Exception("Missing NOTION_API_KEY or NOTION_PARENT_PAGE_ID")

    # 1) Canvas courses
    course_map = get_canvas_courses()
    if not course_map:
        print("No courses found.")
        return

    # 2) Archive old DB
    archive_old_db()

    # 3) Create new DB
    db_id = create_db()
    print(f"Created DB {db_id}")

    # 4) Process assignments
    for cid, info in course_map.items():
        assignments = get_assignments(cid)

        for a in assignments:
            if not due_date_filter_ok(a):
                continue

            submission = get_submission(cid, a["id"])
            create_page(db_id, info, a, submission)

    print("Sync complete.")


if __name__ == "__main__":
    main()
