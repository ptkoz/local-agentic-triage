import json
import unittest
from unittest.mock import patch
from local_agentic_triage.classifier import core
from local_agentic_triage.mail.mime import parse

class FourPassTests(unittest.TestCase):
    def test_destination_pass_returns_only_disposition(self):
        mail=parse(b'From: synthetic@example.invalid\n\nbody',1000)
        answer={'destination':'leave','summary':None}
        with patch.object(core,'chat_completion',return_value=answer) as chat:
            self.assertEqual(core.classify(mail),answer)
        messages,schema=chat.call_args.args
        self.assertEqual(set(schema['json_schema']['schema']['properties']),{'destination','summary'})
        self.assertNotEqual(messages[0]['content'],core.prompt())
        self.assertEqual(json.loads(messages[1]['content']),core.mail_payload(mail))


    def test_extraction_failure_is_optional_for_both_dispositions(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock
        from helpers import make_config
        from local_agentic_triage.mail.pipeline import poll
        from local_agentic_triage.reporting.store import Store
        for destination in ('leave','archive'):
            with self.subTest(destination=destination),tempfile.TemporaryDirectory() as directory:
                box=Mock(validity='77'); box.candidates.return_value=['9']; box.source_key.return_value='synthetic'
                box.raw.return_value=b'Date: Wed, 01 Jan 2025 12:00:00 +0100\nFrom: synthetic@example.invalid\n\nbody'
                box.all_uids.return_value={'9'}
                classifier=Mock(return_value={'destination':destination,'summary':'summary' if destination=='archive' else None})
                extractor=Mock(side_effect=ValueError('PRIVATE'))
                processor=Mock()
                with self.subTest(boundary='optional'):
                    store=Store(Path(directory)/'db')
                    try:
                        with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                            self.assertEqual(poll(box,store,make_config(),classifier,processor,extractor=extractor),0)
                        self.assertNotIn('PRIVATE','\n'.join(logs.output))
                        getattr(box,destination).assert_called_once_with('9')
                        processor.assert_not_called()
                        self.assertEqual(extractor.call_args.args,classifier.call_args.args)
                    finally: store.close()


    def test_all_four_schemas_are_closed_and_complete(self):
        from local_agentic_triage.events import core as events
        def check(schema):
            if schema.get('type')=='object':
                self.assertIs(schema['additionalProperties'],False)
                self.assertEqual(set(schema['required']),set(schema['properties']))
                for child in schema['properties'].values(): check(child)
            if schema.get('type')=='array': check(schema['items'])
        for schema in (core.CLASSIFICATION_SCHEMA,events.EXTRACTION_SCHEMA,events.DUPLICATE_SCHEMA,events.COMPLETION_SCHEMA):
            self.assertIs(schema['json_schema']['strict'],True)
            check(schema['json_schema']['schema'])


    def test_each_model_failure_has_the_correct_disposition_boundary(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock
        from helpers import make_config
        from test_events import decision_response
        from local_agentic_triage.events import core as events
        from local_agentic_triage.mail.pipeline import poll
        from local_agentic_triage.reporting.store import Store
        candidate={'title':'Synthetic','start':'2099-02-01T10:00:00Z','end':None,'date_note':None,'location':None}
        for destination in ('leave','archive'):
            answers=[{'destination':destination,'summary':'Synthetic' if destination=='archive' else None},
                     {'events':[candidate]}, {'outcome':'create'}]
            for stage in range(4):
                for failure in ('timeout','malformed','incomplete'):
                    with self.subTest(destination=destination,stage=stage,failure=failure),tempfile.TemporaryDirectory() as directory:
                        box=Mock(validity='77');box.candidates.return_value=['9'];box.source_key.return_value='synthetic'
                        box.all_uids.return_value={'9'}
                        box.raw.return_value=b'Date: Wed, 01 Jan 2025 12:00:00 +0100\nFrom: synthetic@example.invalid\n\nbody'
                        responses=[decision_response(answer) for answer in answers[:stage]]
                        responses.append(TimeoutError('PRIVATE') if failure=='timeout' else
                                         decision_response(content='PRIVATE') if failure=='malformed' else
                                         decision_response({},finish='length'))
                        store=Store(Path(directory)/'db')
                        try:
                            with patch('urllib.request.OpenerDirector.open',side_effect=responses), \
                                 patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
                                 patch('local_agentic_triage.events.calendar.Calendar.create') as create, \
                                 self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                                self.assertEqual(poll(box,store,make_config(),core.classify,events.process,extractor=events.extract),int(stage==0))
                            self.assertNotIn('PRIVATE','\n'.join(logs.output))
                            create.assert_not_called()
                            if stage==0:
                                box.leave.assert_not_called();box.archive.assert_not_called()
                                self.assertIsNone(store.prepared('synthetic'))
                            else:
                                getattr(box,destination).assert_called_once_with('9')
                                self.assertNotIn('mail_item_failed','\n'.join(logs.output))
                                if destination=='archive': self.assertIsNotNone(store.prepared('synthetic')['ready_at'])
                        finally: store.close()
