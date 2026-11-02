from dataclasses import dataclass, fields
from pathlib import Path
import re
import tomllib

DEFAULT_PATH = Path.home() / 'Library/Application Support/local-agentic-triage/config.toml'
REQUIRED = (
    'account', 'inbox', 'archive', 'channel', 'discord_user_id', 'calendar',
    'himalaya', 'hermes', 'database', 'poll_lock', 'report_lock',
)
PATHS = ('himalaya', 'hermes', 'database', 'poll_lock', 'report_lock', 'delivery_lock')
PLACEHOLDER = re.compile(r'@[A-Z][A-Z0-9_]*@')
SNOWFLAKE = r'[1-9][0-9]{0,19}'


@dataclass(frozen=True)
class Config:
    account: str
    inbox: str
    archive: str
    channel: str
    discord_user_id: str
    calendar: str
    himalaya: str
    hermes: str
    database: str
    poll_lock: str
    report_lock: str
    delivery_lock: str
    max_mail_bytes: int = 0
    discord_limit: int = 1900
    timeout: int = 150

    def __post_init__(self):
        validate(self)


def validate(config):
    if config.max_mail_bytes < 0 or not 100 <= config.discord_limit <= 2000 or config.timeout < 1:
        raise ValueError('invalid limits')
    for key in REQUIRED:
        value = getattr(config, key)
        if (not value.strip() or PLACEHOLDER.search(value)
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError('invalid config string')
    for key in ('account', 'inbox', 'archive'):
        if any(ord(char) > 126 for char in getattr(config, key)):
            raise ValueError('invalid mail boundary')
    if not re.fullmatch(SNOWFLAKE, config.discord_user_id) or int(config.discord_user_id) > 2**64 - 1:
        raise ValueError('invalid discord user id')
    channel = re.fullmatch(r'discord:(' + SNOWFLAKE + r')', config.channel)
    if not channel or int(channel.group(1)) > 2**64 - 1:
        raise ValueError('invalid discord channel')
    for key in PATHS:
        if not Path(getattr(config, key)).is_absolute():
            raise ValueError('absolute path required')
    return config


def load(path=DEFAULT_PATH):
    with Path(path).expanduser().open('rb') as file:
        data = tomllib.load(file)
    allowed = {field.name for field in fields(Config)}
    if data.keys() - allowed:
        raise ValueError('unknown config key')
    if set(REQUIRED) - data.keys():
        raise ValueError('missing config key')
    for key, value in data.items():
        expected = int if key in {'max_mail_bytes', 'discord_limit', 'timeout'} else str
        if type(value) is not expected:
            raise ValueError('invalid config type')
    if 'delivery_lock' not in data:
        data['delivery_lock'] = data['report_lock'] + '.delivery'
    return Config(**data)
