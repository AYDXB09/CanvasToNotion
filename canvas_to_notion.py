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

NOTION_VERSION = "2022-06-28"
NOTION_DB_TITLE = "Canvas Course - Track Assignments"


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

    print("✅ Environment variables loaded.")
    if CANVAS_COURSE_IDS:
        print(f"✅ Using course filter: {sorted(CANVAS_COURSE_IDS)}")
    else:
        print("✅ No course filter set. Will include ALL active Canvas courses.")


# ==============================
# CANVAS LOGIC
# ==============================
def get_canvas_courses():
    """
    If CANVAS_COURSE_IDS is non-empty:
        - Fetch only those courses by ID (to be safe, we still call /courses?include[]=term etc
          and filter locally by ID).
    Else:
        - Fetch ALL active courses for the student (n8n-style logic).
    """
    headers = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}

    # Base URL matches your n8n flow:
    #   /api/v1/courses?enrollment_type=student&enrollment_state=active&state[]=available
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
        print(f"📘 Canvas returned {len(courses)} active courses; "
              f"filtering down to {len(filtered)} by CANVAS_COURSE_IDS.")
        courses = filtered
    else:
        print(f"📘 Canvas returned {len(courses)} active courses (no ID filter).")

    # Build a mapping of course_id -> (short_name, full_name)
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


def get_canvas_assignments_for_course(course_id):
    headers = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments?per_page=100"

    print(f"   🔎 Fetching assignments for course {course_id}…")
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_all_assignments(course_map):
    """
    Return list of (course_id, assignment_dict).
    """
    all_items = []
    for cid in course_map.keys():
        assignments = get_canvas_assignments_for_course(cid)
        print(f"   📄 {len(assignments)} assignments in course {cid}.")
        for a in assignments:
            all_items.append((cid, a))
    print(f"📚 Total assignments collected: {len(all_items)}")
    return all_items


# ==============================
# NOTION – DATABASE CREATION
# ==============================
def archive_existing_database():
    """
    Find any child_database under NOTION_PARENT_PAGE_ID with title == NOTION_DB_TITLE
    and archive them (n8n: Archive-if-Exists).
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
    Fixed, simple schema (Option A). This matches the core columns you had:
      - Assignment Name (Title)
      - Course (short ID/code)
      - Course Name (full)
      - Due Date
      - Status (Pending / Completed)
      - Canvas URL
      - Canvas ID
      - Max Points
      - Submitted
      - Synced On
    """
    return {
        "Assignment Name": {"title": {}},
        "Course": {"rich_text": {}},
        "Course Name": {"rich_text": {}},
        "Due Date": {"date": {}},
        "Status": {
            "select": {
                "options": [
                    {"name": "Pending", "color": "yellow"},
                    {"name": "Completed", "color": "green"},
                ]
            }
        },
        "Canvas URL": {"url": {}},
        "Canvas ID": {"rich_text": {}},
        "Max Points": {"number": {"format": "number"}},
        "Submitted": {"checkbox": {}},
        "Synced On": {"date": {}},
    }


def create_new_database():
    """
    Create a new database under NOTION_PARENT_PAGE_ID with fixed schema
    and title 'Canvas Course - Track Assignments'.
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

    print("🆕 Creating new Notion database…")
    resp = requests.post(url, headers=headers, data=json.dumps(body))
    resp.raise_for_status()
    db = resp.json()
    db_id = db["id"]
    print(f"✅ Created database '{NOTION_DB_TITLE}' with id {db_id}")
    return db_id


# ==============================
# NOTION – PAGE CREATION
# ==============================
def create_assignment_page(db_id, course_info, assignment):
    headers = get_notion_headers()

    assignment_name = assignment.get("name") or "Untitled Assignment"
    canvas_url = assignment.get("html_url")
    canvas_id = str(assignment.get("id"))
    due_at = assignment.get("due_at")
    points = assignment.get("points_possible")
    has_submitted = assignment.get("has_submitted_submissions", False)

    # Convert due_at ISO -> date
    due_date_prop = None
    if due_at:
        try:
            dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
            due_date_prop = dt.date().isoformat()
        except Exception:
            pass

    now = datetime.now(timezone.utc).date().isoformat()

    properties = {
        "Assignment Name": {
            "title": [
                {"text": {"content": assignment_name}}
            ]
        },
        "Course": {
            "rich_text": [
                {"text": {"content": course_info["short_name"]}}
            ]
        },
        "Course Name": {
            "rich_text": [
                {"text": {"content": course_info["full_name"]}}
            ]
        },
        "Status": {
            "select": {"name": "Pending"}
        },
        "Canvas URL": {
            "url": canvas_url,
        },
        "Canvas ID": {
            "rich_text": [
                {"text": {"content": canvas_id}}
            ]
        },
        "Submitted": {
            "checkbox": bool(has_submitted),
        },
        "Synced On": {
            "date": {"start": now},
        },
    }

    if points is not None:
        properties["Max Points"] = {
            "number": float(points),
        }

    if due_date_prop:
        properties["Due Date"] = {
            "date": {"start": due_date_prop}
        }

    body = {
        "parent": {"database_id": db_id},
        "properties": properties,
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
            # Should not happen, but be safe
            continue
        create_assignment_page(db_id, course_info, assignment)
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

    # 2) Canvas: get all assignments for those courses
    assignments = get_all_assignments(course_map)
    if not assignments:
        print("⚠️ No assignments found. Nothing to sync.")
        return

    # 3) Notion: archive old DB (same name) and create new DB under same parent page
    archive_existing_database()
    new_db_id = create_new_database()

    # 4) Notion: create pages for all assignments
    sync_assignments_to_notion(new_db_id, course_map, assignments)

    print("🎉 Canvas → Notion sync completed.")


if __name__ == "__main__":
    main()
