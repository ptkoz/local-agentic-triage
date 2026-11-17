from importlib.resources import files
import json

__all__ = (
    'SUMMARIZE_OUTPUT_SCHEMA',
    'CLASSIFY_OUTPUT_SCHEMA',
    'EXTRACT_EVENTS_OUTPUT_SCHEMA',
    'IS_DUPLICATE_OUTPUT_SCHEMA',
    'COMPLETE_EVENT_OUTPUT_SCHEMA',
    'summarize_prompt',
    'classify_prompt',
    'extract_events_prompt',
    'is_duplicate_prompt',
    'complete_event_prompt',
)


def _output_schema(name):
    return json.loads(files(__package__).joinpath(name).read_text(encoding='utf-8'))


SUMMARIZE_OUTPUT_SCHEMA = _output_schema('summarize_output_schema.json')
CLASSIFY_OUTPUT_SCHEMA = _output_schema('classify_output_schema.json')
EXTRACT_EVENTS_OUTPUT_SCHEMA = _output_schema('extract_events_output_schema.json')
IS_DUPLICATE_OUTPUT_SCHEMA = _output_schema('is_duplicate_output_schema.json')
COMPLETE_EVENT_OUTPUT_SCHEMA = _output_schema('complete_event_output_schema.json')


def summarize_prompt():
    return files(__package__).joinpath('summarize_prompt.md').read_text(encoding='utf-8')


def classify_prompt():
    return files(__package__).joinpath('classify_prompt.md').read_text(encoding='utf-8')


def extract_events_prompt():
    return files(__package__).joinpath('extract_events_prompt.md').read_text(encoding='utf-8')


def is_duplicate_prompt():
    return files(__package__).joinpath('is_duplicate_prompt.md').read_text(encoding='utf-8')


def complete_event_prompt():
    return files(__package__).joinpath('complete_event_prompt.md').read_text(encoding='utf-8')
