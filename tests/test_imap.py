import subprocess
import unittest
from unittest.mock import patch
from local_agentic_triage.mail.imap import Mailbox
from helpers import make_config

SELECT=b'* FLAGS (\\Seen HermesLeave)\r\n* OK [PERMANENTFLAGS (\\Seen \\Flagged)] flags\r\n* 3 EXISTS\r\n* OK [UIDVALIDITY 77] valid\r\na1 OK [READ-WRITE] SELECT completed\r\n'
SEARCH=SELECT+b'* SEARCH 9 12\r\na2 OK SEARCH completed\r\n'
INTERNALDATE=SELECT+b'* 3 FETCH (UID 9 INTERNALDATE "31-Oct-2026 01:27:00 +0100")\r\na2 OK FETCH completed\r\n'
def done(output): return subprocess.CompletedProcess([],0,output,b'')

class HimalayaTests(unittest.TestCase):
    def test_uid_commands_and_identity(self):
        with patch('subprocess.run',side_effect=[done(SELECT),done(SEARCH),done(SEARCH),done(b'From: synthetic\r\n\r\nbody'),done(SEARCH),done(b'{"message":"Flag(s) successfully added"}'),done(SEARCH),done(b'{"message":"Flag(s) successfully added"}'),done(SEARCH),done(b'{"message":"1 message successfully moved"}')]) as run:
            box=Mailbox.connect(make_config(account='account-two',inbox='Inbox "Two"',archive='Archive \\ Two'))
            self.assertEqual(box.candidates(),['9','12'])
            self.assertEqual(box.raw('9'),b'From: synthetic\r\n\r\nbody')
            box.leave('9'); box.archive('9')
            self.assertEqual(box.source_key('9'),'imap:account-two:Inbox%20%22Two%22:77:9')
            calls=run.call_args_list
            self.assertEqual(calls[0].kwargs['input'],b'a1 SELECT "Inbox \\"Two\\""\r\n')
            self.assertEqual(calls[1].kwargs['input'],b'a1 SELECT "Inbox \\"Two\\""\r\na2 UID SEARCH UNFLAGGED\r\n')
            self.assertEqual(calls[3].args[0][-6:],['message','read','-m','Inbox "Two"','--raw','9'])
            self.assertEqual(calls[5].args[0][-8:],['imap','store','-m','Inbox "Two"','--flag','\\Flagged','--','9'])
            self.assertEqual(calls[-3].args[0][-3:],['\\Seen','--','9'])
            self.assertEqual(calls[-1].args[0][-7:],['message','move','--from','Inbox "Two"','--to','Archive \\ Two','9'])
            for call in calls:
                self.assertIn('account-two',call.args[0]); self.assertFalse(call.kwargs['shell']); self.assertGreater(call.kwargs['timeout'],0)
    def test_reject_protocol_failure_and_invalid_uids(self):
        for output in (SELECT.replace(b'77',b'0'),SELECT.replace(b'a1 OK',b'a1 NO'),SELECT+b'a1 OK duplicate\r\n'):
            with patch('subprocess.run',return_value=done(output)),self.assertRaises((ValueError,RuntimeError)):
                Mailbox.connect(make_config())
        with patch('subprocess.run',return_value=done(SELECT)) as run:
            box=Mailbox.connect(make_config())
            for uid in ('1:*','0','01','4294967296'):
                with self.assertRaises(ValueError): box.leave(uid)
            self.assertEqual(run.call_count,1)
    def test_search_validity_change_and_missing_uid_block_archive(self):
        for output in (SEARCH.replace(b'77',b'78'),SEARCH.replace(b'9 12',b'12'),SEARCH.replace(b'9 12',b'9 x'),SEARCH.replace(b'a2 OK',b'a2 NO')):
            with patch('subprocess.run',side_effect=[done(SELECT),done(output)]) as run:
                box=Mailbox.connect(make_config())
                with self.assertRaises((ValueError,RuntimeError)): box.archive('9')
                self.assertEqual(run.call_count,2)
    def test_failure_is_fixed_and_output_not_exposed(self):
        with patch('subprocess.run',side_effect=OSError('PRIVATE')):
            with self.assertRaisesRegex(RuntimeError,'^os_error$'): Mailbox.connect(make_config())
        with patch('subprocess.run',return_value=subprocess.CompletedProcess([],19,b'PRIVATE',b'PRIVATE')):
            with self.assertRaisesRegex(RuntimeError,'^himalaya_command_failed$') as raised: Mailbox.connect(make_config())
            self.assertEqual(getattr(raised.exception,'returncode',None),19)
    def test_validity_rechecked_before_read_and_leave(self):
        for operation in ('raw','leave'):
            with patch('subprocess.run',side_effect=[done(SELECT),done(SEARCH.replace(b'77',b'78'))]) as run:
                box=Mailbox.connect(make_config())
                with self.assertRaisesRegex(ValueError,'himalaya_validity_changed'): getattr(box,operation)('9')
                self.assertEqual(run.call_count,2)
    def test_invalid_mutation_outputs_stop_before_move(self):
        for output in (b'null',b'"No message moved: no id matched in the source mailbox"',b'{"error":"private"}',b'not json'):
            with patch('subprocess.run',side_effect=[done(SELECT),done(SEARCH),done(output)]) as run:
                box=Mailbox.connect(make_config())
                with self.assertRaises(ValueError): box.archive('9')
                self.assertEqual(run.call_count,3)
    def test_permanent_flagged_is_supported_without_custom_keywords(self):
        with patch('subprocess.run',return_value=done(SELECT)):
            self.assertEqual(Mailbox.connect(make_config()).validity,'77')

    def test_permanent_flagged_tokens_must_match_exactly(self):
        for flags in (b'\\Seen \\*',b'\\Seen HermesLeave',b'\\Seen \\FlaggedOther',b'\\Seen'):
            with self.subTest(flags=flags):
                output=SELECT.replace(b'\\Seen \\Flagged',flags)
                with patch('subprocess.run',return_value=done(output)),self.assertRaisesRegex(ValueError,'^himalaya_flagged_unavailable$'):
                    Mailbox.connect(make_config())

    def test_candidates_ignore_legacy_markers_and_exclude_flagged(self):
        messages={'9':{'HermesLeave'},'12':set(),'15':{'HermesLeave','\\Flagged'},'18':{'\\Flagged'}}
        def respond(argv,**kwargs):
            request=kwargs['input']
            if request==b'a1 SELECT "Test Inbox"\r\n':
                return done(SELECT)
            self.assertEqual(request,b'a1 SELECT "Test Inbox"\r\na2 UID SEARCH UNFLAGGED\r\n')
            uids=' '.join(uid for uid,flags in messages.items() if '\\Flagged' not in flags)
            return done(SELECT+f'* SEARCH {uids}\r\na2 OK SEARCH completed\r\n'.encode('ascii'))
        with patch('subprocess.run',side_effect=respond) as run:
            self.assertEqual(Mailbox.connect(make_config()).candidates(),['9','12'])
            self.assertEqual(run.call_count,2)

    def test_all_uids_validates_search_and_validity(self):
        with patch('subprocess.run',side_effect=[done(SELECT),done(SEARCH)]) as run:
            self.assertEqual(Mailbox.connect(make_config()).all_uids(),{'9','12'})
            self.assertEqual(run.call_args_list[1].kwargs['input'],b'a1 SELECT "Test Inbox"\r\na2 UID SEARCH ALL\r\n')
        for output in (SEARCH.replace(b'77',b'78'),SEARCH.replace(b'9 12',b'9 x'),SEARCH.replace(b'a2 OK',b'a2 NO')):
            with patch('subprocess.run',side_effect=[done(SELECT),done(output)]):
                with self.assertRaises((ValueError,RuntimeError)):
                    Mailbox.connect(make_config()).all_uids()

    def test_empty_and_invalid_search_outputs(self):
        with patch('subprocess.run',side_effect=[done(SELECT),done(SEARCH.replace(b'* SEARCH 9 12',b'* SEARCH'))]):
            self.assertEqual(Mailbox.connect(make_config()).candidates(),[])
        for values in (b'9 9',b'0',b'01',b'4294967296'):
            with patch('subprocess.run',side_effect=[done(SELECT),done(SEARCH.replace(b'9 12',values))]),self.assertRaises(ValueError): Mailbox.connect(make_config()).candidates()

    def test_default_reads_raw_message_larger_than_one_megabyte(self):
        raw=b'From: synthetic\r\n\r\n'+b'x'*1100000
        with patch('subprocess.run',side_effect=[done(SELECT),done(SEARCH),done(raw)]):
            self.assertEqual(len(Mailbox.connect(make_config()).raw('9')),len(raw))

    def test_internal_date_is_uid_scoped_and_normalized(self):
        with patch('subprocess.run',side_effect=[done(SELECT),done(SEARCH),done(INTERNALDATE)]) as run:
            self.assertEqual(Mailbox.connect(make_config()).internal_date('9'),'2026-10-31T01:27:00+01:00')
            self.assertEqual(run.call_args_list[2].kwargs['input'],b'a1 SELECT "Test Inbox"\r\na2 UID FETCH 9 (UID INTERNALDATE)\r\n')
        for output in (INTERNALDATE.replace(b'UID 9',b'UID 12'),INTERNALDATE.replace(b'+0100',b'private'),INTERNALDATE.replace(b'a2 OK',b'a2 NO')):
            with patch('subprocess.run',side_effect=[done(SELECT),done(SEARCH),done(output)]),self.assertRaises((ValueError,RuntimeError)):
                Mailbox.connect(make_config()).internal_date('9')
