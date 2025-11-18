import os
import re
import json
from datetime import datetime, timezone
import requests

# ==============================
# CONFIG
# ==============================

CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com").rstrip("/")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")

# Comma–separated list of course IDs, e.g. "7220,7229"
CANVAS_COURSE_IDS_RAW = os.environ.get("CANVAS_COURSE_IDS", "").strip()
CANVAS_COURSE_IDS = {
    c.strip()
    for c in CANVAS_COURSE_IDS_RAW.split(",")
    if c.strip()
}

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")

NOTION_VERSION = "2022-06-28"
NOTION_DB_TITLE = os.environ.get("NOTION_DB_TITLE", "Canvas Course - Track Assignments")

# Due-date filter env vars (optional)
DUE_DATE_PERIOD_START = os.environ.get("DUE_DATE_PERIOD_START", "").strip()
DUE_DATE_PERIOD_END = os.environ.get("DUE_DATE_PERIOD_END", "").strip()
INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE = os.environ.get(
    "INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE", "false"
).lower() == "true"


# ==============================
# HELPERS
# ==============================

def get_notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def ensure_env():
    missing = []
    if not CANVAS_API_TOKEN:
        missing.append("CANVAS_API_TOKEN")
    if not NOTION_API_KEY:
        missing.append("NOTION_API_KEY")
    if not NOTION_PARENT_PAGE_ID:
        missing.append("NOTION_PARENT_PAGE_ID")

    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    print("✅ Environment OK")
    if CANVAS_COURSE_IDS:
        print(f"📘 Course filter: {sorted(CANVAS_COURSE_IDS)}")
    else:
        print("📘 No course ID filter – will include ALL active Canvas courses.")


def parse_canvas_datetime(value: str):
    """Parse Canvas ISO datetime (with trailing Z) to aware datetime in UTC."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def parse_filter_date(value: str):
    """Parse YYYY-MM-DD from env to date."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


FILTER_START_DATE = parse_filter_date(DUE_DATE_PERIOD_START)
FILTER_END_DATE = parse_filter_date(DUE_DATE_PERIOD_END)


def should_include_by_due(assignment: dict) -> bool:
    """
    Apply the date-window rules:

    1) If both start & end given → keep if start <= due <= end
    2) If only end given → keep if due <= end
    3) If only start given → keep if due >= start
    4) If no filters → keep everything
    5) If assignment has no due date → respect INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE
    """
    due_dt = parse_canvas_datetime(assignment.get("due_at"))

    if due_dt is None:
        return INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE

    due_d = due_dt.date()

    if FILTER_START_DATE and FILTER_END_DATE:
        return FILTER_START_DATE <= due_d <= FILTER_END_DATE
    if FILTER_END_DATE and not FILTER_START_DATE:
        return due_d <= FILTER_END_DATE
    if FILTER_START_DATE and not FILTER_END_DATE:
        return due_d >= FILTER_START_DATE
    return True


HTML_TAG_RE = re.compile(r"<[^>]*>")


def clean_description(html: str) -> str:
    if not html:
        return ""
    # Strip HTML tags and collapse whitespace, limit length to 500 chars
    text = HTML_TAG_RE.sub("", html)
    text = " ".join(text.split())
    return text[:500]


def legacy_schema_properties():
    """
    Legacy Schema A:

    - Name (title)
    - Assignment Updated Date (date)
    - Class (text)
    - Description (text)
    - Due Date (date)
    - ID (text)
    - Link (url)
    - Points (number)
    - Score (number)
    - Status (select: Overdue / In Progress / Completed / Not Started)
    - Submitted Date (date)
    """
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


def rtext(content: str):
    """Helper to build rich_text / title arrays."""
    if not content:
        content = ""
    return [{"type": "text", "text": {"content": content}}]


def date_prop_from_dt(dt: datetime, date_only: bool = False):
    if not dt:
        return None
    if date_only:
        value = dt.date().isoformat()
    else:
        value = dt.isoformat()
    return {"start": value}


# ==============================
# CANVAS LOGIC
# ==============================

