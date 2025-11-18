import os
import json
import requests
from datetime import datetime, timezone
from html import unescape
import re

# =====================================================================
# ENVIRONMENT VARIABLES
# =====================================================================
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "").strip()
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN", "").strip()

CANVAS_COURSE_IDS_RAW = os.environ.get("CANVAS_COURSE_IDS", "").strip()
CANVAS_COURSE_IDS = {c.strip() for c in CANVAS_COURSE_IDS_RAW.split(",") if c.strip()}

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "").strip()
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()
NOTION_DB_TITLE = "Canvas Course - Track Assignments"

NOTION_VERSION = "2022-06-28"

# Due date filters
DUE_DATE_START = os.environ.get("DUE_DATE_PERIOD_START", "").strip()
DUE_DATE_END = os.environ.get("DUE_DATE_PERIOD_END", "").strip()
INCLUDE_NO_DUE = os.environ.get("INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE", "false").lower() == "true"


# =====================================================================
# SANITIZERS (Required to avoid Notion 400 "Bad Request")
# =====================================================================
def safe_date_prop(dt):
    if not dt:
        return None
    return {"start": dt.isoformat()}


def safe_number_prop(value):
    if value is None:
        return None
    try:
        return float(value)
    except:
        return None


def safe_text(txt):
    if not txt:
        return ""
    cleaned = unescape(txt)
    cleaned = re.sub("<[^>]+>", "", cleaned)  # remove HTML tags
    return cleaned.strip()


# =====================================================================
# DATE PARSERS
# =====================================================================
def parse_canvas_dt(dt):
    if not dt:
        return None
    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except:
        return None


def parse_filter_dt(dt):
    if not dt:
        return None
    try:
        return datetime.strptime(dt, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except:
        return None


DUE_START_DT = parse_filter_dt(DUE_DATE_START)
DUE_END_DT = parse_filter_dt(DUE_DATE_END)


# =====================================================================
# CANVAS API
# =====================================================================
def canvas_headers():
    return {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}


def get_canvas_courses():
    url = (
        f"{CANVAS_BASE_URL}/api/v1/courses"
        "?enrollment_type=student&enrollment_state=active&state[]=available&per_page=100"
    )
    resp = requests.get(url, headers=canvas_headers())
    resp.raise_for_status()
    courses = resp.json()

    return {
        str(c["id"]): {
            "full": c.get("name", f"Course {c['id']}"),
            "short": c.get("course_code", c.get("name", "")),
        }
        for c in courses
        if not CANVAS_COURSE_IDS or str(c["id"]) in CANVAS_COURSE_IDS
    }


def get_canvas_assignments(cid):
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{cid}/assignments?per_page=100"
    resp = requests.get(url, headers=canvas_headers())
    resp.raise_for_status()
    return resp.json()


# =====================================================================
# ASSIGNMENT FILTER
# =====================================================================
def include_assignment(a):
    due = parse_canvas_dt(a.get("due_at"))

    if due is None:
        return INCLUDE_NO_DUE

    # 1) both present
    if DUE_START_DT and DUE_END_DT:
        return DUE_START_DT <= due <= DUE_END_DT

    # 2) only END
    if DUE_END_DT:
        return due <= DUE_END_DT

    # 3) only START
    if DUE_START_DT:
        return due >= DUE_START_DT

    return True


# =====================================================================
# NOTION — SCHEMA A (Legacy v1)
# =====================================================================
def legacy_schema_properties():
    return {
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


def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


# =====================================================================
# NOTION – FIND & ARCHIVE LEGACY DB
# =====================================================================
def find_existing_db():
    url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children?page_size=100"
    resp = requests.get(url, headers=notion_headers())
    resp.raise_for_status()
    data = resp.json()

    for child in data.get("results", []):
        if child["type"] == "child_database":
            title = child["child_database"].get("title", "")
            if title == NOTION_DB_TITLE:
                return child["id"]
    return None


def archive_db(db_id):
    url = f"https://api.notion.com/v1/databases/{db_id}"
    resp = requests.patch(url, headers=notion_headers(), json={"archived": True})
    resp.raise_for_status()


# =====================================================================
# NOTION – CREATE NEW DB
# =====================================================================
def create_database():
    url = "https://api.notion.com/v1/databases"
    body = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "title": [{"type": "text", "text": {"content": NOTION_DB_TITLE}}],
        "properties": legacy_schema_properties(),
    }

    resp = requests.post(url, headers=notion_headers(), json=body)
    resp.raise_for_status()
    return resp.json()["id"]


# =====================================================================
# NOTION – CREATE PAGE (Assignment Row)
# =====================================================================
def create_page(db_id, course_info, a):
    due = parse_canvas_dt(a.get("due_at"))
    updated = parse_canvas_dt(a.get("updated_at"))
    submitted = parse_canvas_dt(a.get("submitted_at"))

    body = {
        "parent": {"database_id": db_id},
        "properties": {
            "Name": {"title": [{"text": {"content": safe_text(a.get("name"))}}]},
            "Assignment Updated Date": safe_date_prop(updated),
            "Class": {"rich_text": [{"text": {"content": safe_text(course_info["short"])}}]},
            "Description": {"rich_text": [{"text": {"content": safe_text(a.get("description"))}}]},
            "Due Date": safe_date_prop(due),
            "ID": {"rich_text": [{"text": {"content": str(a.get("id"))}}]},
            "Link": {"url": a.get("html_url")},
            "Points": {"number": safe_number_prop(a.get("points_possible"))},
            "Score": {"number": safe_number_prop(a.get("score"))},
            "Status": {"select": {"name": "Not Started"}},
            "Submitted Date": safe_date_prop(submitted),
        },
    }

    resp = requests.post("https://api.notion.com/v1/pages", headers=notion_headers(), json=body)
    resp.raise_for_status()


# =====================================================================
# MAIN SYNC
# =====================================================================
def main():
    print("🚀 Starting Canvas → Notion sync…")

    if not (CANVAS_API_TOKEN and NOTION_API_KEY and NOTION_PARENT_PAGE_ID):
        raise Exception("❌ Missing required environment variables.")

    courses = get_canvas_courses()
    print(f"📘 Active filtered courses: {list(courses.keys())}")

    assignments = []

    for cid in courses:
        items = get_canvas_assignments(cid)
        for a in items:
            if include_assignment(a):
                assignments.append((cid, a))

    print(f"📦 Assignments after filtering: {len(assignments)}")

    old_db = find_existing_db()
    if old_db:
        print(f"🗑 Archiving old DB: {old_db}")
        archive_db(old_db)

    print("📚 Creating new Notion DB…")
    new_db = create_database()
    print(f"✅ Created DB: {new_db}")

    print("📝 Creating pages…")
    for cid, a in assignments:
        create_page(new_db, courses[cid], a)

    print("🎉 Sync complete!")


if __name__ == "__main__":
    main()
