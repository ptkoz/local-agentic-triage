"""Private raw-mail handoff; only the closed delivery receipt crosses back."""
import subprocess

from local_agentic_triage.classifier.core import strict_json
from local_agentic_triage.common.diagnostics import CommandError


def forward(raw, config):
    try:
        result = subprocess.run(
            [config.agentic_vulcan, 'process', '--config', config.agentic_vulcan_config,
             '--state', config.agentic_vulcan_state],
            input=raw, shell=False, capture_output=True, timeout=config.timeout,
        )
        if result.returncode != 0:
            raise ValueError('delivery failed')
        receipt = strict_json(result.stdout.decode('utf-8'))
        if (type(receipt) is not dict or set(receipt) != {'code', 'deliveries'}
                or receipt['code'] != 'DELIVERED'
                or type(receipt['deliveries']) is not int or receipt['deliveries'] != 1):
            raise ValueError('invalid receipt')
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise CommandError('agentic_vulcan_failed') from None
