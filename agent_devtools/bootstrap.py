from __future__ import annotations

import hashlib
import json
import os
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_devtools.check.config import load_check_config
from agent_devtools.check.policy import load_policy
from agent_devtools.core.io import atomic_json_write
from agent_devtools.presets import Preset, get_preset, list_presets
from agent_devtools.onboarding import ensure as ensure_onboarding, status as onboarding_status

BOOTSTRAP_FORMAT = "agent-devtools-bootstrap-plan"
BOOTSTRAP_VERSION = 1
VENDOR_REL = Path("devtools/agent")
GITIGNORE_REL = Path(".gitignore")
GITIGNORE_BEGIN = "# >>> Agent DevTools bootstrap >>>"
GITIGNORE_END = "# <<< Agent DevTools bootstrap <<<"
GITIGNORE_ENTRIES = (
    "devtools/agent/",
    ".agent-cache/",
    ".agent-work/",
    ".agent-bootstrap-report.json",
)
_DYNAMIC_DIRS = {".git", ".agent-cache", ".agent-work", "__pycache__", ".pytest_cache"}
_DYNAMIC_FILES = {"MANIFEST.sha256"}
_BOOTSTRAP_SELF_NAMES = {
    "BOOTSTRAP_AGENT_DEVTOOLS.py",
    "AGENT-READ-ME-FIRST.md",
    "AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py",
}
_BOOTSTRAP_SELF_PREFIXES = ("agent-devtools-bootstrap-", "AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME-")
_RUNTIME_TOP_LEVEL_FILES = {"agent.py"}
_RUNTIME_TOP_LEVEL_DIRS = {"agent_devtools", "presets"}
_SOURCE_SKIP_DIRS = {
    ".git", ".agent-cache", ".agent-work", ".venv", "venv", "node_modules", "vendor",
    "dist", "build", "coverage", "tests", "test", "docs", "devtools", "scripts",
}


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class IntentDetection:
    profile_id: str
    development: bool
    preset_id: str | None
    confidence: str
    reasons: tuple[str, ...]
    needs_stack: bool = False


def _is_bootstrap_artifact(path: Path) -> bool:
    name = path.name
    return name in _BOOTSTRAP_SELF_NAMES or any(name.startswith(prefix) for prefix in _BOOTSTRAP_SELF_PREFIXES)


def _top_level_files(root: Path, pattern: str) -> list[Path]:
    return [path for path in root.glob(pattern) if path.is_file() and not _is_bootstrap_artifact(path)]


