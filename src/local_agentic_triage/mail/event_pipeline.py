from local_agentic_triage.classifier.events import (
    complete_event,
    is_duplicate,
)
from local_agentic_triage.events.calendar import (
    Calendar,
    InvalidCalendarEvent,
    canonical,
    lookup_reason,
)


def process_events(events, config, *, clock=None):
    if not isinstance(events, list):
        raise ValueError('event_structure')
    calendar = Calendar(config, clock=clock)
    results = []
    for index, event in enumerate(events):
        reason = lookup_reason(event)
        if reason is not None:
            results.append({'index': index, 'outcome': 'skipped', 'reason': reason})
            continue

        nearby = calendar.nearby(event)
        if is_duplicate(event, nearby):
            results.append({'index': index, 'outcome': 'duplicate', 'reason': None})
            continue

        completed = complete_event(event)
        try:
            canonical(completed)
        except InvalidCalendarEvent as error:
            results.append({'index': index, 'outcome': 'skipped', 'reason': error.reason})
            continue
        if calendar.too_soon(completed):
            results.append({'index': index, 'outcome': 'skipped', 'reason': 'too_soon'})
            continue
        calendar.create(completed)
        results.append({'index': index, 'outcome': 'created', 'reason': None})
    return {'status': 'ok', 'results': results}
