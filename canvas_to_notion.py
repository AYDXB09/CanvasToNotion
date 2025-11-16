import os
import re
import json
import requests
from datetime import datetime, timezone

# ------------------------------
# CONFIGURATION (all via secrets)
# ------------------------------
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")
# Comma-separated list like "4872,1234"
CANVAS_COURSE_IDS = os.environ.get("CANVAS_COURSE_IDS", "").split(",")

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
NOTION_VERSION = "2022-06-28"


# ------------------------------
# HELPERS
# ------------------------------
def require_env():
    missing = [
        name for name, value in [
            ("CANVAS_API_TOKEN", CANVAS_API_TOKEN),
            ("NOTION_API_KEY", NOTION_API_KEY),
            ("NOTION_DATABASE_ID", NOTION_DATABASE_ID),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


def get_notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def get_canvas_headers():
    return {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}


# ------------------------------
# CANVAS FUNCTIONS
# ------------------------------
def get_canvas_course_name(course_id: str) -> str:
    """Fetch course name for nicer 'Class' column."""
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}"
    r = requests.get(url, headers=get_canvas_headers())
    r.raise_for_status()
    data = r.json()
    return data.get("name") or f"Course {course_id}"


def get_canvas_assignments(course_id: str):
    """
    Match the n8n request:
    /courses/{id}/assignments?per_page=100&order_by=due_at&include[]=submission
    """
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments"
    params = {
        "per_page": 100,
        "order_by": "due_at",
        "include[]": "submission",
    }
    r = requests.get(url, headers=get_canvas_headers(), params=params)
    r.raise_for_status()
    return r.json()


# ------------------------------
# TRANSFORM: match n8n logic
# ------------------------------
def compute_status(assignment: dict) -> str:
    sub = assignment.get("submission") or {}
    status = "Not Started"

    # Completed?
    if sub.get("workflow_state") in {"graded", "submitted", "pending_review"}:
        status = "Completed"
    elif assignment.get("has_submitted_submissions"):
        status = "Completed"
    else:
        due_at = assignment.get("due_at")
        if due_at:
            try:
                due_dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if due_dt < now:
                    status = "Overdue"
                else:
                    status = "In Progress"
            except Exception:
                # If parsing fails, leave default / fallback
                status = "In Progress"

    # Explicit override from n8n logic
    if sub.get("workflow_state") == "unsubmitted":
        status = "Not Started"

    return status


def pick_date_source(assignment: dict) -> str | None:
    """
    n8n: const dateSource = a.due_at || a.unlock_at || a.lock_at;
    We return YYYY-MM-DD or None.
    """
    date_src = assignment.get("due_at") or assignment.get("unlock_at") or assignment.get("lock_at")
    if not date_src:
        return None
    try:
        dt = datetime.fromisoformat(date_src.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except Exception:
        return None


def clean_description(html: str | None) -> str:
    if not html:
        return ""
    # Strip HTML tags, limit length (like n8n’s substring(0, 500))
    text = re.sub(r"<[^>]*>", "", html)
    return text.strip()[:500]


# ------------------------------
# NOTION FUNCTIONS (API key)
# ------------------------------
def notion_find_page_by_canvas_id(canvas_id: int) -> dict | None:
    """
    Query the database for an existing page with ID == canvas_id.
    Assumes Notion DB has a 'ID' number property (same as your n8n DB).
    """
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "ID",
            "number": {"equals": canvas_id},
        }
    }
    r = requests.post(url, headers=get_notion_headers(), json=payload)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


def notion_build_properties(assignment: dict, course_name: str) -> dict:
    sub = assignment.get("submission") or {}
    status = compute_status(assignment)
    due_date = pick_date_source(assignment)
    description = clean_description(assignment.get("description"))
    points = assignment.get("points_possible")
    score = sub.get("score")
    updated_at = assignment.get("updated_at")
    submitted_at = sub.get("submitted_at")

    props: dict = {
        "Name": {
            "title": [{"text": {"content": assignment.get("name") or "Untitled Assignment"}}]
        },
        "Class": {
            "rich_text": [{"text": {"content": course_name}}]
        },
        "Status": {
            "select": {"name": status}
        },
        "Link": {
            "url": assignment.get("html_url")
        },
        "ID": {
            "number": assignment.get("id")
        },
    }

    if due_date:
        props["Due Date"] = {"date": {"start": due_date}}

    if description:
        props["Description"] = {
            "rich_text": [{"text": {"content": description}}]
        }

    if points is not None:
        props["Points"] = {"number": points}

    if score is not None:
        props["Score"] = {"number": score}

    if updated_at:
        props["Assignment Updated Date"] = {"date": {"start": updated_at}}

    if submitted_at:
        props["Submitted Date"] = {"date": {"start": submitted_at}}

    return props


def notion_create_page(assignment: dict, course_name: str) -> None:
    url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": notion_build_properties(assignment, course_name),
    }
    r = requests.post(url, headers=get_notion_headers(), json=payload)
    r.raise_for_status()


def notion_update_page(page_id: str, assignment: dict, course_name: str) -> None:
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {
        "properties": notion_build_properties(assignment, course_name),
    }
    r = requests.patch(url, headers=get_notion_headers(), json=payload)
    r.raise_for_status()


# ------------------------------
# MAIN
# ------------------------------
def main():
    require_env()

    for raw_id in CANVAS_COURSE_IDS:
        course_id = raw_id.strip()
        if not course_id:
            continue

        print(f"📘 Processing Canvas course {course_id}...")
        course_name = get_canvas_course_name(course_id)
        assignments = get_canvas_assignments(course_id)

        for a in assignments:
            canvas_id = a.get("id")
            if canvas_id is None:
                continue

            print(f"  → Syncing assignment {canvas_id}: {a.get('name')!r}")
            existing = notion_find_page_by_canvas_id(canvas_id)

            if existing:
                notion_update_page(existing["id"], a, course_name)
            else:
                notion_create_page(a, course_name)

    print("✅ Canvas → Notion sync complete.")


if __name__ == "__main__":
    main()
