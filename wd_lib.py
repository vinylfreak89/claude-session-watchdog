#!/usr/bin/python3
"""wd_lib.py -- watchdog library. READ-ONLY against everything it inspects.

Sources of truth (all verified on this machine, 2026-09-09):
  session state : ~/Library/Application Support/Claude/claude-code-sessions/<acct>/<ws>/local_<uuid>.json
                  (globbed; fields completedTurns, contextExceededCount, lastActivityAt, cliSessionId, title, cwd)
  transcript    : ~/.claude/projects/<slug(cwd)>/<cliSessionId>.jsonl  (append-only JSONL)
  tasks         : /private/tmp/claude-<uid>/<slug(cwd)>/<cliSessionId>/tasks/<taskid>.output
  codex rollout : ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<threadid>.jsonl
Turn model: a user record that is not a tool_result and whose promptId differs from the previous
user record's promptId opens a turn; tool_results, task notifications injected mid-turn, slash-command
records and interrupt markers inherit the open turn's promptId. Assistant records carry no promptId.
"""
import os, re, json, glob, time, subprocess, hashlib, datetime, collections

HOME = os.path.expanduser('~')
STATE_GLOB = os.path.join(HOME, 'Library', 'Application Support', 'Claude', 'claude-code-sessions', '*', '*', 'local_*.json')
PROJECTS = os.path.join(HOME, '.claude', 'projects')
CODEX_SESSIONS = os.path.join(HOME, '.codex', 'sessions')
SCRATCH_ROOT = '/private/tmp/claude-%d' % os.getuid()
WATCHDOG_TAG = '[watchdog]'

# ----------------------------------------------------------------------------- small utils
def slug(path):
    return re.sub(r'[^A-Za-z0-9]', '-', path or '')

def iso_ms(ms):
    if not ms: return None
    return datetime.datetime.utcfromtimestamp(ms / 1000.0).strftime('%Y-%m-%dT%H:%M:%SZ')

def now_iso():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

def epoch_from_iso(s):
    if not s: return None
    s = s.rstrip('Z')
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
        try:
            return (datetime.datetime.strptime(s, fmt) - datetime.datetime(1970, 1, 1)).total_seconds()
        except ValueError:
            pass
    return None

def iso_from_epoch(e):
    return datetime.datetime.utcfromtimestamp(e).strftime('%Y-%m-%dT%H:%M:%SZ')

def short(s, n=160):
    s = re.sub(r'\s+', ' ', s or '').strip()
    return s if len(s) <= n else s[:n - 1] + '…'

def h(s):
    return hashlib.sha1(s.encode('utf-8', 'replace')).hexdigest()[:10]

def read_json_retry(path, tries=5):
    last = None
    for _ in range(tries):
        try:
            with open(path, 'rb') as f:
                return json.loads(f.read().decode('utf-8', 'replace'))
        except (ValueError, OSError) as e:      # partial write in progress
            last = e; time.sleep(0.05)
    return None

def run(cmd, timeout=30, cwd=None):
    """Run a read-only command; return (rc, stdout, stderr). Never raises."""
    try:
        p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode, p.stdout.decode('utf-8', 'replace'), p.stderr.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout after %ss' % timeout
    except OSError as e:
        return 127, '', str(e)

def mtime_iso(path):
    try:
        return iso_from_epoch(os.path.getmtime(path))
    except OSError:
        return None

def is_dataless(path):
    """iCloud File Provider placeholder check: `ls -lO` shows a 'dataless' flag."""
    rc, out, _ = run(['ls', '-lO', path])
    return rc == 0 and 'dataless' in out

# ----------------------------------------------------------------------------- sessions
def list_sessions():
    out = []
    for f in glob.glob(STATE_GLOB):
        d = read_json_retry(f)
        if not d: continue
        out.append(dict(state_path=f, sessionId=d.get('sessionId'), cli=d.get('cliSessionId'), title=d.get('title'),
                        cwd=d.get('cwd'), ct=d.get('completedTurns'), cec=d.get('contextExceededCount'),
                        lastActivityAt=d.get('lastActivityAt'), createdAt=d.get('createdAt'),
                        isArchived=d.get('isArchived'), permissionMode=d.get('permissionMode')))
    out.sort(key=lambda s: s['lastActivityAt'] or 0, reverse=True)
    return out

