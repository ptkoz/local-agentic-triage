# Safe debugging runbook

Use this runbook when investigating production failures. Investigation is read-only unless the operator explicitly authorizes a repair.

## Logs and identifiers

Launchd writes application output to:

- `~/Library/Logs/local-agentic-triage/poll.log`

Follow the log with:

```sh
tail -F ~/Library/Logs/local-agentic-triage/poll.log
```

A poll error such as:

```text
ERROR mail_item_failed item=23 uid=15318 uidvalidity=123 stage=summarize error=model_content_json_invalid
```

contains only technical metadata:

- `item` is the transient position in that poll's candidate list. Do not use it as an identifier.
- `uid` is the IMAP UID in the configured Inbox. It is not global across mailboxes or a changed `UIDVALIDITY`.
- `uidvalidity` is the mailbox generation that scopes the UID.
- `stage` is the last pipeline boundary entered. `summarize` covers the summary request and summary validation. `classify` covers the later destination request and disposition validation; it receives the already generated summary and returns only `archive` or `leave`.
- `error` is generated from a controlled exception type. Exception text, subprocess input/output, headers, sender, subject, body, summary, and model output must never be printed or copied into logs or reports.

Read the configured account and Inbox locally from the private `config.toml`; never copy their values into Git or a debugging report. `Mailbox.connect` and `require_uid` in `mail/imap.py` show the mailbox selection, `UIDVALIDITY`, and UID-scoping rules.

## Read-only replay of summary and classification

For an explicitly authorized investigation, replay only the read, summary, and local-classification stages:

```sh
/absolute/path/to/python3.14 \
  ~/.local/libexec/local-agentic-triage/local-agentic-triage.pyz \
  mail diagnose --uid 15318 --uidvalidity 123
```

Put `--config /absolute/path/config.toml` before `mail` when using a non-default config.

`mail diagnose`:

- selects only the configured Inbox and validates its current `UIDVALIDITY`;
- verifies that the UID still exists, reads that message without setting flags, bounds the model payload, and calls only the fixed loopback summary and destination passes;
- does not acquire the production poll lock, run Calendar/event decisions, set `\Flagged`/`Seen`, move mail, or send a notification;
- logs only UID, UIDVALIDITY, stage, and a controlled error code;
- returns 0 if both model stages currently succeed and 1 if either replay stage fails.

Do not use `mail poll` for diagnosis. It has production side effects. Do not add temporary logging of private input or model output.

A missing UID, a different mailbox, or `--uidvalidity` mismatch stops the replay.
The command rejects a mismatched expected generation before reading the message.
Do not search other mailboxes automatically or guess a replacement identifier.

## Classification JSON errors

The shared local-model client performs and distinguishes two strict JSON decodes:

- `model_envelope_json_invalid`: the HTTP body returned by the loopback Docker Model Runner endpoint was not strict JSON. This is a transport/runtime response failure before reading a model message.
- `model_content_json_invalid`: `choices[0].message.content` was not strict JSON. The HTTP envelope was valid, but the model returned malformed model JSON.

Both decoders also reject duplicate keys, non-finite constants, invalid encoding, and wrong input types. Schema errors after successful decoding have their own exception-derived code and are not JSON decode failures.
Five distinct prompts and closed strict JSON Schemas use the same fixed loopback
endpoint and model. Summary generation returns one summary. Destination classification
then receives separate summary and bounded-mail fields and returns only the disposition.
Sparse extraction independently receives bounded mail JSON and returns complete
title/start/end/date_note/location candidates. After start validation and nearby
lookup, duplicate checking sees each extracted event unchanged plus minimal nearby
title/start/end/all_day/location rows. The frozen model schema still returns
existing, possible_duplicate, or create; `classifier/events.py` maps the first
two to boolean true and create to false. A false candidate enters completion
with all extracted fields unchanged.
Completion returns one full event and is not allowed to alter directly supplied
values or invent a location. Python validates each completed event's structure,
authorization, interval, and temporal eligibility immediately before its individual
write. Event payloads are rejected before the model when their conservative context
budgets would be exceeded; nearby rows are never truncated. See architecture.md for
the five contracts.

An event exception or invalid canonical event result produces one
`mail_event_failed uid=... error=...` line, optionally with a bounded numeric
`returncode`. This is optional-enrichment telemetry: it does not produce a
second `mail_item_failed` line, make the poll fail, or prevent leave/archive.
Summary, destination, read, notification rendering/delivery, and disposition
errors remain fatal and retryable.

Before trusting a result, compare the installed zipapp with the reviewed repository version or confirm the deployed commit. The implementation in `classifier/resources.py`, `classifier/disposition.py`, `classifier/events.py`, `classifier/transport.py`, `classifier/input.py`, `mail/event_pipeline.py`, `events/calendar.py`, `mail/pipeline.py`, and `mail/imap.py` is normative documentation for the exact boundaries above. `classifier/resources.py` is the single owner and loader of all five function-named prompt and output-schema resources; `classifier/transport.py` contains transport and limits only.

## Reporting an investigation

Report only:

1. whether the UID was still valid in the configured Inbox;
2. the observed technical stage and controlled error code;
3. the corresponding code boundary;
4. whether the result is confirmed by current replay or remains unproven.

Do not repair, restart jobs, mutate mail, inspect Calendar, or deliver notifications unless the operator separately authorizes those actions.
