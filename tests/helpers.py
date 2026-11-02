from dataclasses import asdict, replace

from local_agentic_triage.common.config import Config


def make_config(**changes):
    base = Config(
        account='test-account',
        inbox='Test Inbox',
        archive='Test Archive',
        channel='discord:1',
        discord_user_id='2',
        calendar='Test Calendar',
        himalaya='/opt/local/bin/himalaya',
        hermes='/opt/local/bin/hermes',
        database='/var/tmp/local-agentic-triage.sqlite3',
        poll_lock='/var/tmp/local-agentic-triage-poll.lock',
        report_lock='/var/tmp/local-agentic-triage-report.lock',
        delivery_lock='/var/tmp/local-agentic-triage-delivery.lock',
    )
    return replace(base, **changes)


def config_text(**changes):
    values = asdict(make_config())
    values.update(changes)
    strings = ('account','inbox','archive','channel','discord_user_id','calendar',
               'himalaya','hermes','database','poll_lock','report_lock','delivery_lock')
    lines = [f'{key} = {values[key]!r}' for key in strings]
    lines.extend(f'{key} = {values[key]}' for key in ('max_mail_bytes','discord_limit','timeout'))
    return '\n'.join(lines) + '\n'
