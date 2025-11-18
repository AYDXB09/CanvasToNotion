# Canvas → Notion Automation  
Automatically sync all your Canvas assignments into a freshly-created Notion database every week.

This project connects:

- **Canvas LMS** → pulls all active courses + their assignments  
- **Notion** → recreates a clean database on every run  
- **GitHub Actions** → runs the sync automatically every Monday evening  
- Supports **optional due-date filtering**, **HTML-cleaned descriptions**, and **fixed legacy schema** identical to the n8n workflow.

---

# 📌 Features

### ✅ 1. Automatically fetches Canvas assignments  
- Reads all **active courses** for the authenticated student  
- Optional: manually restrict course IDs using `CANVAS_COURSE_IDS`  
- Pulls:
  - Assignment name  
  - Due date  
  - Description (HTML → clean text, no `<tags>`, no `&nbsp;`)  
  - Updated date  
  - Submission status  
  - Points possible  
  - Score (if any)  

---

### ✅ 2. Fully recreates Notion database every run  
- Archives any existing database named **“Canvas Course - Track Assignments”** under the parent page  
- Creates a fresh database under your chosen Notion page  
- Uses **Legacy Schema A** (the long version used in your n8n workflow), including:

| Field Name | Type |
|------------|------|
| Name | Title |
| Assignment Updated Date | Date |
| Class | Text |
| Description | Text |
| Due Date | Date |
| ID | Text |
| Link | URL |
| Points | Number |
| Score | Number |
| Status | Select (Overdue / In Progress / Completed / Not Started) |
| Submitted Date | Date |

---

### ✅ 3. Optional Due-Date Filtering  
Uses two repository variables:

| Variable | Purpose |
|---------|----------|
| `DUE_DATE_PERIOD_START` | Include assignments due after this date |
| `DUE_DATE_PERIOD_END` | Include assignments due before this date |
| `INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE` | `"true"` or `"false"` |

Filter logic:

1. If **both start & end** are provided → include between  
2. If **only end** is provided → include before end  
3. If **only start** is provided → include after start  
4. If neither provided → include all  
5. If assignment has **no due date** → include only if `INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE=true`

---

### ✅ 4. Clean & HTML-free descriptions  
The script removes:

- `<p>`, `<strong>`, `<em>`, etc.  
- Inline HTML links  
- Canvas’s `&nbsp;` entities  

So your Notion database stays clean and readable.

---

### ✅ 5. Weekly Scheduler (GitHub Actions)  
Runs automatically:

> **Every Monday – 6:00 PM Dubai Time (14:00 UTC)**

You also have **Run workflow manually** support.

---

# 📁 Project Structure

📦 repository
├── canvas_to_notion.py        → Main script
└── .github/workflows/
└── canvas_to_notion.yml → Scheduler + automation

---

# 🔧 Setup Instructions

Follow these steps **exactly** after cloning the repository.

---

## 1️⃣ Create the required *Secrets*

Open:

**GitHub → Repository → Settings → Secrets and variables → Actions → New repository secret**

Create these secrets:

| Secret Name | Value |
|-------------|--------|
| `CANVAS_API_TOKEN` | Your Canvas API token |
| `NOTION_API_KEY` | Your Notion internal integration token |
| `NOTION_PARENT_PAGE_ID` | Page ID where the DB will be created |
| `EMAIL_RECIPIENTS` | Comma-separated list: `redacted@example.com, redacted@example.com` |

---

## 2️⃣ Create the required *Variables*

Go to:

**Settings → Secrets and variables → Actions → Variables**

Add:

| Variable Name | Example Value | Purpose |
|---------------|---------------|---------|
| `CANVAS_COURSE_IDS` | `7229,7243` | Optional course filtering |
| `NOTION_DATABASE_NAME` | `Canvas Course - Track Assignments` | Name of DB to create |
| `DUE_DATE_PERIOD_START` | `2025-11-20` | Optional filter |
| `DUE_DATE_PERIOD_END` | `2025-11-30` | Optional filter |
| `INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE` | `true` | Include/exclude assignments without due dates |

You may leave date variables blank or default them to `" "`.

---

# ▶️ Running the Sync Manually

Go to:

**GitHub → Actions → Canvas → Notion (Assignments Sync) → Run workflow**

---

# 🔄 Automated Weekly Scheduler

Runs automatically **every Monday at 6 PM Dubai**.

The workflow:

1. Checks out repository  
2. Installs Python  
3. Runs `canvas_to_notion.py`  
4. Emails success/failure to recipients (coming from `EMAIL_RECIPIENTS` secret)

---

# 📤 Email Notifications
After each scheduled run, an email is sent to:

- **redacted@example.com**
- **redacted@example.com**

Recipients are stored safely in the `EMAIL_RECIPIENTS` secret.

---

# 🛡️ Security Notes

- No OAuth needed.  
- All API tokens stored in GitHub Secrets.  
- Notion database is recreated from scratch every time → consistent & clean.  
- Descriptions sanitized to avoid Notion corruption.

---

# 🧪 How to Test Changes (Best Practice)

1. Create a branch such as `feature/due-date-filter`  
2. Push your changes  
3. Run workflow manually for the branch  
4. Validate Notion database  
5. Merge into `main`

---

# 🚀 That's it!
