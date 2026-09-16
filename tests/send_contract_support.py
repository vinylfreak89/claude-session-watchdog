"""Synthetic transcript and CLI fixtures; no production decision functions are mocked."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wd_check as C
import wd_lib as W
import wd_wake as K

SELF = 'local_control_watchdog'
TARGET = 'local_control_target'

def ts(second):
    return '2026-09-16T10:%02d:%02dZ' % divmod(second, 60)

class ContractCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wd-contract-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / 'state'
        self.state_dir.mkdir()
        self.tx = self.root / 'target.jsonl'
        self.tx.write_text('')
        self.mine = self.root / 'watchdog.jsonl'
        self.mine.write_text('')
        self.sess = dict(cwd=str(self.root), cli='control-cli', sessionId=TARGET, title='control')
        self.self_sess = dict(cwd=str(self.root), cli='control-self', sessionId=SELF)
        K.save_state(str(self.state_dir), dict(last_relay_ts=ts(599)))
        self.clock = 5
        self.serial = 0
        self.turn('initial', 0, 1)
        self.addCleanup(patch.stopall)
        patch.object(W, 'find_session', side_effect=lambda sel: self.self_sess if sel == SELF else self.sess).start()
        patch.object(W, 'transcript_path', side_effect=lambda sess: str(self.mine if sess['sessionId'] == SELF else self.tx)).start()
        patch.object(W, 'read_state', return_value=dict(ct=1, lastActivityAt=0, cec=0)).start()
        patch.object(W, 'live_children', return_value=[]).start()
        patch.object(W, 'session_pids', return_value=[]).start()
        self.tasks = self.root / 'control-cli' / 'tasks'
        self.tasks.mkdir(parents=True)
        patch.object(W, 'tasks_dir', return_value=str(self.tasks)).start()

    def records(self, *records, path=None):
        with (path or self.tx).open('a') as f:
            for r in records:
                f.write(json.dumps(r) + '\n')

    def turn(self, ident, start, end):
        self.records(dict(type='user', uuid='u-' + ident, promptId=ident, timestamp=ts(start),
                          origin=dict(kind='human'), message=dict(role='user', content='Synthetic request')),
                     dict(type='assistant', uuid='a-' + ident, timestamp=ts(end),
                          message=dict(role='assistant', content=[dict(type='text', text='Synthetic result')], stop_reason='end_turn')))

    def deliver(self, body, ident='delivery-1', at=10, origin=True, blocks=False):
        content = '<cross-session-message from="%s" name="Control">%s</cross-session-message>' % (SELF, body)
        r = dict(type='user', uuid=ident, promptId=ident, timestamp=ts(at), isMeta=True,
                 message=dict(role='user', content=[dict(type='text', text=content)] if blocks else content))
        if origin:
            r['origin'] = dict(kind='peer', **{'from': SELF})
        self.records(r)
        return ident

    def tool(self, name, inp, result='ok', at=20, error=False):
        self.serial += 1
        uid = 'tool-%d' % self.serial
        self.records(dict(type='assistant', timestamp=ts(at), message=dict(role='assistant',
                          content=[dict(type='tool_use', id=uid, name=name, input=inp)], stop_reason='tool_use')),
                     dict(type='user', promptId='delivery-1', timestamp=ts(at + 1),
                          message=dict(role='user', content=[dict(type='tool_result', tool_use_id=uid,
                                                               content=result, is_error=error)])))
        return uid

    def write_target(self, name, content, at=20):
        path = self.root / name
        path.write_text(content)
        epoch = W.epoch_from_iso(ts(at + 1))
        os.utime(path, (epoch, epoch))
        self.tool('Write', dict(file_path=str(path), content=content), at=at)

    def state(self):
        return K.load_state(str(self.state_dir))

    def cli(self, module, *args):
        base = [module.__file__, '--state-dir', str(self.state_dir), '--target', TARGET, '--self', SELF,
                '--repo', str(self.root), '--ledger', 'ledger.md']
        out = io.StringIO()
        err = io.StringIO()
        with patch.object(sys, 'argv', base + list(args)), patch.object(W, 'now_iso', return_value=ts(self.clock)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = module.main()
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 1
                if not isinstance(e.code, int): out.write(str(e.code))
        # JSON stdout is a separate protocol from diagnostics. In particular,
        # Python 3.14 warns on utcfromtimestamp under unittest's warning filter.
        self.cli_stderr = err.getvalue()
        return rc, out.getvalue() + (self.cli_stderr if rc != 0 else '')

    def queue(self, text='Create artifact', spec='file artifact.txt'):
        rc, out = self.cli(K, '--queue-add', text, '--acted-when', spec)
        self.assertEqual(rc, 0, out)
        items = self.state().get('owner_queue', [])
        self.assertTrue(items, 'queue handler did not persist an item')
        return items[-1]['id']

    def sent(self, qid, receipt='delivery-1'):
        self.clock = 15
        rc, out = self.cli(C, 'sent1', qid, receipt)
        self.assertEqual(rc, 0, out)
        item = next(i for i in self.state()['owner_queue'] if i['id'] == qid)
        self.assertEqual(item.get('sent'), ts(10), 'send must retain delivery time')
        return item

    def poll(self):
        self.clock = 40
        rc, out = self.cli(C, 'owed')
        self.assertEqual(rc, 0, out)
        self.assertIn('SENT, NOT YET ACTED ON:', out, 'owed handler did not run')
        return out
