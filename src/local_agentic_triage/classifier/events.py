from datetime import date as _date, datetime as _datetime
import json as _json
import re as _re

from local_agentic_triage.classifier import resources as _resources
from local_agentic_triage.classifier.helpers import (
    LOCAL_MODEL_CONTEXT_TOKEN_LIMIT as _LOCAL_MODEL_CONTEXT_TOKEN_LIMIT,
    LOCAL_MODEL_OUTPUT_TOKEN_LIMIT as _LOCAL_MODEL_OUTPUT_TOKEN_LIMIT,
    chat_completion as _chat_completion,
)
from local_agentic_triage.classifier.input import (
    ContextBoundMail as _ContextBoundMail,
    serialize_mail as _serialize_mail,
)

__all__ = ('extract_events', 'is_duplicate', 'complete_event')


_EVENT_FIELDS = {'title', 'start', 'end', 'date_note', 'location'}
_EVENT_TEMPLATE_OVERHEAD_BYTES = 1024


class _ModelRequestFailed(RuntimeError):
    pass


_ModelRequestFailed.__name__ = 'ModelRequestFailed'


def _timestamp(value):
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError('date type')
    if _re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        _date.fromisoformat(value)
        return
    if not _re.fullmatch(
            r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?(?:Z|[+-][0-9]{2}:[0-9]{2})',
            value):
        raise ValueError('date format')
    parsed = _datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('date timezone')


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


def _event_user_budget(system=None, schema=None):
    system = system or _resources.is_duplicate_prompt()
    schema = schema or _resources.IS_DUPLICATE_OUTPUT_SCHEMA
    reserved = (len(system.encode('utf-8')) + len(_json.dumps(schema).encode('utf-8'))
                + _EVENT_TEMPLATE_OVERHEAD_BYTES + _LOCAL_MODEL_OUTPUT_TOKEN_LIMIT)
    budget = _LOCAL_MODEL_CONTEXT_TOKEN_LIMIT - reserved
    if budget < 1:
        raise RuntimeError('event_payload_budget')
    return budget


def _model_batch(system, schema, payload):
    user = _json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    if len(user.encode('utf-8')) > _event_user_budget(system, schema):
        raise ValueError('event_payload_size')
    try:
        return _chat_completion([
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ], schema)
    except OSError:
        raise _ModelRequestFailed() from None


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


def extract_events(context_bound_mail: _ContextBoundMail):
    result = _chat_completion([
        {'role': 'system', 'content': _resources.extract_events_prompt()},
        {'role': 'user', 'content': _serialize_mail(context_bound_mail)},
    ], _resources.EXTRACT_EVENTS_OUTPUT_SCHEMA)
    if not isinstance(result, dict) or set(result) != {'events'}:
        raise ValueError('event_extraction_keys')
    _validate_events(result['events'])
    return result['events']


def is_duplicate(event, nearby) -> bool:
    _validate_events([event])
    outcome = _duplicate_result(_model_batch(
        _resources.is_duplicate_prompt(), _resources.IS_DUPLICATE_OUTPUT_SCHEMA,
        {'event': event, 'nearby': nearby},
    ))
    return outcome in ('existing', 'possible_duplicate')


def complete_event(event):
    _validate_events([event])
    result = _model_batch(
        _resources.complete_event_prompt(), _resources.COMPLETE_EVENT_OUTPUT_SCHEMA,
        {'event': event})
    return _completion_result(result, event)
