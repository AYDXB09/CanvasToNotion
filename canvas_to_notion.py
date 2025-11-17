import os
import json
import requests
from datetime import datetime

# ------------------------------
# ENVIRONMENT VARIABLES
# ------------------------------
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")

# Course filtering (optional)
CANVAS_COURSE_IDS = [
    c.strip() for c in os.environ.get("CANVAS_COURSE_IDS", "").split(",") if c.strip()
]

# NOTION configuration
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")  # "Canvas Assignments" page
NOTION_DATABASE_NAME = os.environ.get("NOTION_DATABASE_NAME", "Canvas Assignments")

NOTION_VERSION = "2022-06-28"

# ------------------------------
# DUE DATE FILTERS
# ------------------------------
START_RAW = os.environ.get("DUE_DATE_PERIOD_START", "NONE").strip().upper()
END_RAW = os.environ.get("DUE_DATE_PERIOD_END", "NONE").strip().upper()

INCLUDE_NO_DUE_DATE = os.environ.get(
    "INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE", "false"
).lower() == "true"

DUE_DATE_START = None if START_RAW in ("", "NONE") else datetime.fromisoformat(START_RAW)
DUE_DATE_END = None if END_RAW in ("", "NONE") else datetime.fromisoformat(END_RAW)

# ------------------------------
# NOTION HELPERS
# ------------------------------
def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def create_database():
    """
    Recreate the Notion DB every run using the FIXED schema (Option A).
    """
    url = "https://api.notion.com/v1/databases"

    payload = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "title": [{"type": "text", "text": {"content": NOTION_DATABASE_NAME}}],
        "properties": {
            "Assignment Name": {"title": {}},
            "Course ID": {"number": {}},
            "Due Date": {"date": {}},
            "Canvas URL": {"url": {}},
            "Canvas ID": {"rich_text": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "Pending", "color": "yellow"},
                        {"name": "Done", "color": "green"},
                    ]
                }
            },
        },
    }

    r = requests.post(url, headers=notion_headers(), json=payload)
    r.raise_for_status()
    db = r.json()
    print("Created new DB:", db["id"])
    return db["id"]


def add_page_to_db(db_id, assignment):
    """
    Insert a row into the database.
    """

    due = assignment.get("due_at")
    due_fmt = None
    if due:
        due_fmt = datetime.fromisoformat(due.replace("Z", "+00:00")).date().isoformat()

    payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "Assignment Name": {
                "title": [{"text": {"content": assignment["name"]}}]
            },
            "Course ID": {"number": assignment.get("course_id")},
            "Due Date": {"date": {"start": due_fmt}} if due_fmt else None,
            "Canvas URL": {"url": assignment.get("html_url")},
            "Canvas ID": {
                "rich_text": [{"text": {"content": str(assignment["id"])}}]
            },
            "Status": {"select": {"name": "Pending"}},
        },
    }

    # Remove empty keys (Notion rejects them)
    payload["properties"] = {
        k: v for k, v in payload["properties"].items() if v is not None
    }

    r = requests.post("https://api.notion.com/v1/pages", headers=notion_headers(), json=payload)
    r.raise_for_status()


# ------------------------------
# CANVAS HELPERS
# ------------------------------
def get_assignments(course_id):
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments"
    r = requests.get(url, headers={"Authorization": f"Bearer {CANVAS_API_TOKEN}"})
    r.raise_for_status()
    return r.json()


# ------------------------------
# FILTER LOGIC
# ------------------------------
def should_include_assignment(assignment):
    due_at = assignment.get("due_at")

    # Case: no due date
    if due_at is None:
        return INCLUDE_NO_DUE_DATE

    # Convert due date
    due_dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))

    # Case 1: both start & end
    if DUE_DATE_START and DUE_DATE_END:
        return DUE_DATE_START <= due_dt <= DUE_DATE_END

    # Case 2: only end
    if DUE_DATE_END:
        return due_dt <= DUE_DATE_END

    # Case 3: only start
    if DUE_DATE_START:
        return due_dt >= DUE_DATE_START

    # No filters → include everything
    return True


# ------------------------------
# MAIN
# ------------------------------
def main():
    if not NOTION_API_KEY or not NOTION_PARENT_PAGE_ID:
        raise Exception("Missing NOTION_API_KEY or NOTION_PARENT_PAGE_ID")

    # 1️⃣ Create database fresh every run
    db_id = create_database()

    # 2️⃣ Determine which courses to use
    if CANVAS_COURSE_IDS:
        course_ids = CANVAS_COURSE_IDS
        print("Filtering Canvas courses:", course_ids)
    else:
        raise Exception("No course filtering logic for auto-discovery implemented yet.")

    # 3️⃣ Process each course
    for cid in course_ids:
        assignments = get_assignments(cid)
        for assn in assignments:
            if should_include_assignment(assn):
                print("✓ Adding:", assn["name"])
                add_page_to_db(db_id, assn)
            else:
                print("✗ Skipped:", assn["name"])


if __name__ == "__main__":
    main()
