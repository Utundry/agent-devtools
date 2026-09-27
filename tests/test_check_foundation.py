from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from agent_devtools.check.policy import GENERIC_POLICY_FORMAT, GENERIC_POLICY_VERSION, load_policy
from agent_devtools.core.evidence import CertifiedEvidenceStore
from agent_devtools.core.process import COMMAND_OUTPUT_TAIL_BYTES, ManagedProcessRunner


class PortablePolicyTests(unittest.TestCase):
    def test_generic_policy_format_works_without_application_groups_or_certification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps({
                "format": GENERIC_POLICY_FORMAT,
                "formatVersion": GENERIC_POLICY_VERSION,
                "features": {"api": {"affects": ["tests"]}, "tests": {"affects": []}},
                "sources": [
                    {"patterns": ["src/**"], "impact": ["api"]},
                    {"patterns": ["tests/**"], "impact": ["tests"]},
                ],
                "suites": {"unit": {"impact": ["tests"]}, "lint": {"impact": ["api", "tests"]}},
                "profiles": {
                    "affected": {"selection": "affected", "suites": ["unit", "lint"], "always": []},
                    "full": {"selection": "all", "suites": ["unit", "lint"], "always": []},
                },
            }), encoding="utf-8")
            policy = load_policy(path)
            plan = policy.plan("affected", {"src/example.py"})
            self.assertEqual(("api",), plan.initial_impact)
            self.assertEqual(("api", "tests"), plan.effective_impact)
            self.assertEqual(["unit", "lint"], [item["id"] for item in plan.selected])
            self.assertEqual((), plan.application_group_order)
            self.assertIsNone(policy.certification)

    def test_legacy_policy_format_remains_loadable_without_private_donor_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps({
                "format": "business-organizer-next-agent-check-policy",
                "formatVersion": 2,
                "features": {"domain": {"affects": ["tests"]}, "tests": {"affects": []}},
                "sources": [{"patterns": ["src/**"], "impact": ["domain"]}],
                "suites": {"application": {"impact": ["tests"]}},
                "applicationGroups": {"core": {"impact": ["tests"]}},
                "profiles": {
                    "affected": {"selection": "affected", "suites": ["application"], "always": []},
                    "full": {"selection": "all", "suites": ["application"], "always": []},
                },
                "certification": {
                    "forceColdPatterns": ["src/**"],
                    "applicationGlobalInputs": ["tests/**"],
                    "reusableStages": {"unit": {"tool": "python", "inputs": ["tests/**"]}},
                },
            }), encoding="utf-8")
            policy = load_policy(path)
            self.assertEqual("business-organizer-next-agent-check-policy", policy.source_format)
            self.assertEqual(("core",), policy.plan("full", {"src/a.py"}).application_group_order)
            self.assertIsNotNone(policy.certification)


class ManagedProcessTests(unittest.TestCase):
    def test_timeout_streams_log_and_returns_124(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = ManagedProcessRunner(root=root, log_dir=root / "logs", default_timeout_seconds=1.5)
            result = runner.run("hang", [sys.executable, "-c", "import time; print('before-timeout', flush=True); time.sleep(2)"])
            self.assertEqual(124, result.returncode)
            self.assertTrue(result.timed_out)
            self.assertIn("before-timeout", result.log_path.read_text(encoding="utf-8"))

    def test_large_output_is_streamed_with_bounded_memory_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = ManagedProcessRunner(root=root, log_dir=root / "logs", default_timeout_seconds=10)
            result = runner.run("large", [sys.executable, "-c", "import sys; sys.stdout.write('x'*2000000)"])
            self.assertEqual(0, result.returncode)
            self.assertGreater(result.log_path.stat().st_size, 1_900_000)
            self.assertLessEqual(len(result.output_tail.encode("utf-8")), COMMAND_OUTPUT_TAIL_BYTES)


class CertifiedEvidenceTests(unittest.TestCase):
    def test_queued_evidence_is_not_visible_until_successful_flush(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            store = CertifiedEvidenceStore(path)
            store.queue(kind="stage", item_id="unit", input_fingerprint="abc", source_fingerprint="source", completed_at_utc="2026-09-25T00:00:00Z", result={"tests": 3}, duration_seconds=1.5)
            self.assertIsNone(store.probe("stage", "unit", "abc"))
            self.assertFalse(path.exists())
            store.flush()
            reopened = CertifiedEvidenceStore(path)
            entry = reopened.probe("stage", "unit", "abc")
            self.assertIsNotNone(entry)
            self.assertEqual({"tests": 3}, entry["result"])

    def test_corrupt_evidence_fails_safe_to_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            path.write_text("not-json", encoding="utf-8")
            store = CertifiedEvidenceStore(path)
            self.assertEqual("corrupt", store.state)
            self.assertIsNone(store.probe("stage", "x", "y"))


class StageCacheTests(unittest.TestCase):
    def test_input_hashes_ignore_unrelated_files(self) -> None:
        from agent_devtools.check.cache import matching_input_hashes
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "src").mkdir(); (root / "docs").mkdir()
            (root / "src/a.py").write_text("a=1\n", encoding="utf-8")
            (root / "docs/note.md").write_text("one\n", encoding="utf-8")
            first = matching_input_hashes(root, ("src/**",), ())
            (root / "docs/note.md").write_text("two\n", encoding="utf-8")
            self.assertEqual(first, matching_input_hashes(root, ("src/**",), ()))
            (root / "src/a.py").write_text("a=2\n", encoding="utf-8")
            self.assertNotEqual(first, matching_input_hashes(root, ("src/**",), ()))

    def test_stage_cache_reuses_only_matching_key(self) -> None:
        from agent_devtools.check.cache import StageCache
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"; cache = StageCache(path)
            key = cache.key(suite="tests", argv=("python", "-V"), tool="python", input_hashes={"a": "1"})
            self.assertIsNone(cache.probe(key, "tests"))
            cache.queue(key, suite="tests", result={"count": 2}, completed_at_utc="2026-09-25T00:00:00Z"); cache.flush()
            reopened = StageCache(path)
            self.assertIsNotNone(reopened.probe(key, "tests"))
            other = reopened.key(suite="tests", argv=("python", "-V"), tool="python", input_hashes={"a": "2"})
            self.assertIsNone(reopened.probe(other, "tests"))


class WorkspaceTests(unittest.TestCase):
    def test_workspace_publishes_active_state_and_clears_it_on_finalize(self) -> None:
        from agent_devtools.core.workspace import RunWorkspace, active_status
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); old = os.environ.get("AGENT_DEVTOOLS_WORK_ROOT"); os.environ["AGENT_DEVTOOLS_WORK_ROOT"] = str(root / "work")
            try:
                workspace = RunWorkspace(root, "test")
                self.assertEqual("test", active_status(root)["mode"])
                workspace.finalize("pass")
                self.assertIsNone(active_status(root))
            finally:
                if old is None: os.environ.pop("AGENT_DEVTOOLS_WORK_ROOT", None)
                else: os.environ["AGENT_DEVTOOLS_WORK_ROOT"] = old


if __name__ == "__main__":
    unittest.main()
