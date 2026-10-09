"""Offline tests for canvas_to_notion.py. No network, no credentials:  python -m unittest -v"""
import importlib
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENV_KEYS = ("CANVAS_API_TOKEN", "CANVAS_COURSE_IDS", "NOTION_API_KEY", "NOTION_PARENT_PAGE_ID", "NOTION_DB_TITLE",
            "DUE_DATE_PERIOD_START", "DUE_DATE_PERIOD_END", "INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE")


def load(**env):
    """Import canvas_to_notion fresh with the given environment (settings are read at import time)."""
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(env)
    sys.modules.pop("canvas_to_notion", None)
    return importlib.import_module("canvas_to_notion")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)
PAST, FUTURE = iso(NOW - timedelta(days=3)), iso(NOW + timedelta(days=3))


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def status(self, due, state):
        return self.m.status_from_canvas({"due_at": due}, {"workflow_state": state} if state else {})

    def test_past_due_and_not_submitted_is_overdue(self):
        # This is the case the tracker exists for; it used to come out as "Not Started".
        self.assertEqual(self.status(PAST, "unsubmitted"), "Overdue")

    def test_future_due_and_not_submitted_is_not_started(self):
        self.assertEqual(self.status(FUTURE, "unsubmitted"), "Not Started")

    def test_no_due_date_and_not_submitted_is_not_started(self):
        self.assertEqual(self.status(None, "unsubmitted"), "Not Started")

    def test_submitted_graded_and_pending_review_are_completed(self):
        for state in ("submitted", "graded", "pending_review"):
            for due in (PAST, FUTURE, None):
                self.assertEqual(self.status(due, state), "Completed", (state, due))

    def test_a_classmate_submitting_does_not_complete_my_assignment(self):
        # has_submitted_submissions is an assignment-wide flag ("any student has submitted").
        a = {"due_at": PAST, "has_submitted_submissions": True}
        self.assertEqual(self.m.status_from_canvas(a, {"workflow_state": "unsubmitted"}), "Overdue")
        self.assertEqual(self.m.status_from_canvas(a, {}), "Overdue")

    def test_when_the_submission_lookup_failed_it_falls_back_to_the_due_date(self):
        self.assertEqual(self.status(PAST, None), "Overdue")
        self.assertEqual(self.status(FUTURE, None), "In Progress")
        self.assertEqual(self.status(None, None), "Not Started")


class CleanDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.clean = load().clean_description

    def test_paragraphs_do_not_run_together(self):
        self.assertEqual(self.clean("<p>Read ch1.</p><p>Do Q2.</p>"), "Read ch1.\nDo Q2.")

    def test_line_breaks_and_list_items_are_kept_as_lines(self):
        self.assertEqual(self.clean("Line one<br>Line two<br/>Line three"), "Line one\nLine two\nLine three")
        self.assertEqual(self.clean("<ul><li>Alpha</li><li>Beta</li></ul>"), "Alpha\nBeta")

    def test_html_entities_are_decoded(self):
        self.assertEqual(self.clean("<p>Fish &amp; chips, 5 &lt; 6, &quot;q&quot; &#39;x&#39; caf&eacute;</p>"),
                         "Fish & chips, 5 < 6, \"q\" 'x' caf\u00e9")

    def test_non_breaking_spaces_become_spaces(self):
        self.assertEqual(self.clean("a&nbsp;b\u00a0c"), "a b c")

    def test_inline_tags_do_not_add_spaces(self):
        self.assertEqual(self.clean("<strong>bold</strong>text"), "boldtext")

    def test_script_and_style_content_is_dropped(self):
        self.assertEqual(self.clean("<script>alert(1)</script>Hi<style>p{color:red}</style>"), "Hi")

    def test_empty_input(self):
        self.assertEqual(self.clean(None), "")
        self.assertEqual(self.clean(""), "")

    def test_long_descriptions_are_cut_to_500_characters(self):
        self.assertEqual(len(self.clean("<p>" + "x" * 900 + "</p>")), 500)


def day(date_str, hour=12):
    return iso(datetime.strptime(date_str, "%Y-%m-%d").replace(hour=hour, tzinfo=timezone.utc))


