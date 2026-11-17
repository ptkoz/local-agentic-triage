import json
import unittest
from unittest.mock import patch, Mock
from local_agentic_triage.classifier import disposition as core
from local_agentic_triage.classifier import helpers as classifier_helpers
from local_agentic_triage.classifier.helpers import disposition_prompt
from local_agentic_triage.mail.mime import parse
from local_agentic_triage.classifier.input import fit_to_context

class FivePassTests(unittest.TestCase):
    def test_destination_pass_returns_only_disposition(self):
        mail=fit_to_context(parse(b'From: synthetic@example.invalid\n\nbody',1000),10000)
        with patch.object(classifier_helpers,'chat_completion',return_value={'destination':'leave'}) as chat:
            self.assertEqual(core.classify(mail,'Synthetic summary'),'leave')
        messages,schema=chat.call_args.args
        self.assertEqual(set(schema['json_schema']['schema']['properties']),{'destination'})
        self.assertEqual(messages[0]['content'],disposition_prompt())
        self.assertEqual(messages[1]['content'],
            '{"summary": "Synthetic summary", "mail": {"metadata": {"sent_date": "", "sender": "synthetic@example.invalid", '
            '"subject": ""}, "body": "body"}}')


    def test_extraction_failure_is_optional_for_both_dispositions(self):
        from helpers import make_config
        from local_agentic_triage.mail.pipeline import poll
        for destination in ('leave','archive'):
            with self.subTest(destination=destination):
                box=Mock(validity='77'); box.candidates.return_value=['9']
                box.raw.return_value=b'Date: Wed, 01 Jan 2025 12:00:00 +0100\nFrom: synthetic@example.invalid\n\nbody'
                summarizer=Mock(return_value='summary')
                classifier=Mock(return_value=destination)
                extractor=Mock(side_effect=ValueError('PRIVATE'))
                processor=Mock()
                runner=Mock(return_value=Mock(returncode=0))
                with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                    self.assertEqual(poll(box,make_config(),classifier,processor,summarizer=summarizer,
                                          extractor=extractor,runner=runner),0)
                self.assertNotIn('PRIVATE','\n'.join(logs.output))
                getattr(box,destination).assert_called_once_with('9')
                runner.assert_called_once()
                processor.assert_not_called()
                self.assertEqual(extractor.call_args.args,classifier.call_args.args[:1])
                self.assertEqual(classifier.call_args.args[1],'summary')


    def test_all_five_schemas_are_closed_and_complete(self):
        from local_agentic_triage.classifier import events
        def check(schema):
            if schema.get('type')=='object':
                self.assertIs(schema['additionalProperties'],False)
                self.assertEqual(set(schema['required']),set(schema['properties']))
                for child in schema['properties'].values(): check(child)
            if schema.get('type')=='array': check(schema['items'])
        for schema in (classifier_helpers.CLASSIFICATION_SCHEMA,classifier_helpers.SUMMARY_SCHEMA,
                       events._EXTRACTION_SCHEMA,events._DUPLICATE_SCHEMA,events._COMPLETION_SCHEMA):
            self.assertIs(schema['json_schema']['strict'],True)
            check(schema['json_schema']['schema'])


    def test_each_model_failure_has_the_correct_disposition_boundary(self):
        from helpers import make_config
        from test_events import decision_response
        from local_agentic_triage.classifier import events
        from local_agentic_triage.mail.event_pipeline import process_events
        from local_agentic_triage.mail.pipeline import poll
        candidate={'title':'Synthetic','start':'2099-02-01T10:00:00Z','end':None,'date_note':None,'location':None}
        for destination in ('leave','archive'):
            answers=[{'summary':'Synthetic'},{'destination':destination}]
            decision_calls=2
            answers.extend([{'events':[candidate]}, {'outcome':'create'}])
            for stage in range(len(answers)+1):
                for failure in ('timeout','malformed','incomplete'):
                    with self.subTest(destination=destination,stage=stage,failure=failure):
                        box=Mock(validity='77'); box.candidates.return_value=['9']
                        box.raw.return_value=b'Date: Wed, 01 Jan 2025 12:00:00 +0100\nFrom: synthetic@example.invalid\n\nbody'
                        responses=[decision_response(answer) for answer in answers[:stage]]
                        responses.append(TimeoutError('PRIVATE') if failure=='timeout' else
                                         decision_response(content='PRIVATE') if failure=='malformed' else
                                         decision_response({},finish='length'))
                        runner=Mock(return_value=Mock(returncode=0))
                        with patch('urllib.request.OpenerDirector.open',side_effect=responses), \
                             patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
                             patch('local_agentic_triage.events.calendar.Calendar.create') as create, \
                             self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                            self.assertEqual(poll(box,make_config(),core.classify,process_events,extractor=events.extract_events,runner=runner),int(stage<decision_calls))
                        self.assertNotIn('PRIVATE','\n'.join(logs.output))
                        create.assert_not_called()
                        if stage<decision_calls:
                            box.leave.assert_not_called(); box.archive.assert_not_called()
                            runner.assert_not_called()
                        else:
                            getattr(box,destination).assert_called_once_with('9')
                            self.assertNotIn('mail_item_failed','\n'.join(logs.output))
                            runner.assert_called_once()
