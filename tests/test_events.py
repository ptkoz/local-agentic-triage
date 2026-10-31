import json
import subprocess
import unittest
from unittest.mock import patch
from helpers import make_config
from local_agentic_triage.events.core import process, parse_results, prompt

EVENT={'title':'Private title','start':'2026-12-01','end':None,'date_note':None}
class EventTests(unittest.TestCase):
    def test_results_exact_coverage(self):
        for status in ('created','existing','skipped'):
            self.assertTrue(parse_results(json.dumps({'status':'ok','results':[{'index':0,'outcome':status,'reason':'possible_duplicate' if status=='skipped' else None}]}),[EVENT]))
        for result in ({'outcomes':[]},{'outcomes':[{'index':0,'status':'error'}]},{'outcomes':[{'index':0,'status':'created','extra':1}]},{'outcomes':[{'index':0,'status':'created'},{'index':0,'status':'existing'}]}):
            with self.assertRaises(ValueError): parse_results(json.dumps(result),[EVENT])
        with self.assertRaises(ValueError): parse_results('{"outcomes":[],"outcomes":[]}',[])
        with self.assertRaises(ValueError): parse_results(json.dumps({'outcomes':[{'index':0,'status':'created'}]}),[EVENT|{'start':None}])
    def test_stdin_privacy_and_arguments(self):
        with patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,json.dumps({'status':'ok','results':[{'index':0,'outcome':'existing','reason':None}]}),'secret stderr')) as run:
            process([EVENT],make_config(calendar='Only This Calendar'))
            args=run.call_args.args[0]
            self.assertEqual(args[1:],['chat','--query-file','-','--oneshot','--quiet','--source','tool','--toolsets','none','--ignore-rules','--max-turns','20','--run-budget','120'])
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
