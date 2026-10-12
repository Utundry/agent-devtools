from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.update_scenario import UpdateScenarioError, apply_scenario, load_scenario, preflight_scenarios, scenario_marker


class UpdateScenarioTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (root / "a.txt").write_text("before\n", encoding="utf-8")
        return tmp, root

    def write_scenario(self, root, payload):
        path = root / "scenario.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def base(self):
        return {
            "format": "agent-devtools-update-scenario",
            "formatVersion": 1,
            "fromVersion": "1.0.0",
            "toVersion": "1.1.0",
            "title": "test",
            "changes": [],
        }

    def test_replace_and_write(self):
        tmp, root = self.make_root()
        try:
            payload = self.base()
            payload["changes"] = [
                {"op": "replace", "path": "a.txt", "before": "before", "after": "after"},
                {"op": "write", "path": "new/child.txt", "content": "hello\n"},
            ]
            scenario = load_scenario(self.write_scenario(root, payload))
            first = apply_scenario(root, scenario)
            self.assertEqual(2, first["applied"])
            self.assertEqual("after\n", (root / "a.txt").read_text())
            self.assertTrue(scenario_marker(scenario).startswith("Agent-DevTools-Scenario-SHA256: "))
        finally:
            tmp.cleanup()

    def test_replace_retry_is_idempotent(self):
        tmp, root = self.make_root()
        try:
            payload = self.base()
            payload["changes"] = [
                {"op": "replace", "path": "a.txt", "before": "before", "after": "after"},
            ]
            scenario = load_scenario(self.write_scenario(root, payload))
            apply_scenario(root, scenario)
            second = apply_scenario(root, scenario)
            self.assertEqual(0, second["applied"])
            self.assertEqual(1, second["unchanged"])
        finally:
            tmp.cleanup()

    def test_replace_rejects_after_contained_in_before(self):
        tmp, root = self.make_root()
        try:
            payload = self.base()
            payload["changes"] = [
                {
                    "op": "replace",
                    "path": "a.txt",
                    "before": "from x import A\nfrom y import B\n",
                    "after": "from y import B\n",
                },
            ]
            with self.assertRaisesRegex(UpdateScenarioError, "structurally ambiguous"):
                load_scenario(self.write_scenario(root, payload))
        finally:
            tmp.cleanup()

    def test_replace_rejects_before_contained_in_after(self):
        tmp, root = self.make_root()
        try:
            payload = self.base()
            payload["changes"] = [
                {
                    "op": "replace",
                    "path": "a.txt",
                    "before": "value = 1",
                    "after": "value = 1\nextra = 2",
                },
            ]
            with self.assertRaisesRegex(UpdateScenarioError, "structurally ambiguous"):
                load_scenario(self.write_scenario(root, payload))
        finally:
            tmp.cleanup()

    def test_base_blob_mismatch_fails_before_mutation(self):
        # Keep this preimage boundary distinct from the inserted authoring tests.
        tmp, root = self.make_root()
        try:
            payload = self.base()
            payload["baseBlobs"] = {"a.txt": "0" * 40}
            payload["changes"] = [
                {"op": "replace", "path": "a.txt", "before": "before", "after": "after"},
            ]
            scenario = load_scenario(self.write_scenario(root, payload))
            with self.assertRaisesRegex(UpdateScenarioError, "baseline blob mismatch"):
                apply_scenario(root, scenario)
            self.assertEqual("before\n", (root / "a.txt").read_text())
        finally:
            tmp.cleanup()

    def test_base_blob_mismatch_can_be_explicitly_ignored_without_disabling_content_guards(self):
        tmp, root = self.make_root()
        try:
            payload = self.base()
            payload["baseBlobs"] = {"a.txt": "0" * 40}
            payload["changes"] = [
                {"op": "replace", "path": "a.txt", "before": "before", "after": "after"},
            ]
            scenario = load_scenario(self.write_scenario(root, payload))
            result = apply_scenario(root, scenario, ignore_base_blobs=True)
            checks = result["baselineBlobChecks"]
            self.assertFalse(checks["enforced"])
            self.assertEqual(1, checks["ignoredMismatchCount"])
            self.assertEqual("a.txt", checks["mismatches"][0]["path"])
            self.assertEqual("after\n", (root / "a.txt").read_text())

            (root / "a.txt").write_text("unexpected\n", encoding="utf-8")
            with self.assertRaisesRegex(UpdateScenarioError, "replace state is ambiguous"):
                apply_scenario(root, scenario, ignore_base_blobs=True)
        finally:
            tmp.cleanup()

    def test_ignore_base_blobs_does_not_ignore_missing_baseline_file(self):
        tmp, root = self.make_root()
        try:
            payload = self.base()
            payload["baseBlobs"] = {"missing.txt": "0" * 40}
            payload["changes"] = [
                {"op": "assert_contains", "path": "a.txt", "text": "before"},
            ]
            scenario = load_scenario(self.write_scenario(root, payload))
            with self.assertRaisesRegex(UpdateScenarioError, "baseline file missing"):
                apply_scenario(root, scenario, ignore_base_blobs=True)
        finally:
            tmp.cleanup()

    def test_write_overwrite_requires_exact_preimage(self):
        tmp, root = self.make_root()
        try:
            payload = self.base()
            payload["changes"] = [{"op": "write", "path": "a.txt", "content": "new\n"}]
            scenario = load_scenario(self.write_scenario(root, payload))
            with self.assertRaisesRegex(UpdateScenarioError, "refuses to overwrite"):
                apply_scenario(root, scenario)
            payload["changes"][0]["expectedSha256"] = hashlib.sha256((root / "a.txt").read_bytes()).hexdigest()
            scenario = load_scenario(self.write_scenario(root, payload))
            apply_scenario(root, scenario)
            self.assertEqual("new\n", (root / "a.txt").read_text())
        finally:
            tmp.cleanup()

    def test_delete_requires_hash_and_path_escape_is_rejected(self):
        tmp, root = self.make_root()
        try:
            payload = self.base()
            payload["changes"] = [{"op": "delete", "path": "a.txt"}]
            with self.assertRaisesRegex(UpdateScenarioError, "requires expectedSha256"):
                load_scenario(self.write_scenario(root, payload))
            payload["changes"] = [{"op": "write", "path": "../escape.txt", "content": "x"}]
            with self.assertRaisesRegex(UpdateScenarioError, "unsafe scenario path"):
                load_scenario(self.write_scenario(root, payload))
        finally:
            tmp.cleanup()


    def test_preflight_is_sparse_and_does_not_mutate_project(self):
        tmp, root = self.make_root()
        try:
            payload = self.base()
            payload["changes"] = [
                {"op": "replace", "path": "a.txt", "before": "before", "after": "after"},
                {"op": "assert_contains", "path": "a.txt", "text": "after"},
            ]
            scenario = load_scenario(self.write_scenario(root, payload))
            result = preflight_scenarios(root, [scenario])
            self.assertEqual("pass", result["status"])
            self.assertEqual(1, result["scenarioCount"])
            self.assertEqual("before\n", (root / "a.txt").read_text())
            self.assertEqual("1.0.0\n", (root / "VERSION").read_text())
        finally:
            tmp.cleanup()

    def test_preflight_catches_assertion_before_real_apply(self):
        tmp, root = self.make_root()
        try:
            payload = self.base()
            payload["changes"] = [
                {"op": "assert_contains", "path": "a.txt", "text": "missing-marker"},
            ]
            scenario = load_scenario(self.write_scenario(root, payload))
            with self.assertRaisesRegex(
                UpdateScenarioError,
                r"scenario\.json \(1\.0\.0 -> 1\.1\.0\): assert_contains failed",
            ):
                preflight_scenarios(root, [scenario])
            self.assertEqual("before\n", (root / "a.txt").read_text())
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
