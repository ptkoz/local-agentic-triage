from importlib.resources import files
from datetime import datetime, timezone
import json
from local_agentic_triage.classifier.core import CONTEXT_TOKENS, MAX_TOKENS, chat_completion, strict_json, timestamp, validate

def prompt(): return files(__package__).joinpath('prompt.md').read_text(encoding='utf-8')

SKIP_REASONS={'invalid_title','invalid_date','missing_date','uncertain_date','missing_end','past_event','cancelled_event','possible_duplicate','too_soon'}
DECISION_SKIP_REASONS=SKIP_REASONS-{'too_soon'}
ERROR_REASONS={'search_failed','create_failed','verification_failed','invalid_input'}
# Conservatively budget at most one model token per UTF-8 byte within the 16k
# context. Reserve the prompt, schema, framing, and maximum output. Reject the
# whole user payload rather than truncate nearby rows and hide a duplicate.
EVENT_TEMPLATE_OVERHEAD_BYTES=1024
EVENT_DECISION_SCHEMA={
    'type':'json_schema',
    'json_schema':{
        'name':'event_decision','strict':True,
        'schema':{
            'type':'object','additionalProperties':False,
            'required':['status','results'],
            'properties':{
                'status':{'type':'string','enum':['ok','error']},
                'results':{'type':'array','items':{
                    'type':'object','additionalProperties':False,
                    'required':['index','outcome','reason','estimated_end'],
                    'properties':{
                        'index':{'type':'integer'},
                        'outcome':{'type':'string','enum':['create','existing','skipped','error']},
                        'reason':{'type':['string','null']},
                        'estimated_end':{'type':['string','null']},
                    },
                }},
            },
        },
    },
}

class ModelRequestFailed(RuntimeError):
    pass

def event_user_budget():
    reserved=len(prompt().encode('utf-8'))+len(json.dumps(EVENT_DECISION_SCHEMA).encode('utf-8'))+EVENT_TEMPLATE_OVERHEAD_BYTES+MAX_TOKENS
    budget=CONTEXT_TOKENS-reserved
    if budget<1: raise RuntimeError('event_payload_budget')
    return budget

def event_user_content(calendar,events,nearby):
    content=json.dumps({'calendar':calendar,'events':events,'nearby':nearby},ensure_ascii=False,separators=(',',':'))
    if len(content.encode('utf-8'))>event_user_budget(): raise ValueError('event_payload_size')
    return content

def validate_events(events):
    validate({'destination':'leave','summary':None,'events':events})
    if any(len(event['title'])>200 for event in events): raise ValueError('event_title_limit')

def parse_results(raw,events, *, decisions=False, processed=False):
    if decisions and processed: raise ValueError('event_result_mode')
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
        estimate=None
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
                     not ((decisions and outcome=='create' and estimate is not None) or
                          (processed and outcome=='created')))):
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
    user=event_user_content(config.calendar,selected_events,nearby)
    messages=[{'role':'system','content':prompt()},{'role':'user','content':user}]
    try:
        content=chat_completion(messages,EVENT_DECISION_SCHEMA)
    except OSError:
        raise ModelRequestFailed() from None
    final=parse_results(json.dumps(content),selected_events,decisions=True)
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
