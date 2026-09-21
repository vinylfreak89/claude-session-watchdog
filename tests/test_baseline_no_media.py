"""A file baseline keeps the hash, not the bytes, once the subject is large.

2026-09-21: `file_snapshot` base64'd the whole subject into the acceptance baseline. Several
items pointed at cap4.mp4 (38 MB), so state.json reached 358 MB, and every wd.sh invocation
loaded and rewrote it -- `owed` took minutes and had to be backgrounded all night.

The sha256 is what detects change; the content exists only to replay a Write/Edit against it
or to read as text. Neither applies to media, so above the cap the bytes are dropped and the
consumers' existing "content is None" path answers honestly.

What this test pins is the PROPERTY, not the number: whatever the cap is, a subject above it
keeps a usable hash and drops the bytes, and one below it keeps both.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wd_acceptance as A


def write(tmp, name, nbytes, fill=b'\x00'):
    path = os.path.join(tmp, name)
    with open(path, 'wb') as fh:
        fh.write(fill * nbytes)
    return path


def main():
    tmp = tempfile.mkdtemp()
    cap = A.CONTENT_BASELINE_MAX

    # 1. A small text subject keeps its bytes -- grep/csv/row read them as text.
    small = write(tmp, 'ledger.md', 1024, b'x')
    snap = A.file_snapshot(small)
    assert snap['content'] is not None, 'small subject lost its content'
    assert snap['sha256'] and snap['size'] == 1024

    # 2. A subject just over the cap keeps hash and size, drops the bytes.
    big = write(tmp, 'render.mp4', cap + 1)
    snap = A.file_snapshot(big)
    assert snap['content'] is None, 'oversized subject still carries its bytes'
    assert snap['sha256'], 'oversized subject lost the hash that detects change'
    assert snap['size'] == cap + 1

    # 3. THE PROPERTY THAT MATTERS: the hash still changes when the file changes, so
    #    change detection is untouched by dropping the bytes.
    first = A.file_snapshot(big)['sha256']
    with open(big, 'ab') as fh:
        fh.write(b'\x01')
    assert A.file_snapshot(big)['sha256'] != first, 'change no longer detectable without content'

    # 4. The baseline of an oversized subject must be small enough to live in state.
    import json
    assert len(json.dumps(A.file_snapshot(big))) < 4096, 'baseline is still large'

    # 5. A missing subject is unchanged in shape.
    snap = A.file_snapshot(os.path.join(tmp, 'absent'))
    assert snap['exists'] is False and snap['content'] is None and snap['sha256'] is None
    print('ok')


if __name__ == '__main__':
    main()
