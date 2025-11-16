import os
import re
import json
import requests
from datetime import datetime, timezone

# ============================================================
# CONFIGURATION
# ============================================================

# Canvas
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com").rstrip("/")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")
# Comma-separated list of course IDs. If empty → all active student courses.
CANVAS_COURSE_IDS = [
    c.strip()
    for c in os.environ.get("CANVAS_COURSE_IDS", "").split(",")
    if c.strip()
]

# Notion
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")

# These two come from your n8n flow:
# parent page: 2942b5d3a8c4800d8737f4fa86c16050
# DB title: "Canvas Course - Track Assignments"
NOTION_PARENT_PAGE_ID = os.environ.get(
    "NOTION_PARENT_PAGE_ID",
    "2942b5d3a8c4800d8737f4fa86c16050",
)
NOTION_DB_TITLE = os.environ.get(
    "NOTION_DB_TITLE",
    "Canvas Course - Track Assignments",
)

NOTION_VERSION = "2022-06-28"


# ============================================================
# HELPERS
# ============================================================

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


# ============================================================
# NOTION – DB DISCOVERY / CLONE / ARCHIVE
# ============================================================

def get_latest_db_id():
    """
    Mimics n8n's "Get DB in Page" node:

    GET /blocks/{parent_page_id}/children
    Then find child_database with title == NOTION_DB_TITLE
    """
    url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children"
    headers = get_notion_headers()
    params = {"page_size": 100}

    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()

    results = data.get("results", [])
    for block in results:
        if block.get("type") == "child_database":
            child = block.get("child_database", {})
            if child.get("title") == NOTION_DB_TITLE:
                return block["id"]

    raise RuntimeError(
        f"❌ No child database with title '{NOTION_DB_TITLE}' found under parent page {NOTION_PARENT_PAGE_ID}"
    )


def get_db_schema(db_id):
    """
    Corresponds to n8n's "Read Old DB Schema" node:
    GET /databases/{db_id}
    """
    url = f"https://api.notion.com/v1/databases/{db_id}"
    headers = get_notion_headers()
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def clean_schema_properties(props: dict) -> dict:
    """
    Same logic as your n8n "Prepare New DB JSON" code:

    Recursively remove keys named 'description' if they are "" or null.
    Some old schemas with empty descriptions can cause errors if sent back.
    """

    def clean(obj):
        if isinstance(obj, list):
            return [clean(x) for x in obj]
        if isinstance(obj, dict):
            new_obj = {}
            for k, v in obj.items():
                if k == "description" and (v == "" or v is None):
                    # omit invalid description
                    continue
                new_obj[k] = clean(v)
            return new_obj
        return obj

    return clean(props)


def create_new_database_from_schema(old_schema: dict) -> str:
    """
    Builds the payload like n8n's "Prepare New DB JSON" + "Create New Database":

    parent: page_id = NOTION_PARENT_PAGE_ID
    title: "Canvas Course - Track Assignments"
    properties: cloned & cleaned from old_schema['properties']
    """
    properties = old_schema.get("properties", {})
    clean_props = clean_schema_properties(properties)

    payload = {
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
        "properties": clean_props,
    }

    url = "https://api.notion.com/v1/databases"
    headers = get_notion_headers()
    resp = requests.post(url, headers=headers, data=json.dumps(payload))
    resp.raise_for_status()

    data = resp.json()
    new_db_id = data["id"]
    print(f"✅ Created new Notion database: {new_db_id}")
    return new_db_id


def archive_database(db_id: str):
    """
    PATCH /databases/{db_id} with {"archived": true}

    Equivalent to the "Archive Old Database" HTTP node in n8n.
    """
    url = f"https://api.notion.com/v1/databases/{db_id}"
    headers = get_notion_headers()
    payload = {"archived": True}
    resp = requests.patch(url, headers=headers, data=json.dumps(payload))
    resp.raise_for_status()
    print(f"📦 Archived old Notion database: {db_id}")


# ============================================================
# CANVAS – COURSES & ASSIGNMENTS
# ============================================================

