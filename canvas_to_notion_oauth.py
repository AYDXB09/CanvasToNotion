import os
import json
import requests
from urllib.parse import urlencode
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta

# ------------------------------
# CONFIGURATION
# ------------------------------
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://dwight.instructure.com")
CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN")
CANVAS_COURSE_IDS = os.environ.get("CANVAS_COURSE_IDS", "").split(",")

NOTION_CLIENT_ID = os.environ.get("NOTION_CLIENT_ID")
NOTION_CLIENT_SECRET = os.environ.get("NOTION_CLIENT_SECRET")
NOTION_REDIRECT_URI = os.environ.get("NOTION_REDIRECT_URI", "http://localhost:8000/callback")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

TOKEN_FILE = ".token.json"
NOTION_VERSION = "2022-06-28"

# ------------------------------
# OAUTH FLOW
# ------------------------------
def start_oauth_flow():
    """Step 1: Start OAuth authorization flow in browser"""
    params = {
        "owner": "user",
        "client_id": NOTION_CLIENT_ID,
        "scope": "databases:read databases:write pages:read pages:write",
        "redirect_uri": NOTION_REDIRECT_URI,
        "response_type": "code",
    }
    url = f"https://api.notion.com/v1/oauth/authorize?{urlencode(params)}"
    print("Open this URL in your browser to authorize Notion:")
    print(url)
    print("\nAfter authorization, you will be redirected to the redirect URI.\n")

# Simple HTTP server to capture code
class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if "/callback" in self.path:
            code = self.path.split("code=")[-1]
            self.server.auth_code = code
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorization code received. You can close this tab.")
        else:
            self.send_response(404)
            self.end_headers()

def get_tokens():
    """Exchange code for access + refresh tokens"""
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            data = json.load(f)
            return data

    # Start OAuth flow
    start_oauth_flow()
    server = HTTPServer(("localhost", 8000), OAuthHandler)
    print("Waiting for OAuth authorization...")
    server.handle_request()
    code = server.auth_code
    print("Received code:", code)

    # Exchange code
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": NOTION_REDIRECT_URI,
        "client_id": NOTION_CLIENT_ID,
        "client_secret": NOTION_CLIENT_SECRET,
    }
    r = requests.post("https://api.notion.com/v1/oauth/token", data=payload)
    r.raise_for_status()
    token_data = r.json()
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)
    return token_data

def refresh_token(refresh_token):
    """Refresh Notion token"""
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": NOTION_CLIENT_ID,
        "client_secret": NOTION_CLIENT_SECRET,
    }
    r = requests.post("https://api.notion.com/v1/oauth/token", data=payload)
    r.raise_for_status()
    token_data = r.json()
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)
    return token_data

def get_notion_headers(token_data):
    return {
        "Authorization": f"Bearer {token_data['access_token']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

# ------------------------------
# CANVAS FUNCTIONS
# ------------------------------
def get_canvas_assignments(course_id):
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments"
    headers = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json()

# ------------------------------
# NOTION FUNCTIONS
# ------------------------------
def notion_find_page_by_canvas_id(canvas_id, token_data):
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    headers = get_notion_headers(token_data)
    payload = {
        "filter": {
            "property": "Canvas ID",
            "rich_text": {"equals": str(canvas_id)}
        }
    }
    r = requests.post(url, headers=headers, json=payload)
    if r.status_code == 401:
        token_data = refresh_token(token_data['refresh_token'])
        headers = get_notion_headers(token_data)
        r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None

def notion_create_or_update(assignment, token_data):
    existing_page = notion_find_page_by_canvas_id(assignment["id"], token_data)
    headers = get_notion_headers(token_data)

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

    if existing_page:
        page_id = existing_page["id"]
        url = f"https://api.notion.com/v1/pages/{page_id}"
        r = requests.patch(url, headers=headers, json=data)
    else:
        url = "https://api.notion.com/v1/pages"
        r = requests.post(url, headers=headers, json=data)
    r.raise_for_status()
    return r.json()

# ------------------------------
# MAIN
# ------------------------------
def main():
    if not all([CANVAS_API_TOKEN, NOTION_CLIENT_ID, NOTION_CLIENT_SECRET, NOTION_DATABASE_ID]):
        raise Exception("Missing environment variables for Canvas or Notion OAuth.")

    token_data = get_tokens()

    for course_id in CANVAS_COURSE_IDS:
        course_id = course_id.strip()
        if not course_id:
            continue

        print(f"Fetching assignments for course {course_id}...")
        assignments = get_canvas_assignments(course_id)
        for assignment in assignments:
            print(f"Syncing assignment: {assignment['name']}")
            notion_create_or_update(assignment, token_data)


if __name__ == "__main__":
    main()
