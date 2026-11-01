from importlib.resources import files
from datetime import datetime
import json
from local_agentic_triage.classifier.core import CONTEXT_TOKENS, MAX_TOKENS, chat_completion, strict_json, timestamp, mail_payload, response_schema

def prompt(): return files(__package__).joinpath('duplicate.md').read_text(encoding='utf-8')

def completion_prompt(): return files(__package__).joinpath('completion.md').read_text(encoding='utf-8')

SKIP_REASONS={'invalid_location','invalid_title','invalid_date','missing_date','uncertain_date','missing_end','past_event','cancelled_event','possible_duplicate','too_soon'}
ERROR_REASONS={'search_failed','create_failed','verification_failed','invalid_input'}
# Conservatively budget at most one model token per UTF-8 byte within the 16k
# context. Reserve the prompt, schema, framing, and maximum output. Reject the
# whole user payload rather than truncate nearby rows and hide a duplicate.
EVENT_TEMPLATE_OVERHEAD_BYTES=1024
DUPLICATE_SCHEMA=response_schema(__package__,'duplicate')
COMPLETION_SCHEMA=response_schema(__package__,'completion')

class ModelRequestFailed(RuntimeError):
    pass

def event_user_budget(system=None,schema=None):
    reserved=len((system or prompt()).encode('utf-8'))+len(json.dumps(schema or DUPLICATE_SCHEMA).encode('utf-8'))+EVENT_TEMPLATE_OVERHEAD_BYTES+MAX_TOKENS
    budget=CONTEXT_TOKENS-reserved
    if budget<1: raise RuntimeError('event_payload_budget')
    return budget

EVENT_FIELDS={'title','start','end','date_note','location'}

def validate_events(events):
    """Extraction validation is structural only; semantic checks follow completion."""
    if not isinstance(events,list): raise ValueError('event_structure')
    for event in events:
        if not isinstance(event,dict) or set(event)!=EVENT_FIELDS:
            raise ValueError('event_structure')
        if any(value is not None and not isinstance(value,str) for value in event.values()):
            raise ValueError('event_structure')

def start_reason(event):
    value=event.get('start')
    if value is None or value=='': return 'missing_date'
    try: timestamp(value)
    except (ValueError,TypeError): return 'invalid_date'
    return None

class FinalSemanticError(ValueError):
    def __init__(self,code,reason):
        super().__init__(code)
        self.reason=reason

def validate_final(event):
    validate_events([event])
    if not event['title'] or not event['title'].strip() or len(event['title'])>200:
        raise FinalSemanticError('event_invalid_title','invalid_title')
    if event['date_note'] is not None: raise FinalSemanticError('event_unresolved_date','uncertain_date')
    if start_reason(event): raise FinalSemanticError('event_invalid_date','invalid_date')
    if not event['end']: raise FinalSemanticError('event_invalid_date','missing_end')
    try: timestamp(event['end'])
    except ValueError:
        raise FinalSemanticError('event_invalid_date','invalid_date') from None
    if (len(event['start'])==10)!=(len(event['end'])==10): raise FinalSemanticError('event_range','invalid_date')
    if len(event['start'])==10:
        if event['end']<=event['start']: raise FinalSemanticError('event_order','invalid_date')
    elif datetime.fromisoformat(event['end'])<=datetime.fromisoformat(event['start']):
        raise FinalSemanticError('event_order','invalid_date')
    location=event['location']
    if location is not None and (not location.strip() or len(location)>1000):
        raise FinalSemanticError('event_invalid_location','invalid_location')

