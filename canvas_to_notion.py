import os
import re
import html
import json
from datetime import datetime, timezone
import requests

# =========================================
# ENVIRONMENT / CONFIG
# =========================================

CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")

# Comma-separated Canvas course IDs, e.g. "7220,7229"
CANVAS_COURSE_IDS_RAW = os.environ.get("CANVAS_COURSE_IDS", "").strip()
CANVAS_COURSE_IDS = {
    c.strip()
    for c in CANVAS_COURSE_IDS_RAW.split(",")
    if c.strip()
}

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")

NOTION_VERSION = "2022-06-28"
NOTION_DB_TITLE = os.environ.get(
    "NOTION_DB_TITLE", "Canvas Course - Track Assignments"
)

# Due-date filter env vars (branch feature)
DUE_DATE_PERIOD_START = os.environ.get("DUE_DATE_PERIOD_START", "").strip()
DUE_DATE_PERIOD_END = os.environ.get("DUE_DATE_PERIOD_END", "").strip()
INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE = (
    os.environ.get("INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE", "false").lower() == "true"
)


# =========================================
# HELPERS
# =========================================

def notion_headers():
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
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    print("✅ Environment OK")
    if CANVAS_COURSE_IDS:
        print(f"📘 Course filter: {sorted(CANVAS_COURSE_IDS)}")
    else:
        print("📘 No course ID filter set – will use all active Canvas courses.")


def parse_canvas_datetime(value: str):
    """Parse Canvas ISO8601 datetime string into timezone-aware datetime (UTC)."""
    if not value:
        return None
    try:
        # Canvas usually returns "2025-11-17T12:34:56Z"
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def parse_filter_date(value: str):
    """Parse YYYY-MM-DD into timezone-aware datetime at midnight UTC."""
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


DUE_DATE_START_DT = parse_filter_date(DUE_DATE_PERIOD_START)
DUE_DATE_END_DT = parse_filter_date(DUE_DATE_PERIOD_END)


def clean_description(html_text: str) -> str:
    """Rudimentary HTML → plain text (better than raw tags)."""
    if not html_text:
        return ""
    # Remove tags
    text = re.sub(r"<[^>]+>", "", html_text)
    # Unescape entities (&amp;, &nbsp;, etc.)
    text = html.unescape(text)
    return text.strip()


# =========================================
# CANVAS LOGIC
# =========================================

