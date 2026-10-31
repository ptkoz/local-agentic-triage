# Safe debugging runbook

Use this runbook when investigating production failures. Investigation is read-only unless the operator explicitly authorizes a repair.

## Logs and identifiers

Launchd writes application output to:

- `~/Library/Logs/local-agentic-triage/poll.log`
- `~/Library/Logs/local-agentic-triage/report.log`

Follow both logs with:

```sh
tail -F ~/Library/Logs/local-agentic-triage/{poll,report}.log
```

A poll error such as:

```text
ERROR mail_item_failed item=23 uid=15318 uidvalidity=123 stage=classify error=model_content_json_invalid
```

contains only technical metadata:

- `item` is the transient position in that poll's candidate list. Do not use it as an identifier.
- `uid` is the IMAP UID in the configured Inbox. It is not global across mailboxes or a changed `UIDVALIDITY`.
- `uidvalidity` is the mailbox generation that makes the UID historically unambiguous. Current builds include it in per-message start and failure logs.
- `stage` is the last pipeline boundary entered. `classify` covers bounded mail-payload construction, the loopback DMR request, strict decoding of its response, and classification-schema validation.
- `error` is generated from a controlled exception type. Exception text, subprocess input/output, headers, sender, subject, body, summary, and model output must never be printed or copied into logs or reports.

Read the configured account and Inbox locally from the private `config.toml`; never copy their values into Git or a debugging report. `Mailbox.connect`, `require_uid`, and `source_key` in `mail/imap.py` show the mailbox selection, `UIDVALIDITY`, and UID-scoping rules.

## Read-only replay of one classification

For an explicitly authorized investigation, replay only the read and local-classification stages:

```sh
/absolute/path/to/python3.14 \
  ~/.local/libexec/local-agentic-triage/local-agentic-triage.pyz \
  mail diagnose --uid 15318 --uidvalidity 123
```

Put `--config /absolute/path/config.toml` before `mail` when using a non-default config.

If the installed zipapp predates this command, export a reviewed commit into a
new isolated directory. Do not run from a normal working tree: staged,
unstaged, untracked, or ignored importable files could change executed code.

```sh
mkdir -p "$HOME/.local/share/local-agentic-triage/diagnostics"
diagnostic_root=$(mktemp -d "$HOME/.local/share/local-agentic-triage/diagnostics/source.XXXXXX")
reviewed_commit='REPLACE_WITH_REVIEWED_FULL_COMMIT_SHA'
git -C /absolute/path/to/local-agentic-triage archive \
  --output="$diagnostic_root/source.tar" "$reviewed_commit"
tar -xf "$diagnostic_root/source.tar" -C "$diagnostic_root"
rm "$diagnostic_root/source.tar"
cd "$diagnostic_root"
PYTHONPATH=src /absolute/path/to/python3.14 -m local_agentic_triage \
  --config "$HOME/Library/Application Support/local-agentic-triage/config.toml" \
  mail diagnose --uid 15318 --uidvalidity 123
```

Record the reviewed full commit SHA in the technical report and remove the
isolated directory afterward. Running from this exported source does not update
the installed application or restart its LaunchAgents.

`mail diagnose`:

- selects only the configured Inbox and validates its current `UIDVALIDITY`;
- verifies that the UID still exists, reads that message without setting flags, bounds the model payload, and calls only the fixed loopback classifier;
- does not open SQLite, acquire production locks, run Calendar/Hermes, set `\Flagged`/`Seen`, move mail, or send a report;
- logs only UID, UIDVALIDITY, stage, truncation field names, and a controlled error code;
- returns 0 if classification currently succeeds and 1 if the replay fails.

Do not use `mail poll` or `report send --deliver` for diagnosis. They have production side effects. Do not add temporary logging of private input or model output.

A missing UID, a different mailbox, or `--uidvalidity` mismatch means the
historical message cannot be replayed by that UID. The command rejects a
mismatched expected generation before reading the message. Do not search other
mailboxes automatically or guess a replacement identifier.

Legacy failure lines without `uidvalidity` cannot prove historical identity
after a mailbox-generation change. A UID-only replay may describe the message
currently using that UID, but must not be reported as confirmed evidence about
the historical failure. State this limitation explicitly.

## Classification JSON errors

The classifier performs two strict JSON decodes. Current builds distinguish them:

- `model_envelope_json_invalid`: the HTTP body returned by the loopback Docker Model Runner endpoint was not strict JSON. This is a transport/runtime response failure before reading a model message.
- `model_content_json_invalid`: `choices[0].message.content` was not strict JSON. The HTTP envelope was valid, but the model returned malformed classification JSON.

Both decoders also reject duplicate keys, non-finite constants, invalid encoding, and wrong input types. Schema errors after successful decoding have their own exception-derived code and are not JSON decode failures.
The request uses a strict JSON Schema for destination, summary, and complete
event objects; the same deterministic validator still enforces leave/archive
conditions, date syntax, range compatibility, and temporal ordering afterward.

Hermes event decisions use `--format stream-json`. The parser permits only a
small bounded prefix of `Warning: ` lines, requires one system/init and one
final result with matching session identifiers and zero integer exit codes,
rejects tools or trailing records, and parses only `result.text` as the existing
strict decision document. Framing failures expose only fixed technical errors;
captured output must not be added to logs while debugging.

Older builds logged both decoder failures as `json_decode_error`. That legacy code alone is ambiguous: it proves strict JSON decoding failed but not which decoder failed. Use the read-only diagnostic command on the current build. If replay succeeds, report that the historical decoder location cannot be proven from legacy logs; do not infer it or claim a root cause from private output.

Before trusting a result, compare the installed zipapp with the reviewed repository version or confirm the deployed commit. The implementation in `classifier/core.py`, `mail/pipeline.py`, and `mail/imap.py` is normative documentation for the exact boundaries above.

## Reporting an investigation

Report only:

1. whether the UID was still valid in the configured Inbox;
2. the observed technical stage and controlled error code;
3. the corresponding code boundary;
4. whether the result is confirmed by current replay or limited by legacy evidence.

Do not repair, restart jobs, mutate mail, inspect Calendar, modify SQLite, or deliver reports unless the operator separately authorizes those actions.
