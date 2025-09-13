import requests
import json
from datetime import datetime, timezone
import os
from typing import List, Dict, Optional 
import time

class CanvasNotionSync:
    def __init__(self):
        # API credentials from environment variables
        self.canvas_api_url = os.getenv('CANVAS_API_URL')  # https://yourschool.instructure.com/api/v1
        self.canvas_token = os.getenv('CANVAS_TOKEN')
        self.notion_token = os.getenv('NOTION_TOKEN')
        self.notion_database_id = os.getenv('NOTION_DATABASE_ID')
        
        # Headers for API requests
        self.canvas_headers = {
            'Authorization': f'Bearer {self.canvas_token}',
            'Content-Type': 'application/json'
        }
        
        self.notion_headers = {
            'Authorization': f'Bearer {self.notion_token}',
            'Content-Type': 'application/json',
            'Notion-Version': '2022-06-28'
        }
    
    def get_canvas_courses(self) -> List[Dict]:
        """Fetch all courses from Canvas"""
        url = f"{self.canvas_api_url}/courses"
        params = {
            'enrollment_state': 'active',
            'per_page': 100
        }
        
        try:
            response = requests.get(url, headers=self.canvas_headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching courses: {e}")
            return []
    
    def get_canvas_assignments(self, course_id: str) -> List[Dict]:
        """Fetch assignments for a specific course"""
        url = f"{self.canvas_api_url}/courses/{course_id}/assignments"
        params = {
            'per_page': 100,
            'order_by': 'due_at'
        }
        
        try:
            response = requests.get(url, headers=self.canvas_headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching assignments for course {course_id}: {e}")
            return []
    
    def get_assignment_submission(self, course_id: str, assignment_id: str) -> Optional[Dict]:
        """Get submission status for an assignment"""
        url = f"{self.canvas_api_url}/courses/{course_id}/assignments/{assignment_id}/submissions/self"
        
        try:
            response = requests.get(url, headers=self.canvas_headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching submission for assignment {assignment_id}: {e}")
            return None
    
    def format_date_for_notion(self, date_str: Optional[str]) -> Optional[str]:
        """Convert Canvas date to Notion format"""
        if not date_str:
            return None
        
        try:
            # Canvas uses ISO format
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return dt.isoformat()
        except Exception as e:
            print(f"Error formatting date {date_str}: {e}")
            return None
    
    def determine_assignment_status(self, submission: Optional[Dict]) -> str:
        """Determine assignment status based on submission data"""
        if not submission:
            return "Not Started"
        
        workflow_state = submission.get('workflow_state', '')
        submitted_at = submission.get('submitted_at')
        
        if submitted_at and workflow_state == 'submitted':
            return "Completed"
        elif workflow_state == 'pending_review':
            return "Submitted - Pending Review"
        elif submission.get('late', False):
            return "Late Submission"
        else:
            return "In Progress"
    
    def create_notion_page(self, assignment_data: Dict) -> bool:
        """Create a new page in Notion database"""
        url = "https://api.notion.com/v1/pages"
        
        # Prepare the page data
        page_data = {
            "parent": {
                "database_id": self.notion_database_id
            },
            "properties": {
                "Assignment Name": {
                    "title": [
                        {
                            "text": {
                                "content": assignment_data['name']
                            }
                        }
                    ]
                },
                "Course": {
                    "rich_text": [
                        {
                            "text": {
                                "content": assignment_data['course_name']
                            }
                        }
                    ]
                },
                "Due Date": {
                    "date": {
                        "start": assignment_data['due_date']
                    } if assignment_data['due_date'] else None
                },
                "Status": {
                    "select": {
                        "name": assignment_data['status']
                    }
                },
                "Points": {
                    "number": assignment_data.get('points_possible', 0)
                },
                "Canvas URL": {
                    "url": assignment_data['html_url']
                },
                "Canvas ID": {
                    "rich_text": [
                        {
                            "text": {
                                "content": str(assignment_data['id'])
                            }
                        }
                    ]
                },
                "Description": {
                    "rich_text": [
                        {
                            "text": {
                                "content": assignment_data.get('description', '')[:2000]  # Notion has character limits
                            }
                        }
                    ] if assignment_data.get('description') else []
                }
            }
        }
        
        try:
            response = requests.post(url, headers=self.notion_headers, json=page_data)
            response.raise_for_status()
            print(f"✅ Created: {assignment_data['name']}")
            return True
        except requests.exceptions.RequestException as e:
            print(f"❌ Error creating page for {assignment_data['name']}: {e}")
            return False
    
    def get_existing_notion_assignments(self) -> Dict[str, str]:
        """Get existing assignments from Notion database"""
        url = f"https://api.notion.com/v1/databases/{self.notion_database_id}/query"
        
        existing_assignments = {}
        has_more = True
        start_cursor = None
        
        while has_more:
            payload = {}
            if start_cursor:
                payload["start_cursor"] = start_cursor
            
            try:
                response = requests.post(url, headers=self.notion_headers, json=payload)
                response.raise_for_status()
                data = response.json()
                
                for page in data.get('results', []):
                    canvas_id_prop = page.get('properties', {}).get('Canvas ID', {})
                    if canvas_id_prop.get('rich_text'):
                        canvas_id = canvas_id_prop['rich_text'][0]['text']['content']
                        existing_assignments[canvas_id] = page['id']
                
                has_more = data.get('has_more', False)
                start_cursor = data.get('next_cursor')
                
            except requests.exceptions.RequestException as e:
                print(f"Error fetching existing assignments: {e}")
                break
        
        return existing_assignments
    
    def update_notion_page(self, page_id: str, assignment_data: Dict) -> bool:
        """Update an existing Notion page"""
        url = f"https://api.notion.com/v1/pages/{page_id}"
        
        update_data = {
            "properties": {
                "Status": {
                    "select": {
                        "name": assignment_data['status']
                    }
                },
                "Due Date": {
                    "date": {
                        "start": assignment_data['due_date']
                    } if assignment_data['due_date'] else None
                }
            }
        }
        
        try:
            response = requests.patch(url, headers=self.notion_headers, json=update_data)
            response.raise_for_status()
            print(f"🔄 Updated: {assignment_data['name']}")
            return True
        except requests.exceptions.RequestException as e:
            print(f"❌ Error updating {assignment_data['name']}: {e}")
            return False
    
    def sync_assignments(self):
        """Main sync function"""
        print("🚀 Starting Canvas to Notion sync...")
        
        # Get existing assignments from Notion
        existing_assignments = self.get_existing_notion_assignments()
        print(f"📋 Found {len(existing_assignments)} existing assignments in Notion")
        
        # Get all courses
        courses = self.get_canvas_courses()
        print(f"📚 Found {len(courses)} active courses")
        
        total_assignments = 0
        new_assignments = 0
        updated_assignments = 0
        
        for course in courses:
            course_id = course['id']
            course_name = course['name']
            print(f"\n📖 Processing course: {course_name}")
            
            # Get assignments for this course
            assignments = self.get_canvas_assignments(course_id)
            
            for assignment in assignments:
                total_assignments += 1
                
                # Get submission status
                submission = self.get_assignment_submission(course_id, assignment['id'])
                
                # Prepare assignment data
                assignment_data = {
                    'id': assignment['id'],
                    'name': assignment['name'],
                    'course_name': course_name,
                    'due_date': self.format_date_for_notion(assignment.get('due_at')),
                    'status': self.determine_assignment_status(submission),
                    'points_possible': assignment.get('points_possible'),
                    'html_url': assignment['html_url'],
                    'description': assignment.get('description', '')
                }
                
                canvas_id = str(assignment['id'])
                
                if canvas_id in existing_assignments:
                    # Update existing assignment
                    if self.update_notion_page(existing_assignments[canvas_id], assignment_data):
                        updated_assignments += 1
                else:
                    # Create new assignment
                    if self.create_notion_page(assignment_data):
                        new_assignments += 1
                
                # Small delay to avoid rate limiting
                time.sleep(0.1)
        
        print(f"\n✨ Sync completed!")
        print(f"📊 Total assignments processed: {total_assignments}")
        print(f"🆕 New assignments created: {new_assignments}")
        print(f"🔄 Assignments updated: {updated_assignments}")
    
    def get_pending_assignments_count(self) -> int:
        """Get count of pending assignments from Notion"""
        url = f"https://api.notion.com/v1/databases/{self.notion_database_id}/query"
        
        filter_data = {
            "filter": {
                "and": [
                    {
                        "property": "Status",
                        "select": {
                            "does_not_equal": "Completed"
                        }
                    }
                ]
            }
        }
        
        try:
            response = requests.post(url, headers=self.notion_headers, json=filter_data)
            response.raise_for_status()
            data = response.json()
            return len(data.get('results', []))
        except requests.exceptions.RequestException as e:
            print(f"Error getting pending assignments count: {e}")
            return 0

def main():
    import sys
    
    # Initialize the sync class
    sync = CanvasNotionSync()
    
    # Check if user wants to list courses
    if len(sys.argv) > 1 and sys.argv[1] == '--list-courses':
        sync.list_available_courses()
        return
    
    # Validate environment variables
    required_vars = ['CANVAS_API_URL', 'CANVAS_TOKEN', 'NOTION_TOKEN', 'NOTION_DATABASE_ID']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("\nPlease set these in your GitHub Codespaces secrets:")
        print("1. CANVAS_API_URL (e.g., https://yourschool.instructure.com/api/v1)")
        print("2. CANVAS_TOKEN (from Canvas Account Settings)")
        print("3. NOTION_TOKEN (from Notion integrations)")
        print("4. NOTION_DATABASE_ID (from your Notion database URL)")
        print("\nOptional filtering variables:")
        print("5. CANVAS_COURSE_IDS (comma-separated course IDs)")
        print("6. CANVAS_COURSE_CODES (comma-separated course codes)")
        print("7. CANVAS_COURSE_NAMES (comma-separated course names)")
        print("8. EXCLUDE_COMPLETED_COURSES (true/false, default: true)")
        return
    
    # Show current filter settings
    if sync.course_ids or sync.course_codes or sync.course_names:
        print("🎯 Course Filtering Active:")
        if sync.course_ids:
            print(f"  📋 Course IDs: {', '.join(sync.course_ids)}")
        if sync.course_codes:
            print(f"  🏷️  Course Codes: {', '.join(sync.course_codes)}")
        if sync.course_names:
            print(f"  📚 Course Names: {', '.join(sync.course_names)}")
        print(f"  🚫 Exclude Completed: {sync.exclude_completed}")
        print()
    else:
        print("📚 Syncing ALL courses (no filters applied)")
        print("💡 Use --list-courses to see available courses")
        print()
    
    # Run the sync
    sync.sync_assignments()
    
    # Show pending assignments count
    pending_count = sync.get_pending_assignments_count()
    print(f"\n📈 Dashboard Update: {pending_count} assignments still pending!")

if __name__ == "__main__":
    main()
