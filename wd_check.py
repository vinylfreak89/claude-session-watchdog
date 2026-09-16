#!/usr/bin/python3
"""wd_check.py -- verification on demand, and findings the model cannot fake.

  wd_check.py --target SEL check running                    live child processes of the target + in-flight items + Codex turns
  wd_check.py --target SEL check commit <sha>               exists? on which branches? branch drift vs origin
  wd_check.py --target SEL check file <path> [since_iso]    exists? mtime? modified since?
  wd_check.py --target SEL check task <task_id>             output status, notification, live process
  wd_check.py --target SEL check row <ID>                   present in the ledger? its text
  wd_check.py --target SEL check msg-to-watchdog [since]    messages the target sent to the watchdog session since <iso>
  wd_check.py --target SEL check dispatch <thread>          Codex rollout state for a thread id (prefix ok)
  wd_check.py --target SEL conditional <key> "<cond>"       park an item: still listed, not due until `fired`
  wd_check.py --target SEL fired <key>                      its condition happened; it is due again
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
import wd_state as S
import wd_wake as WK
import wd_receipts as D
import wd_turns as TD
import wd_acceptance as A

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
        mt = W.mtime_iso(rp); mod = (os.stat(rp).st_mtime > D.epoch(since)) if since else None
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
        # A Codex thread id is UUID-shaped, and this check can only look up that namespace.
        # Handed an id of any other kind -- a harness background-task id, say -- it used to glob,
        # miss, and return a bare `no rollout found`, which reads as "the claimed dispatch never
        # happened" and is one step from filing dispatch_claim_no_call against work that was done.
        # A negative must say WHICH negative it is. Control: tests/test_dispatch_check.py.
        # And NO id at all is a third outcome again: it used to raise IndexError, which is a crash
        # where a refusal belongs -- a caller reading only the last line sees a traceback and cannot
        # tell a missing argument from a missing dispatch.
        if not args:
            return ('no id given', 'NO ID GIVEN -- `check dispatch` needs the thread id to resolve. '
                    'This is a missing argument, NOT a missing dispatch.',
                    dict(found=None, id_form='absent'))
        if not __import__('re').fullmatch(r'[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{8,}', args[0]):
            return ('id %r against Codex thread ids' % args[0],
                    'NOT A THREAD ID -- this check only resolves UUID-shaped Codex thread ids, so it '
                    'cannot speak to this id at all. Absence here is NOT evidence the dispatch did not '
                    'happen; corroborate by the reply and by rollout mtimes instead.',
                    dict(found=None, id_form='unrecognised'))
        fs = glob.glob(os.path.join(W.CODEX_SESSIONS, '*', '*', '*', 'rollout-*-%s*.jsonl' % args[0]))
        if not fs: return 'Codex rollouts for thread %s*' % args[0], 'no rollout found', dict(found=False, id_form='thread')
        tid = os.path.basename(max(fs, key=os.path.getmtime)).split('rollout-')[1][20:-6]
        ts_ = W.codex_thread_state(tid)
        return 'rollout %s' % ts_.get('rollout'), 'in_flight %s; last task_started %s; last task_complete %s; last event %s; last message: %s' % (ts_.get('in_flight'), ts_.get('last_started'), ts_.get('last_complete'), ts_.get('last_event'), W.short(ts_.get('last_agent_message') or '', 160)), dict(in_flight=ts_.get('in_flight'), lifecycle_known=ts_.get('lifecycle_known'), last_started=ts_.get('last_started'), last_complete=ts_.get('last_complete'))
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

def answered_allowed(tx_path, self_sel, state, owner_ack, message_id=None):
    """Compatibility entry point; every credit needs a registered transcript receipt."""
    if owner_ack is not None:
        return False, 'operator-attested acknowledgements are retired'
    try:
        rec, changed = D.record_delivery(tx_path, self_sel, state, message_id)
        return True, 'delivered record %s at %s' % (rec['id'], rec['ts'])
    except D.EvidenceError as exc:
        return False, str(exc)


def owed(sess, state, self_sel=None):
    """Relay is required; only a transcript-bound receipt can answer a specific turn.

    Legacy watermarks and operator-authored hold/closure metadata cannot discharge
    an obligation. Read the whole record so unanswered turns cannot age out of a tail.
    """
    turns = W.split_turns(D.read_records(W.transcript_path(sess)))
    tracking = state.get('turn_tracking') or {}
    since = tracking.get('since')
    done = [t for t in turns if t.end_state != 'open'
            and (not since or D.epoch(t.end_ts) >= D.epoch(since))]
    relayed = state.get('last_relay_ts') or ''
    answered = D.answered_turns(W.transcript_path(sess), self_sel, state)
    rows = []
    for t in done:
        why = []
        if not (relayed and t.end_ts <= relayed): why.append('not relayed')
        legacy = tracking.get('legacy_answered_through')
        historical = bool(legacy and D.epoch(t.end_ts) <= D.epoch(legacy))
        held = TD.valid_disposition(sess, t, (state.get('held_turns') or {}).get(t.end_ts), 'hold')
        closed = TD.valid_disposition(sess, t, (state.get('closed_turns') or {}).get(t.end_ts), 'closed')
        if not (historical or t.end_ts in answered or held or closed):
            why.append('not answered or held')
        if not why: continue
        text = ' '.join(x for _, x in t.assistant_texts)
        rows.append(dict(ts=t.end_ts, why=' + '.join(why),
                         declared=W.declared_actions(text),
                         dispatched=turn_made_a_dispatch(t),
                         head=W.short(t.final_text or '', 130)))
    return rows


# --------------------------------------------------------------------------- acceptance

def acceptance_valid(spec):
    try:
        A.parse(spec)
        return True, 'valid acceptance'
    except (Exception, SystemExit) as exc:
        return False, str(exc)


def acceptance_satisfied(a, sess, state, item):
    return A.evaluate(a, sess, state, item)


def unacted_items(a, sess, state):
    rows = []
    for item in state.get('owner_queue') or []:
        if not item.get('sent'):
            continue
        decision = acceptance_satisfied(a, sess, state, item)
        rows.append(dict(id=item.get('id'), sent=item['sent'], spec=item.get('acted_when'),
                         status=decision.status.value, evidence=decision.evidence))
    # Findings lacking a postcondition cannot silently become completed actions.
    for ident, finding in (state.get('sent_findings') or {}).items():
        rows.append(dict(id=ident, sent=finding.get('sent'), spec=None, status='undecided',
                         evidence='finding has no independently verifiable action postcondition'))
    return rows


def settle_acted(a, sess, state):
    closed = []
    for item in list(state.get('owner_queue') or []):
        if not item.get('sent'):
            continue
        decision = acceptance_satisfied(a, sess, state, item)
        if decision.status is A.Status.PASS:
            item['acted_ts'] = W.now_iso()
            item['acted_status'] = decision.status.value
            item['acted_evidence'] = decision.evidence
            state.setdefault('owner_queue_sent', []).append(item)
            state['owner_queue'].remove(item)
            closed.append(item['id'])
    return closed


def next_item(sess, state):
    """The send gate, as ONE function so a control can exercise IT rather than re-derive it.

    Refuses on four grounds, and the middle two were both missing (owner, 2026-09-11: "you're not
    waiting for turns to close... don't rapid fire the queue"):

      1. the target is MID-TURN                      -- it cannot read a second item
      2. a completed turn is OWED                    -- its reply to the LAST item is unhandled, so
                                                        sending the next one outruns the work
      3. the item is HELD behind a condition          -- `queue hold` wrote hold_until and this gate
                                                        never read it, so the hold did NOTHING
      4. nothing is queued

    Only the owner's send-immediately mark overrides 1-3. A hold is the watchdog's own pacing; his
    urgency outranks it.

    ⚠️ The hold defect is the two-stores failure again: `wd_wake.py --due` read hold_until and this
    did not, and THIS is the one that gates the send. The gate is the reading side.
    """
    q = [x for x in (state.get('owner_queue') or []) if not x.get('sent')]
    identities = [x.get('id') for x in state.get('owner_queue') or []]
    if len(identities) != len(set(identities)):
        return 'held', None, 'AMBIGUOUS QUEUE IDS. SEND NOTHING.', len(q)
    urgent = [x for x in q if x.get('urgent')]
    if not q:
        return 'none', None, 'nothing queued.', 0
    if urgent:
        return 'send', urgent[0], '** URGENT override: owner marked this send-immediately **', len(q)
    if any(x.get('sent') for x in state.get('owner_queue') or []) or state.get('sent_findings'):
        return 'owed', None, 'SENT WORK IS NOT YET VERIFIED. SEND NOTHING.', len(q)
    _, turns = W.last_turns(sess, n=1)
    if turns and turns[-1].end_state == 'open':
        return 'busy', None, 'TARGET BUSY (turn open). %d queued. SEND NOTHING.' % len(q), len(q)
    # Only the RELAY half gates a send, and the reason is a deadlock this hit within the hour:
    # `owed` counts a turn unhandled until it is BOTH relayed and answered, and the thing that
    # answers it is usually the next queued item -- so blocking on "not answered" blocked the only
    # message that could clear it. Relaying is a duty to the owner that no queue item performs, so
    # that half gates; answering is what releasing the item DOES.
    # The pacing the owner asked for ("don't rapid fire the queue") is carried by hold_until below,
    # which is the control that actually waits for the WORK rather than for a turn boundary.
    unrelayed = [r for r in owed(sess, state) if 'not relayed' in r['why']]
    if unrelayed:
        return 'owed', None, ('ITS LAST REPLY IS UNRELAYED (%d turn(s), oldest %s). %d queued. '
                              'SEND NOTHING -- read it and relay to the owner first.'
                              % (len(unrelayed), unrelayed[0]['ts'], len(q))), len(q)
    sendable = [x for x in q if not x.get('hold_until')]
    if not sendable:
        held = q[0]
        return 'held', None, ('ALL %d QUEUED ITEMS ARE HELD. SEND NOTHING.\n   %s waits on: %s'
                              % (len(q), held.get('id'), held.get('hold_until'))), len(q)
    ok, why = acceptance_valid(sendable[0].get('acted_when'))
    if not ok or not sendable[0].get('acceptance_baseline'):
        return 'held', None, 'ACCEPTANCE UNAVAILABLE. SEND NOTHING: %s' % why, len(q)
    return 'send', sendable[0], '', len(q)


def due_questions(sess, state, quiet_min):
    """Open questions that should be nudged NOW: the peer has gone quiet, or has moved several
    turns past the ask without resolving it. Never 'every poll' -- nagging a session mid-work is
    the behaviour this primitive exists to replace."""
    qs = state.get('open_questions') or {}
    if not qs: return []
    st = W.read_state(sess); ct = st.get('ct')
    quiet = False
    try:
        last = W.activity_ms(sess)
        quiet = (W.ms_of_iso(W.now_iso()) - last) / 60000.0 >= quiet_min
    except Exception:
        pass
    out = []
    for k, q in sorted(qs.items()):
        try: age = int(ct) - int(q.get('asked_ct') or ct)
        except Exception: age = 0
        # An item owed only IF something else happens is PARKED, not suppressed: it stays in
        # open_questions and in the open listing, and `fired` makes it due like any other. Without
        # this state a conditional sat in the nudge list and came due every three turns, and was
        # marked nudged four times without being sent -- recording a send that did not happen.
        # Control: tests/test_conditional_item.py, whose second case requires an ORDINARY overdue
        # item to still come due, because that is what fails if this becomes a mute button.
        if q.get('conditional'): continue
        if quiet: out.append((k, q, 'peer is quiet', age))
        elif age >= 3: out.append((k, q, 'moved %d turns past the ask' % age, age))
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target', required=True); ap.add_argument('--self', dest='self_sel'); ap.add_argument('--repo'); ap.add_argument('--ledger')
    ap.add_argument('--state-dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state')); ap.add_argument('--quiet-min', type=float, default=10.0)
    ap.add_argument('--row-pattern', default=W.DEFAULT_ROW_PATTERN)
    ap.add_argument('mode', choices=['check', 'finding', 'owed', 'relayed', 'hold', 'answered', 'ask', 'resolved', 'open', 'next', 'sent1', 'nudged', 'conditional', 'fired', 'closed']); ap.add_argument('rest', nargs=argparse.REMAINDER)
    a = ap.parse_args()
    with S.transaction(a.state_dir):
        return run(a, ap)

def run(a, ap):
    W.set_row_pattern(a.row_pattern)
    sess = W.find_session(a.target); state = WK.load_state(a.state_dir)
    if TD.initialize_tracking(state):
        WK.save_state(a.state_dir, state)
    if a.mode == 'answered':
        if len(a.rest) != 1:
            print('REFUSED: answered <target delivery uuid>; the body must match registered obligations')
            return 1
        ok, why = answered_allowed(W.transcript_path(sess), a.self_sel, state, None, a.rest[0])
        if not ok:
            print('REFUSED: %s' % why); return 1
        WK.save_state(a.state_dir, state)
        print('answered: %s' % why); return 0
    if a.mode in ('hold', 'closed'):
        if len(a.rest) < 2:
            print('REFUSED: %s <turn end_ts> "reason"' % a.mode); return 1
        try:
            actor = W.find_session(a.self_sel)['sessionId'] if a.self_sel else None
            changed = TD.record_disposition(sess, actor, state, a.mode, a.rest[0], ' '.join(a.rest[1:]))
        except D.EvidenceError as exc:
            print('REFUSED: %s' % exc); return 1
        if changed:
            WK.save_state(a.state_dir, state)
        print('%s %s with recorded attribution and turn evidence' % (a.mode, a.rest[0])); return 0
    if a.mode == 'relayed':
        ts = a.rest[0] if a.rest else ''
        if not ts: ap.error('relayed <turn end_ts>')
        state['last_relay_ts'] = max(ts, state.get('last_relay_ts') or '')
        WK.save_state(a.state_dir, state)
        print('relayed to the owner up to %s' % state['last_relay_ts']); return 0

    # ---- unanswered questions (owner's primitive, 2026-09-10) ----------------------------
    # A question sent mid-turn is exactly where things stop getting answered: it lands at a
    # turn boundary the other side is already past, and nothing ever asks again. So questions
    # are REGISTERED when asked and stay open until explicitly resolved. There is deliberately
    # no keyword test for "did they answer" -- that is a proxy for the property, and every
    # proxy this project built tonight came apart. Being loud until resolved is the design.
    #
    # Re-send is NOT every poll. The owner's rule: only when the other side goes quiet, or when
    # it is moving past a gate with the question unreacted. Nagging a session mid-work is the
    # behaviour that caused this.
    if a.mode == 'ask':
        key = a.rest[0] if a.rest else ''
        text = ' '.join(a.rest[1:]).strip()
        if not key or not text: ap.error('ask <key> "<the question as sent>"')
        st = W.read_state(sess)
        state.setdefault('open_questions', {})[key] = dict(
            text=text, asked_ts=W.now_iso(), asked_ct=str(st.get('ct')), resends=0, last_send=W.now_iso())
        WK.save_state(a.state_dir, state); print('open question %s registered at ct %s' % (key, st.get('ct'))); return 0
    if a.mode == 'nudged':
        # `resends` existed from the start and NOTHING incremented it, so every question read
        # "0 resend(s)" however often it was re-sent -- a counter that cannot count. Recording a
        # re-send re-arms the clock: it comes back DUE at the next gate crossing or quiet period,
        # so a nudge never becomes permanently silent, only temporarily satisfied.
        key = a.rest[0] if a.rest else ''
        if not key: ap.error('nudged <key>')
        qs = state.get('open_questions') or {}
        q = qs.get(key)
        if q is None: print('no open question %s' % key); return 1
        st = W.read_state(sess)
        q['resends'] = int(q.get('resends', 0)) + 1
        q['last_send'] = W.now_iso(); q['asked_ct'] = str(st.get('ct'))
        WK.save_state(a.state_dir, state)
        print('nudged %s (%d resend(s)); due again at the next gate crossing' % (key, q['resends'])); return 0
    if a.mode in ('conditional', 'fired'):
        key = a.rest[0] if a.rest else ''
        if not key: ap.error('%s <key> [condition]' % a.mode)
        qs = state.get('open_questions') or {}
        q = qs.get(key)
        if q is None: print('no open question %s' % key); return 1
        if a.mode == 'conditional':
            why = ' '.join(a.rest[1:]).strip()
            if not why: ap.error('conditional <key> "<the condition that would make it due>"')
            q['conditional'] = why
            print('PARKED %s -- still open and still listed, but not due until: %s' % (key, why))
        else:
            was = q.pop('conditional', None)
            q['asked_ct'] = str(W.read_state(sess).get('ct'))
            print('FIRED %s (was parked on: %s); due at the next gate crossing' % (key, was or '-'))
        WK.save_state(a.state_dir, state); return 0
    if a.mode == 'resolved':
        key = a.rest[0] if a.rest else ''
        if not key: ap.error('resolved <key>')
        q = (state.get('open_questions') or {}).pop(key, None)
        if q is None: print('no open question %s' % key); return 1
        # ARCHIVE, NEVER DESTROY. This used to `pop` and drop the item on the floor: D13's text
        # survived only because it had been copied into the digest by hand, and two others had to be
        # reconstructed from the transcript. An item's own words are the evidence that it was real
        # and what it asked, so closing it must keep them. Control: tests/test_resolved_archives.py.
        q = dict(q); q['resolved_ts'] = W.now_iso()
        if len(a.rest) > 1: q['resolved_reason'] = ' '.join(a.rest[1:])
        state.setdefault('resolved_questions', {})[key] = q
        WK.save_state(a.state_dir, state)
        print('resolved %s (open since %s, %d resend(s)) -- ARCHIVED, not deleted'
              % (key, q['asked_ts'], q.get('resends', 0))); return 0
    if a.mode == 'open':
        qs = state.get('open_questions') or {}
        st = W.read_state(sess); ct = st.get('ct')
        idle = W.activity_ms(sess) if hasattr(W, 'activity_ms') else None
        quiet = (idle is not None and (W.ms_of_iso(W.now_iso()) - idle) / 60000.0 >= a.quiet_min)
        print('OPEN QUESTIONS: %d' % len(qs))
        for k, q in sorted(qs.items()):
            try: age = int(ct) - int(q.get('asked_ct') or ct)
            except Exception: age = 0
            # gate-crossing: the other side has moved several turns past the ask without it resolving
            gate = age >= 3
            due = quiet or gate
            print('   %-14s %s turns ago%s' % (k, age, '   ** DUE: %s' % ('peer quiet' if quiet else 'moved past a gate') if due else ''))
            print('      %s' % W.short(q['text'], 150))
        if not qs: print('   none')
        return 0

    # ---- ONE AT A TIME (owner, 2026-09-10) --------------------------------------------------
    # "ONE AT A TIME. the whole point is not to confuse the target. when you flush it a bunch of
    #  shit it gets confused. you should be dolling things out one at a time. wherever you get
    #  hooked to actually send a message, thats where you need to put the instruction to check if
    #  its busy, and if it is queue it" -- and "unless I specifically tell you send this immediately".
    #
    # This is the gate that runs BEFORE every send. It is not a flush: it hands back at most ONE
    # item, and only when the target is not mid-turn. An advisory queue was bypassed all evening
    # because nothing sat on the send path; this does.
    if a.mode == 'next':
        verdict, item, why, n = next_item(sess, state)
        if verdict == 'send':
            if why: print(why)
            print('SEND EXACTLY THIS ONE ITEM, then run:  wd.sh sent1 %s' % item.get('id'))
            print('---'); print(item.get('text','')); print('---')
            print('%d other item(s) stay queued.' % (n - 1)); return 0
        print(why); return 1
    if a.mode == 'sent1':
        # sent1 <id> <message_id> --acted-when "<check> <args>"
        #
        # Two requirements, and neither is this session's word for it. The message must be IN the
        # target's transcript, so a mark with no send behind it cannot silence `owed`. And the item
        # must say what the target HAVING ACTED would look like, so it cannot close on delivery --
        # delivery is not action (owner, 2026-09-16). An item marked sent stays owed, reminding,
        # until its check passes; `settle_acted` closes it from the record, and there is
        # deliberately no verb to close one by hand.
        rest = list(a.rest)
        i = rest[0] if rest else ''
        mid = rest[1] if len(rest) > 1 else ''
        matches = [x for x in state.get('owner_queue') or [] if str(x.get('id')) == i]
        if len(matches) > 1:
            print('REFUSED: ambiguous queue id'); return 1
        item = matches[0] if matches else None
        if item is None:
            print('no queued item %s' % i); return 1
        if not mid:
            print('REFUSED: sent1 <id> <message_id>. The message id is what ties this mark to a '
                  'delivery that can be checked.'); return 1
        # The acceptance lives on the ITEM, set when it was queued or with `queue acted-when`.
        # Declaring what done looks like is not a claim that anything was sent, so it is deliberately
        # NOT part of this call: keeping them apart is what lets an item whose acceptance was never
        # recorded get one without re-citing a delivery, and stops this verb growing a second job.
        spec = item.get('acted_when')
        ok, why = acceptance_valid(spec)
        if not ok:
            print('REFUSED: %s. Set it first:  wd.sh queue acted-when %s "<check> <args>"  '
                  '(e.g. "commit <sha>", "grep <path> <regex>", "file <path> <since>", '
                  '"msg-to-watchdog")' % (why, i))
            return 1
        try:
            rec, changed = D.record_delivery(W.transcript_path(sess), a.self_sel, state, mid, queue_id=i)
        except D.EvidenceError as exc:
            print('REFUSED: %s' % exc); return 1
        if changed:
            WK.save_state(a.state_dir, state)
        print('item %s delivered at %s; OWED until %s' % (i, rec['ts'], spec)); return 0
    if a.mode == 'owed':
        # Settle first: an item whose acceptance check now passes is closed BY THE RECORD, on the
        # poll that is already running, so nothing has to remember to close it. Then whatever is
        # still unacted is nagged with the rest -- a sent item is not a finished one.
        closed = settle_acted(a, sess, state)
        unacted = unacted_items(a, sess, state)
        if closed:
            WK.save_state(a.state_dir, state)
        rows = owed(sess, state, a.self_sel)
        broken = [r for r in rows if r['declared'] and not r['dispatched']]
        due_now = due_questions(sess, state, a.quiet_min)
        # The nudge rides INSIDE owed, in the headline the monitor already reads (owner,
        # 2026-09-11: "throw nudge inside owed so it fires consistently... You should be as
        # annoying to it as the hook is to you lol"). Listing due questions in a section
        # further down was not an alarm: 21 questions sat DUE for 30+ turns with 0 resends,
        # because nothing counted them as owed and nothing recorded a re-send either.
        print('OWED completed turns: %d  DUE NUDGES: %d' % (len(rows), len(due_now)))
        for r in rows: print('   %s  [%s]  %s' % (r['ts'], r['why'], r['head']))
        for k, q, why, age in due_now:
            print('   NUDGE %-22s %s  (%d resend(s))  -- re-send it, then: wd.sh nudged %s'
                  % (k, why, q.get('resends', 0), k))
        print('DECLARED an action and made no dispatch: %d' % len(broken))
        for r in broken: print('   %s  declared: %s' % (r['ts'], ' | '.join(r['declared'])))
        if not rows: print('   nothing owed')
        # In the headline the monitor already reads, for the same reason the nudge is: a section
        # further down is not an alarm.
        print('SENT, NOT YET ACTED ON: %d%s' % (len(unacted),
              ('  (closed this poll: %s)' % ', '.join(closed)) if closed else ''))
        for u in unacted:
            print('   %s sent %s -- waiting on: %s' % (u['id'], u['sent'], u['spec']))
            print('      %s: %s' % (u['status'], W.short(u['evidence'], 300)))
        # The owner's primitive, 2026-09-10: "keep track of what you've requested and what the
        # engine has and hasn't answered... keep reminding yourself, and not interrupting, but
        # nudging for answers when you don't get them." So open questions ride on the check that
        # already polls, rather than needing to be remembered -- being remembered is what failed.
        qs = state.get('open_questions') or {}
        due = due_questions(sess, state, a.quiet_min)
        print('UNANSWERED requests: %d (due to nudge: %d)' % (len(qs), len(due)))
        for k, q, why, age in due:
            print('   ** NUDGE %-14s asked %s turns ago -- %s' % (k, age, why))
            print('      %s' % W.short(q['text'], 150))
        for k in sorted(set(qs) - {d[0] for d in due}):
            print('   (open, not due) %s' % k)
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
                                          asks_reply=False, is_poke=False, message=f['message'],
                                          held=(None if f['status'] == 'send' else f['status']))
    WK.append_findings_md(a.state_dir, [dict(id=f['id'], wake=state.get('wake_count'), wake_ts=W.now_iso(), turn_ct=st['ct'], cls=cls, status=f['status'], message=f['message'])])
    WK.log_line(a.state_dir, '%s FINDING %s %s %s (model-selected, script-verified)' % (W.now_iso(), f['id'], cls, f['status']))
    WK.save_state(a.state_dir, state)
    print('%s [%s] %s' % (f['id'], f['status'], cls)); print(f['message'])
    if f['status'] == 'send': print('(send verbatim, then: wd.sh sent %s <message_id>)' % f['id'])
    return 0

if __name__ == '__main__':
    sys.exit(main())
