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

    def test_sparse_extraction_is_independent_and_rejects_guesses(self):
        from local_agentic_triage.events import core as events
        mail=parse(b'From: synthetic@example.invalid\n\nbody',1000)
        candidate={'title':'Synthetic meeting','start':None,'end':None,'date_note':'uncertain day'}
        with patch.object(events,'chat_completion',return_value={'events':[candidate]}) as chat:
            self.assertEqual(events.extract(mail),[candidate])
        messages,schema=chat.call_args.args
        self.assertEqual(json.loads(messages[1]['content']),core.mail_payload(mail))
        self.assertEqual(set(schema['json_schema']['schema']['properties']),{'events'})
        self.assertNotEqual(messages[0]['content'],core.disposition_prompt())
        for change in ({'start':'2099-01-01'}, {'end':'2099-01-01'},
                       {'date_note':None,'start':'2099-01-01T10:00:00'},
                       {'date_note':None,'start':'2099-01-01','end':'2099-01-01'}):
            with self.subTest(change=change),patch.object(events,'chat_completion',return_value={'events':[candidate|change]}):
                with self.assertRaises(ValueError): events.extract(mail)

    def test_duplicate_then_completion_patch_before_write(self):
        from datetime import datetime,timezone
        from helpers import make_config
        from local_agentic_triage.events import core as events
        event={'title':'Synthetic meeting','start':'2099-02-01T10:00:00+01:00','end':None,'date_note':None}
        answers=[{'results':[{'index':0,'outcome':'no_match'}]},
                 {'results':[{'index':0,'outcome':'complete','reason':None,'estimated_end':'2099-02-01T12:00:00+01:00'}]}]
        with patch.object(events,'chat_completion',side_effect=answers) as chat, \
             patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
             patch('local_agentic_triage.events.calendar.Calendar.create') as create:
            result=events.process([event],make_config(),clock=lambda tz=None:datetime(2099,1,1,tzinfo=timezone.utc))
        self.assertEqual(result['results'],[{'index':0,'outcome':'created','reason':None}])
        create.assert_called_once_with(event|{'end':'2099-02-01T12:00:00+01:00'})
        duplicate,completion=chat.call_args_list
        self.assertEqual(json.loads(duplicate.args[0][1]['content'])['events'],[{k:event[k] for k in ('title','start','end')}])
        self.assertEqual(json.loads(completion.args[0][1]['content']),{'events':[{'index':0,'title':event['title'],'start':event['start']}]})
        self.assertNotIn('estimated_end',json.dumps(duplicate.args))
        self.assertNotEqual(duplicate.args[1],completion.args[1])
        self.assertNotEqual(duplicate.args[0][0],completion.args[0][0])

    def test_identical_extracted_candidates_create_only_once_offline(self):
        from datetime import datetime, timezone
        from helpers import make_config
        from test_events import decision_response
        from local_agentic_triage.events import core as events
        mail = parse(b'Date: Thu, 01 Jan 2099 12:00:00 +0100\nFrom: synthetic@example.invalid\n\nSynthetic booking', 1000)
        event = dict(title='Synthetic meeting', start='2099-02-01T10:00:00+01:00',
                     end='2099-02-01T11:00:00+01:00', date_note=None)
        answers = [dict(events=[event, dict(event)]),
                   dict(results=[dict(index=i, outcome='no_match') for i in range(2)])]
        with patch('urllib.request.OpenerDirector.open', side_effect=[decision_response(a) for a in answers]) as opened, \
             patch('local_agentic_triage.events.calendar.Calendar.nearby', return_value=[]), \
             patch('local_agentic_triage.events.calendar.Calendar.create') as create:
            result = events.process(events.extract(mail), make_config(),
                                    clock=lambda tz=None: datetime(2099, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result['results'], [dict(index=0, outcome='created', reason=None),
                                             dict(index=1, outcome='existing', reason=None)])
        create.assert_called_once_with(event | {'end': '2099-02-01T11:00:00+01:00'})
        payload = json.loads(opened.call_args_list[1].args[0].data)
        sparse = json.loads(payload['messages'][1]['content'])
        self.assertEqual(sparse['events'], [{k: event[k] for k in ('title', 'start', 'end')}] * 2)
        self.assertEqual(sparse['nearby'], [[], []])

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

    def test_closed_validators_reject_replacements_and_bad_coverage(self):
        from local_agentic_triage.events import core as events
        candidate={'title':'Synthetic','start':'2099-02-01T10:00:00+01:00','end':None,'date_note':None}
        duplicate={'index':0,'outcome':'no_match'}
        patch_result={'index':0,'outcome':'complete','reason':None,'estimated_end':'2099-02-01T10:30:00Z'}
        for validator,entry,args in ((events.duplicate_results,duplicate,([candidate],)),
                                     (events.completion_results,patch_result,([candidate],[0]))):
            self.assertEqual(validator({'results':[entry]},*args),[entry])
            malformed=[[],[entry,entry],[entry|{'index':True}],[entry|{'index':0.0}],
                       [entry|{'index':1}],[entry|{'title':'replacement'}],
                       [entry|{'start':candidate['start']}],[entry|{'end':'replacement'}],
                       [entry|{'date_note':None}],[entry|{'outcome':'created'}]]
            for entries in malformed:
                with self.subTest(stage=validator.__name__,entries=entries),self.assertRaises(ValueError):
                    validator({'results':entries},*args)
        for estimate in (None,False,'2099-02-01','2099-02-01T10:00:00','2099-02-01T09:00:00Z',
                         '2099-02-01T08:59:00Z','2099-02-01 11:00:00+01:00'):
            with self.subTest(estimate=estimate),self.assertRaises(ValueError):
                events.completion_results({'results':[patch_result|{'estimated_end':estimate}]},[candidate],[0])
        for end in ('2099-02-01T11:00:00+01:00',):
            with self.assertRaises(ValueError):
                events.completion_results({'results':[patch_result]},[candidate|{'end':end}],[0])
        # Offset-equivalent instants compare absolutely, and long estimates have no cap.
        for end in ('2099-02-01T09:01:00Z','2099-03-01T10:00:00+01:00'):
            events.completion_results({'results':[patch_result|{'estimated_end':end}]},[candidate],[0])
        for extra in ({'estimated_end':None},{'reason':None}):
            with self.assertRaises(ValueError):
                events.duplicate_results({'results':[duplicate|extra]},[candidate])

    def test_completion_failure_prevents_all_writes_and_final_gate_rechecks_batch(self):
        from datetime import datetime,timezone
        from helpers import make_config
        from local_agentic_triage.events import core as events
        first={'title':'Complete','start':'2099-02-01T10:00:00Z','end':'2099-02-01T11:00:00Z','date_note':None}
        second=first|{'title':'Sparse','end':None}
        duplicate={'results':[{'index':i,'outcome':'no_match'} for i in range(2)]}
        valid={'index':1,'outcome':'complete','reason':None,'estimated_end':'2099-02-01T12:00:00Z'}
        for completion in ({'results':[valid|{'title':'replacement'}]}, {'results':[]},
                           {'results':[valid|{'index':0}]}, {'results':[valid|{'estimated_end':None}]}):
            with self.subTest(completion=completion),patch.object(events,'chat_completion',side_effect=[duplicate,completion]), \
                 patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
                 patch('local_agentic_triage.events.calendar.Calendar.create') as create:
                with self.assertRaises(ValueError): events.process([first,second],make_config())
                create.assert_not_called()
        moments=iter([datetime(2099,1,1,tzinfo=timezone.utc),datetime(2099,1,31,tzinfo=timezone.utc)])
        with patch.object(events,'chat_completion',side_effect=[duplicate,{'results':[valid]}]), \
             patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
             patch('local_agentic_triage.events.calendar.Calendar.create') as create:
            with self.assertRaisesRegex(ValueError,'calendar_too_soon'):
                events.process([first,second],make_config(),clock=lambda tz=None:next(moments))
            create.assert_not_called()

    def test_four_transport_payloads_and_route_skipping(self):
        from helpers import make_config
        from test_events import decision_response
        from local_agentic_triage.events import core as events
        mail=parse(b'Date: Wed, 01 Jan 2025 12:00:00 +0100\nFrom: synthetic@example.invalid\nSubject: Synthetic\n\nbody',1000)
        candidate={'title':'Synthetic','start':'2099-02-01T10:00:00+01:00','end':None,'date_note':None}
        routes=[([],None,None,2),([candidate],'existing',None,3),
                ([candidate],'possible_duplicate',None,3),
                ([candidate|{'end':'2099-02-01T11:00:00+01:00'}],'no_match',None,3),
                ([candidate|{'start':'2099-02-01'}],'no_match',None,3),
                ([candidate],'no_match','complete',4),([candidate],'no_match','skipped',4)]
        for candidates,duplicate,completion,count in routes:
            with self.subTest(duplicate=duplicate,completion=completion,count=count):
                answers=[{'destination':'archive','summary':'Synthetic summary'},{'events':candidates}]
                if duplicate: answers.append({'results':[{'index':0,'outcome':duplicate}]})
                if completion:
                    answers.append({'results':[{'index':0,'outcome':completion,
                        'reason':None if completion=='complete' else 'missing_end',
                        'estimated_end':'2099-02-01T12:00:00+01:00' if completion=='complete' else None}]})
                with patch('urllib.request.OpenerDirector.open',side_effect=[decision_response(a) for a in answers]) as opened, \
                     patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
                     patch('local_agentic_triage.events.calendar.Calendar.create') as create:
                    core.classify(mail)
                    extracted=events.extract(mail)
                    events.process(extracted,make_config())
                self.assertEqual(opened.call_count,count)
                payloads=[json.loads(call.args[0].data) for call in opened.call_args_list]
                schemas=[core.CLASSIFICATION_SCHEMA,events.EXTRACTION_SCHEMA,events.DUPLICATE_SCHEMA,events.COMPLETION_SCHEMA][:count]
                prompts=[core.disposition_prompt(),events.extraction_prompt(),events.prompt(),events.completion_prompt()][:count]
                self.assertEqual([p['response_format'] for p in payloads],schemas)
                self.assertEqual([p['messages'][0]['content'] for p in payloads],prompts)
                self.assertEqual(len(set(prompts)),count)
                self.assertEqual(payloads[0]['messages'][1],payloads[1]['messages'][1])
                for call,payload in zip(opened.call_args_list,payloads):
                    self.assertEqual(call.args[0].full_url,core.ENDPOINT)
                    self.assertEqual(call.kwargs['timeout'],120)
                    self.assertEqual(payload['model'],core.MODEL)
                    self.assertEqual(payload['temperature'],0)
                    self.assertEqual(payload['chat_template_kwargs'],{'enable_thinking':False})
                    self.assertNotIn('tools',payload)
                if duplicate:
                    sparse=json.loads(payloads[2]['messages'][1]['content'])['events'][0]
                    self.assertEqual(sparse,{k:candidates[0][k] for k in ('title','start','end')})
                if completion:
                    self.assertEqual(json.loads(payloads[3]['messages'][1]['content']),
                                     {'events':[{'index':0,'title':candidate['title'],'start':candidate['start']}]})
                if duplicate=='no_match' and completion!='skipped': self.assertEqual(create.call_count,1)
                else: create.assert_not_called()

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

    def test_uncertain_and_missing_dates_never_reach_later_stages(self):
        from helpers import make_config
        from local_agentic_triage.events import core as events
        candidates=[{'title':'Synthetic','start':None,'end':None,'date_note':note} for note in (None,'ambiguous')]
        with patch.object(events,'chat_completion') as chat,patch('local_agentic_triage.events.calendar.Calendar.nearby') as nearby:
            result=events.process(candidates,make_config())
        self.assertEqual([r['reason'] for r in result['results']],['missing_date','uncertain_date'])
        chat.assert_not_called();nearby.assert_not_called()

    def test_each_model_failure_has_the_correct_disposition_boundary(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock
        from helpers import make_config
        from test_events import decision_response
        from local_agentic_triage.events import core as events
        from local_agentic_triage.mail.pipeline import poll
        from local_agentic_triage.reporting.store import Store
        candidate={'title':'Synthetic','start':'2099-02-01T10:00:00Z','end':None,'date_note':None}
        for destination in ('leave','archive'):
            answers=[{'destination':destination,'summary':'Synthetic' if destination=='archive' else None},
                     {'events':[candidate]}, {'results':[{'index':0,'outcome':'no_match'}]}]
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
