"""Run the real reconciliation CLI against a fixture-owned process census.

The process table is an external input, like the transcript and target repository.
A tiny ps executable replaces only that input in the child's PATH; argument parsing,
exclusion, ledger initialization and every reconciliation stage remain real.
"""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


def run_reconcile(argv, *, process_table='', ps_exit=0, **kwargs):
    with tempfile.TemporaryDirectory(prefix='recon-process-fixture-') as directory:
        ps = Path(directory) / 'ps'
        ps.write_text('#!/bin/sh\nprintf %s ' + shlex.quote(process_table) + '\nexit ' + str(int(ps_exit)) + '\n')
        ps.chmod(0o700)
        env = dict(kwargs.pop('env', os.environ))
        env['PATH'] = directory + os.pathsep + env.get('PATH', '')
        return subprocess.run(argv, env=env, **kwargs)
