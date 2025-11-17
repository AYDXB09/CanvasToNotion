import os
import requests
import json
from datetime import datetime, timezone

# -------------------------------------------------
# ENVIRONMENT VARIABLES
# -------------------------------------------------
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")
CANVAS_COURSE_IDS = os.environ.get("CANVAS_COURSE_IDS", "")

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")
NOTION_DATABASE_NAME = os.environ.get("NOTION_DATABASE_NAME", "Canvas Assignments")

DUE_DATE_PERIOD_START = os.environ.get("DUE_DATE_PERIOD_START", "").strip()
DUE_DATE_PERIOD_END = os.environ.get("DUE_DATE_PERIOD_END", "").strip()
INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE = os.environ.get("INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE", "false").lower() == "true"

NOTION_VERSION = "2022-06-28"

# -------------------------------------------------
# HELPERS
# -------------------------------------------------

def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def parse_canvas_date(d):
    if not d:
        return None
    try:
        return datetime.fromisoformat(d.replace("Z", "+00:00"))
    except Exception:
        return None


def parse_filter_date(d):
    if not d:
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except:
        return None


DUE_DATE_START_DT = parse_filter_date(DUE_DATE_PERIOD_START)
DUE_DATE_END_DT = parse_filter_date(DUE_DATE_PERIOD_END)

# -------------------------------------------------
# CANVAS API
# -------------------------------------------------

def get_canvas_courses():
    url = f"{CANVAS_BASE_URL}/api/v1/courses?enrollment_state=active"
    r = requests.get(url, headers={"Authorization": f"Bearer {CANVAS_API_TOKEN}"})
    r.raise_for_status()
    return r.json()


def get_canvas_assignments(course_id):
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments"
    r = requests.get(url, headers={"Authorization": f"Bearer {CANVAS_API_TOKEN}"})
    r.raise_for_status()
    return r.json()

# -------------------------------------------------
# DATE FILTERING
# -------------------------------------------------

def should_include_assignment(a):
    due = parse_canvas_date(a.get("due_at"))

    if due is None:
        return INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE

    # both given
    if DUE_DATE_START_DT and DUE_DATE_END_DT:
        return DUE_DATE_START_DT <= due <= DUE_DATE_END_DT

    # only end
    if DUE_DATE_END_DT:
        return due <= DUE_DATE_END_DT

    # only start
    if DUE_DATE_START_DT:
        return due >= DUE_DATE_START_DT

    return True

# -------------------------------------------------
# NOTION DB MANAGEMENT
# -------------------------------------------------

def find_child_database_under_parent():
    url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children?page_size=100"
    r = requests.get(url, headers=notion_headers())
    r.raise_for_status()
    for c in r.json().get("results", []):
        if c.get("type") == "child_database":
            return c["id"]
    return None


def archive_database(db_id):
    url = f"https://api.notion.com/v1/databases/{db_id}"
    r = requests.patch(url, headers=notion_headers(), json={"archived": True})
    r.raise_for_status()


def create_new_database():
    url = "https://api.notion.com/v1/databases"

    schema = {
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
                    {"name": "Not Started", "color": "blue"}
                ]
            }
        },
        "Submitted Date": {"date": {}},
    }

    payload = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "title": [{"type": "text", "text": {"content": NOTION_DATABASE_NAME}}],
        "properties": schema,
    }

    r = requests.post(url, headers=notion_headers(), json=payload)
    r.raise_for_status()
    return r.json()["id"]

# -------------------------------------------------
# PAGE CREATION (FIXED)
# -------------------------------------------------

def create_notion_page(db_id, a, course_name):
    due = parse_canvas_date(a.get("due_at"))
    updated = parse_canvas_date(a.get("updated_at"))
    submitted = parse_canvas_date(a.get("submitted_at"))

    props = {
        "Name": {"title": [{"text": {"content": a.get("name", "")}}]},
        "Class": {"rich_text": [{"text": {"content": course_name}}]},
        "Description": {"rich_text": [{"text": {"content": a.get("description") or ""}}]},
        "ID": {"rich_text": [{"text": {"content": str(a.get("id"))}}]},
        "Link": {"url": a.get("html_url")},
        "Points": {"number": a.get("points_possible")},
        "Score": {"number": a.get("score") if a.get("score") is not None else None},
        "Status": {"select": {"name": "Not Started"}},
    }

    # ADD DATES ONLY IF THEY EXIST
    if updated:
        props["Assignment Updated Date"] = {"date": {"start": updated.isoformat()}}

    if due:
        props["Due Date"] = {"date": {"start": due.isoformat()}}

    if submitted:
        props["Submitted Date"] = {"date": {"start": submitted.isoformat()}}

    payload = {
        "parent": {"database_id": db_id},
        "properties": props
    }

    r = requests.post("https://api.notion.com/v1/pages", headers=notion_headers(), json=payload)
    r.raise_for_status()

# -------------------------------------------------
# MAIN
# -------------------------------------------------

def main():
    if not NOTION_API_KEY or not NOTION_PARENT_PAGE_ID:
        raise Exception("Missing NOTION_API_KEY or NOTION_PARENT_PAGE_ID")

    old_db = find_child_database_under_parent()
    if old_db:
        archive_database(old_db)

    new_db_id = create_new_database()
    print(f"Created new DB: {new_db_id}")

    course_filter = [c.strip() for c in CANVAS_COURSE_IDS.split(",") if c.strip()]
    print(f"Filtering Canvas courses: {course_filter}")

    all_courses = get_canvas_courses()

    for course in all_courses:
        cid = str(course["id"])
        cname = course.get("name")

        if course_filter and cid not in course_filter:
            continue

        print(f"Processing Canvas course {cid} ({cname})…")

        assignments = get_canvas_assignments(cid)

        for a in assignments:
            if should_include_assignment(a):
                create_notion_page(new_db_id, a, cname)

    print("Sync complete.")


if __name__ == "__main__":
    main()
