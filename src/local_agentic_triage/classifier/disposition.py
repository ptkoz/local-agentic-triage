from typing import Literal

from local_agentic_triage.classifier import helpers
from local_agentic_triage.classifier import resources as _resources
from local_agentic_triage.classifier.input import (ContextBoundMail,
    serialize_destination_input, serialize_mail)


def validate_destination(obj):
    if not isinstance(obj,dict) or set(obj)!={'destination'}:
        raise ValueError('classification keys')
    if obj['destination'] not in ('leave','archive'):
        raise ValueError('destination')
    return obj


def validate_summary(obj):
    if not isinstance(obj,dict) or set(obj)!={'summary'}:
        raise ValueError('classification keys')
    if not isinstance(obj['summary'],str) or not obj['summary']:
        raise ValueError('summary')
    return obj


def summarize(context_bound_mail: ContextBoundMail) -> str:
    return validate_summary(helpers.chat_completion([
        {'role':'system','content':_resources.summarize_prompt()},
        {'role':'user','content':serialize_mail(context_bound_mail)},
    ],_resources.SUMMARIZE_OUTPUT_SCHEMA))['summary']


def classify(context_bound_mail: ContextBoundMail,summary: str) -> Literal['archive','leave']:
    return validate_destination(helpers.chat_completion([
        {'role':'system','content':_resources.classify_prompt()},
        {'role':'user','content':serialize_destination_input(context_bound_mail,summary)},
    ],_resources.CLASSIFY_OUTPUT_SCHEMA))['destination']
