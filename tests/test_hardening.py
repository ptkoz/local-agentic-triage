import json
import unittest
from unittest.mock import Mock,patch
from local_agentic_triage.classifier.core import classify,validate
from local_agentic_triage.mail.mime import parse
from local_agentic_triage.common.config import load
from pathlib import Path
import tempfile

class HardeningTests(unittest.TestCase):
    def test_date_formats_and_mixed_ranges(self):
        base={'destination':'leave','summary':None,'events':[]}
        for start,end in [('2026-W01-1',None),('2026-12-01','2026-12-01T18:00:00+01:00'),('2026-12-01T18:00:00+01:00','2026-12-02'),('2026-12-01T18:00:00',None)]:
            with self.subTest(start=start,end=end),self.assertRaises(ValueError):
                validate(base|{'events':[{'title':'X','start':start,'end':end,'date_note':None}]})
    def test_dmr_bypasses_proxies_and_redirects_and_passes_metadata(self):
        response=Mock(); response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"destination":"leave","summary":null,"events":[]}'}}]}).encode()
        opener=Mock(); opener.open.return_value=response
        with patch('urllib.request.build_opener',return_value=opener) as build,patch('urllib.request.urlopen',side_effect=AssertionError('unsafe open')):
            mail=parse(b'From: sender\nSubject: title\nDate: Wed, 01 Jan 2025 12:00:00 +0100\n\nbody',1000)
            classify(mail)
            handlers=build.call_args.args
            self.assertEqual(handlers[0].proxies,{})
            with self.assertRaises(ValueError): handlers[1].redirect_request(None,None,302,'','', 'https://example.invalid')
            payload=json.loads(opener.open.call_args.args[0].data)
            metadata=json.loads(payload['messages'][1]['content'])
            self.assertEqual(metadata['subject'],'title')
            self.assertEqual(metadata['sender'],'sender')
    def test_invalid_config_values(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.toml'
            for value in ('inbox=""','database="relative.sqlite"','discord_limit=0','timeout=true','credential_command=[]','imap_host="https://bad"'):
                p.write_text(value)
                with self.subTest(value=value),self.assertRaises(ValueError): load(p)
