#!/usr/bin/python3
"""wd_check.py -- verification on demand, and findings the model cannot fake.

  wd_check.py --target SEL check running                    live child processes of the target + in-flight items + Codex turns
  wd_check.py --target SEL check commit <sha>               exists? on which branches? branch drift vs origin
  wd_check.py --target SEL check file <path> [since_iso]    exists? mtime? modified since?
  wd_check.py --target SEL check task <task_id>             output status, notification, live process
  wd_check.py --target SEL check row <ID>                   present in the ledger? its text
  wd_check.py --target SEL check msg-to-watchdog [since]    messages the target sent to the watchdog session since <iso>
  wd_check.py --target SEL check dispatch <thread>          Codex rollout state for a thread id (prefix ok)
  wd_check.py --target SEL check tree [path]                uncommitted tracked changes, diff size, last commit, branch drift
  wd_check.py --target SEL check grep <path> <regex>        matching lines of a file (file-vs-file disagreements)
  wd_check.py --target SEL check csv <path> <col><op><val> [idcol]   rows of a CSV record matching a condition
  wd_check.py --target SEL finding <class> "<quote>" check <kind> [args...]
        runs the check, builds the fixed-form message from ITS output (the model supplies class and quote only),
        applies dedupe and the quiet rule, logs it to findings.md and proposes it. Then: wd.sh sent Fn <message_id>.
The model does the reading; this does the measuring and the wording. It cannot emit a result it did not compute."""
import os, sys, json, argparse, glob, time, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wd_lib as W
import wd_wake as WK