def parse_results(raw,events, *, processed=False):
    validate_events(events)
    result=strict_json(raw)
    if not isinstance(result,dict) or set(result)!={'status','results'} or not isinstance(result['results'],list): raise ValueError('event_result_keys')
    if result['status'] not in ('ok','error'): raise ValueError('event_result_status')
    if len(result['results'])!=len(events): raise ValueError('event_result_coverage')
    seen=set(); failed=False
    for expected_index,entry in enumerate(result['results']):
        keys={'index','outcome','reason'}
        if not isinstance(entry,dict) or set(entry)!=keys: raise ValueError('event_outcome_keys')
        index=entry['index']; outcome=entry['outcome']; reason=entry['reason']
        if type(index) is not int or index!=expected_index or index in seen: raise ValueError('event_outcome_index')
        seen.add(index)
        event=events[index]
        if outcome in ('created','existing'):
            if reason is not None: raise ValueError('event_outcome_reason')
            if start_reason(event): raise ValueError('unsafe_event_outcome')
            if outcome=='created' and not processed: validate_final(event)
        elif outcome=='skipped':
            if not isinstance(reason,str) or reason not in SKIP_REASONS: raise ValueError('event_outcome_reason')
        elif outcome=='error':
            if not isinstance(reason,str) or reason not in ERROR_REASONS: raise ValueError('event_outcome_reason')
            failed=True
        else: raise ValueError('event_outcome_invalid')
    if (result['status']=='error')!=failed: raise ValueError('event_status_inconsistent')
    if failed: raise ValueError('event_processing_failed')
    return result

def model_batch(system,schema,payload):
    user=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
    if len(user.encode('utf-8'))>event_user_budget(system,schema): raise ValueError('event_payload_size')
    try:
        return chat_completion([{'role':'system','content':system},{'role':'user','content':user}],schema)
    except OSError:
        raise ModelRequestFailed() from None

def duplicate_results(result):
    if (not isinstance(result,dict) or set(result)!={'outcome'} or
            result['outcome'] not in ('existing','possible_duplicate','create')):
        raise ValueError('event_outcome_invalid')
    return result['outcome']

def completion_results(result,event):
    validate_events([result])
    if result['start']!=event['start']: raise ValueError('event_completion_unauthorized')
    for key in ('title','end','location'):
        if event[key] is not None and result[key]!=event[key]:
            raise ValueError('event_completion_unauthorized')
    # Location is the dedicated direct-evidence field; null supplies no place.
    if event['location'] is None and result['location'] is not None:
        raise ValueError('event_completion_unauthorized')
    if event['date_note'] is not None and result['date_note'] not in (None,event['date_note']):
        raise ValueError('event_completion_unauthorized')
    return result

def process(events,config, *, clock=None):
    from local_agentic_triage.events.calendar import Calendar, canonical
    if not isinstance(events,list): raise ValueError('event_structure')
    calendar=Calendar(config,clock=clock)
    results=[]
    for index,event in enumerate(events):
        validate_events([event])
        reason=start_reason(event)
        outcome='skipped'
        if reason is None:
            nearby=calendar.nearby(event)
            decision=duplicate_results(model_batch(prompt(),DUPLICATE_SCHEMA,
                {'event':event,'nearby':nearby}))
            if decision=='existing': outcome='existing'
            elif decision=='possible_duplicate': reason='possible_duplicate'
            else:
                completed=completion_results(model_batch(completion_prompt(),COMPLETION_SCHEMA,
                    {'event':event}),event)
                try:
                    validate_final(completed)
                except FinalSemanticError as error:
                    results.append({'index':index,'outcome':'skipped','reason':error.reason})
                    continue
                canonical(completed)
                if calendar.too_soon(completed): reason='too_soon'
                else:
                    calendar.create(completed)
                    outcome='created'
        results.append({'index':index,'outcome':outcome,'reason':reason})
    return {'status':'ok','results':results}


def extraction_prompt():
    return files(__package__).joinpath('extraction.md').read_text(encoding='utf-8')

EXTRACTION_SCHEMA=response_schema(__package__,'extraction')

def extract(mail):
    result=chat_completion([{'role':'system','content':extraction_prompt()},
                            {'role':'user','content':json.dumps(mail_payload(mail),ensure_ascii=False)}],EXTRACTION_SCHEMA)
    if not isinstance(result,dict) or set(result)!={'events'}: raise ValueError('event_extraction_keys')
    validate_events(result['events'])
    return result['events']
