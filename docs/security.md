# Security boundaries

Runtime umask is 077. Installation uses private directories and files; launchd
sets HOME, HERMES_HOME and umask explicitly. Configuration requires explicit
executable/data paths, account/mailbox names, Discord targets, calendar name and
limits. Himalaya v2.1.0 is the sole mail boundary and owns its already-configured
credentials. Python never reads its config, retrieves passwords or logs in.

Message bodies and headers are transient independent destination and sparse-extraction input. SQLite retains
only the required sender/summary reporting data, the normalized message Date
header or IMAP INTERNALDATE fallback, technical source identity and processing timestamps. Protect the data directory and backups accordingly. Mail
content, headers, classifier output, sender, subject, summaries and event fields
never enter application logs. The numeric IMAP UID is intentionally logged with
poll actions and failures so a failed message can be reproduced. Failure logs use
fixed technical codes, without exception text or captured stdout/stderr. Himalaya
logging is explicitly disabled.
Event-enrichment failures log only the UID, a fixed technical error code, and an
allowed numeric subprocess return code when present. They do not enter the fatal
mail-failure count or prevent leave/archive disposition.
Discord delivery is fail-closed: the report function requires an explicit runner,
and the CLI requires `report send --deliver`. Tests and review probes that omit
either gate cannot call the real Hermes sender or advance report state.
Sparse extraction receives the same bounded mail JSON as destination/summary,
without receiving that pass's output. Later duplicate checking receives only sparse
title/start/supplied end and minimal nearby title/start/end/all_day rows from the
configured calendar, without mail provenance. No estimated end can enter duplicate
checking. Completion sees only index/title/start for timed no_match candidates
with a null end, without nearby rows. Its closed patch contract cannot replace
any supplied field. Python materializes only a validated missing-end patch.

All four passes use the same fixed loopback DMR transport and model, with
separate small prompts and strict closed schemas. The client disables proxies,
rejects redirects, bounds time and response bytes, strictly decodes both HTTP
envelope and content, and requires finish_reason stop. Temperature is 0 and
thinking is disabled. There is no hosted fallback. JSON key sets, duplicate-key
rejection, typed fields, date syntax, cross-field semantics, and exact ordered
batch coverage (rejecting bool indices) are independently enforced in Python.
Nearby payloads fail closed over budget rather than truncate evidence.

Destination/summary failures remain fatal/retryable. Extraction, duplicate,
completion, and Calendar failures are optional and do not block disposition.
Complete duplicate and completion batches and all materialized results validate,
with a batch temporal recheck, before the first Calendar write. No model stage
has write authority. Fixed JXA reads only the configured calendar and creates
validated events there, then verifies exact title/start/end by readback. Calendar
and report commands use argument lists, shell=False, stdin data, captured output,
checked exit status, and timeouts. Hermes sends rendered report text without an LLM.

Calendar search alone normalizes a known equal-date one-day all-day adapter row
to an exclusive next-day end; create readback is never normalized.
All untrusted values use stdin JSON, never executable JXA interpolation. No
Calendar edits/deletes, attendees, alarms, location or notes are exposed.

Every mail read/store/move has a fresh SELECT UIDVALIDITY and exact UID-existence
guard, including a recheck after Seen before MOVE. Separate Himalaya CLI sessions
prevent atomic binding: mailbox recreation between guard and operation remains
an explicitly accepted residual race in this simple design. Credentials and
direct IMAP clients remain outside the boundary.

No live mailbox, DMR, calendar or Discord operations belong in tests. Build,
compile and offline smoke tests are sufficient for local review. Deployment,
configuration review and service activation are a separate parent-agent step.
