import unittest
import hashlib
from pathlib import Path
from local_agentic_triage.mail.mime import parse
from local_agentic_triage.classifier.core import validate, prompt, classify, mail_payload
from unittest.mock import patch
import json

class ContentTests(unittest.TestCase):
    def test_mime(self):
        raw=b'Date: Wed, 01 Jan 2025 12:00:00 +0100\nFrom: A <a@example.invalid>\nContent-Type: multipart/mixed; boundary=x\n\n--x\nContent-Type: text/html\n\n<p>Hello &amp; world</p><script>evil</script>\n--x\nContent-Type: text/plain\nContent-Disposition: attachment\n\nSECRET\n--x--\n'
        m=parse(raw,1000)
        self.assertEqual(m.body,'Hello & world')
        self.assertEqual(m.sent_date,'2025-01-01T12:00:00+01:00')
        self.assertNotIn('SECRET',m.body)
        with self.assertRaises(ValueError): parse(raw,2)
    def test_plain_preferred(self):
        raw=b'Content-Type: multipart/alternative; boundary=x\n\n--x\nContent-Type: text/html\n\n<b>HTML</b>\n--x\nContent-Type: text/plain\n\nplain\n--x--'
        self.assertEqual(parse(raw,1000).body,'plain')
    def test_validation(self):
        good={'destination':'archive','summary':'Potwierdzono zakup biletu.','events':[{'title':'Kino','start':'2026-12-01T18:00:00+01:00','end':None,'date_note':None}]}
        self.assertEqual(validate(good),good)
        self.assertEqual(validate(good|{'summary':'x '*100})['summary'],'x '*100)
        self.assertEqual(validate(good|{'summary':''})['summary'],'')
        for change in ({'extra':1},{'summary':None},{'destination':'leave'},{'events':[{'title':'X','start':'2026-99-99','end':None,'date_note':None}]}):
            with self.assertRaises(ValueError): validate(good|change)
        self.assertEqual(validate({'destination':'leave','summary':None,'events':[]})['destination'],'leave')
    def test_exact_prompt(self):
        self.assertEqual(hashlib.sha256(prompt().encode()).hexdigest(),'12a7044a41d3acc9b173ad69a71a55f3e8783399eaee291057460a1075aecf6c')
    def test_local_request_and_truncation(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def read(self,n): return json.dumps({'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}).encode()
        with patch('urllib.request.OpenerDirector.open',return_value=Response()) as call:
            with self.assertRaises(ValueError): classify(parse(b'Date: today\n\nbody',100))
            req=call.call_args.args[0]; data=json.loads(req.data)
            self.assertEqual(req.full_url,'http://127.0.0.1:12434/engines/v1/chat/completions')
            self.assertEqual(data['model'],'docker.io/ai/qwen35-mail:16k')
            self.assertEqual(data['max_tokens'],2048)
            self.assertNotIn('enable_thinking',data)
            self.assertEqual(data['chat_template_kwargs'],{'enable_thinking':False})
            self.assertIn('sent_date',data['messages'][1]['content'])

    def test_model_input_bounds_every_mail_field(self):
        from types import SimpleNamespace
        mail=SimpleNamespace(sent_date='d'*200001,sender='s'*200001,subject='t'*200001,body='b'*200001)
        payload=mail_payload(mail)
        encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        self.assertLessEqual(len(encoded),10000)
        for field in ('sent_date','sender','subject','body'):
            self.assertLess(len(payload[field]),200001)
            self.assertTrue(payload[field].endswith('[content truncated]'))