def check(a, sess, kind, args, state):
    repo = a.repo or sess['cwd']
    if kind == 'running':
        procs = W.live_children(sess)
        infl = state.get('in_flight') or []
        cod = []
        for i in infl:
            if i.get('kind') == 'codex':
                ts_ = W.codex_thread_state(i.get('thread') or ''); cod.append('%s in_flight=%s last_event=%s' % ((i.get('thread') or '')[:8], ts_.get('in_flight'), ts_.get('last_event')))
        checked = 'live child processes of the session pid(s) %s; in-flight items in state; Codex rollouts of dispatched threads' % W.session_pids(sess)
        result = '%d live process(es)%s; %d in-flight item(s)%s%s' % (len(procs), (': ' + '; '.join(W.short(p['command'], 70) for p in procs[:4])) if procs else '', len(infl),
                                                                     (': ' + '; '.join((i.get('id') or (i.get('thread') or '')[:8]) for i in infl)) if infl else '', ('; codex: ' + '; '.join(cod)) if cod else '')
        # A turn can be OPEN with zero subprocesses -- the model is reading or writing and spawns nothing.
        # Reporting that as idle is how a working session gets nudged for stopping. State it explicitly.
        _, _turns = W.last_turns(sess, n=2)
        _open = _turns[-1] if _turns and _turns[-1].end_state == 'open' else None
        if _open is not None:
            result += '; TURN IS OPEN since %s -- NOT idle, whatever the process count says' % _open.start_ts
        else:
            result += '; no turn open'
        return checked, result, dict(procs=len(procs), inflight=len(infl), turn_open=bool(_open))
    if kind == 'commit':
        info = W.git_sha_info(repo, args[0]); d = W.git_branch_drift(repo)
        checked = 'git cat-file/branch --contains %s; git ls-remote origin %s vs local HEAD' % (args[0], d['branch'])
        if not info['exists']: return checked, 'no commit object %s in %s' % (args[0], repo), dict(exists=False)
        result = '%s exists (%s, %s); local branches %s; remote branches %s; %s at %s, origin %s, ahead %s behind %s' % (args[0], info['date'], W.short(info['subject'], 60), info['local_branches'], info['remote_branches'], d['branch'], d['head'], d['remote_sha'], d['ahead'], d['behind'])
        return checked, result, dict(exists=True, remote=info['remote_branches'], ahead=d['ahead'])
    if kind == 'file':
        rp = WK.resolve_path(args[0], repo); since = args[1] if len(args) > 1 else None
        checked = 'os.stat %s%s' % (rp, (' vs %s' % since) if since else '')
        if not os.path.exists(rp): return checked, 'no such file', dict(exists=False)
        mt = W.mtime_iso(rp); mod = (mt > since) if since else None
        return checked, 'exists, %d bytes, mtime %s%s' % (os.path.getsize(rp), mt, ('' if mod is None else (', modified since %s' % since if mod else ', NOT modified since %s' % since))), dict(exists=True, mtime=mt, modified_since=mod)
    if kind == 'task':
        tid = args[0]; out = os.path.join(W.tasks_dir(sess), tid + '.output'); st = W.task_output_status(out)
        notified = tid in (state.get('notified') or [])
        procs = W.live_children(sess)
        checked = 'output %s (exit marker, mtime); task-notifications seen; live child processes of the session' % out
        result = 'output %s; exit_code %s; mtime %s; notified %s; live processes under session %d' % ('exists' if st.get('exists') else 'missing', st.get('exit_code'), st.get('mtime'), notified, len(procs))
        return checked, result, dict(exit=st.get('exit_code'), notified=notified, procs=len(procs))
    if kind == 'row':
        L = W.parse_ledger(os.path.join(repo, a.ledger)) if a.ledger else dict(exists=False)
        checked = '%s at %s' % (a.ledger, L.get('mtime'))
        if not L.get('exists'): return checked, 'ledger missing/not configured', dict(present=None)
        r = L['rows'].get(args[0])
        return checked, ('row %s present: %s' % (args[0], r['text']) if r else 'row %s absent' % args[0]), dict(present=bool(r), hash=(r or {}).get('hash'))
    if kind == 'msg-to-watchdog':
        since = args[0] if args else ''
        _, turns = W.last_turns(sess, n=6); me = (W.find_session(a.self_sel)['sessionId'] if a.self_sel else None)
        msgs = [(ts, m) for t in turns for ts, m in W.messages_to_watchdog(t, me) if (ts or '') > since]
        checked = "send_message tool calls to session %s in the target's last %d turns since %s" % (me, len(turns), since or 'start of window')
        return checked, ('%d message(s): %s' % (len(msgs), '; '.join('%s "%s"' % ((ts or '')[11:19], W.short(m, 80)) for ts, m in msgs)) if msgs else 'none'), dict(count=len(msgs))
    if kind == 'csv':
        rp = WK.resolve_path(args[0], repo); expr = args[1]; idcol = args[2] if len(args) > 2 else None
        checked = 'rows of %s matching %s (file mtime %s)' % (rp, expr, W.mtime_iso(rp))
        if not os.path.exists(rp): return checked, 'no such file', dict(exists=False)
        import csv as _csv, re as _re
        m = _re.match(r'\s*([\w.]+)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$', expr)
        if not m: raise SystemExit('csv check: expression must be <column><op><value>, e.g. applied_d2!=0')
        col, op, val = m.group(1), m.group(2), m.group(3)
        rows = list(_csv.DictReader(open(rp, errors='replace')))
        if rows and col not in rows[0]: return checked, 'no column %r in the file (columns: %s)' % (col, ', '.join(list(rows[0])[:8]) + '…'), dict(column_missing=True)
        def num(x):
            try: return float(x)
            except (TypeError, ValueError): return None
        def keep(r):
            a, b = num(r.get(col)), num(val)
            if a is None or b is None:
                return (r.get(col) == val) if op == '==' else ((r.get(col) != val) if op == '!=' else False)
            return {'==': a == b, '!=': a != b, '>': a > b, '<': a < b, '>=': a >= b, '<=': a <= b}[op]
        hit = [r for r in rows if keep(r)]
        ids = ('; %s = %s' % (idcol, ', '.join(str(r.get(idcol)) for r in hit[:12]))) if idcol and hit else ''
        vals = collections.Counter(r.get(col) for r in hit)
        return checked, '%d of %d rows match %s (values %s)%s' % (len(hit), len(rows), expr, dict(vals.most_common(5)), ids), dict(matched=len(hit), total=len(rows))
    if kind == 'tree':
        path = args[0] if args else None
        rc, st, _ = W.git(repo, 'status', '--porcelain', *( [path] if path else [] ))
        tracked = [l for l in st.splitlines() if not l.startswith('??')]
        _, stat, _ = W.git(repo, 'diff', '--stat', *(['--', path] if path else []))
        _, last, _ = W.git(repo, 'log', '-1', '--format=%h %ci %s', *(['--', path] if path else []))
        d = W.git_branch_drift(repo)
        checked = 'git status --porcelain%s; git diff --stat; git log -1 -- %s; branch vs origin' % ((' -- ' + path) if path else '', path or '(repo)')
        result = '%s; diff %s; last commit touching it: %s; %s at %s, origin %s, ahead %s' % (
            ('uncommitted tracked changes: ' + '; '.join(W.short(x, 60) for x in tracked)) if tracked else 'no uncommitted tracked changes',
            W.short(stat.strip().splitlines()[-1] if stat.strip() else 'none', 80), W.short(last.strip(), 90), d['branch'], d['head'], d['remote_sha'], d['ahead'])
        return checked, result, dict(dirty=len(tracked), ahead=d['ahead'])
    if kind == 'grep':
        rp = WK.resolve_path(args[0], repo); pat = args[1]
        checked = 'grep -n -i %r %s (mtime %s)' % (pat, rp, W.mtime_iso(rp))
        if not os.path.exists(rp): return checked, 'no such file', dict(exists=False)
        import re as _re
        hits = [(i + 1, ln.strip()) for i, ln in enumerate(open(rp, errors='replace').read().splitlines()) if _re.search(pat, ln, _re.I)]
        return checked, ('%d line(s): %s' % (len(hits), '; '.join('L%d: %s' % (n, W.short(t, 120)) for n, t in hits[:4])) if hits else 'no line matches'), dict(hits=len(hits))
    if kind == 'dispatch':
        fs = glob.glob(os.path.join(W.CODEX_SESSIONS, '*', '*', '*', 'rollout-*-%s*.jsonl' % args[0]))
        if not fs: return 'Codex rollouts for thread %s*' % args[0], 'no rollout found', dict(found=False)
        tid = os.path.basename(max(fs, key=os.path.getmtime)).split('rollout-')[1][20:-6]
        ts_ = W.codex_thread_state(tid)
        return 'rollout %s' % ts_.get('rollout'), 'in_flight %s; last task_started %s; last task_complete %s; last event %s; last message: %s' % (ts_.get('in_flight'), ts_.get('last_started'), ts_.get('last_complete'), ts_.get('last_event'), W.short(ts_.get('last_agent_message') or '', 160)), dict(in_flight=ts_.get('in_flight'))
    raise SystemExit('unknown check kind %r' % kind)

