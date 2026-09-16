"""Review probes for 5800a53, cc0fb3a, and bd3a5c8.

Run: /usr/bin/python3 docs/reviews/reproduce_send_acceptance.py
These assertions demonstrate CURRENT UNSAFE BEHAVIOR; OK means reproduced,
not that the implementation satisfies the intended properties. All mutable
state and transcripts are synthetic, in temporary directories. Session lookup,
clock and transcript location are patched; no real session is contacted.
"""
import contextlib, io, json, os, sys, tempfile, types, unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import wd_check as C, wd_wake as K, wd_lib as W, wd_wait as Wait
SELF = 'local_review_sender'
T0='2026-09-16T10:00:00.000Z'
T1='2026-09-16T11:00:00.000Z'
T2='2026-09-16T12:00:00.000Z'
class Review(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='review-wd-')
        self.p=Path(self.tmp.name)
        self.sess=dict(cwd=str(self.p), cli='review_cli', sessionId='local_target')
        self.a=types.SimpleNamespace(repo=str(self.p), ledger=None, self_sel=SELF, state_dir=str(self.p), quiet_min=10)
        self.tx=self.p/'target.jsonl'
        self.tx.write_text(json.dumps(dict(type='user', timestamp=T0, message=dict(content='<cross-session-message from="%s">Do A</cross-session-message>' % SELF)))+'\n')
    def tearDown(self): self.tmp.cleanup()
    def save(self,s): K.save_state(str(self.p),s)
    def load(self): return K.load_state(str(self.p))
    def cli(self,mod,args,now=T2):
        with patch.object(sys,'argv',[mod.__file__,'--state-dir',str(self.p)]+args), patch.object(W,'find_session',return_value=self.sess), patch.object(W,'transcript_path',return_value=str(self.tx)), patch.object(W,'now_iso',return_value=now), contextlib.redirect_stdout(io.StringIO()):
            return mod.main()
    def debt(self,s,ts=T1):
        turn=types.SimpleNamespace(end_state='end_turn', end_ts=ts, assistant_texts=[], final_text='Target finished later work',tool_uses=[])
        with patch.object(W,'last_turns',return_value=('unused',[turn])):
            return C.owed(self.sess,s)
    def test_reconcile_restores_refused_answer_without_delivery(self):
        import wd_recon_lib as R
        self.tx.write_text(''); self.save(dict(last_relay_ts=T2))
        self.assertEqual(self.cli(C,['--target','target','--self',SELF,'answered']),1)
        action=dict(verb='answered',id=None,ts=T2,cite='synthetic:1',outcome='failed',state='live')
        work, accounted=R.restorations_owed([action],self.load())
        self.assertEqual(len(work),1)
        R.stage5_repair(str(self.p),[work[0]['repair']],apply=True)
        self.assertEqual(self.load()['last_send_ts'],T2)
        self.assertEqual(self.debt(self.load()),[])
    def test_delivered_but_unmarked_item_can_be_dropped(self):
        self.save(dict(owner_queue=[dict(id='Q1',text='Do A',ts=T0,acted_when='file missing')]))
        self.assertTrue(C.answered_allowed(str(self.tx),SELF,{},None)[0])
        self.assertEqual(self.cli(K,['--queue-drop','Q1','--reason','withdraw']),0)
        self.assertEqual(self.load()['owner_queue'],[])
    def test_next_ignores_sent_unacted_item(self):
        s=dict(owner_queue=[dict(id='Q1',sent=T0,acted_when='file missing'),dict(id='Q2',text='B')])
        with patch.object(W,'last_turns',return_value=('unused',[])):
            verdict,item,_,_=C.next_item(self.sess,s)
        self.assertEqual((verdict,item['id']),('send','Q2'))
    def test_sent_without_delivery(self):
        self.tx.write_text(''); self.save(dict(last_relay_ts=T2))
        self.assertEqual(self.cli(K,['--sent','F_DOES_NOT_EXIST']),0)
        self.assertEqual(self.load()['last_send_ts'],T2)
        self.assertEqual(self.debt(self.load()),[])
    def test_reusing_message_id_clears_new_turn(self):
        self.save(dict(last_relay_ts=T2,owner_queue=[dict(id='Q1',text='Do A',acted_when='file missing')]))
        args=['--target','target','--self',SELF,'sent1','Q1','invented-id']
        self.assertEqual(self.cli(C,args,now=T0),0)
        self.assertEqual(len(self.debt(self.load())),1)
        self.assertEqual(self.cli(C,args,now=T2),0)
        self.assertEqual(self.debt(self.load()),[])
        self.assertEqual(self.load()['credited_sends'],{'invented-id':T0})
    def test_same_delivery_marks_unrelated_queue_item(self):
        self.save(dict(owner_queue=[dict(id=i,text='Do '+i,acted_when='file missing') for i in ['Q1','Q2']]))
        for i in ['Q1','Q2']:
            self.assertEqual(self.cli(C,['--target','target','--self',SELF,'sent1',i,'same-id']),0)
        self.assertTrue(all(i.get('sent') for i in self.load()['owner_queue']))
    def test_delayed_first_mark_clears_post_delivery_turn(self):
        self.save(dict(last_relay_ts=T2))
        self.assertEqual(len(self.debt(self.load())),1)
        self.assertEqual(self.cli(C,['--target','target','--self',SELF,'answered']),0)
        self.assertEqual(self.debt(self.load()),[])
    def test_change_acceptance_after_send_closes_without_action(self):
        (self.p/'existing').write_text('old content')
        self.save(dict(owner_queue=[dict(id='Q1',text='Create missing',sent=T0,acted_when='file missing')]))
        self.assertEqual(C.settle_acted(self.a,self.sess,self.load()),[])
        self.assertEqual(self.cli(K,['--queue-acted-when','Q1','--acted-when','file existing']),0)
        self.assertEqual(C.settle_acted(self.a,self.sess,self.load()),['Q1'])
    def test_preexisting_file_is_action(self):
        (self.p/'existing').write_text('old content')
        os.utime(self.p/'existing',(1,1))
        s=dict(owner_queue=[dict(id='Q1',text='Rewrite existing',sent=T0,acted_when='file existing')])
        self.assertEqual(C.settle_acted(self.a,self.sess,s),['Q1'])
    def test_missing_file_fact_fails_open(self):
        with patch.object(C,'check',return_value=('stat','exists',{'exists':True,'modified_after':False})):
            self.assertEqual(C.acceptance_satisfied(self.a,self.sess,{},'file existing '+T0)[:2],(True,True))
    def test_bad_fact_type_kills_poll(self):
        with patch.object(C,'check',return_value=('grep','hits',{'hits':'one'})):
            with self.assertRaises(TypeError): C.acceptance_satisfied(self.a,self.sess,{},'grep existing match')
    def test_unknown_kind_accepted_on_add(self):
        self.assertEqual(self.cli(K,['--queue-add','do something','--acted-when','running']),0)
        self.assertEqual(self.load()['owner_queue'][0]['acted_when'],'running')
    def test_queue_ids_collide_after_drop(self):
        self.save(dict(owner_queue=[dict(id='Q1',text='a',ts=T0),dict(id='Q2',text='b',ts=T0)]))
        self.assertEqual(self.cli(K,['--queue-drop','Q1','--reason','withdraw']),0)
        self.assertEqual(self.cli(K,['--queue-add','c']),0)
        self.assertEqual([i['id'] for i in self.load()['owner_queue']],['Q2','Q2'])
    def test_human_quote_is_delivery(self):
        d=dict(type='user',timestamp=T0,origin=dict(kind='human'),message=dict(content='Example from="'+SELF+'" copied from documentation'))
        self.tx.write_text(json.dumps(d)+'\n')
        self.assertTrue(C.answered_allowed(str(self.tx),SELF,{},None)[0])
    def test_real_text_block_delivery_is_rejected(self):
        d=json.loads(self.tx.read_text()); d['message']['content']=[dict(type='text',text=d['message']['content'])]
        self.tx.write_text(json.dumps(d)+'\n')
        self.assertFalse(C.answered_allowed(str(self.tx),SELF,{},None)[0])
    def test_lifecycle_event_split_across_chunks_is_lost(self):
        tid='review-thread'; d=self.p/'2026'/'09'/'16'; d.mkdir(parents=True)
        f=d/('rollout-example-'+tid+'.jsonl')
        start=(json.dumps(dict(timestamp=T0,type='event_msg',payload=dict(type='task_started')))+'\n').encode()
        # Exactly 4 MiB after offset 20 of the lifecycle record: scanner drops the
        # suffix in one chunk and sees an incomplete JSON prefix in the next.
        want=4*1024*1024-(len(start)-20)
        empty=(json.dumps(dict(timestamp=T1,type='response_item',payload=dict(type='reasoning',text='')))+'\n').encode()
        filler=empty.replace(b'"text": ""',b'"text": "'+b'x'*(want-len(empty))+b'"')
        f.write_bytes(start+filler)
        with patch.object(W,'CODEX_SESSIONS',str(self.p)):
            s=W.codex_thread_state(tid)
        self.assertIsNone(s['last_started']); self.assertTrue(s['lifecycle_known']); self.assertFalse(s['in_flight'])
    def test_unknown_lifecycle_treated_as_finished_by_monitor(self):
        obj=object.__new__(Wait.Watch)
        s=dict(found=True,size=10,mtime=T1,in_flight=False,lifecycle_known=False,last_complete=None)
        with patch.object(W,'codex_thread_state',return_value=s):
            sig,alive,assessable,detail=obj._progress(dict(kind='codex',thread='review'))
        self.assertFalse(alive); self.assertTrue(assessable); self.assertIn('no turn in flight',detail)
    def test_foreground_quoted_dispatch_is_a_launch(self):
        turn=types.SimpleNamespace(tool_uses=[dict(id='u',name='Bash',ts=T0,input=dict(command="printf '%s' 'codex-run task 00000000-0000-0000-0000-000000000000'"))],tool_results={'u':dict(text='Command running in background with ID: foreign. Output is being written to: /private/tmp/claude-501/project/foreign-cli/tasks/foreign.output.',is_error=False)})
        self.assertEqual(W.dispatches_in(turn)[0]['background']['task_id'],'foreign')
if __name__=='__main__': unittest.main(verbosity=2)
