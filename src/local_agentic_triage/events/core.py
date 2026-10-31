from importlib.resources import files
import json
import subprocess
from local_agentic_triage.classifier.core import validate
from local_agentic_triage.classifier.core import strict_json
from local_agentic_triage.common.diagnostics import CommandError, error_code

def prompt(): return files(__package__).joinpath('prompt.md').read_text(encoding='utf-8')

SKIP_REASONS={'invalid_title','invalid_date','missing_date','uncertain_date','missing_end','past_event','cancelled_event','possible_duplicate'}
ERROR_REASONS={'search_failed','create_failed','verification_failed','invalid_input'}
DELIMITER='\n--- BEGIN UNTRUSTED EVENTS JSON ---\n'

def validate_events(events):
    validate({'destination':'leave','summary':None,'events':events})
    if any(len(event['title'])>200 for event in events): raise ValueError('event_title_limit')

def parse_results(raw,events, *, decisions=False):
    validate_events(events)
    result=strict_json(raw)
    if not isinstance(result,dict) or set(result)!={'status','results'} or not isinstance(result['results'],list): raise ValueError('event_result_keys')
    if result['status'] not in ('ok','error'): raise ValueError('event_result_status')
    if len(result['results'])!=len(events): raise ValueError('event_result_coverage')
    seen=set(); failed=False
    for entry in result['results']:
        if not isinstance(entry,dict) or set(entry)!={'index','outcome','reason'}: raise ValueError('event_outcome_keys')
        index=entry['index']; outcome=entry['outcome']; reason=entry['reason']
        if type(index) is not int or index<0 or index>=len(events) or index in seen: raise ValueError('event_outcome_index')
        seen.add(index)
        if outcome in (('create','existing') if decisions else ('created','existing')):
            if reason is not None: raise ValueError('event_outcome_reason')
            event=events[index]
            if event['start'] is None or event['date_note'] is not None or (len(event['start'])>10 and event['end'] is None): raise ValueError('unsafe_event_outcome')
        elif outcome=='skipped':
            if not isinstance(reason,str) or reason not in SKIP_REASONS: raise ValueError('event_outcome_reason')
        elif outcome=='error':
            if not isinstance(reason,str) or reason not in ERROR_REASONS: raise ValueError('event_outcome_reason')
            failed=True
        else: raise ValueError('event_outcome_invalid')
    if (result['status']=='error')!=failed: raise ValueError('event_status_inconsistent')
    if failed: raise ValueError('event_processing_failed')
    return result

def process(events,config):
    from local_agentic_triage.events.calendar import Calendar
    validate_events(events)
    if not events: return {'status':'ok','results':[]}
    calendar=Calendar(config)
    nearby=[calendar.nearby(event) for event in events]
    args=[config.hermes,'chat','--query-file','-','--oneshot','--quiet','--source','tool','--toolsets','none','--ignore-rules','--max-turns','20','--run-budget','120']
    try:
        result=subprocess.run(args,input=prompt()+DELIMITER+json.dumps({'calendar':config.calendar,'events':events,'nearby':nearby},ensure_ascii=False),text=True,capture_output=True,timeout=config.timeout,shell=False)
    except (OSError,subprocess.SubprocessError) as exc:
        raise CommandError(error_code(exc)) from None
    if result.returncode: raise CommandError('event_command_failed',result.returncode)
    if len(result.stdout)>1000000: raise ValueError('event_result_size')
    final=parse_results(result.stdout,events,decisions=True)
    for entry in final['results']:
        if entry['outcome']=='create':
            calendar.create(events[entry['index']])
            entry['outcome']='created'
    return parse_results(json.dumps(final),events)
