from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORMAT = "agent-devtools-update-scenario"
FORMAT_VERSION = 1
_SAFE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?$")


class UpdateScenarioError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateScenario:
    path: Path
    digest: str
    from_version: str
    to_version: str
    title: str
    commit_message: str
    base_blobs: dict[str, str]
    contracts: tuple[dict[str, Any], ...]
    changes: tuple[dict[str, Any], ...]


def _safe_rel(value: Any) -> str:
    rel = str(value or "").replace("\\", "/").strip()
    if not rel or rel.startswith("/") or rel == ".." or rel.startswith("../") or "/../" in f"/{rel}/":
        raise UpdateScenarioError(f"unsafe scenario path: {rel!r}")
    return rel


def _path(root: Path, rel: str) -> Path:
    root = root.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise UpdateScenarioError(f"scenario path escapes project root: {rel}") from exc
    return target


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def load_scenario(path: Path) -> UpdateScenario:
    path = path.expanduser().resolve()
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateScenarioError(f"cannot read update scenario: {exc}") from exc
    if not isinstance(payload, dict):
        raise UpdateScenarioError("update scenario must be a JSON object")
    if payload.get("format") != FORMAT or payload.get("formatVersion") != FORMAT_VERSION:
        raise UpdateScenarioError("unsupported update scenario format/version")

    from_version = str(payload.get("fromVersion") or "").strip()
    to_version = str(payload.get("toVersion") or "").strip()
    if not _SAFE_VERSION.fullmatch(from_version) or not _SAFE_VERSION.fullmatch(to_version):
        raise UpdateScenarioError("scenario fromVersion/toVersion must be semantic release versions")
    if from_version == to_version:
        raise UpdateScenarioError("scenario fromVersion and toVersion must differ")

    title = str(payload.get("title") or "").strip()
    commit_message = str(payload.get("commitMessage") or title or f"Apply update scenario {to_version}").strip()
    if not commit_message or "\n" in commit_message or "\r" in commit_message:
        raise UpdateScenarioError("scenario commitMessage must be one non-empty line")

    base_raw = payload.get("baseBlobs", {})
    if not isinstance(base_raw, dict):
        raise UpdateScenarioError("scenario baseBlobs must be an object")
    base_blobs: dict[str, str] = {}
    for key, value in base_raw.items():
        rel = _safe_rel(key)
        digest = str(value or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", digest):
            raise UpdateScenarioError(f"invalid Git blob SHA-1 for {rel}")
        base_blobs[rel] = digest

    contracts_raw = payload.get("contracts", {})
    if not isinstance(contracts_raw, dict):
        raise UpdateScenarioError("scenario contracts must be an object")
    contracts: list[dict[str, Any]] = []
    for raw_name, raw_contract in contracts_raw.items():
        name = str(raw_name or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
            raise UpdateScenarioError(f"invalid contract name: {name!r}")
        if not isinstance(raw_contract, dict):
            raise UpdateScenarioError(f"contract {name} must be an object")
        rel = _safe_rel(raw_contract.get("path"))
        symbol = str(raw_contract.get("symbol") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
            raise UpdateScenarioError(f"invalid contract symbol for {name}: {symbol!r}")
        before = raw_contract.get("from")
        after = raw_contract.get("to")
        if isinstance(before, bool) or not isinstance(before, int) or before < 0:
            raise UpdateScenarioError(f"contract {name} from must be a non-negative integer")
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise UpdateScenarioError(f"contract {name} to must be a non-negative integer")
        contracts.append({
            "name": name,
            "path": rel,
            "symbol": symbol,
            "from": before,
            "to": after,
        })

    changes_raw = payload.get("changes")
    if not isinstance(changes_raw, list) or not changes_raw:
        raise UpdateScenarioError("scenario changes must be a non-empty array")
    changes: list[dict[str, Any]] = []
    for index, raw_change in enumerate(changes_raw):
        if not isinstance(raw_change, dict):
            raise UpdateScenarioError(f"scenario change {index} must be an object")
        change = dict(raw_change)
        op = str(change.get("op") or "")
        if op not in {"replace", "write", "delete", "assert_contains"}:
            raise UpdateScenarioError(f"unsupported scenario op at index {index}: {op!r}")
        change["path"] = _safe_rel(change.get("path"))

        if op == "replace":
            before, after = change.get("before"), change.get("after")
            if not isinstance(before, str) or not before or not isinstance(after, str) or not after:
                raise UpdateScenarioError(f"replace change {index} requires non-empty string before/after")
            if before == after:
                raise UpdateScenarioError(f"replace change {index} before/after must differ")
        elif op == "write":
            if not isinstance(change.get("content"), str):
                raise UpdateScenarioError(f"write change {index} requires string content")
            expected = change.get("expectedSha256")
            if expected is not None and not re.fullmatch(r"[0-9a-f]{64}", str(expected).lower()):
                raise UpdateScenarioError(f"write change {index} has invalid expectedSha256")
        elif op == "delete":
            expected = str(change.get("expectedSha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise UpdateScenarioError(f"delete change {index} requires expectedSha256")
        elif op == "assert_contains":
            text = change.get("text")
            if not isinstance(text, str) or not text:
                raise UpdateScenarioError(f"assert_contains change {index} requires non-empty text")
        changes.append(change)

    return UpdateScenario(
        path=path,
        digest=_sha256_bytes(raw),
        from_version=from_version,
        to_version=to_version,
        title=title,
        commit_message=commit_message,
        base_blobs=base_blobs,
        contracts=tuple(contracts),
        changes=tuple(changes),
    )


def scenario_marker(scenario: UpdateScenario) -> str:
    return f"Agent-DevTools-Scenario-SHA256: {scenario.digest}"


def _project_version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UpdateScenarioError(f"cannot read project VERSION: {exc}") from exc



def _contract_value(root: Path, contract: dict[str, Any]) -> tuple[Path, str, re.Match[str]]:
    rel = str(contract["path"])
    symbol = str(contract["symbol"])
    path = _path(root, rel)
    if not path.is_file():
        raise UpdateScenarioError(f"contract source file missing: {rel}")
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"(?m)^({re.escape(symbol)}[ \t]*=[ \t]*)([0-9]+)([ \t]*(?:#.*)?)$"
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise UpdateScenarioError(
            f"contract symbol must have exactly one integer assignment: "
            f"{contract['name']} ({rel}:{symbol}), found {len(matches)}"
        )
    return path, text, matches[0]


def _validate_contract_preimages(root: Path, contracts: tuple[dict[str, Any], ...]) -> None:
    for contract in contracts:
        _, _, match = _contract_value(root, contract)
        current = int(match.group(2))
        before = int(contract["from"])
        after = int(contract["to"])
        if current not in {before, after}:
            raise UpdateScenarioError(
                f"contract {contract['name']} expects {before} or already-applied {after}, found {current}"
            )


def _apply_contracts(root: Path, contracts: tuple[dict[str, Any], ...]) -> tuple[int, int, list[dict[str, Any]]]:
    applied = 0
    unchanged = 0
    rows: list[dict[str, Any]] = []
    for contract in contracts:
        path, text, match = _contract_value(root, contract)
        current = int(match.group(2))
        before = int(contract["from"])
        after = int(contract["to"])
        if current == after:
            unchanged += 1
            rows.append({
                "name": contract["name"],
                "path": contract["path"],
                "symbol": contract["symbol"],
                "from": before,
                "to": after,
                "status": "unchanged" if before == after else "already-applied",
            })
            continue
        if current != before:
            raise UpdateScenarioError(
                f"contract {contract['name']} expects {before}, found {current}"
            )
        replacement = match.group(1) + str(after) + match.group(3)
        updated = text[:match.start()] + replacement + text[match.end():]
        path.write_text(updated, encoding="utf-8")
        applied += 1
        rows.append({
            "name": contract["name"],
            "path": contract["path"],
            "symbol": contract["symbol"],
            "from": before,
            "to": after,
            "status": "applied",
        })
    return applied, unchanged, rows


def _assert_contract_targets(root: Path, contracts: tuple[dict[str, Any], ...]) -> None:
    for contract in contracts:
        _, _, match = _contract_value(root, contract)
        current = int(match.group(2))
        expected = int(contract["to"])
        if current != expected:
            raise UpdateScenarioError(
                f"contract {contract['name']} target mismatch: expected {expected}, found {current}"
            )


def _contract_payload_keys(symbol: str) -> set[str]:
    parts = [part.lower() for part in symbol.split("_") if part]
    keys: set[str] = set()
    if len(parts) >= 2 and parts[-2:] == ["contract", "version"]:
        stem = parts[:-2]
        if stem:
            keys.add(stem[0] + "".join(part.title() for part in stem[1:]) + "ContractVersion")
    if symbol == "WORKFLOW_CONTRACT_VERSION":
        keys.update({"formatVersion", "workflowContractVersion"})
    elif symbol == "CLI_CONTRACT_VERSION":
        keys.add("cliContractVersion")
    return keys


def _expression_refs_contract(node: ast.AST, symbol: str, payload_keys: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id == symbol
    if isinstance(node, ast.Attribute):
        return node.attr in payload_keys
    if isinstance(node, ast.Subscript):
        key = node.slice
        return isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value in payload_keys
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        if not node.args:
            return False
        key = node.args[0]
        return isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value in payload_keys
    return False


def _frozen_contract_pair(left: ast.AST, right: ast.AST, *, before: int, symbol: str, payload_keys: set[str]) -> bool:
    return (
        isinstance(left, ast.Constant)
        and left.value == before
        and _expression_refs_contract(right, symbol, payload_keys)
    ) or (
        isinstance(right, ast.Constant)
        and right.value == before
        and _expression_refs_contract(left, symbol, payload_keys)
    )


def _stale_exact_contract_assertions(root: Path, contracts: tuple[dict[str, Any], ...]) -> list[str]:
    """Find historical tests that freeze an aggregate contract at the pre-transition value."""
    tests = root / "tests"
    if not tests.is_dir():
        return []
    stale: set[str] = set()
    for contract in contracts:
        before = int(contract["from"])
        after = int(contract["to"])
        symbol = str(contract["symbol"])
        if before == after:
            continue
        payload_keys = _contract_payload_keys(symbol)
        for path in sorted(tests.glob("test_*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                raise UpdateScenarioError(f"cannot inspect contract regression test {path.name}: {exc}") from exc
            for node in ast.walk(tree):
                frozen = False
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr == "assertEqual" and len(node.args) >= 2:
                        frozen = _frozen_contract_pair(
                            node.args[0], node.args[1],
                            before=before, symbol=symbol, payload_keys=payload_keys,
                        )
                elif isinstance(node, ast.Compare):
                    if len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq) and len(node.comparators) == 1:
                        frozen = _frozen_contract_pair(
                            node.left, node.comparators[0],
                            before=before, symbol=symbol, payload_keys=payload_keys,
                        )
                if frozen:
                    stale.add(f"{path.relative_to(root).as_posix()}:{getattr(node, 'lineno', '?')}")
    return sorted(stale)


def _assert_base(root: Path, scenario: UpdateScenario) -> None:
    version = _project_version(root)
    if version != scenario.from_version:
        raise UpdateScenarioError(f"scenario expects VERSION {scenario.from_version}, found {version}")
    for rel, expected in scenario.base_blobs.items():
        path = _path(root, rel)
        if not path.is_file():
            raise UpdateScenarioError(f"baseline file missing: {rel}")
        actual = _git_blob_sha1(path.read_bytes())
        if actual != expected:
            raise UpdateScenarioError(f"baseline blob mismatch {rel}: expected {expected}, found {actual}")


def apply_scenario(root: Path, scenario: UpdateScenario) -> dict[str, Any]:
    root = root.resolve()
    _assert_base(root, scenario)
    _validate_contract_preimages(root, scenario.contracts)
    contract_applied, contract_unchanged, contract_rows = _apply_contracts(root, scenario.contracts)
    applied = 0
    unchanged = 0
    rows: list[dict[str, str]] = []

    for change in scenario.changes:
        op = str(change["op"])
        rel = str(change["path"])
        path = _path(root, rel)

        if op == "assert_contains":
            if not path.is_file():
                raise UpdateScenarioError(f"assert_contains file missing: {rel}")
            if str(change["text"]) not in path.read_text(encoding="utf-8"):
                raise UpdateScenarioError(f"assert_contains failed: {rel}")
            unchanged += 1
            rows.append({"op": op, "path": rel, "status": "pass"})
            continue

        if op == "replace":
            if not path.is_file():
                raise UpdateScenarioError(f"replace file missing: {rel}")
            text = path.read_text(encoding="utf-8")
            before = str(change["before"])
            after = str(change["after"])
            before_count = text.count(before)
            after_count = text.count(after)
            if before_count == 1 and after_count == 0:
                path.write_text(text.replace(before, after, 1), encoding="utf-8")
                applied += 1
                rows.append({"op": op, "path": rel, "status": "applied"})
            elif before_count == 0 and after_count == 1:
                unchanged += 1
                rows.append({"op": op, "path": rel, "status": "already-applied"})
            else:
                raise UpdateScenarioError(
                    f"replace state is ambiguous for {rel}: before={before_count}, after={after_count}"
                )
            continue

        if op == "write":
            desired = str(change["content"]).encode("utf-8")
            if path.exists():
                if not path.is_file():
                    raise UpdateScenarioError(f"write target is not a file: {rel}")
                current = path.read_bytes()
                if current == desired:
                    unchanged += 1
                    rows.append({"op": op, "path": rel, "status": "already-applied"})
                    continue
                expected = change.get("expectedSha256")
                if expected is None:
                    raise UpdateScenarioError(
                        f"write refuses to overwrite differing file without expectedSha256: {rel}"
                    )
                actual = _sha256_bytes(current)
                if actual != str(expected).lower():
                    raise UpdateScenarioError(
                        f"write preimage mismatch {rel}: expected {expected}, found {actual}"
                    )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(desired)
            applied += 1
            rows.append({"op": op, "path": rel, "status": "applied"})
            continue

        if op == "delete":
            if not path.exists():
                unchanged += 1
                rows.append({"op": op, "path": rel, "status": "already-applied"})
                continue
            if not path.is_file():
                raise UpdateScenarioError(f"delete target is not a file: {rel}")
            actual = _sha256_bytes(path.read_bytes())
            expected = str(change["expectedSha256"]).lower()
            if actual != expected:
                raise UpdateScenarioError(
                    f"delete preimage mismatch {rel}: expected {expected}, found {actual}"
                )
            path.unlink()
            applied += 1
            rows.append({"op": op, "path": rel, "status": "applied"})

    _assert_contract_targets(root, scenario.contracts)
    stale = _stale_exact_contract_assertions(root, scenario.contracts)
    if stale:
        raise UpdateScenarioError(
            "stale exact contract-version assertion(s) freeze a historical aggregate version: "
            + ", ".join(stale)
            + "; historical regression tests should assert their capability/minimum, "
              "while the update scenario owns the public contract transition"
        )

    return {
        "format": "agent-devtools-update-scenario-result",
        "formatVersion": 1,
        "scenarioSha256": scenario.digest,
        "fromVersion": scenario.from_version,
        "toVersion": scenario.to_version,
        "contractApplied": contract_applied,
        "contractUnchanged": contract_unchanged,
        "contracts": contract_rows,
        "applied": applied,
        "unchanged": unchanged,
        "changes": rows,
    }

def preflight_scenarios(root: Path, scenarios: tuple[UpdateScenario, ...] | list[UpdateScenario]) -> dict[str, Any]:
    """Validate one or more scenarios against a sparse temporary projection without mutating the project."""
    root = root.resolve()
    sequence = tuple(scenarios)
    if not sequence:
        return {
            "format": "agent-devtools-update-preflight",
            "formatVersion": 1,
            "status": "pass",
            "scenarioCount": 0,
            "results": [],
        }

    touched = {"VERSION"}
    copy_tests = False
    for scenario in sequence:
        touched.update(scenario.base_blobs)
        touched.update(str(item["path"]) for item in scenario.changes)
        touched.update(str(item["path"]) for item in scenario.contracts)
        copy_tests = copy_tests or bool(scenario.contracts)

    with tempfile.TemporaryDirectory(prefix="agent-devtools-scenario-preflight-") as td:
        sandbox = Path(td) / "project"
        sandbox.mkdir()
        if copy_tests and (root / "tests").is_dir():
            shutil.copytree(root / "tests", sandbox / "tests")
        for rel in sorted(touched):
            source = _path(root, rel)
            if not source.is_file():
                continue
            target = _path(sandbox, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        results = []
        for scenario in sequence:
            result = apply_scenario(sandbox, scenario)
            results.append(result)
            (sandbox / "VERSION").write_text(scenario.to_version + "\n", encoding="utf-8")

    return {
        "format": "agent-devtools-update-preflight",
        "formatVersion": 1,
        "status": "pass",
        "scenarioCount": len(sequence),
        "fromVersion": sequence[0].from_version,
        "toVersion": sequence[-1].to_version,
        "results": results,
    }

