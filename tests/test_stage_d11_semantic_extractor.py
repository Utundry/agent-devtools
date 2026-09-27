from __future__ import annotations

import unittest

from agent_devtools.context.chunks import chunk_text
from agent_devtools.context.semantic import extract_semantic_document


class SemanticExtractorFoundationTests(unittest.TestCase):
    def test_php_modifiers_do_not_hide_class_or_method(self) -> None:
        text = """<?php\nfinal class MoneyService {\n    public static function allocate() {\n        return 1;\n    }\n}\n"""
        document = extract_semantic_document("src/MoneyService.php", text)
        self.assertIsNotNone(document)
        assert document is not None
        identities = [(d.kind, d.qualified_name, d.parent) for d in document.definitions]
        self.assertIn(("class", "MoneyService", None), identities)
        self.assertIn(("method", "MoneyService.allocate", "MoneyService"), identities)
        chunks = chunk_text("src/MoneyService.php", text, chunk_lines=80, overlap_lines=10)
        ids = {chunk.chunk_id for chunk in chunks}
        self.assertIn("symbol:src/MoneyService.php::MoneyService", ids)
        self.assertIn("symbol:src/MoneyService.php::MoneyService.allocate", ids)

    def test_typescript_modifiers_use_same_extractor_family(self) -> None:
        text = """export default class PaymentService {\n    public async function allocate() { return 1; }\n}\n"""
        document = extract_semantic_document("src/payment.ts", text)
        assert document is not None
        self.assertEqual("typescript", document.language)
        names = [d.qualified_name for d in document.definitions]
        self.assertEqual(["PaymentService", "PaymentService.allocate"], names)

    def test_java_modifiers_are_profile_data_not_engine_branch(self) -> None:
        text = "public final class Ledger {\n}\n"
        document = extract_semantic_document("src/Ledger.java", text)
        assert document is not None
        self.assertEqual("java", document.language)
        self.assertEqual("Ledger", document.definitions[0].qualified_name)

    def test_unknown_language_degrades_without_failure(self) -> None:
        self.assertIsNone(extract_semantic_document("src/file.xyz", "public final class Foo {}\n"))

    def test_symbol_identity_survives_line_shift(self) -> None:
        base = "final class MoneyService {\n public function allocate() { return 1; }\n}\n"
        moved = "// moved\n// again\n" + base
        before = chunk_text("src/MoneyService.php", base, chunk_lines=80, overlap_lines=10)
        after = chunk_text("src/MoneyService.php", moved, chunk_lines=80, overlap_lines=10)
        before_chunk = next(c for c in before if c.chunk_id.endswith("::MoneyService.allocate"))
        after_chunk = next(c for c in after if c.chunk_id.endswith("::MoneyService.allocate"))
        self.assertEqual(before_chunk.chunk_id, after_chunk.chunk_id)
        self.assertGreater(after_chunk.start_line, before_chunk.start_line)


if __name__ == "__main__":
    unittest.main()

class SemanticRelationFoundationTests(unittest.TestCase):
    def test_php_use_resolves_through_common_symbol_index(self) -> None:
        from agent_devtools.context.relations import build_definition_index, extract_relations
        docs = {
            "src/MoneyService.php": "<?php\nfinal class MoneyService {}\n",
            "src/Controller.php": "<?php\nuse App\\Money\\MoneyService;\nfinal class Controller {}\n",
        }
        index = build_definition_index(docs)
        resolved = extract_relations("src/Controller.php", docs["src/Controller.php"], set(docs), definition_index=index)
        self.assertIn(("src/MoneyService.php", "import"), resolved)

    def test_typescript_relative_import_uses_same_normalized_relation_model(self) -> None:
        from agent_devtools.context.relations import build_definition_index, extract_relations, extract_normalized_relations
        docs = {
            "src/payment.ts": "export class Payment {}\n",
            "src/app.ts": "import { Payment } from './payment';\n",
        }
        normalized = extract_normalized_relations("src/app.ts", docs["src/app.ts"])
        self.assertTrue(any(r.kind == "import:path" and r.target == "./payment" for r in normalized))
        resolved = extract_relations("src/app.ts", docs["src/app.ts"], set(docs), definition_index=build_definition_index(docs))
        self.assertIn(("src/payment.ts", "import"), resolved)

class RequiredPathBriefingTests(unittest.TestCase):
    def test_required_changed_path_cannot_be_displaced_by_preferred_neighbours(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from agent_devtools.context.affected import build_affected_briefing
        from agent_devtools.context.config import load_context_config

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src/ref").mkdir(parents=True)
            (root / "agent-tools.json").write_text(json.dumps({
                "version": 1,
                "ignore": [".agent-cache/**", ".agent-work/**"],
                "context": {"defaultBudget": 500, "candidateFiles": 2, "candidateChunks": 20, "kindCaps": {"source": 0.5}},
                "check": {"policy": "agent-check.policy.json", "suiteOrder": ["ref-tests"], "guards": [], "dependencies": [], "commands": {}},
            }), encoding="utf-8")
            (root / "agent-check.policy.json").write_text(json.dumps({
                "format": "agent-devtools-check-policy", "formatVersion": 1,
                "features": {"reference": {"affects": ["ref-tests"]}, "ref-tests": {"affects": []}},
                "sources": [{"patterns": ["src/ref/**"], "impact": ["reference"]}],
                "suites": {"ref-tests": {"impact": ["ref-tests"]}},
                "profiles": {"affected": {"selection": "affected", "suites": ["ref-tests"], "always": []}, "full": {"selection": "all", "suites": ["ref-tests"], "always": []}},
            }), encoding="utf-8")
            changed = root / "src/ref/RequisitesSchema.php"
            changed.write_text("<?php\nfinal class RequisitesSchema { public function normalize() {} }\n" + ("schema value filler\n" * 120), encoding="utf-8")
            (root / "src/ref/VeryRelevant.php").write_text("<?php\nfinal class ReferenceReferenceReference {\n" + ("reference requisites schema normalize\n" * 180) + "}\n", encoding="utf-8")
            config = load_context_config(root)
            briefing = build_affected_briefing(root, config, changed_files=("src/ref/RequisitesSchema.php",), limit=6, budget=500)
            self.assertIn("src/ref/RequisitesSchema.php", briefing.required_paths)
            self.assertTrue(any(item.path == "src/ref/RequisitesSchema.php" for item in briefing.results))

class AffectedJsonContractTests(unittest.TestCase):
    def test_scope_payload_exposes_required_paths_and_budget_omission(self) -> None:
        from agent_devtools.context.affected import AffectedBriefing

        briefing = AffectedBriefing(
            changed_files=("src/ref/RequisitesSchema.php",),
            impact_available=True,
            initial_impact=("reference",),
            effective_impact=("reference",),
            selected_suites=("ref-tests",),
            selected_application_groups=(),
            fallback_full=False,
            fallback_reasons=(),
            query="reference requisites",
            results=(),
            candidate_files=1,
            candidate_chunks=1,
            estimated_tokens=128,
            budget=128,
            omitted={"requiredPathBudgetExceeded": 2},
            required_paths=("src/ref/RequisitesSchema.php",),
            preferred_paths=("src/ref/ReferenceService.php",),
            excluded_paths=("src/money/MoneyService.php",),
        )
        self.assertEqual(
            {
                "requiredPaths": ["src/ref/RequisitesSchema.php"],
                "preferredPaths": ["src/ref/ReferenceService.php"],
                "excludedPaths": ["src/money/MoneyService.php"],
                "requiredPathBudgetExceeded": 2,
            },
            briefing.scope_payload(),
        )
