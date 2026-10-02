from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import tempfile
import tracemalloc
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_devtools.bootstrap import classify_intent
from agent_devtools.check.cli import command_run, command_certify
from agent_devtools.check.cache import matching_input_hashes
from agent_devtools.check.contracts import matching_paths
from agent_devtools.context.config import load_context_config
from agent_devtools.context.index import ensure_index
from agent_devtools.core.archive import ArchiveSafetyError, extract_zip_bounded, read_zip_bounded
from agent_devtools.core.hashing import sha256_file
from agent_devtools.work.brief import build_brief
from agent_devtools.work.completion import complete_work
from agent_devtools.work.state import TaskStateError, start_task, align_task, load_task_state
from agent_devtools.work.verification import record_verification
from agent_devtools.workspace_snapshot import create_snapshot, inspect_snapshot, restore_snapshot, WorkspaceSnapshotError


def project(root: Path, code="print('ok')\n", **options):
    (root / 'src').mkdir()
    (root / 'src/probe.py').write_text(code)
    spec = {'argv': ['{python}', 'src/probe.py'], 'inputs': ['src/**'], 'tool': 'python', 'cache': True, 'resultAdapter': 'exit-code', **options}
    raw = {'version': 1, 'ignore': ['.agent-work/**', '.agent-cache/**', '**/__pycache__/**'],
           'work': {'profile': 'development'}, 'check': {'policy': 'agent-check.policy.json', 'suiteOrder': ['probe'], 'commands': {'probe': spec}}}
    (root / 'agent-tools.json').write_text(json.dumps(raw))
    policy = {'format': 'agent-devtools-check-policy', 'formatVersion': 1, 'features': {'source': {'affects': []}},
              'sources': [{'patterns': ['**/*.py'], 'impact': ['source']}], 'suites': {'probe': {'impact': ['source']}},
              'profiles': {'affected': {'selection': 'affected', 'suites': ['probe'], 'always': []},
                           'full': {'selection': 'all', 'suites': ['probe'], 'always': []}}}
    (root / 'agent-check.policy.json').write_text(json.dumps(policy))
    start_task(root, goal='Audit regression')
    align_task(root, no_material_gaps=True)


def run(root: Path, no_cache=False, profile='full', changed=()):
    args = argparse.Namespace(profile=profile, base=None, changed=list(changed), config=None, no_cache=no_cache,
                              resume=False, max_chunks=None, time_slice_seconds=None, json_output=True)
    output = io.StringIO()
    with redirect_stdout(output):
        code = command_run(root, args)
    return code, json.loads(output.getvalue())


