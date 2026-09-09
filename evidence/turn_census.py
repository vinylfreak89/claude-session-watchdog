#!/usr/bin/python3
"""Census: for every desktop session state file, count turn-shaped records in its transcript
and compare with completedTurns. Resolves brief unknown 1 (interrupted/errored turns) and looks
for prior cross-session messages (unknown 3)."""
import json, os, glob, re, collections, datetime
HOME=os.path.expanduser('~')
STATE=os.path.join(HOME,'Library/Application Support/Claude/claude-code-sessions')
PROJ=os.path.join(HOME,'.claude/projects')
def slug(c): return re.sub(r'[^A-Za-z0-9]','-',c)
def find_transcript(cwd,cli):
    c=[os.path.join(PROJ,slug(cwd),cli+'.jsonl')]+glob.glob(os.path.join(PROJ,'*',cli+'.jsonl'))
    for p in c:
        if os.path.exists(p): return p
    return None
def iso(ms): return '-' if not ms else datetime.datetime.utcfromtimestamp(ms/1000).strftime('%m-%dT%H:%M:%SZ')
def text_of(c):
    if isinstance(c,str): return c
    if isinstance(c,list): return '\n'.join(b.get('text','') for b in c if isinstance(b,dict) and b.get('type')=='text')
    return ''
def has_block(c,t): return isinstance(c,list) and any(isinstance(b,dict) and b.get('type')==t for b in c)
for f in sorted(glob.glob(os.path.join(STATE,'*','*','local_*.json')),key=os.path.getmtime):
    d=json.load(open(f)); cli=d.get('cliSessionId') or ''; t=find_transcript(d.get('cwd',''),cli)
    r=dict(file=os.path.basename(f)[6:14],title=d.get('title'),ct=d.get('completedTurns'),cec=d.get('contextExceededCount'),last=iso(d.get('lastActivityAt')),created=iso(d.get('createdAt')))
    if not t: r['transcript']=None; print(json.dumps(r,ensure_ascii=False)); continue
    human=0; markers=0; api_err=0; compact=0; meta=0
    origins=collections.Counter(); stop_before_human=collections.Counter(); pend_tool=0
    nonhuman=[]; froms=[]; errs=[]; markers_s=[]
    last_stop=None; pending_tool_use=False; last_ts=None; first_human_ts=None; prev_was_marker=False
    ended_after_marker=0
    for line in open(t,'r',errors='replace'):
        line=line.strip()
        if not line: continue
        try: o=json.loads(line)
        except Exception: continue
        ty=o.get('type')
        if ty=='assistant':
            m=o.get('message',{}) or {}
            if o.get('isApiErrorMessage'):
                api_err+=1
                if len(errs)<6: errs.append((o.get('timestamp'),text_of(m.get('content'))[:90]))
            if has_block(m.get('content'),'tool_use'): pending_tool_use=True
            if m.get('stop_reason'): last_stop=m.get('stop_reason')
            last_ts=o.get('timestamp')
        elif ty=='user':
            m=o.get('message',{}) or {}; c=m.get('content')
            if o.get('isCompactSummary'): compact+=1
            if has_block(c,'tool_result'):
                pending_tool_use=False; last_ts=o.get('timestamp'); continue
            ok=(o.get('origin') or {}).get('kind')
            origins[ok]+=1
            txt=text_of(c)
            if o.get('isMeta'): meta+=1; continue
            if '[Request interrupted by user' in txt:
                markers+=1; prev_was_marker=True
                if len(markers_s)<3: markers_s.append((o.get('timestamp'),txt[:60]))
                last_ts=o.get('timestamp'); continue
            if ok in ('human',None):
                human+=1
                if first_human_ts is None: first_human_ts=o.get('timestamp')
                stop_before_human[last_stop]+=1
                if pending_tool_use: pend_tool+=1
                if prev_was_marker: ended_after_marker+=1
            else:
                if len(nonhuman)<6: nonhuman.append((ok,o.get('timestamp'),txt[:120]))
            if re.match(r'^\s*(\[?From\b|From ")',txt) or 'From "' in txt[:80]:
                if len(froms)<6: froms.append((ok,o.get('timestamp'),txt[:140]))
            pending_tool_use=False; prev_was_marker=False; last_ts=o.get('timestamp')
    r.update(transcript=os.path.relpath(t,PROJ),size_mb=round(os.path.getsize(t)/1e6,1),human=human,markers=markers,api_err=api_err,compact=compact,meta=meta,
             origins=dict(origins),stop_before_human=dict(stop_before_human),pending_tool_at_human=pend_tool,after_marker=ended_after_marker,
             first_human=first_human_ts,last_ts=last_ts,nonhuman=nonhuman,froms=froms,errs=errs,markers_s=markers_s)
    print(json.dumps(r,ensure_ascii=False),flush=True)