def get_canvas_courses():
    """
    Fetch active Canvas courses for the student and build a course map:
      course_id -> { 'short_name': ..., 'full_name': ... }
    """
    headers = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}
    url = (
        f"{CANVAS_BASE_URL}/api/v1/courses"
        "?enrollment_type=student"
        "&enrollment_state=active"
        "&state[]=available"
        "&per_page=100"
    )
    print("📡 Fetching Canvas courses…")
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    courses = resp.json()

    # Filter by CANVAS_COURSE_IDS if provided
    if CANVAS_COURSE_IDS:
        filtered = [c for c in courses if str(c.get("id")) in CANVAS_COURSE_IDS]
        print(
            f"📘 Canvas returned {len(courses)} active courses; "
            f"after ID filter → {len(filtered)}."
        )
        courses = filtered
    else:
        print(f"📘 Canvas returned {len(courses)} active courses (no ID filter).")

    course_map = {}
    for c in courses:
        cid = str(c.get("id"))
        short_name = c.get("course_code") or c.get("name") or f"Course {cid}"
        full_name = c.get("name") or short_name
        course_map[cid] = {
            "short_name": short_name,
            "full_name": full_name,
        }
    return course_map


def get_canvas_assignments_for_course(course_id: str):
    """
    Fetch assignments for a single course, including submission info.
    """
    headers = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}
    url = (
        f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments"
        "?include[]=submission&per_page=100"
    )
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def collect_filtered_assignments(course_map):
    """
    Return list of (course_id, assignment_dict) AFTER due-date filtering.
    """
    all_items = []
    total_before = 0
    for cid in course_map.keys():
        print(f"🔍 Fetching assignments for course {cid}…")
        assignments = get_canvas_assignments_for_course(cid)
        total_before += len(assignments)
        print(f"   → {len(assignments)} assignments before filtering.")
        for a in assignments:
            if should_include_by_due(a):
                all_items.append((cid, a))
    print(
        f"📚 Total assignments before filtering: {total_before}; "
        f"after filtering: {len(all_items)}"
    )
    return all_items


# ==============================
# NOTION – DB ARCHIVE + CREATE
# ==============================

def archive_existing_legacy_db():
    """
    Find any child_database under NOTION_PARENT_PAGE_ID whose title matches NOTION_DB_TITLE
    and archive them.
    """
    headers = get_notion_headers()
    url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children"

    print("🗃️  Looking for existing Notion databases to archive…")
    has_more = True
    next_cursor = None
    archived = 0

    while has_more:
        params = {"page_size": 100}
        if next_cursor:
            params["start_cursor"] = next_cursor

        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

        for child in data.get("results", []):
            if child.get("type") != "child_database":
                continue
            db_id = child.get("id")
            title = child.get("child_database", {}).get("title", "")
            if title == NOTION_DB_TITLE:
                print(f"   🧹 Archiving DB: {title} ({db_id})")
                patch_url = f"https://api.notion.com/v1/databases/{db_id}"
                patch_body = {"archived": True}
                r2 = requests.patch(patch_url, headers=headers, data=json.dumps(patch_body))
                r2.raise_for_status()
                archived += 1

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    if archived:
        print(f"✅ Archived {archived} database(s) named '{NOTION_DB_TITLE}'.")
    else:
        print("ℹ️  No existing legacy DB found to archive.")


def create_legacy_database():
    """
    Create a new database under NOTION_PARENT_PAGE_ID using legacy schema.
    """
    headers = get_notion_headers()
    url = "https://api.notion.com/v1/databases"

    body = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "title": [
            {
                "type": "text",
                "text": {"content": NOTION_DB_TITLE},
            }
        ],
        "properties": legacy_schema_properties(),
    }

    print("🆕 Creating new Notion database…")
    resp = requests.post(url, headers=headers, data=json.dumps(body))
    resp.raise_for_status()
    db = resp.json()
    db_id = db["id"]
    print(f"✅ Created DB: {NOTION_DB_TITLE} ({db_id})")
    return db_id


# ==============================
# TRANSFORM + PAGE CREATION
# ==============================