def find_session(selector):
    ss = list_sessions()
    sel = selector.strip()
    exact = [s for s in ss if sel in (s['sessionId'], s['cli'])]
    if len(exact) == 1: return exact[0]
    pref = [s for s in ss if (s['cli'] or '').startswith(sel) or (s['sessionId'] or '').startswith(sel)
            or (s['sessionId'] or '').startswith('local_' + sel)]
    if len(pref) == 1: return pref[0]
    sub = [s for s in ss if sel.lower() in (s['title'] or '').lower()]
    if len(sub) == 1: return sub[0]
    if len(sub) > 1:
        raise SystemExit('ambiguous selector %r: %s' % (sel, [(s['sessionId'], s['title']) for s in sub]))
    raise SystemExit('no session matches %r' % sel)

def read_state(sess):
    d = read_json_retry(sess['state_path']) or {}
    return dict(ct=d.get('completedTurns'), cec=d.get('contextExceededCount'), lastActivityAt=d.get('lastActivityAt'),
                title=d.get('title'), permissionMode=d.get('permissionMode'))

def transcript_path(sess):
    p = os.path.join(PROJECTS, slug(sess['cwd']), (sess['cli'] or '') + '.jsonl')
    if os.path.exists(p): return p
    g = glob.glob(os.path.join(PROJECTS, '*', (sess['cli'] or '') + '.jsonl'))
    return g[0] if g else p

def tasks_dir(sess):
    return os.path.join(SCRATCH_ROOT, slug(sess['cwd']), sess['cli'] or '', 'tasks')

def session_pids(sess):
    """pids of `claude` CLI processes belonging to this session (matched by --resume=<cli>)."""
    rc, out, _ = run(['ps', '-axo', 'pid=,command='])
    pids = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and '--resume=' + (sess['cli'] or '\0') in parts[1] and 'MacOS/claude' in parts[1]:
            pids.append(int(parts[0]))
    return pids

def process_table():
    rc, out, _ = run(['ps', '-axo', 'pid=,ppid=,etime=,command='])
    procs = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 3: continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        procs[pid] = dict(pid=pid, ppid=ppid, etime=parts[2], command=parts[3] if len(parts) > 3 else '')
    return procs

def descendants(procs, root_pids):
    kids = collections.defaultdict(list)
    for p in procs.values():
        kids[p['ppid']].append(p['pid'])
    out, stack = [], list(root_pids)
    seen = set()
    while stack:
        p = stack.pop()
        for c in kids.get(p, []):
            if c in seen: continue
            seen.add(c); out.append(procs[c]); stack.append(c)
    return out

# ----------------------------------------------------------------------------- transcript
def _text_of(content):
    if isinstance(content, str): return content
    if isinstance(content, list):
        return '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
    return ''

def _blocks(content, t):
    return [b for b in (content if isinstance(content, list) else []) if isinstance(b, dict) and b.get('type') == t]

def _result_text(block):
    c = block.get('content')
    if isinstance(c, str): return c
    if isinstance(c, list):
        return '\n'.join(x.get('text', '') for x in c if isinstance(x, dict) and x.get('type') == 'text')
    return ''

def parse_lines(data, base_offset):
    recs, off = [], base_offset
    for line in data.split(b'\n'):
        ln = len(line) + 1
        if line.strip():
            try:
                o = json.loads(line.decode('utf-8', 'replace'))
                o['_offset'] = off
                recs.append(o)
            except ValueError:
                pass
        off += ln
    return recs

def is_opener(rec, prev_pid):
    if rec.get('type') != 'user': return False
    if _blocks((rec.get('message') or {}).get('content'), 'tool_result'): return False
    return rec.get('promptId') != prev_pid

