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
        from local_agentic_triage.classifier import disposition, events, helpers, resources
        expected = {
            'SUMMARIZE_OUTPUT_SCHEMA', 'CLASSIFY_OUTPUT_SCHEMA',
            'EXTRACT_EVENTS_OUTPUT_SCHEMA', 'IS_DUPLICATE_OUTPUT_SCHEMA',
            'COMPLETE_EVENT_OUTPUT_SCHEMA', 'summarize_prompt', 'classify_prompt',
            'extract_events_prompt', 'is_duplicate_prompt', 'complete_event_prompt',
        }
        self.assertEqual(set(resources.__all__), expected)
        for module in (helpers, disposition, events):
            self.assertFalse(any(name.endswith('OUTPUT_SCHEMA') for name in vars(module)))
        for name in expected:
            self.assertFalse(hasattr(helpers, name))
        self.assertFalse(hasattr(helpers, 'response_schema'))

    def test_disposition_has_explicit_public_surface(self):
        from local_agentic_triage.classifier import disposition
        self.assertEqual(disposition.__all__, ('summarize', 'classify'))

    def test_event_model_api_maps_both_duplicate_outcomes_to_bool(self):
        from local_agentic_triage.classifier import events
        public_functions = {
            name for name, value in inspect.getmembers(events, inspect.isfunction)
            if value.__module__ == events.__name__ and not name.startswith('_')
        }
        self.assertEqual(public_functions, {'extract_events', 'is_duplicate', 'complete_event'})
        self.assertEqual(events.__all__, ('extract_events', 'is_duplicate', 'complete_event'))
        tree = ast.parse(Path(events.__file__).read_text(encoding='utf-8'))
        imports = [ast.unparse(node) for node in ast.walk(tree)
                   if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any('local_agentic_triage.events' in item or
                             'local_agentic_triage.mail' in item for item in imports))
        with patch.object(events.helpers, 'chat_completion_with_payload', side_effect=[
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

    def test_stale_core_modules_and_imports_are_absent(self):
        self.assertFalse((PACKAGE / 'classifier/core.py').exists())
        self.assertFalse((PACKAGE / 'events/core.py').exists())
        for source in PACKAGE.rglob('*.py'):
            text = source.read_text(encoding='utf-8')
            self.assertNotIn('classifier.core', text, source)
            self.assertNotIn('events.core', text, source)

    def test_pipeline_uses_internal_result_without_json_roundtrip(self):
        source = (PACKAGE / 'mail/pipeline.py').read_text(encoding='utf-8')
        self.assertNotIn('parse_results', source)
        self.assertNotIn('json.dumps(event_result)', source)
        self.assertIn('from local_agentic_triage.mail.event_pipeline import process_events', source)

    def test_mail_event_pipeline_imports_only_public_domain_apis(self):
        source = PACKAGE / 'mail/event_pipeline.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        private_imports = [alias.name for node in ast.walk(tree)
                           if isinstance(node, ast.ImportFrom)
                           for alias in node.names if alias.name.startswith('_')]
        self.assertEqual(private_imports, [])

    def test_strict_json_is_common_boundary(self):
        from local_agentic_triage.common.json import compact_json, strict_json
        self.assertEqual(compact_json({'value': 'żółć'}), '{"value":"żółć"}')
        self.assertEqual(strict_json('{"value":1}'), {'value': 1})
        with self.assertRaisesRegex(ValueError, 'duplicate key'):
            strict_json('{"value":1,"value":2}')
        helpers = (PACKAGE / 'classifier/helpers.py').read_text(encoding='utf-8')
        imap = (PACKAGE / 'mail/imap.py').read_text(encoding='utf-8')
        self.assertNotIn('def strict_json', helpers)
        self.assertNotIn('classifier.helpers import strict_json', imap)


if __name__ == '__main__':
    unittest.main()
