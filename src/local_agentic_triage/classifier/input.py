from dataclasses import dataclass
from functools import cached_property
import json

from local_agentic_triage.mail.mime import Mail

MAIL_PAYLOAD_BYTES=10000
TRUNCATION='[content truncated]'


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
    def sent_date(self) -> str:
        return _bounded_string(self.mail.sent_date,256)

    @cached_property
    def sender(self) -> str:
        return _bounded_string(self.mail.sender,1024)

    @cached_property
    def subject(self) -> str:
        return _bounded_string(self.mail.subject,2048)

    @cached_property
    def body(self) -> str:
        return _bounded_string(self.mail.body,6400)

    @cached_property
    def source(self) -> dict[str,object]:
        source={
            'metadata':{
                'sent_date':self.sent_date,
                'sender':self.sender,
                'subject':self.subject,
            },
            'body':self.body,
        }
        if len(_compact(source).encode())>self.limit:
            raise ValueError('mail payload size')
        return source

    @cached_property
    def source_json(self) -> str:
        return _compact(self.source)


def fit_to_context(mail: Mail,limit: int) -> ContextBoundMail:
    return ContextBoundMail(mail,limit)
