import json
import subprocess
import unittest
from unittest.mock import patch
from helpers import make_config
from local_agentic_triage.events.core import process, parse_results, prompt, parse_stream_envelope

EVENT={'title':'Private title','start':'2026-12-01','end':None,'date_note':None}
DECISION={'status':'ok','results':[{'index':0,'outcome':'existing','reason':None}]}

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
        for text in ('configured calendar','untrusted','7 days','readback','missing end','different title','strict JSON'):
            self.assertIn(text,p)
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
            {'index':0,'outcome':'create','reason':None},
            {'index':1,'outcome':'error','reason':'search_failed'},
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
