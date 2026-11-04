"""Hand raw eduVULCAN mail to the local agentic-vulcan; success is its exact delivery receipt."""
import logging
import re
import subprocess
from email import policy
from email.parser import BytesParser
from html import unescape
from pathlib import Path

from local_agentic_triage.classifier.core import strict_json
from local_agentic_triage.mail.mime import Text

VULCAN_SENDER = 'noreply@vulcan.net.pl'
log = logging.getLogger('local_agentic_triage')
UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
UUID_ANY = re.compile(UUID)
MARKER = re.compile(r'ID:\s*(.*)')
INVALID = 'invalid notification'


def notification_uuid(raw):
    """Return the sole lowercase notification UUID from raw RFC822 bytes; raise ValueError(INVALID) otherwise."""
    try:
        found = _uuids(raw)
    except Exception:
        found = None
    if not found or len(found) != 1:
        raise ValueError(INVALID) from None
    return found.pop()


def _uuids(raw):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    representations = []

    def visit(part):
        if part.get_content_disposition() == 'attachment' or part.get_filename():
            return
        if part.get_content_maintype() == 'multipart':
            for child in part.iter_parts():
                visit(child)
        elif part.get_content_type() == 'text/plain':
            content = part.get_content()
            representations.append((content, content))
        elif part.get_content_type() == 'text/html':
            source = part.get_content()
            parser = Text()
            parser.feed(source)
            parser.close()
            representations.append((''.join(parser.parts), unescape(source)))

    visit(message)
    found = set()
    for text, source in representations:
        value = _representation(text, source)
        if value is not None:
            found.add(value)
    return found


def _representation(text, source):
    """Return one controlled marker, ignore UUID-free text, and reject every other UUID."""
    markers = [m.group(1).strip() for m in map(MARKER.fullmatch, (line.strip() for line in text.splitlines())) if m]
    ids = UUID_ANY.findall(text)
    source_ids = UUID_ANY.findall(source)
    if not ids and not markers:
        if source_ids:
            raise ValueError
        return None
    if len(markers) != 1 or len(ids) != 1 or not re.fullmatch(UUID, markers[0]) or ids[0] != markers[0]:
        raise ValueError
    if [value.lower() for value in source_ids] != [value.lower() for value in ids]:
        raise ValueError
    return markers[0].lower()


def forward(uid, raw, config):
    """Best effort: log a fixed result and never raise."""
    try:
        home = Path.home()
        app = home / 'Library/Application Support/agentic-vulcan'
        result = subprocess.run(
            [str(home / '.local/bin/agentic-vulcan'), 'process', '--config', str(app / 'config.json'),
             '--state', str(app / 'state')],
            input=raw, shell=False, capture_output=True, timeout=config.timeout)
        if result.returncode == 0:
            receipt = strict_json(result.stdout)
            if receipt == {'code': 'DELIVERED', 'deliveries': 1} and type(receipt['deliveries']) is int:
                log.info('agentic_vulcan_completed uid=%s result=delivered', uid)
                return
    except Exception:
        pass
    log.error('agentic_vulcan_failed uid=%s result=failed', uid)
