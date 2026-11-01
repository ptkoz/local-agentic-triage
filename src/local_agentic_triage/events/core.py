from importlib.resources import files
from datetime import datetime, timezone
import json
from local_agentic_triage.classifier.core import CONTEXT_TOKENS, MAX_TOKENS, chat_completion, strict_json, timestamp, validate, mail_payload

def prompt(): return files(__package__).joinpath('duplicate.md').read_text(encoding='utf-8')

def completion_prompt(): return files(__package__).joinpath('completion.md').read_text(encoding='utf-8')

SKIP_REASONS={'invalid_title','invalid_date','missing_date','uncertain_date','missing_end','past_event','cancelled_event','possible_duplicate','too_soon'}
ERROR_REASONS={'search_failed','create_failed','verification_failed','invalid_input'}
# Conservatively budget at most one model token per UTF-8 byte within the 16k
# context. Reserve the prompt, schema, framing, and maximum output. Reject the
# whole user payload rather than truncate nearby rows and hide a duplicate.
EVENT_TEMPLATE_OVERHEAD_BYTES=1024
def batch_schema(name,properties):
    return {'type':'json_schema','json_schema':{'name':name,'strict':True,'schema':{
        'type':'object','additionalProperties':False,'required':['results'],
        'properties':{'results':{'type':'array','items':{
            'type':'object','additionalProperties':False,'required':list(properties),
            'properties':properties}}}}}}

DUPLICATE_SCHEMA=batch_schema('event_duplicates',{
    'index':{'type':'integer'},
    'outcome':{'type':'string','enum':['existing','possible_duplicate','no_match']}})
COMPLETION_SCHEMA=batch_schema('event_completion',{
    'index':{'type':'integer'},'outcome':{'type':'string','enum':['complete','skipped']},
    'reason':{'type':['string','null'],'enum':[None,'missing_end']},
    'estimated_end':{'type':['string','null']}})

class ModelRequestFailed(RuntimeError):
    pass

def event_user_budget(system=None,schema=None):
    reserved=len((system or prompt()).encode('utf-8'))+len(json.dumps(schema or DUPLICATE_SCHEMA).encode('utf-8'))+EVENT_TEMPLATE_OVERHEAD_BYTES+MAX_TOKENS
    budget=CONTEXT_TOKENS-reserved
    if budget<1: raise RuntimeError('event_payload_budget')
    return budget

def validate_events(events):
    validate({'destination':'leave','summary':None,'events':events})
    for event in events:
        if event['date_note'] is not None and (event['start'] is not None or event['end'] is not None):
            raise ValueError('event_unresolved_date')
        if event['end'] is not None and len(event['start'])==10 and event['end']<=event['start']:
            raise ValueError('event_order')
    if any(len(event['title'])>200 for event in events): raise ValueError('event_title_limit')

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
            missing_timed_end=isinstance(event['start'],str) and len(event['start'])>10 and event['end'] is None
            if (event['start'] is None or event['date_note'] is not None or
                    (missing_timed_end and outcome=='created' and not processed)):
                raise ValueError('unsafe_event_outcome')
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

def validate_batch(result,indices,keys):
    if not isinstance(result,dict) or set(result)!={'results'} or not isinstance(result['results'],list):
        raise ValueError('event_result_keys')
    if len(result['results'])!=len(indices): raise ValueError('event_result_coverage')
    for entry,index in zip(result['results'],indices):
        if not isinstance(entry,dict) or set(entry)!=keys: raise ValueError('event_outcome_keys')
        if type(entry['index']) is not int or entry['index']!=index: raise ValueError('event_outcome_index')
    return result['results']

def duplicate_results(result,events):
    entries=validate_batch(result,range(len(events)),{'index','outcome'})
    for entry in entries:
        if entry['outcome'] not in ('existing','possible_duplicate','no_match'):
            raise ValueError('event_outcome_invalid')
    return entries

def completion_results(result,events,indices):
    entries=validate_batch(result,indices,{'index','outcome','reason','estimated_end'})
    for entry in entries:
        event=events[entry['index']]
        if event['end'] is not None or event['start'] is None or len(event['start'])==10:
            raise ValueError('event_completion_unauthorized')
        if entry['outcome']=='skipped':
            if entry['reason']!='missing_end' or entry['estimated_end'] is not None:
                raise ValueError('event_completion_invalid')
        elif entry['outcome']=='complete':
            if entry['reason'] is not None: raise ValueError('event_completion_invalid')
            estimate=entry['estimated_end']
            timestamp(estimate)
            if not isinstance(estimate,str) or len(estimate)==10 or datetime.fromisoformat(estimate)<=datetime.fromisoformat(event['start']):
                raise ValueError('event_estimated_end')
        else: raise ValueError('event_completion_invalid')
    return entries

