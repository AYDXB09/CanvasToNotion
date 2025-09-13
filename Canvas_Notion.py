import requests

# ========================
# CONFIG
# ========================
CANVAS_BASE = "https://dwight.instructure.com/api/v1"
CANVAS_TOKEN = "REDACTED_CANVAS_TOKEN"
COURSE_IDS = [7297]  # Add more course IDs if needed

NOTION_TOKEN = "REDACTED_NOTION_TOKEN"
NOTION_DATABASE_ID = "REDACTED_NOTION_ID" 

headers_canvas = {"Authorization": f"Bearer {CANVAS_TOKEN}"} 
headers_notion = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}


# ========================
# FETCH NOTION DATABASE SCHEMA
# ========================
def get_database_schema():
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}"
    r = requests.get(url, headers=headers_notion)
    if r.status_code != 200:
        raise Exception(f"Error fetching database schema: {r.text}")
    return r.json()["properties"]


# ========================
# FETCH EXISTING NOTION PAGES
# ========================
def get_notion_assignments():
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    results = []
    has_more = True
    next_cursor = None

    while has_more:
        payload = {"start_cursor": next_cursor} if next_cursor else {}
        resp = requests.post(url, headers=headers_notion, json=payload)
        data = resp.json()
        if resp.status_code != 200:
            raise Exception(f"Error querying Notion database: {resp.text}")
        results.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    # Map Canvas ID -> Notion Page ID
    notion_map = {}
    for page in results:
        props = page["properties"]
        canvas_id = props.get("Canvas ID", {}).get("rich_text", [])
        if canvas_id:
            notion_map[canvas_id[0]["text"]["content"]] = page["id"]

    return notion_map


# ========================
# BUILD PAYLOAD FOR NOTION
# ========================
def build_payload(assignment, course_name, schema):
    canvas_id = str(assignment["id"])
    due_date = assignment.get("due_at")
    status = "Completed" if assignment.get("has_submitted_submissions") else "Pending"

    payload = {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": {}}

    # Assignment Name (Title)
    if "Assignment Name" in schema:
        payload["properties"]["Assignment Name"] = {
            "title": [{"text": {"content": assignment["name"]}}]
        }

    # Course (Rich Text)
    if "Course" in schema:
        payload["properties"]["Course"] = {
            "rich_text": [{"text": {"content": course_name}}]
        }

    # Due Date (Date)
    if "Due Date" in schema:
        payload["properties"]["Due Date"] = {
            "date": {"start": due_date} if due_date else None
        }

    # Status (Select)
    if "Status" in schema:
        payload["properties"]["Status"] = {"select": {"name": status}}

    # Canvas URL (URL)
    if "Canvas URL" in schema:
        payload["properties"]["Canvas URL"] = {"url": assignment["html_url"]}

    # Canvas ID (Rich Text)
    if "Canvas ID" in schema:
        payload["properties"]["Canvas ID"] = {
            "rich_text": [{"text": {"content": canvas_id}}]
        }

    return payload


# ========================
# CREATE OR UPDATE NOTION PAGE
# ========================
def upsert_assignment(assignment, course_name, notion_map, schema):
    payload = build_payload(assignment, course_name, schema)
    canvas_id = str(assignment["id"])

    if canvas_id in notion_map:
        # Update existing page
        notion_page_id = notion_map[canvas_id]
        url = f"https://api.notion.com/v1/pages/{notion_page_id}"
        r = requests.patch(url, headers=headers_notion, json=payload)
        print("Update:", assignment["name"], r.status_code, r.text)
    else:
        # Create new page
        url = "https://api.notion.com/v1/pages"
        r = requests.post(url, headers=headers_notion, json=payload)
        print("Create:", assignment["name"], r.status_code, r.text)


# ========================
# MAIN
# ========================
def main():
    print("🔄 Sync starting...")
    schema = get_database_schema()
    notion_map = get_notion_assignments()

    for course_id in COURSE_IDS:
        # Get course info
        course = requests.get(f"{CANVAS_BASE}/courses/{course_id}", headers=headers_canvas).json()
        course_name = course.get("name", f"Course {course_id}")

        # Get assignments
        resp = requests.get(f"{CANVAS_BASE}/courses/{course_id}/assignments", headers=headers_canvas)
        if resp.status_code != 200:
            print(f"❌ Error fetching assignments for {course_id}: {resp.text}")
            continue

        assignments = resp.json()
        for assignment in assignments:
            upsert_assignment(assignment, course_name, notion_map, schema)

    print("✅ Sync complete.")


if __name__ == "__main__":
    main()
