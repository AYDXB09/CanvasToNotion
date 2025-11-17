import os
import json
import requests
from datetime import datetime, timezone

# ==============================
# CONFIG
# ==============================
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")

# Comma–separated list of course IDs: "7229,7243"
CANVAS_COURSE_IDS_RAW = os.environ.get("CANVAS_COURSE_IDS", "").strip()
CANVAS_COURSE_IDS = {
    c.strip()
    for c in CANVAS_COURSE_IDS_RAW.split(",")
    if c.strip()
}

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")  # same parent page as n8n
NOTION_DATABASE_NAME = os.environ.get("NOTION_DATABASE_NAME", "Canvas Course - Track Assignments")

NOTION_VERSION = "2022-06-28"
NOTION_DB_TITLE = NOTION_DATABASE_NAME

# Due-date filters (repository variables)
DUE_DATE_PERIOD_START = os.environ.get("DUE_DATE_PERIOD_START", "").strip()  # "YYYY-MM-DD" or ""
DUE_DATE_PERIOD_END = os.environ.get("DUE_DATE_PERIOD_END", "").strip()      # "YYYY-MM-DD" or ""
INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE = (
    os.environ.get("INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE", "false").lower() == "true"
)


# ==============================
# HELPER FUNCTIONS
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

    print("✅ Environment variables loaded.")
    if CANVAS_COURSE_IDS:
        print(f"✅ Using course filter (CANVAS_COURSE_IDS): {sorted(CANVAS_COURSE_IDS)}")
    else:
        print("✅ No course filter set. Will include ALL active Canvas courses.")

    print(f"✅ INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE = {INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE}")
    if DUE_DATE_PERIOD_START:
        print(f"✅ DUE_DATE_PERIOD_START = {DUE_DATE_PERIOD_START}")
    if DUE_DATE_PERIOD_END:
        print(f"✅ DUE_DATE_PERIOD_END   = {DUE_DATE_PERIOD_END}")


def parse_canvas_datetime(value: str):
    """Parse Canvas ISO datetime (with 'Z') into timezone-aware UTC datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def parse_filter_date(value: str):
    """Parse YYYY-MM-DD into a timezone-aware UTC datetime at midnight."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


# Pre-compute filter datetimes
DUE_DATE_START_DT = parse_filter_date(DUE_DATE_PERIOD_START)
DUE_DATE_END_DT = parse_filter_date(DUE_DATE_PERIOD_END)


def assignment_passes_due_date_filter(assignment: dict) -> bool:
    """
    Apply the rules you requested:

    1) If both start & end set → include if start <= due <= end
    2) If only end set → include if due <= end
    3) If only start set → include if due >= start
    4) If neither set → don't filter by date at all
    5) If no due_at:
         - include only if INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE == True
    """
    due = parse_canvas_datetime(assignment.get("due_at"))

    # No due_at
    if due is None:
        return INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE

    # Now due is timezone-aware
    if DUE_DATE_START_DT and DUE_DATE_END_DT:
        return DUE_DATE_START_DT <= due <= DUE_DATE_END_DT

    if DUE_DATE_END_DT and not DUE_DATE_START_DT:
        return due <= DUE_DATE_END_DT

    if DUE_DATE_START_DT and not DUE_DATE_END_DT:
        return due >= DUE_DATE_START_DT

    # No filters set → include everything
    return True


