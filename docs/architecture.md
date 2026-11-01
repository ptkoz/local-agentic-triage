# Architecture and failure semantics

- `common` owns configuration and locking.
- `classifier` owns destination/summary validation and the fixed loopback DMR
  client.
- `events` owns structural extraction, per-event duplicate checking, full event
  completion, final creation validation, and the deterministic Calendar adapter.
- `mail` owns Himalaya, MIME parsing, and poll orchestration.
- `reporting` owns SQLite state, report rendering, and delivery.
- Future SMS and log sources remain separate explicit modules.
- The project does not need a plugin or dependency-injection framework;
  constructors and functions accept small fakes in tests.

## Mail

Himalaya v2.1.0 is the only mail boundary, with explicitly configured account,
mailboxes, absolute executable path, backend `imap`, and logging disabled.
Python never loads Himalaya configuration or handles authentication. Raw
commands on stdin select the configured inbox with IMAP quoted-string escaping
and issue `UID SEARCH UNFLAGGED`, regardless of Seen.
SELECT must succeed with numeric UIDVALIDITY and `\Flagged` in PERMANENTFLAGS;
SEARCH must have one successful tagged completion and unique numeric UIDs.
Identity combines account, mailbox, UIDVALIDITY and UID; all numeric identities
are nonzero 32-bit unsigned values. No sequence-number commands are used. `imap store` uses `--` before the UID
to terminate its variadic flag argument.

`himalaya message read -m <inbox> --raw UID` preserves Seen. Raw MIME is fetched
without a size limit when `max_mail_bytes = 0` (the default), and stays bytes to
avoid lossy JSON conversion. This deliberately accepts unbounded local memory
use for one raw message; operators may set a positive limit. After MIME parsing,
every model-facing mail field is
bounded and the final compact JSON payload stays within 10,000 UTF-8 bytes, with
an explicit truncation marker on each shortened field.
A positive `max_mail_bytes` remains an optional raw-message limit. Mutations use
JSON output, validating the exact success string emitted by v2.1.0; raw protocol
parsing is limited to SELECT and UID SEARCH. Before read, leave or archive, another SELECT and
exact `UID SEARCH UID <uid>` checks UIDVALIDITY and Inbox presence without
filtering `\Flagged`. The same guard runs again after Seen and immediately
before MOVE. A changed validity or missing UID stops the operation.
Himalaya CLI calls create separate IMAP sessions: SELECT/search and the following
read/store/move cannot bind atomically. Mailbox recreation between the final
guard and the next command remains an explicitly accepted residual race for
this simple Himalaya-only design; no direct IMAP/authentication path is added.
MIME processing
prefers nonattachment text/plain, falls back deterministically to HTML text,
ignores script/style content, and skips attachment subtrees. Subject, sender,
sent date and body go independently to the destination and extraction passes. The sent date is a
separate JSON field, allowing relative dates to resolve against the message.
Malformed MIME, oversized input, and invalid/truncated destination classification
cause no disposition. Event processing is optional enrichment: its failures are logged
and do not block mail disposition.

## Four local-model stages

All model operations use exactly docker.io/ai/qwen35-mail:16k at
http://127.0.0.1:12434/engines/v1/chat/completions, temperature 0,
`chat_template_kwargs.enable_thinking=false`, and a 2048-token output budget.
The shared transport disables proxies, rejects redirects, uses a timeout of at
least 300 seconds, strictly decodes the API envelope and model content, rejects
duplicate JSON keys and non-finite constants, and requires `finish_reason=stop`.
There is no model-response byte cap: a response that finishes with `stop` and
fits the model's token limit is read and handled in full. Each operation has a
separate packaged operational prompt and closed strict JSON Schema. No model has
tools or write authority.

The four entries below are logical stages, not a fixed four-request budget.
Destination and extraction run once per mail. Duplicate checking and completion
run separately for each extracted event, so a mail containing N events can make
2 + N duplicate requests plus as many completion requests as remain eligible for
creation.

1. **Destination and summary.** The first request receives bounded mail JSON and
   returns only destination and summary. Leave requires a null summary; archive
   requires a factual Polish summary regardless of input language. Structural
   and destination/summary coupling validation remain fatal and retryable because
   disposition and reporting depend on this result.

2. **Event extraction.** The second request independently receives the same
   bounded mail JSON and returns an `events` array. Every event object has the
   structurally defined fields `title`, `start`, `end`, `date_note`, and
   `location`; values may be null where permitted by the schema. `location` is
   nullable and may contain an address or descriptive place only when the mail
   provides direct evidence. Neither extraction nor completion may guess it.
   Explicit date components are preserved. A missing year defaults to the year
   from `sent_date`; when only the day of month is supplied, both month and year
   default to `sent_date`, without rolling forward. Explicit unambiguous relative
   dates also resolve against `sent_date`. Unzoned local times use Europe/Warsaw
   with the offset valid on the event date; explicit timezones are preserved.

   Python checks the closed JSON structure and field types required by the
   extraction schema, followed by the minimum validation needed for Calendar
   lookup. `start` must be either a valid `YYYY-MM-DD` all-day date or a valid
   timezone-aware ISO 8601 timestamp. A null, missing, or empty `start` is
   finalized locally as `skipped/missing_date`; a nonempty value that is not one
   of those supported date forms is finalized as `skipped/invalid_date`. In both
   cases the event never reaches Calendar, duplicate checking, completion, or
   creation.

   This gate does not normalize or repair the supplied start, does not evaluate
   whether it is in the past or more than three days away, and does not validate
   title, end, date_note, or location. Every field of a candidate that passes the
   start gate is preserved exactly and all five fields are forwarded to duplicate
   checking.

