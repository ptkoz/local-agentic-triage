from importlib.resources import files
from datetime import date, datetime
import json
import re
import urllib.request
from dataclasses import replace

ENDPOINT='http://127.0.0.1:12434/engines/v1/chat/completions'
MODEL='docker.io/ai/ministral3-mail-8b:16k'
CONTEXT_TOKENS=16384
MAX_TOKENS=2048
TIMEOUT=300
MAIL_FIELD_BUDGETS={'sent_date':256,'sender':1024,'subject':2048,'body':6400}
TRUNCATION='[content truncated]'
def response_schema(package,name):
    return json.loads(files(package).joinpath(name+'.json').read_text(encoding='utf-8'))

CLASSIFICATION_SCHEMA=response_schema(__package__,'destination')
SUMMARY_SCHEMA=response_schema(__package__,'summary')

class ModelEnvelopeJsonInvalid(ValueError): pass
class ModelContentJsonInvalid(ValueError): pass

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

def compact_json(value): return json.dumps(value,ensure_ascii=False,separators=(',',':'))

def mail_payload(mail):
    result={field:bounded_string(getattr(mail,field),budget) for field,budget in MAIL_FIELD_BUDGETS.items()}
    if len(compact_json(result).encode())>10000:
        raise ValueError('mail payload size')
    return result

def mail_source(mail):
    """Model view of a bounded mail: parsed body apart from date, sender and subject."""
    fields=mail_payload(mail)
    source={'metadata':{name:fields[name] for name in ('sent_date','sender','subject')},'body':fields['body']}
    if len(compact_json(source).encode())>10000: raise ValueError('mail payload size')
    return source

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

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): raise ValueError('model redirect forbidden')

def chat_completion(messages,response_format):
    data={'model':MODEL,'temperature':0,'response_format':response_format,'max_tokens':MAX_TOKENS,'messages':messages}
    request=urllib.request.Request(ENDPOINT,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    # Disable environment proxies; only this fixed loopback endpoint is permitted.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=TIMEOUT) as response:
        raw=response.read()
    try:
        envelope=strict_json(raw.decode('utf-8') if isinstance(raw,bytes) else raw)
    except (TypeError,ValueError,UnicodeError):
        raise ModelEnvelopeJsonInvalid from None
    try:
        if (not isinstance(envelope,dict) or not isinstance(envelope['choices'],list)
                or len(envelope['choices'])!=1 or not isinstance(envelope['choices'][0],dict)):
            raise ValueError
        result=envelope['choices'][0]
        finish_reason=result['finish_reason']
        content=result['message']['content']
        if not isinstance(result['message'],dict): raise ValueError
    except (KeyError,IndexError,TypeError,ValueError):
        raise ModelEnvelopeJsonInvalid from None
    if finish_reason!='stop': raise ValueError('model incomplete')
    try:
        return strict_json(content)
    except (TypeError,ValueError,UnicodeError):
        raise ModelContentJsonInvalid from None

def disposition_prompt():
    return files(__package__).joinpath('destination.md').read_text(encoding='utf-8')

def summary_prompt():
    return files(__package__).joinpath('summary.md').read_text(encoding='utf-8')

def validate_destination(obj):
    if not isinstance(obj,dict) or set(obj)!={'destination'}:
        raise ValueError('classification keys')
    if obj['destination'] not in ('leave','archive'):
        raise ValueError('destination')
    return obj

def validate_summary(obj):
    if not isinstance(obj,dict) or set(obj)!={'summary'}:
        raise ValueError('classification keys')
    if not isinstance(obj['summary'],str) or not obj['summary']:
        raise ValueError('summary')
    return obj

def validate_disposition(obj):
    if obj not in ('leave','archive'):
        raise ValueError('destination')
    return obj

def summarize(mail):
    source=mail_source(mail)
    return validate_summary(chat_completion([
        {'role':'system','content':summary_prompt()},
        {'role':'user','content':compact_json(source)},
    ],SUMMARY_SCHEMA))['summary']

def classify(mail,summary):
    source=mail_source(mail)
    return validate_destination(chat_completion([
        {'role':'system','content':disposition_prompt()},
        {'role':'user','content':json.dumps({'summary':summary,'mail':source},ensure_ascii=False)},
    ],CLASSIFICATION_SCHEMA))['destination']
