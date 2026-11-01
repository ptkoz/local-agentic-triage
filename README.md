# local-agentic-triage

Local, standard-library-only email triage for Python 3.14. Himalaya v2.1.0 is
the sole mail boundary. One fixed loopback Qwen model performs four separate passes with closed strict
JSON Schemas: destination/summary, independent sparse event extraction, duplicate
checking against nearby Calendar rows, and missing timed-end completion only
after no_match. Guessed values never influence duplicate checking. A deterministic
single-calendar adapter creates and verifies events, and Hermes sends immediate archive rows and a daily
Discord summary without participating in classification. Destination failures are
fatal/retryable; extraction and later event failures never block leave/archive.

## Offline development

```sh
make test
make compile
make smoke
```

Tests use synthetic data and never connect to mail, DMR, Calendar, Discord, or
other live services. Delivery requires an explicitly injected runner; direct `poll()` calls disable
immediate sends by default. The production `mail poll` CLI injects the runner
and sends each row after archive completion, including recovered archives.
Scheduled `report send` additionally requires `--deliver`; without it the command
exits before report-state access. Failed immediate sends leave ready rows for
`report send` to retry without failing the mail poll.

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
Hermes is used only to deliver the rendered Discord report.

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

For read-only investigation of a single classification failure, use `mail
diagnose --uid <UID>` rather than `mail poll`. See the [safe debugging
runbook](docs/debugging.md) for UID/UIDVALIDITY scope, error-code meanings,
privacy rules, and the exact no-mutation procedure.

The poll LaunchAgent runs every 300 seconds. The report LaunchAgent runs at
local 18:00. It retries pending rows first, then sends one mentioned summary
counting every archive in the frozen Europe/Warsaw interval, including rows
already sent immediately and intervals with zero archives. See [architecture](docs/architecture.md) for retry and delivery
semantics, [security](docs/security.md) for privacy boundaries, and the
[debugging runbook](docs/debugging.md) for production diagnosis.

## Deploy on a new macOS account

1. Install Python 3.14, Himalaya v2.1.0, Hermes, Docker Model Runner, and the
   local classification model. Configure Himalaya separately; never place mail
   credentials in this repository or in `config.toml`.
2. Clone the repository as the target macOS user and run:

   ```sh
   python3.14 scripts/install.py --python /absolute/path/to/python3.14
   ```

   This installs the zipapp, an unconfigured private config, logs, and two
   unloaded LaunchAgents. It does not start mail processing or report delivery.
3. Edit
   `~/Library/Application Support/local-agentic-triage/config.toml`. Use
   [`config.example.toml`](config.example.toml) as the complete field reference.
   `@HOME@` is replaced automatically by the installer; replace every remaining
   `@...@` placeholder. Keep the file mode at `0600`. Discord
   IDs, calendar/account names, executable paths, database paths, and lock paths
   belong only in this local file, never in Git.
4. Validate the complete configuration and installation without live effects:

   ```sh
   plutil -lint ~/Library/LaunchAgents/local.local-agentic-triage.poll.plist
   plutil -lint ~/Library/LaunchAgents/local.local-agentic-triage.report.plist
   /absolute/path/to/python3.14 ~/.local/libexec/local-agentic-triage/local-agentic-triage.pyz config validate
   ```

   `config validate` parses every field, rejects unresolved placeholders and
   invalid targets, and confirms that the configured Himalaya and Hermes paths
   are executable. It does not open the database or contact any service. The
   LaunchAgents use a minimal system `PATH`, so both executable paths must be
   absolute.
5. Before allowing live effects, verify the external dependencies separately:
   Himalaya v2.1.0 can access the configured account and both mailboxes; Docker
   Model Runner is running the model documented in `classifier/core.py` on its
   loopback endpoint; Hermes has valid Discord delivery credentials; and the target
   Calendar exists with Calendar Automation/TCC permission granted to the
   production process. A GUI login
   alone does not establish these permissions or start Docker Model Runner.
6. On a new account, perform one explicitly authorized controlled run before
   scheduling. Use an isolated test account/mailbox; `mail poll` processes every
   eligible message in the configured Inbox, so a single synthetic message is
   safe only after explicitly confirming that no other eligible messages exist.
   Invoke `mail poll` manually, inspect the resulting mailbox/calendar state and
   privacy-safe logs, then invoke `report send --deliver` and verify the target.
   Do not use a real mailbox as an unreviewed first-run probe.
7. After those checks and explicit production authorization, load both jobs from that
   account's active GUI session:

   ```sh
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.local-agentic-triage.poll.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.local-agentic-triage.report.plist
   launchctl print gui/$(id -u)/local.local-agentic-triage.poll
   launchctl print gui/$(id -u)/local.local-agentic-triage.report
   ```

   A logged-in GUI session is required because the pipeline depends on Docker
   Desktop and macOS Calendar/TCC access. To stop production, use `launchctl
   bootout gui/$(id -u) <plist-path>` for each plist. Runtime logs are under
   `~/Library/Logs/local-agentic-triage/`.
