import os
import json
from datetime import datetime, timedelta, timezone

import requests

# ==========================================================
# CONFIG (matches your n8n flow as closely as possible)
# ==========================================================

CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")

# Optional filter: comma-separated course IDs: "7229,7243"
CANVAS_COURSE_IDS_RAW = os.environ.get("CANVAS_COURSE_IDS", "").strip()
CANVAS_COURSE_IDS = [
    c.strip() for c in CANVAS_COURSE_IDS_RAW.split(",") if c.strip()
]

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")
NOTION_DB_TITLE = os.environ.get(
    "NOTION_DB_TITLE", "Canvas Course - Track Assignments"
)

NOTION_VERSION = "2022-06-28"


# ----------------------------------------------------------
# Helpers
# ----------------------------------------------------------

def notion_headers(extra=None):
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def canvas_headers():
    return {
        "Authorization": f"Bearer {CANVAS_API_TOKEN}",
    }


def require_env(name, value):
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# ==========================================================
# NOTION – DB lifecycle (mirror n8n)
# ==========================================================

def get_latest_db_id():
    """
    n8n: "Get DB in Page" + "Get DB Number"

    GET /v1/blocks/{parent_page_id}/children
    Find the child_database whose title is NOTION_DB_TITLE.
    """
    require_env("NOTION_PARENT_PAGE_ID", NOTION_PARENT_PAGE_ID)

    url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children?page_size=100"
    resp = requests.get(url, headers=notion_headers())
    resp.raise_for_status()
    data = resp.json()

    results = data.get("results", [])
    for block in results:
        if block.get("type") != "child_database":
            continue

        child_db = block.get("child_database", {})
        title = child_db.get("title")
        archived = block.get("archived", False)

        if title == NOTION_DB_TITLE and not archived:
            # In Notion, child_database.id is the database ID.
            return child_db["id"]

    raise RuntimeError(
        f"No active child database titled '{NOTION_DB_TITLE}' found under parent page."
    )


def get_db_schema(database_id):
    """
    n8n: "Read Old DB Schema"
    """
    url = f"https://api.notion.com/v1/databases/{database_id}"
    resp = requests.get(url, headers=notion_headers())
    resp.raise_for_status()
    return resp.json()


def _clean_descriptions(obj):
    """
    n8n: JS in "Prepare New DB JSON"

    Recursively remove empty/null 'description' fields.
    """
    if isinstance(obj, list):
        return [_clean_descriptions(x) for x in obj]

    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            if k == "description" and (v is None or v == "" or v == []):
                # drop it
                continue
            new_obj[k] = _clean_descriptions(v)
        return new_obj

    return obj


def build_new_db_payload(schema):
    """
    Build a POST /v1/databases payload using the old schema,
    like n8n "Prepare New DB JSON".
    """
    require_env("NOTION_PARENT_PAGE_ID", NOTION_PARENT_PAGE_ID)

    cleaned = _clean_descriptions(schema)

    # Title is an array of rich-text; reuse as-is.
    title = cleaned.get("title", [])

    properties = cleaned.get("properties", {})

    payload = {
        "parent": {
            "type": "page_id",
            "page_id": NOTION_PARENT_PAGE_ID,
        },
        "title": title,
        "properties": properties,
    }
    return payload


def create_new_database(payload):
    """
    n8n: "Create New Database"
    """
    url = "https://api.notion.com/v1/databases"
    resp = requests.post(url, headers=notion_headers(), data=json.dumps(payload))
    resp.raise_for_status()
    return resp.json()


def archive_database(database_id):
    """
    n8n: "Archive Old Database"
    PATCH /v1/databases/{id} { "archived": true }
    """
    url = f"https://api.notion.com/v1/databases/{database_id}"
    body = {"archived": True}
    resp = requests.patch(url, headers=notion_headers(), data=json.dumps(body))
    resp.raise_for_status()
    return resp.json()


# ==========================================================
# CANVAS – courses & assignments
# ==========================================================

def get_canvas_courses():
    """
    n8n: "Get Canvas Courses"

    Logic:
      - If CANVAS_COURSE_IDS is set: fetch ONLY those course IDs.
      - Else: fetch all "active/available" courses for the token user.
    """
    require_env("CANVAS_API_TOKEN", CANVAS_API_TOKEN)

    courses = []

    if CANVAS_COURSE_IDS:
        print(f"▶ Using CANVAS_COURSE_IDS filter from environment: {CANVAS_COURSE_IDS}")
        for cid in CANVAS_COURSE_IDS:
            url = f"{CANVAS_BASE_URL}/api/v1/courses/{cid}"
            r = requests.get(url, headers=canvas_headers())
            r.raise_for_status()
            course = r.json()
            # Only keep "available" or "active" style courses
            if course.get("workflow_state") in ("available", "active"):
                courses.append(course)
        return courses

    print("▶ No CANVAS_COURSE_IDS set – loading all active Canvas courses…")
    url = f"{CANVAS_BASE_URL}/api/v1/courses"
    params = {
        "include[]": "term",
        "state[]": "available",  # closest to n8n "current" behaviour
    }

    while url:
        r = requests.get(url, headers=canvas_headers(), params=params)
        r.raise_for_status()
        batch = r.json()
        for c in batch:
            if c.get("workflow_state") in ("available", "active"):
                courses.append(c)

        # Pagination via Link header
        next_url = None
        links = r.headers.get("Link", "")
        for part in links.split(","):
            if 'rel="next"' in part:
                next_url = part[part.find("<") + 1 : part.find(">")]
                break
        url = next_url
        params = None  # only include params on first page

    return courses


