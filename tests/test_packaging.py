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

    def test_install_creates_runtime_config_and_poll_agent(self):
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
            logs = home / 'Library/Logs/local-agentic-triage'
            self.assertTrue((logs / 'poll.log').exists())
            self.assertNotIn('launchctl', (ROOT / 'scripts/install.py').read_text())

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
        self.assertIn('(`**Archiwum**` for `archive`, `**Inbox**` for `leave`)', readme)

    def test_documentation_describes_read_only_calendar_and_ical_attachments(self):
        architecture = (ROOT / 'docs/architecture.md').read_text()
        readme = (ROOT / 'README.md').read_text()
        security = (ROOT / 'docs/security.md').read_text()
        self.assertIn('Triage never creates, edits, or deletes Calendar events', architecture)
        self.assertIn('Duplicate and skipped candidates produce no attachment.', architecture)
        self.assertIn('candidates\nfrom the same mail are never compared with each other', architecture)
        self.assertIn('Other candidates from the same mail never enter\nduplicate checking', security)
        for document in (architecture, security):
            for removed in ('together with the events already prepared',
                            'rows of the same shape for events already prepared'):
                self.assertNotIn(removed, ' '.join(document.split()))
        self.assertIn('Prepared event attachments are\ndelivered with that same notification', architecture)
        for detail in ('MEDIA:', '.ics', 'event-1', 'CRLF', 'VCALENDAR', 'readback'):
            self.assertNotIn(detail, architecture)
        self.assertIn('triage never creates\nCalendar events', readme)
        self.assertIn('iCalendar (`.ics`) file', readme)
        self.assertIn('triage has no Calendar write capability', security)
        for document in (architecture, readme, security):
            self.assertNotIn('verified creation', document)
            self.assertNotIn('creates and verifies', document)

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
