import json
import subprocess
import unittest
from datetime import datetime
from unittest.mock import patch
from helpers import make_config
from local_agentic_triage.events.core import process, parse_results, prompt, parse_stream_envelope

EVENT={'title':'Private title','start':'2026-12-01','end':None,'date_note':None}
DECISION={'status':'ok','results':[{'index':0,'outcome':'existing','reason':None,'estimated_end':None}]}

def stream(decision=DECISION,session='session-1',events=(),warnings=()):
    records=[{'type':'system','subtype':'init','session_id':session},*events,
             {'type':'result','session_id':session,'exit_code':0,'text':json.dumps(decision)}]
    return '\n'.join([*(f'Warning: {warning}' for warning in warnings),*(json.dumps(record) for record in records)])+'\n'

class EventTests(unittest.TestCase):
    def test_results_exact_coverage(self):
        for status in ('created','existing','skipped'):
            self.assertTrue(parse_results(json.dumps({'status':'ok','results':[{'index':0,'outcome':status,'reason':'possible_duplicate' if status=='skipped' else None}]}),[EVENT]))
        for result in ({'outcomes':[]},{'outcomes':[{'index':0,'status':'error'}]},{'outcomes':[{'index':0,'status':'created','extra':1}]},{'outcomes':[{'index':0,'status':'created'},{'index':0,'status':'existing'}]}):
            with self.assertRaises(ValueError): parse_results(json.dumps(result),[EVENT])
        with self.assertRaises(ValueError): parse_results('{"outcomes":[],"outcomes":[]}',[])
        with self.assertRaises(ValueError): parse_results(json.dumps({'outcomes':[{'index':0,'status':'created'}]}),[EVENT|{'start':None}])
    def test_stdin_privacy_and_arguments(self):
        with patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,stream(),'secret stderr')) as run:
            process([EVENT],make_config(calendar='Only This Calendar'))
            args=run.call_args.args[0]
            self.assertEqual(args[1:],['chat','--query-file','-','--oneshot','--quiet','--format','stream-json','--source','tool','--toolsets','none','--ignore-rules','--max-turns','20','--run-budget','120'])
            self.assertNotIn('Private title',' '.join(args))
            self.assertTrue(run.call_args.kwargs['input'].endswith(json.dumps({'calendar':'Only This Calendar','events':[EVENT],'nearby':[[]]},ensure_ascii=False)))
            self.assertFalse(run.call_args.kwargs['shell'])
        with patch('subprocess.run') as run:
            process([],make_config()); run.assert_not_called()
    def test_prompt_contract(self):
        p=prompt()
        for text in ('configured calendar','untrusted','7 days','readback','estimated_end','best plausible','title/event type','no fixed duration cap','strict JSON'):
            self.assertIn(text,p)
        self.assertIn('block event creation only',p)
        self.assertNotIn('block creation and disposition',p)
        self.assertNotIn('blocks mail disposition',p)

    def test_missing_timed_end_is_estimated_and_only_copy_is_created(self):
        event=EVENT|{'start':'2099-12-01T18:00:00+01:00'}
        estimated='2099-12-01T21:00:00+01:00'
        decision={'status':'ok','results':[{
            'index':0,'outcome':'create','reason':None,'estimated_end':estimated,
        }]}
        with patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
             patch('local_agentic_triage.events.calendar.Calendar.create') as create, \
             patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,stream(decision),'')):
            result=process([event],make_config())
        create.assert_called_once_with(event|{'end':estimated})
        self.assertIsNone(event['end'])
        self.assertEqual(result,{'status':'ok','results':[{
            'index':0,'outcome':'created','reason':None,
        }]})

    def test_estimated_end_validation_is_temporal_and_authorized(self):
        missing=EVENT|{'start':'2099-12-01T18:00:00+01:00'}
        supplied=missing|{'end':'2099-12-01T19:00:00+01:00'}
        base={'index':0,'outcome':'create','reason':None}
        invalid=(
            (missing,base|{'estimated_end':None}),
            (missing,base|{'estimated_end':'not-a-date'}),
            (missing,base|{'estimated_end':'2099-12-01 19:00:00+01:00'}),
            (missing,base|{'estimated_end':'2099-12-01T18:00:00+01:00'}),
            (missing,base|{'estimated_end':'2099-12-01T19:00:00'}),
            (supplied,base|{'estimated_end':'2099-12-01T20:00:00+01:00'}),
            (EVENT,base|{'estimated_end':'2099-12-02'}),
            (missing,base|{'outcome':'existing','estimated_end':'2099-12-01T19:00:00+01:00'}),
            (missing,base|{'outcome':'skipped','reason':'missing_end','estimated_end':'2099-12-01T19:00:00+01:00'}),
        )
        for event,entry in invalid:
            with self.subTest(entry=entry),self.assertRaises(ValueError):
                parse_results(json.dumps({'status':'ok','results':[entry]}),[event],decisions=True)
        valid=(
            base|{'estimated_end':'2099-12-04T18:00:01+01:00'},
            base|{'estimated_end':'2099-12-01T18:30:00Z'},
        )
        for entry in valid:
            with self.subTest(entry=entry):
                self.assertEqual(parse_results(json.dumps({'status':'ok','results':[entry]}),[missing],decisions=True)['results'][0],entry)
        delegated=base|{'outcome':'skipped','reason':'too_soon','estimated_end':None}
        with self.assertRaises(ValueError):
            parse_results(json.dumps({'status':'ok','results':[delegated]}),[missing],decisions=True)

    def test_existing_missing_timed_end_is_safe_without_an_estimate(self):
        existing={'index':0,'outcome':'existing','reason':None,'estimated_end':None}
        final={'index':0,'outcome':'existing','reason':None}
        known=EVENT|{'start':'2099-12-01T18:00:00+01:00'}
        self.assertEqual(
            parse_results(json.dumps({'status':'ok','results':[existing]}),[known],decisions=True)['results'],
            [existing],
        )
        self.assertEqual(parse_results(json.dumps({'status':'ok','results':[final]}),[known])['results'],[final])
        for unsafe in (known|{'start':None},known|{'date_note':'uncertain'}):
            with self.subTest(unsafe=unsafe),self.assertRaisesRegex(ValueError,'unsafe_event_outcome'):
                parse_results(json.dumps({'status':'ok','results':[existing]}),[unsafe],decisions=True)
            with self.subTest(unsafe=unsafe),self.assertRaisesRegex(ValueError,'unsafe_event_outcome'):
                parse_results(json.dumps({'status':'ok','results':[final]}),[unsafe])

    def test_processed_created_may_complete_missing_end_without_weakening_final_results(self):
        created={'status':'ok','results':[{'index':0,'outcome':'created','reason':None}]}
        known=EVENT|{'start':'2099-12-01T18:00:00+01:00'}
        with self.assertRaisesRegex(ValueError,'unsafe_event_outcome'):
            parse_results(json.dumps(created),[known])
        self.assertEqual(parse_results(json.dumps(created),[known],processed=True),created)
        for unsafe in (known|{'start':None},known|{'date_note':'uncertain'}):
            with self.subTest(unsafe=unsafe),self.assertRaisesRegex(ValueError,'unsafe_event_outcome'):
                parse_results(json.dumps(created),[unsafe],processed=True)
        existing={'status':'ok','results':[{'index':0,'outcome':'existing','reason':None}]}
        for unsafe in (known|{'start':None},known|{'date_note':'uncertain'}):
            with self.subTest(unsafe=unsafe),self.assertRaisesRegex(ValueError,'unsafe_event_outcome'):
                parse_results(json.dumps(existing),[unsafe],processed=True)

    def test_final_results_require_ordered_complete_coverage(self):
        events=[EVENT,EVENT|{'title':'Second','start':'2026-12-02'}]
        unordered={'status':'ok','results':[
            {'index':1,'outcome':'existing','reason':None},
            {'index':0,'outcome':'existing','reason':None},
        ]}
        with self.assertRaisesRegex(ValueError,'event_outcome_index'):
            parse_results(json.dumps(unordered),events)

    def test_gate_excludes_boundary_near_and_missing_before_calendar_or_hermes(self):
        events=[
            EVENT|{'start':'2030-01-04T12:00:00+02:00','end':'2030-01-04T13:00:00+02:00'},
            EVENT|{'title':'All day boundary','start':'2030-01-04'},
            EVENT|{'title':'Missing','start':None},
        ]
        with patch('local_agentic_triage.events.calendar.Calendar.nearby') as nearby, \
             patch('subprocess.run') as run:
            result=process(events,make_config(),clock=lambda tz=None: datetime(2030,1,1,10,0,tzinfo=tz))
        self.assertEqual(result,{'status':'ok','results':[
            {'index':0,'outcome':'skipped','reason':'too_soon'},
            {'index':1,'outcome':'skipped','reason':'too_soon'},
            {'index':2,'outcome':'skipped','reason':'missing_date'},
        ]})
        nearby.assert_not_called(); run.assert_not_called()

    def test_gate_clock_is_snapshotted_once_for_the_batch(self):
        events=[EVENT|{'start':'2030-01-04'},EVENT|{'title':'Second','start':'2030-01-04'}]
        calls=[]
        def clock(tz=None):
            calls.append(tz)
            return datetime(2030,1,1,10,0,tzinfo=tz)
        with patch('local_agentic_triage.events.calendar.Calendar.nearby') as nearby,patch('subprocess.run') as run:
            result=process(events,make_config(),clock=clock)
        self.assertEqual(len(calls),1)
        self.assertEqual([entry['reason'] for entry in result['results']],['too_soon','too_soon'])
        nearby.assert_not_called(); run.assert_not_called()

    def test_mixed_gate_sends_only_eligible_events_and_maps_original_indices(self):
        events=[
            EVENT|{'title':'Near private','start':'2030-01-04'},
            EVENT|{'title':'Eligible private','start':'2030-01-05'},
            EVENT|{'title':'Missing private','start':None},
            EVENT|{'title':'Timed eligible private','start':'2030-01-04T12:00:01+02:00','end':'2030-01-04T13:00:01+02:00'},
        ]
        decision={'status':'ok','results':[
            {'index':0,'outcome':'existing','reason':None,'estimated_end':None},
            {'index':1,'outcome':'create','reason':None,'estimated_end':None},
        ]}
        def run(args,**kwargs):
            payload=json.loads(kwargs['input'].split('\n--- BEGIN UNTRUSTED EVENTS JSON ---\n')[1])
            self.assertEqual(payload['events'],[events[1],events[3]])
            self.assertEqual(payload['nearby'],[['first'],['second']])
            self.assertNotIn('Near private',kwargs['input'])
            self.assertNotIn('Missing private',kwargs['input'])
            return subprocess.CompletedProcess(args,0,stream(decision),'')
        with patch('local_agentic_triage.events.calendar.Calendar.nearby',side_effect=[['first'],['second']]) as nearby, \
             patch('local_agentic_triage.events.calendar.Calendar.create') as create, \
             patch('subprocess.run',side_effect=run):
            result=process(events,make_config(),clock=lambda tz=None: datetime(2030,1,1,10,0,tzinfo=tz))
        self.assertEqual([call.args[0] for call in nearby.call_args_list],[events[1],events[3]])
        create.assert_called_once_with(events[3])
        self.assertEqual(result['results'],[
            {'index':0,'outcome':'skipped','reason':'too_soon'},
            {'index':1,'outcome':'existing','reason':None},
            {'index':2,'outcome':'skipped','reason':'missing_date'},
            {'index':3,'outcome':'created','reason':None},
        ])

    def test_eligible_decisions_validate_every_result_before_any_write(self):
        events=[EVENT|{'start':'2030-01-01'},EVENT|{'title':'Later','start':'2030-01-05'}]
        decision={'status':'ok','results':[
            {'index':0,'outcome':'create','reason':None,'estimated_end':None},
            {'index':1,'outcome':'create','reason':None},
        ]}
        with patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
             patch('local_agentic_triage.events.calendar.Calendar.create') as create, \
             patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,stream(decision),'')), \
             self.assertRaises(ValueError):
            process(events,make_config(),clock=lambda tz=None: datetime(2030,1,1,10,0,tzinfo=tz))
        create.assert_not_called()

    def test_supplied_end_is_preserved_and_estimate_must_be_null(self):
        event=EVENT|{'start':'2099-12-01T18:00:00+01:00','end':'2099-12-01T19:30:00+01:00'}
        decision={'status':'ok','results':[{
            'index':0,'outcome':'create','reason':None,'estimated_end':None,
        }]}
        with patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
             patch('local_agentic_triage.events.calendar.Calendar.create') as create, \
             patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,stream(decision),'')):
            process([event],make_config())
        create.assert_called_once_with(event)
    def test_nonzero(self):
        with patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), patch('subprocess.run',return_value=subprocess.CompletedProcess([],1,'private','private')):
            with self.assertRaisesRegex(RuntimeError,'event_command_failed') as raised: process([EVENT],make_config())
            self.assertEqual(getattr(raised.exception,'returncode',None),1)
        with patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), patch('subprocess.run',side_effect=OSError('PRIVATE')):
            with self.assertRaisesRegex(RuntimeError,'^os_error$'): process([EVENT],make_config())
    def test_full_contract_rejects_inconsistent_and_unknown_reasons(self):
        valid={'status':'ok','results':[{'index':0,'outcome':'created','reason':None}]}
        for result in (valid|{'status':'error'},valid|{'extra':1}, {'status':'ok','results':[{'index':0,'outcome':'skipped','reason':None}]}, {'status':'ok','results':[{'index':0,'outcome':'existing','reason':'possible_duplicate'}]}, {'status':'error','results':[{'index':0,'outcome':'error','reason':'search_failed'}]}, {'status':'ok','results':[{'index':False,'outcome':'created','reason':None}]}):
            with self.subTest(result=result),self.assertRaises(ValueError): parse_results(json.dumps(result),[EVENT])
        with self.assertRaises(ValueError): parse_results(json.dumps(valid),[EVENT|{'title':'x'*201}])
        p=prompt()
        for text in ('possible_duplicate','200','no other actions','status','reason'):
            self.assertIn(text,p)

    def test_stream_envelope_valid_with_warnings_and_text_metadata(self):
        for warnings in ((),('synthetic warning',)):
            raw=stream(events=({'type':'text','text':'ignored','timestamp':3},),warnings=warnings)
            self.assertEqual(parse_stream_envelope(raw,0),json.dumps(DECISION))

    def test_stream_envelope_bounds(self):
        from local_agentic_triage.events.core import (MAX_STREAM_LINE_BYTES,
            MAX_STREAM_RECORDS,MAX_STREAM_WARNING_BYTES,MAX_STREAM_WARNINGS)
        cases=(
            stream(warnings=('x',)*(MAX_STREAM_WARNINGS+1)),
            stream(warnings=('x'*MAX_STREAM_WARNING_BYTES,)),
            stream(events=({'type':'metadata','value':'x'*MAX_STREAM_LINE_BYTES},)),
            stream(events=tuple({'type':'text','text':'ignored'} for _ in range(MAX_STREAM_RECORDS))),
        )
        for raw in cases:
            with self.subTest(size=len(raw)),self.assertRaisesRegex(ValueError,'^event_stream_invalid$'):
                parse_stream_envelope(raw,0)

    def test_stream_envelope_rejects_framing_and_privately(self):
        valid_result={'type':'result','session_id':'session-1','exit_code':0,'text':json.dumps(DECISION)}
        init={'type':'system','subtype':'init','session_id':'session-1'}
        cases=(
            'not-json\n',
            '[]\n',
            '{"type":"metadata","type":"metadata"}\n',
            ' Warning: misplaced\n'+json.dumps(init)+'\n'+json.dumps(valid_result)+'\n',
            json.dumps(init)+'\nWarning: misplaced\n'+json.dumps(valid_result)+'\n',
            json.dumps(valid_result)+'\n',
            json.dumps(init)+'\n'+json.dumps(init)+'\n'+json.dumps(valid_result)+'\n',
            json.dumps(init)+'\n',
            json.dumps(init)+'\n'+json.dumps(valid_result)+'\n'+json.dumps({'type':'metadata'})+'\n',
            json.dumps(init)+'\n'+json.dumps(valid_result)+'\n'+json.dumps(valid_result)+'\n',
            json.dumps({'type':'metadata'})+'\n'+json.dumps(init)+'\n'+json.dumps(valid_result)+'\n',
            json.dumps(init)+'\n'+json.dumps({'type':'result','session_id':'session-1','exit_code':False,'text':'PRIVATE_SENTINEL'})+'\n',
            json.dumps(init)+'\n'+json.dumps(valid_result|{'exit_code':2})+'\n',
            json.dumps(init)+'\n'+json.dumps(valid_result|{'error':None})+'\n',
            json.dumps(init)+'\n'+json.dumps(valid_result|{'session_id':'other'})+'\n',
            json.dumps(init)+'\n'+json.dumps({'type':'tool_use','text':'PRIVATE_SENTINEL'})+'\n'+json.dumps(valid_result)+'\n',
            json.dumps(init)+'\n'+json.dumps({'type':'tool_result','text':'PRIVATE_SENTINEL'})+'\n'+json.dumps(valid_result)+'\n',
            json.dumps(init)+'\n'+json.dumps({'type':'metadata','text':'PRIVATE_SENTINEL'})+'\n'+json.dumps(valid_result)+'\n',
            json.dumps(init)+'\n'+json.dumps({'type':'text','text':False})+'\n'+json.dumps(valid_result)+'\n',
            json.dumps(init)+'\n\n'+json.dumps(valid_result)+'\n',
            json.dumps(init)+'\n'+json.dumps(valid_result|{'text':False})+'\n',
            json.dumps({'type':'system','subtype':'init','session_id':''})+'\n'+json.dumps(valid_result)+'\n',
        )
        for raw in cases:
            with self.subTest(raw_length=len(raw)),self.assertRaisesRegex(ValueError,'^event_stream_invalid$') as caught:
                parse_stream_envelope(raw,0)
            self.assertNotIn('PRIVATE_SENTINEL',str(caught.exception))
        for code in (False,1):
            with self.subTest(code=code),self.assertRaisesRegex(ValueError,'^event_stream_invalid$'):
                parse_stream_envelope(stream(),code)

    def test_stream_inner_decision_remains_strict(self):
        raw=stream(decision='not authoritative',events=({'type':'text','text':json.dumps(DECISION)},))
        text=parse_stream_envelope(raw,0)
        with self.assertRaises((TypeError,ValueError)):
            parse_results(text,[EVENT],decisions=True)

    def test_stream_failure_never_creates(self):
        with patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
             patch('local_agentic_triage.events.calendar.Calendar.create') as create, \
             patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,'PRIVATE_SENTINEL','PRIVATE_SENTINEL')):
            with self.assertRaises(ValueError) as caught:
                process([EVENT],make_config())
        create.assert_not_called()
        self.assertNotIn('PRIVATE_SENTINEL',str(caught.exception))

    def test_later_error_blocks_earlier_authorized_create(self):
        events=[EVENT,EVENT|{'title':'Second private title','start':'2026-12-02'}]
        decision={'status':'error','results':[
            {'index':0,'outcome':'create','reason':None,'estimated_end':None},
            {'index':1,'outcome':'error','reason':'search_failed','estimated_end':None},
        ]}
        with patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
             patch('local_agentic_triage.events.calendar.Calendar.create') as create, \
             patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,stream(decision),'')):
            with self.assertRaisesRegex(ValueError,'^event_processing_failed$'):
                process(events,make_config())
        create.assert_not_called()

    def test_aggregate_stream_limit_blocks_before_create(self):
        with patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
             patch('local_agentic_triage.events.calendar.Calendar.create') as create, \
             patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,'x'*1000001,'')):
            with self.assertRaisesRegex(ValueError,'^event_result_size$'):
                process([EVENT],make_config())
        create.assert_not_called()

    def test_aggregate_stream_limit_counts_utf8_bytes(self):
        records:list[dict]=[{'type':'system','subtype':'init','session_id':'session-1'}]
        records.extend({'type':'text','text':'🙂'*1000} for _ in range(250))
        records.append({'type':'result','session_id':'session-1','exit_code':0,
                        'text':json.dumps(DECISION)})
        raw='\n'.join(json.dumps(record,ensure_ascii=False) for record in records)+'\n'
        self.assertLess(len(raw),1000000)
        self.assertGreater(len(raw.encode('utf-8')),1000000)
        with patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
             patch('local_agentic_triage.events.calendar.Calendar.create') as create, \
             patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,raw,'')):
            with self.assertRaisesRegex(ValueError,'^event_result_size$'):
                process([EVENT],make_config())
        create.assert_not_called()
