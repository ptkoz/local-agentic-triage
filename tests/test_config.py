import tempfile
import unittest
from pathlib import Path

from local_agentic_triage.common.config import Config, load
from helpers import config_text, make_config


class ConfigTests(unittest.TestCase):
    def test_operational_values_are_required(self):
        with self.assertRaises(TypeError):
            Config()
        required = ('account', 'inbox', 'archive', 'channel', 'discord_user_id',
                    'calendar', 'himalaya', 'hermes', 'database', 'poll_lock',
                    'report_lock')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            for key in required:
                values = {name: getattr(make_config(), name) for name in required}
                values.pop(key)
                path.write_text('\n'.join(f'{name} = {value!r}' for name, value in values.items()))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    load(path)

    def test_pre_delivery_lock_config_gets_compatible_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            old_config = '\n'.join(
                line for line in config_text().splitlines()
                if not line.startswith('delivery_lock =')
            )
            path.write_text(old_config)
            config = load(path)
            self.assertEqual(config.delivery_lock, config.report_lock + '.delivery')

    def test_loads_explicit_generic_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text(config_text())
            config = load(path)
            self.assertEqual(config.account, 'test-account')
            self.assertEqual(config.calendar, 'Test Calendar')
            self.assertEqual(config.discord_user_id, '2')
            self.assertEqual(config.max_mail_bytes, 0)

    def test_rejects_unknown_placeholders_paths_and_discord_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            invalid = (
                {'account': '@ACCOUNT@'},
                {'account': 'prefix@ACCOUNT@'},
                {'inbox': 'Mailbox @INBOX@'},
                {'calendar': 'Calendar @CALENDAR@'},
                {'himalaya': '/opt/@HIMALAYA@'},
                {'calendar': ''},
                {'himalaya': 'relative/himalaya'},
                {'database': '~/triage.sqlite3'},
                {'channel': 'discord:not-an-id'},
                {'discord_user_id': '0'},
            )
            for changes in invalid:
                path.write_text(config_text(**changes))
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    load(path)
            path.write_text(config_text() + 'unsupported = "value"\n')
            with self.assertRaises(ValueError):
                load(path)
