import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from local_agentic_triage.common.diagnostics import error_code, error_suffix, CommandError

log = logging.getLogger('local_agentic_triage')
WARSAW = ZoneInfo('Europe/Warsaw')
MONTHS = ('', 'sty', 'lut', 'mar', 'kwi', 'maj', 'cze', 'lip', 'sie', 'wrz', 'paź', 'lis', 'gru')
DESTINATION_LABELS = {'archive': 'Archiwum', 'leave': 'Inbox'}


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


def _sender(name, address):
    # Without a distinct display name, show the address once instead of repeating it.
    if name and name != address:
        return f'**{name}** ({address})' if address else f'**{name}**'
    return f'**{address}**'


def render_notification(row, limit, *, destination):
    if not 100 <= limit <= 2000:
        raise ValueError('report limit invalid')
    if destination not in DESTINATION_LABELS:
        raise ValueError('destination invalid')
    heading = f'**{DESTINATION_LABELS[destination]}**:'
    sender = _sender(row['sender_name'], row['sender_address'])
    summary = row['summary']
    if not isinstance(summary, str) or not summary:
        raise ValueError('summary required')
    if row['sent_at']:
        text = f'{heading} z **{_label(row["sent_at"], comma=True)}** od {sender}\n> {summary}'
    else:
        text = f'{heading} od {sender} — data wiadomości niedostępna\n> {summary}'
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
