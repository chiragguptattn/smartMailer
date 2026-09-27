"""Unit tests for calendar digest date handling, conflicts, and HTML."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from calendar_digest import (
    CalendarEvent,
    ZohoCalendarClient,
    build_digest_html,
    calendar_digest_recipients,
    conflict_groups,
    format_digest_date,
    parse_digest_date,
    parse_zoho_event,
)


TZ = ZoneInfo("Asia/Kolkata")


def event(title: str, start: str, end: str, source: str = "Gmail") -> CalendarEvent:
    return CalendarEvent(
        source=source,
        calendar_email=f"{source.lower()}@example.com",
        title=title,
        start=datetime.fromisoformat(start).replace(tzinfo=TZ),
        end=datetime.fromisoformat(end).replace(tzinfo=TZ),
    )


class CalendarDigestDateTests(unittest.TestCase):
    def test_parse_dd_mm_yyyy(self) -> None:
        self.assertEqual(parse_digest_date("25-08-2026"), date(2026, 8, 25))

    def test_format_dd_mm_yyyy(self) -> None:
        self.assertEqual(format_digest_date(date(2026, 8, 25)), "25-08-2026")

    def test_rejects_other_date_format(self) -> None:
        with self.assertRaises(ValueError):
            parse_digest_date("2026-08-25")


class CalendarDigestConflictTests(unittest.TestCase):
    def test_detects_overlapping_events(self) -> None:
        events = [
            event("A", "2026-08-25T09:00:00", "2026-08-25T10:00:00"),
            event("B", "2026-08-25T09:30:00", "2026-08-25T10:30:00", "Zoho"),
            event("C", "2026-08-25T11:00:00", "2026-08-25T11:30:00"),
        ]

        self.assertEqual(conflict_groups(events), {0: 1, 1: 1})

    def test_html_marks_conflicts(self) -> None:
        events = [
            event("Planning", "2026-08-25T09:00:00", "2026-08-25T10:00:00"),
            event("Review", "2026-08-25T09:30:00", "2026-08-25T10:30:00", "Zoho"),
        ]

        html = build_digest_html(events, date(2026, 8, 25), TZ)

        self.assertIn("Conflict 1", html)
        self.assertIn("#fff3cd", html)


class CalendarDigestRecipientTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._old_env)

    def test_recipients_from_override(self) -> None:
        self.assertEqual(
            calendar_digest_recipients("a@example.com, b@example.com"),
            ["a@example.com", "b@example.com"],
        )

    def test_recipients_from_calendar_emails(self) -> None:
        os.environ.pop("CALENDAR_DIGEST_RECIPIENTS", None)
        os.environ["GOOGLE_CALENDAR_EMAIL"] = "g@example.com"
        os.environ["SECONDARY_CALENDAR_EMAIL"] = "z@example.com"

        self.assertEqual(
            calendar_digest_recipients(),
            ["g@example.com", "z@example.com"],
        )


class CalendarDigestZohoTests(unittest.TestCase):
    def test_parse_timed_zoho_event(self) -> None:
        raw = {
            "title": "Client Review",
            "dateandtime": {
                "timezone": "Asia/Kolkata",
                "start": "20260825T090000+0530",
                "end": "20260825T100000+0530",
            },
            "location": "Meet",
            "organizer": "z@example.com",
            "isallday": False,
        }

        parsed = parse_zoho_event(raw, TZ)

        self.assertEqual(parsed.title, "Client Review")
        self.assertEqual(parsed.source, "Zoho")
        self.assertEqual(parsed.location, "Meet")
        self.assertFalse(parsed.is_all_day)

    def test_zoho_no_events_message_is_empty_day(self) -> None:
        client = ZohoCalendarClient(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
            access_token="access-token",
            expires_at=9999999999,
            api_domain="https://www.zohoapis.in",
            calendar_uid="primary",
        )
        client._request_json = lambda url, *, auth_prefix: {"events": [{"message": "No events found."}]}  # type: ignore[method-assign]

        events = client.list_events(date(2026, 8, 28), TZ)

        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
