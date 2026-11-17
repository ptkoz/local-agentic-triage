import logging
from dataclasses import replace
from local_agentic_triage.mail.mime import parse
from local_agentic_triage.classifier.input import MAIL_PAYLOAD_BYTES, fit_to_context
from local_agentic_triage.mail.vulcan import VULCAN_SENDER, delegate_to_vulcan_handler
from local_agentic_triage.mail.imap import uid_value
from local_agentic_triage.classifier.disposition import summarize, classify
from local_agentic_triage.classifier.events import extract_events
from local_agentic_triage.mail.event_pipeline import process_events
from local_agentic_triage.common.diagnostics import error_code, error_suffix
from local_agentic_triage.reporting.report import render_notification, deliver

log = logging.getLogger('local_agentic_triage')


def diagnose(mailbox, config, uid, classifier=classify, expected_validity=None, *, summarizer=summarize):
    """Replay reading and local classification for one UID without mutations."""
    uid = uid_value(uid)
    validity = uid_value(mailbox.validity)
    if expected_validity is not None and uid_value(expected_validity) != validity:
        log.error('mail_diagnosis_identity_failed uid=%s error=uidvalidity_mismatch', uid)
        return 1
    stage = 'read'
    try:
        mail = parse(mailbox.raw(uid), config.max_mail_bytes)
        if not mail.sent_date:
            mail = replace(mail, sent_date=mailbox.internal_date(uid))
        context_bound_mail = fit_to_context(mail, MAIL_PAYLOAD_BYTES)
        stage = 'summarize'
        summary = summarizer(context_bound_mail)
        stage = 'classify'
        classifier(context_bound_mail, summary)
        log.info('mail_diagnosis_completed uid=%s uidvalidity=%s stage=classify', uid, validity)
        return 0
    except Exception as exc:
        log.error('mail_diagnosis_failed uid=%s uidvalidity=%s stage=%s error=%s%s',
                  uid, validity, stage, error_code(exc), error_suffix(exc))
        return 1


def poll(mailbox, config, classifier=classify, event_processor=process_events, *, summarizer=summarize,
         extractor=None, runner=None):
    extractor = extract_events if extractor is None else extractor
    failures = 0; completed = 0
    log.info('mail_poll_started')
    try:
        candidates = mailbox.candidates()
    except Exception as exc:
        log.error('mail_candidates_failed error=%s%s', error_code(exc), error_suffix(exc))
        return 1
    log.info('mail_candidates_discovered count=%d', len(candidates))

    for index, uid in enumerate(candidates, 1):
        log.info('mail_item_started item=%d total=%d uid=%s uidvalidity=%s',
                 index, len(candidates), uid, mailbox.validity)
        stage = 'read'
        try:
            raw = mailbox.raw(uid)
            mail = parse(raw, config.max_mail_bytes)
            if not mail.sent_date:
                mail = replace(mail, sent_date=mailbox.internal_date(uid))
            context_bound_mail = fit_to_context(mail, MAIL_PAYLOAD_BYTES)
            stage = 'summarize'
            log.info('mail_item_action item=%d uid=%s stage=summarize', index, uid)
            summary = summarizer(context_bound_mail)
            stage = 'classify'
            log.info('mail_item_action item=%d uid=%s stage=classify', index, uid)
            destination = classifier(context_bound_mail, summary)

            # Validate the mandatory notification before optional side effects.
            stage = 'notify'
            notification = render_notification(
                {'sender': mail.sender, 'summary': summary, 'sent_at': mail.sent_date},
                destination,
                config.discord_limit,
            )

            # Optional: eduVULCAN handoff (best-effort)
            if mail.sender_address.lower() == VULCAN_SENDER.lower():
                delegate_to_vulcan_handler(uid, raw, config)

            # Optional: Calendar enrichment (best-effort)
            log.info('mail_item_action item=%d uid=%s stage=events', index, uid)
            try:
                extracted = extractor(context_bound_mail)
                event_result = event_processor(extracted, config)
                for entry in event_result['results']:
                    log.info('mail_event_result uid=%s index=%d outcome=%s reason=%s',
                             uid, entry['index'], entry['outcome'], entry['reason'])
            except Exception as exc:
                log.error('mail_event_failed uid=%s error=%s%s', uid, error_code(exc), error_suffix(exc))

            # Send to Discord; failure blocks disposition
            stage = 'send'
            log.info('mail_item_action item=%d uid=%s stage=send', index, uid)
            deliver(runner, config, notification)

            # Apply disposition only after successful send
            stage = 'disposition'
            if destination == 'leave':
                mailbox.leave(uid)
                log.info('mail_item_action item=%d uid=%s stage=leave', index, uid)
            else:
                mailbox.archive(uid)
                log.info('mail_item_action item=%d uid=%s stage=archive', index, uid)
            completed += 1
        except Exception as exc:
            log.error('mail_item_failed item=%d uid=%s uidvalidity=%s stage=%s error=%s%s',
                      index, uid, mailbox.validity, stage, error_code(exc), error_suffix(exc))
            failures += 1

    log.info('mail_poll_completed candidates=%d completed=%d failures=%d',
             len(candidates), completed, failures)
    return int(bool(failures))
