"""`relayed` advances a watermark with max(), so a non-timestamp argument can silence it.

2026-09-20: `wd.sh relayed --help` ran the verb instead of printing usage. It happened to
be harmless -- "--help" sorts BELOW any ISO timestamp, so max() kept the existing value --
but the handler accepted any string, and one sorting above (a typo, a pasted word) would
have marked every owed turn relayed in a single call.

The defect is synthesised here, not borrowed: the test builds its own state with a known
watermark and drives junk that sorts above it. It cannot go quiet because something
elsewhere was fixed.
"""
import json, os, shutil, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, 'config.json')))
BASE = ['--target', CFG['target'], '--self', CFG['self'], '--row-pattern', CFG.get('row_pattern', '[A-Z]{1,2}\\d{1,3}')]
MARK = '2026-09-20T10:05:18.290Z'


def relayed(st, arg):
    return subprocess.run([sys.executable, os.path.join(ROOT, 'wd_check.py')] + BASE +
                          ['--state-dir', st, 'relayed', arg], capture_output=True, text=True, cwd=ROOT)


def watermark(st):
    return json.load(open(os.path.join(st, 'state.json'))).get('last_relay_ts')


def main():
    tmp = tempfile.mkdtemp()
    try:
        st = os.path.join(tmp, 'state'); shutil.copytree(os.path.join(ROOT, 'state'), st)
        s = json.load(open(os.path.join(st, 'state.json')))
        s['last_relay_ts'] = MARK
        json.dump(s, open(os.path.join(st, 'state.json'), 'w'))

        # Junk that sorts ABOVE the watermark is the dangerous shape: without the guard
        # each of these would advance it and discharge every owed turn.
        for bad in ('zzz', 'yesterday', 'relayed', 'now', 'today'):
            assert bad > MARK, 'test is not exercising the defect: %r sorts below %r' % (bad, MARK)
            p = relayed(st, bad)
            assert 'REFUSED' in p.stdout, (bad, p.stdout, p.stderr)
            assert watermark(st) == MARK, 'junk %r moved the watermark to %r' % (bad, watermark(st))

        # Option-shaped arguments are refused too, though these sort below and were never
        # able to move it -- the historical `--help` call is in this class.
        for bad in ('--help', '-h', 'TURN_END', '2026'):
            p = relayed(st, bad)
            assert 'REFUSED' in p.stdout, (bad, p.stdout, p.stderr)
            assert watermark(st) == MARK, bad

        # Control: a real later timestamp still advances it, and an earlier one never
        # moves it backwards.
        later = '2026-09-20T11:00:00.000Z'
        p = relayed(st, later)
        assert watermark(st) == later, (p.stdout, watermark(st))
        relayed(st, MARK)
        assert watermark(st) == later, 'an earlier timestamp moved the watermark backwards'
        print('ok')
    finally:
        shutil.rmtree(tmp)


if __name__ == '__main__':
    main()
