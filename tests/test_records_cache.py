#!/usr/bin/python3
"""The transcript parse cache must never serve a stale view.

One `owed` pass re-read two transcripts 204 times (171 of 188 s, 2026-09-18), so
reads are cached per file version. The key includes size and mtime, so an append
must be visible on the very next read -- a cache that hid a new delivery would make
the send gate decide on an old transcript.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wd_receipts as D


class RecordsCache(unittest.TestCase):
    def setUp(self):
        D._RECORDS_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 't.jsonl'
        self.path.write_text(json.dumps(dict(n=1)) + '\n')

    def test_append_is_visible_on_the_next_read(self):
        self.assertEqual(len(D.read_records(str(self.path))), 1)
        with self.path.open('a') as f: f.write(json.dumps(dict(n=2)) + '\n')
        self.assertEqual([r['n'] for r in D.read_records(str(self.path))], [1, 2])

    def test_same_size_rewrite_with_new_mtime_is_visible(self):
        D.read_records(str(self.path))
        self.path.write_text(json.dumps(dict(n=9)) + '\n')
        st = os.stat(self.path); os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        self.assertEqual(D.read_records(str(self.path))[0]['n'], 9)

    def test_callers_cannot_shrink_each_others_list(self):
        first = D.read_records(str(self.path)); first.clear()
        self.assertEqual(len(D.read_records(str(self.path))), 1)

    def test_a_malformed_file_still_raises(self):
        self.path.write_text('{broken\n')
        with self.assertRaises(D.EvidenceError):
            D.read_records(str(self.path))


if __name__ == '__main__':
    unittest.main(verbosity=2)