def detect_stack(root: Path) -> dict[str, Any]:
    """Describe stack signals without forcing bootstrap artifacts into project evidence."""
    root = root.resolve()
    signals: list[str] = []
    technologies: list[str] = []
    if (root / "pyproject.toml").is_file() or (root / "requirements.txt").is_file() or _top_level_files(root, "*.py"):
        technologies.append("python"); signals.append("Python files/metadata")
    if (root / "composer.json").is_file() or any(root.glob("*.php")):
        technologies.append("php"); signals.append("composer.json/PHP files")
    if (root / "package.json").is_file():
        technologies.append("node"); signals.append("package.json")
        tokens = _package_tokens(root)
        if "typescript" in tokens or (root / "tsconfig.json").is_file(): technologies.append("typescript")
        if "vue" in tokens or any(root.glob("**/*.vue")): technologies.append("vue")
        if any("vite" in token for token in tokens): technologies.append("vite")
        if "react" in tokens: technologies.append("react")
    if (root / "Cargo.toml").is_file(): technologies.append("rust"); signals.append("Cargo.toml")
    if (root / "go.mod").is_file(): technologies.append("go"); signals.append("go.mod")
    if (root / "pom.xml").is_file() or (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        technologies.append("java"); signals.append("Maven/Gradle metadata")
    return {"technologies": list(dict.fromkeys(technologies)), "signals": signals}


def classify_intent(intent: str, *, stack: str | None = None) -> IntentDetection:
    """Classify a human goal conservatively; unsupported/ambiguous stacks remain explicit."""
    text = f"{intent} {stack or ''}".strip().lower()
    dev_words = ("разработ", "код", "прилож", "сервис", "api", "сайт", "frontend", "backend", "software", "develop", "development", "program", "программ", "build", "implement", "coding", "cli")
    research_words = ("исслед", "research", "изуч", "сравн", "обосн", "источник")
    document_words = ("документ", "статья", "отчет", "отчёт", "memo", "proposal", "договор", "текст")
    analysis_words = ("анализ", "analysis", "модель", "расчет", "расчёт", "оцен")
    development = bool(stack and stack.strip()) or any(w in text for w in dev_words)
    if development:
        preset = None
        reasons: list[str] = ["intent indicates software development"]
        if "php" in text and ("vue" in text or "vite" in text): preset = "php-vue-vite"
        elif "php" in text and ("phpunit" in text or "composer" in text): preset = "php-phpunit"
        elif "python" in text and ("pytest" in text or "fastapi" in text or "django" in text or "flask" in text): preset = "python-pytest"
        elif "python" in text: preset = "python-stdlib"
        elif "vue" in text: preset = "vue-vite"
        elif "typescript" in text or "node" in text: preset = "node-typescript"
        if preset:
            reasons.append(f"stack text maps to supported preset {preset}")
            return IntentDetection("development", True, preset, "high", tuple(reasons), False)
        return IntentDetection("development", True, None, "medium", tuple(reasons + ["development stack is not specific enough for a safe preset"]), True)
    if any(w in text for w in research_words):
        return IntentDetection("research", False, None, "high", ("intent indicates research",), False)
    if any(w in text for w in document_words):
        return IntentDetection("document", False, None, "medium", ("intent indicates document-oriented work",), False)
    if any(w in text for w in analysis_words):
        return IntentDetection("analysis", False, None, "medium", ("intent indicates analytical work",), False)
    return IntentDetection("general", False, None, "low", ("no development-specific intent detected",), False)


def _managed_gitignore_block() -> str:
    return "\n".join((GITIGNORE_BEGIN, *GITIGNORE_ENTRIES, GITIGNORE_END))


def _gitignore_action(root: Path) -> str:
    path = root / GITIGNORE_REL
    if not path.exists():
        return "create"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "update-managed-block"
    block = _managed_gitignore_block()
    if block in text:
        return "unchanged"
    if GITIGNORE_BEGIN in text or GITIGNORE_END in text:
        return "update-managed-block"
    return "append"


def _ensure_gitignore(root: Path) -> str:
    path = root / GITIGNORE_REL
    action = _gitignore_action(root)
    block = _managed_gitignore_block()
    if action == "unchanged":
        return action

    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeDecodeError):
        text = ""

    start = text.find(GITIGNORE_BEGIN)
    end = text.find(GITIGNORE_END)
    if start >= 0 and end >= start:
        end += len(GITIGNORE_END)
        prefix = text[:start].rstrip("\n")
        suffix = text[end:].lstrip("\n")
        parts = [part for part in (prefix, block, suffix) if part]
        updated = "\n\n".join(parts) + "\n"
    else:
        prefix = text.rstrip("\n")
        updated = ((prefix + "\n\n") if prefix else "") + block + "\n"

    path.write_text(updated, encoding="utf-8")
    return action


@dataclass(frozen=True)
class Detection:
    preset_id: str | None
    confidence: str
    reasons: tuple[str, ...]
    candidates: tuple[str, ...]


def _read_json_if_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _package_tokens(root: Path) -> set[str]:
    package = _read_json_if_object(root / "package.json")
    tokens: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = package.get(key)
        if isinstance(section, dict):
            tokens.update(str(name).lower() for name in section)
    scripts = package.get("scripts")
    if isinstance(scripts, dict):
        tokens.update(str(name).lower() for name in scripts)
        tokens.update(str(value).lower() for value in scripts.values())
    return tokens


