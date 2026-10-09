from __future__ import annotations
import contextlib, io, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from agent_devtools.work.cli import main_verify
from agent_devtools.work.semantic_closeout import semantic_checkpoint
from agent_devtools.work.sources import add_source
from agent_devtools.work.state import align_task, start_task, update_task
from agent_devtools.workflow import CLI_CONTRACT_VERSION, WORKFLOW_CONTRACT_VERSION, capabilities

class VerificationIntegrityTests(unittest.TestCase):
    def make_root(self):
        tmp=tempfile.TemporaryDirectory(); root=Path(tmp.name)
        (root/'agent-tools.json').write_text(json.dumps({'version':1,'work':{'profile':'research'}}),encoding='utf-8')
        state=start_task(root,goal='Research a hardware recommendation'); align_task(root,no_material_gaps=True,resolution='ready')
        return tmp,root,state
    def args(self,**overrides):
        values=dict(verify_command='research',arithmetic=None,sourcing=None,assumptions=None,knowledge=None,unresolved_questions=None,confirm_all_pass=False,evidence=[],summary='',json_output=True)
        values.update(overrides); return SimpleNamespace(**values)
    def test_missing_source_provenance_blocks_compact_all_pass(self):
        tmp,root,_=self.make_root()
        try:
            err=io.StringIO()
            with contextlib.redirect_stderr(err): rc=main_verify(root,self.args(confirm_all_pass=True,summary='Everything passes'))
            self.assertEqual(2,rc); self.assertIn('--confirm-all-pass is unavailable',err.getvalue()); self.assertIn('missing-source-provenance',err.getvalue())
        finally: tmp.cleanup()
    def test_pending_evidence_can_satisfy_source_awareness(self):
        tmp,root,_=self.make_root()
        try:
            out=io.StringIO()
            with contextlib.redirect_stdout(out): rc=main_verify(root,self.args(confirm_all_pass=True,evidence=['Vendor specification reviewed'],summary='Reviewed with evidence'))
            self.assertEqual(0,rc); payload=json.loads(out.getvalue()); self.assertEqual('pass',payload['record']['status'])
            self.assertNotIn('missing-source-provenance',{x['id'] for x in payload['contextWarnings']})
        finally: tmp.cleanup()
    def test_material_question_blocks_compact_but_granular_remains(self):
        tmp,root,_=self.make_root()
        try:
            add_source(root,url='https://example.com/spec',title='Example spec')
            update_task(root,add_open_questions=['Which model size changes the GPU requirement?'],semantic_subjects={'openQuestions':'models'})
            err=io.StringIO()
            with contextlib.redirect_stderr(err): rc=main_verify(root,self.args(confirm_all_pass=True,summary='Compact'))
            self.assertEqual(2,rc); self.assertIn('potentially-material-open-questions',err.getvalue())
            out=io.StringIO()
            with contextlib.redirect_stdout(out): rc=main_verify(root,self.args(arithmetic='pass',sourcing='pass',assumptions='pass',knowledge='pass',unresolved_questions='pass',summary='Explicitly retained question'))
            self.assertEqual(0,rc); self.assertEqual('pass',json.loads(out.getvalue())['record']['status'])
        finally: tmp.cleanup()
    def test_subject_refinement_surfaces_possible_stale_cognition(self):
        tmp,root,_=self.make_root()
        try:
            add_source(root,url='https://example.com/spec',title='Example spec')
            update_task(root,add_assumptions=['Assume 8GB per GPU, four cards gives 32GB total.'],semantic_subjects={'assumptions':'gpu-setup'})
            update_task(root,add_assumptions=['Use 16GB per GPU, four cards gives 64GB total.'],semantic_subjects={'assumptions':'gpu-setup-16gb'})
            checkpoint=semantic_checkpoint(root); self.assertEqual(1,len(checkpoint['possibleStaleCognition']))
            stale=checkpoint['possibleStaleCognition'][0]; self.assertEqual('gpu-setup',stale['olderSubject']); self.assertEqual('gpu-setup-16gb',stale['newerSubject'])
            out=io.StringIO()
            with contextlib.redirect_stdout(out): rc=main_verify(root,self.args())
            self.assertEqual(1,rc); payload=json.loads(out.getvalue())
            warning=next(x for x in payload['contextWarnings'] if x['id']=='possible-stale-cognition')
            self.assertEqual(['gpu-setup -> gpu-setup-16gb'],warning['subjectTransitions'])
        finally: tmp.cleanup()
    def test_unrelated_subjects_do_not_create_stale_signal(self):
        tmp,root,_=self.make_root()
        try:
            update_task(root,add_assumptions=['RAM is 64GB'],semantic_subjects={'assumptions':'memory'})
            update_task(root,add_assumptions=['PSU is 1000W'],semantic_subjects={'assumptions':'power'})
            self.assertEqual([],semantic_checkpoint(root)['possibleStaleCognition'])
        finally: tmp.cleanup()
    def test_review_payload_disables_compact_for_strong_warning(self):
        tmp,root,_=self.make_root()
        try:
            out=io.StringIO()
            with contextlib.redirect_stdout(out): rc=main_verify(root,self.args())
            self.assertEqual(1,rc); payload=json.loads(out.getvalue()); self.assertFalse(payload['guidance']['compactAttestationAllowed'])
            self.assertEqual(['missing-source-provenance'],payload['guidance']['blockingWarningIds']); self.assertEqual(1,len(payload['guidance']['nextCommands']))
        finally: tmp.cleanup()
    def test_capabilities(self):
        tmp,root,_=self.make_root()
        try:
            caps=capabilities(root); self.assertGreaterEqual(CLI_CONTRACT_VERSION,30); self.assertGreaterEqual(WORKFLOW_CONTRACT_VERSION,21)
            self.assertTrue(caps['commands']['verification']['researchStrongWarningGate']); self.assertTrue(caps['commands']['cognition']['possibleStaleCognition'])
            self.assertTrue(caps['commands']['verification']['granularAttestationEscapeHatch'])
        finally: tmp.cleanup()
if __name__=='__main__': unittest.main()