3. **Per-event duplicate checking.** Each extracted event that passed the start
   gate gets its own model request before any missing value is guessed. Python
   uses that already validated, unchanged start to fetch the corresponding
   Calendar window. The request then contains the complete extracted event
   unchanged, including `date_note` and `location`, together with the minimal
   nearby Calendar rows for that window. Nearby rows contain
   title/start/end/all_day/location. Each lookup reads the current Calendar state,
   including events successfully created while processing earlier candidates from
   the same message. No completed or guessed version of the current event is
   included.

   Every event reaching this stage has a syntactically valid start, so Python
   performs the corresponding Calendar window lookup before calling the duplicate
   model. A Calendar lookup error remains an event-enrichment failure and never
   becomes an empty successful result. The duplicate model returns only
   `existing`, `possible_duplicate`, or `create`. `existing` and
   `possible_duplicate` end processing for that event. Only `create` authorizes
   the completion stage. Duplicate checking never writes to Calendar.

4. **Per-event completion.** Each event authorized as `create` is sent unchanged
   in a separate request to the completion model. Python does not first validate
   its remaining fields, date syntax, start date, completeness, or temporal
   eligibility. The completion model receives all five extracted fields and
   returns one complete event object. It preserves start exactly and all other
   directly supplied values, and makes the best supported inference for missing
   fields needed for creation, including title, end, and resolution of
   `date_note`. It must not invent
   `location`; a null location stays null unless direct location evidence was
   already present in the extracted event.

   The completion response does not authorize a write by itself. Python performs
   semantic and temporal validation only after completion.

### Final validation and write authorization

For each completed event, deterministic Python validates the whole final object:

- the object has exactly the supported event fields and field types;
- title is nonempty and within the configured bound;
- start and end form a complete, compatible all-day or timezone-aware timed
  interval, and end is strictly after start;
- a retained `date_note` does not block creation when the required fields and
  concrete interval are valid;
- location is null or a bounded nonempty string and is passed through only as
  model data derived from direct evidence;
- the event's end, rather than its start, determines the three-day horizon: a
  timed end must be strictly later than `now + 72 hours`, and an all-day exclusive
  end date must be strictly later than local `today + 3 calendar days`; therefore
  a multi-day event may start sooner than the threshold and still qualify when it
  ends beyond it;
- the entire event is canonicalizable by the deterministic Calendar adapter.

### Sequential processing and write visibility

Events from one extraction are processed sequentially in extraction order. Each
event completes its entire lookup, duplicate decision, completion, final
validation, and verified Calendar creation (or terminal skip) before processing
the next event. Calendar lookups are fresh per event, never prefetched or reused
across candidates; creation is not deferred until a batch has been processed.

Consequently, if two extracted events describe the same occurrence, a successful
creation of the first is visible to the second event's lookup. An `existing` or
`possible_duplicate` decision terminates only the current event: no completion or
creation follows for that event, and any remaining candidates may still proceed.

Only a final event passing every rule is created. Validation failures produce a
fixed safe event outcome and no write. Each actual create repeats the temporal
guard and verifies exact readback before the next candidate begins. A later
write/readback failure can leave an earlier verified creation in place; retries
rely on fresh per-event Calendar lookup and duplicate checking.

Model user payloads retain conservative input/context budgets and fail closed
without truncating event or nearby data. All input strings are untrusted data in
user-message JSON, never system-prompt instructions.

## Calendar and disposition

`events/calendar.py` owns a fixed JXA program invoked by `/usr/bin/osascript` with
`shell=False` and a timeout. Event values travel solely as JSON on stdin, never
as executable source. The adapter requires exactly one calendar with the
configured name. Every event reaching duplicate checking already has a
syntactically valid extraction-provided start, which selects its +/-7-day search
window without inference or normalization. Search rows expose only title, start,
end, all-day status, and location and are validated at the adapter boundary. An
adapter/search failure fails that event's enrichment rather than masquerading as
an empty result.

The adapter creates only the final Python-validated title, start, end, all-day
status, and optional location. It never edits, deletes, sets attendees, alarms,
or notes, and never accesses another calendar's events. A null location is
omitted. A non-null location is written exactly as validated. The created UID is
read back from the configured calendar and title, temporal values, all-day status,
and location are verified. Any search, model, final-validation, create, or
readback failure is contained as optional event enrichment and does not block the
mail's leave/archive disposition.