def get_canvas_courses():
    """
    Fetch active student courses from Canvas, then apply optional ID filter.
    Returns: dict[course_id_str] = {"short_name": ..., "full_name": ...}
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

    if CANVAS_COURSE_IDS:
        filtered = [c for c in courses if str(c.get("id")) in CANVAS_COURSE_IDS]
        print(
            f"📊 Canvas returned {len(courses)} active courses; "
            f"after ID filter → {len(filtered)}."
        )
        courses = filtered
    else:
        print(f"📊 Canvas returned {len(courses)} active courses (no ID filter).")

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
    headers = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments?per_page=100"

    print(f"  🔎 Fetching assignments for course {course_id}…")
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_all_assignments(course_map):
    """
    Returns: list[(course_id_str, assignment_dict)]
    """
    all_items = []
    for cid in course_map.keys():
        assignments = get_canvas_assignments_for_course(cid)
        print(f"  📄 {len(assignments)} assignments before filtering for course {cid}.")
        for a in assignments:
            all_items.append((cid, a))
    print(f"📚 Total assignments before filtering: {len(all_items)}")
    return all_items


# =========================================
# DUE-DATE FILTERING
# =========================================

def assignment_passes_due_filter(assignment: dict) -> bool:
    due_dt = parse_canvas_datetime(assignment.get("due_at"))

    # No due date
    if due_dt is None:
        return INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE

    # Both start and end → [start, end]
    if DUE_DATE_START_DT and DUE_DATE_END_DT:
        return DUE_DATE_START_DT <= due_dt <= DUE_DATE_END_DT

    # Only end → <= end
    if DUE_DATE_END_DT and not DUE_DATE_START_DT:
        return due_dt <= DUE_DATE_END_DT

    # Only start → >= start
    if DUE_DATE_START_DT and not DUE_DATE_END_DT:
        return due_dt >= DUE_DATE_START_DT

    # No filter → always include
    return True


def filter_assignments(assignments):
    """
    assignments: list[(course_id_str, assignment_dict)]
    returns same shape, filtered by due date window.
    """
    filtered = []
    for cid, a in assignments:
        if assignment_passes_due_filter(a):
            filtered.append((cid, a))
    print(f"🧮 Assignments after filtering: {len(filtered)}")
    return filtered


# =========================================
# NOTION – DATABASE HANDLING (LEGACY SCHEMA)
# =========================================

def archive_existing_databases():
    """
    Archive any child_database under NOTION_PARENT_PAGE_ID
    whose title == NOTION_DB_TITLE.
    """
    headers = notion_headers()
    url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children"

    print("🗃️  Looking for existing Notion databases to archive…")
    archived_count = 0
    has_more = True
    start_cursor = None

    while has_more:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor

        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

        for child in data.get("results", []):
            if child.get("type") != "child_database":
                continue
            db_id = child.get("id")
            db_title = child["child_database"].get("title", "")
            if db_title == NOTION_DB_TITLE:
                print(f"  🧹 Archiving DB: {db_title} ({db_id})")
                patch_url = f"https://api.notion.com/v1/databases/{db_id}"
                patch_body = {"archived": True}
                r2 = requests.patch(patch_url, headers=headers, json=patch_body)
                r2.raise_for_status()
                archived_count += 1

        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    if archived_count == 0:
        print("ℹ️  No existing database with that name – fresh create.")
    else:
        print(f"✅ Archived {archived_count} database(s) named '{NOTION_DB_TITLE}'.")


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


def create_new_database():
    headers = notion_headers()
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
    resp = requests.post(url, headers=headers, json=body)
    resp.raise_for_status()
    db = resp.json()
    db_id = db["id"]
    print(f"✅ Created DB: {NOTION_DB_TITLE} ({db_id})")
    return db_id


# =========================================
# NOTION – PAGE CREATION (LEGACY SCHEMA)
# =========================================

def build_status_for_assignment(assignment, due_dt, submitted_dt):
    """
    Simple status logic:
      - if submitted → Completed
      - else if due date passed → Overdue
      - else → In Progress
    """
    has_submitted = assignment.get("has_submitted_submissions", False)

    if has_submitted:
        return "Completed"

    now = datetime.now(timezone.utc)
    if due_dt and due_dt < now:
        return "Overdue"

    return "In Progress"


def create_page(db_id: str, course_map: dict, item):
    cid, assignment = item
    headers = notion_headers()

    course_info = course_map.get(cid, {})
    course_name = course_info.get("short_name") or ""
    full_course_name = course_info.get("full_name") or ""

    name = assignment.get("name") or "Untitled Assignment"
    description_raw = assignment.get("description") or ""
    description = clean_description(description_raw)

    due_dt = parse_canvas_datetime(assignment.get("due_at"))
    updated_dt = parse_canvas_datetime(assignment.get("updated_at"))
    submitted_dt = parse_canvas_datetime(assignment.get("submitted_at"))

    canvas_id = str(assignment.get("id"))
    link = assignment.get("html_url")
    points = assignment.get("points_possible")
    score = assignment.get("score")  # may be None

    status_name = build_status_for_assignment(assignment, due_dt, submitted_dt)

    props = {
        "Name": {
            "title": [
                {"type": "text", "text": {"content": name}}
            ]
        },
        "Class": {
            "rich_text": [
                {"type": "text", "text": {"content": course_name}}
            ]
        },
        # store full course name in description if you want? Keeping legacy: just Class.
        "ID": {
            "rich_text": [
                {"type": "text", "text": {"content": canvas_id}}
            ]
        },
        "Link": {"url": link},
        "Status": {
            "select": {"name": status_name}
        },
    }

    # Only include optional properties when we actually have a value.
    if updated_dt:
        props["Assignment Updated Date"] = {
            "date": {"start": updated_dt.isoformat()}
        }

    if description:
        props["Description"] = {
            "rich_text": [
                {"type": "text", "text": {"content": description}}
            ]
        }

    if due_dt:
        props["Due Date"] = {"date": {"start": due_dt.isoformat()}}

    if points is not None:
        props["Points"] = {"number": float(points)}

    if score is not None:
        props["Score"] = {"number": float(score)}

    if submitted_dt:
        props["Submitted Date"] = {"date": {"start": submitted_dt.isoformat()}}

    body = {
        "parent": {"database_id": db_id},
        "properties": props,
    }

    url = "https://api.notion.com/v1/pages"
    resp = requests.post(url, headers=headers, json=body)
    resp.raise_for_status()


def create_pages_for_all_assignments(db_id, course_map, assignments):
    print("📝 Creating Notion pages…")
    count = 0
    for item in assignments:
        create_page(db_id, course_map, item)
        count += 1
    print(f"✅ Created {count} pages in Notion.")


# =========================================
# MAIN
# =========================================

def main():
    print("🚀 Starting Canvas → Notion sync…")
    ensure_env()

    # 1) Canvas courses
    course_map = get_canvas_courses()
    if not course_map:
        print("⚠️ No Canvas courses after filtering – nothing to do.")
        return

    # 2) Canvas assignments
    assignments = get_all_assignments(course_map)
    if not assignments:
        print("⚠️ No assignments found – nothing to do.")
        return

    # 3) Due-date filtering
    assignments = filter_assignments(assignments)
    if not assignments:
        print("⚠️ No assignments after due-date filtering – nothing to create.")
        return

    # 4) Notion – archive old DB and create new one
    archive_existing_databases()
    new_db_id = create_new_database()

    # 5) Notion – create pages
    create_pages_for_all_assignments(new_db_id, course_map, assignments)

    print("🎉 Sync complete.")


if __name__ == "__main__":
    main()
