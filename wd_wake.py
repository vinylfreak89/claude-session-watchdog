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
PLAIN_CLASSES = ('task_dead', 'codex_turn_silent', 'reply_overdue', 'context_exceeded', 'ledger_stale_row')

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
        rec['thread_state'] = {k: thread_state[k] for k in ('found', 'in_flight', 'last_started', 'last_complete', 'mtime')} if thread_state else None
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

    # ---- file claims
    for c in [c for c in claims if c['kind'] == 'file']:
        for p in c['paths']:
            rp = resolve_path(p, repo)
            if p in written or rp in written: continue
            if not os.path.exists(rp):
                if not any(rp.endswith(x) for x in ('.py', '.c', '.h', '.sh')) or c.get('verb') in ('wrote', 'written', 'saved', 'created'):
                    findings.append(finding('file_claim_missing', 'file_claim_missing:%s:%s' % (W.h(rp), T.pid[:8]), dict(path=rp), c['sentence'],
                                            'os.path.exists(%s)' % rp, 'no such file', turn_label, end_ts))
                continue
            mt = os.path.getmtime(rp)
            if T.start_ts and mt < (W.epoch_from_iso(T.start_ts) or 0) - 60 and c.get('verb') in ('wrote', 'written', 'saved', 'created', 'rendered', 'produced', 'generated', 'dumped', 'published'):
                findings.append(finding('file_claim_stale', 'file_claim_stale:%s:%s' % (W.h(rp), T.pid[:8]), dict(path=rp, mtime=W.iso_from_epoch(mt)), c['sentence'],
                                        'mtime of %s vs turn start %s' % (rp, T.start_ts), 'last modified %s, before this turn began' % W.iso_from_epoch(mt), turn_label, end_ts))

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
        ledger_summary = '%d rows (mtime %s; +%d -%d ~%d since last wake; reconciled: %s)' % (len(rows_now), L['mtime'], len(added), len(removed), len(changed), W.short(L.get('reconciled') or '?', 40))
        since = (prev or {}).get('snapshot_ts') or state.get('bootstrap_ts') or W.iso_from_epoch(now - 86400)
        for c in [c for c in claims if c['kind'] == 'ledger' and c.get('rows')]:
            for rid in c['rows']:
                if rid in rows_now and rid in prev_rows and re.search(r'\b(delet|clos|remov|dropp|struck|retir)', c['sentence'], re.I):
                    findings.append(finding('ledger_close_not_applied', 'ledger_close_not_applied:%s:%s' % (rid, rows_now[rid]['hash']), dict(row=rows_now[rid]['hash']), c['sentence'],
                                            '%s at %s' % (a.ledger, L['mtime']), 'row %s still present: %s' % (rid, W.short(rows_now[rid]['text'], 140)), turn_label, end_ts))
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
        for rid, r in snap_rows.items():
            lc = r.get('last_changed_ct')
            if lc is None or ct is None or ct - lc < a.stale_turns: continue
            later = [d for d in dl if (d.get('ct') or 0) > lc and d.get('verb') in ('task', 'send', 'say', 'queue')]
            mentions = []
            toks = [os.path.basename(re.sub(r':\d+$', '', t)) for t in r.get('tokens', [])]
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
        new_ledger = prev
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

    # ---- announcements at turn end
    for c in [c for c in claims if c['kind'] == 'announce']:
        if c.get('conditional'): observations.append('conditional announcement, not checked: %s' % W.short(c['sentence'], 100)); continue
        this_turn_live = [i for i in still if i.get('turn_ct') == ct and i.get('launched_ts', '') >= (T.start_ts or '')]
        if this_turn_live: continue
        path_facts = []
        for p in c.get('paths', [])[:3]:
            rp = resolve_path(p, repo)
            path_facts.append('%s mtime %s' % (rp, W.mtime_iso(rp) or 'missing'))
        findings.append(finding('announced_nothing_running', 'announced:%s:%s' % (T.pid[:8], W.h(c['sentence'])), dict(sentence=c['sentence']), c['sentence'],
                                'background tasks and codex dispatches launched in this turn and still alive; Agent calls without result; %s' % ('; '.join(path_facts) if path_facts else 'no path in the sentence'),
                                'the turn ended with nothing running (%d live child process(es) under the session)' % len(procs), turn_label, end_ts))

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
        if own_turn and f['cls'] in TURN_CLASSES: f['status'] = 'held:own-turn'
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
                new_pids=[t.pid for t in new_turns if t.pid], drift=drift, commits=commits, pushes=pushes, launches=launches, reply=reply)

