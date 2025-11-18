import os
import json
import requests
from datetime import datetime, timezone

# ============================================================
# ENV VARS
# ============================================================
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")
CANVAS_COURSE_IDS = [c.strip() for c in os.environ.get("CANVAS_COURSE_IDS", "").split(",") if c.strip()]

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")
NOTION_DB_TITLE = "Canvas Course - Track Assignments"
NOTION_VERSION = "2022-06-28"

# Due-date filters
DUE_DATE_START = os.environ.get("DUE_DATE_PERIOD_START", "").strip()
DUE_DATE_END = os.environ.get("DUE_DATE_PERIOD_END", "").strip()
INCLUDE_ASSIGNMENTS_WITHOUT_DUE = os.environ.get("INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE", "false").lower() == "true"


def parse_canvas_dt(d):
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


FILTER_START = parse_filter_date(DUE_DATE_START)
FILTER_END = parse_filter_date(DUE_DATE_END)


# ============================================================
# SAFE SANITIZERS
# ============================================================
def safe_text(s):
    if not s:
        return ""
    return s


def clean_html(s):
    """Strip HTML tags exactly like n8n did."""
    import re
    if not s:
        return ""
    s = re.sub(r"<[^>]*>", "", s)
    return s[:500]


def safe_number(n):
    if n is None:
        return None
    try:
        return float(n)
    except:
        return None


def safe_date_prop(dt):
    if dt is None:
        return None
    return {"start": dt.isoformat()}


# ============================================================
# CANVAS API
# ============================================================
def canvas_headers():
    return {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}


def get_canvas_courses():
    url = f"{CANVAS_BASE_URL}/api/v1/courses?enrollment_type=student&enrollment_state=active&state[]=available&per_page=100"
    r = requests.get(url, headers=canvas_headers())
    r.raise_for_status()
    all_courses = r.json()
    if CANVAS_COURSE_IDS:
        return [c for c in all_courses if str(c["id"]) in CANVAS_COURSE_IDS]
    return all_courses


def get_assignments(course_id):
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments?per_page=100"
    r = requests.get(url, headers=canvas_headers())
    r.raise_for_status()
    return r.json()


def get_submission(course_id, assignment_id):
    """Needed to reproduce n8n logic: workflow_state, submitted_at, score."""
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self"
    r = requests.get(url, headers=canvas_headers())
    if r.status_code != 200:
        return {}
    return r.json()


# ============================================================
# DUE DATE FILTER LOGIC (Branch Feature)
# ============================================================
def should_include(a):
    due = parse_canvas_dt(a.get("due_at"))

    if due is None:
        return INCLUDE_ASSIGNMENTS_WITHOUT_DUE

    if FILTER_START and FILTER_END:
        return FILTER_START <= due <= FILTER_END

    if FILTER_START and not FILTER_END:
        return due >= FILTER_START

    if FILTER_END and not FILTER_START:
        return due <= FILTER_END

    return True


# ============================================================
# NOTION HELPERS
# ============================================================
def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json"
    }


# ============================================================
# FIND/ARCHIVE OLD DB
# ============================================================
def archive_existing_db():
    url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children?page_size=100"
    r = requests.get(url, headers=notion_headers())
    r.raise_for_status()
    children = r.json().get("results", [])

    for c in children:
        if c.get("type") == "child_database":
            title = c["child_database"].get("title", "")
            if title == NOTION_DB_TITLE:
                db_id = c["id"]
                print(f"🗑️ Archiving old DB: {db_id}")
                r2 = requests.patch(
                    f"https://api.notion.com/v1/databases/{db_id}",
                    headers=notion_headers(),
                    json={"archived": True}
                )
                r2.raise_for_status()


# ============================================================
# CREATE NEW DB (Legacy Schema A)
# ============================================================
def legacy_schema():
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
        "Submitted Date": {"date": {}}
    }


def create_new_db():
    payload = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "title": [{"type": "text", "text": {"content": NOTION_DB_TITLE}}],
        "properties": legacy_schema()
    }
    r = requests.post(
        "https://api.notion.com/v1/databases",
        headers=notion_headers(),
        json=payload
    )
    r.raise_for_status()
    return r.json()["id"]


# ============================================================
# CREATE PAGE (with n8n Transform Logic)
# ============================================================
def determine_status(a, sub):
    """Matches n8n transform logic precisely."""
    status = "Not Started"

    wf = sub.get("workflow_state")
    if wf in ["graded", "submitted", "pending_review"]:
        return "Completed"

    if a.get("has_submitted_submissions"):
        return "Completed"

    due = parse_canvas_dt(a.get("due_at"))
    now = datetime.now(timezone.utc)

    if due and due < now:
        return "Overdue"

    if due:
        return "In Progress"

    return "Not Started"


def create_page(db_id, course_name, a):
    assignment_id = a["id"]
    sub = get_submission(a["course_id"], assignment_id)

    updated = parse_canvas_dt(a.get("updated_at"))
    due = parse_canvas_dt(a.get("due_at"))
    submitted = parse_canvas_dt(sub.get("submitted_at"))

    # Fallback due date exactly like n8n
    date_src = a.get("due_at") or a.get("unlock_at") or a.get("lock_at")
    fallback_due = None
    if date_src:
        try:
            fallback_due = datetime.fromisoformat(date_src.replace("Z", "+00:00"))
        except:
            fallback_due = None

    final_due = due or fallback_due

    desc = clean_html(a.get("description"))

    status = determine_status(a, sub)

    payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "Name": {"title": [{"text": {"content": safe_text(a.get("name"))}}]},
            "Assignment Updated Date": safe_date_prop(updated),
            "Class": {"rich_text": [{"text": {"content": course_name}}]},
            "Description": {"rich_text": [{"text": {"content": desc}}]},
            "Due Date": safe_date_prop(final_due),
            "ID": {"rich_text": [{"text": {"content": str(assignment_id)}}]},
            "Link": {"url": a.get("html_url")},
            "Points": safe_number(a.get("points_possible")),
            "Score": safe_number(sub.get("score")),
            "Status": {"select": {"name": status}},
            "Submitted Date": safe_date_prop(submitted),
        }
    }

    r = requests.post("https://api.notion.com/v1/pages", headers=notion_headers(), json=payload)
    r.raise_for_status()


# ============================================================
# MAIN
# ============================================================
def main():
    print("🚀 Starting Canvas → Notion sync…")

    # 1. Canvas courses
    courses = get_canvas_courses()
    course_map = {str(c["id"]): c.get("name", "") for c in courses}

    # 2. Assignments
    all_assignments = []
    for cid in course_map:
        items = get_assignments(cid)
        for a in items:
            a["course_id"] = cid  # needed for submission lookup
            if should_include(a):
                all_assignments.append(a)

    print(f"📦 Assignments after filtering: {len(all_assignments)}")

    # 3. Archive old DB and create new one
    archive_existing_db()
    new_db = create_new_db()
    print(f"✅ Created DB: {new_db}")

    # 4. Create pages
    print("📝 Creating pages…")
    for a in all_assignments:
        cname = course_map[str(a["course_id"])]
        create_page(new_db, cname, a)

    print("🎉 Sync complete.")


if __name__ == "__main__":
    main()
