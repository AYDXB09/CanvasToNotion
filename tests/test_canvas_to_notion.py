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


if __name__ == "__main__":
    unittest.main()