def get_canvas_courses():
    """
    Mirrors n8n's "Get Canvas Courses" node:

    GET /courses?enrollment_type=student&enrollment_state=active&state=available&per_page=100

    This automatically restricts to CURRENT active courses.
    """
    url = (
        f"{CANVAS_BASE_URL}/api/v1/courses"
        "?enrollment_type=student"
        "&enrollment_state=active"
        "&state=available"
        "&per_page=100"
    )
    headers = get_canvas_headers()
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_canvas_assignments(course_id: int):
    """
    Mirrors n8n's "Get Canvas Assignments" node:

    GET /courses/{id}/assignments?per_page=100&order_by=due_at&include[]=submission
    """
    url = (
        f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments"
        "?per_page=100&order_by=due_at&include[]=submission"
    )
    headers = get_canvas_headers()
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


# ============================================================
# TRANSFORM ASSIGNMENTS → NOTION FIELDS (n8n CODE NODE)
# ============================================================

def build_course_map(courses):
    """
    Build {course_id_str: course_name} map from Canvas courses.
    """
    mapping = {}
    for c in courses:
        cid = c.get("id")
        if cid is None:
            continue
        mapping[str(cid)] = c.get("name") or "Unnamed Course"
    return mapping


def clean_description(html: str, max_len: int = 500) -> str:
    """
    Strip HTML tags and limit to max_len characters.
    Same idea as your n8n 'Transform Data' node.
    """
    if not html:
        return ""
    text = re.sub(r"<[^>]*>", "", html)
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len]
    return text


def transform_assignment(assignment: dict, course_map: dict) -> dict:
    """
    Apply the same status & field logic as the n8n "Transform Data" node.

    Returns a dict with keys:
    - notion_db_id (we'll set later)
    - name, class, due_date, status, link, canvas_id, description,
      points_possible, updated_at, score_obtained, submitted_date
    """
    course_id = str(assignment.get("course_id", ""))
    course_name = course_map.get(course_id, "Unknown Course")

    status = "Not Started"
    sub = assignment.get("submission") or {}

    wf = sub.get("workflow_state")
    if wf in ["graded", "submitted", "pending_review"]:
        status = "Completed"
    elif assignment.get("has_submitted_submissions"):
        status = "Completed"
    elif assignment.get("due_at"):
        try:
            due_dt = datetime.fromisoformat(
                assignment["due_at"].replace("Z", "+00:00")
            )
        except Exception:
            due_dt = None
        now = datetime.now(timezone.utc)
        if due_dt and due_dt < now:
            status = "Overdue"
        else:
            status = "In Progress"

    if wf == "unsubmitted":
        status = "Not Started"

    # Due date: due_at OR unlock_at OR lock_at → YYYY-MM-DD
    due_source = (
        assignment.get("due_at")
        or assignment.get("unlock_at")
        or assignment.get("lock_at")
    )
    due_date_str = None
    if due_source:
        try:
            d = datetime.fromisoformat(due_source.replace("Z", "+00:00"))
            due_date_str = d.date().isoformat()
        except Exception:
            due_date_str = None

    desc = clean_description(assignment.get("description", ""))

    return {
        "name": assignment.get("name") or "Untitled Assignment",
        "class": course_name,
        "due_date": due_date_str or "",
        "status": status,
        "link": assignment.get("html_url") or "",
        "canvas_id": assignment.get("id"),
        "description": desc,
        "points_possible": assignment.get("points_possible") or 0,
        "updated_at": assignment.get("updated_at") or "",
        "score_obtained": sub.get("score"),
        "submitted_date": sub.get("submitted_at") or "",
    }


# ============================================================
# NOTION – CREATE PAGES IN NEW DB
# ============================================================

