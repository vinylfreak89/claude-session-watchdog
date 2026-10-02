#!/usr/bin/python3
"""A background task the target stopped with TaskStop leaves in-flight, and holds the gate no longer.

Defect, 2026-09-30 to 2026-10-02: a stopped task writes no exit marker and sends no completion
notification, so its in-flight row never retired and the send gate answered TARGET BUSY.
"""
import unittest
from send_contract_support import ContractCase, K, C, ts


class StoppedTaskRetires(ContractCase):
    def launch(self):
        path = self.tasks / 'task-control.output'
        self.tool('Bash', dict(command='sleep 999', run_in_background=True),
                  'Command running in background with ID: task-control. Output is being written to: %s.' % path)
        path.write_text('')
        self.end(25)
        rc, out = self.cli(K, '--bootstrap')
        self.assertEqual(rc, 0, out)
        self.assertTrue(any(i.get('id') == 'task-control' for i in self.state()['in_flight']))

    def end(self, at):
        self.records(dict(type='assistant', timestamp=ts(at), message=dict(role='assistant',
                          content=[dict(type='text', text='Synthetic result')], stop_reason='end_turn')))

    def stop(self, task_id='task-control', result=None, error=False):
        self.tool('TaskStop', dict(task_id=task_id),
                  result if result is not None else '{"message":"Successfully stopped task: %s (sleep 999)"}' % task_id,
                  at=30, error=error)
        self.end(35)
        rc, out = self.cli(K, '--trigger', 'manual-control')
        self.assertEqual(rc, 0, out)
        return out, [i.get('id') for i in self.state()['in_flight']]

    def test_confirmed_stop_retires_the_row(self):
        self.launch()
        out, ids = self.stop()
        self.assertNotIn('task-control', ids)
        self.assertIn('RETIRED in-flight task task-control', out)

    def test_refused_stop_keeps_the_row(self):
        self.launch()
        _, ids = self.stop(result='No task found with ID: task-control', error=True)
        self.assertIn('task-control', ids)

    def test_stop_of_another_task_keeps_the_row(self):
        self.launch()
        _, ids = self.stop(task_id='task-other')
        self.assertIn('task-control', ids)

    def test_result_naming_a_different_id_keeps_the_row(self):
        self.launch()
        _, ids = self.stop(result='{"message":"Successfully stopped task: task-other"}')
        self.assertIn('task-control', ids)


if __name__ == '__main__':
    unittest.main()
