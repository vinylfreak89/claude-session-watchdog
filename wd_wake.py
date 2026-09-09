#!/usr/bin/python3
"""wd_wake.py -- one wake of the watchdog.

Reads the target's latest completed turn (tool calls first, prose second), verifies every assertion that has a
checkable referent against the world (git, files, background tasks, Codex rollouts, the ledger), diffs the
ledger against the previous wake's snapshot, checks work believed in flight, and prints the findings with the
EXACT message text to send.  READ-ONLY on the target session, the repo and every project file; it writes only
under --state-dir (state.json, findings.md, wake.log).

  wd_wake.py --target SEL [--trigger 'TURN ct=1->2'] [--repo DIR] [--ledger REL] [--quiet-min 10] [--stale-turns 5]
  wd_wake.py --target SEL --bootstrap            # initialise state from the current turn, no findings
  wd_wake.py --target SEL --replay K --no-state  # analyse turn[-K] as if it had just ended (testing only)
  wd_wake.py --sent F12,F13 [--message-id ID]    # record that these findings were sent (dedupe memory)
  wd_wake.py --veto F14 --reason "..."           # record a veto (the watchdog session may only veto, never add)

Message form (fixed):  [watchdog] turn N (<end ts>) said "<quote>" | checked: <what> | result: <what>
"""
import os, sys, re, json, time, argparse, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wd_lib as W

FAIL_SIG = re.compile(r'(\bfailed\b|Traceback|queue failed|\bexit=[1-9]\d*|\[exited with code [1-9]\d*\]|\bError\b|No such file|not found|refused|thread not found)', re.I)
NOT_PUSHED = re.compile(r"(not pushed|unpushed|haven't pushed|hasn't been pushed|without pushing|committed, not pushed|committed but not pushed|local only|not yet pushed|push(?:ed)? later)", re.I)
DEFAULT_STATE = dict(wake_count=0, finding_counter=0, seen_pids=[], ledger=None, dispatch_log=[], in_flight=[], raised={}, proposed={},
                     notified=[], last_ct=None, last_cec=None, last_wake_ts=None, bootstrap_ts=None)

# ----------------------------------------------------------------------------- state files
def load_state(d):
    p = os.path.join(d, 'state.json')
    s = W.read_json_retry(p) if os.path.exists(p) else None
    base = dict(DEFAULT_STATE); base.update(s or {}); return base

def save_state(d, s):
    tmp = os.path.join(d, 'state.json.tmp')
    with open(tmp, 'w') as f: json.dump(s, f, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(d, 'state.json'))

def append_findings_md(d, rows):
    p = os.path.join(d, 'findings.md')
    new = not os.path.exists(p)
    with open(p, 'a') as f:
        if new:
            f.write('# Watchdog findings (acceptance log)\n\nOwner marks each SENT row TRUE or FALSE in the `owner` column. Precision on the first 20 sent rows is the acceptance number.\n\n')
            f.write('| id | wake | wake_ts | target_ct | class | status | owner | message |\n|---|---|---|---|---|---|---|---|\n')
        for r in rows:
            f.write('| %s | %s | %s | %s | %s | %s |  | %s |\n' % (r['id'], r['wake'], r['wake_ts'], r['turn_ct'], r['cls'], r['status'], r['message'].replace('|', '\\|').replace('\n', ' ')))

def rewrite_status(d, fid, status, note=''):
    p = os.path.join(d, 'findings.md')
    if not os.path.exists(p): return False
    lines = open(p).read().split('\n'); hit = False
    for i, ln in enumerate(lines):
        if ln.startswith('| %s |' % fid):
            cells = ln.split(' | ')
            if len(cells) >= 8:
                cells[5] = status + ((' ' + note) if note else '')
                lines[i] = ' | '.join(cells); hit = True
    if hit: open(p, 'w').write('\n'.join(lines))
    return hit

def log_line(d, s):
    with open(os.path.join(d, 'wake.log'), 'a') as f: f.write(s + '\n')

# ----------------------------------------------------------------------------- helpers
PLAIN_CLASSES = ('task_dead', 'codex_turn_silent', 'reply_overdue', 'context_exceeded', 'ledger_stale_row', 'idle_no_blocker')

def msg(turn_label, end_ts, quote, checked, result, plain=False):
    """Fixed message form. Turn-derived findings quote the turn; state-derived ones (a dead job, a stale row,
    an overdue reply) state the fact instead of putting words in the turn's mouth."""
    if plain:
        return '%s %s | checked: %s | result: %s' % (W.WATCHDOG_TAG, W.short(quote, 200), W.short(checked, 260), W.short(result, 320))
    return '%s turn %s (%s) said "%s" | checked: %s | result: %s' % (W.WATCHDOG_TAG, turn_label, end_ts or '?', W.short(quote, 170).replace('"', "'"), W.short(checked, 260), W.short(result, 320))

def finding(cls, key, evidence, quote, checked, result, turn_label, end_ts, severity='normal'):
    return dict(cls=cls, key=key, evidence_hash=W.h(json.dumps(evidence, sort_keys=True, default=str)), evidence=evidence,
                quote=quote, checked=checked, result=result, severity=severity, message=msg(turn_label, end_ts, quote, checked, result, plain=cls in PLAIN_CLASSES))

def resolve_path(p, repo):
    p = os.path.expanduser(p)
    if not os.path.isabs(p): p = os.path.join(repo, p)
    return re.sub(r':\d+$', '', p)

def repo_files(repo):
    rc, out, _ = W.git(repo, 'ls-files')
    return out.split('\n') if rc == 0 else []

def token_paths(tokens, repo, files):
    out = []
    for t in tokens:
        t = re.sub(r':\d+$', '', t.strip())
        if not t: continue
        if os.path.exists(os.path.join(repo, t)): out.append(t); continue
        base = os.path.basename(t)
        hits = [f for f in files if f.endswith('/' + base) or f == base]
        out.extend(hits[:3])
    return sorted(set(out))

