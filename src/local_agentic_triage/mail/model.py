from dataclasses import dataclass
import json

MAIL_FIELD_BUDGETS={'sent_date':256,'sender':1024,'subject':2048,'body':6400}
TRUNCATION='[content truncated]'


class FrozenDict(dict):
    def _immutable(self,*args,**kwargs):
        raise TypeError('prepared mail is immutable')

    __setitem__=_immutable
    __delitem__=_immutable
    __ior__=_immutable
    clear=_immutable
    pop=_immutable
    popitem=_immutable
    setdefault=_immutable
    update=_immutable


@dataclass(frozen=True)
class PreparedMail:
    sender: str
    sent_date: str
    body: str
    subject: str
    source: FrozenDict
    source_json: str


def _bounded_string(value,budget):
    if not isinstance(value,str): raise ValueError('mail field type')
    encoded=lambda text:len(json.dumps(text,ensure_ascii=False).encode())
    if encoded(value)<=budget: return value
    low=0; high=len(value)
    while low<high:
        middle=(low+high+1)//2
        if encoded(value[:middle]+TRUNCATION)<=budget: low=middle
        else: high=middle-1
    return value[:low]+TRUNCATION


def prepare(mail):
    fields={field:_bounded_string(getattr(mail,field),budget)
            for field,budget in MAIL_FIELD_BUDGETS.items()}
    compact=lambda value:json.dumps(value,ensure_ascii=False,separators=(',',':'))
    if len(compact(fields).encode())>10000: raise ValueError('mail payload size')
    source=FrozenDict(metadata=FrozenDict(
        {name:fields[name] for name in ('sent_date','sender','subject')}),
        body=fields['body'])
    source_json=compact(source)
    if len(source_json.encode())>10000: raise ValueError('mail payload size')
    prepared=PreparedMail(sender=fields['sender'],sent_date=fields['sent_date'],
                          body=fields['body'],subject=fields['subject'],
                          source=source,source_json=source_json)
    truncated=tuple(field for field,value in fields.items()
                    if value!=getattr(mail,field))
    return prepared,truncated
