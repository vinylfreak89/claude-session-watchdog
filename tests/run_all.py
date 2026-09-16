#!/usr/bin/env python3
"""Run every test script in its own process with this interpreter; no exclusions."""
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    scripts = sorted((root / 'tests').glob('test_*.py'))
    print('INTERPRETER: %s (%s)' % (sys.executable, sys.version.split()[0]), flush=True)
    print('DISCOVERED: %d test scripts; exclusions: 0' % len(scripts), flush=True)
    if not scripts:
        print('FAILED: no test scripts discovered', flush=True)
        return 1
    failed = []
    for script in scripts:
        print('\nRUN %s' % script.name, flush=True)
        result = subprocess.run([sys.executable, str(script)], cwd=str(root), capture_output=True, text=True)
        print(result.stdout, end='', flush=True)
        print(result.stderr, end='', flush=True)
        print('EXIT %s: %d' % (script.name, result.returncode), flush=True)
        if result.returncode:
            failed.append(script.name)
    print('\nRESULT: %d/%d scripts passed; %d failed; exclusions: 0' %
          (len(scripts) - len(failed), len(scripts), len(failed)), flush=True)
    for name in failed: print('FAILED: ' + name, flush=True)
    return int(bool(failed))


if __name__ == '__main__':
    raise SystemExit(main())
