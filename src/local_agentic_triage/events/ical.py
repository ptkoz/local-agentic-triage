"""RFC 5545 export of one validated event for manual Calendar import."""
from datetime import date, datetime, timezone
import hashlib
import json
import re

from local_agentic_triage.events.calendar import InvalidCalendarEvent, canonical

__all__ = ('ical_event',)

PRODID = '-//local-agentic-triage//iCalendar export//EN'
UID_DOMAIN = 'local-agentic-triage'


def _text(value):
    value = value.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')
    value = re.sub(r'\r\n|\r|\n', r'\\n', value)
    # TEXT permits no control characters other than HTAB.
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', ' ', value)


def _utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc).replace(microsecond=0)


def _fold(line):
    data = line.encode('utf-8')
    parts = []
    limit = 75
    while len(data) > limit:
        cut = limit
        while data[cut] & 0xC0 == 0x80:
            cut -= 1
        parts.append(data[:cut])
        data = data[cut:]
        limit = 74
    parts.append(data)
    return b'\r\n '.join(parts).decode('utf-8')


def ical_event(event, *, stamp, occurrence):
    """Return one VCALENDAR document; the UID depends only on the candidate's occurrence
    index within its mail and the exported event data."""
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        raise ValueError('ical_stamp')
    if type(occurrence) is not int or occurrence < 0:
        raise ValueError('ical_occurrence')
    expected = canonical(event)
    if expected['all_day']:
        start = date.fromisoformat(expected['start']).strftime('%Y%m%d')
        end = date.fromisoformat(expected['end']).strftime('%Y%m%d')
        dates = [f'DTSTART;VALUE=DATE:{start}', f'DTEND;VALUE=DATE:{end}']
    else:
        start_at = _utc(expected['start']); end_at = _utc(expected['end'])
        if end_at <= start_at:
            raise InvalidCalendarEvent('event_order', 'invalid_date')
        start = start_at.strftime('%Y%m%dT%H%M%SZ')
        end = end_at.strftime('%Y%m%dT%H%M%SZ')
        dates = [f'DTSTART:{start}', f'DTEND:{end}']
    # The occurrence keeps identical candidates from one mail apart on import.
    identity = json.dumps([occurrence, expected['title'], start, end, expected['all_day'], expected['location']],
                          ensure_ascii=False, separators=(',', ':'))
    uid = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32] + '@' + UID_DOMAIN
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', f'PRODID:{PRODID}', 'BEGIN:VEVENT',
             f'UID:{uid}', 'DTSTAMP:' + stamp.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
             *dates, 'SUMMARY:' + _text(expected['title'])]
    if expected['location'] is not None:
        lines.append('LOCATION:' + _text(expected['location']))
    lines += ['END:VEVENT', 'END:VCALENDAR']
    return ''.join(_fold(line) + '\r\n' for line in lines)
