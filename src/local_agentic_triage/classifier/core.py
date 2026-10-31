from importlib.resources import files
from datetime import date, datetime
import json
import re
import urllib.request
from dataclasses import replace

ENDPOINT='http://127.0.0.1:12434/engines/v1/chat/completions'
MODEL='docker.io/ai/qwen35-mail:16k'
MAIL_FIELD_BUDGETS={'sent_date':256,'sender':1024,'subject':2048,'body':6400}
TRUNCATION='[content truncated]'
CLASSIFICATION_SCHEMA={
    'type':'json_schema',
    'json_schema':{
        'name':'mail_classification','strict':True,
        'schema':{
            'type':'object','additionalProperties':False,
            'required':['destination','summary','events'],
            'properties':{
                'destination':{'type':'string','enum':['leave','archive']},
                'summary':{'type':['string','null']},
                'events':{'type':'array','items':{
                    'type':'object','additionalProperties':False,
                    'required':['title','start','end','date_note'],
                    'properties':{
                        'title':{'type':'string'},
                        'start':{'type':['string','null']},
                        'end':{'type':['string','null']},
                        'date_note':{'type':['string','null']},
                    },
                }},
            },
        },
    },
}

class ModelEnvelopeJsonInvalid(ValueError): pass
class ModelContentJsonInvalid(ValueError): pass

def prompt(): return files(__package__).joinpath('prompt.md').read_text(encoding='utf-8')

def bounded_string(value,budget):
    if not isinstance(value,str): raise ValueError('mail field type')
    encoded=lambda text:len(json.dumps(text,ensure_ascii=False).encode())
    if encoded(value)<=budget: return value
    low=0; high=len(value)
    while low<high:
        middle=(low+high+1)//2
        if encoded(value[:middle]+TRUNCATION)<=budget: low=middle
        else: high=middle-1
    return value[:low]+TRUNCATION

def mail_payload(mail):
    result={field:bounded_string(getattr(mail,field),budget) for field,budget in MAIL_FIELD_BUDGETS.items()}
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode())>10000:
        raise ValueError('mail payload size')
    return result

def bound_mail(mail):
    payload=mail_payload(mail)
    truncated=tuple(field for field,value in payload.items() if value!=getattr(mail,field))
    return replace(mail,**payload),truncated

def strict_json(text):
    def pairs(items):
        obj={}
        for key,value in items:
            if key in obj: raise ValueError('duplicate key')
            obj[key]=value
        return obj
    return json.loads(text,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('constant')))

def timestamp(value):
    if value is None: return
    if not isinstance(value,str): raise ValueError('date type')
    if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value): date.fromisoformat(value)
    else:
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?(?:Z|[+-][0-9]{2}:[0-9]{2})',value): raise ValueError('date format')
        dt=datetime.fromisoformat(value)
        if 'T' not in value or dt.tzinfo is None: raise ValueError('date timezone')

def validate(obj):
    if not isinstance(obj,dict) or set(obj)!={'destination','summary','events'}: raise ValueError('classification keys')
    if obj['destination']=='leave':
        if obj['summary'] is not None: raise ValueError('leave summary')
    elif obj['destination']=='archive':
        if not isinstance(obj['summary'],str): raise ValueError('archive summary')
    else: raise ValueError('destination')
    if not isinstance(obj['events'],list): raise ValueError('events type')
    for e in obj['events']:
        if not isinstance(e,dict) or set(e)!={'title','start','end','date_note'}: raise ValueError('event keys')
        if not isinstance(e['title'],str) or not e['title'].strip(): raise ValueError('event title')
        if e['date_note'] is not None and (not isinstance(e['date_note'],str) or not e['date_note'].strip()): raise ValueError('date note')
        timestamp(e['start']); timestamp(e['end'])
        if e['end'] is not None:
            if e['start'] is None or (len(e['start'])==10) != (len(e['end'])==10): raise ValueError('event range')
            if len(e['start'])==10:
                if e['end']<e['start']: raise ValueError('event order')
            elif datetime.fromisoformat(e['end'])<=datetime.fromisoformat(e['start']): raise ValueError('event order')
    return obj

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): raise ValueError('model redirect forbidden')

def classify(mail):
    data={'model':MODEL,'temperature':0,'chat_template_kwargs':{'enable_thinking':False},'response_format':CLASSIFICATION_SCHEMA,'max_tokens':2048,'messages':[{'role':'system','content':prompt()},{'role':'user','content':json.dumps(mail_payload(mail),ensure_ascii=False)}]}
    request=urllib.request.Request(ENDPOINT,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    # Disable environment proxies; only this fixed loopback endpoint is permitted.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=120) as response:
        raw=response.read(1000001)
    if len(raw)>1000000: raise ValueError('model size limit')
    try:
        envelope=strict_json(raw)
    except (TypeError,ValueError,UnicodeError):
        raise ModelEnvelopeJsonInvalid from None
    result=envelope['choices'][0]
    if result['finish_reason']!='stop': raise ValueError('model incomplete')
    try:
        content=strict_json(result['message']['content'])
    except (TypeError,ValueError,UnicodeError):
        raise ModelContentJsonInvalid from None
    return validate(content)