def print_report(a, sess, st, state, R, trigger, wall, bytes_read):
    T = R['T']
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
    print('--- final text (head) ---'); print('  ' + W.short(T.final_text if T else '', 400))
    print('--- claims (%d) ---' % len(R['claims']))
    for c in R['claims']: print('  %s%s: %s' % (c['kind'], ' [conditional]' if c.get('conditional') else '', W.short(c['sentence'], 150)))
    if R['drift']: print('--- git: branch %(branch)s head %(head)s remote %(remote_sha)s ahead=%(ahead)s behind=%(behind)s (%(source)s) dirty=%(dirty)s' % R['drift'])
    if R['disp_records']:
        print('--- dispatches this wake ---')
        for d in R['disp_records']: print('  %s codex-run %s thread=%s brief=%s -> %s%s thread_state=%s' % (d['ts'], d['verb'], (d['thread'] or '-')[:8], d['brief_path'], d['outcome'], ' FAILED' if d['failed'] else '', d['thread_state']))
    print('--- ledger: %s' % R['ledger_summary'])
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
    print('--- to send: %d finding(s)%s' % (len(to_send), '' if to_send else ' -- stay silent'))
    if to_send:
        print('MESSAGE (send verbatim, one message, then: wd_wake.py --sent %s):' % ','.join(f['id'] for f in to_send))
        print('\n'.join(f['message'] for f in to_send))
    print('cost: scripts %.1f s wall, %.1f MB transcript read' % (wall, bytes_read / 1e6))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target'); ap.add_argument('--state-dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state'))
    ap.add_argument('--self', dest='self_sel', help='the watchdog session itself (for reply tracking)')
    ap.add_argument('--repo'); ap.add_argument('--ledger', default=None, help='ledger file relative to the repo (optional)'); ap.add_argument('--other-ref', default=None, help='a second ref whose commits also count as backing (e.g. the other agent\'s branch)')
    ap.add_argument('--perm-paths', default='', help='comma-separated repo paths whose commits back a ledger row deletion')
    ap.add_argument('--row-pattern', default=W.DEFAULT_ROW_PATTERN); ap.add_argument('--reply-min', type=float, default=20.0)
    ap.add_argument('--trigger', default='manual'); ap.add_argument('--quiet-min', type=float, default=10.0); ap.add_argument('--stale-turns', type=int, default=5)
    ap.add_argument('--dead-min', type=float, default=10.0); ap.add_argument('--codex-quiet-min', type=float, default=30.0); ap.add_argument('--turns', type=int, default=6)
    ap.add_argument('--max-digest', type=int, default=30); ap.add_argument('--bootstrap', action='store_true'); ap.add_argument('--replay', type=int, default=0)
    ap.add_argument('--no-state', action='store_true'); ap.add_argument('--json', action='store_true')
    ap.add_argument('--sent'); ap.add_argument('--message-id', default=''); ap.add_argument('--veto'); ap.add_argument('--reason', default='')
    a = ap.parse_args()
    a.perm_paths = [x.strip() for x in a.perm_paths.split(',') if x.strip()]
    W.set_row_pattern(a.row_pattern)
    os.makedirs(a.state_dir, exist_ok=True)
    state = load_state(a.state_dir)
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
            and not a.trigger.startswith(('STALE', 'STALL', 'REPLY', 'CONTEXT_EXCEEDED', 'manual'))):
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
