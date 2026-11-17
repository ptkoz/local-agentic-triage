import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_build_zip_and_offline_invocation(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / 'triage.pyz'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/build.py'), '--output', str(archive)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with zipfile.ZipFile(archive) as z:
                for prompt in ('classifier/summarize_prompt.md', 'classifier/classify_prompt.md',
                               'classifier/extract_events_prompt.md', 'classifier/is_duplicate_prompt.md',
                               'classifier/complete_event_prompt.md',
                               'classifier/summarize_output_schema.json', 'classifier/classify_output_schema.json',
                               'classifier/extract_events_output_schema.json', 'classifier/is_duplicate_output_schema.json',
                               'classifier/complete_event_output_schema.json'):
                    self.assertIn('local_agentic_triage/' + prompt, z.namelist())
                self.assertFalse(any('__pycache__' in n for n in z.namelist()))
            result = subprocess.run([sys.executable, str(archive), '--help'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            code = ("import sys; sys.path.insert(0,sys.argv[1]); "
                    "from local_agentic_triage.classifier.resources import (SUMMARIZE_OUTPUT_SCHEMA,CLASSIFY_OUTPUT_SCHEMA,EXTRACT_EVENTS_OUTPUT_SCHEMA,IS_DUPLICATE_OUTPUT_SCHEMA,COMPLETE_EVENT_OUTPUT_SCHEMA); "
                    "assert all(s['json_schema']['strict'] for s in "
                    "(SUMMARIZE_OUTPUT_SCHEMA,CLASSIFY_OUTPUT_SCHEMA,EXTRACT_EVENTS_OUTPUT_SCHEMA,IS_DUPLICATE_OUTPUT_SCHEMA,COMPLETE_EVENT_OUTPUT_SCHEMA))")
            result = subprocess.run([sys.executable, '-I', '-c', code, str(archive)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            # report command is removed; argparse exits 2 for unrecognised subcommand
            config = Path(d) / 'config.toml'
            config.write_text(f'poll_lock="{d}/poll.lock"\n')
            result = subprocess.run([sys.executable, str(archive), '--config', str(config), 'report', 'send'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_no_sqlite_no_store_module(self):
        """Store module must not exist; reporting package exposes only render_notification and deliver."""
        store_path = ROOT / 'src/local_agentic_triage/reporting/store.py'
        self.assertFalse(store_path.exists(), 'store.py should be deleted')
        from local_agentic_triage.reporting import report
        self.assertTrue(hasattr(report, 'render_notification'))
        self.assertTrue(hasattr(report, 'deliver'))
        self.assertFalse(hasattr(report, 'send'))
        self.assertFalse(hasattr(report, 'header'))
        self.assertFalse(hasattr(report, 'record_message'))

    def test_install_creates_only_poll_plist_and_no_report_log(self):
        with tempfile.TemporaryDirectory() as d:
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/install.py'), '--home', d],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            home = Path(d).resolve()
            data = home / 'Library/Application Support/local-agentic-triage'
            self.assertEqual(data.stat().st_mode & 0o777, 0o700)
            config = data / 'config.toml'
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            content = config.read_text()
            self.assertNotIn('@HOME@', content)
            self.assertIn('@ACCOUNT@', content)
            from local_agentic_triage.common.config import load
            with self.assertRaises(ValueError):
                load(config)
            cache = home / 'Library/Caches/local-agentic-triage'
            self.assertEqual(cache.stat().st_mode & 0o777, 0o700)
            # Preserve existing config
            config.write_text('# preserved\n')
            subprocess.run([sys.executable, str(ROOT / 'scripts/install.py'), '--home', str(home)],
                           check=True, capture_output=True)
            self.assertEqual(config.read_text(), '# preserved\n')
            # Only poll plist, no report plist
            plists = list((home / 'Library/LaunchAgents').glob('*.plist'))
            self.assertEqual(len(plists), 1, f'expected 1 plist, got {[p.name for p in plists]}')
            p = plists[0]
            self.assertTrue(p.name.endswith('poll.plist'), p.name)
            info = plistlib.loads(p.read_bytes())
            self.assertEqual(info['EnvironmentVariables']['HOME'], str(home))
            self.assertEqual(info['EnvironmentVariables']['HERMES_HOME'], str(home) + '/.hermes')
            self.assertNotIn('@HOME@', p.read_text())
            self.assertNotIn('@PYTHON@', p.read_text())
            self.assertFalse(info.get('RunAtLoad', False))
            self.assertIn(str(home / '.local/libexec/local-agentic-triage/local-agentic-triage.pyz'),
                          info['ProgramArguments'])
            self.assertNotIn('--deliver', info['ProgramArguments'])
            # Only poll.log, no report.log
            logs = home / 'Library/Logs/local-agentic-triage'
            self.assertTrue((logs / 'poll.log').exists())
            self.assertFalse((logs / 'report.log').exists())
            self.assertNotIn('launchctl', (ROOT / 'scripts/install.py').read_text())

    def test_upgrade_removes_legacy_reporter_and_databases_but_preserves_config(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d).resolve()
            data = home / 'Library/Application Support/local-agentic-triage'
            agents = home / 'Library/LaunchAgents'
            logs = home / 'Library/Logs/local-agentic-triage'
            for path in (data, agents, logs):
                path.mkdir(parents=True, exist_ok=True)
            config = data / 'config.toml'
            config.write_text('# preserved\n')
            obsolete = [
                agents / 'local.local-agentic-triage.report.plist',
                logs / 'report.log',
                data / 'triage.sqlite3',
                data / 'triage.sqlite3-wal',
                data / 'triage.sqlite3-shm',
                data / 'triage.sqlite3.init.lock',
                data / 'reports.sqlite3',
                data / 'reports.sqlite3-wal',
                data / 'reports.sqlite3-shm',
            ]
            for path in obsolete:
                path.write_bytes(b'legacy')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/install.py'),
                                     '--home', str(home)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_text(), '# preserved\n')
            self.assertTrue((agents / 'local.local-agentic-triage.poll.plist').exists())
            for path in obsolete:
                self.assertFalse(path.exists(), path)

    def test_upgrade_runbook_removes_legacy_config_before_bootstrap(self):
        readme = (ROOT / 'README.md').read_text()
        poll = readme.index('launchctl bootout gui/$(id -u)/local.local-agentic-triage.poll')
        report = readme.index('launchctl bootout gui/$(id -u)/local.local-agentic-triage.report')
        install = readme.index('python3.14 scripts/install.py --python', poll)
        remove = readme.index('Remove these obsolete keys from the preserved config', install)
        validate = readme.index('local-agentic-triage.pyz config validate', remove)
        bootstrap = readme.index('Bootstrap only `local.local-agentic-triage.poll.plist`', validate)
        self.assertLess(poll, report)
        self.assertLess(report, install)
        self.assertLess(install, remove)
        self.assertLess(remove, validate)
        self.assertLess(validate, bootstrap)
        for key in ('database', 'report_lock', 'delivery_lock', 'discord_user_id'):
            self.assertIn(f'`{key}`', readme[remove:validate])
        self.assertIn('removes the obsolete reporter plist, report log, and SQLite files', readme)

    def test_documentation_places_render_validation_before_optional_side_effects(self):
        architecture = (ROOT / 'docs/architecture.md').read_text()
        readme = (ROOT / 'README.md').read_text()
        render = architecture.index('Render and validate the per-mail Discord notification.')
        optional = architecture.index('Run optional Calendar enrichment and approved sender-specific handoffs.')
        deliver = architecture.index('Deliver the notification to Discord.')
        disposition = architecture.index('Apply the selected mail disposition.')
        self.assertLess(render, optional)
        self.assertLess(optional, deliver)
        self.assertLess(deliver, disposition)
        self.assertIn('Eligibility is determined from', architecture)
        self.assertIn('the event end, not its start', architecture)
        self.assertIn('72 elapsed hours', architecture)
        self.assertIn('three local calendar days', architecture)
        self.assertIn('after the mandatory notification has been rendered and validated', readme)

    def test_failed_build_preserves_existing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'triage.pyz'
            target.write_bytes(b'existing')

            def fail(_source, output, **_kwargs):
                Path(output).write_bytes(b'partial')
                raise RuntimeError('synthetic failure')

            with patch.object(build.zipapp, 'create_archive', side_effect=fail), \
                 self.assertRaises(RuntimeError):
                build.build(target)
            self.assertEqual(target.read_bytes(), b'existing')

    def test_frozen_resources_match_reviewed_baseline(self):
        import hashlib
        from importlib.resources import files
        expected = {
            'classifier/classify_output_schema.json': '02cdef55c33c3cad5a51455b47ba4068242c5c81547d9a609fc871ca22d037c6',
            'classifier/classify_prompt.md': '387648d89455521af54121888be33c9ea4e6df2a69da181168105274f670db4a',
            'classifier/summarize_output_schema.json': '7778048986365c1f57273fbef05f9ff773310bac8ccc8f371357c5d8c392b06f',
            'classifier/summarize_prompt.md': 'a16a8dfffbcc0852b41a9e3b00b2447622128568a2e13cc51cc1fd5030ceb233',
            'classifier/complete_event_output_schema.json': '04110de9593868434971c4490d7b877202e055b5bbd25783afe7d9b5f0a4f85f',
            'classifier/complete_event_prompt.md': '7ce3e967ea94f5d5d21554138b922fcdec8e4ecc2b9b1e28de04ab42d5960ca8',
            'classifier/is_duplicate_output_schema.json': 'bd7f0ec4d72545eab7d4af81f7e548bcff334475b6fe9331645c85c9313173c3',
            'classifier/is_duplicate_prompt.md': '20e7d321d2c0c9c12122df351ecac86da26338d41dab3ee1266fe6bf3d0966be',
            'classifier/extract_events_output_schema.json': 'a9160a341ed35135f0331dcc902364e1de8324e2a1a5658128505c9c31c07644',
            'classifier/extract_events_prompt.md': 'b0aee6d79c104eed3559d52af1145cafbb0f6b9b5a4ea0e21ccaee31b7d506d3'}
        for name, digest in expected.items():
            with self.subTest(resource=name):
                self.assertEqual(
                    hashlib.sha256(files('local_agentic_triage').joinpath(name).read_bytes()).hexdigest(),
                    digest)
