import json
import tempfile
import unittest
from pathlib import Path
from agent_devtools import handoff_tasks as h

class HandoffTasksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def test_explicit_claim_return_and_close(self):
        obj = h.create(self.root, goal="Test UI", kind="delegation", steps=["Open", "Test"])
        self.assertEqual(h.listing(self.root)[0]["status"], "available")
        with self.assertRaises(h.TaskHandoffError): h.complete(self.root,obj['id'],claim_id='bad',outcome='pass',summary='OK')
        claimed = h.claim(self.root,obj['id'],"local-bot")
        with self.assertRaises(h.TaskHandoffError): h.claim(self.root,obj['id'],"another")
        result = h.complete(self.root,obj['id'],claim_id=claimed['claimId'],outcome='fail',summary='UI defect')
        self.assertEqual(result['outcome'],'fail')
        with self.assertRaises(h.TaskHandoffError): h.close(self.root,obj['id'],receipt_sha=result['receiptSha256'])
        h.close(self.root,obj['id'],receipt_sha=result['receiptSha256'],confirm=True)
        self.assertEqual(h.listing(self.root),[])
    def test_zombie_and_ttl(self):
        o=h.create(self.root,goal="Old",ttl_days=2)
        o['lastMeaningfulActivityAtUtc']='2020-01-01T00:00:00Z'
        self.assertTrue(h.flags(o)['zombie'])
        self.assertFalse(h.flags(o)['expired'])
        o['expiresAtUtc']='2020-01-03T00:00:00Z'
        h.atomic_json_write(h.item_path(self.root,o['id']),o)
        self.assertEqual(h.cleanup(self.root)['removed'],[o['id']])
    def test_config_rejects_autoclaim(self):
        (self.root/'agent-tools.json').write_text(json.dumps({'handoff':{'autoClaim':True}}))
        with self.assertRaises(h.TaskHandoffError): h.settings(self.root)
    def test_path_traversal(self):
        with self.assertRaises(h.TaskHandoffError): h.load(self.root, '../steal')

if __name__=='__main__': unittest.main()

class ExchangeTests(unittest.TestCase):
    def test_two_independent_environments(self):
        with tempfile.TemporaryDirectory() as td:
            a,b=Path(td)/'cloud',Path(td)/'local'
            a.mkdir(); b.mkdir()
            original=h.create(a,goal='Browser QA',kind='delegation',steps=['Open app','Check UI'])
            assignment=Path(td)/'assignment.json'; receipt=Path(td)/'receipt.json'
            h.export_assignment(a,original['id'],assignment)
            imported=h.import_assignment(b,assignment)
            self.assertFalse(imported['claimed'])
            with self.assertRaises(h.TaskHandoffError): h.import_assignment(b,assignment)
            claim=h.claim(b,original['id'],'local-vision-bot')
            report=h.complete(b,original['id'],claim_id=claim['claimId'],outcome='fail',summary='Column insertion regression',receipt_out=receipt)
            received=h.receive_result(a,receipt)
            self.assertEqual(received['receiptSha256'],report['receiptSha256'])
            self.assertEqual(h.receive_result(a,receipt)['status'],'already-received')
            h.close(b,original['id'],claim_id=claim['claimId'],confirm=True)
            h.close(a,original['id'],receipt_sha=report['receiptSha256'],confirm=True)
            self.assertEqual(h.listing(a),[])
            self.assertEqual(h.listing(b),[])
    def test_corrupt_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'a'; root.mkdir()
            obj=h.create(root,goal='QA')
            receipt=Path(td)/'r.json'
            receipt.write_text(json.dumps({'format':h.RESULT_FORMAT,'handoffId':obj['id'],'receiptSha256':'bad'}))
            with self.assertRaises(h.TaskHandoffError): h.receive_result(root,receipt)

class SelectionTests(unittest.TestCase):
    def test_unclaimed_delete_explicit_and_claimed_protected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            first=h.create(root,goal='Do not take me')
            with self.assertRaises(h.TaskHandoffError): h.discard(root,first['id'])
            h.discard(root,first['id'],confirm=True)
            second=h.create(root,goal='Claimed task')
            h.claim(root,second['id'],'agent-one')
            with self.assertRaises(h.TaskHandoffError): h.discard(root,second['id'],confirm=True)