# ----------------------------------------------------------------------------- the wake
def analyse(a, sess, st, state, turns, trigger, replay=False, self_sess=None):
    repo = a.repo or sess['cwd']
    now = time.time()
    findings, observations = [], []
    ct = st['ct']
    latest = turns[-1] if turns else None
    completed = [t for t in turns if t.end_state != 'open'] if turns else []
    # a turn followed by a later turn is closed whatever its own last record says
    if len(turns) > 1:
        completed = turns[:-1] + ([turns[-1]] if turns[-1].end_state != 'open' else [])
    T = completed[-1] if completed else None
    turn_label = str(ct) if ct is not None else '?'
    open_turn = turns[-1] if turns and turns[-1].end_state == 'open' else None
    # owner activity (rule 5)
    last_human = None
    for t in turns:
        for ts, _ in t.human_messages:
            if ts and (last_human is None or ts > last_human): last_human = ts
    last_human_age = (now - W.epoch_from_iso(last_human)) / 60.0 if last_human else None
    owner_active = (last_human_age is not None and last_human_age <= a.quiet_min) or (open_turn is not None and open_turn.opener_kind == 'human')
    own_turn = bool(T and T.is_watchdog_turn)

    new_turns = [t for t in completed if t.pid not in set(state['seen_pids'])] if not replay else ([T] if T else [])
    if replay: new_turns = [T]
    files = repo_files(repo)
    digest = []
    if T:
        for tu in T.tool_uses:
            inp = tu['input'] or {}
            summ = inp.get('command') or inp.get('file_path') or inp.get('description') or inp.get('prompt') or json.dumps(inp)
            r = T.tool_results.get(tu['id'])
            digest.append('%s %s: %s -> %s' % ((tu['ts'] or '')[11:19], tu['name'], W.short(summ, 110), ('ERR ' if r and r['is_error'] else '') + (W.short(r['text'], 90) if r else 'NO RESULT')))
        for n in T.notifications:
            digest.append('%s notification: task %s %s %s' % ((n['ts'] or '')[11:19], n['task_id'], n['status'], n['summary']))
    claims = W.extract_claims(T) if T else []
    dispatches = [d for t in new_turns for d in W.dispatches_in(t)]
    launches = [b for t in new_turns for b in t.background_launches()]
    commits = [c for t in new_turns for c in W.commits_in(t)]
    pushes = [p for t in new_turns for p in W.pushes_in(t)]
    written = W.files_written_in(T) if T else {}
    final = T.final_text if T else ''
    said_not_pushed = bool(NOT_PUSHED.search(final))
    end_ts = T.end_ts if T else None

    # ---- git: commit / push claims and drift
    drift = None
    if T and (commits or pushes or any(c['kind'] in ('push', 'commit') for c in claims)):
        drift = W.git_branch_drift(repo)
    for c in [c for c in claims if c['kind'] in ('commit', 'push')]:
        for sha in c.get('shas', []):
            info = W.git_sha_info(repo, sha)
            if not info['exists']:
                findings.append(finding('commit_missing', 'commit_missing:%s' % sha, dict(sha=sha), c['sentence'],
                                        'git cat-file -t %s in %s' % (sha, repo), 'no commit object %s in the repo' % sha, turn_label, end_ts))
            elif c['kind'] == 'push' and not info['remote_branches'] and drift and drift.get('remote_sha'):
                findings.append(finding('push_not_on_remote', 'push_not_on_remote:%s' % sha, dict(sha=sha, remote=drift['remote_sha']), c['sentence'],
                                        'git branch -r --contains %s; ls-remote origin %s' % (sha, drift['branch']),
                                        '%s is on local %s only; origin/%s = %s (%s); local ahead %s' % (sha, info['local_branches'], drift['branch'], drift['remote_sha'], drift['source'], drift['ahead']), turn_label, end_ts))
    if drift and drift.get('ahead') and not said_not_pushed:
        push_claim = next((c for c in claims if c['kind'] == 'push'), None)
        quote = push_claim['sentence'] if push_claim else ('tool call: ' + (pushes[-1]['command'] if pushes else ('git commit -> %s' % ', '.join(c['sha'] for c in commits))))
        findings.append(finding('push_drift', 'push_drift:%s:%s' % (drift['branch'], drift['head']), dict(head=drift['head'], remote=drift['remote_sha'], ahead=drift['ahead']), quote,
                                'git ls-remote origin %s vs local HEAD (%s)' % (drift['branch'], drift['source']),
                                'local %s at %s is %d ahead of origin (%s): %s' % (drift['branch'], drift['head'], drift['ahead'], drift['remote_sha'], '; '.join(drift['ahead_shas'][:4])), turn_label, end_ts))
    if drift and drift.get('ls_remote_error'):
        observations.append('ls-remote failed (%s); drift measured against last-fetched origin/%s' % (drift['ls_remote_error'], drift['branch']))

    # ---- dispatches (tool facts) and dispatch claims (prose)
    procs = W.live_children(sess)
    disp_records = []
    for d in dispatches:
        rec = dict(ts=d['ts'], verb=d['verb'], thread=d['thread'], brief_path=d['brief_path'], turn_ct=ct, task_id=(d['background'] or {}).get('task_id'),
                   output_file=(d['background'] or {}).get('output_file'), inline=d['inline'])
        outcome, failed, tail = 'unknown', False, ''
        ts_ = {}
        if d['background']:
            ts_ = W.task_output_status(d['background']['output_file'])
            if ts_.get('exit_code') is not None:
                outcome = 'exited %s' % ts_['exit_code']; failed = ts_['exit_code'] != 0; tail = ts_.get('tail', '')
            elif ts_.get('exists'):
                outcome = 'running (output %s bytes, mtime %s)' % (ts_.get('size'), ts_.get('mtime')); tail = ts_.get('tail', '')
            else:
                outcome = 'no output file'
        elif d['result_text'] is not None:
            failed = bool(d['result_is_error']) or bool(FAIL_SIG.search(d['result_text'][-1500:]))
            outcome = ('error result' if d['result_is_error'] else 'result') + ': ' + W.short(d['result_text'][-300:], 160)
            tail = d['result_text'][-600:]
        else:
            outcome = 'no tool result'
        if not failed and tail and FAIL_SIG.search(tail): failed = True
        rec.update(outcome=outcome, failed=failed)
        thread_state = W.codex_thread_state(d['thread']) if d['thread'] else None
        rec['thread_state'] = {k: thread_state.get(k) for k in ('found', 'in_flight', 'last_started', 'last_complete', 'mtime')} if thread_state else None
        disp_records.append(rec)
        quote = 'tool call at %s: `codex-run %s %s`' % (d['ts'], d['verb'], W.short(d['inline'], 90))
        dc = next((c for c in claims if c['kind'] == 'dispatch'), None)
        if failed:
            findings.append(finding('dispatch_failed', 'dispatch_failed:%s' % (rec['task_id'] or d['tool_use_id']), dict(outcome=outcome, tail=W.short(tail, 200)),
                                    dc['sentence'] if dc else quote, 'codex-run output for that call (%s)' % (rec['output_file'] or 'tool result'),
                                    '%s; tail: %s' % (outcome, W.short(tail, 220)), turn_label, end_ts))
        elif thread_state and thread_state.get('found') and d['verb'] in ('task', 'send', 'queue') and rec['task_id']:
            age = now - (W.epoch_from_iso(d['ts']) or now)
            ls_ = thread_state.get('last_started')
            if age > 120 and (not ls_ or ls_ < d['ts']) and ts_.get('exit_code') is None:
                findings.append(finding('dispatch_no_turn', 'dispatch_no_turn:%s' % rec['task_id'], dict(last_started=ls_, dispatched=d['ts']),
                                        dc['sentence'] if dc else quote, 'Codex rollout for thread %s (%s)' % (d['thread'][:8], thread_state['rollout']),
                                        'no task_started after the dispatch at %s (last task_started %s, last task_complete %s); codex-run task %s still without an exit marker' % (d['ts'], ls_, thread_state.get('last_complete'), rec['task_id']), turn_label, end_ts))
    for c in [c for c in claims if c['kind'] == 'dispatch']:
        recent = [d for t in turns[-3:] for d in W.dispatches_in(t)]
        if not recent:
            findings.append(finding('dispatch_claim_no_call', 'dispatch_claim_no_call:%s' % W.h(c['sentence']), dict(sentence=c['sentence']), c['sentence'],
                                    'codex-run task/send/say/queue/steer tool calls in the last 3 turns', 'none', turn_label, end_ts))

    # ---- file claims: HINTS for the model (paths are regex-extracted and can be wrong, e.g. "25.0/25.3")
    for c in [c for c in claims if c['kind'] == 'file']:
        facts = []
        for p in c['paths'][:4]:
            rp = resolve_path(p, repo)
            if p in written or rp in written: facts.append('%s: written by a tool call this turn' % p); continue
            facts.append('%s: %s' % (p, ('exists, mtime %s' % W.mtime_iso(rp)) if os.path.exists(rp) else 'no such file'))
        observations.append('FILE HINT: "%s" -- %s. If this claims a file was produced and it was not, raise: wd.sh finding file_claim_missing "<sentence>" check file <path>' % (W.short(c['sentence'], 140), '; '.join(facts)))

    # ---- ledger
    ledger_path = os.path.join(repo, a.ledger) if a.ledger else None
    L = W.parse_ledger(ledger_path) if ledger_path else dict(exists=False)
    prev = state.get('ledger')
    ledger_summary = 'missing'
    if L.get('exists') and not L.get('dataless'):
        rows_now = L['rows']
        prev_rows = (prev or {}).get('rows', {})
        removed = [r for r in prev_rows if r not in rows_now]
        added = [r for r in rows_now if r not in prev_rows]
        changed = [r for r in rows_now if r in prev_rows and rows_now[r]['hash'] != prev_rows[r]['hash']]
        prev_sections = (prev or {}).get('sections', {}) or {}
        ledger_diff = []
        for r in added: ledger_diff.append('+ row %s: %s' % (r, rows_now[r]['text']))
        for r in removed: ledger_diff.append('- row %s: %s' % (r, prev_rows[r].get('text', '')))
        for r in changed: ledger_diff.append('~ row %s\n    was: %s\n    now: %s' % (r, prev_rows[r].get('text', ''), rows_now[r]['text']))
        for name, hh in L['sections'].items():
            if prev_sections.get(name) != hh and not name.startswith(('A.', 'B.')):
                body = L['section_text'].get(name, '')
                ledger_diff.append('%s section "%s":\n%s' % ('+' if name not in prev_sections else '~', name, '\n'.join('    ' + ln for ln in body.splitlines()[:80])))
        for name in prev_sections:
            if name not in L['sections']: ledger_diff.append('- section "%s" removed' % name)
        ledger_summary = '%d rows (mtime %s; +%d -%d ~%d since last wake; reconciled: %s)' % (len(rows_now), L['mtime'], len(added), len(removed), len(changed), W.short(L.get('reconciled') or '?', 40))
        since = (prev or {}).get('snapshot_ts') or state.get('bootstrap_ts') or W.iso_from_epoch(now - 86400)
        # ledger close claims are HINTS: whether a sentence claims a row closed is language, not measurement
        for c in [c for c in claims if c['kind'] == 'ledger' and c.get('rows')]:
            for rid in c['rows']:
                if rid in rows_now:
                    observations.append('LEDGER HINT: "%s" -- row %s is still present: %s. If that sentence claims the row closed, raise it: wd.sh finding ledger_close_not_applied "<sentence>" check row %s' % (
                        W.short(c['sentence'], 140), rid, W.short(rows_now[rid]['text'], 120), rid))
        for rid in removed:
            backing_paths = token_paths(prev_rows[rid].get('tokens', []), repo, files)
            mention = W.git_log_grep_since(repo, since, r'\b' + rid + r'\b')
            perm = W.git_log_paths_since(repo, since, a.perm_paths) if a.perm_paths else []
            touch = W.git_log_paths_since(repo, since, backing_paths) if backing_paths else []
            said = next((c['sentence'] for c in claims if c['kind'] == 'ledger' and rid in c.get('rows', [])), None)
            if not mention and not touch and not perm:
                findings.append(finding('ledger_close_unbacked', 'ledger_close_unbacked:%s:%s' % (rid, prev_rows[rid]['hash']), dict(row=prev_rows[rid]['hash'], since=since),
                                        said or ('row %s deleted from %s this turn (not mentioned in the turn text)' % (rid, a.ledger)),
                                        'git log --since=%s: --grep %s; -- %s; -- %s' % (since, rid, ' '.join(a.perm_paths) or '(no permanent paths configured)', ' '.join(backing_paths) or '(no paths in row)'),
                                        'no commit since %s mentions %s, touches the permanent files, or touches its paths; row text was: %s' % (since, rid, W.short(prev_rows[rid]['text'], 120)), turn_label, end_ts))
            else:
                observations.append('row %s removed; backing: grep=%s perm=%s paths=%s' % (rid, len(mention), len(perm), len(touch)))
        # snapshot bookkeeping (first_seen / last_changed by ct)
        snap_rows = {}
        for rid, r in rows_now.items():
            pr = prev_rows.get(rid)
            if pr and pr.get('hash') == r['hash']:
                snap_rows[rid] = dict(pr)
            else:
                snap_rows[rid] = dict(hash=r['hash'], text=r['text'], state=r['state'], tokens=r['tokens'], first_seen_ct=(pr or {}).get('first_seen_ct', ct), first_seen_ts=(pr or {}).get('first_seen_ts', W.now_iso()), last_changed_ct=ct, last_changed_ts=W.now_iso())
        # stale rows: unchanged for >= stale_turns turns while dispatches that mention the row went out
        dl = state.get('dispatch_log', []) + [dict(ts=r['ts'], ct=ct, verb=r['verb'], thread=r['thread'], brief_path=r['brief_path'], inline=r['inline']) for r in disp_records]
        def _ident(tok):
            """A row token usable as a mention indicator: an identifier or a path, never an English word.
            Path-like tokens keep their whole path (basenaming f*_rf_peak_line/position gave 'position',
            which matched ordinary prose)."""
            t = re.sub(r':\d+$', '', (tok or '').strip())
            if '/' not in t: t = os.path.basename(t)
            if len(t) < 6: return None
            if '_' in t or re.search(r'\.[A-Za-z0-9]{1,5}$', t) or '/' in t: return t
            return None
        tok_rows = collections.Counter()
        for r_ in snap_rows.values():
            for t_ in set(x for x in (_ident(t) for t in r_.get('tokens', [])) if x): tok_rows[t_] += 1
        for rid, r in snap_rows.items():
            lc = r.get('last_changed_ct')
            if lc is None or ct is None or ct - lc < a.stale_turns: continue
            later = [d for d in dl if (d.get('ct') or 0) > lc and d.get('verb') in ('task', 'send', 'say', 'queue')]
            mentions = []
            toks = [t_ for t_ in set(x for x in (_ident(t) for t in r.get('tokens', [])) if x) if tok_rows[t_] == 1]
            for d in later:
                text = ''
                if d.get('brief_path') and os.path.exists(d['brief_path']):
                    try: text = open(d['brief_path'], errors='replace').read()
                    except OSError: text = ''
                text += ' ' + (d.get('inline') or '')
                if re.search(r'\b%s\b' % rid, text) or any(t and t in text for t in toks):
                    mentions.append('%s %s %s' % (d['ts'], d['verb'], os.path.basename(d.get('brief_path') or '') or W.short(d.get('inline'), 40)))
            if not mentions: continue
            paths = token_paths(r.get('tokens', []), repo, files)
            since_ts = r.get('last_changed_ts') or since
            commits_head = W.git_log_paths_since(repo, since_ts, paths) if paths else []
            commits_eng = W.git_log_paths_since(repo, since_ts, paths, ref=a.other_ref) if paths and a.other_ref else []
            findings.append(finding('ledger_stale_row', 'ledger_stale_row:%s:%s' % (rid, r['hash']), dict(row=r['hash'], mentions=len(mentions), commits=len(commits_head) + len(commits_eng)),
                                    'row %s state: %s' % (rid, W.short(r['state'], 120)),
                                    'row unchanged since ct %s (%s); dispatches since that mention it: %s; git log --since=%s -- %s on HEAD and %s' % (lc, since_ts, '; '.join(mentions[:3]), since_ts, ' '.join(paths) or '(no paths)', a.other_ref or '-'),
                                    '%d turns, %d matching dispatch(es), commits touching its paths: HEAD %s, %s %s' % (ct - lc, len(mentions), commits_head[:3] or 'none', a.other_ref or '-', commits_eng[:3] or 'none'), turn_label, end_ts))
        new_ledger = dict(rows=snap_rows, order=L['order'], sections=L['sections'], snapshot_ts=W.now_iso(), snapshot_ct=ct, mtime=L['mtime'], reconciled=L.get('reconciled'))
    else:
        new_ledger = prev; ledger_diff = []
        ledger_summary = 'DATALESS placeholder (iCloud); not read' if L.get('dataless') else ('missing at %s' % ledger_path if ledger_path else 'not configured')
        if ledger_path: observations.append('ledger ' + ledger_summary)

    # ---- context exceeded
    if state.get('last_cec') is not None and st['cec'] is not None and st['cec'] > state['last_cec']:
        findings.append(finding('context_exceeded', 'context_exceeded:%s' % st['cec'], dict(cec=st['cec']), 'session state: contextExceededCount %s -> %s' % (state['last_cec'], st['cec']),
                                'the session state file %s' % os.path.basename(sess['state_path']), 'a prompt was rejected at the API edge (hard context failure, not a compaction)', turn_label, end_ts))

    # ---- in-flight work: background tasks, codex dispatches
    notified = set(state.get('notified', []))
    for t in turns:
        for n in t.notifications:
            if n.get('task_id'): notified.add(n['task_id'])
    inflight = [i for i in state.get('in_flight', [])]
    for b in launches:
        if not any(i.get('id') == b['task_id'] for i in inflight):
            inflight.append(dict(kind='bg', id=b['task_id'], output_file=b['output_file'], command=W.short(b['command'], 300), desc=b['description'], launched_ts=b['ts'], turn_ct=ct))
    for r in disp_records:
        if r['thread'] and r['verb'] in ('task', 'send', 'queue', 'say') and not r['failed'] and not (r['outcome'] or '').startswith('exited'):
            if not any(i.get('kind') == 'codex' and i.get('thread') == r['thread'] and i.get('launched_ts') == r['ts'] for i in inflight):
                inflight.append(dict(kind='codex', thread=r['thread'], task_id=r['task_id'], output_file=r['output_file'], launched_ts=r['ts'], turn_ct=ct, brief_path=r['brief_path']))
    still = []
    for i in inflight:
        if i['kind'] == 'bg':
            s_ = W.task_output_status(i.get('output_file'))
            done = s_.get('exit_code') is not None or i['id'] in notified
            if done: continue
            hits = W.proc_matches(i.get('command', ''), procs)
            age_min = (now - (W.epoch_from_iso(i['launched_ts']) or now)) / 60.0
            mt_age = (now - (W.epoch_from_iso(s_['mtime']) or now)) / 60.0 if s_.get('mtime') else None
            i['status'] = 'alive(%d proc)' % len(hits) if hits else ('no process; output mtime %s' % s_.get('mtime'))
            if not hits and age_min > a.dead_min and (mt_age is None or mt_age > a.dead_min):
                findings.append(finding('task_dead', 'task_dead:%s' % i['id'], dict(id=i['id'], mtime=s_.get('mtime')), 'turn %s launched background task %s: %s' % (i.get('turn_ct'), i['id'], W.short(i.get('desc') or i.get('command'), 90)),
                                        'output %s (exit marker, mtime), task-notifications since, live processes under the session pid(s) %s' % (i.get('output_file'), W.session_pids(sess)),
                                        'no [exited with code] marker, no notification, no live process; output last modified %s; launched %s (%.0f min ago)' % (s_.get('mtime'), i['launched_ts'], age_min), turn_label, end_ts))
            still.append(i)
        else:
            ts_ = W.codex_thread_state(i['thread'])
            out_ = W.task_output_status(i.get('output_file')) if i.get('output_file') else {}
            if out_.get('exit_code') is not None: continue
            if ts_.get('found') and ts_.get('last_complete') and ts_['last_complete'] > i['launched_ts'] and not ts_.get('in_flight'):
                continue
            i['status'] = 'codex in_flight=%s last_event=%s' % (ts_.get('in_flight'), ts_.get('last_event'))
            quiet_min = (now - (W.epoch_from_iso(ts_.get('last_event')) or now)) / 60.0 if ts_.get('last_event') else None
            if ts_.get('found') and ts_.get('in_flight') and quiet_min is not None and quiet_min > a.codex_quiet_min:
                findings.append(finding('codex_turn_silent', 'codex_turn_silent:%s:%s' % (i['thread'][:8], ts_.get('last_started')), dict(last_event=ts_.get('last_event')), 'turn %s dispatched `codex-run` to thread %s at %s' % (i.get('turn_ct'), i['thread'][:8], i['launched_ts']),
                                        'the thread rollout %s' % ts_.get('rollout'), 'task_started %s with no task_complete; no rollout event for %.0f min (last %s)' % (ts_.get('last_started'), quiet_min, ts_.get('last_event')), turn_label, end_ts))
            still.append(i)

    # ---- stall confirmation request (owner's loop): on a STALL trigger the dead/silent findings ask for a reply
    if a.self_sel:
        me = self_sess['sessionId'] if self_sess else a.self_sel
        for f in findings:
            if f['cls'] in ('task_dead', 'codex_turn_silent'):
                f['asks_reply'] = True
                f['message'] += ' | confirm to the watchdog whether you consider this a stall, reconcile it against the contract, and reply to session %s' % me
    aw = state.get('awaiting_reply')
    if trigger.startswith('REPLY_OVERDUE') and aw and not aw.get('poked'):
        findings.append(finding('reply_overdue', 'reply_overdue:%s' % aw.get('message_id'), dict(message_id=aw.get('message_id')),
                                'watchdog message %s sent %s asked you to confirm a stall and reply' % (aw.get('message_id'), aw.get('sent_ts')),
                                'this session\'s transcript for a cross-session message from you after %s' % aw.get('sent_ts'),
                                'no reply by %s (deadline %s); reply to session %s' % (W.now_iso(), aw.get('deadline'), (self_sess or {}).get('sessionId') or a.self_sel), turn_label, end_ts))
        findings[-1]['asks_reply'] = True; findings[-1]['is_poke'] = True

    # ---- announcements at turn end: HINTS for the model, never findings (the scripts do not judge language)
    for c in [c for c in claims if c['kind'] == 'announce']:
        this_turn_live = [i for i in still if i.get('turn_ct') == ct and i.get('launched_ts', '') >= (T.start_ts or '')]
        observations.append('ANNOUNCE HINT%s: "%s" -- at turn end: %d live process(es), %d item(s) from this turn still in flight. If this is a commitment to act, raise it: wd.sh finding announced_nothing_running "<sentence>" check running' % (
            ' [conditional]' if c.get('conditional') else '', W.short(c['sentence'], 160), len(procs), len(this_turn_live)))

    # ---- the target simply stopped, against a standing instruction to continue (owner's rule: report the
    # contradiction when it stopped without saying why; stay quiet when it named a blocker or a question)
    if trigger.startswith('IDLE') and a.standing_instruction:
        # a question to the owner from ANY recent turn keeps the session legitimately waiting, not stopped:
        # looking only at the last turn missed one asked two turns earlier and called a waiting session idle
        gate_hints = []
        for _t in turns[-4:]:
            for _ts, _txt in _t.assistant_texts: gate_hints += W.owner_gate_hints(_txt)
        open_rows = len((L.get('rows') or {})) if L.get('exists') and not L.get('dataless') else None
        idle_min = re.search(r'idle_min=(\d+)', trigger)
        if gate_hints:
            observations.append('IDLE, but a recent turn named a blocker or a question for the owner, so it is waiting rather than stopped: %s' % W.short(gate_hints[0], 160))
        else:
            findings.append(finding('idle_no_blocker', 'idle_no_blocker:%s' % (T.pid[:8] if T else ct), dict(ct=ct, open_rows=open_rows),
                                    "the owner's standing instruction: %s" % a.standing_instruction,
                                    'live child processes of the session pid(s) %s; in-flight items; the turn text for a named blocker or a question for the owner; %s' % (W.session_pids(sess), a.ledger or 'no ledger configured'),
                                    'nothing running, nothing in flight, no blocker or question named in the last turn, idle %s min%s' % (
                                        idle_min.group(1) if idle_min else '?', ('; %d open rows remain in %s' % (open_rows, a.ledger)) if open_rows else ''),
                                    turn_label, end_ts))

    # ---- for the owner: declarations that work is gated on the owner, wherever they appear
    for_owner = []
    if T:
        for ts, txt in T.assistant_texts:
            for hnt in W.owner_gate_hints(txt): for_owner.append(('turn text %s' % (ts or '')[11:19], hnt))
    if L.get('exists') and not L.get('dataless'):
        for name, body in (L.get('section_text') or {}).items():
            if W.OWNER_GATE_RE.search(name) or name.lower().startswith(('e.', 'e ')):
                # only OPEN items count: drop struck-through lines and lines recording an answer
                live = [ln for ln in body.splitlines() if ln.strip() and not ln.strip().startswith('~~') and not re.search(r'ANSWERED|CONFIRMED and corrected|RULED', ln)]
                if any(re.match(r'\s*(\d+\.|[-*])\s', ln) for ln in live):
                    for_owner.append(('ledger section "%s"' % name, '\n'.join(live).strip()))
    seen_fo = set(state.get('for_owner_seen') or [])
    for_owner_new = [(src, txt) for src, txt in for_owner if W.h(txt) not in seen_fo]
    for_owner_all = for_owner; for_owner = for_owner_new
    to_wd = W.messages_to_watchdog(T, (self_sess or {}).get('sessionId')) if T else []
    bypass_hint = None
    if for_owner_new and T and not any(re.search(r'owner|blocked|ruling|question|decide', m, re.I) for _, m in to_wd):
        bypass_hint = 'work declared gated on the owner in this turn (%s) and no message to the watchdog session carried it (messages to watchdog this turn: %d)' % ('; '.join(sorted(set(src for src, _ in for_owner)))[:160], len(to_wd))

    # ---- did the target reply to a question we asked?
    reply = None
    if aw and self_sess:
        got = W.peer_replies(self_sess, sess['sessionId'], aw.get('sent_ts'))
        if got:
            reply = dict(ts=got[-1][0], text=got[-1][1], message_id=aw.get('message_id'))
            observations.append('REPLY from the target at %s to message %s: %s' % (reply['ts'], aw.get('message_id'), W.short(reply['text'], 300)))

    # ---- policy
    raised = state.get('raised', {})
    for pid_, p_ in state.get('proposed', {}).items():      # unmarked proposals from earlier wakes count as raised (rule 2 safety)
        raised.setdefault(p_['key'], dict(evidence_hash=p_['evidence_hash'], finding_id=pid_, ts=p_.get('ts'), note='assumed sent (never marked)'))
    TURN_CLASSES = ('commit_missing', 'push_not_on_remote', 'push_drift', 'dispatch_failed', 'dispatch_no_turn', 'dispatch_claim_no_call',
                    'file_claim_missing', 'file_claim_stale', 'ledger_close_not_applied', 'ledger_close_unbacked', 'announced_nothing_running')
    for f in findings:
        prev_r = raised.get(f['key'])
        if own_turn and f['cls'] in TURN_CLASSES and f['quote'] and f['quote'][:60] in (T.opener_text or ''): f['status'] = 'held:own-turn'   # only our own words coming back
        elif prev_r and prev_r.get('evidence_hash') == f['evidence_hash']: f['status'] = 'held:dup(%s)' % prev_r.get('finding_id')
        elif owner_active and f['severity'] != 'destructive-risk': f['status'] = 'held:quiet'
        else: f['status'] = 'send'
    # dedupe within this wake by key
    seen_keys, uniq = set(), []
    for f in findings:
        if f['key'] in seen_keys: continue
        seen_keys.add(f['key']); uniq.append(f)
    findings = uniq
    return dict(T=T, ct=ct, turn_label=turn_label, end_ts=end_ts, owner_active=owner_active, last_human=last_human, last_human_age=last_human_age,
                own_turn=own_turn, open_turn=open_turn, digest=digest, claims=claims, findings=findings, observations=observations,
                ledger_summary=ledger_summary, new_ledger=new_ledger, inflight=still, disp_records=disp_records, notified=sorted(notified)[-500:],
                new_pids=[t.pid for t in new_turns if t.pid], drift=drift, commits=commits, pushes=pushes, launches=launches, reply=reply,
                ledger_diff=ledger_diff, for_owner=for_owner, for_owner_all=for_owner_all, to_wd=to_wd, bypass_hint=bypass_hint)

