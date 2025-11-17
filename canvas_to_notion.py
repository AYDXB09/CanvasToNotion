import os
import re
import html
import json
import requests
from datetime import datetime, timezone, date

# ==============================
# ENVIRONMENT / CONFIG
# ==============================

CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")

# Comma-separated list: "7285,7201,7210,7239"
CANVAS_COURSE_IDS_RAW = os.environ.get("CANVAS_COURSE_IDS", "").strip()
CANVAS_COURSE_IDS = {
    c.strip()
    for c in CANVAS_COURSE_IDS_RAW.split(",")
    if c.strip()
}

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")
NOTION_DATABASE_NAME = os.environ.get(
    "NOTION_DATABASE_NAME",
    "Canvas Course - Track Assignments",
)

NOTION_VERSION = "2022-06-28"

# Due-date filters (branch feature)
DUE_DATE_PERIOD_START = os.environ.get("DUE_DATE_PERIOD_START", "").strip()
DUE_DATE_PERIOD_END = os.environ.get("DUE_DATE_PERIOD_END", "").strip()
INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE = (
    os.environ.get("INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE", "false").lower() == "true"
)


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
        print(f"✅ Course filter: {sorted(CANVAS_COURSE_IDS)}")
    else:
        print("✅ No course filter (will use ALL active student courses).")


def parse_canvas_datetime(value: str):
    """Canvas timestamps → timezone-aware datetime (UTC)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def parse_filter_date(value: str):
    """YYYY-MM-DD → timezone-aware datetime at midnight UTC."""
    if not value:
        return None
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except Exception:
        return None


DUE_START_DT = parse_filter_date(DUE_DATE_PERIOD_START)
DUE_END_DT = parse_filter_date(DUE_DATE_PERIOD_END)


TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(html_str: str) -> str:
    """Very simple HTML → plain text converter for Canvas descriptions."""
    if not html_str:
        return ""
    # Remove tags
    text = TAG_RE.sub("", html_str)
    # Unescape entities (&amp; → &)
    text = html.unescape(text)
    # Normalise whitespace a bit
    return " ".join(text.split())


# ==============================
# DUE-DATE FILTER LOGIC
# ==============================

def should_include_assignment(assignment) -> bool:
    """
    Apply the due-date window logic:

    1) If no due date:
         - include only if INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE = true
    2) If both start & end set:
         - include when start <= due <= end
    3) Only end set:
         - include when due <= end
    4) Only start set:
         - include when due >= start
    5) No filters:
         - include all
    """
    due_at = assignment.get("due_at")
    due_dt = parse_canvas_datetime(due_at)

    # No due date at all
    if due_dt is None:
        return INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE

    # Ensure due_dt is UTC aware
    if due_dt.tzinfo is None:
        due_dt = due_dt.replace(tzinfo=timezone.utc)

    # Both start & end
    if DUE_START_DT and DUE_END_DT:
        return DUE_START_DT <= due_dt <= DUE_END_DT

    # Only END
    if DUE_END_DT and not DUE_START_DT:
        return due_dt <= DUE_END_DT

    # Only START
    if DUE_START_DT and not DUE_END_DT:
        return due_dt >= DUE_START_DT

    # No filters → keep everything
    return True


# ==============================
# CANVAS LOGIC
# ==============================

def get_canvas_courses():
    """
    Fetch active student courses from Canvas, then filter by
    CANVAS_COURSE_IDS (if provided).

    Returns: dict course_id (str) -> dict(short_name, full_name)
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
            f"   Canvas returned {len(courses)} active courses; "
            f"after ID filter → {len(filtered)}."
        )
        courses = filtered
    else:
        print(f"   Canvas returned {len(courses)} active courses (no ID filter).")

    course_map = {}
    for c in courses:
        cid = str(c.get("id"))
        short_name = c.get("course_code") or c.get("name") or f"Course {cid}"
        full_name = c.get("name") or short_name
        course_map[cid] = {"short_name": short_name, "full_name": full_name}

    return course_map


def get_canvas_assignments_for_course(course_id: str):
    headers = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments?per_page=100"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_filtered_assignments(course_map):
    """
    Collect assignments from all selected courses and apply due-date filters.
    Returns list of (course_id, assignment).
    """
    all_items = []
    for cid in course_map.keys():
        print(f"🔎 Fetching assignments for course {cid}…")
        assignments = get_canvas_assignments_for_course(cid)
        print(f"   → {len(assignments)} assignments before filtering.")

        for a in assignments:
            if should_include_assignment(a):
                all_items.append((cid, a))

    print(f"📚 Assignments after filtering: {len(all_items)}")
    return all_items


# ==============================
# NOTION – DATABASE MANAGEMENT
# ==============================