# ==============================
# CANVAS LOGIC
# ==============================
def get_canvas_courses():
    """
    n8n-style:

    /api/v1/courses?enrollment_type=student&enrollment_state=active&state[]=available

    Then filter locally by CANVAS_COURSE_IDS (if provided).
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

    # Filter by CANVAS_COURSE_IDS if specified
    if CANVAS_COURSE_IDS:
        filtered = [c for c in courses if str(c.get("id")) in CANVAS_COURSE_IDS]
        print(
            f"📘 Canvas returned {len(courses)} active courses; "
            f"filtering down to {len(filtered)} by CANVAS_COURSE_IDS."
        )
        courses = filtered
    else:
        print(f"📘 Canvas returned {len(courses)} active courses (no ID filter).")

    # Map course_id -> {short_name, full_name}
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

    print(f"   🔎 Fetching assignments for course {course_id}…")
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_canvas_submission(course_id: str, assignment_id: int):
    """
    Get submission for the *current user*, like n8n did,
    so we can fill Score + Submitted Date.
    """
    headers = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}
    url = (
        f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}"
        f"/assignments/{assignment_id}/submissions/self"
    )
    resp = requests.get(url, headers=headers)

    if resp.status_code == 404:
        # No submission yet
        return None

    resp.raise_for_status()
    return resp.json()


def get_all_filtered_assignments(course_map):
    """
    Return list of (course_id, assignment_dict) AFTER applying due-date filter.
    """
    all_items = []
    for cid in course_map.keys():
        assignments = get_canvas_assignments_for_course(cid)
        print(f"   📄 {len(assignments)} assignments in course {cid} (before filtering).")

        for a in assignments:
            if assignment_passes_due_date_filter(a):
                all_items.append((cid, a))

    print(f"📚 Total assignments AFTER due-date filter: {len(all_items)}")
    return all_items


# ==============================
# NOTION – DATABASE (LEGACY SCHEMA)
# ==============================
def archive_existing_database():
    """
    Find any child_database under NOTION_PARENT_PAGE_ID
    with title == NOTION_DB_TITLE and archive them.
    (n8n: Archive-if-Exists)
    """
    headers = get_notion_headers()
    url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children?page_size=100"

    print("🗃️  Looking for existing Notion databases to archive…")
    has_more = True
    next_cursor = None
    archived_count = 0

    while has_more:
        params = {}
        if next_cursor:
            params["start_cursor"] = next_cursor

        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

        for child in data.get("results", []):
            if child.get("type") == "child_database":
                db_id = child.get("id")
                db_title = child["child_database"].get("title", "")
                if db_title == NOTION_DB_TITLE:
                    print(f"   🧹 Archiving old database: {db_title} ({db_id})")
                    patch_url = f"https://api.notion.com/v1/databases/{db_id}"
                    patch_body = {"archived": True}
                    r2 = requests.patch(patch_url, headers=headers, data=json.dumps(patch_body))
                    r2.raise_for_status()
                    archived_count += 1

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    if archived_count == 0:
        print("ℹ️  No existing database with that name found. Fresh create.")
    else:
        print(f"✅ Archived {archived_count} old database(s) named '{NOTION_DB_TITLE}'.")


def build_db_properties_schema():
    """
    ✅ LEGACY SCHEMA (your original):
      - Name (Title)
      - Assignment Updated Date (Date)
      - Class (Rich text)
      - Description (Rich text)
      - Due Date (Date)
      - ID (Rich text)
      - Link (URL)
      - Points (Number)
      - Score (Number)
      - Status (Select)
      - Submitted Date (Date)
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
    """
    Create a new database under NOTION_PARENT_PAGE_ID with the legacy schema
    and title NOTION_DB_TITLE.
    """
    headers = get_notion_headers()
    url = "https://api.notion.com/v1/databases"

    body = {
        "parent": {
            "type": "page_id",
            "page_id": NOTION_PARENT_PAGE_ID,
        },
        "title": [
            {
                "type": "text",
                "text": {"content": NOTION_DB_TITLE},
            }
        ],
        "properties": build_db_properties_schema(),
    }

    print("🆕 Creating new Notion database (legacy schema)…")
    resp = requests.post(url, headers=headers, data=json.dumps(body))
    resp.raise_for_status()
    db = resp.json()
    db_id = db["id"]
    print(f"✅ Created database '{NOTION_DB_TITLE}' with id {db_id}")
    return db_id


