Canvas to Notion Sync (Python Script)
A powerful Python script to automatically synchronize your assignments and course data from Canvas LMS directly into a Notion database. Stay organized and keep all your academic tasks in one place without manual entry.

This script fetches assignments from your Canvas courses, intelligently determines their status (e.g., "Not Started", "In Progress", "Completed", "Overdue"), formats the data, and then creates or updates corresponding entries in a structured Notion database.

✨ Features
Automated Syncing: Run the script to keep your Notion database up-to-date with Canvas.

Intelligent Status Tracking: Automatically assigns a status to each assignment based on submission status and due dates.

Handles Multiple Courses: Processes assignments from all Canvas courses specified in your configuration.

Secure Credential Management: Uses environment variables to keep your API keys and tokens safe and out of the codebase.

Easy to Customize: Built with Python, making it simple to modify and extend.

🚀 Getting Started
Follow these steps to get the script running on your local machine.

Prerequisites

Python 3.8+ installed on your machine.

pip (Python package installer).

Git for cloning the repository.

Canvas LMS Account: You'll need an Access Token.

Notion Account: You'll need a Notion API key and a database.

1. Set Up Your Notion Database

Create a Notion Database: If you don't have one, create a new database in Notion for your assignments.

Add Database Properties: Your database must have properties that correspond to the data you want to sync. Here is a recommended schema:

Property Name

Type

Purpose

Name

Title

The name of the assignment.

Class

Select

The name of the course.

Due Date

Date

The assignment's due date.

Status

Select

e.g., Not Started, In Progress, Overdue, Completed

Link

URL

A direct link to the assignment on Canvas.

Canvas ID

Text

The unique ID from Canvas for syncing.

Points Possible

Number

The total points for the assignment.

Score Obtained

Number

Your score after grading.

Get Your Notion API Key & Database ID:

Go to https://www.notion.so/my-integrations.

Create a new integration to get your Internal Integration Token. This is your NOTION_API_KEY.

Go to your Notion database, click the ... menu, and select "Add connections". Select the integration you just created.

Find your Database ID in the URL of your Notion page. It's the long string of characters between your workspace name and the ?v=. This is your NOTION_DATABASE_ID.

2. Get Your Canvas Access Token

Log in to Canvas.

Go to Account -> Settings.

Scroll down to Approved Integrations and click "+ New Access Token".

Give it a purpose (e.g., "Notion Sync Script") and generate the token.

Copy this token immediately. You won't be able to see it again. This is your CANVAS_API_TOKEN.

3. Installation & Configuration

Clone the repository:

git clone [https://github.com/AYDXB09/CanvasToNotion.git](https://github.com/AYDXB09/CanvasToNotion.git)
cd CanvasToNotion

Create a virtual environment (recommended):

python -m venv venv
source venv/bin/activate  # On Windows, use `venv\Scripts\activate`

Install the required packages:

pip install -r requirements.txt

Configure your credentials:

This project uses a .env file to manage your secret keys.

Make a copy of the example file:

cp .env.example .env

Open the newly created .env file with a text editor and fill in your actual credentials that you collected in the steps above.

4. Run the Script

Once everything is installed and configured, you can run the sync script from your terminal:

python your_main_script_name.py

(Please replace your_main_script_name.py with the actual name of your main Python file.)

The script will now fetch data from Canvas and populate your Notion database.

🔧 Customization
Change Course IDs: Update the CANVAS_COURSE_IDS variable in your .env file with a comma-separated list of the course IDs you want to sync.

Modify Logic: Feel free to edit the Python script to change how statuses are determined or what data gets synced.

🤝 Contributing
Contributions, issues, and feature requests are welcome! Feel free to check the issues page.

📝 License
This project is open-source and available under the MIT License.