There is deliberately no semantic or temporal eligibility gate before duplicate
checking or completion. The strictly-more-than-three-days policy is evaluated
from the final event end, not its start; interval checks and creation completeness
checks likewise run only on the final completed event. This lets a multi-day event
start within three days but still qualify when its validated end lies beyond the
threshold. It also lets the duplicate model see the original sparse extraction
and the completion model reason over every field before deterministic code
decides whether the event is safe and complete enough to create.

For leave, event processing is followed, whether successful or not, by
`himalaya imap store -m <inbox> --flag \Flagged -- UID`, without changing Seen
or writing SQLite. The standard system flag makes explicit leave decisions visible
in mail clients. Existing HermesLeave keywords are neither migrated nor removed
and do not count as processed: those Inbox messages are candidates again unless
they independently already have `\Flagged`.

For archive, deterministic code first renders and validates the eventual single
Discord report row against `discord_limit`. Oversized rows fail before events,
SQLite, Seen, or MOVE and are never split or truncated. Event processing is
then followed, whether successful or not, by INSERT OR IGNORE of an immutable prepared report row with
ready_at NULL. Only then does Himalaya verify UID presence, add raw `\Seen` by
UID with `imap store --flag \Seen -- UID`, and move from the configured inbox to
configured archive. Success sets ready_at, then immediately sends that row through
Hermes when a delivery runner is supplied. Successful delivery sets reported_at
to the actual delivery time. Delivery failure logs only safe metadata and leaves
the row ready/unreported without failing the poll; scheduled reporting retries it.
The production CLI explicitly injects subprocess.run; direct poll() calls default
to no delivery, and tests can inject a synthetic runner. A prepared row found
on retry preserves the original sender/summary, does not fetch the body, does
not classify again, and does not rerun events. It retries IMAP finalization, then
marks ready and attempts immediate delivery.
A ready row is already finalized. SQLite uses WAL, a busy timeout and parameter
binding. The `reports` table stores source_key, sender, the normalized message
timestamp (`sent_at`), summary, created_at, ready_at and reported_at; the single-row `report_state` table preserves one
in-progress report window across partial delivery failures.

There is no transaction spanning IMAP and SQLite. At the beginning and end of
every poll, reconciliation enumerates prepared rows with ready_at NULL. Only
keys for the configured account/inbox/UIDVALIDITY are considered. An exact UID search
without the `\Flagged` filter marks an absent UID ready: MOVE succeeded or the
message was otherwise removed. Present UIDs remain for normal prepared retry.
Search/validity failures leave rows unready and fail the poll. Reconciliation
never reruns classification or events; after marking an absent row ready it
attempts the same immediate delivery. It adds no queue or schema columns.
If a Calendar call succeeds before a crash preceding prepare, the next call
relies on configured-calendar semantic search to avoid duplication. A Seen update can succeed
before a failed MOVE, and is safely repeated on retry.

## Reports

Ready, unreported rows sort by created_at then source_key. Scheduled reporting
first sends pending rows whose ready_at is at or before the frozen end boundary,
acknowledging each successful delivery immediately with its actual delivery time.
There is no introduction. After all pending rows succeed, exactly one final
summary mentions the configured user and describes the Europe/Warsaw interval,
including a zero count. Its count includes all rows with ready_at in (start, end],
even those already delivered immediately. For example:
`<@USER_ID>, powyżej znajduje się informacja o **9 wiadomościach** przeniesionych do Archiwum między 1 lis 08:00 a 1 lis 18:00:`.
The singular uses `1 wiadomości` and `przeniesionej`.

The start comes only from report_meta.last_end_at, the previously completed
scheduled-report end, never from reported_at. Without that metadata the initial
start defaults to one day before the captured end. Existing frozen windows are
preserved across upgrades; legacy has_rows state remains compatible but is no
longer used for delivery decisions. No schema columns are added.

Each row is one Discord message in the form `**Archiwum**: Od **…** z dnia …`
followed by the summary as a Discord quote.
The displayed date comes from the message's valid Date header or, when that is
missing, malformed or lacks a timezone, from its IMAP INTERNALDATE. Both are
captured before disposition and converted to Europe/Warsaw; processing time is
never substituted. Rows lacking a retained Date or INTERNALDATE render `data
wiadomości niedostępna` and do not block the rest of the report. Each row is
marked reported immediately after its own send succeeds. A failed row send leaves
that row and all following rows pending under the frozen window and prevents the
final summary. A failed summary keeps the same window frozen; the next run sends
only the summary because successful row deliveries are already acknowledged.
Only successful summary delivery advances report_meta.last_end_at and clears
the window. Rows becoming ready after the captured end belong to the next window. A record that
cannot fit the configured Discord limit fails before any delivery; records are
not split or truncated. Sanitization flattens whitespace/control characters and
neutralizes Discord Markdown, mentions and links while preserving ordinary email
addresses. There is no LLM call for report delivery.

Discord delivery is at least once: a crash after sending a record but before
marking it can cause that record to be duplicated on retry. There is no remote
idempotency token in the required send command. No success is recorded before
the subprocess exits successfully.
