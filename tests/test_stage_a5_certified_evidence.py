from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.check.certification import certify_plan
from agent_devtools.check.config import load_check_config
from agent_devtools.check.policy import load_policy
from agent_devtools.check.selection import apply_selection_safety_guards
from agent_devtools.presets import get_preset


class CertificationHarness:
    @staticmethod
    def write_project(root: Path, commands: dict[str, dict], *, reusable: list[str] | None = None) -> None:
        suites = list(commands)
        (root / "agent-check.policy.json").write_text(json.dumps({
            "format": "agent-devtools-check-policy",
            "formatVersion": 1,
            "features": {suite: {"affects": []} for suite in suites},
            "sources": [{"patterns": ["src/**", "shared.cfg"], "impact": [suites[0]]}],
            "suites": {suite: {"impact": [suite]} for suite in suites},
            "profiles": {
                "affected": {"selection": "affected", "suites": suites, "always": []},
                "full": {"selection": "all", "suites": suites, "always": []},
            },
        }), encoding="utf-8")
        (root / "agent-tools.json").write_text(json.dumps({
            "version": 1,
            "ignore": [".agent-cache/**", ".agent-work/**", "**/__pycache__/**"],
            "check": {
                "policy": "agent-check.policy.json",
                "suiteOrder": suites,
                "guards": [],
                "dependencies": [],
                "certification": {
                    "profile": "full",
                    "reusableSuites": reusable if reusable is not None else suites,
                    "globalInputs": ["agent-tools.json", "agent-check.policy.json"],
                    "evidencePath": ".agent-cache/check-certified-evidence-v1.json",
                },
                "commands": commands,
            },
        }), encoding="utf-8")

    @staticmethod
    def certify(root: Path, *, cold: bool = False):
        config = load_check_config(root)
        policy = load_policy(config.policy_path)
        plan = apply_selection_safety_guards(policy.plan(config.certification.profile, ()), config)
        return certify_plan(root, config, plan, cold=cold)


