from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import re

TRUSTED_VULCAN_SENDER='noreply@vulcan.net.pl'

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

def vulcan_sender(raw):
    """Match the same deliberately narrow From grammar as agentic-vulcan."""
    if not isinstance(raw,bytes): return False
    boundary=raw.find(b'\r\n\r\n')
    if boundary<0: boundary=raw.find(b'\n\n')
    if boundary<0: return False
    header=raw[:boundary]
    if re.search(br'\r(?!\n)',header): return False
    normalized=header.replace(b'\r\n',b'\n')
    try: text=normalized.decode('utf-8')
    except UnicodeError: return False
    unfolded=[]
    for line in text.split('\n'):
        if line.startswith((' ','\t')):
            if not unfolded: return False
            unfolded[-1]+=' '+line.lstrip(' \t')
        else: unfolded.append(line)
    found=[]
    for line in unfolded:
        index=line.find(':')
        if index<=0 or not re.fullmatch(r'[!-9;-~]+',line[:index]): return False
        if line[:index].lower()=='from': found.append(line[index+1:])
    if len(found)!=1: return False
    value=found[0]
    if re.search(r'[\x00-\x08\x0a-\x1f\x7f]',value): return False
    value=value.strip(' \t')
    if value.lower()==TRUSTED_VULCAN_SENDER: return True
    bracketed=re.fullmatch(r'(.*?)<([^<>]+)>',value)
    if not bracketed or bracketed.group(2).lower()!=TRUSTED_VULCAN_SENDER: return False
    display=bracketed.group(1).rstrip(' \t')
    if not display: return True
    if re.search(r'[<>@()]',display): return False
    if display.startswith('"'):
        return (re.fullmatch(r'"(?:[^"\\]|\\["\\])+"',display) is not None
                and bool(display[1:-1].strip()))
    return re.search(r'[",;:\\]',display) is None

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