class VerificationIntegrityTests(unittest.TestCase):
    def test_finish_does_not_execute_unchanged_check_again(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root)
            self.assertEqual(0, run(root)[0])
            before = len(list((root / '.agent-work/runs').iterdir()))
            with patch('agent_devtools.check.cli.command_run', side_effect=AssertionError('unnecessary execution')):
                self.assertEqual('completed', complete_work(root, run_verification=False)['task']['status'])
            self.assertEqual(before, len(list((root / '.agent-work/runs').iterdir())))

    def test_finish_rejects_modified_added_and_deleted_inputs(self):
        for mutation in ('modify', 'add', 'delete'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as td:
                root = Path(td); project(root); run(root)
                if mutation == 'modify': (root / 'src/probe.py').write_text('invalid !!!\n')
                elif mutation == 'add': (root / 'src/new.py').write_text('pass\n')
                else: (root / 'src/probe.py').unlink()
                with self.assertRaisesRegex(TaskStateError, 'stale'):
                    complete_work(root, run_verification=False)
                self.assertEqual('active', load_task_state(root)['status'])

    def test_finish_rejects_tampered_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root); _, report = run(root)
            path = Path(report['runDirectory']) / 'report.json'
            report['checks'] = {}; path.write_text(json.dumps(report))
            with self.assertRaisesRegex(TaskStateError, 'integrity'):
                complete_work(root, run_verification=False)

    def test_finish_rejects_changed_configuration_and_other_task(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root); run(root)
            config = root / 'agent-tools.json'; config.write_text(config.read_text() + '\n')
            with self.assertRaisesRegex(TaskStateError, 'config.*stale'):
                complete_work(root, run_verification=False)
            run(root); start_task(root, goal='Successor', replace=True); align_task(root, no_material_gaps=True)
            with self.assertRaisesRegex(TaskStateError, 'current task'):
                complete_work(root, run_verification=False)

    def test_external_config_remains_supported_by_check_and_finish(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td); root = parent / 'project'; root.mkdir(); project(root)
            external = parent / 'agent-tools.json'
            external.write_bytes((root / 'agent-tools.json').read_bytes())
            args = argparse.Namespace(profile='full', base=None, changed=[], config=external, no_cache=True,
                                      resume=False, max_chunks=None, time_slice_seconds=None, json_output=True)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, command_run(root, args))
            self.assertEqual('completed', complete_work(root, run_verification=False)['task']['status'])

    def test_environment_changes_invalidate_default_cache_without_exposing_values(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root, "import os\nassert os.environ['AUDIT_FLAG'] == 'good'\n")
            with patch.dict(os.environ, {'AUDIT_FLAG': 'good'}): self.assertEqual(0, run(root)[0])
            with patch.dict(os.environ, {'AUDIT_FLAG': 'secret-bad-value'}):
                code, report = run(root)
            self.assertEqual(1, code)
            self.assertEqual('miss', report['checks']['probe']['cache']['status'])
            self.assertNotIn('secret-bad-value', json.dumps(report))

    def test_explicit_cache_environment_avoids_unrelated_invalidations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root, cacheEnv=['AUDIT_RELEVANT'])
            with patch.dict(os.environ, {'AUDIT_RELEVANT': 'same', 'AUDIT_UNRELATED': 'before'}): run(root)
            with patch.dict(os.environ, {'AUDIT_RELEVANT': 'same', 'AUDIT_UNRELATED': 'after'}):
                self.assertEqual('hit', run(root)[1]['checks']['probe']['cache']['status'])
            with patch.dict(os.environ, {'AUDIT_RELEVANT': 'different'}):
                self.assertEqual('miss', run(root)[1]['checks']['probe']['cache']['status'])

    def test_cache_environment_patterns_track_new_relevant_names(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root, cacheEnv=['AUDIT_RELEVANT*'])
            with patch.dict(os.environ, {'AUDIT_RELEVANT': 'same', 'AUDIT_UNRELATED': 'before'}): run(root)
            with patch.dict(os.environ, {'AUDIT_RELEVANT': 'same', 'AUDIT_UNRELATED': 'after'}):
                self.assertEqual('hit', run(root)[1]['checks']['probe']['cache']['status'])
            with patch.dict(os.environ, {'AUDIT_RELEVANT': 'same', 'AUDIT_RELEVANT_NEW': 'new'}):
                self.assertEqual('miss', run(root)[1]['checks']['probe']['cache']['status'])

    def test_changed_hard_deadline_does_not_reuse_prior_long_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root, 'import time\ntime.sleep(0.05)\n', timeoutSeconds=1)
            self.assertEqual(0, run(root)[0])
            path = root / 'agent-tools.json'; config = json.loads(path.read_text())
            config['check']['commands']['probe']['timeoutSeconds'] = 0.005
            path.write_text(json.dumps(config))
            code, result = run(root)
            self.assertEqual(1, code)
            self.assertTrue(result['checks']['probe']['timedOut'])
            self.assertEqual('miss', result['checks']['probe']['cache']['status'])

    def test_root_input_glob_prevents_stale_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root, "import ast\nfrom pathlib import Path\nast.parse(Path('top.py').read_text())\n", inputs=['**/*.py'])
            (root / 'top.py').write_text('pass\n'); self.assertEqual(0, run(root)[0])
            (root / 'top.py').write_text('invalid !!!\n')
            code, report = run(root)
            self.assertEqual(1, code); self.assertEqual('miss', report['checks']['probe']['cache']['status'])

    def test_cache_retains_origin_and_marks_disposable_evidence_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root); _, cold = run(root)
            _, warm = run(root)
            origin = warm['checks']['probe']['executionEvidence']
            path = root / origin['report']
            self.assertEqual(Path(cold['runDirectory']) / 'report.json', path)
            self.assertEqual(sha256_file(path), origin['reportSha256'])
            self.assertTrue(origin['detailsAvailable'])
            shutil.rmtree(cold['runDirectory'])
            _, warm = run(root)
            self.assertEqual('hit', warm['checks']['probe']['cache']['status'])
            self.assertFalse(warm['checks']['probe']['executionEvidence']['detailsAvailable'])

    def test_corrupt_origin_report_prevents_cached_reuse(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root); _, cold = run(root)
            (Path(cold['runDirectory']) / 'report.json').write_text('{}')
            code, result = run(root)
            self.assertEqual(0, code)
            self.assertEqual('miss', result['checks']['probe']['cache']['status'])

    def test_finish_accepts_bound_cold_certification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root)
            args = argparse.Namespace(profile="full", base=None, changed=[], config=None, cold=True,
                resume=False, max_chunks=None, time_slice_seconds=None, json_output=True)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, command_certify(root, args))
            self.assertEqual("completed", complete_work(root, run_verification=False)["task"]["status"])

    def test_finish_rejects_tampered_generated_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root, "from pathlib import Path\nPath('generated.txt').write_text('expected')\n",
                                      outputs={"capture": ["generated.txt"]})
            run(root); (root / "generated.txt").write_text("tampered")
            with self.assertRaisesRegex(TaskStateError, "stale"):
                complete_work(root, run_verification=False)

    def test_finish_rejects_incomplete_affected_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); project(root)
            (root / "other").mkdir(); (root / "other/a.py").write_text("pass\n")
            config = json.loads((root / "agent-tools.json").read_text())
            config["check"]["suiteOrder"].append("other")
            config["check"]["commands"]["other"] = {"argv": ["{python}", "other/a.py"], "inputs": ["other/**"], "tool": "python"}
            (root / "agent-tools.json").write_text(json.dumps(config))
            policy = json.loads((root / "agent-check.policy.json").read_text())
            policy["features"]["other"] = {"affects": []}
            policy["sources"] = [{"patterns": ["src/**"], "impact": ["source"]}, {"patterns": ["other/**"], "impact": ["other"]}]
            policy["suites"]["other"] = {"impact": ["other"]}
            for profile in policy["profiles"].values(): profile["suites"].append("other")
            (root / "agent-check.policy.json").write_text(json.dumps(policy))
            (root / ".gitignore").write_text(".agent-work/\n.agent-cache/\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "base"], check=True)
            (root / "src/probe.py").write_text("print('changed')\n")
            (root / "other/a.py").write_text("invalid !!!\n")
            self.assertEqual(0, run(root, profile="affected", changed=["src/probe.py"])[0])
            with self.assertRaisesRegex(TaskStateError, "coverage"):
                complete_work(root, run_verification=False)


class PreservationIntegrityTests(unittest.TestCase):
    def make_root(self, root):
        (root / 'agent-tools.json').write_text(json.dumps({'work': {'profile': 'analysis'}}))
        (root / 'facts.md').write_text('Important facts\n')

    def test_repeated_snapshots_do_not_embed_previous_outputs_or_drop_user_archives(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.make_root(root)
            with zipfile.ZipFile(root / 'user.zip', 'w') as archive: archive.writestr('user.txt', 'keep')
            first = create_snapshot(root, out=root / 'custom.zip')
            second = create_snapshot(root, out=root / 'custom.zip')
            self.assertEqual(first['bytes'], second['bytes'])
            self.assertEqual(first['artifacts'], second['artifacts'])
            with zipfile.ZipFile(second['path']) as archive:
                self.assertIn('artifacts/user.zip', archive.namelist())
                self.assertNotIn('artifacts/custom.zip', archive.namelist())

    def test_snapshot_state_conflicts_are_checked_before_any_writes(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td); source = parent / 'source'; target = parent / 'target'; source.mkdir(); target.mkdir()
            self.make_root(source)
            (target / 'agent-tools.json').write_bytes((source / 'agent-tools.json').read_bytes())
            start_task(source, goal='Snapshot'); start_task(target, goal='Keep existing task')
            snap = create_snapshot(source, out=parent / 'snap.zip')
            with self.assertRaisesRegex(WorkspaceSnapshotError, 'task.json'):
                restore_snapshot(Path(snap['path']), target)
            self.assertFalse((target / 'facts.md').exists())
            self.assertEqual('Keep existing task', load_task_state(target)['goal'])
            restore_snapshot(Path(snap['path']), target, force=True)
            self.assertEqual('Snapshot', load_task_state(target)['goal'])

    def test_restore_parent_file_conflicts_are_preflighted(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td); source = parent / 'source'; target = parent / 'target'; source.mkdir(); target.mkdir()
            self.make_root(source); (source / 'nested').mkdir(); (source / 'nested/payload.txt').write_text('data')
            (target / 'nested').write_text('Keep parent file')
            snap = create_snapshot(source, out=parent / 'snap.zip')
            with self.assertRaisesRegex(WorkspaceSnapshotError, 'nested/payload.txt'):
                restore_snapshot(Path(snap['path']), target)
            self.assertFalse((target / 'facts.md').exists())
            self.assertEqual('Keep parent file', (target / 'nested').read_text())

    def test_verification_only_conflict_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td); source = parent / 'source'; target = parent / 'target'; source.mkdir(); target.mkdir()
            self.make_root(source); (target / 'agent-tools.json').write_bytes((source / 'agent-tools.json').read_bytes())
            record_verification(source, label='original', status='pass'); record_verification(target, label='different', status='pass')
            snap = create_snapshot(source, out=parent / 'snap.zip')
            with self.assertRaisesRegex(WorkspaceSnapshotError, 'verification.json'):
                restore_snapshot(Path(snap['path']), target)
            self.assertFalse((target / 'facts.md').exists())

    def test_size_cap_rejects_large_file_before_allocation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.make_root(root)
            with (root / 'large.bin').open('wb') as stream: stream.truncate(8 * 1024 * 1024)
            tracemalloc.start()
            try:
                with self.assertRaisesRegex(WorkspaceSnapshotError, 'max bytes'):
                    create_snapshot(root, out=root / 'out.zip', max_bytes=1024 * 1024)
                _, peak = tracemalloc.get_traced_memory()
            finally: tracemalloc.stop()
            self.assertLess(peak, 2 * 1024 * 1024)
            self.assertFalse((root / 'out.zip').exists())
            self.assertFalse((root / 'out.zip.tmp').exists())

    def test_snapshot_inspect_and_restore_stream_large_payload(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td); root = parent / 'source'; root.mkdir(); self.make_root(root)
            block = os.urandom(1024 * 1024)
            with (root / 'large.bin').open('wb') as stream:
                for _ in range(8): stream.write(block)
            snap = create_snapshot(root, out=parent / 'snapshot.zip')
            tracemalloc.start()
            try:
                inspect_snapshot(Path(snap['path']))
                restore_snapshot(Path(snap['path']), parent / 'target')
                _, peak = tracemalloc.get_traced_memory()
            finally: tracemalloc.stop()
            self.assertLess(peak, 5 * 1024 * 1024)
            self.assertEqual(sha256_file(root / 'large.bin'), sha256_file(parent / 'target/large.bin'))

    def test_archive_aliases_and_file_directory_collisions_fail_before_extraction(self):
        for names in [('a/b.txt', 'a/./b.txt'), ('a/b.txt', 'a//b.txt'), ('a', 'a/b.txt'), ('a/b.txt', 'a')]:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as td:
                root = Path(td); path = root / 'bad.zip'
                with zipfile.ZipFile(path, 'w') as archive:
                    for name in names: archive.writestr(name, 'payload')
                with self.assertRaises(ArchiveSafetyError): read_zip_bounded(path)
                with self.assertRaises(ArchiveSafetyError): extract_zip_bounded(path, root / 'out')
                self.assertFalse((root / 'out/a').exists())


class EfficiencyAndWorkflowTests(unittest.TestCase):
    def test_ignored_tree_is_not_descended_into(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / 'src').mkdir(); (root / 'src/a.py').write_text('pass\n')
            (root / 'node_modules/nested').mkdir(parents=True); (root / 'node_modules/nested/noise.py').write_text('pass\n')
            walk, visited = os.walk, []
            def observed(*args, **kwargs):
                for directory, dirs, files in walk(*args, **kwargs):
                    visited.append(directory); yield directory, dirs, files
            with patch('agent_devtools.core.files.os.walk', observed):
                hashes = matching_input_hashes(root, ('**/*.py',), ('node_modules/**',))
            self.assertEqual(['src/a.py'], list(hashes))
            self.assertFalse(any('node_modules' in str(path) for path in visited))
            self.assertEqual(['a.py'], [path.name for path in matching_paths(root, ('src/**/*.py',))])

    def test_research_brief_refreshes_existing_index(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / 'agent-tools.json').write_text(json.dumps({'work': {'profile': 'research'}}))
            start_task(root, goal='solar radiation')
            (root / 'facts.md').write_text('# solar\nsolar radiation OLD_MARKER\n')
            ensure_index(load_context_config(root))
            (root / 'facts.md').write_text('# solar\nsolar radiation NEW_MARKER\n')
            brief = build_brief(root)
            text = '\n'.join(row['content'] for row in brief['context']['results'])
            self.assertIn('NEW_MARKER', text); self.assertNotIn('OLD_MARKER', text)
            self.assertEqual('metadata', brief['fingerprintKind'])

    def test_business_intents_do_not_require_software_stack(self):
        for text in ('Оценить эффективность сервиса доставки', 'Разработать методику анализа воды', 'Prepare a proposal for building renovation', 'Research API benchmark', 'Analysis of clinical outcomes'):
            self.assertFalse(classify_intent(text).development, text)
            self.assertFalse(classify_intent(text).needs_stack, text)
        self.assertEqual('python-pytest', classify_intent('Build Python API with pytest').preset_id)


if __name__ == '__main__':
    unittest.main()
