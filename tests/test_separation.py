import ast
import inspect
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'src/local_agentic_triage'
EVENT = {
    'title': 'Synthetic',
    'start': '2099-01-02T10:00:00Z',
    'end': '2099-01-06T10:00:00Z',
    'date_note': None,
    'location': None,
}


class SeparationTests(unittest.TestCase):
    def test_model_resources_have_one_explicit_public_owner(self):
        from local_agentic_triage.classifier import disposition, events, resources, transport
        expected = {
            'SUMMARIZE_OUTPUT_SCHEMA', 'CLASSIFY_OUTPUT_SCHEMA',
            'EXTRACT_EVENTS_OUTPUT_SCHEMA', 'IS_DUPLICATE_OUTPUT_SCHEMA',
            'COMPLETE_EVENT_OUTPUT_SCHEMA', 'summarize_prompt', 'classify_prompt',
            'extract_events_prompt', 'is_duplicate_prompt', 'complete_event_prompt',
        }
        self.assertEqual(set(resources.__all__), expected)
        for module in (transport, disposition, events):
            self.assertFalse(any(name.endswith('OUTPUT_SCHEMA') for name in vars(module)))
        for name in expected:
            self.assertFalse(hasattr(transport, name))

    def test_disposition_has_explicit_public_surface(self):
        from local_agentic_triage.classifier import disposition
        self.assertEqual(disposition.__all__, ('summarize', 'classify'))

    def test_event_model_api_maps_both_duplicate_outcomes_to_bool(self):
        from local_agentic_triage.classifier import events
        public_functions = {
            name for name, value in inspect.getmembers(events, inspect.isfunction)
            if value.__module__ == events.__name__ and not name.startswith('_')
        }
        self.assertEqual(public_functions, {'extract_event_candidates', 'is_duplicate', 'complete_event'})
        self.assertEqual(events.__all__, ('extract_event_candidates', 'is_duplicate', 'complete_event'))
        self.assertFalse(hasattr(events, 'extract_events'))
        tree = ast.parse(Path(events.__file__).read_text(encoding='utf-8'))
        imports = [ast.unparse(node) for node in ast.walk(tree)
                   if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any('local_agentic_triage.events' in item or
                             'local_agentic_triage.mail' in item for item in imports))
        with patch.object(events.transport, 'chat_completion_with_payload', side_effect=[
                {'outcome': 'existing'}, {'outcome': 'possible_duplicate'},
                {'outcome': 'create'}]):
            self.assertIs(events.is_duplicate(EVENT, []), True)
            self.assertIs(events.is_duplicate(EVENT, []), True)
            self.assertIs(events.is_duplicate(EVENT, []), False)


    def test_calendar_package_has_no_classifier_imports_or_model_resources(self):
        for source in (PACKAGE / 'events').glob('*.py'):
            tree = ast.parse(source.read_text(encoding='utf-8'))
            imports = [node for node in ast.walk(tree)
                       if isinstance(node, (ast.Import, ast.ImportFrom))]
            self.assertFalse(any('classifier' in ast.unparse(node) for node in imports), source)
        self.assertFalse(any((PACKAGE / 'events').glob('*.md')))
        self.assertFalse(any((PACKAGE / 'events').glob('*.json')))

    def test_calendar_independently_validates_lookup_input(self):
        from local_agentic_triage.events.calendar import Calendar
        from tests.helpers import make_config
        with self.assertRaisesRegex(ValueError, 'event_structure'):
            Calendar(make_config()).nearby({'start': EVENT['start']})

    def test_mail_event_pipeline_imports_only_public_domain_apis(self):
        source = PACKAGE / 'mail/event_pipeline.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        private_imports = [alias.name for node in ast.walk(tree)
                           if isinstance(node, ast.ImportFrom)
                           for alias in node.names if alias.name.startswith('_')]
        self.assertEqual(private_imports, [])

    def test_reporting_delivers_attachments_without_domain_imports(self):
        tree = ast.parse((PACKAGE / 'reporting/report.py').read_text(encoding='utf-8'))
        imports = [ast.unparse(node) for node in ast.walk(tree)
                   if isinstance(node, (ast.Import, ast.ImportFrom))]
        for domain in ('classifier', 'local_agentic_triage.events', 'local_agentic_triage.mail'):
            self.assertFalse(any(domain in item for item in imports), domain)

    def test_ical_export_has_explicit_public_surface(self):
        from local_agentic_triage.events import ical
        self.assertEqual(ical.__all__, ('ical_event',))

    def test_poll_and_diagnose_inject_callables_under_their_own_names(self):
        from local_agentic_triage.classifier import disposition, events
        from local_agentic_triage.mail import event_pipeline, pipeline
        poll = inspect.signature(pipeline.poll).parameters
        self.assertEqual(list(poll), ['mailbox', 'config', 'classify', 'process_events', 'summarize',
                                      'extract_event_candidates', 'runner'])
        diagnose = inspect.signature(pipeline.diagnose).parameters
        self.assertEqual(list(diagnose), ['mailbox', 'config', 'uid', 'classify', 'expected_validity', 'summarize'])
        for parameters in (poll, diagnose):
            self.assertIs(parameters['classify'].default, disposition.classify)
            self.assertIs(parameters['summarize'].default, disposition.summarize)
        self.assertIs(poll['process_events'].default, event_pipeline.process_events)
        self.assertIs(poll['extract_event_candidates'].default, events.extract_event_candidates)
        tree = ast.parse((PACKAGE / 'mail/pipeline.py').read_text(encoding='utf-8'))
        names = ({node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)}
                 | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
                 | {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names})
        for alias in ('classifier', 'summarizer', 'extractor', 'event_processor', 'extract_events'):
            self.assertNotIn(alias, names)
        poll_tree = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'poll')
        called = {node.func.id for node in ast.walk(poll_tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertLessEqual({'summarize', 'classify', 'extract_event_candidates', 'process_events'}, called)

    def test_attachment_is_a_frozen_dependency_neutral_value(self):
        import dataclasses
        from local_agentic_triage.common import attachment
        self.assertEqual(attachment.__all__, ('Attachment',))
        self.assertEqual([field.name for field in dataclasses.fields(attachment.Attachment)], ['filename', 'content'])
        value = attachment.Attachment('event-1.ics', 'content')
        with self.assertRaises(dataclasses.FrozenInstanceError):
            value.content = 'other'
        tree = ast.parse((PACKAGE / 'common/attachment.py').read_text(encoding='utf-8'))
        self.assertFalse(any('local_agentic_triage' in ast.unparse(node) for node in ast.walk(tree)
                             if isinstance(node, (ast.Import, ast.ImportFrom))))

    def test_common_json_boundaries(self):
        from local_agentic_triage.common.json import compact_json, strict_json
        self.assertEqual(compact_json({'value': 'żółć'}), '{"value":"żółć"}')
        self.assertEqual(strict_json('{"value":1}'), {'value': 1})
        with self.assertRaisesRegex(ValueError, 'duplicate key'):
            strict_json('{"value":1,"value":2}')


if __name__ == '__main__':
    unittest.main()
