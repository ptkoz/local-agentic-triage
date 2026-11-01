# Architecture and failure semantics

`common` owns config and locking; `classifier` owns destination/summary validation
and the fixed loopback DMR client; `events` owns sparse extraction, duplicate
checking, and missing-end completion; `mail` owns Himalaya, MIME and poll orchestration;
`reporting` owns SQLite, rendering and delivery. Future SMS/log sources should
be separate explicit modules. No plugin framework or dependency injection
framework is needed; constructors/functions accept simple fakes in tests.

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

## Four local-model passes

All four passes use exactly docker.io/ai/qwen35-mail:16k at
http://127.0.0.1:12434/engines/v1/chat/completions, temperature 0,
`chat_template_kwargs.enable_thinking=false`, and a 2048-token output budget.
The shared transport disables proxies, rejects redirects, limits response bytes,
uses a timeout, strictly decodes envelope and content, rejects duplicate keys and
non-finite constants, and requires finish_reason stop. Each pass has a separate
small packaged operational prompt and closed strict JSON Schema. Python validates
all conditional, temporal, and coverage rules independently. No model has tools
or write authority. The byte-pinned classifier/prompt.md is a compatibility
artifact; runtime uses classifier/destination.md.

1. Destination/summary receives bounded mail JSON and returns destination and
   summary only. Leave requires null summary; archive requires a factual Polish
   summary regardless of input language. Failure is fatal/retryable because disposition and reporting depend
   on it.
2. Sparse extraction independently receives the same bounded mail JSON, never
   destination output. It returns events containing exactly title/start/end/date_note.
   Missing fields are null; confirmed events with uncertain date alternatives
   remain candidates with those alternatives in a note and null start/end.
   Timed timestamps must be timezone-aware ISO, defaulting to Europe/Warsaw
   for resolved local date/time without a supplied zone; explicit zones take
   precedence. Exact supplied date components and years are preserved; explicit
   unambiguous relative dates resolve from sent_date. Single-date all-day events
   have a date start and null end unless an explicit range/end is supplied. Supplied
   ranges must have compatible types and end strictly after start. Malformed
   output is rejected rather than repaired. This and every later event failure
   are optional enrichment failures and cannot block leave/archive.
3. After deterministic eligibility gates and Calendar nearby lookup, duplicate
   checking receives only title/start/supplied end, plus nearby title/start/end/all_day
   rows and the configured calendar name. It never receives date_note or estimated
   values. Null end means unknown, never difference; offsets compare as absolute
   instants. Timing alone is insufficient, and unrelated concurrent events are
   no_match. Each events[i] compares to nearby[i], an aligned array of
   rows, and earlier candidates events[0:i], never later candidates; results cover candidates in order, never nearby rows. Clear shared
   specific identity at the same instant is existing; ambiguous shared identity or modest
   time differences are possible_duplicate. Results contain index/outcome only:
   existing, possible_duplicate, or no_match. After full duplicate-batch validation,
   Python suppresses later candidates with exactly equal sparse title/start/end
   before completion; semantic title variants remain the model’s responsibility.
   Python maps these to existing,
   skipped/possible_duplicate, or eligibility for creation/completion.
4. Only timed no_match candidates with null end enter completion. Its input is
   index/title/start only, without nearby rows. Each output contains index/outcome/
   reason/estimated_end only. Complete requires null reason and an aware end
   strictly after start; skipped requires missing_end and null estimate. No fixed
   maximum duration applies. This patch can fill only a previously null end;
   it cannot replace title, start, date_note, or supplied end. Complete supplied
   timed events and all-day events bypass completion.

Duplicate and completion results require exact ordered batch coverage and integer
indices (bool is rejected). The entire duplicate batch and required completion
batch validate before patches are materialized. Python then canonicalizes every
creation, validates all final results, and reapplies the temporal guard to the
whole creation batch before the first write. Each actual create also repeats the
guard and verifies readback. A later write/readback failure can leave an earlier
verified creation in place; retries use nearby duplicate checking.

Event-model user payloads have conservative context budgets and fail closed
without truncation: omitting nearby rows could hide duplicates. All input strings
are untrusted data in separate user-message JSON, never prompt instructions.

## Calendar and disposition

`events/calendar.py` owns a fixed JXA program invoked by `/usr/bin/osascript` with
shell=False and a timeout. Event values travel solely as JSON on stdin, never
as executable source. The adapter requires exactly one calendar with the configured name,
reads only overlapping events in each candidate's +/-7-day window (full days
for all-day candidates), and returns title/start/end/all_day only. It validates
search rows before use. Calendar's one-day all-day search representation may
return equal valid ISO dates; only `nearby` copies such a row and advances its
end to the next exclusive ISO day. Other search rows and every create/readback
value retain their original representation. It validates
creates, rejects incomplete/uncertain/past events, and creates only title, start,
end and all-day status. A null all-day end is serialized as the next exclusive
day; explicit ends are preserved. It never edits, deletes, sets attendees,
alarms, location or notes, or accesses another calendar's events. It reads the
created UID back from that calendar and verifies exact title and temporal start/end
values (timezone-equivalent timestamps compare equally). Any search, model,
validation, create, or readback failure is contained as an event-enrichment
failure and does not block mail disposition. Search failure prevents even
invoking the model.

Before Calendar or later model stages, fixed Python gates extracted events.
Unresolved date notes become skipped/uncertain_date; missing starts become
skipped/missing_date. Timed starts must be strictly more than now+72 hours;
all-day dates must be strictly later than local today+3 days. Other starts become
skipped/too_soon. Only eligible candidates reach nearby lookup and duplicate
checking; results map back to original indices alongside deterministic skips.
Titles are nonempty and bounded to 200 characters. The final internal report
contains index/outcome/reason, with created returned only after verified writes.
Semantic matching remains the local model's responsibility; Calendar effects
remain structurally constrained by deterministic code.

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
configured archive. Success sets ready_at. A prepared row found
on retry preserves the original sender/summary, does not fetch the body, does
not classify again, and does not rerun events. It retries only IMAP finalization.
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
never reruns classification or events, and adds no queue or schema columns.
If a Calendar call succeeds before a crash preceding prepare, the next call
relies on configured-calendar semantic search to avoid duplication. A Seen update can succeed
before a failed MOVE, and is safely repeated on retry.

## Reports

Ready, unreported rows sort by created_at then source_key. A new empty window
sends a short notification without a mention and advances the durable boundary;
a retry after every row was already acknowledged only completes its stale window.
A nonempty run first sends one introduction with the configured user's mention
and Europe/Warsaw period. It then sends exactly one Discord message per report
row in the form `Od **…** z dnia …` followed by the summary as a Discord quote.
The displayed date comes from the message's valid Date header or, when that is
missing, malformed or lacks a timezone, from its IMAP INTERNALDATE. Both are
captured before disposition and converted to Europe/Warsaw; processing time is
never substituted. Rows lacking a retained Date or INTERNALDATE render `data
wiadomości niedostępna` and do not block the rest of the report. Each row is
marked reported immediately after its own send succeeds. A failed send leaves
that row and all following rows pending under the frozen window. A record that
cannot fit the configured Discord limit fails before any delivery; records are
not split or truncated. Sanitization flattens whitespace/control characters and
neutralizes Discord Markdown, mentions and links while preserving ordinary email
addresses. There is no LLM call for report delivery.

Discord delivery is at least once: a crash after sending a record but before
marking it can cause that record to be duplicated on retry. There is no remote
idempotency token in the required send command. No success is recorded before
the subprocess exits successfully.
