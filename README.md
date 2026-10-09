# Canvas → Notion Assignment Sync

![Python](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/runs%20on-GitHub%20Actions-2088FF?logo=githubactions&logoColor=white)
![Notion](https://img.shields.io/badge/Notion-API-000000?logo=notion&logoColor=white)
![Tests](https://img.shields.io/badge/tests-27%20offline-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

> [!NOTE]
> The code is covered by 27 offline tests, and the last real scheduled run in this repository's history succeeded (2026-01-19). **GitHub automatically disables scheduled workflows in a repository with no activity for 60 days** — if the Actions tab shows this workflow as disabled, click **Enable workflow** to resume the weekly sync.

A small automation for a student who tracks homework in Notion: every Monday it reads **all your active Canvas courses and their assignments** and rebuilds a clean Notion database with one row per assignment — name, class, due date, points, score, a link back to Canvas, and a status that says what you still need to do. It runs for free on GitHub Actions, with no server.

### Contents
[How it works](#how-it-works) · [The status column](#the-status-column) · [Real examples](#real-examples) · [Notion database](#the-notion-database) · [Configuration](#configuration) · [Setup](#setup) · [Date filter](#the-due-date-filter) · [Tests](#tests) · [Honest limits](#honest-limits) · [Security & privacy](#security--privacy) · [License](#license)

## How it works

1. **Read Canvas first.** Lists your active student courses (all pages of results, optionally limited to certain course IDs), fetches every assignment, applies the due-date filter, and looks up *your own* submission for each one.
2. **Only then touch Notion.** If anything in step 1 fails, your existing Notion database is left exactly as it was.
3. **Rebuild.** Archives the old database with the configured title under your parent page, creates a fresh one, and writes one row per assignment.

Python + `requests` only; no Notion or Canvas SDK. The schedule is `0 14 * * 1` — **Mondays 14:00 UTC = 6 PM Dubai** — and you can also start it by hand (**Actions → Run workflow**).

## The status column

| Status | When |
|---|---|
| **Completed** | Canvas says *you* submitted it, it is graded, or it is pending review |
| **Overdue** | You haven't submitted and the due date has passed |
| **Not Started** | You haven't submitted and the due date is still ahead (or there is none) |
| **In Progress** | Only if Canvas couldn't return your submission and the due date is ahead |

> [!IMPORTANT]
> Earlier versions of this script could only ever produce *Completed* and *Not Started* (the "not submitted" check ran after, and overrode, the overdue check), and treated "any student has submitted" as "I submitted". Both were fixed and are covered by tests — see [Honest limits](#honest-limits) for what this means for old rows.

## Real examples

What the script does with real-shaped Canvas data (these cases are asserted by the test suite):

| Canvas gives it | The Notion row gets |
|---|---|
| Due 3 days ago, `workflow_state: unsubmitted` | Status **Overdue** |
| Due in 3 days, `unsubmitted` | Status **Not Started** |
| `workflow_state: graded`, score 9 | Status **Completed**, Score **9** |
| A classmate has submitted, you haven't, and it's past due | **Overdue** (the assignment-wide "someone submitted" flag is ignored) |
| Description `<p>Write 500 words.</p><p>Cite &amp; check.</p>` | `Write 500 words.` ⏎ `Cite & check.` (paragraphs kept apart, entities decoded) |
| Description `<ul><li>Alpha</li><li>Beta</li></ul>` | `Alpha` ⏎ `Beta` |
| Description `<script>alert(1)</script>Hi` | `Hi` |
| An assignment with no due date | Included only if `INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE` is `true` |
| A course with 150 assignments | All 150 (Canvas's paginated responses are followed) |
| Notion answers HTTP 429 (rate limited) | Waits as long as Notion asks, then retries |

## The Notion database

Created under `NOTION_PARENT_PAGE_ID`, titled `NOTION_DB_TITLE` (default **Canvas Course - Track Assignments**):

| Field | Type |
|---|---|
| Name | Title |
| Class | Text — the course code (or name if there is no code) |
| Description | Text — plain text, at most 500 characters |
| Due Date · Submitted Date · Assignment Updated Date | Date |
| Points · Score | Number |
| Status | Select — Overdue / In Progress / Completed / Not Started |
| ID | Text — the Canvas assignment ID |
| Link | URL — the assignment in Canvas |

## Configuration

Set these in **Settings → Secrets and variables → Actions**.

| Name | Kind | Required | Purpose |
|---|---|---|---|
| `CANVAS_API_TOKEN` | **Secret** | yes | Your Canvas access token (read-only use) |
| `NOTION_API_KEY` | **Secret** | yes | A Notion internal-integration token |
| `NOTION_PARENT_PAGE_ID` | Variable | yes | The Notion page the database is created under |
| `NOTION_DB_TITLE` | Variable | no | Database title (default above). Also the title looked for when archiving the old one |
| `CANVAS_COURSE_IDS` | Variable | no | Comma-separated course IDs to include, e.g. `101,102`. Blank = all active courses |
| `DUE_DATE_PERIOD_START` · `DUE_DATE_PERIOD_END` | Variable | no | `YYYY-MM-DD`; blank = no limit. See [the filter](#the-due-date-filter) |
| `INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE` | Variable | no | `true` or `false` (default `false`) |

The Canvas address is set on one line of the workflow (`CANVAS_BASE_URL`, currently `https://dwight.instructure.com`) — change it for your school.

## Setup

<details>
<summary><b>Step by step</b> (click to expand)</summary>

1. **Canvas token:** Canvas → Account → Settings → *New Access Token*.
2. **Notion integration:** create an internal integration at notion.so/my-integrations, copy its token, then **share your parent page with the integration** (page menu → Connections). Without that share, Notion answers 404.
3. **Parent page ID:** the 32-character ID at the end of the page's URL.
4. **Add the secrets and variables** from the table above to your fork of this repository.
5. **Run it once by hand:** Actions → *Canvas → Notion (Assignments Sync)* → *Run workflow*, and check the database appears.

To run locally instead, export the same names as environment variables and run `python canvas_to_notion.py`.

</details>

## The due-date filter

| Settings | Included |
|---|---|
| Start **and** end | start ≤ due ≤ end |
| End only | due ≤ end |
| Start only | due ≥ start |
| Neither | everything with a due date |
| No due date on the assignment | only if `INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE=true` |

Dates are **midnight UTC**, so the *start* day is included in full while assignments due later than 00:00 UTC on the *end* day are not. A malformed date (anything but `YYYY-MM-DD`) stops the run with a clear message — earlier versions ignored it and silently switched the filter off.

## Tests

```bash
python -m unittest -v
```

27 tests, no network and no credentials. They cover the status rules, HTML cleaning, the date filter, configuration parsing, pagination, and a full fake run against stand-in Canvas and Notion servers, including "Canvas fails, Notion is untouched" and the rate-limit retry. The workflow runs them before every sync.

## Honest limits

- **The database is rebuilt from scratch every run.** Anything you add by hand in Notion (extra columns, notes, comments) and any links to individual rows are lost when the old database is archived. Archived databases go to Notion's trash.
- **Rows from before this version may show wrong statuses** (see the note above). They disappear on the next run, since the whole database is rebuilt.
- **One extra Canvas request per assignment** (your submission). Large courses take a while; if a lookup fails, that row falls back to a due-date-based status and the run log says how many did.
- **Descriptions are plain text, cut at 500 characters.** Images, tables and formatting are dropped.
- **Canvas tokens can expire** (your school sets the lifetime). A `401` in the run log means paste a new token into the `CANVAS_API_TOKEN` secret.
- **The schedule pauses itself** after 60 days without repository activity (see the note at the top). GitHub emails the person who last edited the schedule when a scheduled run fails, depending on their notification settings; the script sends no email of its own.
- It reads your own Canvas data only and never writes to Canvas.

## Security & privacy

- Tokens live in GitHub **Actions secrets** and are never written to the repository or printed. The workflow's token is read-only (`permissions: contents: read`).
- Data path: **Canvas → the GitHub Actions runner → your Notion workspace.** Nothing else receives it, and nothing is stored in this repository.
- No OAuth: it uses a personal Canvas token and a Notion internal-integration token. Use tokens you can revoke, and revoke them if they are ever exposed.

## License

[MIT](LICENSE) © 2026 Anvith Yalamanchili
