# Security boundaries

Runtime umask is 077. Installation uses private directories and files; launchd
sets HOME, HERMES_HOME and umask explicitly. Configuration requires explicit
executable/data paths, account/mailbox names, Discord targets, calendar name and
limits. Himalaya v2.1.0 is the sole mail boundary and owns its already-configured
credentials. Python never reads its config, retrieves passwords or logs in.

Message bodies and headers are transient local classifier input. SQLite retains
only the required sender/summary reporting data, the normalized message Date
header or IMAP INTERNALDATE fallback, technical source identity and processing timestamps. Protect the data directory and backups accordingly. Mail
content, headers, classifier output, sender, subject, summaries and event fields
never enter application logs. The numeric IMAP UID is intentionally logged with
poll actions and failures so a failed message can be reproduced. Failure logs use
fixed technical codes, without exception text or captured stdout/stderr. Himalaya
logging is explicitly disabled.
Discord delivery is fail-closed: the report function requires an explicit runner,
and the CLI requires `report send --deliver`. Tests and review probes that omit
either gate cannot call the real Hermes sender or advance report state.
The Hermes/calendar subsystem has its own local storage and logging policy;
only the configured calendar name, event candidates, and minimal nearby records
from that calendar, without mail provenance,
cross that boundary.

The fixed DMR endpoint bypasses HTTP proxies and rejects redirects. There is no
network model fallback. External commands use argument lists, shell=False,
stdin for event/report data, captured output, checked exit status, and timeouts.
Hermes chat receives no --model or
--provider. Hermes send delivers rendered text without an LLM.

Classifier and event prompts treat supplied content as untrusted data. Exact
JSON key sets, duplicate-key rejection, typed fields, date formats, completion
status and complete event-result coverage limit what model output can trigger.
The classifier also requests that exact outer shape with strict JSON Schema,
then independently applies conditional and temporal validation. Hermes output
uses bounded line-delimited stream framing: limited leading warnings are
discarded, session and exit status must match, tool records are forbidden, and
only the final result text reaches the unchanged decision validator.
Hermes has zero tools (`--toolsets none --ignore-rules`) and no skills; it only
semantically authorizes create/existing/skipped decisions. Fixed JXA reads only
the configured calendar and creates validated events there, then verifies
title/start/end by readback.
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