def process(events,config, *, clock=None):
    from local_agentic_triage.events.calendar import Calendar, canonical
    validate_events(events)
    if not events: return {'status':'ok','results':[]}
    calendar=Calendar(config,clock=clock)
    gate_now=calendar.clock(timezone.utc)
    eligible=[]; results=[]
    for index,event in enumerate(events):
        reason=('uncertain_date' if event['date_note'] is not None else
                'missing_date' if event['start'] is None else
                'too_soon' if calendar.too_soon(event,now=gate_now) else None)
        if reason:
            results.append({'index':index,'outcome':'skipped','reason':reason})
        else: eligible.append((index,event))
    if not eligible: return {'status':'ok','results':results}
    selected=[event for _,event in eligible]
    nearby=[calendar.nearby(event) for event in selected]
    sparse=[{key:event[key] for key in ('title','start','end')} for event in selected]
    decisions=duplicate_results(model_batch(prompt(),DUPLICATE_SCHEMA,
        {'calendar':config.calendar,'events':sparse,'nearby':nearby}),selected)
    # Exact sparse equality is a fail-safe when the model misses an earlier
    # candidate. Do this only after validating the entire duplicate batch and
    # before completion; never use guessed ends or fuzzy title matching.
    seen=set()
    decisions=[dict(entry) for entry in decisions]
    for entry,event in zip(decisions,sparse):
        identity=tuple(event[key] for key in ('title','start','end'))
        if identity in seen: entry['outcome']='existing'
        seen.add(identity)
    required=[entry['index'] for entry in decisions if entry['outcome']=='no_match'
              and selected[entry['index']]['end'] is None and len(selected[entry['index']]['start'])>10]
    patches={}
    if required:
        payload={'events':[{'index':index,'title':selected[index]['title'],'start':selected[index]['start']} for index in required]}
        patches={entry['index']:entry for entry in completion_results(
            model_batch(completion_prompt(),COMPLETION_SCHEMA,payload),selected,required)}
    materialized=list(events); creates=[]
    for entry in decisions:
        index=entry['index']; original,event=eligible[index]
        outcome=entry['outcome']; reason=None
        if outcome=='possible_duplicate': outcome='skipped'; reason='possible_duplicate'
        elif outcome=='no_match':
            patch=patches.get(index)
            if patch and patch['outcome']=='skipped': outcome='skipped'; reason='missing_end'
            else:
                if patch: event=event|{'end':patch['estimated_end']}
                canonical(event)
                materialized[original]=event
                creates.append((original,event))
                outcome='created'
        results.append({'index':original,'outcome':outcome,'reason':reason})
    combined={'status':'ok','results':sorted(results,key=lambda entry:entry['index'])}
    # Validate every materialized result and recheck every creation's time before
    # the first write, including when the model calls have taken a long time.
    final=parse_results(json.dumps(combined),materialized)
    write_now=calendar.clock(timezone.utc)
    if any(calendar.too_soon(event,now=write_now) for _,event in creates):
        raise ValueError('calendar_too_soon')
    for _,event in creates: calendar.create(event)
    return final


def extraction_prompt():
    return files(__package__).joinpath('extraction.md').read_text(encoding='utf-8')

EXTRACTION_SCHEMA={
    'type':'json_schema','json_schema':{'name':'event_extraction','strict':True,'schema':{
        'type':'object','additionalProperties':False,'required':['events'],
        'properties':{'events':{'type':'array','items':{
            'type':'object','additionalProperties':False,
            'required':['title','start','end','date_note'],
            'properties':{'title':{'type':'string'},
                          **{key:{'type':['string','null']} for key in ('start','end','date_note')}}}}}}}}

def extract(mail):
    result=chat_completion([{'role':'system','content':extraction_prompt()},
                            {'role':'user','content':json.dumps(mail_payload(mail),ensure_ascii=False)}],EXTRACTION_SCHEMA)
    if not isinstance(result,dict) or set(result)!={'events'}: raise ValueError('event_extraction_keys')
    validate_events(result['events'])
    return result['events']
