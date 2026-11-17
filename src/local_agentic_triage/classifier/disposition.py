from typing import Literal

from local_agentic_triage.classifier import transport
from local_agentic_triage.classifier import resources
from local_agentic_triage.classifier.input import (ContextBoundMail,
    serialize_destination_input, serialize_mail)

__all__ = ('summarize', 'classify')


def _validate_destination(obj):
    if not isinstance(obj,dict) or set(obj)!={'destination'}:
        raise ValueError('classification keys')
    if obj['destination'] not in ('leave','archive'):
        raise ValueError('destination')
    return obj


def _validate_summary(obj):
    if not isinstance(obj,dict) or set(obj)!={'summary'}:
        raise ValueError('classification keys')
    if not isinstance(obj['summary'],str) or not obj['summary']:
        raise ValueError('summary')
    return obj


def summarize(context_bound_mail: ContextBoundMail) -> str:
    return _validate_summary(transport.chat_completion([
        {'role':'system','content':resources.summarize_prompt()},
        {'role':'user','content':serialize_mail(context_bound_mail)},
    ],resources.SUMMARIZE_OUTPUT_SCHEMA))['summary']


def classify(context_bound_mail: ContextBoundMail,summary: str) -> Literal['archive','leave']:
    return _validate_destination(transport.chat_completion([
        {'role':'system','content':resources.classify_prompt()},
        {'role':'user','content':serialize_destination_input(context_bound_mail,summary)},
    ],resources.CLASSIFY_OUTPUT_SCHEMA))['destination']
