import logging
import os
import re
import shutil
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from local_agentic_triage.common.attachment import Attachment
from local_agentic_triage.common.diagnostics import error_code, error_suffix, CommandError

log = logging.getLogger('local_agentic_triage')
WARSAW = ZoneInfo('Europe/Warsaw')
MONTHS = ('', 'sty', 'lut', 'mar', 'kwi', 'maj', 'cze', 'lip', 'sie', 'wrz', 'paź', 'lis', 'gru')
DESTINATION_LABELS = {'archive': 'Archiwum', 'leave': 'Inbox'}
ATTACHMENT_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}')
# Hermes treats MEDIA: anywhere in the text, in any case, as a local-file attachment.
MEDIA_DIRECTIVE = re.compile(r'(?i)(media):')


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


def _materialize(attachments):
    """Write attachments into a new private directory; remove it again on any failure."""
    for item in attachments:
        if (not isinstance(item, Attachment) or not isinstance(item.filename, str)
                or not isinstance(item.content, str) or not ATTACHMENT_NAME.fullmatch(item.filename)):
            raise ValueError('attachment_invalid')
    directory = tempfile.mkdtemp(prefix='local-agentic-triage-')
    try:
        # Hermes reads bare MEDIA:<path> lines; the path must be absolute and unbroken.
        if not os.path.isabs(directory) or re.search(r'[\s\x00-\x1f\x7f]', directory):
            raise ValueError('attachment_path_unsafe')
        paths = []
        for item in attachments:
            path = os.path.join(directory, item.filename)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'wb') as file:
                file.write(item.content.encode('utf-8'))
            paths.append(path)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return directory, paths


def deliver(runner, config, text, attachments=()):
    if runner is None:
        raise RuntimeError('discord_delivery_disabled')
    directory = None
    # Untrusted text must not carry directives; a same-length lookalike colon keeps it readable.
    message = MEDIA_DIRECTIVE.sub('\\1\u2236', text)
    if attachments:
        # Optional: attachment failures fall back to the mandatory text-only notification.
        try:
            directory, paths = _materialize(attachments)
            message += ''.join(f'\nMEDIA:{path}' for path in paths)
        except Exception as exc:
            log.error('mail_attachment_failed error=%s', error_code(exc))
    try:
        _send(runner, config, message)
    finally:
        if directory is not None:
            try:
                shutil.rmtree(directory)
            except OSError as exc:
                log.error('mail_attachment_cleanup_failed error=%s', error_code(exc))


def _send(runner, config, text):
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
