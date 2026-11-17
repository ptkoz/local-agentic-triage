# local-agentic-triage

Local, standard-library-only email triage for Python 3.14. Himalaya v2.1.0 is
the sole mail boundary. One fixed loopback Ministral model performs five separate passes with closed strict
JSON Schemas: summary generation, destination classification, independent sparse event extraction,
duplicate checking against nearby Calendar rows, and event completion only
after `create`. Guessed values never influence duplicate checking. A deterministic
single-calendar adapter creates and verifies events. Every mail receives a mandatory
summary and an immediate neutral Discord notification via Hermes before the IMAP
disposition is applied. Destination failures are fatal/retryable; extraction and
later event failures never block leave/archive.

`classifier/resources.py` is the single owner and loader of every frozen model
prompt and output schema; each resource is named after its consuming public
classifier function. `classifier/helpers.py` owns only model transport and
limits. `events` contains only the Calendar adapter and deterministic Calendar validation.
`mail/event_pipeline.py` orchestrates fresh lookup, boolean duplicate checking,
completion, horizon enforcement, and verified creation for each candidate.

## Offline development

```sh
make test
make compile
make smoke
```

Tests use synthetic data and never connect to mail, DMR, Calendar, Discord, or
other live services. Delivery requires an explicitly injected runner; direct `poll()` calls fail
closed before disposition. The production `mail poll` CLI injects the runner
and sends each notification immediately before applying the IMAP disposition.
A failed Discord send blocks the disposition; there is no durable state to retry
from, so a crash after send but before disposition can cause a duplicate notification
on the next poll.

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

### Upgrade from the scheduled-reporter release

Stop both legacy jobs before installing so the old poller cannot keep SQLite open
and deleting the reporter plist does not leave its loaded 08:00/18:00 job alive:

```sh
launchctl bootout gui/$(id -u)/local.local-agentic-triage.poll
launchctl bootout gui/$(id -u)/local.local-agentic-triage.report
python3.14 scripts/install.py --python /absolute/path/to/python3.14
```

`bootout` may report that a job is absent when it was not loaded. The installer
preserves the existing config, accepts its obsolete reporting keys as ignored
compatibility input, and removes the obsolete reporter plist, report log, and SQLite files.
Bootstrap only `local.local-agentic-triage.poll.plist` afterward and verify that
`launchctl print gui/$(id -u)/local.local-agentic-triage.report` does not find a job.

The generated config intentionally fails closed until every operational
placeholder is replaced. Set all of these explicitly:

- Himalaya account, inbox, and archive mailbox;
- Discord channel;
- the one allowed Calendar name;
- absolute Himalaya, Hermes, and poll-lock paths.

Unknown keys, unresolved placeholders, relative paths, and control characters are
rejected. Himalaya account and mailbox values are used consistently in CLI arguments
and IMAP SELECT commands. The application never reads Himalaya credentials or config.
Hermes delivers the rendered Discord notification for each mail.

`max_mail_bytes = 0` is deliberate: raw messages can consume unbounded local
memory so that no valid message is skipped by a default byte cap. Model-facing
fields remain bounded. Operators who prefer a hard local-memory ceiling may set
a positive byte count.

## Runtime commands

These commands have live effects and belong only in authorized deployment:

```sh
/absolute/path/to/python3.14 /absolute/home/.local/libexec/local-agentic-triage/local-agentic-triage.pyz mail poll
```

Put `--config /absolute/path/config.toml` before the command to override the
platform default. Poll uses a nonblocking run lock. Exit 0 is success, 1 is an
operational failure, 2 is CLI misuse, and 75 means the lock is busy. Logs contain
technical stages, fixed error codes, and numeric IMAP UIDs, never private mail or
model content.

For read-only investigation of a single classification failure, use `mail
diagnose --uid <UID>` rather than `mail poll`. See the [safe debugging
runbook](docs/debugging.md) for UID/UIDVALIDITY scope, error-code meanings,
privacy rules, and the exact no-mutation procedure.

