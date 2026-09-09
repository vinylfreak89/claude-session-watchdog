#!/usr/bin/python3
"""Per-session: model completedTurns from promptId-keyed turns.
c0 = distinct promptIds; drop turns started by meta/compact/null (slash commands); then classify each turn:
 interrupted (contains a '[Request interrupted' marker), api_error (assistant isApiErrorMessage inside it),
 last_turn_open (no end_turn text after last tool activity)."""
import json,os,glob,re,collections,datetime
HOME=os.path.expanduser('~')
STATE=os.path.join(HOME,'Library/Application Support/Claude/claude-code-sessions')
PROJ=os.path.join(HOME,'.claude/projects')
def slug(c): return re.sub(r'[^A-Za-z0-9]','-',c)
def text_of(c):
    if isinstance(c,str): return c
    if isinstance(c,list): return '\n'.join(b.get('text','') for b in c if isinstance(b,dict) and b.get('type')=='text')
    return ''
def has_block(c,t): return isinstance(c,list) and any(isinstance(b,dict) and b.get('type')==t for b in c)
print(f"{'sess':8} {'ct':>4} {'c0':>4} {'-cmd':>4} {'intr':>4} {'err':>3} {'c1=c0-cmd':>9} {'c2=c1-intr':>10} {'c3=c2-err':>9} {'lastkind':14} {'lastopen':8} title")
for f in sorted(glob.glob(os.path.join(STATE,'*','*','local_*.json')),key=os.path.getmtime):
    d=json.load(open(f)); cli=d.get('cliSessionId') or ''
    t=os.path.join(PROJ,slug(d.get('cwd','')),cli+'.jsonl')
    if not os.path.exists(t): continue
    turns=collections.OrderedDict()  # pid -> dict(kind, interrupted, err, last_event)
    for line in open(t,errors='replace'):
        try: o=json.loads(line)
        except Exception: continue
        ty=o.get('type'); pid=o.get('promptId')
        if ty=='assistant':
            if pid is None: pid=list(turns.keys())[-1] if turns else None
            if pid is None: continue
            tr=turns.setdefault(pid,dict(kind='?',intr=0,err=0,last='',open=True))
            m=o.get('message') or {}
            if o.get('isApiErrorMessage'): tr['err']+=1
            c=m.get('content')
            if has_block(c,'tool_use'): tr['last']='tool_use'; tr['open']=True
            elif m.get('stop_reason')=='end_turn' or (text_of(c) and not has_block(c,'tool_use')):
                if m.get('stop_reason') in ('end_turn','stop_sequence',None): tr['last']='end_turn'; tr['open']=False
        elif ty=='user':
            m=o.get('message') or {}; c=m.get('content')
            ok=(o.get('origin') or {}).get('kind') or 'null'
            kind=ok
            if has_block(c,'tool_result'): kind='tool_result'
            elif o.get('isCompactSummary'): kind='compact'
            elif o.get('isMeta'): kind='meta'
            txt=text_of(c)
            if pid not in turns:
                turns[pid]=dict(kind=kind,intr=0,err=0,last='',open=True)
            tr=turns[pid]
            if '[Request interrupted by user' in txt: tr['intr']+=1
            if kind=='tool_result': tr['last']='tool_result'; tr['open']=True
    c0=len(turns)
    cmd=sum(1 for v in turns.values() if v['kind'] in ('meta','compact','null'))
    intr=sum(1 for v in turns.values() if v['intr']>0 and v['kind'] not in ('meta','compact','null'))
    err=sum(1 for v in turns.values() if v['err']>0 and v['intr']==0 and v['kind'] not in ('meta','compact','null'))
    lastv=list(turns.values())[-1] if turns else {}
    c1=c0-cmd; c2=c1-intr; c3=c2-err
    ct=d.get('completedTurns')
    print(f"{cli[:8]:8} {str(ct):>4} {c0:>4} {cmd:>4} {intr:>4} {err:>3} {c1:>9} {c2:>10} {c3:>9} {lastv.get('kind','')[:14]:14} {str(lastv.get('open')):8} {d.get('title')[:38]}")
