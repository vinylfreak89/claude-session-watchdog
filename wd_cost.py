#!/usr/bin/python3
"""wd_cost.py -- cost per wake, from the watchdog session's OWN transcript (usage fields on assistant records).
Appends one TSV row per turn to state/cost.tsv:  end_ts  opener_kind  input  cache_read  cache_create  output  tool_calls  wall_s  head
Run at the end of each wake with --self <this session's id>. Idempotent: turns already logged (by promptId) are skipped."""
import os, sys, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wd_lib as W

def usage_of(turn):
    u = dict(input=0, cache_read=0, cache_create=0, output=0, calls=0)
    for r in turn.records:
        if r.get('type') != 'assistant': continue
        us = ((r.get('message') or {}).get('usage')) or {}
        u['input'] += us.get('input_tokens', 0) or 0
        u['cache_read'] += us.get('cache_read_input_tokens', 0) or 0
        u['cache_create'] += us.get('cache_creation_input_tokens', 0) or 0
        u['output'] += us.get('output_tokens', 0) or 0
        u['calls'] += 1
    return u

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--self', required=True, help='the watchdog session (id or title substring)')
    ap.add_argument('--state-dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state'))
    ap.add_argument('--turns', type=int, default=3)
    a = ap.parse_args()
    sess = W.find_session(a.self)
    path, turns = W.last_turns(sess, n=a.turns)
    out = os.path.join(a.state_dir, 'cost.tsv')
    done = set()
    if os.path.exists(out):
        for line in open(out):
            parts = line.rstrip('\n').split('\t')
            if parts: done.add(parts[0])
    new = 0
    with open(out, 'a') as f:
        if os.path.getsize(out) == 0:
            f.write('pid\tend_ts\topener\tinput\tcache_read\tcache_create\toutput\tapi_calls\twall_s\thead\n')
        for t in turns:
            if t.pid in done: continue
            u = usage_of(t)
            wall = (W.epoch_from_iso(t.end_ts) or 0) - (W.epoch_from_iso(t.start_ts) or 0)
            f.write('\t'.join(str(x) for x in [t.pid, t.end_ts, t.opener_kind, u['input'], u['cache_read'], u['cache_create'], u['output'], u['calls'], int(wall), W.short(t.opener_text, 60).replace('\t', ' ')]) + '\n')
            new += 1
    print('cost.tsv: %d new turn row(s) -> %s' % (new, out))
    for t in turns[-1:]:
        u = usage_of(t)
        print('latest turn (%s, %s): input=%d cache_read=%d cache_create=%d output=%d api_calls=%d' % (t.opener_kind, t.end_ts, u['input'], u['cache_read'], u['cache_create'], u['output'], u['calls']))

if __name__ == '__main__':
    main()
