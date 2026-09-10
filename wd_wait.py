#!/usr/bin/python3
"""wd_wait.py -- the hook. Blocks until the target session ends a turn, until work it launched stalls, or until
a reply the watchdog asked for is overdue. Event-driven (kqueue on the session-state directory and the
transcript) with a 15 s re-check backstop. Prints ONE line per event and, by default, exits (re-arm it after
every wake); --follow streams.

  TURN ct=765->766 ts=<utc>                          completedTurns moved and the transcript agrees the turn ended
  TURN_END ts=<utc> ct=766->766 opener=<kind>        the transcript ended the turn; the counter did not move within 6 s
                                                     (measured for turns in a session the app is not attached to, e.g.
                                                     peer-opened turns: the count arrives ~3 s after the NEXT delivery)
  INTERRUPTED ts=<utc> ct=a->b                       '[Request interrupted by user' marker appended
  API_ERROR ts=<utc> ct=a->b text=<head>             assistant record with isApiErrorMessage appended
  CONTEXT_EXCEEDED cec=1->2 ct=<n>                   contextExceededCount moved (hard API-edge failure)
  STALL kind=bg id=<taskid> idle_min=<n> reason=<r>  after --stale-after of silence, an in-flight item (from state/state.json)
  STALL kind=codex thread=<id> idle_min=<n> reason=<r>   has shown no progress for --stall-min: no live process and no output
                                                     growth (bg), or no rollout event (codex). One line per item per episode.
  IDLE idle_min=<n> ct=<n>                           the target stopped: silent past --idle-after with NOTHING running,
                                                     no work in flight and no reply owed. The wake decides whether that
                                                     contradicts a standing instruction; the hook only spots the state.
  REPLY_OVERDUE message_id=<id> sent=<ts>            the target has not replied to the watchdog's question within its deadline
  HEARTBEAT idle_min=<n> ct=<n> inflight=<k>          --max-wait reached with nothing to report: still watching,
                                                     re-arm. Default 60s, so the loop comes back every minute
                                                     rather than blocking for hours and losing a turn end.

Exactly one event per transcript turn: a counter bump that lands while a turn is open, or after the turn was
already reported, is logged to stderr as a lagged count and NOT emitted. Silent interrogation: while the target
is idle past --stale-after and state.json lists in-flight work, every backstop tick re-checks each item's
progress and says nothing until one stalls. Read-only on everything except its own memory file,
state/wait_memory.json (which stalls and overdue replies it has already reported, so a re-armed hook stays quiet).
"""
import os, sys, json, time, select, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wd_lib as W

def log(msg):
    sys.stderr.write('[wd_wait %s] %s\n' % (W.now_iso(), msg)); sys.stderr.flush()

def emit(line):
    sys.stdout.write(line + '\n'); sys.stdout.flush()