def read_tail_turns(path, need_turns=3, start_bytes=2 * 1024 * 1024, max_bytes=1024 * 1024 * 1024):
    """Return (chunk_offset, records) such that the last `need_turns` turns are complete in `records`."""
    size = os.path.getsize(path)
    n = min(start_bytes, size)
    while True:
        off = size - n
        with open(path, 'rb') as f:
            f.seek(off); data = f.read(n)
        if off > 0:
            i = data.find(b'\n')
            data = data[i + 1:]; off += i + 1
        recs = parse_lines(data, off)
        openers, prev = 0, None
        for r in recs:
            if r.get('type') != 'user': continue
            if is_opener(r, prev): openers += 1
            prev = r.get('promptId')
        if off == 0 or n >= size or n >= max_bytes or openers >= need_turns + 1:
            return off, recs
        n = min(n * 2, size)

def read_records_from(path, offset):
    with open(path, 'rb') as f:
        f.seek(offset); data = f.read()
    return parse_lines(data, offset)

MARKER = '[Request interrupted by user'

class Turn(object):
    def __init__(self, opener):
        self.opener = opener
        self.pid = opener.get('promptId')
        self.offset = opener.get('_offset')
        self.start_ts = opener.get('timestamp')
        self.end_ts = opener.get('timestamp')
        self.opener_kind = (opener.get('origin') or {}).get('kind') or ('compact' if opener.get('isCompactSummary') else ('meta' if opener.get('isMeta') else 'unknown'))
        self.opener_text = _text_of((opener.get('message') or {}).get('content'))
        self.records = [opener]
        self.tool_uses = []          # dict(id,name,input,ts)
        self.tool_results = {}       # id -> dict(text,is_error,ts)
        self.notifications = []      # dict(task_id,status,summary,ts)
        self.assistant_texts = []    # (ts,text)
        self.markers = []            # ts
        self.api_errors = []         # (ts,text)
        self.human_messages = []     # (ts,text) incl. opener if human
        self.last_stop = None
        self.last_kind = 'user'      # last user/assistant record kind
        self.last_assistant_had_tool_use = False
        if self.opener_kind == 'human': self.human_messages.append((self.start_ts, self.opener_text))
        if self.opener_kind == 'task-notification': self._note(opener)

    def _note(self, rec):
        t = _text_of((rec.get('message') or {}).get('content'))
        m = dict(task_id=_grab(r'<task-id>([^<]+)</task-id>', t), status=_grab(r'<status>([^<]+)</status>', t),
                 summary=short(_grab(r'<summary>(.*?)</summary>', t) or '', 140), ts=rec.get('timestamp'),
                 output_file=_grab(r'<output-file>([^<]+)</output-file>', t))
        self.notifications.append(m)

    def add(self, rec):
        self.records.append(rec)
        ty = rec.get('type')
        if rec.get('timestamp'): self.end_ts = rec['timestamp']
        if ty == 'assistant':
            m = rec.get('message') or {}
            c = m.get('content')
            if rec.get('isApiErrorMessage'):
                self.api_errors.append((rec.get('timestamp'), short(_text_of(c), 120)))
            for b in _blocks(c, 'tool_use'):
                self.tool_uses.append(dict(id=b.get('id'), name=b.get('name'), input=b.get('input') or {}, ts=rec.get('timestamp')))
            txt = _text_of(c)
            if txt.strip(): self.assistant_texts.append((rec.get('timestamp'), txt))
            self.last_stop = m.get('stop_reason')
            self.last_assistant_had_tool_use = bool(_blocks(c, 'tool_use'))
            self.last_kind = 'assistant'
        elif ty == 'user':
            m = rec.get('message') or {}
            c = m.get('content')
            trs = _blocks(c, 'tool_result')
            if trs:
                for b in trs:
                    self.tool_results[b.get('tool_use_id')] = dict(text=_result_text(b), is_error=bool(b.get('is_error')), ts=rec.get('timestamp'))
                self.last_kind = 'tool_result'
            else:
                kind = (rec.get('origin') or {}).get('kind')
                txt = _text_of(c)
                if MARKER in txt:
                    self.markers.append(rec.get('timestamp'))
                    self.last_kind = 'marker'
                elif kind == 'task-notification':
                    self._note(rec); self.last_kind = 'notification'
                elif kind == 'human':
                    self.human_messages.append((rec.get('timestamp'), txt)); self.last_kind = 'human'
                else:
                    self.last_kind = 'user-other'

    @property
    def end_state(self):
        if self.last_kind == 'marker': return 'interrupted'
        if self.api_errors and self.last_kind == 'assistant' and not self.assistant_texts_after_error(): return 'api_error'
        if self.last_kind == 'assistant' and not self.last_assistant_had_tool_use and self.last_stop in ('end_turn', 'stop_sequence', 'max_tokens', None):
            return 'end_turn'
        return 'open'

    def assistant_texts_after_error(self):
        if not self.api_errors: return False
        last_err = self.api_errors[-1][0]
        return any(ts and last_err and ts > last_err for ts, _ in self.assistant_texts)

    @property
    def final_text(self):
        return self.assistant_texts[-1][1] if self.assistant_texts else ''

    @property
    def is_watchdog_turn(self):
        return WATCHDOG_TAG in (self.opener_text or '')

    def background_launches(self):
        """Background Bash tasks launched in this turn: from tool results 'Command running in background with ID: X'."""
        out = []
        for tu in self.tool_uses:
            r = self.tool_results.get(tu['id'])
            if not r: continue
            m = re.search(r'Command running in background with ID:\s*(\S+)\.\s*Output is being written to:\s*(\S+)\.', r['text'])
            if m:
                out.append(dict(task_id=m.group(1), output_file=m.group(2), command=(tu['input'] or {}).get('command', ''),
                                description=(tu['input'] or {}).get('description', ''), ts=tu['ts'], tool_use_id=tu['id']))
        return out

    def agent_launches(self):
        out = []
        for tu in self.tool_uses:
            if tu['name'] == 'Agent':
                r = self.tool_results.get(tu['id'])
                out.append(dict(tool_use_id=tu['id'], description=(tu['input'] or {}).get('description', ''), ts=tu['ts'],
                                result=short(r['text'], 200) if r else None, background=bool((tu['input'] or {}).get('run_in_background', True))))
        return out

