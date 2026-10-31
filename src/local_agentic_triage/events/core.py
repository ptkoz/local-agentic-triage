from importlib.resources import files
from datetime import datetime, timezone
import json
import subprocess
from typing import Never
from local_agentic_triage.classifier.core import validate, timestamp
from local_agentic_triage.classifier.core import strict_json
from local_agentic_triage.common.diagnostics import CommandError, error_code

def prompt(): return files(__package__).joinpath('prompt.md').read_text(encoding='utf-8')

SKIP_REASONS={'invalid_title','invalid_date','missing_date','uncertain_date','missing_end','past_event','cancelled_event','possible_duplicate','too_soon'}
DECISION_SKIP_REASONS=SKIP_REASONS-{'too_soon'}
ERROR_REASONS={'search_failed','create_failed','verification_failed','invalid_input'}
DELIMITER='\n--- BEGIN UNTRUSTED EVENTS JSON ---\n'
MAX_STREAM_WARNINGS=4
MAX_STREAM_WARNING_BYTES=4096
MAX_STREAM_LINE_BYTES=65536
MAX_STREAM_RECORDS=256

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
    for expected_index,entry in enumerate(result['results']):
        keys={'index','outcome','reason','estimated_end'} if decisions else {'index','outcome','reason'}
        if not isinstance(entry,dict) or set(entry)!=keys: raise ValueError('event_outcome_keys')
        index=entry['index']; outcome=entry['outcome']; reason=entry['reason']
        if type(index) is not int or index!=expected_index or index in seen: raise ValueError('event_outcome_index')
        seen.add(index)
        event=events[index]
        if decisions:
            estimate=entry['estimated_end']
            authorized=(outcome=='create' and isinstance(event['start'],str) and len(event['start'])>10 and event['end'] is None)
            if authorized:
                try:
                    timestamp(estimate)
                    start=datetime.fromisoformat(event['start'])
                    end=datetime.fromisoformat(estimate)
                    if (start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or
                            end.utcoffset() is None or end<=start):
                        raise ValueError
                except (TypeError,ValueError,OverflowError):
                    raise ValueError('event_estimated_end') from None
            elif estimate is not None:
                raise ValueError('event_estimated_end')
        if outcome in (('create','existing') if decisions else ('created','existing')):
            if reason is not None: raise ValueError('event_outcome_reason')
            missing_timed_end=isinstance(event['start'],str) and len(event['start'])>10 and event['end'] is None
            if (event['start'] is None or event['date_note'] is not None or
                    (missing_timed_end and outcome in ('create','created') and
                     not (decisions and outcome=='create' and estimate is not None))):
                raise ValueError('unsafe_event_outcome')
        elif outcome=='skipped':
            allowed=DECISION_SKIP_REASONS if decisions else SKIP_REASONS
            if not isinstance(reason,str) or reason not in allowed: raise ValueError('event_outcome_reason')
        elif outcome=='error':
            if not isinstance(reason,str) or reason not in ERROR_REASONS: raise ValueError('event_outcome_reason')
            failed=True
        else: raise ValueError('event_outcome_invalid')
    if (result['status']=='error')!=failed: raise ValueError('event_status_inconsistent')
    if failed: raise ValueError('event_processing_failed')
    return result

def parse_stream_envelope(raw,returncode):
    def invalid()->Never: raise ValueError('event_stream_invalid')
    if type(returncode) is not int or returncode!=0 or not isinstance(raw,str): invalid()
    warnings=warning_bytes=records=0
    session_id=result_text=None
    saw_record=saw_result=False
    for line in raw.splitlines():
        try:
            size=len(line.encode('utf-8'))
        except UnicodeError:
            invalid()
        if size>MAX_STREAM_LINE_BYTES: invalid()
        if not line: invalid()
        if line.startswith('Warning: '):
            if saw_record: invalid()
            warnings+=1; warning_bytes+=size
            if warnings>MAX_STREAM_WARNINGS or warning_bytes>MAX_STREAM_WARNING_BYTES: invalid()
            continue
        saw_record=True; records+=1
        if records>MAX_STREAM_RECORDS or saw_result: invalid()
        try:
            record=strict_json(line)
        except (TypeError,ValueError,UnicodeError):
            invalid()
        if not isinstance(record,dict): invalid()
        kind=record.get('type')
        if records==1:
            if kind!='system' or record.get('subtype')!='init': invalid()
            session_id=record.get('session_id')
            if not isinstance(session_id,str) or not session_id: invalid()
        elif kind=='system':
            invalid()
        elif kind in ('tool_use','tool_result'):
            invalid()
        elif kind=='text':
            if not isinstance(record.get('text'),str): invalid()
        elif kind=='result':
            result_session=record.get('session_id')
            exit_code=record.get('exit_code')
            text=record.get('text')
            if (result_session!=session_id or not isinstance(result_session,str) or
                    type(exit_code) is not int or exit_code!=0 or 'error' in record or
                    not isinstance(text,str)):
                invalid()
            result_text=text; saw_result=True
        else:
            invalid()
    if records<2 or not saw_result: invalid()
    return result_text

def process(events,config, *, clock=None):
    from local_agentic_triage.events.calendar import Calendar
    validate_events(events)
    if not events: return {'status':'ok','results':[]}
    calendar=Calendar(config,clock=clock)
    gate_now=calendar.clock(timezone.utc)
    eligible=[]; results=[]
    for index,event in enumerate(events):
        if event['start'] is None:
            results.append({'index':index,'outcome':'skipped','reason':'missing_date'})
        elif calendar.too_soon(event,now=gate_now):
            results.append({'index':index,'outcome':'skipped','reason':'too_soon'})
        else:
            eligible.append((index,event))
    if not eligible: return {'status':'ok','results':results}
    selected_events=[event for _,event in eligible]
    nearby=[calendar.nearby(event) for event in selected_events]
    args=[config.hermes,'chat','--query-file','-','--oneshot','--quiet','--format','stream-json','--source','tool','--toolsets','none','--ignore-rules','--max-turns','20','--run-budget','120']
    try:
        result=subprocess.run(args,input=prompt()+DELIMITER+json.dumps({'calendar':config.calendar,'events':selected_events,'nearby':nearby},ensure_ascii=False),text=True,capture_output=True,timeout=config.timeout,shell=False)
    except (OSError,subprocess.SubprocessError) as exc:
        raise CommandError(error_code(exc)) from None
    if type(result.returncode) is int and result.returncode: raise CommandError('event_command_failed',result.returncode)
    try:
        output_size=len(result.stdout.encode('utf-8'))
    except (AttributeError,UnicodeError):
        raise ValueError('event_stream_invalid') from None
    if output_size>1000000: raise ValueError('event_result_size')
    text=parse_stream_envelope(result.stdout,result.returncode)
    final=parse_results(text,selected_events,decisions=True)
    validated_events=list(events)
    for entry in final['results']:
        selected_index=entry['index']
        original_index,event=eligible[selected_index]
        if entry['outcome']=='create':
            if entry['estimated_end'] is not None: event=event|{'end':entry['estimated_end']}
            calendar.create(event)
            validated_events[original_index]=event
            entry['outcome']='created'
        del entry['estimated_end']
        entry['index']=original_index
        results.append(entry)
    combined={'status':'ok','results':sorted(results,key=lambda entry:entry['index'])}
    return parse_results(json.dumps(combined),validated_events)