def archive_existing_databases():
    """
    Archive any child_database under the parent page that has
    the same title as NOTION_DATABASE_NAME.
    """
    headers = get_notion_headers()
    url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children"
    params = {"page_size": 100}

    print("🗃  Looking for existing Notion databases to archive…")
    archived = 0
    while True:
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

        for child in data.get("results", []):
            if child.get("type") == "child_database":
                db_id = child.get("id")
                db_title = child["child_database"].get("title", "")
                if db_title == NOTION_DATABASE_NAME:
                    print(f"   🧹 Archiving DB: {db_title} ({db_id})")
                    patch_url = f"https://api.notion.com/v1/databases/{db_id}"
                    patch_body = {"archived": True}
                    r2 = requests.patch(patch_url, headers=headers, json=patch_body)
                    r2.raise_for_status()
                    archived += 1

        if not data.get("has_more"):
            break
        params["start_cursor"] = data.get("next_cursor")

    if archived == 0:
        print("   ℹ️  No existing DB with that name.")
    else:
        print(f"   ✅ Archived {archived} database(s).")


def build_legacy_schema():
    """
    Legacy schema exactly as your Notion DB:

    Name (title)
    Assignment Updated Date (date)
    Class (text)
    Description (text)
    Due Date (date)
    ID (number)
    Link (url)
    Points (number)
    Score (number)
    Status (select: Overdue/In Progress/Completed/Not Started)
    Submitted Date (date)
    """
    return {
        "Name": {"title": {}},
        "Assignment Updated Date": {"date": {}},
        "Class": {"rich_text": {}},
        "Description": {"rich_text": {}},
        "Due Date": {"date": {}},
        "ID": {"number": {}},
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
    """
    Create a new database under the parent page with the legacy schema.
    """
    headers = get_notion_headers()
    url = "https://api.notion.com/v1/databases"

    body = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "title": [
            {
                "type": "text",
                "text": {"content": NOTION_DATABASE_NAME},
            }
        ],
        "properties": build_legacy_schema(),
    }

    print("🆕 Creating new Notion database…")
    resp = requests.post(url, headers=headers, json=body)
    resp.raise_for_status()
    db = resp.json()
    db_id = db["id"]
    print(f"   ✅ Created DB: {NOTION_DATABASE_NAME} ({db_id})")
    return db_id


# ==============================
# NOTION – PAGE CREATION
# ==============================

def build_properties_for_assignment(course_info, assignment):
    """
    Build the Notion properties payload for one assignment,
    matching the legacy schema and avoiding invalid values.
    """
    assignment_name = assignment.get("name") or "Untitled Assignment"
    canvas_url = assignment.get("html_url")
    canvas_id = assignment.get("id")
    points = assignment.get("points_possible")
    # Canvas list API generally doesn't give a score; keep Score empty for now
    score = None

    # Dates
    due_dt = parse_canvas_datetime(assignment.get("due_at"))
    updated_dt = parse_canvas_datetime(assignment.get("updated_at"))
    has_submitted = assignment.get("has_submitted_submissions", False)

    properties = {
        "Name": {
            "title": [
                {"type": "text", "text": {"content": assignment_name}}
            ]
        },
        "Class": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": course_info["short_name"]},
                }
            ]
        },
        "Description": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {
                        "content": html_to_text(assignment.get("description") or "")
                    },
                }
            ]
        },
        "ID": {
            "number": float(canvas_id) if canvas_id is not None else None,
        },
        "Status": {
            "select": {"name": "Not Started"},
        },
    }

    # Dates – include only when we actually have values
    if updated_dt:
        properties["Assignment Updated Date"] = {
            "date": {"start": updated_dt.isoformat()}
        }

    if due_dt:
        properties["Due Date"] = {
            "date": {"start": due_dt.isoformat()}
        }

    if has_submitted:
        # We don't have real submitted_at from this endpoint;
        # use "today" as a simple marker.
        today_iso = date.today().isoformat()
        properties["Submitted Date"] = {
            "date": {"start": today_iso}
        }

    # Optional numeric / url fields
    if points is not None:
        properties["Points"] = {"number": float(points)}

    if score is not None:
        properties["Score"] = {"number": float(score)}

    if canvas_url:
        properties["Link"] = {"url": canvas_url}

    return properties


def create_page(db_id, course_map, assignment_tuple):
    cid, assignment = assignment_tuple
    course_info = course_map.get(cid)
    if not course_info:
        return  # safety

    properties = build_properties_for_assignment(course_info, assignment)

    body = {
        "parent": {"database_id": db_id},
        "properties": properties,
    }

    url = "https://api.notion.com/v1/pages"
    resp = requests.post(url, headers=get_notion_headers(), json=body)
    resp.raise_for_status()


def create_pages_for_all_assignments(db_id, course_map, assignments):
    print("📝 Creating Notion pages…")
    count = 0
    for item in assignments:
        create_page(db_id, course_map, item)
        count += 1
    print(f"   ✅ Created {count} pages in Notion.")


# ==============================
# MAIN
# ==============================

def main():
    print("🚀 Starting Canvas → Notion sync…")
    ensure_env()

    # 1) Canvas: get courses
    course_map = get_canvas_courses()
    if not course_map:
        print("⚠️ No Canvas courses found. Exiting.")
        return

    # 2) Canvas: get assignments and apply due-date filters
    assignments = get_filtered_assignments(course_map)
    if not assignments:
        print("⚠️ No assignments after due-date filtering. Exiting.")
        return

    # 3) Notion: archive old DB(s) and create new one
    archive_existing_databases()
    new_db_id = create_new_database()

    # 4) Notion: create pages
    create_pages_for_all_assignments(new_db_id, course_map, assignments)

    print("🎉 Sync complete.")


if __name__ == "__main__":
    main()