def get_canvas_assignments(course_id):
    """
    n8n: "Get Canvas Assignments"
    """
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments"
    params = {
        "include[]": ["submission"],
    }

    assignments = []
    while url:
        r = requests.get(url, headers=canvas_headers(), params=params)
        r.raise_for_status()
        assignments.extend(r.json())

        links = r.headers.get("Link", "")
        next_url = None
        for part in links.split(","):
            if 'rel="next"' in part:
                next_url = part[part.find("<") + 1 : part.find(">")]
                break
        url = next_url
        params = None

    return assignments


def filter_assignments(assignments, now_utc):
    """
    n8n: "Filter Assignments" (approximate)

    Keep:
      - assignments that have a due_at
      - due date within the next 7 days (or today) AND not extremely old.
    """
    window_days = 7
    filtered = []

    for a in assignments:
        due_at = a.get("due_at")
        if not due_at:
            continue

        try:
            due = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        except Exception:
            continue

        diff = due - now_utc
        if -window_days <= diff.days <= window_days:
            filtered.append(a)

    return filtered


def determine_status(assignment, now_utc):
    """
    Approximation of your n8n "Transform Data" status logic.

    Uses:
      - submission (if present)
      - due date vs now
    """
    due_at = assignment.get("due_at")
    submission = assignment.get("submission") or {}
    submitted_at = submission.get("submitted_at")
    graded_at = submission.get("graded_at")
    late = submission.get("late", False)

    # If submitted
    if submitted_at or graded_at or submission.get("workflow_state") in (
        "graded",
        "submitted",
    ):
        return "Completed"

    if not due_at:
        return "Pending"

    due = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    if due < now_utc:
        return "Overdue" if late else "Missing"

    return "Pending"


def create_notion_page_for_assignment(db_id, course, assignment, now_utc):
    """
    n8n: "Create Notion Page"
    """
    course_name = course.get("name", f"Course {course.get('id')}")
    due_at = assignment.get("due_at")

    notion_properties = {
        "Assignment Name": {
            "title": [
                {"text": {"content": assignment.get("name", "Untitled assignment")}}
            ]
        },
        "Course": {
            "rich_text": [
                {"text": {"content": course_name}},
            ]
        },
    }

    if due_at:
        due = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        notion_properties["Due Date"] = {
            "date": {"start": due.astimezone(timezone.utc).isoformat()}
        }

    status = determine_status(assignment, now_utc)
    notion_properties["Status"] = {"select": {"name": status}}

    payload = {
        "parent": {"database_id": db_id},
        "properties": notion_properties,
    }

    url = "https://api.notion.com/v1/pages"
    resp = requests.post(url, headers=notion_headers(), data=json.dumps(payload))
    resp.raise_for_status()


# ==========================================================
# MAIN – orchestrate whole flow
# ==========================================================

def main():
    require_env("NOTION_API_KEY", NOTION_API_KEY)
    require_env("NOTION_PARENT_PAGE_ID", NOTION_PARENT_PAGE_ID)
    require_env("CANVAS_API_TOKEN", CANVAS_API_TOKEN)

    print("▶ Connecting to Notion…")

    # 1) Find current DB under parent page
    old_db_id = get_latest_db_id()
    print(f"   Current database id: {old_db_id}")

    # 2) Read its schema
    old_schema = get_db_schema(old_db_id)

    # 3) Prepare payload for new DB
    new_db_payload = build_new_db_payload(old_schema)

    # 4) Create new DB (same title, same schema)
    new_db = create_new_database(new_db_payload)
    new_db_id = new_db["id"]
    print(f"   Created new database id: {new_db_id}")

    # 5) Archive old DB
    archive_database(old_db_id)
    print("   Archived old database.")

    # 6) Canvas: get courses
    print("▶ Fetching Canvas courses…")
    courses = get_canvas_courses()
    print(f"   Found {len(courses)} candidate courses.")

    now_utc = datetime.now(timezone.utc)

    # 7) For each course, get assignments, filter, and write to Notion
    for course in courses:
        cid = course.get("id")
        print(f"▶ Processing Canvas course {cid} ({course.get('name')})…")

        assignments = get_canvas_assignments(cid)
        filtered = filter_assignments(assignments, now_utc)
        print(f"   Found {len(filtered)} assignments in 7-day window.")

        for a in filtered:
            create_notion_page_for_assignment(new_db_id, course, a, now_utc)

    print("✅ Sync complete.")


if __name__ == "__main__":
    main()
