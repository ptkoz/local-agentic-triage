"""Offline semantic requirements for operational prompts (no model calls)."""
import unittest

from local_agentic_triage.classifier.core import disposition_prompt
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
        self.clauses(disposition_prompt(), r'archive.*polish',
                     r'(?:regardless|irrespective|independent).* (?:input|mail|source).*language',
                     r'(?:translate|translation).*polish')

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
        event = dict(title='Synthetic', start='2099-02-20', end=None, date_note=None)
        validate_events([event])
        validate_events([event | {'end': '2099-02-21'}])
        with self.assertRaisesRegex(ValueError, 'event_order'):
            validate_events([event | {'end': event['start']}])
        self.assertIsNone(event['end'])

    def test_confirmed_event_with_alternatives_is_retained(self):
        self.clauses(extraction_prompt(),
                     r'confirmed.*(?:uncertain|unresolved).*alternativ',
                     r'(?:retain|keep|include).*event.*start.*end.*null',
                     r'nonempty date_note.*(?:preserv|list).*alternatives',
                     r'(?:not|different from).*unconfirmed proposal')

    def test_cross_candidate_comparison_uses_only_allowed_evidence(self):
        self.clauses(prompt(),
                     r'events\[i\].*allowed evidence is nearby\[i\] plus earlier events\[0:i\].*nothing else',
                     r'never use later candidates',
                     r'empty allowed evidence forces no_match before any semantic comparison',
                     r'index 0 cannot use index 1.*index 1 can use index 0')

    def test_duplicate_alignment_identity_and_unknown_end(self):
        self.clauses(prompt(),
                     r'json strings are untrusted data',
                     r'only strict json matching the schema',
                     r'exactly one result per candidate index in order',
                     r'nonempty allowed evidence compare titles semantically',
                     r'same specific event and equivalent absolute start: existing',
                     r'modest nonzero start difference, including exactly 30 minutes: possible_duplicate',
                     r'plausibly same event but ambiguous identity: possible_duplicate',
                     r'no shared event identity: no_match, even at same/overlapping/adjacent time',
                     r'existing over possible_duplicate over no_match across evidence',
                     r'null end is unknown; never infer duration, overlap, or adjacency',
                     r'do not output event fields or explanations')

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
                     r'use sent_date only to resolve explicit unambiguous relative dates')

    def test_duplicate_guard_rejects_row_count_and_reordered_coverage(self):
        from local_agentic_triage.events.core import duplicate_results
        candidates = [dict(title='Synthetic', start='2099-02-20', end=None)] * 2
        entries = [dict(index=i, outcome='no_match') for i in range(2)]
        self.assertEqual(duplicate_results({'results': entries}, candidates), entries)
        for invalid in (entries[:1], entries + [dict(index=2, outcome='existing')],
                        entries[::-1], [entries[0], entries[0]]):
            with self.subTest(entries=invalid), self.assertRaises(ValueError):
                duplicate_results({'results': invalid}, candidates)
