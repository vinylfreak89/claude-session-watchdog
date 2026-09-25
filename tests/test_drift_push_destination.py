"""Branch drift is measured against where a branch PUSHES, not against its own name.

Measured 2026-09-25, after the v11 consolidation: two git worktrees share one .git, git
refuses the same branch in both, so the target's checkout is local `v11-claude` pushing to
`origin/v11` while `origin/v11-claude` is frozen by design. Comparing against the frozen
same-name branch reported the target "54 ahead of origin" with zero unpushed commits, and
every later commit would have raised a fresh finding that latched the send gate.

Every case builds its own repos; nothing reads the project's live remote.
"""
import os, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import wd_lib as W


def g(cwd, *a):
    r = subprocess.run(['git', *a], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (a, r.stderr)
    return r.stdout.strip()


class DriftAgainstPushDestination(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.origin = os.path.join(self.d, 'origin.git')
        self.work = os.path.join(self.d, 'work')
        g(self.d, 'init', '-q', '--bare', '-b', 'main', self.origin)
        g(self.d, 'clone', '-q', self.origin, self.work)
        for k, v in (('user.email', 't@t'), ('user.name', 't'), ('commit.gpgsign', 'false')):
            g(self.work, 'config', k, v)
        self._commit('base')
        g(self.work, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
        # The frozen same-name branch: origin/mine stops HERE.
        g(self.work, 'checkout', '-q', '-b', 'mine')
        self._commit('old work')
        g(self.work, 'push', '-q', 'origin', 'mine')
        # Consolidation: two more commits, pushed to origin/shared, never to origin/mine.
        self._commit('merged 1'); self._commit('merged 2')
        g(self.work, 'push', '-q', 'origin', 'HEAD:refs/heads/shared')
        g(self.work, 'fetch', '-q', 'origin')

    def _commit(self, msg):
        with open(os.path.join(self.work, 'f.txt'), 'a') as fh:
            fh.write(msg + '\n')
        g(self.work, 'add', 'f.txt'); g(self.work, 'commit', '-q', '-m', msg)

    def _route_to_shared(self):
        g(self.work, 'branch', '-q', '--set-upstream-to=origin/shared', 'mine')
        g(self.work, 'config', 'push.default', 'upstream')

    def test_fixture_is_the_measured_defect(self):
        """CONTROL: with no push routing, the frozen same-name branch reads 2 ahead --
        exactly the false alarm. If this stops being true the other cases prove nothing."""
        d = W.git_branch_drift(self.work)
        self.assertEqual(d['push_branch'], 'mine')
        self.assertEqual(d['ahead'], 2)

    def test_routed_branch_in_sync_reads_zero_ahead(self):
        self._route_to_shared()
        d = W.git_branch_drift(self.work)
        self.assertEqual(d['branch'], 'mine')
        self.assertEqual(d['push_branch'], 'shared')
        self.assertEqual(d['ahead'], 0)
        self.assertEqual(d['behind'], 0)

    def test_routed_branch_still_catches_a_real_unpushed_commit(self):
        """The fix must not blind the check: an unpushed commit still reads as ahead."""
        self._route_to_shared()
        self._commit('genuinely unpushed')
        d = W.git_branch_drift(self.work)
        self.assertEqual(d['push_branch'], 'shared')
        self.assertEqual(d['ahead'], 1)
        self.assertIn('genuinely unpushed', ' '.join(d['ahead_shas']))


if __name__ == '__main__':
    unittest.main()
