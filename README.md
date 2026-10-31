# local-agentic-triage

Local, standard-library-only email triage for Python 3.14. Himalaya v2.1.0 is
the sole mail boundary, DMR classifies on loopback, Hermes performs a tool-free
semantic event decision, a deterministic single-calendar adapter creates and
verifies events, and Hermes sends a daily Discord report.

## Offline development

```sh
make test
make compile
make smoke
```

Tests use synthetic data and never connect to mail, DMR, Calendar, Discord, or
other live services. Report delivery requires both an injected runner and the
CLI `--deliver` flag; `report send` without that flag exits before report-state
access.

## Build and install

```sh
python3.14 scripts/build.py
python3.14 scripts/install.py --python /absolute/path/to/python3.14
```

The installer atomically writes the zipapp, new config, and LaunchAgent files.
It creates private 0700 directories and 0600 files, preserves an existing
config, and never loads or starts services. `--home /temporary/home` supports
isolated review. Templates use `@HOME@` and `@PYTHON@`; rendering is data-safe
for arbitrary home paths.

The generated config intentionally fails closed until every operational
placeholder is replaced. Set all of these explicitly:

- Himalaya account, inbox, and archive mailbox;
- Discord channel and user ID;
- the one allowed Calendar name;
- absolute Himalaya, Hermes, SQLite database, poll-lock, and report-lock paths.

Unknown keys, unresolved placeholders, relative paths, control characters, and
invalid Discord identifiers are rejected. Himalaya account and mailbox values
are used consistently in CLI arguments, IMAP SELECT commands, source identity,
and reconciliation. The application never reads Himalaya credentials or config.
Hermes uses its configured default provider and model.

`max_mail_bytes = 0` is deliberate: raw messages can consume unbounded local
memory so that no valid message is skipped by a default byte cap. Model-facing
fields remain bounded. Operators who prefer a hard local-memory ceiling may set
a positive byte count.

## Runtime commands

These commands have live effects and belong only in authorized deployment:

```sh
/absolute/path/to/python3.14 /absolute/home/.local/libexec/local-agentic-triage/local-agentic-triage.pyz mail poll
/absolute/path/to/python3.14 /absolute/home/.local/libexec/local-agentic-triage/local-agentic-triage.pyz report send --deliver
```

Put `--config /absolute/path/config.toml` before the command to override the
platform default. Poll and report use independent nonblocking locks. Exit 0 is
success, 1 is an operational failure, 2 is CLI misuse, and 75 means the lock is
busy. Logs contain technical stages, fixed error codes, and numeric IMAP UIDs,
never private mail or model content.

The poll LaunchAgent runs every 300 seconds. The report LaunchAgent runs at
local 18:00. See [architecture](docs/architecture.md) for retry and delivery
semantics and [security](docs/security.md) for privacy boundaries.
