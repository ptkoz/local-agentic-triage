"""Himalaya v2.1.0 is the sole mail boundary; all identifiers are UIDs."""
from datetime import datetime
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

from local_agentic_triage.classifier.core import strict_json
from local_agentic_triage.common.diagnostics import CommandError, error_code


def uid_value(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]*', value)
            or len(value) > 10 or int(value) > 4294967295):
        raise ValueError('invalid_uid')
    return value


def imap_quote(value):
    if (not isinstance(value, str) or not value or any(ord(char) < 32 or ord(char) > 126 for char in value)):
        raise ValueError('invalid_mailbox')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


class Mailbox:
    def __init__(self, config):
        if not Path(config.himalaya).is_absolute():
            raise ValueError('invalid_himalaya_path')
        self.config = config
        self._selected = imap_quote(config.inbox)
        imap_quote(config.archive)
        self.validity = self.selection(self.command(['imap', 'raw'], self._select()))

    @classmethod
    def connect(cls, config):
        return cls(config)

    def _select(self, suffix=''):
        return f'a1 SELECT {self._selected}\r\n{suffix}'.encode('ascii')

    def command(self, args, data=None, json_output=False):
        argv = [self.config.himalaya, '--account', self.config.account,
                '--backend', 'imap', '--log-level', 'off']
        if json_output:
            argv.append('--json')
        try:
            result = subprocess.run(argv + args, input=data, capture_output=True,
                                    timeout=self.config.timeout, shell=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CommandError(error_code(exc)) from None
        if result.returncode:
            raise CommandError('himalaya_command_failed', result.returncode)
        if not isinstance(result.stdout, bytes):
            raise ValueError('himalaya_output_invalid')
        return result.stdout

    @staticmethod
    def selection(output):
        try:
            text = output.decode('ascii')
        except UnicodeError:
            raise ValueError('himalaya_protocol_invalid') from None
        lines = text.splitlines()
        completions = [line for line in lines if line.startswith('a1 ')]
        if len(completions) != 1 or not re.fullmatch(r'a1 OK(?: .*)?', completions[0]):
            raise RuntimeError('himalaya_select_failed')
        values = [match.group(1) for line in lines
                  if (match := re.fullmatch(r'\* OK \[UIDVALIDITY ([0-9]+)\](?: .*)?', line))]
        if len(values) != 1:
            raise ValueError('himalaya_validity_invalid')
        validity = uid_value(values[0])
        flags = [match.group(1).split() for line in lines
                 if (match := re.fullmatch(r'\* OK \[PERMANENTFLAGS \(([^)]*)\)\](?: .*)?', line))]
        if len(flags) != 1 or not {'\\*', 'HermesLeave'}.intersection(flags[0]):
            raise ValueError('himalaya_keywords_unavailable')
        return validity

    def candidates(self):
        return self.search('UNKEYWORD HermesLeave')

    def search(self, query):
        if not re.fullmatch(r'(?:UNKEYWORD HermesLeave|UID [1-9][0-9]*)', query):
            raise ValueError('himalaya_search_invalid')
        output = self.command(['imap', 'raw'], self._select('a2 UID SEARCH ' + query + '\r\n'))
        return self._search_result(output)

    def _search_result(self, output):
        if self.selection(output) != self.validity:
            raise ValueError('himalaya_validity_changed')
        lines = output.decode('ascii').splitlines()
        completions = [line for line in lines if line.startswith('a2 ')]
        if len(completions) != 1 or not re.fullmatch(r'a2 OK(?: .*)?', completions[0]):
            raise RuntimeError('himalaya_search_failed')
        searches = [line for line in lines if line == '* SEARCH' or line.startswith('* SEARCH ')]
        if len(searches) != 1:
            raise ValueError('himalaya_search_invalid')
        uids = [uid_value(value) for value in searches[0][8:].split()]
        if len(set(uids)) != len(uids):
            raise ValueError('himalaya_search_invalid')
        return uids

    def all_uids(self):
        output = self.command(['imap', 'raw'], self._select('a2 UID SEARCH ALL\r\n'))
        return set(self._search_result(output))

    def source_prefix(self):
        account = quote(self.config.account, safe='')
        inbox = quote(self.config.inbox, safe='')
        return f'imap:{account}:{inbox}:{self.validity}:'

    def source_key(self, uid):
        return self.source_prefix() + uid_value(uid)

    def exists(self, uid):
        uid = uid_value(uid)
        return uid in self.search('UID ' + uid)

    def require_uid(self, uid):
        uid = uid_value(uid)
        if not self.exists(uid):
            raise ValueError('himalaya_uid_missing')
        return uid

    def raw(self, uid):
        uid = self.require_uid(uid)
        raw = self.command(['message', 'read', '-m', self.config.inbox, '--raw', uid])
        if not raw or (self.config.max_mail_bytes and len(raw) > self.config.max_mail_bytes):
            raise ValueError('mail_size_invalid')
        return raw

    def internal_date(self, uid):
        uid = self.require_uid(uid)
        output = self.command(['imap', 'raw'], self._select(f'a2 UID FETCH {uid} (UID INTERNALDATE)\r\n'))
        if self.selection(output) != self.validity:
            raise ValueError('himalaya_validity_changed')
        try:
            text = output.decode('ascii')
        except UnicodeError:
            raise ValueError('himalaya_protocol_invalid') from None
        completions = [line for line in text.splitlines() if line.startswith('a2 ')]
        if len(completions) != 1 or not re.fullmatch(r'a2 OK(?: .*)?', completions[0]):
            raise RuntimeError('himalaya_fetch_failed')
        pattern = rf'\* [1-9][0-9]* FETCH \(UID {re.escape(uid)} INTERNALDATE "([^"]+)"\)'
        values = [match.group(1) for line in text.splitlines()
                  if (match := re.fullmatch(pattern, line))]
        if len(values) != 1:
            raise ValueError('himalaya_internal_date_invalid')
        try:
            return datetime.strptime(values[0], '%d-%b-%Y %H:%M:%S %z').isoformat()
        except ValueError:
            raise ValueError('himalaya_internal_date_invalid') from None

    def mutation(self, args, expected):
        output = self.command(args, json_output=True)
        try:
            result = strict_json(output)
        except (ValueError, UnicodeError):
            raise ValueError('himalaya_result_invalid') from None
        if result != {'message': expected}:
            raise ValueError('himalaya_result_invalid')

    def leave(self, uid):
        uid = self.require_uid(uid)
        self.mutation(['imap', 'store', '-m', self.config.inbox, '--flag',
                       'HermesLeave', '--', uid], 'Flag(s) successfully added')

    def archive(self, uid):
        uid = self.require_uid(uid)
        self.mutation(['imap', 'store', '-m', self.config.inbox, '--flag',
                       '\\Seen', '--', uid], 'Flag(s) successfully added')
        self.require_uid(uid)
        self.mutation(['message', 'move', '--from', self.config.inbox, '--to',
                       self.config.archive, uid], '1 message successfully moved')

    def close(self):
        pass
