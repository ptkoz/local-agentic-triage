import json
from typing import Literal

from local_agentic_triage.classifier import helpers
from local_agentic_triage.mail.model import PreparedMail


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


def summarize(prepared_mail: PreparedMail) -> str:
    return validate_summary(helpers.chat_completion([
        {'role':'system','content':helpers.summary_prompt()},
        {'role':'user','content':prepared_mail.source_json},
    ],helpers.SUMMARY_SCHEMA))['summary']


def classify(prepared_mail: PreparedMail,summary: str) -> Literal['archive','leave']:
    return validate_destination(helpers.chat_completion([
        {'role':'system','content':helpers.disposition_prompt()},
        {'role':'user','content':json.dumps({'summary':summary,'mail':prepared_mail.source},ensure_ascii=False)},
    ],helpers.CLASSIFICATION_SCHEMA))['destination']
