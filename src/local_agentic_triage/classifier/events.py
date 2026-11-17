from datetime import date as _date, datetime as _datetime
from importlib.resources import files as _files
import json as _json
import re as _re

from local_agentic_triage.classifier.helpers import (
    CONTEXT_TOKENS as _CONTEXT_TOKENS,
    MAX_TOKENS as _MAX_TOKENS,
    chat_completion as _chat_completion,
    response_schema as _response_schema,
)
from local_agentic_triage.classifier.input import (
    ContextBoundMail as _ContextBoundMail,
    serialize_mail as _serialize_mail,
)

__all__ = ('extract_events', 'is_duplicate', 'complete_event')


_EVENT_FIELDS = {'title', 'start', 'end', 'date_note', 'location'}
_EVENT_TEMPLATE_OVERHEAD_BYTES = 1024
_DUPLICATE_SCHEMA = _response_schema(__package__, 'duplicate')
_COMPLETION_SCHEMA = _response_schema(__package__, 'completion')
_EXTRACTION_SCHEMA = _response_schema(__package__, 'extraction')


class _ModelRequestFailed(RuntimeError):
    pass


_ModelRequestFailed.__name__ = 'ModelRequestFailed'


def _prompt(name):
    return _files(__package__).joinpath(name + '.md').read_text(encoding='utf-8')


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
    system = system or _prompt('duplicate')
    schema = schema or _DUPLICATE_SCHEMA
    reserved = (len(system.encode('utf-8')) + len(_json.dumps(schema).encode('utf-8'))
                + _EVENT_TEMPLATE_OVERHEAD_BYTES + _MAX_TOKENS)
    budget = _CONTEXT_TOKENS - reserved
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
        {'role': 'system', 'content': _prompt('extraction')},
        {'role': 'user', 'content': _serialize_mail(context_bound_mail)},
    ], _EXTRACTION_SCHEMA)
    if not isinstance(result, dict) or set(result) != {'events'}:
        raise ValueError('event_extraction_keys')
    _validate_events(result['events'])
    return result['events']


def is_duplicate(event, nearby) -> bool:
    _validate_events([event])
    outcome = _duplicate_result(_model_batch(
        _prompt('duplicate'), _DUPLICATE_SCHEMA,
        {'event': event, 'nearby': nearby},
    ))
    return outcome in ('existing', 'possible_duplicate')


def complete_event(event):
    _validate_events([event])
    result = _model_batch(
        _prompt('completion'), _COMPLETION_SCHEMA, {'event': event})
    return _completion_result(result, event)
