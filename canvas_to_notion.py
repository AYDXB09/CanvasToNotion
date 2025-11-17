import os
import json
import requests
import html
from datetime import datetime, timezone
from bs4 import BeautifulSoup

# ===========================================================
# ENV VARIABLES
# ===========================================================
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "").rstrip("/")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")
CANVAS_COURSE_IDS_RAW = os.environ.get("CANVAS_COURSE_IDS", "")

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")
NOTION_VERSION = "2022-06-28"

DUE_DATE_START = os.environ.get("DUE_DATE_PERIOD_START", "").strip()
DUE_DATE_END = os.environ.get("DUE_DATE_PERIOD_END", "").strip()
INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE = (
    os.environ.get("INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE", "false").lower() == "true"
)

NOTION_DB_TITLE = "Canvas Course - Track Assignments"

CANVAS_COURSE_IDS = {
    c.strip()
    for c in CANVAS_COURSE_IDS_RAW.split(",")
    if c.strip()
}


# ===========================================================
# HELPERS
# ===========================================================
def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def parse_iso_date(dstr):
    if not dstr:
        return None
    try:
        return datetime.fromisoformat(dstr.replace("Z", "+00:00"))
    except:
        return None


def parse_filter_date(dstr):
    if not dstr:
        return None
    try:
        return datetime.strptime(dstr, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except:
        return None


DUE_START_DT = parse_filter_date(DUE_DATE_START)
DUE_END_DT = parse_filter_date(DUE_DATE_END)


def html_to_plain(text):
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text(separator=" ").strip()


# ===========================================================
# CANVAS API
# ===========================================================
def get_canvas_courses():
    url = (
        f"{CANVAS_BASE_URL}/api/v1/courses"
        "?enrollment_type=student"
        "&enrollment_state=active"
        "&state[]=available"
        "&per_page=100"
    )

    r = requests.get(url, headers={"Authorization": f"Bearer {CANVAS_API_TOKEN}"})
    r.raise_for_status()
    courses = r.json()

    if CANVAS_COURSE_IDS:
        courses = [c for c in courses if str(c["id"]) in CANVAS_COURSE_IDS]

    mapping = {}
    for c in courses:
        cid = str(c["id"])
        mapping[cid] = {
            "short_name": c.get("course_code") or cid,
            "full_name": c.get("name") or cid,
        }

    return mapping


def get_canvas_assignments(course_id):
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments?per_page=100"
    r = requests.get(url, headers={"Authorization": f"Bearer {CANVAS_API_TOKEN}"})
    r.raise_for_status()
    return r.json()


# ===========================================================
# FILTERS
# ===========================================================
def should_include(a):
    due = parse_iso_date(a.get("due_at"))

    if due is None:
        return INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE

    if DUE_START_DT and DUE_END_DT:
        return DUE_START_DT <= due <= DUE_END_DT

    if DUE_END_DT and not DUE_START_DT:
        return due <= DUE_END_DT

    if DUE_START_DT and not DUE_END_DT:
        return due >= DUE_START_DT

    return True


# ===========================================================
# NOTION — ARCHIVING OLD DB
# ===========================================================
def archive_existing_db():
    url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children?page_size=100"
    r = requests.get(url, headers=notion_headers())
    r.raise_for_status()

    for block in r.json().get("results", []):
        if block["type"] == "child_database":
            title = block["child_database"].get("title")
            if title == NOTION_DB_TITLE:
                db_id = block["id"]
                print(f"🗑 Archiving old DB: {db_id}")
                patch_url = f"https://api.notion.com/v1/databases/{db_id}"
                requests.patch(patch_url, headers=notion_headers(), json={"archived": True})


# ===========================================================
# NOTION — CREATE NEW DB (Legacy Schema)
# ===========================================================
def create_database():
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
    r = requests.post(url, headers=notion_headers(), json=body)
    r.raise_for_status()
    print(f"✅ Created DB: {r.json()['id']}")
    return r.json()["id"]


# ===========================================================
# NOTION — INSERT ROW
# ===========================================================
def create_page(db_id, course, a):
    upd = parse_iso_date(a.get("updated_at"))
    due = parse_iso_date(a.get("due_at"))
    sub = parse_iso_date(a.get("submitted_at"))

    description_plain = html_to_plain(a.get("description"))
    points = a.get("points_possible")
    score = a.get("score")

    body = {
        "parent": {"database_id": db_id},
        "properties": {
            "Name": {"title": [{"text": {"content": a.get("name") or ""}}]},
            "Assignment Updated Date": {"date": {"start": upd.isoformat()} if upd else None},
            "Class": {"rich_text": [{"text": {"content": course["short_name"]}}]},
            "Description": {"rich_text": [{"text": {"content": description_plain}}]},
            "Due Date": {"date": {"start": due.isoformat()} if due else None},
            "ID": {"rich_text": [{"text": {"content": str(a.get("id"))}}]},
            "Link": {"url": a.get("html_url")},
            "Points": {"number": float(points) if points is not None else None},
            "Score": {"number": float(score) if score is not None else None},
            "Status": {"select": {"name": "Not Started"}},
            "Submitted Date": {"date": {"start": sub.isoformat()} if sub else None},
        },
    }

    url = "https://api.notion.com/v1/pages"
    r = requests.post(url, headers=notion_headers(), json=body)
    r.raise_for_status()


# ===========================================================
# MAIN
# ===========================================================
def main():
    print("🔧 Starting Canvas → Notion sync…")

    course_map = get_canvas_courses()
    if not course_map:
        print("❌ No valid Canvas courses found.")
        return

    all_assignments = []
    for cid in course_map:
        arr = get_canvas_assignments(cid)
        for a in arr:
            if should_include(a):
                all_assignments.append((cid, a))

    print(f"📘 Assignments after filtering: {len(all_assignments)}")

    archive_existing_db()
    db_id = create_database()

    for cid, a in all_assignments:
        create_page(db_id, course_map[cid], a)

    print("🎉 Sync complete.")


if __name__ == "__main__":
    main()
