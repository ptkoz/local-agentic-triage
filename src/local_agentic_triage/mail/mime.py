from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]; self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'): self.hidden+=1
        if tag in ('br','p','div','li','tr'): self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'): self.hidden=max(0,self.hidden-1)
        if tag in ('p','div','li','tr'): self.parts.append('\n')
    def handle_data(self,data):
        if not self.hidden: self.parts.append(data)

@dataclass(frozen=True)
class Mail:
    sender: str
    sent_date: str
    body: str
    subject: str = ''

def parse(raw,limit):
    if limit and len(raw)>limit: raise ValueError('mail size limit')
    message=BytesParser(policy=policy.default).parsebytes(raw)
    plain=[]; html=[]
    def visit(part):
        if part.get_content_disposition()=='attachment' or part.get_filename(): return
        if part.is_multipart():
            for child in part.iter_parts(): visit(child)
        elif part.get_content_type() in ('text/plain','text/html'):
            content=part.get_content()
            (plain if part.get_content_type()=='text/plain' else html).append(content)
    visit(message)
    if plain: body='\n'.join(plain).strip()
    else:
        parser=Text(); parser.feed('\n'.join(html)); body='\n'.join(x.strip() for x in ''.join(parser.parts).splitlines() if x.strip())
    sent_date=''
    try:
        parsed=parsedate_to_datetime(str(message.get('Date','')))
        if parsed.tzinfo is not None: sent_date=parsed.isoformat()
    except (TypeError,ValueError,OverflowError):
        pass
    return Mail(str(message.get('From','')),sent_date,body,str(message.get('Subject','')))