def determine_status(assignment: dict) -> str:
    """
    Port of your n8n Transform node logic, using embedded submission if present.
    """
    sub = assignment.get("submission") or {}
    workflow_state = sub.get("workflow_state")
    status = "Not Started"

    if workflow_state in ("graded", "submitted", "pending_review"):
        status = "Completed"
    elif assignment.get("has_submitted_submissions"):
        status = "Completed"
    else:
        now = datetime.now(timezone.utc)
        date_source = assignment.get("due_at") or assignment.get("unlock_at") or assignment.get("lock_at")
        due_dt = parse_canvas_datetime(date_source)
        if due_dt:
            if due_dt < now:
                status = "Overdue"
            else:
                status = "In Progress"

    if workflow_state == "unsubmitted":
        status = "Not Started"

    return status


def build_page_properties(course_info: dict, assignment: dict):
    """
    Build Notion page properties for the legacy schema, avoiding invalid nulls.
    """
    name = assignment.get("name") or "Untitled Assignment"
    course_name = course_info.get("short_name") or course_info.get("full_name") or ""
    description_html = assignment.get("description") or ""
    clean_desc = clean_description(description_html)

    updated_dt = parse_canvas_datetime(assignment.get("updated_at"))
    due_dt = parse_canvas_datetime(
        assignment.get("due_at") or assignment.get("unlock_at") or assignment.get("lock_at")
    )

    sub = assignment.get("submission") or {}
    submitted_at = parse_canvas_datetime(sub.get("submitted_at"))
    score = sub.get("score")

    status = determine_status(assignment)
    link = assignment.get("html_url")
    canvas_id = assignment.get("id")
    points = assignment.get("points_possible")

    props = {
        "Name": {"title": rtext(name)},
        "Class": {"rich_text": rtext(course_name)},
        "Description": {"rich_text": rtext(clean_desc)},
        "ID": {"rich_text": rtext(str(canvas_id))},
    }

    if updated_dt:
        props["Assignment Updated Date"] = {"date": date_prop_from_dt(updated_dt)}
    if due_dt:
        props["Due Date"] = {"date": date_prop_from_dt(due_dt)}
    if submitted_at:
        props["Submitted Date"] = {"date": date_prop_from_dt(submitted_at)}
    if link:
        props["Link"] = {"url": link}
    if points is not None:
        try:
            props["Points"] = {"number": float(points)}
        except Exception:
            pass
    if score is not None:
        try:
            props["Score"] = {"number": float(score)}
        except Exception:
            pass
    if status:
        props["Status"] = {"select": {"name": status}}

    return props


def create_page(db_id: str, course_info: dict, assignment: dict):
    headers = get_notion_headers()
    properties = build_page_properties(course_info, assignment)
    body = {
        "parent": {"database_id": db_id},
        "properties": properties,
    }
    url = "https://api.notion.com/v1/pages"
    resp = requests.post(url, headers=headers, data=json.dumps(body))
    resp.raise_for_status()


def create_pages_for_all_assignments(db_id: str, course_map: dict, items):
    print("📝 Creating pages…")
    count = 0
    for cid, assignment in items:
        course_info = course_map.get(cid)
        if not course_info:
            continue
        create_page(db_id, course_info, assignment)
        count += 1
    print(f"✅ Created {count} pages.")


# ==============================
# MAIN
# ==============================

def main():
    print("🚀 Starting Canvas → Notion sync…")
    ensure_env()

    # 1) Canvas: courses
    course_map = get_canvas_courses()
    if not course_map:
        print("⚠️ No active courses after filtering. Nothing to do.")
        return
    print(f"📦 Active filtered courses: {list(course_map.keys())}")

    # 2) Canvas: assignments (already date-filtered)
    assignments = collect_filtered_assignments(course_map)
    if not assignments:
        print("⚠️ No assignments after filtering. Nothing to create.")
        return

    # 3) Notion: archive old DB and create new one
    archive_existing_legacy_db()
    new_db_id = create_legacy_database()

    # 4) Notion: create pages
    create_pages_for_all_assignments(new_db_id, course_map, assignments)

    print("🎉 Sync complete.")


if __name__ == "__main__":
    main()
