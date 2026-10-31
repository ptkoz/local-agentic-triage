"""Fixed Calendar capability: configured-calendar reads and verified creates."""
from datetime import date, datetime, timedelta, time
import json
import subprocess
from local_agentic_triage.classifier.core import strict_json

# This source is constant. The only input is a JSON document read from stdin.
SCRIPT=r'''
ObjC.import('Foundation');
function run() {
    const input = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
    const data = JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding(input, $.NSUTF8StringEncoding)));
    const app = Application('Calendar');
    const calendars = app.calendars.whose({name: data.calendar})();
    if (calendars.length !== 1) throw Error('calendar_boundary');
    const calendar = calendars[0];
    function parse(s) {
        if (s.length === 10) { const a=s.split('-').map(Number); return new Date(a[0],a[1]-1,a[2]); }
        return new Date(s);
    }
    function day(d) {
        return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
    }
    function minimal(e) {
        const all=e.alldayEvent();
        return {title:e.summary(),start:all?day(e.startDate()):e.startDate().toISOString(),
                end:all?day(e.endDate()):e.endDate().toISOString(),all_day:all};
    }
    if (data.operation === 'search') {
        const matches=calendar.events.whose({_and:[{startDate:{_lessThanEquals:parse(data.upper)}},
                                                {endDate:{_greaterThanEquals:parse(data.lower)}}]})();
        if (matches.length > 1000) throw Error('calendar_limit');
        return JSON.stringify(matches.map(minimal));
    }
    if (data.operation !== 'create') throw Error('calendar_operation');
    const e=data.event;
    const created=app.Event({summary:e.title,startDate:parse(e.start),endDate:parse(e.end),alldayEvent:e.all_day});
    calendar.events.push(created);
    const id=created.uid();
    const readback=calendar.events.whose({uid:id})();
    if (readback.length !== 1) throw Error('calendar_readback');
    return JSON.stringify(minimal(readback[0]));
}
'''

def canonical(event):
    from local_agentic_triage.events.core import validate_events
    validate_events([event])
    if event['start'] is None or event['date_note'] is not None:
        raise ValueError('calendar_invalid_event')
    all_day=len(event['start'])==10
    if not all_day and event['end'] is None: raise ValueError('calendar_invalid_event')
    start=event['start']; end=event['end']
    if all_day:
        end=end or (date.fromisoformat(start)+timedelta(days=1)).isoformat()
        if end<=start: raise ValueError('calendar_invalid_event')
    else:
        start=datetime.fromisoformat(start).isoformat()
        end=datetime.fromisoformat(end).isoformat()
    return {'title':event['title'],'start':start,'end':end,'all_day':all_day}

def same(actual,expected):
    if not isinstance(actual,dict) or set(actual)!=set(expected): return False
    if actual['title']!=expected['title'] or actual['all_day'] is not expected['all_day']: return False
    try:
        if expected['all_day']: return actual==expected
        return all(datetime.fromisoformat(actual[k])==datetime.fromisoformat(expected[k]) for k in ('start','end'))
    except (ValueError,TypeError): return False

class Calendar:
    def __init__(self,config): self.config=config
    def call(self,data):
        if 'calendar' in data: raise ValueError('calendar_boundary')
        data={'calendar':self.config.calendar,**data}
        try:
            result=subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',SCRIPT],input=json.dumps(data,ensure_ascii=False),text=True,capture_output=True,timeout=self.config.timeout,shell=False)
            if result.returncode or len(result.stdout)>1000000: raise ValueError('calendar_failed')
            return strict_json(result.stdout)
        except (OSError,subprocess.SubprocessError,ValueError,TypeError):
            raise RuntimeError('calendar_adapter_failed') from None
    def nearby(self,event):
        if event['start'] is None: return []
        start=event['start']
        dt=date.fromisoformat(start) if len(start)==10 else datetime.fromisoformat(start)
        lower=(dt-timedelta(days=7)).isoformat()
        upper=(dt+timedelta(days=7)).isoformat()
        if len(start)==10:
            upper=datetime.combine(dt+timedelta(days=7),time.max).isoformat()
        rows=self.call({'operation':'search','lower':lower,'upper':upper})
        if not isinstance(rows,list) or len(rows)>1000: raise ValueError('calendar_search_invalid')
        normalized=[]
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'title','start','end','all_day'} or type(row['all_day']) is not bool:
                raise ValueError('calendar_search_invalid')
            if row['all_day'] is True and row['start']==row['end'] and isinstance(row['start'],str):
                try:
                    if len(row['start'])==10:
                        end=(date.fromisoformat(row['start'])+timedelta(days=1)).isoformat()
                        row=row|{'end':end}
                except ValueError:
                    pass
            candidate={'title':row['title'],'start':row['start'],'end':row['end'],'date_note':None}
            try:
                valid=same(row,canonical(candidate))
            except (TypeError,ValueError):
                valid=False
            if not valid: raise ValueError('calendar_search_invalid')
            normalized.append(row)
        return normalized
    def create(self,event):
        expected=canonical(event)
        today=date.today() if expected['all_day'] else datetime.now(datetime.fromisoformat(expected['start']).tzinfo)
        start=date.fromisoformat(expected['start']) if expected['all_day'] else datetime.fromisoformat(expected['start'])
        if start<today: raise ValueError('calendar_past_event')
        actual=self.call({'operation':'create','event':expected})
        if not same(actual,expected): raise ValueError('calendar_verification_failed')
        return actual
