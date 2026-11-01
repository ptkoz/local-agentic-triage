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
                for prompt in ('classifier/destination.md','events/extraction.md','events/duplicate.md','events/completion.md'):
                    self.assertIn('local_agentic_triage/'+prompt,z.namelist())
                self.assertFalse(any('__pycache__' in n for n in z.namelist()))
            result=subprocess.run([sys.executable,str(archive),'--help'],capture_output=True,text=True)
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
