from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from local_agentic_triage.classifier.events import (
    complete_event,
    is_duplicate,
)
from local_agentic_triage.common.attachment import Attachment
from local_agentic_triage.events.calendar import (
    Calendar,
    InvalidCalendarEvent,
    canonical,
    lookup_reason,
)
from local_agentic_triage.events.ical import ical_event


@dataclass(frozen=True)
class EventResult:
    index: int
    outcome: Literal['prepared', 'duplicate', 'skipped']
    reason: str | None
    attachment: Attachment | None


def process_events(events, config, *, clock=None) -> Iterator[EventResult]:
    """Yield one result per candidate; a prepared result carries its iCalendar attachment.

    Results are yielded as each candidate finishes, so a later candidate failure cannot
    discard results the caller has already received.
    """
    if not isinstance(events, list):
        raise ValueError('event_structure')
    clock = clock or datetime.now
    calendar = Calendar(config, clock=clock)
    prepared = 0
    for index, event in enumerate(events):
        reason = lookup_reason(event)
        if reason is not None:
            yield EventResult(index, 'skipped', reason, None)
            continue

        nearby = calendar.nearby(event)
        if is_duplicate(event, nearby):
            yield EventResult(index, 'duplicate', None, None)
            continue

        completed = complete_event(event)
        now = clock(timezone.utc)
        try:
            canonical(completed)
            if calendar.too_soon(completed, now=now):
                yield EventResult(index, 'skipped', 'too_soon', None)
                continue
            content = ical_event(completed, stamp=now, occurrence=index)
        except InvalidCalendarEvent as error:
            yield EventResult(index, 'skipped', error.reason, None)
            continue
        prepared += 1
        yield EventResult(index, 'prepared', None, Attachment(f'event-{prepared}.ics', content))
