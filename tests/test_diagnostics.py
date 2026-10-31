import json
import unittest
from local_agentic_triage.common.diagnostics import error_code, error_suffix, CommandError


class DiagnosticsTests(unittest.TestCase):
    def test_safe_known_codes_and_private_unknown_errors(self):
        self.assertEqual(error_code(ValueError('archive summary')),'value_error')
        self.assertEqual(error_code(ValueError('private subject')),'value_error')
        self.assertEqual(error_code(ValueError('sender secret')),'value_error')
        self.assertEqual(error_code(json.JSONDecodeError('PRIVATE JSON','PRIVATE DOCUMENT',0)),'json_decode_error')
        self.assertEqual(error_code(RuntimeError('PRIVATE ERROR')),'runtime_error')
        self.assertEqual(error_code(TimeoutError('PRIVATE TIMEOUT')),'timeout_error')
        self.assertEqual(error_code(OSError('PRIVATE')),'os_error')
        failure=CommandError('himalaya_command_failed',23)
        self.assertEqual(error_code(failure),'himalaya_command_failed')
        self.assertEqual(error_suffix(failure),' returncode=23')
