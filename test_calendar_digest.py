"""Unit tests for calendar digest date handling, conflicts, and HTML."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from calendar_digest import (
    CalendarAiInsights,
    CalendarConflictInsight,
    CalendarEvent,
    CalendarEventInsight,
    ZohoCalendarClient,
    build_digest_html,
    calendar_digest_recipients,
    conflict_groups,
    format_digest_date,
    parse_calendar_ai_insights,
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

    def test_html_includes_ai_brief_priority_prep_and_conflict_advice(self) -> None:
        events = [
            event("Planning", "2026-08-25T09:00:00", "2026-08-25T10:00:00"),
            event("Client Review", "2026-08-25T09:30:00", "2026-08-25T10:30:00", "Zoho"),
        ]
        insights = CalendarAiInsights(
            daily_brief="You have one overlap in the morning.",
            today_focus=("Protect the client review slot.",),
            events=(
                CalendarEventInsight(
                    0,
                    "Medium",
                    "Planning covers the morning work.",
                    "Review the planning agenda.",
                ),
                CalendarEventInsight(
                    1,
                    "High",
                    "Client review overlaps with planning.",
                    "Keep client notes ready.",
                ),
            ),
            conflicts=(
                CalendarConflictInsight(
                    1,
                    "The client review overlaps with planning.",
                    "Prioritize the client review and ask for planning notes.",
                ),
            ),
        )

        html = build_digest_html(events, date(2026, 8, 25), TZ, ai_insights=insights)

        self.assertIn("AI daily brief", html)
        self.assertIn("You have one overlap in the morning.", html)
        self.assertIn("Today's focus", html)
        self.assertIn("Protect the client review slot.", html)
        self.assertIn("High", html)
        self.assertIn("Client review overlaps with planning.", html)
        self.assertIn("Keep client notes ready.", html)
        self.assertIn("Prioritize the client review", html)
        self.assertIn("09:30 AM - 10:00 AM", html)

    def test_parse_calendar_ai_insights_ignores_unknown_events_and_conflicts(self) -> None:
        parsed = parse_calendar_ai_insights(
            {
                "daily_brief": "Busy morning.",
                "today_focus": ["Protect the overlap window."],
                "events": [
                    {
                        "event_index": 0,
                        "priority": "high",
                        "context_note": "Client-facing review.",
                        "preparation_note": "Read notes.",
                    },
                    {"event_index": 9, "priority": "high", "preparation_note": "Ignore me."},
                ],
                "conflicts": [
                    {"group": 1, "explanation": "Overlap.", "recommendation": "Reschedule."},
                    {"group": 3, "explanation": "Invalid.", "recommendation": "Ignore."},
                ],
            },
            event_count=2,
            valid_conflict_groups={1},
        )

        self.assertEqual(parsed.daily_brief, "Busy morning.")
        self.assertEqual(parsed.today_focus, ("Protect the overlap window.",))
        self.assertEqual(len(parsed.events), 1)
        self.assertEqual(parsed.events[0].priority, "High")
        self.assertEqual(parsed.events[0].context_note, "Client-facing review.")
        self.assertEqual(len(parsed.conflicts), 1)
        self.assertEqual(parsed.conflicts[0].group, 1)

    def test_html_fills_default_focus_and_event_details_when_ai_is_thin(self) -> None:
        events = [
            CalendarEvent(
                source="Gmail",
                calendar_email="gmail@example.com",
                title="KT Session",
                start=datetime.fromisoformat("2026-08-25T09:00:00").replace(tzinfo=TZ),
                end=datetime.fromisoformat("2026-08-25T10:00:00").replace(tzinfo=TZ),
                description="Cover deployment checklist and ownership handoff.",
                organizer="lead@example.com",
                attendees=("madhav@example.com", "lead@example.com"),
            ),
            event("Client Review", "2026-08-25T09:30:00", "2026-08-25T10:30:00", "Zoho"),
        ]

        html = build_digest_html(
            events,
            date(2026, 8, 25),
            TZ,
            ai_insights=CalendarAiInsights(),
        )

        self.assertIn("Schedule focus", html)
        self.assertIn("Resolve Conflict 1 around 09:30 AM - 10:00 AM", html)
        self.assertIn("High", html)
        self.assertIn("Cover deployment checklist and ownership handoff.", html)
        self.assertIn(
            "Review the event details, then confirm which overlapping meeting should take priority.",
            html,
        )
        self.assertIn("Attendees: 2", html)


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
            "description": "<p>Review project status and open risks.</p>",
            "attendees": [{"email": "client@example.com"}, {"name": "Madhav"}],
            "isallday": False,
        }

        parsed = parse_zoho_event(raw, TZ)

        self.assertEqual(parsed.title, "Client Review")
        self.assertEqual(parsed.source, "Zoho")
        self.assertEqual(parsed.location, "Meet")
        self.assertEqual(parsed.description, "Review project status and open risks.")
        self.assertEqual(parsed.attendees, ("client@example.com", "Madhav"))
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