def _grab(pat, s, flags=re.S):
    m = re.search(pat, s or '', flags)
    return m.group(1).strip() if m else None

def split_turns(records):
    turns, prev_pid, cur = [], None, None
    for r in records:
        ty = r.get('type')
        if ty == 'user':
            if is_opener(r, prev_pid):
                cur = Turn(r); turns.append(cur)
            elif cur is not None:
                cur.add(r)
            prev_pid = r.get('promptId')
        elif cur is not None and ty in ('assistant',):
            cur.add(r)
        elif cur is not None:
            cur.records.append(r)
    return turns

def last_turns(sess, n=3):
    path = transcript_path(sess)
    off, recs = read_tail_turns(path, need_turns=n)
    turns = split_turns(recs)
    # the first turn in the chunk may be partial unless the chunk starts at 0
    if off > 0 and turns: turns = turns[1:]
    return path, turns[-n:] if len(turns) >= n else turns

# ----------------------------------------------------------------------------- tool-level facts
DISPATCH_RE = re.compile(r'codex-run\s+(task|send|say|queue|steer|on-file)\b([^\n;|&]*)')
THREAD_RE = re.compile(r'\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b')
HEREDOC_RE = re.compile(r"cat\s*>\s*(\S+)\s*<<\s*'?(\w+)'?\n(.*?)\n\2\b", re.S)
COMMIT_RESULT_RE = re.compile(r'^\[([\w./-]+)\s+(?:\(root-commit\)\s+)?([0-9a-f]{7,40})\]', re.M)

