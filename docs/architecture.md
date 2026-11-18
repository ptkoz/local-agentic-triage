# Architecture and failure semantics

## Component responsibilities

- `common` owns configuration, strict JSON decoding, and process locking.
- `classifier` owns LLM context preparation, inference, and response validation
  across all model-assisted decisions.
- `events` owns read-only Calendar access, event validation and eligibility
  policy, and iCalendar export.
- `mail` owns mail access, MIME parsing, the immutable source mail, and pipeline
  orchestration, including sequential per-candidate event processing.
- `reporting` owns per-mail notification rendering and Discord delivery,
  including delivery of prepared event attachments.
- Other input sources remain separate pipelines rather than plugins in the mail
  flow.

## Mail boundary

Himalaya is the only mail-system boundary. The application does not load mail
credentials or open a second IMAP connection.

Messages are identified by account, mailbox, UIDVALIDITY, and UID. The pipeline
revalidates mailbox identity before reads and dispositions so a recreated
mailbox or missing message fails closed. `Seen` is not a processing checkpoint;
messages selected for processing exclude those already marked `Flagged`.

Raw mail remains local. MIME parsing selects the useful message text, and the
classifier bounds all model-facing data. Invalid mail, failed classification,
or failed mandatory notification leaves the message eligible for a later retry.

## Per-mail flow

Each candidate follows one ordered flow:

1. Read and parse the mail.
2. Generate a mandatory nonempty summary.
3. Classify the disposition as `archive` or `leave`.
4. Render and validate the per-mail Discord notification.
5. Run optional Calendar enrichment and approved sender-specific handoffs.
6. Deliver the notification to Discord.
7. Apply the selected mail disposition.

Summary generation, disposition classification, notification validation,
Discord delivery, and mail disposition are mandatory stages. Failure in any of
them leaves the mail pending for retry. Optional enrichment failures are
contained and do not prevent notification or disposition.

## Local-model responsibilities

All inference uses the configured loopback model. Mail content is not sent to a
remote model, and models have no direct tool or write authority.

The model workflow has five logical responsibilities:

1. generate the summary;
2. classify the mail disposition;
3. extract possible calendar events;
4. assess each candidate against current Calendar data;
5. complete only candidates that are not duplicates.

Summary, classification, and extraction run once per mail. Duplicate assessment
and completion run independently for each event candidate. Summary and
classification remain separate so both `archive` and `leave` retain the same
mandatory summary.

Model responses are treated as untrusted data. Structural and semantic
validation remains in deterministic code, and malformed or incomplete results
never authorize a side effect.

## Calendar enrichment

Calendar processing is optional enrichment and is isolated from the mandatory
mail disposition path. Triage never creates, edits, or deletes Calendar events;
its Calendar access is limited to reading the configured calendar.

Event candidates are processed sequentially. Each candidate is compared only with
a fresh view of the configured Calendar before any preparation decision; candidates
from the same mail are never compared with each other, so similar candidates may
each produce an attachment. The model's `existing` and
`possible_duplicate` outcomes both become one boolean duplicate decision and one
`duplicate` pipeline result; duplicates are skipped without stopping the
remaining candidates.

Event processing produces one immutable result per candidate, in candidate order,
as soon as that candidate is decided: `prepared`, `duplicate`, or `skipped` with a
fixed reason. Only a `prepared` result carries an attachment. The mail pipeline
consumes results as they are produced and collects the attachments for delivery.

Only a complete, validated event may be prepared. Eligibility is determined from
the event end, not its start: timed events must end more than 72 elapsed hours
in the future, while all-day events must end beyond three local calendar days.
This policy is checked immediately before preparation. Each prepared event
becomes one standards-compliant iCalendar attachment whose UID is derived
deterministically from the candidate's occurrence position in the extraction and its
validated event data. Identical candidates in one extraction therefore receive distinct
UIDs. A retry retains a candidate's UID only when the extraction order and the completed
event data are unchanged; otherwise it produces a new UID.
Duplicate and skipped candidates produce no attachment.

Prepared attachments accompany the mail's notification. Calendar lookup,
validation, preparation, or attachment failures do not block the mail's
notification or disposition; a later candidate failure does not discard results,
or their attachments, already produced for the same mail.

## Notification and disposition

Every processed mail produces one immediate Discord notification, regardless
of whether its disposition is `archive` or `leave`. The complete notification
must be valid before optional side effects begin and must be delivered
successfully before the mailbox is changed. Prepared event attachments are
delivered with that same notification; they exist only transiently, in private
storage, for the duration of the delivery attempt.

- `leave` marks the message `Flagged` and keeps it in the Inbox.
- `archive` moves the message to Archive without changing its read state.

The pipeline is stateless and delivery is at least once. If delivery succeeds
but the process fails before disposition completes, a later retry may send the
notification again.

## eduVULCAN handoff

Mail from the approved eduVULCAN sender is handed to the external orchestrator
after notification validation. The handoff receives the original raw message
and owns all eduVULCAN-specific identification and repair behavior.

The handoff is best-effort. Its failure does not block Discord delivery or the
selected mail disposition, and triage does not supervise or retry the external
workflow.

## Scheduling

One periodic poller runs the complete per-mail flow.
