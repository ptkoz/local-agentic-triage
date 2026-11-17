import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from local_agentic_triage.common.diagnostics import error_code, error_suffix, CommandError

log = logging.getLogger('local_agentic_triage')
WARSAW = ZoneInfo('Europe/Warsaw')
MONTHS = ('', 'sty', 'lut', 'mar', 'kwi', 'maj', 'cze', 'lip', 'sie', 'wrz', 'paź', 'lis', 'gru')


def _label(value, comma=False):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError('invalid report boundary') from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('invalid report boundary')
    value = value.astimezone(WARSAW)
    separator = ', ' if comma else ' '
    return f'{value.day} {MONTHS[value.month]}{separator}{value:%H:%M}'


def render_notification(row, limit):
    if not 100 <= limit <= 2000:
        raise ValueError('report limit invalid')
    sender = row['sender']
    summary = row['summary']
    if not isinstance(summary, str) or not summary:
        raise ValueError('summary required')
    if row['sent_at']:
        text = f'Od **{sender}** z dnia {_label(row["sent_at"], comma=True)}\n> {summary}'
    else:
        text = f'Od **{sender}** — data wiadomości niedostępna\n> {summary}'
    if len(text) > limit:
        raise ValueError('notification too long')
    return text


def deliver(runner, config, text):
    if runner is None:
        raise RuntimeError('discord_delivery_disabled')
    try:
        result = runner(
            [config.hermes, 'send', '--to', config.channel],
            input=text, text=True, capture_output=True, timeout=config.timeout, shell=False,
        )
    except Exception as exc:
        log.error('mail_send_failed error=%s', error_code(exc))
        raise
    if result.returncode:
        exc = CommandError('mail_send_command_failed', result.returncode)
        log.error('mail_send_failed error=%s%s', error_code(exc), error_suffix(exc))
        raise exc