class Watch(object):
    def __init__(self, sess, state_dir, stale_after, stall_min, self_sess=None, idle_after=0):
        self.sess = sess; self.state_dir = state_dir; self.stale_after = stale_after; self.stall_min = stall_min
        self.idle_after = idle_after; self.idle_reported_for = None
        self.self_sess = self_sess
        self.tpath = W.transcript_path(sess)
        self.kq = select.kqueue(); self.fds = {}
        self._arm(os.path.dirname(sess['state_path']), 'statedir')
        self._arm(self.tpath, 'transcript')
        st = W.read_state(sess)
        self.ct, self.cec, self.last_act = st['ct'], st['cec'], st['lastActivityAt']
        self.pos = os.path.getsize(self.tpath); self.partial = b''
        self.prev_pid, self.turn_open = self._last_turn()
        self.last_record_ms = self._last_record_ms()
        self.cur_opener_kind = None; self.pending_tool = False
        self.event_emitted_for_turn = not self.turn_open
        self.prog = {}          # in-flight item id -> (signature, last_change_epoch)
        self.stalled = set()    # item ids already reported this episode
        self.overdue_emitted = None
        self.mem_path = os.path.join(state_dir, 'wait_memory.json')   # the hook's own memory across re-arms (its only write)
        self.mem = (W.read_json_retry(self.mem_path) if os.path.exists(self.mem_path) else None) or {}
        self.stalled = set(self.mem.get('stalled', {}).keys()); self.overdue_emitted = self.mem.get('overdue_emitted')

    def _save_mem(self):
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            tmp = self.mem_path + '.tmp'
            with open(tmp, 'w') as f: json.dump(self.mem, f, indent=1)
            os.replace(tmp, self.mem_path)
        except OSError as e:
            log('cannot save hook memory: %s' % e)

    def _arm(self, path, tag):
        flags = getattr(os, 'O_EVTONLY', os.O_RDONLY)
        try: fd = os.open(path, flags)
        except OSError as e: log('cannot watch %s: %s' % (path, e)); return
        ev = select.kevent(fd, filter=select.KQ_FILTER_VNODE, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                           fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE)
        self.kq.control([ev], 0, 0); self.fds[fd] = (path, tag)

    def _last_turn(self):
        try:
            _, turns = W.last_turns(self.sess, n=1)
            return (turns[-1].pid, turns[-1].end_state == 'open') if turns else (None, False)
        except Exception:
            return None, False

    def _last_record_ms(self):
        """The transcript's own last-record time. `lastActivityAt` in the app's session state does NOT
        advance during a turn the app is not attached to -- a peer-opened turn freezes it at the moment
        of delivery -- so idle measured from it reads minutes of silence while the target is working.
        Measured 2026-09-10: lastActivityAt 07:06:35 against a transcript record at 07:12:18, 14 tool
        calls into an open turn. The transcript is the truth, the same way it is for turn boundaries."""
        try:
            _, turns = W.last_turns(self.sess, n=1)
            return W.ms_of_iso(turns[-1].end_ts) if turns else None
        except Exception:
            return None

    def activity_ms(self):
        """Whichever source saw the target most recently. Never regresses."""
        return max(self.last_act or 0, self.last_record_ms or 0) or None

    def _rearm_transcript(self):
        for fd, (path, tag) in list(self.fds.items()):
            if tag != 'transcript': continue
            try:
                if os.fstat(fd).st_ino != os.stat(path).st_ino:
                    os.close(fd); del self.fds[fd]; self._arm(path, 'transcript'); self.pos = 0; self.partial = b''
            except OSError:
                pass

    def new_transcript_records(self):
        try: size = os.path.getsize(self.tpath)
        except OSError: return []
        if size < self.pos: self.pos = 0; self.partial = b''
        if size == self.pos: return []
        with open(self.tpath, 'rb') as f:
            f.seek(self.pos); data = self.partial + f.read(size - self.pos)
        self.pos = size
        lines = data.split(b'\n'); self.partial = lines[-1]
        recs = []
        for ln in lines[:-1]:
            if not ln.strip(): continue
            try: recs.append(json.loads(ln.decode('utf-8', 'replace')))
            except ValueError: pass
        return recs

    def state_json(self):
        p = os.path.join(self.state_dir, 'state.json')
        return (W.read_json_retry(p) if os.path.exists(p) else None) or {}

    # ---- silent interrogation of in-flight work
    def _progress(self, item):
        """(signature, alive, assessable, detail) for one in-flight item."""
        now = time.time()
        if item.get('kind') == 'bg':
            s_ = W.task_output_status(item.get('output_file'))
            procs = W.proc_matches(item.get('command', ''), W.live_children(self.sess))
            sig = (s_.get('size'), s_.get('mtime'), len(procs))
            if not s_.get('exists'): return sig, bool(procs), bool(procs), 'no output file; live processes %d' % len(procs)
            return sig, bool(procs), True, 'output %s bytes, mtime %s, live processes %d' % (s_.get('size'), s_.get('mtime'), len(procs))
        ts_ = W.codex_thread_state(item.get('thread') or '')
        if not ts_.get('found'): return None, False, False, 'no rollout found for thread %s' % (item.get('thread') or '')[:8]
        sig = (ts_.get('size'), ts_.get('mtime'), ts_.get('in_flight'))
        if not ts_.get('in_flight'): return sig, False, True, 'rollout shows no turn in flight (last complete %s)' % ts_.get('last_complete')
        return sig, True, True, 'rollout in flight, last event %s' % ts_.get('last_event')

    def interrogate(self):
        out = []
        st = self.state_json()
        items = st.get('in_flight') or []
        now = time.time()
        live_ids = set()
        for it in items:
            iid = it.get('id') or (it.get('thread') or '')[:8]
            live_ids.add(iid)
            sig, alive, assessable, detail = self._progress(it)
            prev = self.prog.get(iid)
            if prev is None and iid in self.stalled and self.mem.get('stalled', {}).get(iid) == list(sig or []):
                self.prog[iid] = (sig, 0)   # already reported in an earlier run with this very signature; do not re-emit
                continue
            if prev is None or prev[0] != sig:
                # first sight: date the item by its own artefact age, not by when we first looked
                if prev is None:
                    age_ref = W.epoch_from_iso(it.get('launched_ts')) or now
                    mt = None
                    if it.get('kind') == 'bg' and it.get('output_file') and os.path.exists(it['output_file']):
                        mt = os.path.getmtime(it['output_file'])
                    self.prog[iid] = (sig, max(mt or 0, age_ref) if mt else age_ref)
                else:
                    self.prog[iid] = (sig, now)
                if iid in self.stalled:
                    self.stalled.discard(iid); self.mem.setdefault('stalled', {}).pop(iid, None); self._save_mem()
                if prev is not None: continue
            last_change = self.prog[iid][1]
            idle_min = (now - last_change) / 60.0
            stalled = (not alive) and idle_min >= self.stall_min
            if it.get('kind') == 'codex' and assessable and sig and not sig[2]:
                stalled = False   # the codex turn finished; the target's own notification will wake it
            if stalled and iid not in self.stalled:
                self.stalled.add(iid); self.mem.setdefault('stalled', {})[iid] = list(sig or []); self._save_mem()
                if it.get('kind') == 'bg':
                    out.append('STALL kind=bg id=%s idle_min=%d reason=%s' % (iid, idle_min, detail.replace(' ', '_')))
                else:
                    out.append('STALL kind=codex thread=%s idle_min=%d reason=%s' % (iid, idle_min, detail.replace(' ', '_')))
        for iid in list(self.prog):
            if iid not in live_ids:
                self.prog.pop(iid, None); self.stalled.discard(iid)
                if iid in self.mem.get('stalled', {}): self.mem['stalled'].pop(iid, None); self._save_mem()
        return out

    def reply_check(self):
        st = self.state_json()
        aw = st.get('awaiting_reply')
        if not aw or aw.get('poked') or not self.self_sess: return []
        if time.time() < (W.epoch_from_iso(aw.get('deadline')) or 0): return []
        if self.overdue_emitted == aw.get('message_id'): return []
        if W.peer_replies(self.self_sess, self.sess['sessionId'], aw.get('sent_ts')): return []
        self.overdue_emitted = aw.get('message_id'); self.mem['overdue_emitted'] = self.overdue_emitted; self._save_mem()
        return ['REPLY_OVERDUE message_id=%s sent=%s' % (aw.get('message_id'), aw.get('sent_ts'))]

    def poll(self, timeout):
        try: self.kq.control(None, 32, timeout)
        except OSError: pass
        self._rearm_transcript()
        out = []
        st = W.read_state(self.sess)
        recs = self.new_transcript_records()
        ended = None
        for r in recs:
            ts = r.get('timestamp')
            if ts:
                ms = W.ms_of_iso(ts)
                if ms and ms > (self.last_record_ms or 0): self.last_record_ms = ms
            ty = r.get('type')
            if ty == 'user':
                m = r.get('message') or {}; c = m.get('content')
                is_tr = bool(W._blocks(c, 'tool_result'))
                if not is_tr and r.get('promptId') != self.prev_pid:
                    self.cur_opener_kind = (r.get('origin') or {}).get('kind') or 'other'
                    self.turn_open = True; self.event_emitted_for_turn = False; ended = None
                self.prev_pid = r.get('promptId')
                if is_tr: self.pending_tool = False
                if W.MARKER in W._text_of(c):
                    out.append(('INTERRUPTED', 'ts=%s' % r.get('timestamp'))); self.turn_open = False
            elif ty == 'assistant':
                m = r.get('message') or {}; c = m.get('content')
                if r.get('isApiErrorMessage'):
                    out.append(('API_ERROR', 'ts=%s text=%s' % (r.get('timestamp'), W.short(W._text_of(c), 80).replace(' ', '_')))); self.turn_open = False
                if W._blocks(c, 'tool_use'):
                    self.pending_tool = True; self.turn_open = True
                elif m.get('stop_reason') in ('end_turn', 'stop_sequence', 'max_tokens') and W._text_of(c).strip():
                    self.pending_tool = False; self.turn_open = False; ended = r.get('timestamp')
        if (out or ended) and not self.event_emitted_for_turn:
            deadline = time.time() + 6
            while time.time() < deadline and st['ct'] == self.ct:
                try: self.kq.control(None, 32, max(0.05, deadline - time.time()))
                except OSError: pass
                st = W.read_state(self.sess)
        if ended and not out and st['ct'] == self.ct and not self.event_emitted_for_turn:
            out.append(('TURN_END', 'ts=%s opener=%s' % (ended, self.cur_opener_kind)))
        lines = []
        if st['ct'] != self.ct:
            if out and not self.event_emitted_for_turn:
                lines.append('%s %s ct=%s->%s' % (out[0][0], out[0][1], self.ct, st['ct'])); self.event_emitted_for_turn = True
            elif self.event_emitted_for_turn or self.turn_open:
                log('counter %s->%s while %s; treated as a lagged count, not an event' % (self.ct, st['ct'], 'the turn is open' if self.turn_open else 'this turn was already reported'))
            else:
                lines.append('TURN ct=%s->%s ts=%s' % (self.ct, st['ct'], W.now_iso())); self.event_emitted_for_turn = True
            self.ct = st['ct']
        elif out and not self.event_emitted_for_turn:
            lines.append('%s %s ct=%s->%s' % (out[0][0], out[0][1], self.ct, st['ct'])); self.event_emitted_for_turn = True
        if st['cec'] != self.cec:
            lines.append('CONTEXT_EXCEEDED cec=%s->%s ct=%s' % (self.cec, st['cec'], st['ct'])); self.cec = st['cec']
        if st['lastActivityAt'] != self.last_act:
            self.last_act = st['lastActivityAt']
        _act = self.activity_ms()
        if self.stale_after and _act and (time.time() - _act / 1000.0) > self.stale_after:
            lines.extend(self.interrogate())
        lines.extend(self.reply_check())
        lines.extend(self.idle_check())
        return lines

    def idle_check(self):
        """The target stopped: silent past --idle-after, nothing running, nothing in flight, no reply owed.
        The hook only reports the STATE; whether it contradicts a standing instruction is the wake's call."""
        act = self.activity_ms()
        if not self.idle_after or not act: return []
        idle = time.time() - act / 1000.0
        if idle < self.idle_after: return []
        if self.idle_reported_for == act: return []
        if self.turn_open: return []   # working, not stopped -- whatever the app's counters say
        st = self.state_json()
        if st.get('in_flight'): return []
        if (st.get('awaiting_reply') or {}) and not (st.get('awaiting_reply') or {}).get('poked'): return []
        try:
            if W.live_children(self.sess): return []
        except Exception:
            return []
        self.idle_reported_for = act
        return ['IDLE idle_min=%d ct=%s' % (idle / 60.0, self.ct)]

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target', required=True, help='session id (local_..), cli session id, or unique title substring')
    ap.add_argument('--self', dest='self_sel', help='the watchdog session itself (for reply tracking)')
    ap.add_argument('--state-dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state'))
    ap.add_argument('--stale-after', type=int, default=1800, help='seconds of target silence before in-flight work is interrogated (0 = never)')
    ap.add_argument('--stall-min', type=float, default=20.0, help='minutes without progress that make an in-flight item a STALL')
    ap.add_argument('--idle-after', type=float, default=0, help='seconds of silence with NOTHING running that emit IDLE (0 = never)')
    ap.add_argument('--max-wait', type=int, default=60, help='return after this many seconds with nothing to report (exit 3, prints HEARTBEAT) so the loop re-arms and cannot lose a turn end; 0 = unbounded')
    ap.add_argument('--backstop', type=float, default=15.0, help='kqueue timeout in seconds (re-check cadence when no events arrive)')
    ap.add_argument('--follow', action='store_true', help='stream events instead of exiting after the first')
    ap.add_argument('--audit', action='store_true', help='backstop mode: ignore the event stream, wait --max-wait, then report the target state whatever it is (a second hook that cannot be lost)')
    a = ap.parse_args()
    sess = W.find_session(a.target)
    self_sess = W.find_session(a.self_sel) if a.self_sel else None
    w = Watch(sess, a.state_dir, a.stale_after, a.stall_min, self_sess, idle_after=a.idle_after)
    log('watching %s (%s) ct=%s cec=%s stale_after=%ss stall_min=%s idle_after=%ss' % (sess['title'], sess['sessionId'], w.ct, w.cec, a.stale_after, a.stall_min, a.idle_after))
    t0 = time.time()
    if a.audit:
        # a slow, dumb second hook: sleep out the window, then report state unconditionally. It shares none of
        # the event logic, so a bug or a wrong assumption in that logic cannot silence it.
        while time.time() - t0 < a.max_wait:
            try: w.kq.control(None, 1, min(30.0, a.max_wait - (time.time() - t0)))
            except OSError: pass
        st = W.read_state(sess); sj = w.state_json()
        w.last_act = st['lastActivityAt']; w.last_record_ms = w._last_record_ms()
        _open = w._last_turn()[1]
        idle = (time.time() - (w.activity_ms() or 0) / 1000.0) / 60.0
        try: procs = len(W.live_children(sess))
        except Exception: procs = -1
        emit('AUDIT ct=%s idle_min=%d live=%d inflight=%d cec=%s open=%d' % (st['ct'], idle, procs, len(sj.get('in_flight') or []), st['cec'], 1 if _open else 0))
        return 0
    while True:
        for line in w.poll(a.backstop):
            emit(line)
            if not a.follow: return 0
        if a.max_wait and time.time() - t0 > a.max_wait:
            _a = w.activity_ms()
            idle = int(time.time() - _a / 1000.0) if _a else -1
            st_ = w.state_json()
            emit('HEARTBEAT idle_min=%d ct=%s inflight=%d open=%d' % (idle / 60.0, w.ct, len(st_.get('in_flight') or []), 1 if w.turn_open else 0)); return 3

if __name__ == '__main__':
    sys.exit(main())
