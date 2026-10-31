# Architecture and failure semantics

`common` owns config and locking; `classifier` owns the exact packaged prompt,
strict JSON validation and the fixed loopback DMR client; `events` owns the
Hermes event contract; `mail` owns Himalaya, MIME and poll orchestration;
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
sent date and body go only to the fixed local classifier. The sent date is a
separate JSON field, allowing relative dates to resolve against the message.
Malformed MIME, oversized input, invalid/truncated classification, and failed
calendar processing cause no disposition.

The classifier prompt is copied exactly from the approved document and pinned
by SHA-256 in tests. The model is exactly docker.io/ai/qwen35-mail:16k at
http://127.0.0.1:12434/engines/v1/chat/completions. Proxies and redirects are
disabled. Temperature is 0, `chat_template_kwargs.enable_thinking` is false, JSON-object format is requested,
and output has a 2048-token budget. No hosted fallback exists.

## Calendar and disposition

Only the configured calendar name, event candidates, and minimal nearby events
from that calendar pass to Hermes as JSON
on stdin after the packaged event prompt and explicit delimiter. Both arrays,
including all titles, are untrusted data. Hermes uses its default provider/model
with `--toolsets none --ignore-rules`, with no skill or tool access. Hermes only
semantically checks and authorizes: different titles and slightly different dates
or times can identify the same real-world event. Clear matches return existing;
ambiguous matches return skipped/possible_duplicate. Only create authorizes a write.

`events/calendar.py` owns a fixed JXA program invoked by `/usr/bin/osascript` with
shell=False and a timeout. Event values travel solely as JSON on stdin, never
as executable source. The adapter requires exactly one calendar with the configured name,
reads only overlapping events in each candidate's +/-7-day window (full days
for all-day candidates), and returns title/start/end/all_day only. It validates
creates, rejects incomplete/uncertain/past events, and creates only title, start,
end and all-day status. A null all-day end is serialized as the next exclusive
day; explicit ends are preserved. It never edits, deletes, sets attendees,
alarms, location or notes, or accesses another calendar's events. It reads the
created UID back from that calendar and verifies exact title and temporal start/end
values (timezone-equivalent timestamps compare equally). Any failure blocks
mail disposition. Search failure prevents even invoking Hermes.

Decision results contain exactly index/outcome/reason, one per candidate, under
status/results. create/existing require null reason; skipped/error use fixed
reason sets. Claimed created decisions, extra keys, duplicate indices and
inconsistent status are rejected. The final internal exact result retains each
index and replaces create with created only after verified readback. Titles
are bounded to 200 characters. Semantic matching remains Hermes' responsibility;
Calendar effects are structurally constrained by deterministic code.

For leave, successful events are followed by
`himalaya imap store -m <inbox> --flag \Flagged -- UID`, without changing Seen
or writing SQLite. The standard system flag makes explicit leave decisions visible
in mail clients. Existing HermesLeave keywords are neither migrated nor removed
and do not count as processed: those Inbox messages are candidates again unless
they independently already have `\Flagged`.

For archive, deterministic code first renders and validates the eventual single
Discord report row against `discord_limit`. Oversized rows fail before events,
SQLite, Seen, or MOVE and are never split or truncated. Successful events are
then followed by INSERT OR IGNORE of an immutable prepared report row with
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
never substituted. Legacy rows created before either value was retained render
`data wiadomości niedostępna` and do not block the rest of the report. Each row is
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
