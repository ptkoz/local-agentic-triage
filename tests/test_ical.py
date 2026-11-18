import re
import unittest
from datetime import datetime, timezone

from local_agentic_triage.events.calendar import InvalidCalendarEvent
from local_agentic_triage.events.ical import ical_event

STAMP = datetime(2099, 1, 1, 12, 30, 45, 123456, tzinfo=timezone.utc)
TIMED = {'title': 'Synthetic review', 'start': '2099-12-01T10:00:00+01:00',
         'end': '2099-12-01T11:30:00+01:00', 'date_note': None, 'location': None}
ALL_DAY = {'title': 'Synthetic trip', 'start': '2099-12-01', 'end': '2099-12-03',
           'date_note': None, 'location': None}


def lines(text):
    return text.split('\r\n')


def unfold(text):
    return text.replace('\r\n ', '')


def uid(text):
    return re.search(r'^UID:(.*)$', unfold(text), re.MULTILINE).group(1).rstrip('\r')


class IcalTests(unittest.TestCase):
    def test_timed_event_is_a_utc_crlf_calendar(self):
        text = ical_event(TIMED | {'location': 'Synthetic Hall'}, stamp=STAMP, occurrence=0)
        self.assertTrue(text.endswith('\r\n'))
        self.assertNotIn('\n', text.replace('\r\n', ''))
        self.assertEqual(lines(text), [
            'BEGIN:VCALENDAR',
            'VERSION:2.0',
            'PRODID:-//local-agentic-triage//iCalendar export//EN',
            'BEGIN:VEVENT',
            'UID:' + uid(text),
            'DTSTAMP:20990101T123045Z',
            'DTSTART:20991201T090000Z',
            'DTEND:20991201T103000Z',
            'SUMMARY:Synthetic review',
            'LOCATION:Synthetic Hall',
            'END:VEVENT',
            'END:VCALENDAR',
            '',
        ])
        self.assertRegex(uid(text), r'^[0-9a-f]{32}@local-agentic-triage$')

    def test_all_day_event_uses_date_values_with_exclusive_end(self):
        text = ical_event(ALL_DAY, stamp=STAMP, occurrence=0)
        self.assertEqual(lines(text)[5:9], [
            'DTSTAMP:20990101T123045Z',
            'DTSTART;VALUE=DATE:20991201',
            'DTEND;VALUE=DATE:20991203',
            'SUMMARY:Synthetic trip',
        ])
        self.assertNotIn('LOCATION', text)

    def test_text_values_are_escaped(self):
        event = TIMED | {'title': 'A\\B; C, D\r\nE\nF\rG\x00H\tI',
                         'location': 'Room 1, Floor; 2'}
        text = ical_event(event, stamp=STAMP, occurrence=0)
        self.assertIn('SUMMARY:A\\\\B\\; C\\, D\\nE\\nF\\nG H\tI\r\n', text)
        self.assertIn('LOCATION:Room 1\\, Floor\\; 2\r\n', text)

    def test_long_lines_fold_at_75_octets_without_splitting_utf8(self):
        title = 'Zażółć gęślą jaźń ' * 11
        text = ical_event(TIMED | {'title': title[:200]}, stamp=STAMP, occurrence=0)
        for line in lines(text):
            self.assertLessEqual(len(line.encode('utf-8')), 75)
        self.assertIn('SUMMARY:' + title[:200] + '\r\n', unfold(text))
        self.assertTrue(any(line.startswith(' ') for line in lines(text)))

    def test_uid_is_stable_for_equivalent_event_data(self):
        first = ical_event(TIMED, stamp=STAMP, occurrence=0)
        later = ical_event(TIMED | {'date_note': 'Synthetic note'},
                           stamp=datetime(2099, 6, 1, tzinfo=timezone.utc), occurrence=0)
        utc = ical_event(TIMED | {'start': '2099-12-01T09:00:00Z', 'end': '2099-12-01T10:30:00Z'},
                         stamp=STAMP, occurrence=0)
        self.assertEqual(uid(first), uid(later))
        self.assertEqual(uid(first), uid(utc))
        self.assertEqual(ical_event(TIMED, stamp=STAMP, occurrence=0), first)
        for change in ({'title': 'Other'}, {'location': 'Synthetic Hall'},
                       {'end': '2099-12-01T12:00:00+01:00'}, {'start': '2099-12-01T09:30:00+01:00'}):
            with self.subTest(change=change):
                self.assertNotEqual(uid(ical_event(TIMED | change, stamp=STAMP, occurrence=0)), uid(first))
        self.assertNotEqual(uid(ical_event(ALL_DAY, stamp=STAMP, occurrence=0)),
                            uid(ical_event(ALL_DAY | {'end': '2099-12-02'}, stamp=STAMP, occurrence=0)))

    def test_fractional_seconds_are_dropped_and_interval_revalidated(self):
        text = ical_event(TIMED | {'start': '2099-12-01T10:00:00.9+01:00'}, stamp=STAMP, occurrence=0)
        self.assertIn('DTSTART:20991201T090000Z\r\n', text)
        with self.assertRaises(InvalidCalendarEvent) as caught:
            ical_event(TIMED | {'start': '2099-12-01T10:00:00.1+01:00',
                                'end': '2099-12-01T10:00:00.5+01:00'}, stamp=STAMP, occurrence=0)
        self.assertEqual(caught.exception.reason, 'invalid_date')

    def test_invalid_events_are_rejected_by_calendar_validation(self):
        for change, reason in (({'title': ' '}, 'invalid_title'), ({'end': None}, 'missing_end'),
                               ({'end': '2099-12-01'}, 'invalid_date'), ({'location': ''}, 'invalid_location')):
            with self.subTest(change=change), self.assertRaises(InvalidCalendarEvent) as caught:
                ical_event(TIMED | change, stamp=STAMP, occurrence=0)
            self.assertEqual(caught.exception.reason, reason)
        with self.assertRaises(ValueError):
            ical_event(TIMED | {'extra': None}, stamp=STAMP, occurrence=0)

    def test_stamp_must_be_aware(self):
        with self.assertRaises(ValueError):
            ical_event(TIMED, stamp=datetime(2099, 1, 1), occurrence=0)

    def test_uid_distinguishes_occurrences_and_is_stable_per_occurrence(self):
        first = ical_event(TIMED, stamp=STAMP, occurrence=0)
        second = ical_event(TIMED, stamp=STAMP, occurrence=1)
        self.assertNotEqual(uid(first), uid(second))
        self.assertEqual(first.replace(uid(first), ''), second.replace(uid(second), ''))
        retry = ical_event(TIMED, stamp=datetime(2099, 6, 1, tzinfo=timezone.utc), occurrence=1)
        self.assertEqual(uid(retry), uid(second))
        self.assertRegex(uid(second), r'^[0-9a-f]{32}@local-agentic-triage$')

    def test_occurrence_must_be_a_non_negative_integer(self):
        for occurrence in (-1, True, 1.0, '1', None):
            with self.subTest(occurrence=occurrence), self.assertRaises(ValueError):
                ical_event(TIMED, stamp=STAMP, occurrence=occurrence)


if __name__ == '__main__':
    unittest.main()