DISPATCH_TOOLS = ('send_message',)
DISPATCH_CMD = __import__('re').compile(r'codex-(run|app)\b', __import__('re').I)

def turn_made_a_dispatch(turn):
    """Did this turn actually send anything -- to a peer session or to Codex?"""
    for u in turn.tool_uses:
        name = u.get('name') or ''
        if any(t in name for t in DISPATCH_TOOLS): return True
        if DISPATCH_CMD.search(json.dumps(u.get('input') or {})): return True
    return False

def owed(sess, state):
    """What the watchdog still owes on each completed target turn.

    Owner's rule, 2026-09-10, in two parts. A turn is answered when it has been RELAYED to him
    AND responded to -- a send back to the target -- or when it is DELIBERATELY HELD because it
    is blocked on his answer. Relaying alone is not enough: that is the failure where he hears
    about a result and nobody acts on it. Sending alone is not enough either: that is the failure
    where the watchdog handles something and he never learns it happened.

    "it should be firing every minute unless you actually sent something back... it firing
    excessively is the point."

    ⚠️ DO NOT ADD A WAY TO DEFER, QUIET, BATCH OR SNOOZE THIS. It is SUPPOSED to be permanently on
    and it is supposed to nag; the owner's design, restated 2026-09-11: "its supposed to be
    permanently on. its supposed to constantly nag you, and hopefully you do nothing. the problem
    is you are fucking great at finding ways to skip it... as you just did by 'fixing' its code.
    its meant to be annoying because its the only fucking thing that keeps you moving."

    It has been "fixed" once, on 2026-09-11, by an agent that had just READ the owner's words three
    lines above this and added a `digest` deferral anyway -- reasoning that an always-on alarm is a
    dead signal. That reasoning is correct in general and WRONG HERE, and the difference is the whole
    point: this alarm is not measuring a system, it is a constraint on the agent reading it. Its cost
    when ignored is borne by the owner, not by the agent, so the agent is exactly the wrong party to
    decide it is too loud. An unrelayed turn is CLEARED BY RELAYING IT. There is no other exit and
    there must not be one.

    Formula: RELAY AND (RESPOND OR HOLD).
    """
    _, turns = W.last_turns(sess, n=8)
    done = [t for t in turns if t.end_state != 'open']
    relayed = state.get('last_relay_ts') or ''
    sent = state.get('last_send_ts') or ''
    held = state.get('held_turns') or {}
    rows = []
    # Owner's formula, 2026-09-10: RELAY AND (RESPOND OR HOLD). Relay is mandatory in both
    # branches - a hold is a decision to wait for him, which he cannot make if he was never
    # told. The earlier version cleared a held turn whether or not it had been relayed.
    for t in done:
        why = []
        if not (relayed and t.end_ts <= relayed): why.append('not relayed')
        if not ((sent and t.end_ts <= sent) or t.end_ts in held): why.append('not answered or held')
        if not why: continue
        text = ' '.join(x for _, x in t.assistant_texts)
        rows.append(dict(ts=t.end_ts, why=' + '.join(why),
                         declared=[m.group(0).strip() for m in W.INTENT_RE.finditer(text)][:3],
                         dispatched=turn_made_a_dispatch(t),
                         head=W.short(t.final_text or '', 130)))
    return rows

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target', required=True); ap.add_argument('--self', dest='self_sel'); ap.add_argument('--repo'); ap.add_argument('--ledger')
    ap.add_argument('--state-dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state')); ap.add_argument('--quiet-min', type=float, default=10.0)
    ap.add_argument('--row-pattern', default=W.DEFAULT_ROW_PATTERN)
    ap.add_argument('mode', choices=['check', 'finding', 'owed', 'relayed', 'hold', 'answered']); ap.add_argument('rest', nargs=argparse.REMAINDER)
    a = ap.parse_args(); W.set_row_pattern(a.row_pattern)
    sess = W.find_session(a.target); state = WK.load_state(a.state_dir)
    if a.mode == 'answered':
        # Sends go out through the MCP tool, which cannot write here, so this is the hook that
        # records them. Forgetting it makes `owed` claim a turn is unanswered when it was answered.
        state['last_send_ts'] = W.now_iso(); WK.save_state(a.state_dir, state)
        print('answered at %s' % state['last_send_ts']); return 0
    if a.mode in ('relayed', 'hold'):
        ts = a.rest[0] if a.rest else ''
        if not ts: ap.error('%s <turn end_ts> %s' % (a.mode, '"reason"' if a.mode == 'hold' else ''))
        if a.mode == 'hold' and 'owner' not in ' '.join(a.rest[1:]).lower() and 'you' not in ' '.join(a.rest[1:]).lower():
            # hold means BLOCKED ON THE OWNER. Waiting on Codex, a subagent or a running job is
            # not a hold -- it is work in flight that still needs collecting, and calling it a
            # hold is how a loop gets lost: the check goes quiet on something that needs a kick.
            # Cost, 2026-09-10: four turns marked held for "with Codex" while nothing collected
            # the reply, until the owner noticed the loop had stopped.
            print('REFUSED: hold is for turns blocked on the OWNER. Waiting on Codex, a subagent'
                  ' or a job is work in flight -- kick it or poll it, do not hold it.')
            return 1
        if a.mode == 'relayed':
            state['last_relay_ts'] = max(ts, state.get('last_relay_ts') or '')
            print('relayed to the owner up to %s' % state['last_relay_ts'])
        else:
            reason = ' '.join(a.rest[1:]).strip()
            if not reason: ap.error('hold <turn end_ts> "why it is blocked on the owner"')
            state.setdefault('held_turns', {})[ts] = dict(reason=reason, ts=W.now_iso())
            print('holding %s: %s' % (ts, reason))
        WK.save_state(a.state_dir, state); return 0
    if a.mode == 'owed':
        rows = owed(sess, state)
        broken = [r for r in rows if r['declared'] and not r['dispatched']]
        print('OWED completed turns: %d' % len(rows))
        for r in rows: print('   %s  [%s]  %s' % (r['ts'], r['why'], r['head']))
        print('DECLARED an action and made no dispatch: %d' % len(broken))
        for r in broken: print('   %s  declared: %s' % (r['ts'], ' | '.join(r['declared'])))
        if not rows: print('   nothing owed')
        return 0
    if a.mode == 'check':
        kind, args = a.rest[0], a.rest[1:]
        checked, result, ev = check(a, sess, kind, args, state)
        print(json.dumps(dict(kind=kind, args=args, checked=checked, result=result, evidence=ev), indent=1, default=str)); return 0
    # finding <class> "<quote>" check <kind> [args]
    if len(a.rest) < 4 or a.rest[2] != 'check': ap.error('finding <class> "<quote>" check <kind> [args...]')
    cls, quote, kind, args = a.rest[0], a.rest[1], a.rest[3], a.rest[4:]
    checked, result, ev = check(a, sess, kind, args, state)
    _, turns = W.last_turns(sess, n=3)
    done = [t for t in turns if t.end_state != 'open'] or turns
    T = done[-1] if done else None
    st = W.read_state(sess)
    f = WK.finding(cls, '%s:%s' % (cls, W.h(quote)), ev, quote, checked, result, str(st['ct']), T.end_ts if T else None)
    raised = state.get('raised', {}); prev = raised.get(f['key'])
    last_human = max([ts for t in turns for ts, _ in t.human_messages if ts] or [''])
    age = (time.time() - (W.epoch_from_iso(last_human) or 0)) / 60.0 if last_human else 1e9
    if prev and prev.get('evidence_hash') == f['evidence_hash']: f['status'] = 'held:dup(%s)' % prev.get('finding_id')
    elif age <= a.quiet_min: f['status'] = 'held:quiet'
    else: f['status'] = 'send'
    state['finding_counter'] += 1; f['id'] = 'F%d' % state['finding_counter']
    # Every finding is OWED until it is sent or vetoed. A held one is not a dropped one: 'held' says
    # not-this-second, never not-at-all, and a finding that leaves the delivery path is a finding lost.
    if not f['status'].startswith('held:dup'):
        state['proposed'][f['id']] = dict(key=f['key'], evidence_hash=f['evidence_hash'], ts=W.now_iso(),
                                          asks_reply=False, is_poke=False,
                                          held=(None if f['status'] == 'send' else f['status']))
    WK.append_findings_md(a.state_dir, [dict(id=f['id'], wake=state.get('wake_count'), wake_ts=W.now_iso(), turn_ct=st['ct'], cls=cls, status=f['status'], message=f['message'])])
    WK.log_line(a.state_dir, '%s FINDING %s %s %s (model-selected, script-verified)' % (W.now_iso(), f['id'], cls, f['status']))
    WK.save_state(a.state_dir, state)
    print('%s [%s] %s' % (f['id'], f['status'], cls)); print(f['message'])
    if f['status'] == 'send': print('(send verbatim, then: wd.sh sent %s <message_id>)' % f['id'])
    return 0

if __name__ == '__main__':
    sys.exit(main())
