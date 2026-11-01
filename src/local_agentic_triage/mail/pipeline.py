import logging
import json
from dataclasses import replace
from local_agentic_triage.mail.mime import parse
from local_agentic_triage.mail.imap import uid_value
from local_agentic_triage.classifier.core import classify, validate_disposition, bound_mail
from local_agentic_triage.events.core import process, parse_results, extract
from local_agentic_triage.common.diagnostics import error_code, error_suffix
from local_agentic_triage.reporting.report import record_message

log=logging.getLogger('local_agentic_triage')

def diagnose(mailbox,config,uid,classifier=classify,expected_validity=None):
    """Replay reading and local classification for one UID without mutations."""
    uid=uid_value(uid)
    validity=uid_value(mailbox.validity)
    if expected_validity is not None and uid_value(expected_validity)!=validity:
        log.error('mail_diagnosis_identity_failed uid=%s error=uidvalidity_mismatch',uid)
        return 1
    stage='read'
    try:
        mail=parse(mailbox.raw(uid),config.max_mail_bytes)
        if not mail.sent_date:
            mail=replace(mail,sent_date=mailbox.internal_date(uid))
        stage='classify'
        mail,truncated=bound_mail(mail)
        if truncated:
            log.info('mail_content_truncated uid=%s fields=%s',uid,','.join(truncated))
        validate_disposition(classifier(mail))
        log.info('mail_diagnosis_completed uid=%s uidvalidity=%s stage=classify',uid,validity)
        return 0
    except Exception as exc:
        log.error('mail_diagnosis_failed uid=%s uidvalidity=%s stage=%s error=%s%s',uid,validity,stage,
                  error_code(exc),error_suffix(exc))
        return 1

def poll(mailbox,store,config,classifier=classify,event_processor=process, *, extractor=None):
    extractor=extract if extractor is None else extractor
    failures=0; completed=0
    log.info('mail_poll_started')
    try: candidates=mailbox.candidates()
    except Exception as exc:
        log.error('mail_candidates_failed error=%s%s',error_code(exc),error_suffix(exc)); return 1
    log.info('mail_candidates_discovered count=%d',len(candidates))
    def reconcile():
        nonlocal failures
        log.info('mail_reconciliation_started')
        recovered=0
        try:
            present=mailbox.all_uids()
        except Exception as exc:
            log.error('mail_reconciliation_failed stage=list error=%s%s',error_code(exc),error_suffix(exc))
            failures+=1
            return
        for row_index,row in enumerate(store.stranded(),1):
            try:
                key=row['source_key']
                uid=uid_value(key.rsplit(':',1)[-1])
            except Exception:
                log.info('mail_reconciliation_state_skipped row=%d',row_index)
                continue
            try:
                if key==mailbox.source_key(uid) and uid not in present:
                    store.ready(key); recovered+=1
            except Exception as exc:
                log.error('mail_reconciliation_failed uid=%s stage=row error=%s%s',uid,error_code(exc),error_suffix(exc))
                failures+=1
        log.info('mail_reconciliation_completed recovered=%d',recovered)
    reconcile()
    for index,uid in enumerate(candidates,1):
        log.info('mail_item_started item=%d total=%d uid=%s uidvalidity=%s',index,len(candidates),uid,mailbox.validity)
        stage='identity'
        try:
            key=mailbox.source_key(uid)
            prepared=store.prepared(key)
            if prepared is not None:
                log.info('mail_item_action item=%d uid=%s stage=archive_retry',index,uid)
                # A ready row already completed its IMAP disposition.
                if prepared['ready_at'] is None:
                    stage='report_row_retry'
                    record_message(prepared,config.discord_limit)
                    stage='archive_retry'; mailbox.archive(uid); store.ready(key)
                completed+=1
                continue
            stage='read'
            mail=parse(mailbox.raw(uid),config.max_mail_bytes)
            if not mail.sent_date:
                mail=replace(mail,sent_date=mailbox.internal_date(uid))
            stage='classify'
            mail,truncated=bound_mail(mail)
            if truncated:
                log.info('mail_content_truncated uid=%s fields=%s',uid,','.join(truncated))
            log.info('mail_item_action item=%d uid=%s stage=classify',index,uid)
            result=validate_disposition(classifier(mail))

            if result['destination']=='archive':
                stage='report_row'
                record_message({'sender':mail.sender,'summary':result['summary'],
                                'sent_at':mail.sent_date},config.discord_limit)

            log.info('mail_item_action item=%d uid=%s stage=events',index,uid)
            try:
                extracted=extractor(mail)
                event_result=event_processor(extracted,config)
                event_result=parse_results(json.dumps(event_result),extracted,processed=True)
                for entry in event_result['results']:
                    log.info('mail_event_result uid=%s index=%d outcome=%s reason=%s',uid,entry['index'],entry['outcome'],entry['reason'])
            except Exception as exc:
                log.error('mail_event_failed uid=%s error=%s%s',uid,error_code(exc),error_suffix(exc))
            stage='disposition'
            if result['destination']=='leave':
                mailbox.leave(uid)
                log.info('mail_item_action item=%d uid=%s stage=leave',index,uid)
            else:
                log.info('mail_item_action item=%d uid=%s stage=archive',index,uid)
                store.prepare(key,mail.sender,result['summary'],sent_at=mail.sent_date)
                mailbox.archive(uid)
                store.ready(key)
            completed+=1
        except Exception as exc:
            log.error('mail_item_failed item=%d uid=%s uidvalidity=%s stage=%s error=%s%s',index,uid,mailbox.validity,stage,error_code(exc),error_suffix(exc))
            failures+=1
    reconcile()
    log.info('mail_poll_completed candidates=%d completed=%d failures=%d',len(candidates),completed,failures)
    return int(bool(failures))
