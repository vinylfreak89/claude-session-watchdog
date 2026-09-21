#!/usr/bin/python3
"""Every supported kind has real-producer, real-handler positive and negative controls."""
import json
import os
import subprocess
import unittest
from unittest.mock import patch
from send_contract_support import ContractCase, C, K, W, SELF, TARGET, ts
import wd_acceptance as A
import wd_receipts as D

class AcceptanceContract(ContractCase):
    def message_case(self, name, recipient=SELF, success=True, receive=True, socket=False):
        q = self.queue('Reply to the watchdog', 'msg-to-watchdog "Synthetic result"')
        self.deliver('Reply to the watchdog'); self.sent(q)
        inp = dict(session_id=recipient, message='Synthetic result')
        result = 'ok'
        if name == 'mcp__ccd_session_mgmt__send_message':
            result = 'Message delivered to session %s ("Synthetic watchdog"); its turn has started on it. (delivery: delivered; message_id: transport-control)' % recipient
        if name == 'SendMessage':
            inp = dict(to=recipient, recipient=recipient, type='message', message='Synthetic result',
                       content='Synthetic transport content', summary='Synthetic summary')
            result = json.dumps(dict(success=success, msg_id='transport-control'))
        self.tool(name, inp, result=result)
        # Both hosts omit is_error; their explicit acknowledgements carry success.
        if name in ('SendMessage', 'mcp__ccd_session_mgmt__send_message'):
            rows = [json.loads(line) for line in self.tx.read_text().splitlines()]
            rows[-1]['message']['content'][0].pop('is_error')
            self.tx.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        if receive:
            origin = dict(kind='peer', **{'from': TARGET})
            if socket:
                origin.update({'from': 'uds:/tmp/cc-socks/43210.sock', 'verifiedPeerPid': 43210,
                               'msg_id': 'transport-control', 'body': 'Synthetic result'})
            self.records(dict(type='user', uuid='reply-control', timestamp=ts(22), origin=origin,
                              message=dict(content='<cross-session-message from="%s">Synthetic result</cross-session-message>' % origin['from'])), path=self.mine)
        return q

    def check_message_count(self, expected):
        rc, out = self.cli(C, 'check', 'msg-to-watchdog', ts(10))
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)['evidence']['count'], expected, out)

    def test_current_message_reply_settles_and_is_visible(self):
        q = self.message_case('SendMessage')
        self.check_message_count(1)
        out = self.poll()
        self.assertEqual(self.state()['owner_queue'], [], out)
        self.assertEqual(self.state()['owner_queue_sent'][0]['id'], q)

    def reply_body(self, body):
        self.message_case('SendMessage')
        rows = D.read_records(str(self.tx))
        rows[-2]['message']['content'][0]['input']['message'] = body
        self.tx.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        rows = D.read_records(str(self.mine))
        rows[-1]['message']['content'] = '<cross-session-message from="%s">%s</cross-session-message>' % (TARGET, body)
        self.mine.write_text(''.join(json.dumps(r) + '\n' for r in rows))

    def test_closeout_line_with_detail_settles(self):
        self.reply_body('Synthetic result\nDetail of the completed work.')
        out = self.poll()
        self.assertEqual(self.state()['owner_queue'], [], out)

    def test_closeout_mentioned_mid_sentence_stays_owed(self):
        self.reply_body('The phrase Synthetic result appears in this sentence.')
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, out)
        self.assertIn('not-yet', out)

    def test_legacy_message_reply_still_settles_and_is_visible(self):
        self.message_case('mcp__ccd_session_mgmt__send_message')
        self.check_message_count(1)
        out = self.poll()
        self.assertEqual(self.state()['owner_queue'], [], out)

    def test_bare_session_uuid_still_identifies_watchdog(self):
        self.message_case('mcp__ccd_session_mgmt__send_message', recipient=SELF.removeprefix('local_'))
        self.check_message_count(1)
        out = self.poll()
        self.assertEqual(self.state()['owner_queue'], [], out)

    def test_socket_recipient_requires_delivered_transport_evidence(self):
        self.message_case('SendMessage', recipient='uds:/tmp/cc-socks/12345.sock', socket=True)
        self.check_message_count(1)
        out = self.poll()
        self.assertEqual(self.state()['owner_queue'], [], out)

    def test_current_message_wrong_recipient_does_not_settle(self):
        self.message_case('SendMessage', recipient='local_other_agent')
        self.check_message_count(0)
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, out)
        self.assertIn('not-yet', out)

    def test_legacy_message_wrong_recipient_does_not_settle(self):
        self.message_case('mcp__ccd_session_mgmt__send_message', recipient='local_other_agent')
        self.check_message_count(0)
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, out)

    def test_similar_tool_name_cannot_count_as_reply(self):
        self.message_case('mcp__unrelated__send_message')
        self.check_message_count(0)
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, out)

    def test_current_message_failed_result_cannot_settle(self):
        self.message_case('SendMessage', success=False)
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, out)

    def test_current_message_without_delivery_cannot_settle(self):
        self.message_case('SendMessage', receive=False)
        self.check_message_count(1)
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, out)

    def test_socket_recipient_without_matching_receipt_does_not_count(self):
        self.message_case('SendMessage', recipient='uds:/tmp/cc-socks/12345.sock', socket=True)
        rows = D.read_records(str(self.mine))
        rows[-1]['origin']['msg_id'] = 'another-transport'
        self.mine.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        self.check_message_count(0)
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, out)

    def test_conflicting_current_recipients_do_not_count(self):
        self.message_case('SendMessage')
        rows = D.read_records(str(self.tx))
        rows[-2]['message']['content'][0]['input']['recipient'] = 'local_other_agent'
        self.tx.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        self.check_message_count(0)
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, out)

    def explicit_message_error(self, name):
        self.message_case(name)
        rows = D.read_records(str(self.tx))
        rows[-1]['message']['content'][0]['is_error'] = True
        self.tx.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, out)
        self.assertIn('not-yet', out)

    def test_current_explicit_error_overrides_success_text(self):
        self.explicit_message_error('SendMessage')

    def test_legacy_explicit_error_overrides_success_text(self):
        self.explicit_message_error('mcp__ccd_session_mgmt__send_message')

    def test_legacy_queued_result_waits_for_actual_delivery(self):
        self.message_case('mcp__ccd_session_mgmt__send_message', receive=False)
        rows = D.read_records(str(self.tx))
        rows[-1]['message']['content'][0]['content'] = 'Message queued for session %s ("Synthetic watchdog"): that session is holding it. (delivery: queued; message_id: transport-control)' % SELF
        self.tx.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, out)
        self.records(dict(type='user', uuid='reply-control', timestamp=ts(22),
            origin=dict(kind='peer', **{'from': TARGET}),
            message=dict(content='<cross-session-message from="%s">Synthetic result</cross-session-message>' % TARGET)), path=self.mine)
        out = self.poll()
        self.assertEqual(self.state()['owner_queue'], [], out)

    def duplicate_use(self, at, ident='duplicated-control'):
        record = dict(type='assistant', timestamp=ts(at), message=dict(role='assistant',
                      content=[dict(type='tool_use', id=ident, name='Read', input=dict(file_path='unrelated.txt'))],
                      stop_reason='tool_use'))
        self.records(record, record)

    def historical_duplicates(self, kind):
        self.duplicate_use(2)
        self.turn('after-history', 3, 4)
        if kind == 'row':
            # An available ledger lacking the requested row is NOT-YET. A missing
            # ledger is correctly UNDECIDED and would test a different property.
            (self.root / 'ledger.md').write_text('| ID | Result |\n|---|---|\n')
        q = self.queue('Perform synthetic action', self.spec(kind))
        self.deliver('Perform synthetic action'); self.sent(q)
        out = self.poll()
        self.assertIn('not-yet', out, 'clean evidence window must reach the kind evaluator')
        self.assertNotIn('undecided', out)
        self.assertEqual(len(self.state()['owner_queue']), 1, out)
        self.act(kind)
        out = self.poll()
        self.assertEqual(self.state()['owner_queue'], [], out)
        self.assertEqual(self.state()['owner_queue_sent'][0]['id'], q)
        self.assertEqual(self.state()['owner_queue_sent'][0]['acted_status'], 'pass')

    def test_window_duplicate_refuses_even_with_satisfied_artifact(self):
        q = self.queue(); self.deliver('Create artifact'); self.sent(q)
        self.write_target('artifact.txt', 'created')
        for at in (10, 25):
            with self.subTest(duplicate_at=at):
                original = self.tx.read_text()
                self.duplicate_use(at)
                with self.assertRaisesRegex(D.EvidenceError, 'duplicate target tool-use id'):
                    A.target_calls(self.sess, ts(10))
                out = self.poll()
                self.assertIn('undecided', out)
                self.assertIn('duplicate target tool-use id', out)
                self.assertEqual(len(self.state()['owner_queue']), 1, out)
                self.tx.write_text(original)

    def test_late_appended_old_duplicates_do_not_poison_window(self):
        q = self.queue(); self.deliver('Create artifact'); self.sent(q)
        self.write_target('artifact.txt', 'created')
        self.duplicate_use(2)
        out = self.poll()
        self.assertEqual(self.state()['owner_queue'], [], out)

    def test_predelivery_call_with_late_result_does_not_settle(self):
        q = self.queue(); self.deliver('Create artifact'); self.sent(q)
        self.write_target('artifact.txt', 'created')
        records = [json.loads(line) for line in self.tx.read_text().splitlines()]
        for stamp in (ts(9), ts(10)):
            with self.subTest(call_timestamp=stamp):
                for r in records:
                    if any(b.get('name') == 'Write' for b in W._blocks((r.get('message') or {}).get('content'), 'tool_use')):
                        r['timestamp'] = stamp
                self.tx.write_text(''.join(json.dumps(r) + '\n' for r in records))
                out = self.poll()
                self.assertIn('not-yet', out)
                self.assertEqual(len(self.state()['owner_queue']), 1, out)

    def test_undated_tool_use_cannot_be_assumed_historical(self):
        q = self.queue(); self.deliver('Create artifact'); self.sent(q)
        self.write_target('artifact.txt', 'created')
        self.records(dict(type='assistant', message=dict(content=[dict(type='tool_use', id='undated', name='Read', input={})])))
        out = self.poll()
        self.assertIn('undecided', out)
        self.assertIn('timestamp', out)
        self.assertEqual(len(self.state()['owner_queue']), 1, out)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root)] + list(args), stderr=subprocess.STDOUT, text=True).strip()

    def spec(self, kind):
        if kind == 'file': return 'file artifact.txt'
        if kind == 'grep': return 'grep artifact.txt completed'
        if kind == 'csv': return 'csv artifact.csv status==completed'
        if kind == 'row': return 'row A1'
        if kind == 'task': return 'task job1'
        if kind == 'msg-to-watchdog': return 'msg-to-watchdog "Synthetic result"'
        if kind == 'commit':
            self.git('init', '-b', 'main')
            self.git('config', 'user.name', 'Synthetic Test')
            self.git('config', 'user.email', 'test@example.invalid')
            (self.root / 'commit.txt').write_text('synthetic')
            self.git('add', 'commit.txt')
            self.git('commit', '-m', 'Synthetic fixture')
            remote = self.root / 'remote.git'
            subprocess.check_output(['git', 'init', '--bare', str(remote)], stderr=subprocess.STDOUT)
            self.git('remote', 'add', 'origin', str(remote))
            return 'commit ' + self.git('rev-parse', 'HEAD')
        raise AssertionError(kind)

    def act(self, kind, at=20, attributed=True):
        if kind in ('file', 'grep', 'row', 'csv'):
            name, content = {'file': ('artifact.txt', 'created'), 'grep': ('artifact.txt', 'completed'),
                             'row': ('ledger.md', '| ID | Result |\n|---|---|\n| A1 | completed |\n'),
                             'csv': ('artifact.csv', 'id,status\n1,completed\n')}[kind]
            if attributed: self.write_target(name, content, at=at)
            else:
                path = self.root / name
                path.write_text(content)
                epoch = W.epoch_from_iso(ts(at + 1))
                os.utime(path, (epoch, epoch))
        elif kind == 'commit':
            refspec = self.git('rev-parse', 'HEAD') + ':refs/heads/main'
            self.git('push', 'origin', refspec)
            if attributed: self.tool('Bash', dict(command='git push origin ' + refspec), result='HEAD -> main', at=at)
        elif kind == 'task':
            output = self.tasks / 'job1.output'
            if attributed:
                self.tool('Bash', dict(command='sleep 1', run_in_background=True),
                          'Command running in background with ID: job1. Output is being written to: %s.' % output, at=at)
            output.write_text('[exited with code 0]\n')
            epoch = W.epoch_from_iso(ts(at + 1)); os.utime(output, (epoch, epoch))
            if attributed:
                self.records(dict(type='user', promptId='delivery-1', timestamp=ts(at + 2),
                     origin=dict(kind='task-notification'), message=dict(role='user',
                     content='<task-notification><task-id>job1</task-id><status>completed</status></task-notification>')))
        elif kind == 'msg-to-watchdog':
            if attributed:
                self.tool('mcp__ccd_session_mgmt__send_message', dict(session_id=SELF, message='Synthetic result'), at=at)
            self.records(dict(type='user', uuid='reply-1', timestamp=ts(at + 2), origin=dict(kind='peer', **{'from': TARGET}),
                 message=dict(role='user', content='<cross-session-message from="%s">Synthetic result</cross-session-message>' % TARGET)), path=self.mine)

    def exercise(self, kind, mode):
        spec = self.spec(kind)
        if mode == 'preexisting': self.act(kind, at=2)
        q = self.queue('Perform synthetic action', spec)
        self.deliver('Perform synthetic action')
        self.sent(q)
        before = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, before)
        if mode == 'preexisting': return
        self.act(kind, attributed=mode != 'unattributed')
        after = self.poll()
        if mode == 'positive':
            self.assertEqual(self.state()['owner_queue'], [], after)
            self.assertEqual(self.state()['owner_queue_sent'][0]['id'], q)
            self.assertTrue(self.state()['owner_queue_sent'][0].get('acted_evidence'))
        else:
            self.assertEqual(len(self.state()['owner_queue']), 1, after)

    def test_missing_file_fact_is_undecided(self):
        q = self.queue(); self.deliver('Create artifact'); self.sent(q)
        self.write_target('artifact.txt', 'created')
        with patch.object(C, 'check', return_value=('stat artifact.txt', 'exists', {'exists': True, 'modified_after': False})):
            out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1)
        self.assertIn('undecided', out.lower())

    def test_missing_artifact_is_not_yet_and_names_subject(self):
        q = self.queue(); self.deliver('Create artifact'); self.sent(q)
        out = self.poll()
        self.assertIn('not-yet', out)
        self.assertIn('artifact.txt', out)
        self.assertEqual(len(self.state()['owner_queue']), 1)

    def test_bad_count_type_is_undecided_not_a_crash(self):
        q = self.queue(spec='grep artifact.txt created'); self.deliver('Create artifact'); self.sent(q)
        self.write_target('artifact.txt', 'created')
        with patch.object(C, 'check', return_value=('grep artifact.txt', 'hits', {'hits': 'one'})):
            out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1)
        self.assertIn('undecided', out.lower())

    def test_failed_target_write_does_not_close(self):
        q = self.queue(); self.deliver('Create artifact'); self.sent(q)
        path = self.root / 'artifact.txt'; path.write_text('created')
        self.tool('Write', dict(file_path=str(path), content='created'), error=True)
        self.assertEqual(len(self.state()['owner_queue']), 1, self.poll())

    def test_missing_baseline_is_undecided(self):
        q = self.queue(); self.deliver('Create artifact'); self.sent(q)
        state = self.state(); state['owner_queue'][0].pop('acceptance_baseline', None)
        K.save_state(str(self.state_dir), state)
        self.write_target('artifact.txt', 'created')
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1)
        self.assertIn('undecided', out.lower())

    def test_unrelated_push_cannot_attribute_commit(self):
        spec = self.spec('commit')
        q = self.queue('Publish synthetic commit', spec)
        self.deliver('Publish synthetic commit'); self.sent(q)
        self.act('commit', attributed=False)
        self.tool('Bash', dict(command='git push origin unrelated'), result='Everything up-to-date')
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, out)

    def test_missing_task_baseline_exit_is_undecided(self):
        q = self.queue(spec='task job1'); self.deliver('Create artifact'); self.sent(q)
        state = self.state(); state['owner_queue'][0]['acceptance_baseline'].pop('exit')
        K.save_state(str(self.state_dir), state)
        self.act('task')
        out = self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1, out)
        self.assertIn('undecided', out.lower())

for _kind in ('file', 'grep', 'csv', 'row', 'commit', 'task', 'msg-to-watchdog'):
    def historical_control(self, kind=_kind): self.historical_duplicates(kind)
    setattr(AcceptanceContract, 'test_%s_historical_duplicates' % _kind.replace('-', '_'), historical_control)
    for _mode in ('positive', 'preexisting', 'unattributed'):
        def control(self, kind=_kind, mode=_mode): self.exercise(kind, mode)
        setattr(AcceptanceContract, 'test_%s_%s' % (_kind.replace('-', '_'), _mode), control)

if __name__ == '__main__': unittest.main(verbosity=2)
