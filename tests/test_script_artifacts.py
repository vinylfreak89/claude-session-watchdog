#!/usr/bin/python3
"""Work the target produces with a SCRIPT must be recordable, and who reviews it is the item's.

Two defects, found 2026-09-18 on a field render the owner asked for:

1. The file-content kinds (file, grep, csv) credited a write only when it could be
   REPLAYED from the target's Write/Edit calls. A script's output cannot be replayed, so
   no render, census CSV or report produced by a command could ever satisfy an item --
   and nearly every output in the target's project is produced by a command. The item
   sat UNDECIDED or NOT_YET forever, holding the send gate.
2. A directory subject was opened as a file (IsADirectoryError -> UNDECIDED).

And one omission the owner named: whether a finished artifact closes the item or goes
to him depends on who reviews it. "if the render is something I need review vs
something that is going to be machine reviewed". A render for his eyes is not done
when it exists; it is his to judge, and must not hold the send gate while he does.

Every fixture is built here. Nothing depends on a real transcript or a real render.
"""
import os
import unittest
from send_contract_support import ContractCase, C, K, W, ts


class ScriptArtifacts(ContractCase):
    def queue_item(self, spec, review=None):
        args = ['--queue-add', 'Render the span', '--acted-when', spec]
        if review: args += ['--queue-review', review]
        rc, out = self.cli(K, *args)
        self.assertEqual(rc, 0, out)
        qid = self.state()['owner_queue'][-1]['id']
        self.deliver('Render the span')
        self.sent(qid)
        return qid

    def produce(self, rel, at_second, data=b'\x00binary render\xff'):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        epoch = W.epoch_from_iso(ts(at_second))
        os.utime(path, (epoch, epoch))
        return path

    def archived(self, qid):
        return next((i for i in self.state().get('owner_queue_sent', []) if i['id'] == qid), None)

    def still_queued(self, qid):
        return any(i['id'] == qid for i in self.state().get('owner_queue', []))

    # --- the recording defect -------------------------------------------------

    def test_script_output_inside_its_own_command_window_settles(self):
        qid = self.queue_item('file out/render.mp4')
        out = self.produce('out/render.mp4', 20)
        self.tool('Bash', dict(command='D=%s; python3 render.py > $D/render.mp4' % out.parent), at=20)
        self.poll()
        self.assertIsNotNone(self.archived(qid), 'a script-produced artifact must be creditable')

    def test_mention_outside_the_write_window_does_not_credit(self):
        qid = self.queue_item('file out/render.mp4')
        out = self.produce('out/render.mp4', 30)            # written later, by something else
        self.tool('Bash', dict(command='ls %s' % out), at=20)  # names it, but ran 20..21
        self.poll()
        self.assertTrue(self.still_queued(qid))

    def test_basename_without_its_directory_does_not_credit(self):
        qid = self.queue_item('file out/render.mp4')
        self.produce('out/render.mp4', 20)
        self.tool('Bash', dict(command='python3 render.py > render.mp4'), at=20)
        self.poll()
        self.assertTrue(self.still_queued(qid))

    def test_failed_command_does_not_credit(self):
        qid = self.queue_item('file out/render.mp4')
        out = self.produce('out/render.mp4', 20)
        self.tool('Bash', dict(command='python3 r.py > %s' % out), at=20, error=True)
        self.poll()
        self.assertTrue(self.still_queued(qid))

    def test_background_command_uses_the_hosts_completion_notice(self):
        qid = self.queue_item('file out/render.mp4')
        out = self.produce('out/render.mp4', 28)
        self.tool('Bash', dict(command='python3 r.py > %s' % out, run_in_background=True), at=20,
                  result='Command running in background with ID: bgx1. Output is being written to: x')
        self.poll()
        self.assertTrue(self.still_queued(qid), 'no completion notice yet: the window is not closed')
        self.records(dict(type='queue-operation', operation='enqueue', timestamp=ts(30),
                          content='<task-notification>\n<task-id>bgx1</task-id>\n<status>completed</status>\n</task-notification>'))
        self.poll()
        self.assertIsNotNone(self.archived(qid))

    def test_failed_background_command_does_not_credit(self):
        qid = self.queue_item('file out/render.mp4')
        out = self.produce('out/render.mp4', 28)
        self.tool('Bash', dict(command='python3 r.py > %s' % out, run_in_background=True), at=20,
                  result='Command running in background with ID: bgx2. Output is being written to: x')
        self.records(dict(type='queue-operation', operation='enqueue', timestamp=ts(30),
                          content='<task-notification>\n<task-id>bgx2</task-id>\n<status>failed</status>\n</task-notification>'))
        self.poll()
        self.assertTrue(self.still_queued(qid))

    # --- directory subjects ---------------------------------------------------

    def test_directory_credits_a_new_attributed_file_inside_it(self):
        self.produce('renders/old.png', 2)
        qid = self.queue_item('file renders')
        new = self.produce('renders/span.mp4', 20)
        self.tool('Bash', dict(command='python3 r.py -o %s' % new), at=20)
        self.poll()
        self.assertIsNotNone(self.archived(qid))

    def test_directory_never_credits_untouched_contents(self):
        old = self.produce('renders/old.png', 2)
        qid = self.queue_item('file renders')
        self.tool('Bash', dict(command='ls %s' % old), at=20)
        self.poll()
        self.assertTrue(self.still_queued(qid))

    # --- who reviews it ---------------------------------------------------------

    def test_owner_reviewed_artifact_goes_to_him_and_frees_the_gate(self):
        qid = self.queue_item('file out/render.mp4', review='owner')
        out = self.produce('out/render.mp4', 20)
        self.tool('Bash', dict(command='python3 r.py > %s' % out), at=20)
        self.poll()
        item = self.archived(qid)
        self.assertIsNotNone(item, 'produced work must leave the send gate')
        self.assertEqual(item['acted_status'], 'awaiting owner review')
        decisions = self.state()['owner_decisions']
        review = decisions[item['review_decision']]
        self.assertEqual(review['review_of'], qid)
        self.assertIsNone(review['gated_on'], 'produced work is READY for him, not gated')
        rc, out = self.cli(C, 'next')
        self.assertNotIn('SENT WORK IS NOT YET VERIFIED', out)

    def test_machine_reviewed_artifact_closes_on_its_check(self):
        qid = self.queue_item('file out/render.mp4')
        out = self.produce('out/render.mp4', 20)
        self.tool('Bash', dict(command='python3 r.py > %s' % out), at=20)
        self.poll()
        item = self.archived(qid)
        self.assertNotEqual(item['acted_status'], 'awaiting owner review')
        self.assertFalse(self.state().get('owner_decisions'))

    def test_owner_review_does_not_fire_before_the_work_exists(self):
        qid = self.queue_item('file out/render.mp4', review='owner')
        self.poll()
        self.assertTrue(self.still_queued(qid))
        self.assertFalse(self.state().get('owner_decisions'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