def create_notion_page(db_id: str, item: dict):
    """
    Equivalent to n8n "Create Notion Page" node.

    Uses property names exactly like your Notion DB:
      - Name (title)
      - Class (rich_text)
      - Due Date (date)
      - Status (select)
      - Link (url)
      - ID (number)
      - Description (rich_text)
      - Points (number)
      - Score (number)
      - Assignment Updated Date (date)
      - Submitted Date (date)
    """
    props = {}

    # Name | title
    props["Name"] = {
        "title": [
            {
                "text": {
                    "content": item["name"],
                }
            }
        ]
    }

    # Class | rich_text
    if item.get("class"):
        props["Class"] = {
            "rich_text": [
                {
                    "text": {
                        "content": item["class"],
                    }
                }
            ]
        }

    # Due Date | date
    if item.get("due_date"):
        props["Due Date"] = {
            "date": {
                "start": item["due_date"],
            }
        }

    # Status | select
    if item.get("status"):
        props["Status"] = {
            "select": {
                "name": item["status"],
            }
        }

    # Link | url
    if item.get("link"):
        props["Link"] = {
            "url": item["link"],
        }

    # ID | number
    if item.get("canvas_id") is not None:
        props["ID"] = {
            "number": item["canvas_id"],
        }

    # Description | rich_text
    if item.get("description"):
        props["Description"] = {
            "rich_text": [
                {
                    "text": {
                        "content": item["description"],
                    }
                }
            ]
        }

    # Points | number
    if item.get("points_possible") is not None:
        props["Points"] = {
            "number": item["points_possible"],
        }

    # Score | number
    if item.get("score_obtained") is not None:
        props["Score"] = {
            "number": item["score_obtained"],
        }

    # Assignment Updated Date | date
    if item.get("updated_at"):
        props["Assignment Updated Date"] = {
            "date": {
                "start": item["updated_at"],
            }
        }

    # Submitted Date | date
    if item.get("submitted_date"):
        props["Submitted Date"] = {
            "date": {
                "start": item["submitted_date"],
            }
        }

    payload = {
        "parent": {"database_id": db_id},
        "properties": props,
    }

    url = "https://api.notion.com/v1/pages"
    headers = get_notion_headers()
    resp = requests.post(url, headers=headers, data=json.dumps(payload))
    resp.raise_for_status()
    return resp.json()


# ============================================================
# MAIN
# ============================================================

def main():
    # Basic checks
    if not CANVAS_API_TOKEN:
        raise RuntimeError("Missing CANVAS_API_TOKEN")
    if not NOTION_API_KEY:
        raise RuntimeError("Missing NOTION_API_KEY")

    print("🔗 Connecting to Notion…")
    # 1) Find latest DB in parent page
    latest_db_id = get_latest_db_id()
    print(f"🧱 Latest DB under parent: {latest_db_id}")

    # 2) Read old schema
    old_schema = get_db_schema(latest_db_id)

    # 3) Create new DB from old schema
    new_db_id = create_new_database_from_schema(old_schema)

    # 4) Archive old DB
    archive_database(latest_db_id)

    print("\n📚 Fetching active Canvas courses…")
    all_courses = get_canvas_courses()
    course_map = build_course_map(all_courses)

    # Decide which course IDs to use
    if CANVAS_COURSE_IDS:
        # Filter active courses by explicit list
        selected_ids = set(CANVAS_COURSE_IDS)
        active_ids = {str(c["id"]) for c in all_courses if c.get("id") is not None}
        # Only process those that are both active AND listed
        final_course_ids = sorted(selected_ids & active_ids)
        if not final_course_ids:
            raise RuntimeError(
                f"⚠ CANVAS_COURSE_IDS={CANVAS_COURSE_IDS} but none are active / available."
            )
        print(f"🎯 Using course filter (env CANVAS_COURSE_IDS): {', '.join(final_course_ids)}")
    else:
        # All active student courses
        final_course_ids = sorted(
            str(c["id"]) for c in all_courses if c.get("id") is not None
        )
        print(f"🎯 No course filter set → using all active student courses: {', '.join(final_course_ids)}")

    # 5) For each selected course, fetch assignments & create pages
    for cid_str in final_course_ids:
        cid = int(cid_str)
        course_name = course_map.get(cid_str, "Unknown Course")
        print(f"\n📘 Course {cid} — {course_name}")
        assignments = get_canvas_assignments(cid)

        count = 0
        for a in assignments:
            item = transform_assignment(a, course_map)
            create_notion_page(new_db_id, item)
            count += 1

        print(f"✅ Synced {count} assignments into Notion for course {cid}.")

    print("\n✨ Done: Canvas → Notion sync complete.")


if __name__ == "__main__":
    main()
