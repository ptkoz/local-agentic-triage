import unittest
import copy
import inspect
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from local_agentic_triage.mail.mime import Mail, parse
from local_agentic_triage.classifier import input as classifier_input
from local_agentic_triage.classifier.input import ContextBoundMail, fit_to_context
from local_agentic_triage.classifier.disposition import classify, summarize
from local_agentic_triage.classifier import helpers as classifier_helpers
from local_agentic_triage.classifier.helpers import (CLASSIFICATION_SCHEMA,
    SUMMARY_SCHEMA, ModelContentJsonInvalid, ModelEnvelopeJsonInvalid,
    disposition_prompt)
from unittest.mock import patch
import json
from local_agentic_triage.common.diagnostics import error_code

class ContentTests(unittest.TestCase):
    def test_classifier_disposition_exposes_only_domain_functions(self):
        import local_agentic_triage.classifier.disposition as core
        functions = {
            name for name, value in inspect.getmembers(core, inspect.isfunction)
            if value.__module__ == core.__name__
        }
        self.assertEqual(functions, {
            'validate_destination', 'validate_summary', 'summarize', 'classify',
        })

    def test_mime(self):
        raw=b'Date: Wed, 01 Jan 2025 12:00:00 +0100\nFrom: A <a@example.invalid>\nContent-Type: multipart/mixed; boundary=x\n\n--x\nContent-Type: text/html\n\n<p>Hello &amp; world</p><script>evil</script>\n--x\nContent-Type: text/plain\nContent-Disposition: attachment\n\nSECRET\n--x--\n'
        m=parse(raw,1000)
        self.assertEqual(m.body,'Hello & world')
        self.assertEqual(m.sent_date,'2025-01-01T12:00:00+01:00')
        self.assertEqual(m.sender_address,'a@example.invalid')
        self.assertNotIn('SECRET',m.body)
        with self.assertRaises(ValueError): parse(raw,2)
    def test_plain_preferred(self):
        raw=b'Content-Type: multipart/alternative; boundary=x\n\n--x\nContent-Type: text/html\n\n<b>HTML</b>\n--x\nContent-Type: text/plain\n\nplain\n--x--'
        self.assertEqual(parse(raw,1000).body,'plain')

    def test_summary_is_validated_at_summary_boundary(self):
        from local_agentic_triage.classifier.disposition import validate_summary
        with self.assertRaises(ValueError):
            validate_summary({'summary':''})

    def test_local_request_and_truncation(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def read(self,n=None): return json.dumps({'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}).encode()
        with patch('urllib.request.OpenerDirector.open',return_value=Response()) as call:
            with self.assertRaises(ValueError): summarize(fit_to_context(parse(b'Date: today\n\nbody',100),10000))
            req=call.call_args.args[0]; data=json.loads(req.data)
            self.assertEqual(req.full_url,'http://127.0.0.1:12434/engines/v1/chat/completions')
            self.assertEqual(data['model'],'docker.io/ai/ministral3-mail-8b:16k')
            self.assertEqual(data['max_tokens'],2048)
            self.assertEqual(call.call_args.kwargs['timeout'],300)
            self.assertNotIn('chat_template_kwargs',data)
            self.assertEqual(set(data['response_format']['json_schema']['schema']['properties']),{'summary'})
            self.assertEqual(data['messages'][1]['content'],'{"metadata":{"sent_date":"","sender":"","subject":""},"body":"body"}')

    def test_summarize_and_classify_are_separate_requests(self):
        import local_agentic_triage.classifier.disposition as core
        mail=fit_to_context(parse(b'Date: Wed, 01 Jan 2025 12:00:00 +0100\nSubject: Synthetic\n\nbody',100),10000)
        with patch.object(classifier_helpers,'chat_completion',side_effect=[
                {'summary':'Syntetyczne podsumowanie.'},{'destination':'leave'}]) as chat:
            summary=summarize(mail)
            self.assertEqual(summary,'Syntetyczne podsumowanie.')
            self.assertEqual(classify(mail,summary),'leave')
        self.assertEqual(chat.call_count,2)
        self.assertIs(chat.call_args_list[0].args[1],SUMMARY_SCHEMA)
        self.assertIs(chat.call_args_list[1].args[1],CLASSIFICATION_SCHEMA)
        source={'metadata':{'sent_date':'2025-01-01T12:00:00+01:00','sender':'','subject':'Synthetic'},'body':'body'}
        self.assertEqual(chat.call_args_list[0].args[0][1]['content'],
            '{"metadata":{"sent_date":"2025-01-01T12:00:00+01:00","sender":"","subject":"Synthetic"},"body":"body"}')
        self.assertEqual(chat.call_args_list[1].args[0][1]['content'],
            '{"summary": "Syntetyczne podsumowanie.", "mail": '
            '{"metadata": {"sent_date": "2025-01-01T12:00:00+01:00", "sender": "", "subject": "Synthetic"}, "body": "body"}}')
        self.assertEqual(json.loads(chat.call_args_list[1].args[0][1]['content'])['mail'],source)

        with patch.object(classifier_helpers,'chat_completion',return_value={'destination':'archive'}) as chat:
            self.assertEqual(classify(mail,'Syntetyczne podsumowanie.'),'archive')
        self.assertEqual(chat.call_count,1)
        self.assertEqual(chat.call_args.args[0][0]['content'],disposition_prompt())


    def test_http_requests_serialize_exact_source_without_recipient(self):
        raw=(b'From: =?utf-8?q?Zo=C5=BCa?= <sender@example.invalid>\nTo: recipient@example.invalid\n'
             b'Subject: Synthetic\nDate: Wed, 01 Jan 2025 12:00:00 +0100\n\nSynthetic body')
        mail=fit_to_context(parse(raw,1000),10000)
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
            summary=summarize(mail)
            result=classify(mail,summary)
            from local_agentic_triage.classifier import events
            events.extract_events(mail)
        requests=[json.loads(c.args[0].data) for c in call.call_args_list]
        self.assertEqual([r['messages'][1]['content'] for r in requests],[
            source,'{"summary": "Syntetyczny opis.", "mail": {"metadata": {"sent_date": "2025-01-01T12:00:00+01:00", "sender": "Zoża <sender@example.invalid>", "subject": "Synthetic"}, "body": "Synthetic body"}}',source])
        self.assertEqual(json.loads(requests[1]['messages'][1]['content'])['mail'],json.loads(source))
        for request in requests:
            self.assertNotIn('recipient',json.dumps(request))
        self.assertEqual(result,'archive')

    def test_classify_does_not_revalidate_summary(self):
        import local_agentic_triage.classifier.disposition as core
        mail=fit_to_context(parse(b'From: sender@example.invalid\n\nSynthetic',1000),10000)
        with patch.object(classifier_helpers,'chat_completion',return_value={'destination':'archive'}), \
             patch.object(core,'validate_summary',side_effect=AssertionError('redundant validation')):
            self.assertEqual(classify(mail,'Opis.'),'archive')

    def test_classifier_distinguishes_envelope_and_content_json(self):
        class Response:
            def __init__(self,payload): self.payload=payload
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def read(self,n=None): return self.payload
        mail=fit_to_context(parse(b'Date: Wed, 01 Jan 2025 12:00:00 +0100\n\nbody',100),10000)
        with patch('urllib.request.OpenerDirector.open',return_value=Response(b'not json')):
            with self.assertRaises(ModelEnvelopeJsonInvalid): summarize(mail)
        for envelope in ({}, {'choices':[]}, {'choices':[{}]},
                         {'choices':[{'finish_reason':'stop','message':{}}]},
                         {'choices':[{'finish_reason':'stop','message':False}]}):
            with self.subTest(envelope=envelope), \
                 patch('urllib.request.OpenerDirector.open',return_value=Response(json.dumps(envelope).encode())), \
                 self.assertRaises(ModelEnvelopeJsonInvalid):
                summarize(mail)
        envelope=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'not json'}}]}).encode()
        with patch('urllib.request.OpenerDirector.open',return_value=Response(envelope)):
            with self.assertRaises(ModelContentJsonInvalid): summarize(mail)

        envelope_cases=(b'{"a":1,"a":2}',b'NaN',b'\xff',{'not':'json text'})
        for payload in envelope_cases:
            with self.subTest(boundary='envelope',payload=type(payload).__name__):
                with patch('urllib.request.OpenerDirector.open',return_value=Response(payload)):
                    with self.assertRaises(ModelEnvelopeJsonInvalid) as caught: summarize(mail)
                self.assertEqual(error_code(caught.exception),'model_envelope_json_invalid')
        content_cases=('{"a":1,"a":2}','NaN',{'not':'json text'})
        for content in content_cases:
            envelope=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':content}}]}).encode()
            with self.subTest(boundary='content',content=type(content).__name__):
                with patch('urllib.request.OpenerDirector.open',return_value=Response(envelope)):
                    with self.assertRaises(ModelContentJsonInvalid) as caught: summarize(mail)
                self.assertEqual(error_code(caught.exception),'model_content_json_invalid')

    def test_context_bound_mail_decorates_the_original_mail(self):
        mail=parse(b'From: sender@example.invalid\nSubject: Synthetic\n\n'+b'b'*20000,0)
        context_bound_mail=fit_to_context(mail,10000)
        self.assertIsInstance(context_bound_mail,ContextBoundMail)
        self.assertIs(context_bound_mail.mail,mail)
        for field in fields(Mail):
            with self.subTest(field=field.name):
                self.assertIs(getattr(context_bound_mail,field.name),getattr(mail,field.name))
        with self.assertRaises(FrozenInstanceError):
            context_bound_mail.body='changed'
        self.assertEqual(copy.copy(context_bound_mail),context_bound_mail)

    def test_context_bound_mail_keeps_model_state_private(self):
        mail=parse(b'From: sender@example.invalid\nSubject: Synthetic\n\nbody',1000)
        context_bound_mail=fit_to_context(mail,10000)
        with patch.object(classifier_helpers,'chat_completion',side_effect=[
                {'summary':'Opis.'},{'destination':'leave'}]):
            classify(context_bound_mail,summarize(context_bound_mail))
        self.assertEqual({name for name in vars(context_bound_mail) if not name.startswith('_')},
                         {'mail','limit'})
        for name in ('source','source_json'):
            self.assertFalse(hasattr(context_bound_mail,name))
        self.assertFalse(hasattr(classifier_input,'FrozenDict'))

    def test_model_json_bounds_every_mail_field_and_fails_closed(self):
        mail=Mail(sender='s'*200001,sender_address='s'*200001,sent_date='d'*200001,body='b'*200001,subject='t'*200001)
        context_bound_mail=fit_to_context(mail,10000)
        source=classifier_input.serialize_mail(context_bound_mail)
        self.assertLessEqual(len(source.encode()),10000)
        fitted=json.loads(source)
        self.assertEqual(set(fitted),{'metadata','body'})
        self.assertEqual(set(fitted['metadata']),{'sent_date','sender','subject'})
        for field,value in (*fitted['metadata'].items(),('body',fitted['body'])):
            with self.subTest(field=field):
                self.assertLess(len(value),200001)
                self.assertTrue(value.endswith('[content truncated]'))
                self.assertIs(getattr(context_bound_mail,field),getattr(mail,field))
        with patch.object(classifier_helpers,'chat_completion') as chat, \
             self.assertRaisesRegex(ValueError,'mail payload size'):
            summarize(fit_to_context(mail,1))
        chat.assert_not_called()
        with patch.object(classifier_helpers,'chat_completion') as chat, \
             self.assertRaisesRegex(ValueError,'mail payload size'):
            classify(fit_to_context(mail,1),'Opis.')
        chat.assert_not_called()

    def test_context_fitting_is_lazy_and_shared_by_every_serialization(self):
        mail=parse(b'From: sender@example.invalid\nSubject: Synthetic\n\nbody',1000)
        with patch.object(classifier_input,'_bounded_string',
                          wraps=classifier_input._bounded_string) as bound:
            context_bound_mail=fit_to_context(mail,10000)
            bound.assert_not_called()
            classifier_input.serialize_mail(context_bound_mail)
            classifier_input.serialize_destination_input(context_bound_mail,'Opis.')
            classifier_input.serialize_mail(context_bound_mail)
        self.assertEqual(bound.call_count,4)

    def test_model_consumers_serialize_the_same_decorated_mail(self):
        from local_agentic_triage.classifier import events
        mail=Mail(sender='s'*5000,sender_address='s'*5000,sent_date='2025-01-01T12:00:00+01:00',body='b'*20000,subject='t'*5000)
        context_bound_mail=fit_to_context(mail,10000)
        with patch.object(classifier_helpers,'chat_completion',side_effect=[
                {'summary':'Opis.'},{'destination':'leave'}]) as chat, \
             patch.object(events,'_chat_completion',return_value={'events':[]}) as extraction:
            classify(context_bound_mail,summarize(context_bound_mail))
            events.extract_events(context_bound_mail)
        source=classifier_input.serialize_mail(context_bound_mail)
        summary_content,destination_content=(c.args[0][1]['content'] for c in chat.call_args_list)
        self.assertEqual(summary_content,source)
        self.assertEqual(extraction.call_args.args[0][1]['content'],source)
        self.assertEqual(destination_content,
                         classifier_input.serialize_destination_input(context_bound_mail,'Opis.'))
        self.assertEqual(json.loads(destination_content),{'summary':'Opis.','mail':json.loads(source)})