def detect_project(root: Path) -> Detection:
    root = root.resolve()
    if not root.exists():
        return Detection(None, "none", ("target directory does not exist yet",), tuple(p.preset_id for p in list_presets()))
    if not root.is_dir():
        raise BootstrapError(f"Target is not a directory: {root}")

    entries = [p.name for p in root.iterdir() if p.name not in _DYNAMIC_DIRS and not _is_bootstrap_artifact(p)]
    if not entries:
        return Detection(None, "none", ("target directory is empty",), tuple(p.preset_id for p in list_presets()))

    has_python = any((root / name).exists() for name in ("pyproject.toml", "requirements.txt", "pytest.ini", "setup.py", "setup.cfg"))
    requirements_text = (root / "requirements.txt").read_text(encoding="utf-8", errors="ignore").lower() if (root / "requirements.txt").is_file() else ""
    pyproject_text = (root / "pyproject.toml").read_text(encoding="utf-8", errors="ignore").lower() if (root / "pyproject.toml").is_file() else ""
    has_pytest = (root / "pytest.ini").is_file() or "pytest" in requirements_text or "pytest" in pyproject_text
    has_composer = (root / "composer.json").is_file()
    has_phpunit = any((root / name).is_file() for name in ("phpunit.xml", "phpunit.xml.dist"))
    has_package = (root / "package.json").is_file()
    tokens = _package_tokens(root) if has_package else set()
    has_vue = "vue" in tokens or any(root.glob("*.vue")) or any(root.glob("src/*.vue"))
    has_vite = any("vite" in token for token in tokens)
    has_ts = (root / "tsconfig.json").is_file() or "typescript" in tokens

    if has_composer and has_package and has_vue:
        return Detection("php-vue-vite", "high", ("composer.json found", "package.json with Vue/Vite signals found"), ("php-vue-vite",))
    if has_python:
        preset = "python-pytest" if has_pytest else "python-stdlib"
        reasons = ["Python project metadata found"]
        reasons.append("pytest signal found" if has_pytest else "no pytest signal; stdlib/unittest is safer")
        return Detection(preset, "high", tuple(reasons), (preset,))
    if has_composer:
        if has_phpunit:
            return Detection("php-phpunit", "high", ("composer.json found", "phpunit configuration found"), ("php-phpunit",))
        return Detection(None, "low", ("composer.json found but PHPUnit could not be confirmed",), ("php-phpunit",))
    if has_package and has_vue:
        reason = "package.json with Vue signal found" + ("; Vite also found" if has_vite else "")
        return Detection("vue-vite", "high", (reason,), ("vue-vite", "node-typescript"))
    if has_package and has_ts:
        return Detection("node-typescript", "high", ("package.json and TypeScript signals found",), ("node-typescript",))
    if has_package:
        return Detection(None, "low", ("package.json found but TypeScript/Vue signals are insufficient for a safe preset choice",), ("node-typescript", "vue-vite"))
    python_files = _top_level_files(root, "*.py")
    if python_files:
        return Detection("python-stdlib", "medium", ("top-level Python source found",), ("python-stdlib", "python-pytest"))
    return Detection(None, "none", ("no supported stack could be inferred safely",), tuple(p.preset_id for p in list_presets()))


def _python_source_roots(root: Path) -> list[str]:
    roots: list[str] = []
    for child in sorted(root.iterdir()) if root.is_dir() else []:
        if not child.is_dir() or child.name in _SOURCE_SKIP_DIRS or child.name.startswith("."):
            continue
        try:
            has_python = any(child.glob("*.py")) or any(child.glob("*/*.py"))
        except OSError:
            has_python = False
        if has_python:
            roots.append(child.name)
    if (root / "src").is_dir() and "src" not in roots:
        roots.insert(0, "src")
    return roots


def _adapt_python_layout(files: dict[str, Any], root: Path, preset_id: str) -> tuple[dict[str, Any], list[str]]:
    if preset_id not in {"python-stdlib", "python-pytest"}:
        return files, []
    roots = _python_source_roots(root)
    if not roots:
        return files, []
    result = deepcopy(files)
    tools = result.get("agent-tools.json")
    policy = result.get("agent-check.policy.json")
    if not isinstance(tools, dict) or not isinstance(policy, dict):
        return result, []

    source_patterns = [f"{name}/**" for name in roots]
    test_patterns = ["tests/**"] if (root / "tests").is_dir() else []
    check = tools.get("check") if isinstance(tools.get("check"), dict) else {}
    commands = check.get("commands") if isinstance(check.get("commands"), dict) else {}
    compile_cmd = commands.get("compile") if isinstance(commands.get("compile"), dict) else None
    if compile_cmd is not None:
        compile_targets = list(roots)
        if (root / "tests").is_dir():
            compile_targets.append("tests")
        compile_cmd["argv"] = ["{python}", "-m", "compileall", "-q", *compile_targets]
        compile_cmd["inputs"] = [*source_patterns, *test_patterns]
    tests_cmd = commands.get("tests") if isinstance(commands.get("tests"), dict) else None
    if tests_cmd is not None:
        extras = [item for item in tests_cmd.get("inputs", []) if item not in {"src/**", "tests/**"}]
        tests_cmd["inputs"] = [*source_patterns, *test_patterns, *extras]
        env = tests_cmd.get("env") if isinstance(tests_cmd.get("env"), dict) else {}
        env["PYTHONPATH"] = os.pathsep.join(f"{{root}}/{name}" for name in roots)
        tests_cmd["env"] = env

    sources = policy.get("sources") if isinstance(policy.get("sources"), list) else []
    for source in sources:
        if not isinstance(source, dict) or source.get("description") != "Python source":
            continue
        source["patterns"] = [*source_patterns, "*.py"]
    return result, roots


