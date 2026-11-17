from local_agentic_triage.classifier import resources
from local_agentic_triage.classifier import helpers
from local_agentic_triage.classifier.input import (
    ContextBoundMail,
    serialize_mail,
)

__all__ = ('extract_events', 'is_duplicate', 'complete_event')


_EVENT_FIELDS = {'title', 'start', 'end', 'date_note', 'location'}


def _validate_events(events):
    """Extraction validation is structural; semantic checks follow completion."""
    if not isinstance(events, list):
        raise ValueError('event_structure')
    for event in events:
        if not isinstance(event, dict) or set(event) != _EVENT_FIELDS:
            raise ValueError('event_structure')
        if any(value is not None and not isinstance(value, str)
               for value in event.values()):
            raise ValueError('event_structure')


def _duplicate_result(result):
    if (not isinstance(result, dict) or set(result) != {'outcome'} or
            result['outcome'] not in ('existing', 'possible_duplicate', 'create')):
        raise ValueError('event_outcome_invalid')
    return result['outcome']


def _completion_result(result, event):
    _validate_events([result])
    if result['start'] != event['start']:
        raise ValueError('event_completion_unauthorized')
    for key in ('title', 'end', 'location'):
        if event[key] is not None and result[key] != event[key]:
            raise ValueError('event_completion_unauthorized')
    if event['location'] is None and result['location'] is not None:
        raise ValueError('event_completion_unauthorized')
    if event['date_note'] is not None and result['date_note'] not in (None, event['date_note']):
        raise ValueError('event_completion_unauthorized')
    return result


def extract_events(context_bound_mail: ContextBoundMail):
    result = helpers.chat_completion([
        {'role': 'system', 'content': resources.extract_events_prompt()},
        {'role': 'user', 'content': serialize_mail(context_bound_mail)},
    ], resources.EXTRACT_EVENTS_OUTPUT_SCHEMA)
    if not isinstance(result, dict) or set(result) != {'events'}:
        raise ValueError('event_extraction_keys')
    _validate_events(result['events'])
    return result['events']


def is_duplicate(event, nearby) -> bool:
    _validate_events([event])
    outcome = _duplicate_result(helpers.chat_completion_with_payload(
        resources.is_duplicate_prompt(), resources.IS_DUPLICATE_OUTPUT_SCHEMA,
        {'event': event, 'nearby': nearby}, 'event',
    ))
    return outcome in ('existing', 'possible_duplicate')


def complete_event(event):
    _validate_events([event])
    result = helpers.chat_completion_with_payload(
        resources.complete_event_prompt(), resources.COMPLETE_EVENT_OUTPUT_SCHEMA,
        {'event': event}, 'event')
    return _completion_result(result, event)
