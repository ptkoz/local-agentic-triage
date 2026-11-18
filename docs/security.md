# Security boundaries

Runtime umask is 077. Installation uses private directories and files; launchd
sets HOME, HERMES_HOME and umask explicitly. Configuration requires explicit
executable/data paths, account/mailbox names, Discord targets, calendar name and
limits. Himalaya v2.1.0 is the sole mail boundary and owns its already-configured
credentials. Python never reads its config, retrieves passwords or logs in.

Message bodies and headers are transient independent summary, destination, and
sparse-extraction input. Mail content, headers, classifier output, sender, subject,
summaries and event fields
never enter application logs. The numeric IMAP UID is intentionally logged with
poll actions and failures so a failed message can be reproduced. Failure logs use
fixed technical codes, without exception text or captured stdout/stderr. Himalaya
logging is explicitly disabled.
Event-enrichment failures log only the UID, a fixed technical error code, and an
allowed numeric subprocess return code when present. They do not enter the fatal
mail-failure count or prevent leave/archive disposition.
Discord delivery requires an explicit runner: production `mail poll` injects
`subprocess.run`, while direct `poll()` calls fail closed before disposition.
Offline tests inject synthetic runners and never call the real Hermes sender.
Every valid mail is reported immediately after optional enrichment and before
either leave or archive disposition. Delivery failures leave the mail unchanged
and log only safe technical metadata.
Summary generation receives bounded mail JSON. Destination classification receives
the generated summary and the same bounded mail as separate untrusted fields. Sparse
extraction independently receives the bounded mail without either classification
output. Later duplicate checking receives the complete extracted event unchanged and
minimal nearby title/start/end/all_day/location rows freshly read from the configured
calendar, without mail provenance. Other candidates from the same mail never enter
duplicate checking, and no completed or guessed value can enter duplicate checking.
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

Summary, destination, rendering, and Discord-delivery failures remain fatal and
retryable. Extraction, duplicate, completion, eduVULCAN, Calendar, and attachment
failures are optional and do not block notification or disposition. Events
are processed sequentially; each completion result is fully validated immediately
before its own iCalendar attachment is prepared. No model stage has write authority,
and triage has no Calendar write capability: fixed JXA accepts only the search
operation, reads only the configured calendar, and has no create, edit, or delete
path. Calendar and Discord commands use argument lists, shell=False, stdin data,
captured output, checked exit status, and timeouts. Hermes sends rendered notification
text without an LLM.

Calendar search normalizes a known equal-date one-day all-day adapter row to an
exclusive next-day end. All untrusted values use stdin JSON, never executable JXA
interpolation.

Each prepared event becomes one RFC 5545 VCALENDAR document with CRLF lines,
escaped and folded TEXT values, UTC timed or VALUE=DATE all-day DTSTART/DTEND,
and a UID derived from a hash of the candidate index within the mail and the validated
title, interval, all-day flag, and location, so identical candidates from one mail never
share a UID. Attendees, alarms, notes, and descriptions are never exported. The
document is private event content: it is never logged or passed in argv. For
delivery only, each attachment is written to a fixed-pattern filename in a fresh
0700 temporary directory as a 0600 file, referenced from Hermes stdin, and the
directory is removed after the send, whether it succeeds or fails. If attachments
cannot be materialized, the notification is sent without them and only a fixed
`mail_attachment_failed` code is logged.

Hermes treats `MEDIA:` anywhere in its stdin, in any case, as a local-file attachment
directive. Before trusted attachment lines are appended, every `MEDIA:` in the rendered
notification (which carries untrusted sender and summary text) has its colon replaced by
a same-length lookalike, with or without attachments. Only the generated bare
`MEDIA:<path>` lines for freshly materialized files remain directives; text without
`MEDIA:` is sent unchanged.

Every mail read/store/move has a fresh SELECT UIDVALIDITY and exact UID-existence
guard immediately before the operation. Separate Himalaya CLI sessions
prevent atomic binding: mailbox recreation between guard and operation remains
an explicitly accepted residual race in this simple design. Credentials and
direct IMAP clients remain outside the boundary.

No live mailbox, DMR, calendar or Discord operations belong in tests. Build,
compile and offline smoke tests are sufficient for local review. Deployment,
configuration review and service activation are a separate parent-agent step.

The eduVULCAN handoff sends raw mail only to the fixed local
`agentic-vulcan-orchestrator` launcher. The parsed `From` match is routing, not authentication. Child output,
mail content, and exception text never enter triage logs. Exact raw bytes are spooled
only to an anonymous private temporary file whose open descriptor becomes detached
orchestrator stdin; both child output streams go to `/dev/null`. Tests mock the boundary
and never run `agentic-vulcan-orchestrator`.