def rendered_preset(root: Path, preset: Preset) -> tuple[dict[str, Any], list[str]]:
    files = deepcopy(preset.files)
    return _adapt_python_layout(files, root, preset.preset_id)


def _iter_vendor_files(source_root: Path):
    """Yield only the portable runtime payload.

    Bootstrap deliberately vendors the executable toolkit, not its own development
    repository. Tests, fixtures, reference projects, docs, release evidence and
    consumer-specific examples are source-kit concerns and must never leak into a
    consumer project.
    """
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source_root)
        if not rel.parts:
            continue
        top = rel.parts[0]
        if len(rel.parts) == 1:
            if rel.as_posix() not in _RUNTIME_TOP_LEVEL_FILES:
                continue
        elif top not in _RUNTIME_TOP_LEVEL_DIRS:
            continue
        if any(part in _DYNAMIC_DIRS for part in rel.parts):
            continue
        if path.name in _DYNAMIC_FILES or path.suffix == ".pyc":
            continue
        yield rel, path


def _runtime_version(root: Path) -> str | None:
    path = root / "agent_devtools" / "__init__.py"
    if not path.is_file():
        return None
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line.startswith("__version__") or "=" not in line:
                continue
            value = line.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                return value[1:-1]
    except (OSError, UnicodeDecodeError):
        return None
    return None