class CertifiedEvidenceTests(unittest.TestCase, CertificationHarness):
    def test_cold_promotes_pass_and_warm_reuses_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src/input.txt").write_text("v1\n", encoding="utf-8")
            self.write_project(root, {
                "check": {
                    "argv": [
                        "{python}", "-c",
                        "from pathlib import Path; p=Path('counter.txt'); n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1))",
                    ],
                    "inputs": ["src/**"],
                    "tool": "python",
                    "resultAdapter": "exit-code",
                }
            })

            code1, report1 = self.certify(root, cold=True)
            self.assertEqual(0, code1)
            self.assertEqual("1", (root / "counter.txt").read_text())
            self.assertEqual(1, report1["certification"]["executedEvidence"])
            self.assertEqual(0, report1["certification"]["reusedEvidence"])
            self.assertTrue((root / ".agent-cache/check-certified-evidence-v1.json").is_file())

            code2, report2 = self.certify(root)
            self.assertEqual(0, code2)
            self.assertEqual("1", (root / "counter.txt").read_text())
            self.assertEqual(0, report2["certification"]["executedEvidence"])
            self.assertEqual(1, report2["certification"]["reusedEvidence"])
            self.assertEqual("reused", report2["checks"]["check"]["certifiedEvidence"]["status"])

            code3, report3 = self.certify(root, cold=True)
            self.assertEqual(0, code3)
            self.assertEqual("2", (root / "counter.txt").read_text())
            self.assertEqual(1, report3["certification"]["executedEvidence"])

    def test_failed_certification_never_promotes_queued_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src/input.txt").write_text("v1\n", encoding="utf-8")
            self.write_project(root, {
                "first": {
                    "argv": ["{python}", "-c", "print('pass')"],
                    "inputs": ["src/**"],
                    "resultAdapter": "exit-code",
                },
                "second": {
                    "argv": ["{python}", "-c", "raise SystemExit(7)"],
                    "inputs": ["src/**"],
                    "resultAdapter": "exit-code",
                },
            })
            code, report = self.certify(root)
            self.assertEqual(1, code)
            self.assertEqual("fail", report["status"])
            self.assertFalse((root / ".agent-cache/check-certified-evidence-v1.json").exists())

    def test_captured_output_must_still_match_before_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src/input.txt").write_text("v1\n", encoding="utf-8")
            self.write_project(root, {
                "build": {
                    "argv": [
                        "{python}", "-c",
                        "from pathlib import Path; c=Path('counter.txt'); n=int(c.read_text()) if c.exists() else 0; c.write_text(str(n+1)); p=Path('dist/out.txt'); p.parent.mkdir(exist_ok=True); p.write_text('certified')",
                    ],
                    "inputs": ["src/**"],
                    "resultAdapter": "exit-code",
                    "outputs": {"required": ["dist/out.txt"], "capture": ["dist/out.txt"]},
                }
            })
            self.assertEqual(0, self.certify(root, cold=True)[0])
            self.assertEqual("1", (root / "counter.txt").read_text())
            (root / "dist/out.txt").write_text("tampered", encoding="utf-8")
            code, report = self.certify(root)
            self.assertEqual(0, code)
            self.assertEqual("2", (root / "counter.txt").read_text())
            self.assertEqual("certified", (root / "dist/out.txt").read_text())
            self.assertEqual("executed", report["checks"]["build"]["certifiedEvidence"]["status"])

    def test_file_set_certification_reuses_unchanged_files_individually(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src/a.txt").write_text("a1\n", encoding="utf-8")
            (root / "src/b.txt").write_text("b1\n", encoding="utf-8")
            (root / "shared.cfg").write_text("shared\n", encoding="utf-8")
            self.write_project(root, {
                "lint": {
                    "argv": [
                        "{python}", "-c",
                        "import sys; from pathlib import Path; p=Path('executions.log'); old=p.read_text() if p.exists() else ''; p.write_text(old + Path(sys.argv[1]).name + '\\n')",
                        "{file}",
                    ],
                    "inputs": ["shared.cfg"],
                    "fileSet": {"patterns": ["src/*.txt"], "allowEmpty": False},
                    "resultAdapter": "exit-code",
                }
            })
            code1, report1 = self.certify(root, cold=True)
            self.assertEqual(0, code1)
            self.assertEqual(["a.txt", "b.txt"], (root / "executions.log").read_text().splitlines())
            self.assertEqual(2, report1["certification"]["executedEvidence"])

            (root / "src/a.txt").write_text("a2\n", encoding="utf-8")
            code2, report2 = self.certify(root)
            self.assertEqual(0, code2)
            self.assertEqual(["a.txt", "b.txt", "a.txt"], (root / "executions.log").read_text().splitlines())
            result = report2["checks"]["lint"]["result"]
            self.assertEqual(["b.txt"], [Path(x).name for x in result["reusedFiles"]])
            self.assertEqual(["a.txt"], [Path(x).name for x in result["executedFiles"]])
            self.assertEqual(1, report2["certification"]["reusedEvidence"])
            self.assertEqual(1, report2["certification"]["executedEvidence"])

    def test_file_set_captured_output_tamper_forces_complete_reexecution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src/a.txt").write_text("a1\n", encoding="utf-8")
            (root / "src/b.txt").write_text("b1\n", encoding="utf-8")
            self.write_project(root, {
                "build-each": {
                    "argv": [
                        "{python}", "-c",
                        "import sys; from pathlib import Path; "
                        "p=Path('executions.log'); old=p.read_text() if p.exists() else ''; "
                        "p.write_text(old + Path(sys.argv[1]).name + '\\n'); "
                        "d=Path('dist/out.txt'); d.parent.mkdir(exist_ok=True); "
                        "d.write_text(''.join(x.read_text() for x in sorted(Path('src').glob('*.txt'))))",
                        "{file}",
                    ],
                    "inputs": [],
                    "fileSet": {"patterns": ["src/*.txt"], "allowEmpty": False},
                    "resultAdapter": "exit-code",
                    "outputs": {"required": ["dist/out.txt"], "capture": ["dist/out.txt"]},
                }
            })

            code1, report1 = self.certify(root, cold=True)
            self.assertEqual(0, code1, report1)
            self.assertEqual(["a.txt", "b.txt"], (root / "executions.log").read_text().splitlines())
            self.assertEqual("a1\nb1\n", (root / "dist/out.txt").read_text())

            code2, report2 = self.certify(root)
            self.assertEqual(0, code2, report2)
            self.assertEqual(["a.txt", "b.txt"], (root / "executions.log").read_text().splitlines())
            self.assertEqual(
                ["a.txt", "b.txt"],
                [Path(x).name for x in report2["checks"]["build-each"]["result"]["reusedFiles"]],
            )
            self.assertEqual([], report2["checks"]["build-each"]["result"]["executedFiles"])

            (root / "dist/out.txt").write_text("tampered", encoding="utf-8")
            code3, report3 = self.certify(root)
            self.assertEqual(0, code3, report3)
            self.assertEqual(
                ["a.txt", "b.txt", "a.txt", "b.txt"],
                (root / "executions.log").read_text().splitlines(),
            )
            result = report3["checks"]["build-each"]["result"]
            self.assertEqual([], result["reusedFiles"])
            self.assertEqual(
                ["a.txt", "b.txt"],
                [Path(x).name for x in result["executedFiles"]],
            )
            self.assertEqual("a1\nb1\n", (root / "dist/out.txt").read_text())

    def test_corrupt_evidence_fails_safe_to_execution_and_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src/input.txt").write_text("v1\n", encoding="utf-8")
            self.write_project(root, {
                "check": {
                    "argv": [
                        "{python}", "-c",
                        "from pathlib import Path; p=Path('counter.txt'); n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1))",
                    ],
                    "inputs": ["src/**"],
                    "resultAdapter": "exit-code",
                }
            })
            self.assertEqual(0, self.certify(root, cold=True)[0])
            evidence = root / ".agent-cache/check-certified-evidence-v1.json"
            evidence.write_text("{broken", encoding="utf-8")
            code, report = self.certify(root)
            self.assertEqual(0, code)
            self.assertEqual("2", (root / "counter.txt").read_text())
            self.assertEqual("corrupt", report["certification"]["evidenceState"])
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual("agent-devtools-certified-evidence", payload["format"])


class CertificationPresetCompositionTests(unittest.TestCase):
    def test_composite_preset_reuses_atomic_certification_lists(self) -> None:
        preset = get_preset("php-vue-vite")
        certification = preset.files["agent-tools.json"]["check"]["certification"]
        self.assertEqual(
            ["backend-tests", "backend-composer", "frontend-typecheck", "frontend-tests", "frontend-build"],
            certification["reusableSuites"],
        )
        self.assertEqual("full", certification["profile"])


if __name__ == "__main__":
    unittest.main()
