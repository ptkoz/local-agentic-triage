import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import build

ROOT=Path(__file__).resolve().parents[1]
class PackagingTests(unittest.TestCase):
    def test_build_zip_and_offline_invocation(self):
        with tempfile.TemporaryDirectory() as d:
            archive=Path(d)/'triage.pyz'
            result=subprocess.run([sys.executable,str(ROOT/'scripts/build.py'),'--output',str(archive)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            with zipfile.ZipFile(archive) as z:
                self.assertIn('local_agentic_triage/classifier/prompt.md',z.namelist())
                for prompt in ('classifier/destination.md','classifier/summary.md','events/extraction.md','events/duplicate.md','events/completion.md','classifier/destination.json','classifier/summary.json','events/extraction.json','events/duplicate.json','events/completion.json'):
                    self.assertIn('local_agentic_triage/'+prompt,z.namelist())
                self.assertFalse(any('__pycache__' in n for n in z.namelist()))
            result=subprocess.run([sys.executable,str(archive),'--help'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            code="import sys; sys.path.insert(0,sys.argv[1]); from local_agentic_triage.classifier.core import CLASSIFICATION_SCHEMA,SUMMARY_SCHEMA; from local_agentic_triage.events.core import EXTRACTION_SCHEMA,DUPLICATE_SCHEMA,COMPLETION_SCHEMA; assert all(s['json_schema']['strict'] for s in (CLASSIFICATION_SCHEMA,SUMMARY_SCHEMA,EXTRACTION_SCHEMA,DUPLICATE_SCHEMA,COMPLETION_SCHEMA))"
            result=subprocess.run([sys.executable,'-I','-c',code,str(archive)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            config=Path(d)/'config.toml'; config.write_text(f'database="{d}/db"\npoll_lock="{d}/poll.lock"\nreport_lock="{d}/report.lock"\n')
            result=subprocess.run([sys.executable,str(archive),'--config',str(config),'report','send'],capture_output=True,text=True)
            self.assertEqual(result.returncode,2,result.stderr)
    def test_install_temp_home_only_and_no_auto_load(self):
        with tempfile.TemporaryDirectory() as d:
            result=subprocess.run([sys.executable,str(ROOT/'scripts/install.py'),'--home',d],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            home=Path(d).resolve(); d=str(home); data=home/'Library/Application Support/local-agentic-triage'
            self.assertEqual(data.stat().st_mode&0o777,0o700)
            config=data/'config.toml'
            self.assertEqual(config.stat().st_mode&0o777,0o600)
            content=config.read_text()
            self.assertNotIn('@HOME@',content)
            self.assertIn('@ACCOUNT@',content)
            from local_agentic_triage.common.config import load
            with self.assertRaises(ValueError): load(config)
            cache=home/'Library/Caches/local-agentic-triage'
            self.assertEqual(cache.stat().st_mode&0o777,0o700)
            config.write_text('# preserved\n')
            subprocess.run([sys.executable,str(ROOT/'scripts/install.py'),'--home',d],check=True,capture_output=True)
            self.assertEqual(config.read_text(),'# preserved\n')
            plists=list((home/'Library/LaunchAgents').glob('*.plist'))
            self.assertEqual(len(plists),2)
            for p in plists:
                info=plistlib.loads(p.read_bytes())
                self.assertEqual(info['EnvironmentVariables']['HOME'],d)
                self.assertEqual(info['EnvironmentVariables']['HERMES_HOME'],d+'/.hermes')
                self.assertNotIn('@HOME@',p.read_text()); self.assertNotIn('@PYTHON@',p.read_text())
                self.assertFalse(info.get('RunAtLoad',False))
                self.assertIn(str(home/'.local/libexec/local-agentic-triage/local-agentic-triage.pyz'),info['ProgramArguments'])
                if p.name.endswith('report.plist'): self.assertIn('--deliver',info['ProgramArguments'])
            self.assertNotIn('launchctl', (ROOT/'scripts/install.py').read_text())

    def test_failed_build_preserves_existing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'triage.pyz'; target.write_bytes(b'existing')
            def fail(_source,output,**_kwargs):
                Path(output).write_bytes(b'partial')
                raise RuntimeError('synthetic failure')
            with patch.object(build.zipapp,'create_archive',side_effect=fail),self.assertRaises(RuntimeError):
                build.build(target)
            self.assertEqual(target.read_bytes(),b'existing')

    def test_frozen_resources_match_reviewed_baseline(self):
        import hashlib
        from importlib.resources import files
        expected={
            'classifier/destination.json': '02cdef55c33c3cad5a51455b47ba4068242c5c81547d9a609fc871ca22d037c6',
            'classifier/destination.md': '387648d89455521af54121888be33c9ea4e6df2a69da181168105274f670db4a',
            'classifier/prompt.md': '12a7044a41d3acc9b173ad69a71a55f3e8783399eaee291057460a1075aecf6c',
            'classifier/summary.json': '7778048986365c1f57273fbef05f9ff773310bac8ccc8f371357c5d8c392b06f',
            'classifier/summary.md': 'a16a8dfffbcc0852b41a9e3b00b2447622128568a2e13cc51cc1fd5030ceb233',
            'events/completion.json': '04110de9593868434971c4490d7b877202e055b5bbd25783afe7d9b5f0a4f85f',
            'events/completion.md': '7ce3e967ea94f5d5d21554138b922fcdec8e4ecc2b9b1e28de04ab42d5960ca8',
            'events/duplicate.json': 'bd7f0ec4d72545eab7d4af81f7e548bcff334475b6fe9331645c85c9313173c3',
            'events/duplicate.md': '20e7d321d2c0c9c12122df351ecac86da26338d41dab3ee1266fe6bf3d0966be',
            'events/extraction.json': 'a9160a341ed35135f0331dcc902364e1de8324e2a1a5658128505c9c31c07644',
            'events/extraction.md': 'b0aee6d79c104eed3559d52af1145cafbb0f6b9b5a4ea0e21ccaee31b7d506d3'}
        for name,digest in expected.items():
            with self.subTest(resource=name):
                self.assertEqual(hashlib.sha256(files('local_agentic_triage').joinpath(name).read_bytes()).hexdigest(),digest)
