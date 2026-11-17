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


def _string(value):
    return json.dumps(value,ensure_ascii=False)


@dataclass(frozen=True)
class ContextBoundMail:
    """Decorate Mail with a bounded, cached model serialization."""
    mail: Mail
    limit: int

    def __getattr__(self,name):
        if name.startswith('_'): raise AttributeError(name)
        return getattr(self.mail,name)

    @cached_property
    def _bounded(self):
        return (
            _bounded_string(self.mail.sent_date,256),
            _bounded_string(self.mail.sender,1024),
            _bounded_string(self.mail.subject,2048),
            _bounded_string(self.mail.body,6400),
        )

    def _serialize(self,item_separator,key_separator):
        sent_date,sender,subject,body=self._bounded
        metadata=item_separator.join((
            _string('sent_date')+key_separator+_string(sent_date),
            _string('sender')+key_separator+_string(sender),
            _string('subject')+key_separator+_string(subject),
        ))
        return ('{'+_string('metadata')+key_separator+'{'+metadata+'}'
                +item_separator+_string('body')+key_separator+_string(body)+'}')

    @cached_property
    def _model_json(self):
        value=self._serialize(',',':')
        if len(value.encode())>self.limit: raise ValueError('mail payload size')
        return value


def serialize_mail(context_bound_mail: ContextBoundMail) -> str:
    return context_bound_mail._model_json


def serialize_destination_input(context_bound_mail: ContextBoundMail,summary: str) -> str:
    serialize_mail(context_bound_mail)
    mail=context_bound_mail._serialize(', ',': ')
    return '{"summary": '+_string(summary)+', "mail": '+mail+'}'


def fit_to_context(mail: Mail,limit: int) -> ContextBoundMail:
    return ContextBoundMail(mail,limit)