class FilterTests(unittest.TestCase):
    def check(self, env, dues):
        m = load(**env)
        return [m.due_date_filter_ok({"due_at": d}) for d in dues]

    dues = [day("2025-11-19"), day("2025-11-25"), day("2025-12-05"), None]

    def test_start_and_end(self):
        self.assertEqual(self.check({"DUE_DATE_PERIOD_START": "2025-11-20", "DUE_DATE_PERIOD_END": "2025-11-30"}, self.dues),
                         [False, True, False, False])

    def test_end_only_and_start_only(self):
        self.assertEqual(self.check({"DUE_DATE_PERIOD_END": "2025-11-30"}, self.dues), [True, True, False, False])
        self.assertEqual(self.check({"DUE_DATE_PERIOD_START": "2025-11-20"}, self.dues), [False, True, True, False])

    def test_no_filter_keeps_everything_dated(self):
        self.assertEqual(self.check({}, self.dues), [True, True, True, False])

    def test_blank_dates_mean_no_filter(self):
        self.assertEqual(self.check({"DUE_DATE_PERIOD_START": " ", "DUE_DATE_PERIOD_END": " "}, self.dues), [True, True, True, False])

    def test_start_day_is_included_but_end_day_is_not(self):
        # Dates are midnight UTC, so anything due later than 00:00 on the END date is out.
        env = {"DUE_DATE_PERIOD_START": "2025-11-20", "DUE_DATE_PERIOD_END": "2025-11-30"}
        self.assertEqual(self.check(env, [day("2025-11-20", 0), day("2025-11-20", 18), day("2025-11-30", 0), day("2025-11-30", 1)]),
                         [True, True, True, False])

    def test_undated_assignments_follow_the_flag_even_with_trailing_whitespace(self):
        # The real repository variable is stored as "true" + newline.
        self.assertTrue(load(INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE="true\n").due_date_filter_ok({"due_at": None}))
        self.assertTrue(load(INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE=" TRUE ").due_date_filter_ok({"due_at": None}))
        self.assertFalse(load(INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE="false").due_date_filter_ok({"due_at": None}))
        self.assertFalse(load().due_date_filter_ok({"due_at": None}))

    def test_a_malformed_date_stops_the_run_instead_of_silently_disabling_the_filter(self):
        for key in ("DUE_DATE_PERIOD_START", "DUE_DATE_PERIOD_END"):
            with self.assertRaises(SystemExit) as ctx:
                load(**{key: "20-11-2025"})
            self.assertIn(key, str(ctx.exception))


class ConfigTests(unittest.TestCase):
    def test_database_title_default_and_override(self):
        self.assertEqual(load().NOTION_DB_TITLE, "Canvas Course - Track Assignments")
        self.assertEqual(load(NOTION_DB_TITLE="  My Assignments ").NOTION_DB_TITLE, "My Assignments")
        self.assertEqual(load(NOTION_DB_TITLE="   ").NOTION_DB_TITLE, "Canvas Course - Track Assignments")

    def test_missing_settings_are_named(self):
        with self.assertRaises(SystemExit) as ctx:
            load().main()
        for name in ("CANVAS_API_TOKEN", "NOTION_API_KEY", "NOTION_PARENT_PAGE_ID"):
            self.assertIn(name, str(ctx.exception))


class Resp:
    def __init__(self, data=None, status=200, links=None, headers=None):
        self._data, self.status_code, self.links, self.headers = data, status, links or {}, headers or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