def dispatches_in(turn):
    """codex-run dispatch calls in a turn, with what the tool result / background output said."""
    out = []
    for tu in turn.tool_uses:
        if tu['name'] != 'Bash': continue
        cmd = (tu['input'] or {}).get('command', '') or ''
        for m in DISPATCH_RE.finditer(cmd):
            verb, rest = m.group(1), m.group(2)
            th = THREAD_RE.search(rest)
            brief = _grab(r'(/[^\s"\']+\.md)\b', rest)
            heredocs = {hp: body for hp, _, body in HEREDOC_RE.findall(cmd)}
            r = turn.tool_results.get(tu['id'])
            bg = None
            if r:
                mb = re.search(r'Command running in background with ID:\s*(\S+)\.\s*Output is being written to:\s*(\S+)\.', r['text'])
                if mb: bg = dict(task_id=mb.group(1), output_file=mb.group(2))
            out.append(dict(tool_use_id=tu['id'], ts=tu['ts'], verb=verb, thread=th.group(1) if th else None, brief_path=brief,
                            brief_text=heredocs.get(brief), inline=short(rest, 200), background=bg,
                            result_text=(r['text'] if r else None), result_is_error=(r['is_error'] if r else None), command=cmd))
    return out

def commits_in(turn):
    """Commits made by tool calls in this turn (git commit results '[branch sha] ...')."""
    out = []
    for tu in turn.tool_uses:
        if tu['name'] != 'Bash': continue
        cmd = (tu['input'] or {}).get('command', '') or ''
        if 'git commit' not in cmd and 'git -C' not in cmd: continue
        r = turn.tool_results.get(tu['id'])
        if not r: continue
        for br, sha in COMMIT_RESULT_RE.findall(r['text']):
            out.append(dict(branch=br, sha=sha, ts=r['ts'], tool_use_id=tu['id']))
    return out

def pushes_in(turn):
    out = []
    for tu in turn.tool_uses:
        if tu['name'] != 'Bash': continue
        cmd = (tu['input'] or {}).get('command', '') or ''
        if re.search(r'\bgit\b[^\n;|&]*\bpush\b', cmd):
            r = turn.tool_results.get(tu['id'])
            out.append(dict(ts=tu['ts'], command=short(cmd, 160), result=short(r['text'], 200) if r else None, is_error=(r['is_error'] if r else None)))
    return out

def files_written_in(turn):
    out = {}
    for tu in turn.tool_uses:
        inp = tu['input'] or {}
        if tu['name'] in ('Write', 'Edit', 'NotebookEdit') and inp.get('file_path'):
            out[inp['file_path']] = tu['ts']
        elif tu['name'] == 'Bash':
            cmd = inp.get('command', '') or ''
            for hp, _, _ in HEREDOC_RE.findall(cmd): out[hp] = tu['ts']
            for m in re.finditer(r'(?:>>?|\btee\b(?:\s+-a)?)\s*([^\s;&|>]+)', cmd):
                p = m.group(1).strip('"\'')
                if p.startswith('/') and not p.startswith('/dev/'): out.setdefault(p, tu['ts'])
    return out

