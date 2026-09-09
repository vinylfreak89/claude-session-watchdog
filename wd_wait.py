#!/usr/bin/python3
"""wd_wait.py -- block until the target session ends a turn, or goes stale with work in flight.

Event-driven: kqueue vnode events on the session-state directory and on the transcript file, with a
15 s re-check backstop (liveness only; the events are what wake it). Prints ONE line per event:

  TURN ct=765->766 ts=<utc>                 completedTurns incremented (normal turn end)
  INTERRUPTED ts=<utc> ct=766->766          '[Request interrupted by user' marker appended (ct folded if it moved within 6 s)
  API_ERROR ts=<utc> ct=... text=<head>     assistant record with isApiErrorMessage appended
  CONTEXT_EXCEEDED cec=1->2 ct=...          contextExceededCount incremented (hard API-edge failure)
  STALE idle_s=<n> inflight=<k> ct=...      lastActivityAt older than --stale-after AND state/state.json lists in-flight work
  TIMEOUT idle_s=<n> ct=...                 --max-wait reached without an event (one-shot only; exit 3)

One-shot (default): exit 0 after the first event line.  --follow: stream forever (use with Monitor persistent=true).
Read-only. Never touches the target.
"""
import os, sys, json, time, select, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wd_lib as W

def log(msg):
    sys.stderr.write('[wd_wait %s] %s\n' % (W.now_iso(), msg)); sys.stderr.flush()

def emit(line):
    sys.stdout.write(line + '\n'); sys.stdout.flush()

class Watch(object):
    def __init__(self, sess, state_dir, stale_after):
        self.sess = sess; self.state_dir = state_dir; self.stale_after = stale_after
        self.tpath = W.transcript_path(sess)
        self.kq = select.kqueue()
        self.fds = {}
        self._arm(os.path.dirname(sess['state_path']), 'statedir')
        self._arm(self.tpath, 'transcript')
        st = W.read_state(sess)
        self.ct, self.cec, self.last_act = st['ct'], st['cec'], st['lastActivityAt']
        self.pos = os.path.getsize(self.tpath); self.partial = b''
        self.stale_reported_for = None

    def _arm(self, path, tag):
        flags = getattr(os, 'O_EVTONLY', os.O_RDONLY)
        try:
            fd = os.open(path, flags)
        except OSError as e:
            log('cannot watch %s: %s' % (path, e)); return
        ev = select.kevent(fd, filter=select.KQ_FILTER_VNODE, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                           fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE)
        self.kq.control([ev], 0, 0)
        self.fds[fd] = (path, tag)

    def _rearm_transcript(self):
        # the transcript could in principle be rotated; re-open if the inode changed
        for fd, (path, tag) in list(self.fds.items()):
            if tag != 'transcript': continue
            try:
                if os.fstat(fd).st_ino != os.stat(path).st_ino:
                    os.close(fd); del self.fds[fd]; self._arm(path, 'transcript'); self.pos = 0; self.partial = b''
            except OSError:
                pass

    def new_transcript_records(self):
        try:
            size = os.path.getsize(self.tpath)
        except OSError:
            return []
        if size < self.pos:
            self.pos = 0; self.partial = b''
        if size == self.pos: return []
        with open(self.tpath, 'rb') as f:
            f.seek(self.pos); data = self.partial + f.read(size - self.pos)
        self.pos = size
        lines = data.split(b'\n')
        self.partial = lines[-1]
        recs = []
        for ln in lines[:-1]:
            if not ln.strip(): continue
            try: recs.append(json.loads(ln.decode('utf-8', 'replace')))
            except ValueError: pass
        return recs

    def in_flight_count(self):
        p = os.path.join(self.state_dir, 'state.json')
        d = W.read_json_retry(p) if os.path.exists(p) else None
        return len((d or {}).get('in_flight') or [])

    def poll(self, timeout):
        """Wait for events; return a list of event lines (possibly empty)."""
        try:
            self.kq.control(None, 32, timeout)
        except OSError:
            pass
        self._rearm_transcript()
        out = []
        st = W.read_state(self.sess)
        recs = self.new_transcript_records()
        for r in recs:
            if r.get('type') == 'user':
                txt = W._text_of((r.get('message') or {}).get('content'))
                if W.MARKER in txt:
                    out.append(('INTERRUPTED', 'ts=%s' % r.get('timestamp')))
            elif r.get('type') == 'assistant' and r.get('isApiErrorMessage'):
                out.append(('API_ERROR', 'ts=%s text=%s' % (r.get('timestamp'), W.short(W._text_of((r.get('message') or {}).get('content')), 80).replace(' ', '_'))))
        if out:   # fold a counter move that follows within 6 s
            deadline = time.time() + 6
            while time.time() < deadline and st['ct'] == self.ct:
                try: self.kq.control(None, 32, max(0.05, deadline - time.time()))
                except OSError: pass
                st = W.read_state(self.sess)
        lines = []
        if st['ct'] != self.ct:
            if not out:
                lines.append('TURN ct=%s->%s ts=%s' % (self.ct, st['ct'], W.now_iso()))
            else:
                lines.append('%s %s ct=%s->%s' % (out[0][0], out[0][1], self.ct, st['ct']))
            self.ct = st['ct']
        elif out:
            lines.append('%s %s ct=%s->%s' % (out[0][0], out[0][1], self.ct, st['ct']))
        if st['cec'] != self.cec:
            lines.append('CONTEXT_EXCEEDED cec=%s->%s ct=%s' % (self.cec, st['cec'], st['ct']))
            self.cec = st['cec']
        if st['lastActivityAt'] != self.last_act:
            self.last_act = st['lastActivityAt']; self.stale_reported_for = None
        if self.stale_after and self.last_act:
            idle = time.time() - self.last_act / 1000.0
            if idle > self.stale_after and self.stale_reported_for != self.last_act:
                k = self.in_flight_count()
                if k:
                    lines.append('STALE idle_s=%d inflight=%d ct=%s' % (idle, k, st['ct']))
                    self.stale_reported_for = self.last_act
        return lines

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target', required=True, help='session id (local_..), cli session id, or unique title substring')
    ap.add_argument('--state-dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state'))
    ap.add_argument('--stale-after', type=int, default=3 * 3600, help='seconds of lastActivityAt silence, with work in flight, that counts as stale (0 = never)')
    ap.add_argument('--max-wait', type=int, default=6 * 3600, help='one-shot: give up after this many seconds (exit 3); 0 = unbounded')
    ap.add_argument('--backstop', type=float, default=15.0, help='kqueue timeout in seconds (re-check cadence when no events arrive)')
    ap.add_argument('--follow', action='store_true', help='stream events forever instead of exiting after the first')
    a = ap.parse_args()
    sess = W.find_session(a.target)
    w = Watch(sess, a.state_dir, a.stale_after)
    log('watching %s (%s) ct=%s cec=%s transcript=%s' % (sess['title'], sess['sessionId'], w.ct, w.cec, w.tpath))
    t0 = time.time()
    while True:
        for line in w.poll(a.backstop):
            emit(line)
            if not a.follow:
                return 0
        if not a.follow and a.max_wait and time.time() - t0 > a.max_wait:
            idle = int(time.time() - (w.last_act or 0) / 1000.0) if w.last_act else -1
            emit('TIMEOUT idle_s=%d ct=%s' % (idle, w.ct)); return 3

if __name__ == '__main__':
    sys.exit(main())
