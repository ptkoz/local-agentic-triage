"""Offline semantic requirements for operational prompts (no model calls)."""
import unittest

from local_agentic_triage.classifier.core import disposition_prompt, summary_prompt
from local_agentic_triage.events.core import extraction_prompt, prompt


class PromptContractTests(unittest.TestCase):
    def clauses(self, text, *patterns):
        text = ' '.join(text.lower().split())
        for pattern in patterns:
            with self.subTest(requirement=pattern):
                self.assertRegex(text, pattern)

    def test_timezone_default_and_explicit_zone_precedence(self):
        self.clauses(extraction_prompt(),
            r'europe/warsaw.*default.*(?:no|without|missing).*timezone',
            r'explicit.*(?:zone|timezone).*never.*(?:replace|override)',
            r'local date.*time')
        self.assertNotRegex(extraction_prompt().lower(), r'never guess[^.]*timezone')

    def test_unzoned_wall_clock_digits_are_preserved(self):
        self.clauses(extraction_prompt(),
                     r'(?:no|without).*zone.*wall.clock.*directly.*europe/warsaw',
                     r'(?:attach|apply).*date.specific.*offset',
                     r'(?:never|do not).*convert.*subtract.*hours',
                     r'(?:preserve|unchanged).*clock digits',
                     r'tomorrow 10:00.?11:00.*10:00.*11:00.*(?:not|never).*08:00')

    def test_archive_summary_language_is_independent_of_mail_language(self):
        self.clauses(summary_prompt(), r'(?:sentence|summary).*polish',
                     r'(?:regardless|irrespective|independent).* (?:input|mail|source).*language',
                     r'(?:translate|translation).*polish')

    def test_archive_summary_is_short_single_sentence_plain_text(self):
        self.clauses(summary_prompt(),
                     r'exactly one sentence',
                     r'at most 180 characters',
                     r'plain text',
                     r'(?:no|without|do not use).*html',
                     r'(?:no|without|do not use).*markdown')

    def test_acknowledgement_is_archive_without_version_history(self):
        self.clauses(disposition_prompt(),
                     r'generated summary.*source email.*separate summary and mail fields',
                     r'substantive personal reply',
                     r'acknowledge.*receipt.*awareness.*having read.*archive')
        text=' '.join(disposition_prompt().lower().split())
        for obsolete in ('overrides the general reply rule',
                         'decide only whether',
                         'do not summarize the mail',
                         'explain the decision',
                         'evaluate its importance',
                         'recommend an action'):
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete,text)

    def test_exact_date_components_and_relative_resolution(self):
        self.clauses(extraction_prompt(), r'(?:exact|preserve).*date tokens.*year',
                     r'(?:never|do not).*transpos.*day.*month',
                     r'(?:resolve|derive).*tomorrow.*sent_date')

    def test_single_day_and_explicit_exclusive_end(self):
        from local_agentic_triage.events.core import validate_events
        self.clauses(extraction_prompt(),
                     r'single.date.*start.*yyyy-mm-dd.*end.*null',
                     r'(?:only|unless).*explicit.*(?:range|end)',
                     r'exclusive.*strictly after start',
                     r'(?:never|do not).*repair')
        event = dict(title='Synthetic', start='2099-02-20', end=None, date_note=None, location=None)
        validate_events([event])
        validate_events([event | {'end': '2099-02-21'}])
        validate_events([event | {'end': event['start']}])
        from local_agentic_triage.events.core import validate_final
        with self.assertRaisesRegex(ValueError, 'event_order'):
            validate_final(event | {'end': event['start']})
        self.assertIsNone(event['end'])

    def test_confirmed_event_with_alternatives_is_retained(self):
        self.clauses(extraction_prompt(),
                     r'confirmed.*(?:uncertain|unresolved).*alternativ',
                     r'(?:retain|keep|include).*event.*start.*end.*null',
                     r'nonempty date_note.*(?:preserv|list).*alternatives',
                     r'(?:not|different from).*unconfirmed proposal')

    def test_single_event_duplicate_identity_and_unknown_values(self):
        self.clauses(prompt(),
                     r'one supplied event',
                     r'all supplied event fields.*title, start, end, date_note, and location',
                     r'missing values are unknown',
                     r'never invent missing values or infer duration, overlap, or adjacency',
                     r'equivalent absolute start means existing',
                     r'exactly 30 minutes.*possible_duplicate',
                     r'existing over possible_duplicate over create',
                     r'empty evidence means create',
                     r'one strict json object.*only outcome')

    def test_explicit_zone_digits_and_offset_are_preserved_before_default(self):
        self.clauses(extraction_prompt(),
                     r'for every timed value, apply this order: 1\.',
                     r'explicitly supplies z, utc, or a numeric utc offset.*copy its date and clock digits exactly',
                     r'copy an explicit numeric offset exactly.*encode z and named utc as \+00:00 without changing the date or clock digits',
                     r'keeping any non.hour numeric offset',
                     r'never convert an explicitly zoned value to warsaw or change it to an equivalent instant',
                     r'2\. otherwise.*no zone.*local wall.clock',
                     r'copy the local date.*preserve the stated clock digits unchanged',
                     r'\+01:00 in winter standard time, \+02:00 in summer daylight.saving time',
                     r'resolve explicit unambiguous relative dates such as tomorrow from sent_date')

    def test_duplicate_guard_rejects_batch_and_extra_fields(self):
        from local_agentic_triage.events.core import duplicate_results
        for outcome in ('existing','possible_duplicate','create'):
            self.assertEqual(duplicate_results({'outcome':outcome}),outcome)
        for invalid in ({'results':[]},{'outcome':'no_match'},{'outcome':'create','index':0},{}):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                duplicate_results(invalid)
