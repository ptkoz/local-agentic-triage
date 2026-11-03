import unittest
import hashlib
from pathlib import Path
from local_agentic_triage.mail.mime import parse
from local_agentic_triage.classifier.core import (ModelContentJsonInvalid,
    ModelEnvelopeJsonInvalid, validate, prompt, classify, mail_payload, mail_source)
from unittest.mock import patch
import json
from local_agentic_triage.common.diagnostics import error_code

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
            def read(self,n=None): return json.dumps({'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}).encode()
        with patch('urllib.request.OpenerDirector.open',return_value=Response()) as call:
            with self.assertRaises(ValueError): classify(parse(b'Date: today\n\nbody',100))
            req=call.call_args.args[0]; data=json.loads(req.data)
            self.assertEqual(req.full_url,'http://127.0.0.1:12434/engines/v1/chat/completions')
            self.assertEqual(data['model'],'docker.io/ai/ministral3-mail-8b:16k')
            self.assertEqual(data['max_tokens'],2048)
            self.assertEqual(call.call_args.kwargs['timeout'],300)
            self.assertNotIn('chat_template_kwargs',data)
            self.assertEqual(set(data['response_format']['json_schema']['schema']['properties']),{'summary'})
            self.assertEqual(data['messages'][1]['content'],'{"metadata":{"sent_date":"","sender":"","subject":""},"body":"body"}')

    def test_classify_summarizes_before_destination_with_separate_source(self):
        import local_agentic_triage.classifier.core as core
        mail=parse(b'Date: Wed, 01 Jan 2025 12:00:00 +0100\nSubject: Synthetic\n\nbody',100)
        with patch.object(core,'chat_completion',side_effect=[
                {'summary':'Syntetyczne podsumowanie.'},{'destination':'leave'}]) as chat:
            self.assertEqual(classify(mail),{'destination':'leave','summary':None})
        self.assertEqual(chat.call_count,2)
        self.assertIs(chat.call_args_list[0].args[1],core.SUMMARY_SCHEMA)
        self.assertIs(chat.call_args_list[1].args[1],core.CLASSIFICATION_SCHEMA)
        source={'metadata':{'sent_date':'2025-01-01T12:00:00+01:00','sender':'','subject':'Synthetic'},'body':'body'}
        self.assertEqual(chat.call_args_list[0].args[0][1]['content'],
            '{"metadata":{"sent_date":"2025-01-01T12:00:00+01:00","sender":"","subject":"Synthetic"},"body":"body"}')
        self.assertEqual(chat.call_args_list[1].args[0][1]['content'],
            '{"summary": "Syntetyczne podsumowanie.", "mail": '
            '{"metadata": {"sent_date": "2025-01-01T12:00:00+01:00", "sender": "", "subject": "Synthetic"}, "body": "body"}}')
        self.assertEqual(json.loads(chat.call_args_list[1].args[0][1]['content'])['mail'],source)

        with patch.object(core,'chat_completion',side_effect=[
                {'summary':'Syntetyczne podsumowanie.'},{'destination':'archive'}]) as chat:
            self.assertEqual(classify(mail),{
                'destination':'archive','summary':'Syntetyczne podsumowanie.'})
        self.assertEqual(chat.call_count,2)
        self.assertEqual(chat.call_args_list[0].args[0][0]['content'],core.summary_prompt())
        self.assertEqual(chat.call_args_list[1].args[0][0]['content'],core.disposition_prompt())


    def test_http_requests_serialize_exact_source_without_recipient(self):
        raw=(b'From: =?utf-8?q?Zo=C5=BCa?= <sender@example.invalid>\nTo: recipient@example.invalid\n'
             b'Subject: Synthetic\nDate: Wed, 01 Jan 2025 12:00:00 +0100\n\nSynthetic body')
        mail=parse(raw,1000)
        class Response:
            def __init__(self,content): self.content=content
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self):
                return json.dumps({'choices':[{'finish_reason':'stop','message':{
                    'content':json.dumps(self.content)}}]}).encode()
        source=('{"metadata":{"sent_date":"2025-01-01T12:00:00+01:00","sender":"Zo\u017ca <sender@example.invalid>",'
                '"subject":"Synthetic"},"body":"Synthetic body"}')
        with patch('urllib.request.OpenerDirector.open',side_effect=[
                Response({'summary':'Syntetyczny opis.'}),Response({'destination':'archive'}),
                Response({'events':[]})]) as call:
            result=classify(mail)
            from local_agentic_triage.events import core as events
            events.extract(mail)
        requests=[json.loads(c.args[0].data) for c in call.call_args_list]
        self.assertEqual([r['messages'][1]['content'] for r in requests],[
            source,'{"summary": "Syntetyczny opis.", "mail": {"metadata": {"sent_date": "2025-01-01T12:00:00+01:00", "sender": "Zoża <sender@example.invalid>", "subject": "Synthetic"}, "body": "Synthetic body"}}',source])
        self.assertEqual(json.loads(requests[1]['messages'][1]['content'])['mail'],json.loads(source))
        for request in requests:
            self.assertNotIn('recipient',json.dumps(request))
        self.assertEqual(result,{'destination':'archive','summary':'Syntetyczny opis.'})

    def test_classify_does_not_revalidate_assembled_disposition(self):
        import local_agentic_triage.classifier.core as core
        mail=parse(b'From: sender@example.invalid\n\nSynthetic',1000)
        with patch.object(core,'chat_completion',side_effect=[
                {'summary':'Opis.'},{'destination':'archive'}]), \
             patch.object(core,'validate_disposition',side_effect=AssertionError('redundant validation')):
            self.assertEqual(classify(mail),{'destination':'archive','summary':'Opis.'})

    def test_classifier_distinguishes_envelope_and_content_json(self):
        class Response:
            def __init__(self,payload): self.payload=payload
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def read(self,n=None): return self.payload
        mail=parse(b'Date: Wed, 01 Jan 2025 12:00:00 +0100\n\nbody',100)
        with patch('urllib.request.OpenerDirector.open',return_value=Response(b'not json')):
            with self.assertRaises(ModelEnvelopeJsonInvalid): classify(mail)
        for envelope in ({}, {'choices':[]}, {'choices':[{}]},
                         {'choices':[{'finish_reason':'stop','message':{}}]},
                         {'choices':[{'finish_reason':'stop','message':False}]}):
            with self.subTest(envelope=envelope), \
                 patch('urllib.request.OpenerDirector.open',return_value=Response(json.dumps(envelope).encode())), \
                 self.assertRaises(ModelEnvelopeJsonInvalid):
                classify(mail)
        envelope=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'not json'}}]}).encode()
        with patch('urllib.request.OpenerDirector.open',return_value=Response(envelope)):
            with self.assertRaises(ModelContentJsonInvalid): classify(mail)

        envelope_cases=(b'{"a":1,"a":2}',b'NaN',b'\xff',{'not':'json text'})
        for payload in envelope_cases:
            with self.subTest(boundary='envelope',payload=type(payload).__name__):
                with patch('urllib.request.OpenerDirector.open',return_value=Response(payload)):
                    with self.assertRaises(ModelEnvelopeJsonInvalid) as caught: classify(mail)
                self.assertEqual(error_code(caught.exception),'model_envelope_json_invalid')
        content_cases=('{"a":1,"a":2}','NaN',{'not':'json text'})
        for content in content_cases:
            envelope=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':content}}]}).encode()
            with self.subTest(boundary='content',content=type(content).__name__):
                with patch('urllib.request.OpenerDirector.open',return_value=Response(envelope)):
                    with self.assertRaises(ModelContentJsonInvalid) as caught: classify(mail)
                self.assertEqual(error_code(caught.exception),'model_content_json_invalid')

    def test_model_input_bounds_every_mail_field(self):
        from types import SimpleNamespace
        mail=SimpleNamespace(sent_date='d'*200001,sender='s'*200001,subject='t'*200001,body='b'*200001)
        payload=mail_payload(mail)
        encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        self.assertLessEqual(len(encoded),10000)
        for field in ('sent_date','sender','subject','body'):
            self.assertLess(len(payload[field]),200001)
            self.assertTrue(payload[field].endswith('[content truncated]'))
        source=mail_source(mail)
        self.assertEqual(set(source),{'metadata','body'})
        self.assertEqual(set(source['metadata']),{'sent_date','sender','subject'})
        self.assertLessEqual(len(json.dumps(source,ensure_ascii=False,separators=(',',':')).encode()),10000)
