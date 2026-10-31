import logging
import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from local_agentic_triage.common.diagnostics import error_code, error_suffix, CommandError

log=logging.getLogger('local_agentic_triage')
WARSAW=ZoneInfo('Europe/Warsaw')
MONTHS=('','sty','lut','mar','kwi','maj','cze','lip','sie','wrz','paź','lis','gru')
MAX_SNOWFLAKE=2**64-1
EMAIL=re.compile(r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}(?![A-Za-z0-9.-])")
SEPARATOR=r'[.\u3002\uff0e\uff61]'
MARKDOWN=str.maketrans({'|':'/','`':"'",'\\':'＼','*':'＊','_':'＿','~':'～','#':'＃','[':'［',']':'］','(':'（',')':'）','+':'＋','-':'‐','<':'‹','>':'›'})
EMAIL_MARKDOWN=str.maketrans({'|':'/','`':"'",'\\':'＼','*':'＊','_':'＿','~':'～'})


def neutralize(value):
    value=re.sub(r'@(everyone|here)\b(?!\.[A-Za-z0-9-])',lambda match:'＠'+match.group(1),value,flags=re.IGNORECASE)
    value=re.sub(r'(?i)\b([a-z][a-z0-9+.-]*):',lambda match:match.group(1)+'：',value)
    domain=rf'(?<![@\w])(?:[\w-]+{SEPARATOR})+[\w-]{{2,63}}(?:/[^\s]*)?'
    value=re.sub(domain,lambda match:re.sub(SEPARATOR,'·',match.group(0)),value)
    ipv4=rf'(?<![@\w])(?:[0-9]{{1,3}}{SEPARATOR}){{3}}[0-9]{{1,3}}(?:/[^\s]*)?'
    value=re.sub(ipv4,lambda match:re.sub(SEPARATOR,'·',match.group(0)),value)
    value=value.translate(MARKDOWN)
    return re.sub(r'^([0-9]+)\. ',r'\1． ',value)


def sanitize(value):
    cleaned=''.join(' ' if unicodedata.category(c).startswith('C') or c.isspace() else c for c in value)
    cleaned=' '.join(cleaned.split())
    parts=[]; offset=0
    for match in EMAIL.finditer(cleaned):
        local,domain=match.group(0).rsplit('@',1)
        parts.extend((neutralize(cleaned[offset:match.start()]),local.translate(EMAIL_MARKDOWN)+'@'+domain))
        offset=match.end()
    parts.append(neutralize(cleaned[offset:]))
    return ''.join(parts)


def boundary(value):
    if isinstance(value,str):
        try: value=datetime.fromisoformat(value)
        except ValueError: raise ValueError('invalid report boundary') from None
    if not isinstance(value,datetime) or value.tzinfo is None: raise ValueError('invalid report boundary')
    return value.astimezone(WARSAW)


def label(value,comma=False):
    value=boundary(value); separator=', ' if comma else ' '
    return f'{value.day} {MONTHS[value.month]}{separator}{value:%H:%M}'


def header(user_id,start,end):
    if not re.fullmatch(r'[1-9][0-9]{0,19}',user_id) or int(user_id)>MAX_SNOWFLAKE: raise ValueError('invalid discord user id')
    if boundary(end)<boundary(start): raise ValueError('invalid report period')
    return f'<@{user_id}>, między {label(start)} a {label(end)}, następujące wiadomości zostały przeniesione do archiwum:'


def record_message(row,limit):
    if not 100<=limit<=2000: raise ValueError('report limit invalid')
    sender=sanitize(row['sender']); summary=sanitize(row['summary'])
    if row['sent_at']:
        text=f'Od **{sender}** z dnia {label(row["sent_at"],comma=True)}\n> {summary}'
    else:
        text=f'Od **{sender}** — data wiadomości niedostępna\n> {summary}'
    if len(text)>limit: raise ValueError('report record too long')
    return text


def deliver(runner,config,text,kind,index=None):
    try:
        result=runner([config.hermes,'send','--to',config.channel],input=text,text=True,capture_output=True,timeout=config.timeout,shell=False)
    except Exception as exc:
        suffix=f' record={index}' if index is not None else ''
        log.error('report_%s_failed%s error=%s',kind,suffix,error_code(exc))
        raise
    if result.returncode:
        exc=CommandError('report_command_failed',result.returncode)
        suffix=f' record={index}' if index is not None else ''
        log.error('report_%s_failed%s error=%s%s',kind,suffix,error_code(exc),error_suffix(exc))
        raise exc


def send(store,config,at=None,*,runner=None):
    if runner is None: raise RuntimeError('report_delivery_disabled')
    end=at or datetime.now(timezone.utc)
    if not isinstance(end,datetime) or end.tzinfo is None: raise ValueError('invalid report boundary')
    end=end.astimezone(timezone.utc)
    existing=store.current_report_window()
    start,stamp=store.report_window(end.isoformat())
    rows=store.pending(stamp)
    window_has_rows=store.report_window_has_rows()
    if window_has_rows is None:
        window_has_rows=bool(rows)
        store.set_report_window_has_rows(window_has_rows)
    log.info('report_started rows=%d',len(rows))
    if not rows:
        if existing is not None and window_has_rows:
            store.complete_report_window(stamp)
            log.info('report_window_completed_after_retry')
            return
        text=f'Od {label(start,comma=True)} nie zarejestrowano żadnych wiadomości przeniesionych do archiwum.'
        deliver(runner,config,text,'empty')
        store.complete_report_window(stamp)
        log.info('report_empty_completed')
        return
    messages=[record_message(row,config.discord_limit) for row in rows]
    intro=header(config.discord_user_id,start,stamp)
    if len(intro)>config.discord_limit: raise ValueError('report intro too long')
    deliver(runner,config,intro,'intro')
    log.info('report_intro_completed rows=%d',len(rows))
    for index,(row,text) in enumerate(zip(rows,messages),1):
        log.info('report_record_sending record=%d total=%d',index,len(rows))
        deliver(runner,config,text,'record',index)
        store.reported([row['source_key']],stamp)
        log.info('report_record_completed record=%d',index)
    store.complete_report_window(stamp)