def _tree_fingerprint(root: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    count = 0
    if not root.is_dir():
        return "", 0
    for rel, path in _iter_vendor_files(root):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        h.update(rel.as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(digest.encode("ascii"))
        h.update(b"\n")
        count += 1
    return h.hexdigest(), count


def _config_action(root: Path, rendered: dict[str, Any]) -> str:
    existing = []
    changed = []
    for rel, content in rendered.items():
        path = root / rel
        if not path.exists():
            continue
        existing.append(rel)
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            changed.append(rel)
            continue
        if current != content:
            changed.append(rel)
    if not existing:
        return "create"
    if changed:
        return "replace-required"
    if len(existing) == len(rendered):
        return "unchanged"
    return "complete-missing"


def build_plan(target: Path, *, preset_id: str | None = None, source_root: Path | None = None) -> dict[str, Any]:
    target = target.resolve()
    source_root = (source_root or Path(__file__).resolve().parents[1]).resolve()
    detection = detect_project(target)
    selected = preset_id or detection.preset_id
    if preset_id is not None:
        get_preset(preset_id)  # validate before constructing the plan
    rendered: dict[str, Any] = {}
    roots: list[str] = []
    if selected is not None:
        rendered, roots = rendered_preset(target, get_preset(selected))

    source_version = _runtime_version(source_root)
    source_fp, source_count = _tree_fingerprint(source_root)
    vendor_root = target / VENDOR_REL
    target_version = _runtime_version(vendor_root)
    vendor_fp, vendor_count = _tree_fingerprint(vendor_root)
    if not vendor_root.exists():
        vendor_action = "create"
    elif source_fp == vendor_fp and source_count == vendor_count:
        vendor_action = "unchanged"
    else:
        vendor_action = "replace-required"

    config_action = _config_action(target, rendered) if rendered else "needs-preset"
    ready = selected is not None
    return {
        "format": BOOTSTRAP_FORMAT,
        "formatVersion": BOOTSTRAP_VERSION,
        "target": str(target),
        "targetExists": target.exists(),
        "mode": "existing" if target.exists() and any(target.iterdir()) else "new",
        "detection": {
            "preset": detection.preset_id,
            "confidence": detection.confidence,
            "reasons": list(detection.reasons),
            "candidates": list(detection.candidates),
        },
        "selectedPreset": selected,
        "pythonSourceRoots": roots,
        "vendor": {
            "path": VENDOR_REL.as_posix(),
            "action": vendor_action,
            "sourceVersion": source_version,
            "targetVersion": target_version,
            "sourceFingerprint": source_fp,
            "sourceFiles": source_count,
            "targetFingerprint": vendor_fp or None,
            "targetFiles": vendor_count,
        },
        "config": {
            "action": config_action,
            "files": sorted(rendered),
        },
        "gitignore": {
            "path": GITIGNORE_REL.as_posix(),
            "action": _gitignore_action(target),
            "entries": list(GITIGNORE_ENTRIES),
            "managedBlock": True,
        },
        "onboarding": onboarding_status(target),
        "readyToApply": ready,
        "suggestedCommands": [
            "python devtools/agent/agent.py doctor",
            "python devtools/agent/agent.py context validate",
            "python devtools/agent/agent.py context ensure",
            "python devtools/agent/agent.py check plan --profile affected --explain",
        ],
    }


def _copy_vendor(source_root: Path, destination: Path) -> None:
    tmp = destination.parent / f".{destination.name}.bootstrap-building"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=False)
    for rel, source in _iter_vendor_files(source_root):
        target = tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if destination.exists():
        shutil.rmtree(destination)
    tmp.replace(destination)




def _neutral_workspace_config(profile_id: str) -> dict[str, Any]:
    if profile_id not in {"research", "analysis", "document", "general"}:
        raise BootstrapError(f"neutral workspace profile is not supported: {profile_id}")
    return {
        "version": 1,
        "work": {"profile": profile_id},
        "ignore": [
            ".git/**", ".agent-cache/**", ".agent-work/**", "**/__pycache__/**",
            "node_modules/**", "vendor/**", "dist/**", "build/**", "coverage/**"
        ],
        "context": {
            "exclude": ["devtools/agent/**", ".agent-devtools-bootstrap-kit/**", "agent-devtools-bootstrap-*.py"],
            "lowPriority": ["AGENTS.md", "agent-tools.json"],
            "defaultBudget": 3500
        },
        "preserve": {
            "exclude": []
        },
        "sourceKinds": [
            {"glob": ".agent-knowledge/**", "kind": "knowledge", "weight": 1.35},
            {"glob": "devtools/agent/**", "kind": "tooling", "weight": 0.10}
        ]
    }


def bootstrap_neutral_workspace(target: Path, *, profile_id: str = "general", source_root: Path | None = None) -> dict[str, Any]:
    """Install a promptless non-development workspace without inventing a software stack."""
    target = target.resolve()
    source_root = (source_root or Path(__file__).resolve().parents[1]).resolve()
    target.mkdir(parents=True, exist_ok=True)
    config_path = target / "agent-tools.json"
    if config_path.exists():
        raise BootstrapError("agent-tools.json already exists; use the normal update/profile flow")
    atomic_json_write(config_path, _neutral_workspace_config(profile_id))
    gitignore_action = _ensure_gitignore(target)
    onboarding_action = ensure_onboarding(target)
    vendor = target / VENDOR_REL
    vendor.parent.mkdir(parents=True, exist_ok=True)
    _copy_vendor(source_root, vendor)
    (target / ".agent-cache").mkdir(parents=True, exist_ok=True)
    (target / ".agent-work").mkdir(parents=True, exist_ok=True)
    source_version = _runtime_version(source_root)
    installed_version = _runtime_version(vendor)
    source_fp, source_count = _tree_fingerprint(source_root)
    vendor_fp, vendor_count = _tree_fingerprint(vendor)
    if source_fp != vendor_fp or source_count != vendor_count:
        raise BootstrapError("Neutral workspace runtime verification failed")
    if not source_version or installed_version != source_version:
        raise BootstrapError("Neutral workspace runtime version does not match source kit")
    return {
        "format": "agent-devtools-neutral-workspace-bootstrap", "formatVersion": 1,
        "status": "applied", "target": str(target), "profile": profile_id,
        "projectType": "non-development", "config": "agent-tools.json",
        "policyCreated": False, "runtimeVersion": installed_version,
        "runtimeFiles": vendor_count, "runtimeFingerprint": vendor_fp,
        "gitignore": gitignore_action, "onboarding": onboarding_action
    }

def set_workspace_profile(target: Path, profile_id: str) -> dict[str, Any]:
    if profile_id not in {"research", "analysis", "document", "general"}:
        raise BootstrapError(f"Unsupported non-development profile: {profile_id}")
    path = target.resolve() / "agent-tools.json"
    data = _read_json_if_object(path)
    if not data:
        raise BootstrapError("agent-tools.json is missing or invalid")
    current = str((data.get("work") or {}).get("profile") or "general")
    if current == "development":
        raise BootstrapError("Refusing to downgrade an existing development workspace through non-development specialization")
    data.setdefault("work", {})["profile"] = profile_id
    atomic_json_write(path, data)
    return {"format":"agent-devtools-workspace-specialization","formatVersion":1,"status":"applied","fromProfile":current,"toProfile":profile_id,"development":False}


def specialize_development_workspace(target: Path, preset_id: str, *, source_root: Path | None = None) -> dict[str, Any]:
    """Upgrade a bootstrap-created neutral workspace into a supported development preset."""
    target = target.resolve()
    tools_path = target / "agent-tools.json"
    data = _read_json_if_object(tools_path)
    current = str((data.get("work") or {}).get("profile") or "general")
    if current == "development":
        return {"format":"agent-devtools-workspace-specialization","formatVersion":1,"status":"unchanged","fromProfile":"development","toProfile":"development","preset":preset_id,"development":True}
    if (target / "agent-check.policy.json").exists():
        raise BootstrapError("Neutral workspace has an unexpected development policy; reconcile it before specialization")
    result = apply_plan(target, preset_id=preset_id, source_root=source_root, force=True)
    return {"format":"agent-devtools-workspace-specialization","formatVersion":1,"status":"applied","fromProfile":current,"toProfile":"development","preset":preset_id,"development":True,"plan":result}


def inspect_existing_installation(target: Path) -> dict[str, Any]:
    """Describe an existing consumer installation before a runtime-only upgrade.

    Project configuration belongs to the consumer and is never silently replaced by
    this path. The vendored runtime is disposable, but legacy config accidentally
    stored inside devtools/agent must be migrated out before replacement.
    """
    target = target.resolve()
    vendor = target / VENDOR_REL
    root_config = {
        name: (target / name).is_file()
        for name in ("agent-tools.json", "agent-check.policy.json", "agent-context.map.json")
    }
    legacy_config: list[str] = []
    if vendor.is_dir():
        for name in ("agent-tools.json", "agent-check.policy.json", "agent-context.map.json"):
            if (vendor / name).is_file():
                legacy_config.append((VENDOR_REL / name).as_posix())
    fp, count = _tree_fingerprint(vendor)
    return {
        "vendorExists": vendor.is_dir(),
        "vendorFingerprint": fp or None,
        "vendorRuntimeFiles": count,
        "rootConfig": root_config,
        "legacyConfigInsideVendor": legacy_config,
    }


def upgrade_runtime(target: Path, *, source_root: Path | None = None) -> dict[str, Any]:
    """Replace only the disposable Agent DevTools runtime, preserving project config.

    This is the safe update path for existing consumers. It intentionally does not
    render or overwrite presets/configuration. Existing project contracts are parsed
    after the runtime swap so an incompatible old config fails visibly.
    """
    target = target.resolve()
    source_root = (source_root or Path(__file__).resolve().parents[1]).resolve()
    if not target.is_dir():
        raise BootstrapError(f"Target is not an existing project directory: {target}")

    inspection = inspect_existing_installation(target)

    tools_path = target / "agent-tools.json"
    policy_path = target / "agent-check.policy.json"
    if not tools_path.is_file():
        if inspection["legacyConfigInsideVendor"]:
            raise BootstrapError(
                "No root project configuration exists, while legacy config files are present inside devtools/agent. "
                "Inspect/migrate them before runtime replacement: " + ", ".join(inspection["legacyConfigInsideVendor"])
            )
        raise BootstrapError("No project Agent DevTools configuration found; use bootstrap instead of runtime-only update")
    profile_id = str((_read_json_if_object(tools_path).get("work") or {}).get("profile") or "development")
    development = profile_id == "development"
    if development and not policy_path.is_file():
        raise BootstrapError("Development profile requires agent-check.policy.json before runtime update")

    before_tools = tools_path.read_bytes()
    before_policy = policy_path.read_bytes() if policy_path.is_file() else None
    map_path = target / "agent-context.map.json"
    before_map = map_path.read_bytes() if map_path.is_file() else None

    source_version = _runtime_version(source_root)
    source_fp, source_count = _tree_fingerprint(source_root)
    vendor = target / VENDOR_REL
    current_fp, current_count = _tree_fingerprint(vendor)
    action = "unchanged" if current_fp == source_fp and current_count == source_count else "replace"
    gitignore_action = _ensure_gitignore(target)
    onboarding_action = ensure_onboarding(target)
    if action == "replace":
        vendor.parent.mkdir(parents=True, exist_ok=True)
        _copy_vendor(source_root, vendor)

    if tools_path.read_bytes() != before_tools or (before_policy is not None and policy_path.read_bytes() != before_policy):
        raise BootstrapError("Project Agent DevTools configuration changed during runtime-only update")
    if before_map is not None and map_path.read_bytes() != before_map:
        raise BootstrapError("Project semantic map changed during runtime-only update")

    if development:
        config = load_check_config(target)
        load_policy(config.policy_path)
    after_fp, after_count = _tree_fingerprint(vendor)
    if after_fp != source_fp or after_count != source_count:
        raise BootstrapError("Runtime update verification failed: installed payload fingerprint does not match source kit")
    installed_version = _runtime_version(vendor)
    if not source_version or installed_version != source_version:
        raise BootstrapError("Runtime update verification failed: installed runtime version does not match source kit")

    return {
        "format": "agent-devtools-runtime-upgrade",
        "formatVersion": 1,
        "target": str(target),
        "action": action,
        "beforeFingerprint": current_fp or None,
        "beforeRuntimeFiles": current_count,
        "afterFingerprint": after_fp,
        "afterRuntimeFiles": after_count,
        "sourceVersion": source_version,
        "installedVersion": installed_version,
        "projectConfigPreserved": True,
        "profile": profile_id,
        "developmentPolicyPresent": policy_path.is_file(),
        "semanticMapPreserved": before_map is not None,
        "gitignore": gitignore_action,
        "onboarding": onboarding_action,
        "inspection": inspection,
        "verification": {"configParsed": True, "policyParsed": (True if development else None), "runtimeFingerprintMatched": True, "runtimeVersionMatched": True},
    }

def apply_plan(target: Path, *, preset_id: str | None = None, source_root: Path | None = None, force: bool = False) -> dict[str, Any]:
    target = target.resolve()
    source_root = (source_root or Path(__file__).resolve().parents[1]).resolve()
    try:
        target.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise BootstrapError("Refusing to bootstrap a target inside the Agent DevTools source tree")

    plan = build_plan(target, preset_id=preset_id, source_root=source_root)
    if not plan["readyToApply"]:
        raise BootstrapError("No safe preset could be inferred; pass --preset explicitly")
    if plan["vendor"]["action"] == "replace-required" and not force:
        raise BootstrapError("Existing devtools/agent differs; rerun with --force to replace it")
    if plan["config"]["action"] == "replace-required" and not force:
        raise BootstrapError("Existing Agent DevTools config differs; rerun with --force to replace it")

    target.mkdir(parents=True, exist_ok=True)
    gitignore_action = _ensure_gitignore(target)
    onboarding_action = ensure_onboarding(target)
    vendor = target / VENDOR_REL
    if plan["vendor"]["action"] != "unchanged":
        vendor.parent.mkdir(parents=True, exist_ok=True)
        _copy_vendor(source_root, vendor)

    preset = get_preset(str(plan["selectedPreset"]))
    rendered, _roots = rendered_preset(target, preset)
    for rel, content in rendered.items():
        path = target / rel
        if path.exists() and not force:
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                current = None
            if current == content:
                continue
            # complete-missing permits existing matching files only; mismatches were rejected above.
            if current is not None:
                continue
        atomic_json_write(path, content)

    # Cheap post-apply verification: parse both declarative contracts. Do not run consumer tests.
    config = load_check_config(target)
    load_policy(config.policy_path)
    source_version = _runtime_version(source_root)
    installed_version = _runtime_version(vendor)
    if not source_version or installed_version != source_version:
        raise BootstrapError("Bootstrap verification failed: installed runtime version does not match source kit")
    applied = build_plan(target, preset_id=str(plan["selectedPreset"]), source_root=source_root)
    applied["status"] = "applied"
    applied["performed"] = {
        "vendor": plan["vendor"]["action"],
        "config": plan["config"]["action"],
        "gitignore": gitignore_action,
        "onboarding": onboarding_action,
    }
    applied["verification"] = {
        "configParsed": True,
        "policyParsed": True,
        "runtimeVersionMatched": True,
        "installedVersion": installed_version,
        "consumerTestsRun": False,
    }
    return applied
