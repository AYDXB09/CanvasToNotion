import os
import json
import requests
from datetime import datetime

# ------------------------------
# CONFIGURATION
# ------------------------------
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")
CANVAS_COURSE_IDS_RAW = os.environ.get("CANVAS_COURSE_IDS", "")

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

NOTION_VERSION = "2022-06-28"


# ------------------------------
# HELPERS
# ------------------------------
def get_notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def get_canvas_headers():
    return {
        "Authorization": f"Bearer {CANVAS_API_TOKEN}",
    }


# ------------------------------
# CANVAS FUNCTIONS
# ------------------------------
def get_canvas_assignments(course_id: str):
    """
    Fetch all assignments for a given Canvas course ID.
    """
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments"
    headers = get_canvas_headers()
    params = {
        "per_page": 100,
    }

    assignments = []
    while url:
        r = requests.get(url, headers=headers, params=params)
        r.raise_for_status()
        assignments.extend(r.json())

        # Handle pagination via "Link" header
        if "next" in r.links:
            url = r.links["next"]["url"]
            params = None  # after first request, params must be None
        else:
            url = None

    return assignments


def get_active_canvas_courses():
    """
    Fetch all ACTIVE Canvas courses for the current user.

    We consider a course "active" if:
      - workflow_state is 'available' or 'active'
      - enrollment_state is 'active' (via API filter)
    """
    url = f"{CANVAS_BASE_URL}/api/v1/courses"
    headers = get_canvas_headers()
    params = {
        "per_page": 100,
        "enrollment_state": "active",
    }

    courses = []
    while url:
        r = requests.get(url, headers=headers, params=params)
        r.raise_for_status()
        batch = r.json()
        courses.extend(batch)

        if "next" in r.links:
            url = r.links["next"]["url"]
            params = None
        else:
            url = None

    active_courses = []
    for c in courses:
        state = c.get("workflow_state")
        if state in ("available", "active"):
            active_courses.append(c)

    print(f"📚 Found {len(active_courses)} active Canvas courses.")
    for c in active_courses:
        print(f"   - {c.get('id')} :: {c.get('name')}")
    return active_courses


def resolve_course_ids_to_sync():
    """
    Logic:
      - If CANVAS_COURSE_IDS has 1+ IDs listed (comma separated), use ONLY those.
      - Otherwise auto-detect all ACTIVE Canvas courses and use them.
    """
    raw_ids = [
        cid.strip()
        for cid in CANVAS_COURSE_IDS_RAW.split(",")
        if cid.strip()
    ]

    if raw_ids:
        print("🎯 Using CANVAS_COURSE_IDS filter from environment:")
        for cid in raw_ids:
            print(f"   - {cid}")
        return raw_ids

    print("ℹ No CANVAS_COURSE_IDS filter set – discovering active courses automatically…")
    active_courses = get_active_canvas_courses()
    course_ids = [str(c["id"]) for c in active_courses]
    if not course_ids:
        raise RuntimeError("No active Canvas courses found for this user.")
    return course_ids


# ------------------------------
# NOTION FUNCTIONS
# ------------------------------
def notion_find_page_by_canvas_id(canvas_id: str):
    """
    Look up an existing Notion page in the database where "Canvas ID" equals canvas_id.
    """
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    headers = get_notion_headers()

    payload = {
        "filter": {
            "property": "Canvas ID",
            "rich_text": {"equals": str(canvas_id)},
        }
    }

    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


def notion_create_or_update(assignment: dict):
    """
    Create or update a Notion page for a single Canvas assignment.
    """
    token_headers = get_notion_headers()
    existing_page = notion_find_page_by_canvas_id(assignment["id"])

    # Format due date (if any) into ISO date only (YYYY-MM-DD)
    due_date = assignment.get("due_at")
    due_date_fmt = None
    if due_date:
        # Canvas returns ISO 8601 string; convert to date (UTC) then .date().isoformat()
        dt = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
        due_date_fmt = dt.date().isoformat()

    props = {
        "Assignment Name": {
            "title": [
                {"text": {"content": assignment["name"]}}
            ]
        },
        "Course": {
            "rich_text": [
                {"text": {"content": str(assignment.get("course_id"))}}
            ]
        },
        "Status": {
            "select": {"name": "Pending"}
        },
        "Canvas URL": {
            "url": assignment.get("html_url"),
        },
        "Canvas ID": {
            "rich_text": [
                {"text": {"content": str(assignment["id"])}}
            ]
        },
    }

    if due_date_fmt:
        props["Due Date"] = {
            "date": {"start": due_date_fmt}
        }

    data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": props,
    }

    if existing_page:
        page_id = existing_page["id"]
        url = f"https://api.notion.com/v1/pages/{page_id}"
        print(f"   ↻ Updating existing Notion page for Canvas ID {assignment['id']}")
        r = requests.patch(url, headers=token_headers, json=data)
    else:
        url = "https://api.notion.com/v1/pages"
        print(f"   ➕ Creating new Notion page for Canvas ID {assignment['id']}")
        r = requests.post(url, headers=token_headers, json=data)

    r.raise_for_status()
    return r.json()


# ------------------------------
# MAIN
# ------------------------------
def main():
    # Basic sanity check – but we do NOT print any secret values
    if not CANVAS_API_TOKEN:
        raise Exception("Missing CANVAS_API_TOKEN environment variable.")
    if not NOTION_API_KEY:
        raise Exception("Missing NOTION_API_KEY environment variable.")
    if not NOTION_DATABASE_ID:
        raise Exception("Missing NOTION_DATABASE_ID environment variable.")

    # Decide which courses to sync
    course_ids = resolve_course_ids_to_sync()

    for course_id in course_ids:
        print(f"\n📥 Fetching assignments for Canvas course {course_id} ...")
        assignments = get_canvas_assignments(course_id)
        print(f"   Found {len(assignments)} assignments.")

        for assignment in assignments:
            name = assignment.get("name", "<unnamed>")
            print(f"   • Syncing assignment: {name}")
            notion_create_or_update(assignment)

    print("\n✅ Canvas → Notion sync complete.")


if __name__ == "__main__":
    main()