The poll LaunchAgent runs every 300 seconds. See
[architecture](docs/architecture.md) for the per-mail pipeline details,
[security](docs/security.md) for privacy boundaries, and the
[debugging runbook](docs/debugging.md) for production diagnosis.

## Deploy on a new macOS account

1. Install Python 3.14, Himalaya v2.1.0, Hermes, Docker Model Runner, and the
   local classification model. Configure Himalaya separately; never place mail
   credentials in this repository or in `config.toml`.
2. Clone the repository as the target macOS user and run:

   ```sh
   python3.14 scripts/install.py --python /absolute/path/to/python3.14
   ```

   This installs the zipapp, an unconfigured private config, the poll log, and one
   unloaded LaunchAgent. It does not start mail processing or Discord delivery.
3. Edit
   `~/Library/Application Support/local-agentic-triage/config.toml`. Use
   [`config.example.toml`](config.example.toml) as the complete field reference.
   `@HOME@` is replaced automatically by the installer; replace every remaining
   `@...@` placeholder. Keep the file mode at `0600`. Discord
   channel, calendar/account names, executable paths, and lock paths
   belong only in this local file, never in Git.
4. Validate the complete configuration and installation without live effects:

   ```sh
   plutil -lint ~/Library/LaunchAgents/local.local-agentic-triage.poll.plist
   /absolute/path/to/python3.14 ~/.local/libexec/local-agentic-triage/local-agentic-triage.pyz config validate
   ```

   `config validate` parses every field, rejects unresolved placeholders and
   invalid targets, and confirms that the configured Himalaya and Hermes paths
   are executable. It does not contact any service. The LaunchAgent uses a minimal
   system `PATH`, so both executable paths must be
   absolute.
5. Before allowing live effects, verify the external dependencies separately:
   Himalaya v2.1.0 can access the configured account and both mailboxes; Docker
   Model Runner is running the model documented in `classifier/helpers.py` on its
   loopback endpoint; Hermes has valid Discord delivery credentials; and the target
   Calendar exists with Calendar Automation/TCC permission granted to the
   production process. A GUI login
   alone does not establish these permissions or start Docker Model Runner.
6. On a new account, perform one explicitly authorized controlled run before
   scheduling. Use an isolated test account/mailbox; `mail poll` processes every
   eligible message in the configured Inbox, so a single synthetic message is
   safe only after explicitly confirming that no other eligible messages exist.
   Invoke `mail poll` manually and inspect the resulting Discord notification,
   mailbox/calendar state, and privacy-safe logs.
   Do not use a real mailbox as an unreviewed first-run probe.
7. After those checks and explicit production authorization, load the poller from that
   account's active GUI session:

   ```sh
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.local-agentic-triage.poll.plist
   launchctl print gui/$(id -u)/local.local-agentic-triage.poll
   ```

   A logged-in GUI session is required because the pipeline depends on Docker
   Desktop and macOS Calendar/TCC access. To stop production, use `launchctl
   bootout gui/$(id -u) ~/Library/LaunchAgents/local.local-agentic-triage.poll.plist`.
   Runtime logs are under
   `~/Library/Logs/local-agentic-triage/`.

## eduVULCAN forwarding

Mail whose parsed `From` address equals `noreply@vulcan.net.pl` case-insensitively
is piped as exact raw bytes to `~/.local/bin/agentic-vulcan-orchestrator --config
~/Library/Application Support/agentic-vulcan/config.json` after the mandatory notification has been rendered and validated.
The paths are fixed and resolved from `$HOME`; there is no triage configuration.
Triage spools the exact bytes to a private anonymous temporary file, starts the
orchestrator in a detached session with that file as stdin, and immediately continues
normal disposition without waiting for completion. A successful spawn is logged as a
fixed `agentic_vulcan_started` line; a spawn failure uses `agentic_vulcan_failed`.
Child output is discarded and triage never checks, retries, or supervises the detached
process. The orchestrator owns identify/read repair and its installed rerun. `mail
diagnose` never invokes it.
