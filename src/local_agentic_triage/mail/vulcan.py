"""Private raw-mail handoff; only the closed delivery receipt crosses back."""
import os
import select
import subprocess
import threading
import time

from local_agentic_triage.classifier.core import strict_json
from local_agentic_triage.common.diagnostics import CommandError

MAX_RECEIPT_BYTES = 1024
POLL_SECONDS = 0.05
DRAIN_GRACE_SECONDS = 1


def _drain(fd, kept, stop):
    """Keep draining so the child never blocks, retaining one byte past the limit as overflow.

    Never blocks in read: after the child is gone (stop) it only takes what is still available,
    for a bounded grace period, so a descendant holding the pipe open cannot pin this thread.
    """
    deadline = None
    while True:
        if stop.is_set() and deadline is None:
            deadline = time.monotonic() + DRAIN_GRACE_SECONDS
        if select.select([fd], [], [], POLL_SECONDS)[0]:
            chunk = os.read(fd, 65536)
            if not chunk:
                return
            kept.extend(chunk[:MAX_RECEIPT_BYTES + 1 - len(kept)])
        elif deadline is not None:
            return
        if deadline is not None and time.monotonic() >= deadline:
            return


def forward(raw, config):
    kept = bytearray()
    try:
        read_fd, write_fd = os.pipe()
        try:
            stop = threading.Event()
            reader = threading.Thread(target=_drain, args=(read_fd, kept, stop), daemon=True)
            reader.start()
        except BaseException:
            os.close(read_fd); os.close(write_fd); raise
        try:
            result = subprocess.run(
                [config.agentic_vulcan, 'process', '--config', config.agentic_vulcan_config,
                 '--state', config.agentic_vulcan_state],
                input=raw, shell=False, stdout=write_fd, stderr=subprocess.DEVNULL,
                timeout=config.timeout,
            )
        finally:
            os.close(write_fd)
            stop.set()
            try:
                reader.join()
            finally:
                os.close(read_fd)
        if len(kept) > MAX_RECEIPT_BYTES:
            raise ValueError('unbounded output')
        if result.returncode != 0:
            raise ValueError('delivery failed')
        receipt = strict_json(bytes(kept).decode('utf-8'))
        if (type(receipt) is not dict or set(receipt) != {'code', 'deliveries'}
                or receipt['code'] != 'DELIVERED'
                or type(receipt['deliveries']) is not int or receipt['deliveries'] != 1):
            raise ValueError('invalid receipt')
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise CommandError('agentic_vulcan_failed') from None