# ==============================
# NOTION – PAGE CREATION
# ==============================
def create_assignment_page(db_id, course_info, assignment, submission):
    """
    Create one page in the legacy schema,
    including improved score + submission data (Option B).
    """
    headers = get_notion_headers()

    assignment_name = assignment.get("name") or "Untitled Assignment"
    canvas_url = assignment.get("html_url")
    canvas_id = str(assignment.get("id"))

    # Legacy fields
    updated_at = parse_canvas_datetime(assignment.get("updated_at"))
    due_at = parse_canvas_datetime(assignment.get("due_at"))

    # Points possible
    points_possible = assignment.get("points_possible")

    # Submission info (Option B)
    score = None
    submitted_at = None
    has_submitted = False

    if submission:
        score = submission.get("score")
        submitted_at = parse_canvas_datetime(submission.get("submitted_at"))
        # Canvas submissions have workflow_state: "submitted", "graded", etc.
        # If there is a submission record at all, treat as submitted.
        has_submitted = True

    # Status logic (simple, like n8n style)
    now = datetime.now(timezone.utc)
    if has_submitted:
        status_name = "Completed"
    elif due_at and due_at < now:
        status_name = "Overdue"
    else:
        status_name = "Not Started"

    # Build Notion properties (omit properties instead of sending nulls)
    props = {
        "Name": {
            "title": [
                {"text": {"content": assignment_name}}
            ]
        },
        "Class": {
            "rich_text": [
                {"text": {"content": course_info["short_name"]}}
            ]
        },
        "ID": {
            "rich_text": [
                {"text": {"content": canvas_id}}
            ]
        },
        "Link": {
            "url": canvas_url,
        },
        "Status": {
            "select": {"name": status_name}
        },
    }

    # Optional: Assignment Updated Date
    if updated_at:
        props["Assignment Updated Date"] = {
            "date": {"start": updated_at.isoformat()}
        }

    # Optional: Description
    desc = assignment.get("description")
    if desc:
        props["Description"] = {
            "rich_text": [
                {"text": {"content": desc[:1900]}}  # avoid super-long property
            ]
        }

    # Optional: Due Date
    if due_at:
        props["Due Date"] = {
            "date": {"start": due_at.date().isoformat()}
        }

    # Optional: Points
    if points_possible is not None:
        try:
            props["Points"] = {"number": float(points_possible)}
        except Exception:
            pass

    # Optional: Score
    if score is not None:
        try:
            props["Score"] = {"number": float(score)}
        except Exception:
            pass

    # Optional: Submitted Date
    if submitted_at:
        props["Submitted Date"] = {
            "date": {"start": submitted_at.isoformat()}
        }

    body = {
        "parent": {"database_id": db_id},
        "properties": props,
    }

    url = "https://api.notion.com/v1/pages"
    resp = requests.post(url, headers=headers, data=json.dumps(body))
    resp.raise_for_status()


def sync_assignments_to_notion(db_id, course_map, assignments):
    print("📝 Creating pages in Notion…")
    count = 0

    for cid, assignment in assignments:
        course_info = course_map.get(cid)
        if not course_info:
            continue

        submission = get_canvas_submission(cid, assignment.get("id"))
        create_assignment_page(db_id, course_info, assignment, submission)
        count += 1

    print(f"✅ Created {count} assignment pages in Notion.")


# ==============================
# MAIN
# ==============================
def main():
    ensure_env()

    # 1) Canvas: get courses (active) + filter by CANVAS_COURSE_IDS if provided
    course_map = get_canvas_courses()
    if not course_map:
        print("⚠️ No courses found (after filtering). Nothing to sync.")
        return

    # 2) Canvas: get assignments, THEN apply due-date filter
    assignments = get_all_filtered_assignments(course_map)
    if not assignments:
        print("⚠️ No assignments passed the due-date filter. Nothing to sync.")
        return

    # 3) Notion: archive old DB (same name) and create new DB under same parent page
    archive_existing_database()
    new_db_id = create_new_database()

    # 4) Notion: create pages for all filtered assignments
    sync_assignments_to_notion(new_db_id, course_map, assignments)

    print("🎉 Canvas → Notion sync completed.")


if __name__ == "__main__":
    main()
