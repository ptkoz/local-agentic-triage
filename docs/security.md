# Security boundaries

Runtime umask is 077. Installation uses private directories and files; launchd
sets HOME, HERMES_HOME and umask explicitly. Configuration requires explicit
executable/data paths, account/mailbox names, Discord targets, calendar name and
limits. Himalaya v2.1.0 is the sole mail boundary and owns its already-configured
credentials. Python never reads its config, retrieves passwords or logs in.

Message bodies and headers are transient independent summary, destination, and sparse-extraction input. SQLite retains
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
Discord delivery requires an explicit runner: direct poll() calls disable
immediate delivery by default, while production mail poll explicitly injects
subprocess.run after archive completion. Scheduled reporting additionally requires
`report send --deliver`. Offline tests inject synthetic runners; they never call
the real Hermes sender. Delivery failures retain ready/unreported rows and log
only safe technical metadata.
Summary generation receives bounded mail JSON. Destination classification receives
the generated summary and the same bounded mail as separate untrusted fields. Sparse
extraction independently receives the bounded mail without either classification
output. Later duplicate checking receives the complete extracted event unchanged and
minimal nearby title/start/end/all_day/location rows from the configured calendar,
without mail provenance. No completed or guessed value can enter duplicate checking.
Completion receives the complete extracted event without nearby rows, must preserve
directly supplied fields, and may fill missing values only from supported evidence.

All five passes use the same fixed loopback DMR transport and Ministral model, with
separate small prompts and strict closed schemas. The client disables proxies,
rejects redirects, bounds request time, strictly decodes the complete HTTP envelope
and model content, and requires finish_reason stop. Temperature is 0; no model-specific
reasoning toggle is sent. There is no hosted fallback. JSON key sets, duplicate-key
rejection, typed fields, date syntax, cross-field semantics, and authorization to
preserve supplied event fields are independently enforced in Python. Event and nearby
payloads fail closed over budget rather than truncate evidence.

Summary and destination failures remain fatal/retryable. Extraction, duplicate,
completion, and Calendar failures are optional and do not block disposition. Events
are processed sequentially; each completion result is fully validated immediately
before its own Calendar write, and that write is exactly read back before processing
the next candidate. No model stage has write authority. Fixed JXA reads only the
configured calendar and creates validated events there, then verifies exact
title/start/end/all-day/location values by readback. Calendar
and report commands use argument lists, shell=False, stdin data, captured output,
checked exit status, and timeouts. Hermes sends rendered report text without an LLM.

Calendar search alone normalizes a known equal-date one-day all-day adapter row
to an exclusive next-day end; create readback is never normalized.
All untrusted values use stdin JSON, never executable JXA interpolation. No
Calendar edits/deletes, attendees, alarms, or notes are exposed. A validated optional
location may be created and verified.

Every mail read/store/move has a fresh SELECT UIDVALIDITY and exact UID-existence
guard, including a recheck after Seen before MOVE. Separate Himalaya CLI sessions
prevent atomic binding: mailbox recreation between guard and operation remains
an explicitly accepted residual race in this simple design. Credentials and
direct IMAP clients remain outside the boundary.

No live mailbox, DMR, calendar or Discord operations belong in tests. Build,
compile and offline smoke tests are sufficient for local review. Deployment,
configuration review and service activation are a separate parent-agent step.

The eduVULCAN handoff sends original raw mail only to the configured absolute
agentic-vulcan launcher. The From match is routing, not sender authentication;
mail delivery authentication remains outside this integration. No child output,
mail content, configured paths, or exception text enters triage logs. Only a strict
success receipt is counted as delivered; nonzero status, invalid receipt, timeout, and
OS errors become the fixed `agentic_vulcan_failed uid=<uid> result=failed` log line
and never block ordinary triage. Config validation checks launcher
executability and config-file existence without opening private agentic-vulcan
state or executing the launcher. Tests mock this boundary and never invoke live
agentic-vulcan or eduVULCAN.
