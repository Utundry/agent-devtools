from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_devtools import cli
from agent_devtools.check.cli import command_run
from agent_devtools.context.config import load_context_config
from agent_devtools.work.completion import complete_work
from agent_devtools.work.state import TaskStateError, align_task, load_task_state, start_task, update_task


@unittest.skipUnless(shutil.which("git"), "Git is required")
class RoutineCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("src", "docs"):
            (self.root / name).mkdir()
            (self.root / name / "item.txt").write_text("before\n")
        code = (
            "from pathlib import Path\nimport sys\n"
            "name = sys.argv[1]\n"
            "p = Path('.agent-work') / (name + '.calls')\n"
            "p.parent.mkdir(exist_ok=True)\n"
            "p.write_text(str(int(p.read_text()) + 1 if p.exists() else 1))\n"
            "sys.exit(1 if 'FAIL' in Path(name + '/item.txt').read_text() else 0)\n"
        )
        config = {
            "version": 1, "ignore": [".git/**", ".agent-work/**", ".agent-cache/**"],
            "work": {"profile": "development"},
            "check": {
                "policy": "agent-check.policy.json", "suiteOrder": ["src", "docs"],
                "commands": {name: {"argv": ["{python}", "-c", code, name],
                    "inputs": [name + "/**"], "tool": "python", "cache": True}
                    for name in ("src", "docs")},
            },
        }
        policy = {
            "format": "agent-devtools-check-policy", "formatVersion": 1,
            "features": {name: {"affects": []} for name in ("src", "docs")},
            "sources": [{"patterns": [name + "/**"], "impact": [name]} for name in ("src", "docs")],
            "suites": {name: {"impact": [name]} for name in ("src", "docs")},
            # No named full profile: the baseline must still cover every suite.
            "profiles": {"affected": {"selection": "affected", "suites": ["src", "docs"], "always": []}},
        }
        (self.root / "agent-tools.json").write_text(json.dumps(config))
        (self.root / "agent-check.policy.json").write_text(json.dumps(policy))
        (self.root / ".gitignore").write_text(".agent-work/\n.agent-cache/\n")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.commit()
        start_task(self.root, goal="Finish routine work")
        align_task(self.root, no_material_gaps=True)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True, stderr=subprocess.STDOUT)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def calls(self, suite):
        path = self.root / ".agent-work" / (suite + ".calls")
        return int(path.read_text()) if path.exists() else 0

    def reports(self):
        return list((self.root / ".agent-work/runs").glob("*/report.json"))

    def baseline(self):
        args = argparse.Namespace(profile="affected", base=None, changed=[], config=None,
            no_cache=False, resume=False, max_chunks=None, time_slice_seconds=None,
            json_output=True, minimum_baseline=True)
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(0, command_run(self.root, args))
        return json.loads(stream.getvalue())

    def test_complete_and_repeated_complete_reuse_pass_without_a_run_or_index(self):
        original = self.baseline()
        first = complete_work(self.root)
        second = complete_work(self.root)
        self.assertEqual("reused", first["verificationAction"])
        self.assertFalse(first["verificationPerformed"])
        self.assertEqual(original["runDirectory"], second["latestVerification"]["runDirectory"])
        self.assertEqual(1, len(self.reports()))
        self.assertEqual((1, 1), (self.calls("src"), self.calls("docs")))
        self.assertFalse(load_context_config(self.root).database.exists())

    def test_stale_source_runs_only_affected_suite(self):
        self.baseline()
        (self.root / "src/item.txt").write_text("changed\n")
        result = complete_work(self.root)
        self.assertEqual("executed", result["verificationAction"])
        self.assertEqual({"src"}, set(result["latestVerification"]["checks"]))
        self.assertEqual((2, 1), (self.calls("src"), self.calls("docs")))
        self.assertEqual(2, len(self.reports()))

    def test_recorded_committed_changes_are_used_for_selection(self):
        self.baseline()
        (self.root / "src/item.txt").write_text("committed change\n")
        self.commit()
        update_task(self.root, changed_files=["src/item.txt"])
        result = complete_work(self.root)
        self.assertEqual(["src/item.txt"], result["latestVerification"]["selection"]["changedFiles"])
        self.assertEqual((2, 1), (self.calls("src"), self.calls("docs")))

    def test_clean_tree_without_evidence_establishes_full_baseline(self):
        result = complete_work(self.root)
        report = result["latestVerification"]
        self.assertTrue(report["selection"]["fallbackFull"])
        self.assertEqual({"src", "docs"}, set(report["checks"]))
        self.assertEqual((1, 1), (self.calls("src"), self.calls("docs")))

    def test_committed_unrecorded_change_invalidates_baseline_reuse(self):
        self.baseline()
        (self.root / "src/item.txt").write_text("committed\n")
        self.commit()
        result = complete_work(self.root)
        self.assertTrue(result["verificationPerformed"])
        self.assertEqual((2, 1), (self.calls("src"), self.calls("docs")))
        # Both suites were selected, while the unchanged docs stage reused cache.
        self.assertEqual({"src", "docs"}, set(result["latestVerification"]["checks"]))

    def test_no_cache_physically_executes_despite_fresh_pass(self):
        self.baseline()
        result = complete_work(self.root, no_cache=True)
        self.assertEqual("executed", result["verificationAction"])
        self.assertEqual((2, 2), (self.calls("src"), self.calls("docs")))

    def test_report_tamper_triggers_new_verification(self):
        report = self.baseline()
        (Path(report["runDirectory"]) / "report.json").write_text("{}")
        result = complete_work(self.root)
        self.assertTrue(result["verificationPerformed"])
        self.assertIn("integrity", result["verificationReason"])
        self.assertEqual((2, 2), (self.calls("src"), self.calls("docs")))

    def test_failure_after_change_does_not_finish(self):
        self.baseline()
        (self.root / "src/item.txt").write_text("FAIL\n")
        with self.assertRaisesRegex(TaskStateError, "verification failed"):
            complete_work(self.root)
        self.assertEqual("active", load_task_state(self.root)["status"])

    def test_fresh_pass_does_not_bypass_alignment_or_blockers(self):
        self.baseline()
        align_task(self.root, material_gaps=["Architecture"], proposal="Clarify architecture")
        with self.assertRaisesRegex(TaskStateError, "alignment"):
            complete_work(self.root)
        align_task(self.root, user_approved=True, resolution="Use clarified architecture")
        update_task(self.root, add_blockers=["Incomplete work"])
        with self.assertRaisesRegex(TaskStateError, "blockers"):
            complete_work(self.root)
        self.assertEqual(1, len(self.reports()))

    def test_no_git_and_catch_all_policy_still_verify_every_suite(self):
        policy_path = self.root / "agent-check.policy.json"
        policy = json.loads(policy_path.read_text())
        policy["sources"] = [{"patterns": ["**"], "impact": ["src"]}]
        policy_path.write_text(json.dumps(policy))
        shutil.rmtree(self.root / ".git")
        result = complete_work(self.root)
        self.assertEqual({"src", "docs"}, set(result["latestVerification"]["checks"]))
        self.assertEqual((1, 1), (self.calls("src"), self.calls("docs")))
        self.assertFalse(complete_work(self.root)["verificationPerformed"])

    def test_workflow_default_is_short_and_details_preserve_responsibilities(self):
        def output(*args):
            stream = io.StringIO()
            with patch("agent_devtools.cli.discover_project_root", return_value=self.root), redirect_stdout(stream):
                self.assertEqual(0, cli.main(list(args)))
            return stream.getvalue()
        compact = output("workflow", "show")
        self.assertLess(len(compact.splitlines()), 10)
        self.assertIn("reuse current PASS", compact)
        self.assertNotIn("orient: required", compact)
        self.assertIn("orient: required", output("workflow", "show", "--details"))
        self.assertEqual(7, len(json.loads(output("workflow", "show", "--json"))["phases"]))


if __name__ == "__main__":
    unittest.main()