class FakeWorld:
    """A tiny fake Canvas + Notion. Records every Notion call, in order."""
    OLD_TITLE = "Canvas Course - Track Assignments"

    def __init__(self):
        self.notion = []          # (method, what, payload)
        self.canvas_calls = []
        self.fail_assignments_for = None
        self.notion_429_once = False
        self.assignments = {
            "1": [[{"id": 11, "name": "Essay", "due_at": PAST, "html_url": "https://c/11", "points_possible": 20,
                    "description": "<p>Write 500 words.</p><p>Cite &amp; check.</p>"},
                   {"id": 12, "name": "Quiz 1", "due_at": PAST, "html_url": "https://c/12", "points_possible": 10}],
                  [{"id": 13, "name": "Reading log", "due_at": None, "html_url": "https://c/13", "points_possible": None}]],
            "2": [[{"id": 21, "name": "Lab report", "due_at": FUTURE, "html_url": "https://c/21", "points_possible": 30}]],
        }
        self.submissions = {11: {"workflow_state": "unsubmitted"}, 12: {"workflow_state": "graded", "score": 9, "submitted_at": PAST},
                            13: {"workflow_state": "unsubmitted"}, 21: {"workflow_state": "unsubmitted"}}

    def get(self, url, headers=None, params=None, timeout=None):
        self.canvas_calls.append(url)
        assert "notion.com" not in url
        if url.endswith("/api/v1/courses"):
            return Resp([{"id": 1, "course_code": "ECON", "name": "Economics"}, {"id": 2, "course_code": "", "name": "Physics"}])
        if "/submissions/self" in url:
            return Resp(self.submissions[int(url.split("/assignments/")[1].split("/")[0])])
        cid = url.split("/courses/")[1].split("/")[0]
        if cid == self.fail_assignments_for:
            return Resp({}, status=500)
        pages, page = self.assignments[cid], 1 + ("page=2" in url)
        links = {"next": {"url": url.split("?")[0] + "?page=2&per_page=100"}} if page < len(pages) else {}
        return Resp(pages[page - 1], links=links)

    def request(self, method, url, headers=None, timeout=None, json=None, **_):
        assert "notion.com" in url
        if method == "GET":      # children of the parent page, in two pages
            if "start_cursor" not in url:
                return Resp({"results": [{"type": "paragraph", "id": "p"},
                                         {"type": "child_database", "id": "other-db", "child_database": {"title": "My notes"}}],
                             "has_more": True, "next_cursor": "c2"})
            return Resp({"results": [{"type": "child_database", "id": "old-db", "child_database": {"title": self.OLD_TITLE}}], "has_more": False})
        if method == "PATCH":
            self.notion.append(("archive", url.rsplit("/", 1)[1], json)); return Resp({})
        if url.endswith("/databases"):
            self.notion.append(("create_db", json["title"][0]["text"]["content"], json)); return Resp({"id": "new-db"})
        if self.notion_429_once and url.endswith("/pages"):
            self.notion_429_once = False
            return Resp({}, status=429, headers={"Retry-After": "0"})
        self.notion.append(("page", json["parent"]["database_id"], json["properties"])); return Resp({})


class EndToEndTests(unittest.TestCase):
    ENV = dict(CANVAS_API_TOKEN="t", NOTION_API_KEY="k", NOTION_PARENT_PAGE_ID="parent",
               INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE="true\n")

    def run_main(self, world, **env_extra):
        m = load(**{**self.ENV, **env_extra})
        with mock.patch.object(m.requests, "get", world.get), mock.patch.object(m.requests, "request", world.request), \
             mock.patch.object(m.time, "sleep"):
            m.main()
        return m

    def test_full_run_follows_pagination_and_builds_the_database(self):
        w = FakeWorld(); self.run_main(w)
        kinds = [k for k, *_ in w.notion]
        self.assertEqual(kinds, ["archive", "create_db"] + ["page"] * 4)           # archive, create, then 4 rows
        self.assertEqual([x[1] for x in w.notion if x[0] == "archive"], ["old-db"])  # the matching title only, found on page 2
        pages = [x[2] for x in w.notion if x[0] == "page"]
        by_name = {p["Name"]["title"][0]["text"]["content"]: p for p in pages}
        self.assertEqual(set(by_name), {"Essay", "Quiz 1", "Reading log", "Lab report"})   # "Reading log" came from page 2 of Canvas
        status = {n: p["Status"]["select"]["name"] for n, p in by_name.items()}
        self.assertEqual(status, {"Essay": "Overdue", "Quiz 1": "Completed", "Reading log": "Not Started", "Lab report": "Not Started"})
        self.assertEqual(by_name["Essay"]["Description"]["rich_text"][0]["text"]["content"], "Write 500 words.\nCite & check.")
        self.assertEqual(by_name["Quiz 1"]["Score"]["number"], 9)
        self.assertEqual(by_name["Lab report"]["Class"]["rich_text"][0]["text"]["content"], "Physics")  # no course_code -> name

    def test_course_filter_and_undated_flag(self):
        w = FakeWorld(); self.run_main(w, CANVAS_COURSE_IDS="1", INCLUDE_ASSIGNMENTS_WITHOUT_DUE_DATE="false")
        names = [x[2]["Name"]["title"][0]["text"]["content"] for x in w.notion if x[0] == "page"]
        self.assertEqual(names, ["Essay", "Quiz 1"])

    def test_a_canvas_failure_leaves_notion_untouched(self):
        w = FakeWorld(); w.fail_assignments_for = "2"
        with self.assertRaises(Exception):
            self.run_main(w)
        self.assertEqual(w.notion, [])        # nothing archived, nothing created

    def test_a_notion_rate_limit_is_waited_out_and_retried(self):
        w = FakeWorld(); w.notion_429_once = True; self.run_main(w)
        self.assertEqual(len([x for x in w.notion if x[0] == "page"]), 4)   # every row still written exactly once


if __name__ == "__main__":
    unittest.main()
