from dataclasses import dataclass
from functools import cached_property
import json

from local_agentic_triage.mail.mime import Mail

MAIL_FIELD_BUDGETS={'sent_date':256,'sender':1024,'subject':2048,'body':6400}
MAIL_PAYLOAD_BYTES=10000
TRUNCATION='[content truncated]'


class FrozenDict(dict):
    def _immutable(self,*args,**kwargs):
        raise TypeError('context-bound mail is immutable')

    __setitem__=_immutable
    __delitem__=_immutable
    __ior__=_immutable
    clear=_immutable
    pop=_immutable
    popitem=_immutable
    setdefault=_immutable
    update=_immutable


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


def _compact(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


@dataclass(frozen=True)
class ContextBoundMail:
    mail: Mail
    limit: int

    @cached_property
    def _fitted(self):
        fields={field:_bounded_string(getattr(self.mail,field),budget)
                for field,budget in MAIL_FIELD_BUDGETS.items()}
        if len(_compact(fields).encode())>self.limit:
            raise ValueError('mail payload size')
        source=FrozenDict(metadata=FrozenDict(
            {name:fields[name] for name in ('sent_date','sender','subject')}),
            body=fields['body'])
        source_json=_compact(source)
        if len(source_json.encode())>self.limit:
            raise ValueError('mail payload size')
        return source,source_json

    @cached_property
    def source(self):
        return self._fitted[0]

    @cached_property
    def source_json(self):
        return self._fitted[1]


def fit_to_context(mail,limit) -> ContextBoundMail:
    return ContextBoundMail(mail,limit)
