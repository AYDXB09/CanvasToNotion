import os
import requests
from datetime import datetime

# ------------------------------
# 🔧 CONFIGURATION
# ------------------------------

# Canvas API
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")  # Set in your Codespaces secrets
CANVAS_COURSE_IDS = os.environ.get("CANVAS_COURSE_IDS", "").split(",")  # e.g. "12345,67890"

# Notion API
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")  # From Notion integration
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")  # 32-char hex, no dashes

NOTION_VERSION = "2022-06-28"

# ------------------------------
# 📌 Helper functions
# ------------------------------

def get_canvas_assignments(course_id):
    """Fetch assignments from Canvas for a given course"""
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments"
    headers = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json()


def notion_query_database():
    """Fetch all pages from the Notion database"""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers)
    r.raise_for_status()
    return r.json()


def notion_find_page_by_canvas_id(canvas_id):
    """Search Notion DB for a page with a matching Canvas ID"""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    payload = {
        "filter": {
            "property": "Canvas ID",
            "rich_text": {"equals": str(canvas_id)}
        }
    }
    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


def notion_create_page(assignment):
    """Create a new page in Notion DB for an assignment"""
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    due_date = assignment.get("due_at")
    due_date_fmt = None
    if due_date:
        due_date_fmt = datetime.fromisoformat(due_date.replace("Z", "+00:00")).date().isoformat()

    data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Assignment Name": {"title": [{"text": {"content": assignment["name"]}}]},
            "Course": {"rich_text": [{"text": {"content": str(assignment.get("course_id"))}}]},
            "Due Date": {"date": {"start": due_date_fmt}} if due_date_fmt else {},
            "Status": {"select": {"name": "Pending"}},
            "Canvas URL": {"url": assignment.get("html_url")},
            "Canvas ID": {"rich_text": [{"text": {"content": str(assignment["id"])}}]},
        },
    }

    r = requests.post(url, headers=headers, json=data)
    r.raise_for_status()
    return r.json()


def notion_update_page(page_id, assignment):
    """Update existing Notion page with latest assignment info"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    due_date = assignment.get("due_at")
    due_date_fmt = None
    if due_date:
        due_date_fmt = datetime.fromisoformat(due_date.replace("Z", "+00:00")).date().isoformat()

    data = {
        "properties": {
            "Assignment Name": {"title": [{"text": {"content": assignment["name"]}}]},
            "Course": {"rich_text": [{"text": {"content": str(assignment.get("course_id"))}}]},
            "Due Date": {"date": {"start": due_date_fmt}} if due_date_fmt else {},
            "Canvas URL": {"url": assignment.get("html_url")},
        }
    }

    r = requests.patch(url, headers=headers, json=data)
    r.raise_for_status()
    return r.json()


# ------------------------------
# 🚀 Main Sync Logic
# ------------------------------

def main():
    if not CANVAS_API_TOKEN or not NOTION_TOKEN or not NOTION_DATABASE_ID:
        raise Exception("❌ Missing required environment variables.")

    for course_id in CANVAS_COURSE_IDS:
        course_id = course_id.strip()
        if not course_id:
            continue

        print(f"📥 Fetching assignments for course {course_id}...")
        assignments = get_canvas_assignments(course_id)

        for assignment in assignments:
            existing_page = notion_find_page_by_canvas_id(assignment["id"])
            if existing_page:
                page_id = existing_page["id"]
                print(f"🔄 Updating assignment in Notion: {assignment['name']}")
                notion_update_page(page_id, assignment)
            else:
                print(f"➕ Creating new assignment in Notion: {assignment['name']}")
                notion_create_page(assignment)


if __name__ == "__main__":
    main()