def print_report(a, sess, st, state, R, trigger, wall, bytes_read):
    T = R['T']
    q = state.get('owner_queue') or []
    sendable = [it for it in q if not it.get('hold_until')]
    held = [it for it in q if it.get('hold_until')]
    if sendable:
        print('=== OWNER ITEMS, SENDABLE NOW (%d) -- send with this wake, as ONE message with any findings ===' % len(sendable))
        for it in sendable: print('  %s [%s]%s %s' % (it['id'], it['ts'], ' URGENT' if it.get('urgent') else '', it['text']))
        print('  (after sending: wd.sh queue clear <message_id>)')
    if held:
        print('=== OWNER ITEMS, HELD (%d) -- do NOT send; each waits on the work named ===' % len(held))
        for it in held: print('  %s HELD UNTIL: %s\n      %s' % (it['id'], it['hold_until'], it['text']))
    print('WAKE #%d  trigger=%s  target="%s" (%s)  ct=%s cec=%s' % (state['wake_count'], trigger, sess['title'], sess['sessionId'], st['ct'], st['cec']))
    if T:
        print('turn: opener=%s start=%s end=%s end_state=%s tools=%d notifications=%d api_errors=%d markers=%d' % (T.opener_kind, T.start_ts, T.end_ts, T.end_state, len(T.tool_uses), len(T.notifications), len(T.api_errors), len(T.markers)))
        print('opener text: %s' % W.short(T.opener_text, 200))
    if R['open_turn'] is not None:
        print('NOTE: a newer turn is OPEN (opener=%s, started %s) -- the target is mid-turn' % (R['open_turn'].opener_kind, R['open_turn'].start_ts))
    print('quiet: owner_active=%s (last human message %s, %s min ago)  own_turn=%s' % (R['owner_active'], R['last_human'], ('%.1f' % R['last_human_age']) if R['last_human_age'] is not None else '?', R['own_turn']))
    print('--- tool digest (%d) ---' % len(R['digest']))
    for d in R['digest'][:a.max_digest]: print('  ' + d)
    if len(R['digest']) > a.max_digest: print('  ... %d more' % (len(R['digest']) - a.max_digest))
    print('--- assistant texts, ALL, IN FULL (%d) --- read every line; the scripts do not judge language ---' % len(T.assistant_texts if T else []))
    for ts, txt in (T.assistant_texts if T else []):
        print('[%s]' % (ts or '')[11:19]); print(txt.rstrip()); print()
    if R['to_wd']:
        print('--- messages this turn sent to the watchdog (%d), IN FULL ---' % len(R['to_wd']))
        for ts, m in R['to_wd']: print('[%s]' % (ts or '')[11:19]); print(m.rstrip()); print()
    print('--- claim hints (%d) -- regex hints only; the model decides ---' % len(R['claims']))
    for c in R['claims']: print('  %s%s: %s' % (c['kind'], ' [conditional]' if c.get('conditional') else '', W.short(c['sentence'], 150)))
    if R['drift']: print('--- git: branch %(branch)s head %(head)s remote %(remote_sha)s ahead=%(ahead)s behind=%(behind)s (%(source)s) dirty=%(dirty)s' % R['drift'])
    if R['disp_records']:
        print('--- dispatches this wake ---')
        for d in R['disp_records']: print('  %s codex-run %s thread=%s brief=%s -> %s%s thread_state=%s' % (d['ts'], d['verb'], (d['thread'] or '-')[:8], d['brief_path'], d['outcome'], ' FAILED' if d['failed'] else '', d['thread_state']))
    print('--- ledger: %s' % R['ledger_summary'])
    if R.get('ledger_diff'):
        print('--- ledger diff since last wake ---')
        for d in R['ledger_diff']: print('  ' + d.replace('\n', '\n  '))
    print('=== FOR THE OWNER (%d new; %d standing) -- relay the NEW ones verbatim to the owner before anything else ===' % (len(R['for_owner']), len(R.get('for_owner_all') or [])))
    for src, txt in R['for_owner']: print('  [%s] %s' % (src, txt.replace('\n', '\n      ')))
    if R.get('bypass_hint'): print('  PROTOCOL BYPASS HINT: %s' % R['bypass_hint'])
    aw = state.get('awaiting_reply')
    if aw: print('--- awaiting reply to message %s sent %s (deadline %s, poked=%s)%s' % (aw.get('message_id'), aw.get('sent_ts'), aw.get('deadline'), aw.get('poked'), ' -- REPLY RECEIVED, see observations' if R.get('reply') else ''))
    print('--- in flight (%d) ---' % len(R['inflight']))
    for i in R['inflight']: print('  %s %s launched %s (turn %s) status=%s' % (i['kind'], i.get('id') or i.get('thread', '')[:8], i.get('launched_ts'), i.get('turn_ct'), i.get('status')))
    print('--- findings (%d) ---' % len(R['findings']))
    if not R['findings']: print('  (none)')
    for f in R['findings']:
        print('  %s [%s] %s' % (f.get('id', '?'), f['status'], f['cls']))
        print('     ' + f['message'])
    if R['observations']:
        print('--- observations (not findings) ---')
        for o in R['observations']: print('  ' + o)
    to_send = [f for f in R['findings'] if f['status'] == 'send']
    print('--- HELD for delivery: %d finding(s)%s' % (len(to_send), '' if to_send else ' -- nothing to say'))
    if to_send:
        print('    (do NOT send now unless it is urgent: deliver with `wd.sh due` when the target\'s loop is closed)')
        for f in to_send: print('    %s: %s' % (f['id'], f['message']))
    print('NEXT: wd.sh due   (what is undelivered, and whether the target is receptive)')
    print('      wd.sh wait  (re-arm the hook -- run it in the background every single wake)')
    print('cost: scripts %.1f s wall, %.1f MB transcript read' % (wall, bytes_read / 1e6))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target'); ap.add_argument('--state-dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state'))
    ap.add_argument('--self', dest='self_sel', help='the watchdog session itself (for reply tracking)')
    ap.add_argument('--repo'); ap.add_argument('--ledger', default=None, help='ledger file relative to the repo (optional)'); ap.add_argument('--other-ref', default=None, help='a second ref whose commits also count as backing (e.g. the other agent\'s branch)')
    ap.add_argument('--perm-paths', default='', help='comma-separated repo paths whose commits back a ledger row deletion')
    ap.add_argument('--row-pattern', default=W.DEFAULT_ROW_PATTERN); ap.add_argument('--reply-min', type=float, default=20.0)
    ap.add_argument('--standing-instruction', default='', help="the owner's standing instruction to keep working; an idle turn that names no blocker contradicts it")
    ap.add_argument('--trigger', default='manual'); ap.add_argument('--quiet-min', type=float, default=10.0); ap.add_argument('--stale-turns', type=int, default=5)
    ap.add_argument('--dead-min', type=float, default=10.0); ap.add_argument('--codex-quiet-min', type=float, default=30.0); ap.add_argument('--turns', type=int, default=6)
    ap.add_argument('--max-digest', type=int, default=30); ap.add_argument('--bootstrap', action='store_true'); ap.add_argument('--replay', type=int, default=0)
    ap.add_argument('--no-state', action='store_true'); ap.add_argument('--json', action='store_true')
    ap.add_argument('--sent'); ap.add_argument('--message-id', default=''); ap.add_argument('--veto'); ap.add_argument('--reason', default='')
    ap.add_argument('--queue-add'); ap.add_argument('--queue-urgent', action='store_true'); ap.add_argument('--queue-list', action='store_true'); ap.add_argument('--queue-clear'); ap.add_argument('--queue-hold'); ap.add_argument('--hold-until', default='')
    ap.add_argument('--due', action='store_true')
    ap.add_argument('--owe-add'); ap.add_argument('--gated-on', default=''); ap.add_argument('--owe-list', action='store_true')
    ap.add_argument('--owe-clear'); ap.add_argument('--owe-ungate')
    a = ap.parse_args()
    a.perm_paths = [x.strip() for x in a.perm_paths.split(',') if x.strip()]
    W.set_row_pattern(a.row_pattern)
    os.makedirs(a.state_dir, exist_ok=True)
    state = load_state(a.state_dir)
    if a.owe_add or a.owe_list or a.owe_clear or a.owe_ungate:
        # Decisions the OWNER owes. Each is READY or GATED behind work the target has not finished: a decision
        # he cannot sensibly make yet must never be presented to him as if it were waiting on him.
        owe = state.setdefault('owner_decisions', {})
        if a.owe_add:
            oid = 'D%d' % (len(owe) + 1)
            owe[oid] = dict(id=oid, ts=W.now_iso(), text=a.owe_add, gated_on=a.gated_on or None)
            save_state(a.state_dir, state); print('recorded %s%s' % (oid, (' GATED behind: ' + a.gated_on) if a.gated_on else ' READY'))
        if a.owe_ungate:
            if a.owe_ungate in owe: owe[a.owe_ungate]['gated_on'] = None; save_state(a.state_dir, state); print('%s is now READY' % a.owe_ungate)
        if a.owe_clear:
            if owe.pop(a.owe_clear, None) is not None: save_state(a.state_dir, state); print('%s answered and cleared' % a.owe_clear)
        ready = [d for d in owe.values() if not d.get('gated_on')]
        gated = [d for d in owe.values() if d.get('gated_on')]
        print('READY for the owner — he can answer these now: %d' % len(ready))
        for d in ready: print('  %s %s' % (d['id'], W.short(d['text'], 220)))
        print('GATED — do not put these to him yet: %d' % len(gated))
        for d in gated: print('  %s %s\n      gated behind: %s' % (d['id'], W.short(d['text'], 200), d['gated_on']))
        return 0
    if a.due:
        if not a.target: ap.error('--due needs --target')
        sess = W.find_session(a.target); st = W.read_state(sess)
        q = state.get('owner_queue') or []; prop = state.get('proposed') or {}
        procs = W.live_children(sess); infl = state.get('in_flight') or []
        _, turns = W.last_turns(sess, n=3)
        openq = []
        for t in turns[-3:]:
            for _ts, _txt in t.assistant_texts: openq += W.owner_gate_hints(_txt)
        idle_min = (time.time() - (st['lastActivityAt'] or 0) / 1000.0) / 60.0
        receptive = (not procs) and (not infl) and idle_min > 0.5
        sendable = [it for it in q if not it.get('hold_until')]
        held = [it for it in q if it.get('hold_until')]
        print('OWNER ITEMS SENDABLE NOW: %d' % len(sendable))
        for it in sendable: print('  %s%s %s' % (it['id'], ' URGENT' if it.get('urgent') else '', W.short(it['text'], 220)))
        print('OWNER ITEMS HELD: %d' % len(held))
        for it in held: print('  %s waits on: %s' % (it['id'], it['hold_until']))
        print('UNDELIVERED findings: %d' % len(prop))
        for fid, pr in prop.items(): print('  %s (%s, proposed %s)' % (fid, pr.get('key', '').split(':')[0], pr.get('ts')))
        print('target: %d live process(es), %d in flight, idle %.1f min -> %s' % (len(procs), len(infl), idle_min,
              'not mid-turn' if receptive else 'BUSY: mid-work'))
        # An idle target is not the gate. The gate is the CURRENT WORK SET: everything it is doing plus
        # every finding still owed to it. A queued item goes only when that set is finished.
        if prop:
            print('GATE: %d finding(s) still owed to it. Those are part of tonight\'s work set -- they go FIRST,'
                  ' and the sendable queue goes only once the work they name is done.' % len(prop))
        elif not sendable:
            print('GATE: nothing sendable.')
        elif receptive:
            print('GATE: work set looks closed and nothing is owed -> deliver the sendable items as ONE message.')
        else:
            print('GATE: it is mid-work -> hold.')
        if openq: print('note: it has a question outstanding to the owner (%s) -- it is waiting, not stopped' % W.short(openq[-1], 90))
        return 0
    if a.queue_hold:
        q = state.setdefault('owner_queue', [])
        hit = [it for it in q if it['id'] == a.queue_hold]
        if not hit: raise SystemExit('no queued item %r' % a.queue_hold)
        if a.hold_until: hit[0]['hold_until'] = a.hold_until
        else: hit[0].pop('hold_until', None)
        save_state(a.state_dir, state)
        print('%s %s' % (hit[0]['id'], ('HELD UNTIL: ' + a.hold_until) if a.hold_until else 'released -- sendable now'))
        return 0
    if a.queue_add or a.queue_list or a.queue_clear:
        q = state.setdefault('owner_queue', [])
        if a.queue_add:
            q.append(dict(id='Q%d' % (len(q) + len(state.get('owner_queue_sent') or []) + 1), ts=W.now_iso(),
                          urgent=bool(a.queue_urgent), text=a.queue_add))
            save_state(a.state_dir, state); print('queued %s%s' % (q[-1]['id'], ' URGENT' if q[-1]['urgent'] else ''))
        if a.queue_clear:
            sent = state.setdefault('owner_queue_sent', [])
            for it in list(q):
                if it.get('hold_until'): continue   # a held item was never in the message; it stays queued
                sent.append(dict(it, sent_ts=W.now_iso(), message_id=a.queue_clear)); q.remove(it)
            save_state(a.state_dir, state); log_line(a.state_dir, '%s OWNER-QUEUE delivered %s' % (W.now_iso(), a.queue_clear))
            print('queue cleared into message %s' % a.queue_clear)
        if a.queue_list or a.queue_add:
            for it in q: print('%s [%s]%s %s' % (it['id'], it['ts'], ' URGENT' if it.get('urgent') else '', W.short(it['text'], 200)))
            if not q: print('(owner queue empty)')
        return 0
    if a.sent or a.veto:
        ids = [x.strip() for x in (a.sent or a.veto).split(',') if x.strip()]
        for fid in ids:
            p = state['proposed'].pop(fid, None)
            if a.sent:
                if p: state['raised'][p['key']] = dict(evidence_hash=p['evidence_hash'], finding_id=fid, ts=W.now_iso(), message_id=a.message_id)
                if p and p.get('is_poke') and state.get('awaiting_reply'):
                    state['awaiting_reply']['poked'] = True; state['awaiting_reply']['poke_message_id'] = a.message_id
                elif p and p.get('asks_reply'):
                    now_ = time.time()
                    state['awaiting_reply'] = dict(message_id=a.message_id, sent_ts=W.now_iso(), deadline=W.iso_from_epoch(now_ + 60 * a.reply_min), findings=[fid], poked=False)
                rewrite_status(a.state_dir, fid, 'sent', a.message_id)
                log_line(a.state_dir, '%s SENT %s %s' % (W.now_iso(), fid, a.message_id))
            else:
                rewrite_status(a.state_dir, fid, 'vetoed', a.reason.replace('|', '/'))
                log_line(a.state_dir, '%s VETO %s %s' % (W.now_iso(), fid, a.reason))
        save_state(a.state_dir, state); print('recorded %s: %s' % ('sent' if a.sent else 'veto', ids)); return 0
    if not a.target: ap.error('--target is required')
    t0 = time.time()
    sess = W.find_session(a.target)
    self_sess = W.find_session(a.self_sel) if a.self_sel else None
    st = W.read_state(sess)
    path, turns = W.last_turns(sess, n=max(a.turns, a.replay + 2))
    bytes_read = W.LAST_TAIL_BYTES
    if a.replay:
        turns = turns[:len(turns) - a.replay + 1] if a.replay > 1 else turns
        a.no_state = True
    replay = bool(a.replay)
    if a.bootstrap:
        R = analyse(a, sess, st, state, turns, 'bootstrap', self_sess=self_sess)
        state.update(seen_pids=[t.pid for t in turns if t.pid][-40:], ledger=R['new_ledger'], in_flight=R['inflight'], notified=R['notified'], last_ct=st['ct'], last_cec=st['cec'],
                     bootstrap_ts=W.now_iso(), last_wake_ts=W.now_iso(), dispatch_log=(state.get('dispatch_log') or []))
        save_state(a.state_dir, state)
        print('bootstrapped: target=%s ct=%s cec=%s turns_seen=%d ledger=%s in_flight=%d' % (sess['sessionId'], st['ct'], st['cec'], len(state['seen_pids']), R['ledger_summary'], len(R['inflight'])))
        log_line(a.state_dir, '%s BOOTSTRAP ct=%s' % (W.now_iso(), st['ct'])); return 0
    latest_done = None
    if turns:
        cands = turns[:-1] + ([turns[-1]] if turns[-1].end_state != 'open' else [])
        latest_done = cands[-1] if cands else None
    if (not replay and latest_done is not None and latest_done.pid in set(state.get('seen_pids', []))
            and not a.trigger.startswith(('STALE', 'STALL', 'REPLY', 'IDLE', 'CONTEXT_EXCEEDED', 'manual'))):
        print('WAKE (skipped) trigger=%s: the latest completed turn (%s, ended %s) was already analysed at an earlier wake; nothing new. stay silent' % (a.trigger, (latest_done.pid or '')[:8], latest_done.end_ts))
        log_line(a.state_dir, '%s SKIP trigger=%s ct=%s (turn %s already seen)' % (W.now_iso(), a.trigger, st['ct'], (latest_done.pid or '')[:8]))
        return 0
    R = analyse(a, sess, st, state, turns, a.trigger, replay=replay, self_sess=self_sess)
    wake_no = state['wake_count'] + 1
    for f in R['findings']:
        state['finding_counter'] += 1; f['id'] = 'F%d' % state['finding_counter']
    wall = time.time() - t0
    if not a.no_state:
        state['wake_count'] = wake_no
        state['seen_pids'] = (state['seen_pids'] + R['new_pids'])[-40:]
        state['ledger'] = R['new_ledger']; state['in_flight'] = R['inflight']; state['notified'] = R['notified']
        state['for_owner_seen'] = sorted(set(state.get('for_owner_seen') or []) | set(W.h(t) for _, t in (R.get('for_owner_all') or [])))[-400:]
        state['last_ct'] = st['ct']; state['last_cec'] = st['cec']; state['last_wake_ts'] = W.now_iso()
        state['dispatch_log'] = (state.get('dispatch_log') or [])[-300:] + [dict(ts=r['ts'], ct=st['ct'], verb=r['verb'], thread=r['thread'], brief_path=r['brief_path'], inline=r['inline']) for r in R['disp_records']]
        for f in R['findings']:
            if f['status'] == 'send': state['proposed'][f['id']] = dict(key=f['key'], evidence_hash=f['evidence_hash'], ts=W.now_iso(), asks_reply=bool(f.get('asks_reply')), is_poke=bool(f.get('is_poke')))
        if R.get('reply'):
            state['last_reply'] = R['reply']; state['awaiting_reply'] = None
        append_findings_md(a.state_dir, [dict(id=f['id'], wake=wake_no, wake_ts=W.now_iso(), turn_ct=st['ct'], cls=f['cls'], status=f['status'], message=f['message']) for f in R['findings']])
        log_line(a.state_dir, '%s WAKE %d trigger=%s ct=%s findings=%d send=%d owner_active=%s own_turn=%s wall=%.1fs' % (W.now_iso(), wake_no, a.trigger, st['ct'], len(R['findings']), sum(1 for f in R['findings'] if f['status'] == 'send'), R['owner_active'], R['own_turn'], wall))
        save_state(a.state_dir, state)
    else:
        state['wake_count'] = wake_no
    if a.json:
        print(json.dumps(dict(wake=wake_no, ct=st['ct'], findings=[{k: v for k, v in f.items() if k != 'evidence'} for f in R['findings']], owner_active=R['owner_active'], own_turn=R['own_turn']), indent=1, default=str))
    else:
        print_report(a, sess, st, state, R, a.trigger, wall, bytes_read)
    return 0

if __name__ == '__main__':
    sys.exit(main())