# ----------------------------------------------------------------------------- prose claims
SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z\[`*\d])|\n+')
PATH_RE = re.compile(r'(?<![\w/])((?:/|~/)[\w.@+-]+(?:/[\w.@+-]+)*|[\w.-]+/[\w./-]+\.[A-Za-z0-9]{1,5}|[\w-]+\.(?:py|c|h|md|csv|tpc|mp4|mov|sh|json|txt|log|cap6))\b')
SHA_RE = re.compile(r'(?<![\w/])([0-9a-f]{7,40})(?![\w/])')
ROWID_RE = re.compile(r'\b([A-F]\d{1,2})\b')
ANNOUNCE_RE = re.compile(r"(?:^|[.;:]\s+|\*\*\s*)(?:(?:I'll|I will|I'm going to|I am going to|Let me|Now I(?:'ll| will)?|Next I(?:'ll| will)?|Then I(?:'ll| will)?)\s+(?:now\s+|then\s+|go\s+(?:and\s+)?|also\s+|just\s+)?(render|dispatch|run|re-?run|launch|build|commit|push|write|replay|measure|re-?measure|merge|fix|implement|send|score|cut|re-?cut|verify|check|watch|hook|burn|encode|generate|produce|start|kick|retry|re-?try|rebuild|re-?render|re-?dispatch|queue|steer)\w*|(Launching|Dispatching|Kicking off|Starting|Re-?running|Rendering|Running|Sending|Retrying|Queuing|Queueing)\b)", re.I)
ACTION_VERB_RE = re.compile(r'\b(render|dispatch|run|re-?run|launch|build|commit|push|write|replay|measure|re-?measure|merge|fix|implement|send|score|cut|re-?cut|verify|check|watch|hook|burn|encode|generate|produce|start|kick)\w*\b', re.I)
CONDITIONAL_RE = re.compile(r"\b(if|once|when|after you|unless|let me know|want me|should I|shall I|your call|you decide|await|waiting for)\b|\?\s*$", re.I)
DISPATCH_CLAIM_RE = re.compile(r'\b(dispatch(?:ed|ing)?|sent (?:it |that |this |the \w+ )?to codex|codex is (?:now )?(?:running|working|on it)|queued (?:to|for|on) codex|handed (?:it |this )?(?:off )?to codex|codex-run (?:task|send|say|queue|steer))\b', re.I)
PUSH_CLAIM_RE = re.compile(r'\b(pushed|push(?:ed)? to origin|committed and pushed|commit(?:ted)?/pushed)\b', re.I)
COMMIT_CLAIM_RE = re.compile(r'\b(committed|commit(?:ted)? (?:as|at|in)|landed (?:as|at|in)|is at|now at|HEAD)\b', re.I)
FILE_CLAIM_RE = re.compile(r'\b(wrote|written|saved|created|rendered|produced|generated|emitted|dumped|published|appended)\b', re.I)
LEDGER_CLAIM_RE = re.compile(r'\b(delet\w*|clos\w*|remov\w*|reconcil\w*|dropp\w*|struck|retir\w*)\b', re.I)
LEDGER_WORD_RE = re.compile(r'\b(row|rows|ledger|tracker|pending|v10_pending)\b', re.I)
NEG_RE = re.compile(r"\b(not|never|no|didn't|did not|failed|cannot|can't|unable|haven't|hasn't|without)\b", re.I)

def sentences(text):
    text = re.sub(r'```.*?```', ' ', text or '', flags=re.S)
    return [s.strip() for s in SENT_SPLIT.split(text) if s and s.strip()]

def extract_claims(turn):
    """Checkable assertions from the turn's own prose (final text weighted; all assistant texts scanned)."""
    claims = []
    seen = set()
    for idx, (ts, text) in enumerate(turn.assistant_texts):
        is_final = idx == len(turn.assistant_texts) - 1
        for s in sentences(text):
            key = h(s)
            if key in seen: continue
            seen.add(key)
            s_neg = bool(NEG_RE.search(s))
            shas = [x for x in SHA_RE.findall(s) if not x.isdigit()]
            paths = [p for p in PATH_RE.findall(s) if '/' in p or '.' in p]
            rows = ROWID_RE.findall(s)
            if PUSH_CLAIM_RE.search(s) and not s_neg:
                claims.append(dict(kind='push', sentence=s, shas=shas, ts=ts, final=is_final))
            elif shas and (COMMIT_CLAIM_RE.search(s) or 'commit' in s.lower()) and not s_neg:
                claims.append(dict(kind='commit', sentence=s, shas=shas, ts=ts, final=is_final))
            if DISPATCH_CLAIM_RE.search(s) and not s_neg:
                claims.append(dict(kind='dispatch', sentence=s, ts=ts, final=is_final, threads=THREAD_RE.findall(s), paths=paths))
            fm = FILE_CLAIM_RE.search(s)
            if fm and paths and not s_neg:
                after = [p for p in paths if s.find(p) > fm.start()]
                if after:
                    claims.append(dict(kind='file', sentence=s, paths=after, verb=fm.group(1).lower(), ts=ts, final=is_final))
            if LEDGER_CLAIM_RE.search(s) and (LEDGER_WORD_RE.search(s) or rows) and not s_neg:
                claims.append(dict(kind='ledger', sentence=s, rows=rows, ts=ts, final=is_final))
            am = ANNOUNCE_RE.search(s) if is_final else None
            if am:
                claims.append(dict(kind='announce', sentence=s, paths=paths, ts=ts, final=True, verb=(am.group(1) or am.group(2) or '').lower(),
                                   conditional=bool(CONDITIONAL_RE.search(s)), threads=THREAD_RE.findall(s)))
    return claims

# ----------------------------------------------------------------------------- world: git
def git(repo, *args, timeout=30):
    return run(['git', '-C', repo] + list(args), timeout=timeout)

def git_sha_info(repo, sha):
    rc, out, err = git(repo, 'cat-file', '-t', sha)
    if rc != 0 or out.strip() != 'commit':
        return dict(sha=sha, exists=False)
    _, log, _ = git(repo, 'log', '-1', '--format=%H%x09%ci%x09%s', sha)
    full, date, subj = (log.strip().split('\t') + ['', ''])[:3]
    _, lb, _ = git(repo, 'branch', '--format=%(refname:short)', '--contains', sha)
    _, rb, _ = git(repo, 'branch', '-r', '--format=%(refname:short)', '--contains', sha)
    return dict(sha=sha, exists=True, full=full, date=date, subject=subj,
                local_branches=[x for x in lb.split() if x], remote_branches=[x for x in rb.split() if x])

def git_branch_drift(repo):
    """Local HEAD vs origin, via ls-remote (no fetch: read-only). Falls back to the last-fetched remote ref."""
    _, br, _ = git(repo, 'symbolic-ref', '--short', 'HEAD')
    br = br.strip() or 'HEAD'
    _, head, _ = git(repo, 'rev-parse', 'HEAD'); head = head.strip()
    rc, lr, err = git(repo, 'ls-remote', 'origin', 'refs/heads/' + br, timeout=25)
    remote_sha, source = None, None
    if rc == 0 and lr.strip():
        remote_sha, source = lr.split()[0], 'ls-remote@' + now_iso()
    else:
        rc2, rs, _ = git(repo, 'rev-parse', 'origin/' + br)
        if rc2 == 0:
            remote_sha = rs.strip()
            _, rl, _ = git(repo, 'reflog', 'show', '--date=iso', '-n1', 'refs/remotes/origin/' + br)
            source = 'origin/%s as last fetched (%s)' % (br, short(rl, 80))
    ahead = behind = None
    if remote_sha:
        rc3, cnt, _ = git(repo, 'rev-list', '--left-right', '--count', remote_sha + '...' + head)
        if rc3 == 0:
            b, a = cnt.split()
            behind, ahead = int(b), int(a)
        else:
            source = (source or '') + ' (remote sha not in local history)'
    ahead_shas = []
    if ahead:
        _, sh, _ = git(repo, 'log', '--format=%h %s', remote_sha + '..' + head)
        ahead_shas = [short(x, 70) for x in sh.splitlines()]
    _, st, _ = git(repo, 'status', '--porcelain')
    return dict(branch=br, head=head[:10], remote_sha=(remote_sha or '')[:10], source=source, ahead=ahead, behind=behind,
                ahead_shas=ahead_shas, dirty=[short(x, 80) for x in st.splitlines()][:12], ls_remote_error=(None if rc == 0 else short(err, 100)))

def git_log_paths_since(repo, since_iso, paths, ref='HEAD'):
    if not paths: return []
    rc, out, _ = git(repo, 'log', ref, '--since=' + since_iso, '--format=%h %ci %s', '--', *paths)
    return [short(x, 90) for x in out.splitlines()] if rc == 0 else []

def git_log_grep_since(repo, since_iso, pattern, ref='--all'):
    rc, out, _ = git(repo, 'log', ref, '--since=' + since_iso, '-i', '--grep=' + pattern, '--format=%h %ci %s')
    return [short(x, 90) for x in out.splitlines()] if rc == 0 else []

# ----------------------------------------------------------------------------- world: ledger
ROW_RE = re.compile(r'^\|\s*([A-F]\d{1,2})\s*\|(.*)\|\s*$')
TICK_RE = re.compile(r'`([^`]+)`')

def parse_ledger(path):
    if not os.path.exists(path): return dict(exists=False)
    if is_dataless(path): return dict(exists=True, dataless=True)
    text = open(path, 'rb').read().decode('utf-8', 'replace')
    rows, order = {}, []
    section = None
    sections = collections.OrderedDict()
    for line in text.splitlines():
        if line.startswith('## '):
            section = line[3:].strip(); sections[section] = []
        if section is not None: sections[section].append(line)
        m = ROW_RE.match(line)
        if m:
            rid = m.group(1)
            cells = [c.strip() for c in m.group(2).split('|')]
            state = cells[-1] if cells else ''
            rows[rid] = dict(id=rid, text=line.strip(), hash=h(line.strip()), state=state, item=cells[0] if cells else '',
                             tokens=[t for t in TICK_RE.findall(line) if '/' in t or '.' in t], section=section)
            order.append(rid)
    recon = _grab(r'Last reconciled ([^.\n]+)', text)
    return dict(exists=True, dataless=False, rows=rows, order=order, mtime=mtime_iso(path), reconciled=recon,
                sections={k: h('\n'.join(v)) for k, v in sections.items()}, size=len(text))

# ----------------------------------------------------------------------------- world: tasks & codex
EXIT_RE = re.compile(r'\[exited with code (\d+)\]')

def task_output_status(output_file):
    if not output_file or not os.path.exists(output_file):
        return dict(exists=False)
    try:
        st = os.stat(output_file)
        with open(output_file, 'rb') as f:
            f.seek(max(0, st.st_size - 4096)); tail = f.read().decode('utf-8', 'replace')
    except OSError as e:
        return dict(exists=True, error=str(e))
    m = EXIT_RE.findall(tail)
    return dict(exists=True, size=st.st_size, mtime=iso_from_epoch(st.st_mtime), exit_code=(int(m[-1]) if m else None),
                tail=short(tail[-600:], 300), is_symlink=os.path.islink(output_file))

def codex_thread_state(thread_id, tail_bytes=1024 * 1024):
    files = glob.glob(os.path.join(CODEX_SESSIONS, '*', '*', '*', 'rollout-*-%s.jsonl' % thread_id))
    if not files: return dict(found=False, thread=thread_id)
    f = max(files, key=os.path.getmtime)
    st = os.stat(f)
    with open(f, 'rb') as fh:
        fh.seek(max(0, st.st_size - tail_bytes)); data = fh.read()
    started, complete, last_msg, last_evt = None, None, None, None
    for line in data.split(b'\n')[1:]:
        if not line.strip(): continue
        try: o = json.loads(line.decode('utf-8', 'replace'))
        except ValueError: continue
        p = o.get('payload') if isinstance(o.get('payload'), dict) else {}
        pt = p.get('type')
        if o.get('timestamp'): last_evt = o['timestamp']
        if pt == 'task_started': started = o.get('timestamp')
        elif pt == 'task_complete':
            complete = o.get('timestamp'); last_msg = short(p.get('last_agent_message') or '', 200)
    in_flight = bool(started) and (not complete or complete < started)
    return dict(found=True, thread=thread_id, rollout=f, mtime=iso_from_epoch(st.st_mtime), size=st.st_size,
                last_started=started, last_complete=complete, in_flight=in_flight, last_agent_message=last_msg, last_event=last_evt)

def watchdog_self_session():
    """The session running this code, if any (env var set by the wake wrapper), else None."""
    return os.environ.get('WD_SELF_CLI')
