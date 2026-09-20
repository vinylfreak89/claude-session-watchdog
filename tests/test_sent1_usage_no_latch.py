"""`sent1` with no message id is a usage error and must not latch the send gate.

2026-09-20: a shell variable came back empty, sent1 recorded "missing target delivery uuid"
as a receipt failure, and every send stopped. An id that IS given still latches when it
cannot be resolved; the control keeps that.
"""
import json, os, shutil, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, 'config.json')))
BASE = ['--target', CFG['target'], '--self', CFG['self'], '--row-pattern', CFG.get('row_pattern', r'[A-Z]{1,2}\d{1,3}')]


def main():
    tmp = tempfile.mkdtemp()
    try:
        st = os.path.join(tmp, 'state'); shutil.copytree(os.path.join(ROOT, 'state'), st)
        path = os.path.join(st, 'state.json'); s = json.load(open(path))
        s.pop('receipt_recording_failure', None)
        queued = [q for q in s.get('owner_queue') or [] if not q.get('sent')]
        assert queued, 'no unsent queued item to exercise'
        ident = str(queued[0]['id'])
        json.dump(s, open(path, 'w'))
        run = lambda *args: subprocess.run([sys.executable, os.path.join(ROOT, 'wd_check.py')] + BASE
                                           + ['--state-dir', st, 'sent1'] + list(args),
                                           capture_output=True, text=True, cwd=ROOT)
        p = run(ident)
        assert 'REFUSED' in p.stdout, p.stdout + p.stderr
        assert 'receipt_recording_failure' not in json.load(open(path)), 'empty id latched the gate'
        p = run(ident, '00000000-0000-0000-0000-000000000000')
        assert 'receipt_recording_failure' in json.load(open(path)), 'an unresolvable id no longer latches: ' + p.stdout
        print('ok')
    finally:
        shutil.rmtree(tmp)


if __name__ == '__main__':
    main()
