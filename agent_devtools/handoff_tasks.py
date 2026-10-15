"""File-based, opt-in agent task handoffs. stdlib only, no daemon or Git state."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_devtools.core.io import atomic_json_write

FORMAT = "agent-devtools-task-handoff"
RESULT_FORMAT = "agent-devtools-task-handoff-result"
DEFAULTS = {"enabled": True, "returnMode": "user-mediated", "ttlDays": None,
            "zombieAfterDays": 30, "maxLifetimeDays": None, "autoCleanup": True,
            "retainResultsDays": 30, "transport": "file", "autoClaim": False,
            "requireDeleteConfirmation": True}

class TaskHandoffError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc)


def stamp(moment):
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(canonical(data)).hexdigest()


def settings(root):
    cfg = root / "agent-tools.json"
    data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.is_file() else {}
    overrides = data.get("handoff", {})
    if not isinstance(overrides, dict) or set(overrides) - set(DEFAULTS):
        raise TaskHandoffError("invalid handoff configuration keys")
    result = {**DEFAULTS, **overrides}
    for field in ("ttlDays", "maxLifetimeDays"):
        if result[field] is not None and (type(result[field]) is not int or result[field] <= 0):
            raise TaskHandoffError(f"{field} must be a positive integer or null")
    for field in ("zombieAfterDays", "retainResultsDays"):
        if type(result[field]) is not int or result[field] <= 0:
            raise TaskHandoffError(f"{field} must be a positive integer")
    if result["returnMode"] not in ("direct", "user-mediated") or result["transport"] != "file":
        raise TaskHandoffError("unsupported handoff return mode or transport")
    for field in ("enabled", "autoCleanup", "autoClaim", "requireDeleteConfirmation"):
        if type(result[field]) is not bool:
            raise TaskHandoffError(f"{field} must be boolean")
    if result["autoClaim"] is True or result["requireDeleteConfirmation"] is False:
        raise TaskHandoffError("automatic claim and unconfirmed deletion are not supported")
    return result


def folder(root):
    return root / ".agent-handoffs"


def _ensure_git_exclude(root):
    """Exclude ephemeral state without rewriting tracked project configuration."""
    exclude = root / ".git" / "info" / "exclude"
    if not exclude.parent.is_dir():
        return
    try:
        current = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        if "/.agent-handoffs/" not in current.splitlines():
            exclude.write_text(current.rstrip("\n") + "\n/.agent-handoffs/\n", encoding="utf-8")
    except OSError as exc:
        raise TaskHandoffError("cannot exclude handoffs from local Git; configure .gitignore") from exc


def item_path(root, ident):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", ident):
        raise TaskHandoffError("invalid handoff id")
    return folder(root) / (ident + ".json")


def load(root, ident):
    path = item_path(root, ident)
    if path.is_symlink():
        raise TaskHandoffError("symlink handoffs are forbidden")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TaskHandoffError(f"handoff not found or damaged: {ident}") from exc
    if obj.get("format") != FORMAT or obj.get("formatVersion") != 1 or obj.get("id") != ident:
        raise TaskHandoffError("unsupported handoff schema")
    return obj


def assignment_hash(obj):
    return digest({field: obj[field] for field in ("id", "kind", "goal", "steps", "createdBy", "createdAtUtc", "expiresAtUtc", "policy")})


def export_assignment(root, ident, destination):
    obj = load(root, ident)
    if obj["status"] not in ("available", "claimed", "result-ready"):
        raise TaskHandoffError("handoff cannot be exported")
    payload = {field: obj[field] for field in ("format", "formatVersion", "id", "kind", "goal", "steps", "createdBy", "createdAtUtc", "expiresAtUtc", "policy", "assignmentSha256")}
    payload["lastMeaningfulActivityAtUtc"] = obj["createdAtUtc"]
    payload["status"] = "available"
    payload["claimedBy"] = payload["claimId"] = payload["claimedAtUtc"] = payload["result"] = None
    destination = Path(destination)
    if destination.exists():
        raise TaskHandoffError("export destination already exists")
    atomic_json_write(destination, payload)
    return {"id": ident, "path": str(destination), "assignmentSha256": obj["assignmentSha256"]}


def import_assignment(root, source):
    try:
        obj = json.loads(Path(source).read_text(encoding="utf-8"))
        ident = obj["id"]
        path = item_path(root, ident)
    except (KeyError, ValueError, OSError) as exc:
        raise TaskHandoffError("invalid assignment file") from exc
    if obj.get("format") != FORMAT or obj.get("formatVersion") != 1 or obj.get("status") != "available":
        raise TaskHandoffError("unsupported assignment")
    if obj.get("assignmentSha256") != assignment_hash(obj):
        raise TaskHandoffError("assignment integrity mismatch")
    if path.exists():
        raise TaskHandoffError("assignment already exists; no automatic overwrite")
    _ensure_git_exclude(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return {"id": ident, "status": "available", "claimed": False}


def receive_result(root, source):
    try:
        result = json.loads(Path(source).read_text(encoding="utf-8"))
        obj = load(root, result["handoffId"])
    except (KeyError, ValueError, OSError) as exc:
        raise TaskHandoffError("invalid receipt file") from exc
    check = dict(result)
    receipt_sha = check.pop("receiptSha256", None)
    if (result.get("format") != RESULT_FORMAT or result.get("formatVersion") != 1
            or result.get("assignmentSha256") != obj.get("assignmentSha256")
            or digest(check) != receipt_sha):
        raise TaskHandoffError("result receipt integrity mismatch")
    if obj["result"] is not None:
        if obj["result"].get("receiptSha256") != receipt_sha:
            raise TaskHandoffError("conflicting result for existing handoff")
        return {"id": obj["id"], "status": "already-received", "receiptSha256": receipt_sha}
    if obj["status"] != "available":
        raise TaskHandoffError("origin handoff not available for result")
    obj["result"] = result
    obj["status"] = "result-ready"
    atomic_json_write(item_path(root, obj["id"]), obj)
    return {"id": obj["id"], "status": "result-ready", "receiptSha256": receipt_sha}


def create(root, *, goal, kind="continuation", steps=(), owner="", ttl_days=None, return_mode=None):
    opts = settings(root)
    if not opts["enabled"]:
        raise TaskHandoffError("handoff is disabled")
    if kind not in ("continuation", "delegation") or not goal.strip():
        raise TaskHandoffError("kind or goal invalid")
    mode = return_mode or opts["returnMode"]
    if mode not in ("user-mediated", "direct"):
        raise TaskHandoffError("unsupported return mode")
    ttl = ttl_days if ttl_days is not None else opts["ttlDays"]
    if ttl is not None and (type(ttl) is not int or ttl <= 0):
        raise TaskHandoffError("ttl must be positive")
    moment = now()
    ident = secrets.token_hex(12)
    obj = {"format": FORMAT, "formatVersion": 1, "id": ident,
           "kind": kind, "goal": goal.strip(), "steps": list(steps), "createdBy": owner,
           "createdAtUtc": stamp(moment), "lastMeaningfulActivityAtUtc": stamp(moment),
           "status": "available", "claimedBy": None, "claimId": None,
           "claimedAtUtc": None, "expiresAtUtc": stamp(moment + timedelta(days=ttl)) if ttl else None,
           "policy": {**opts, "ttlDays": ttl, "returnMode": mode}, "result": None}
    obj["assignmentSha256"] = assignment_hash(obj)
    path = item_path(root, ident)
    _ensure_git_exclude(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return obj


def flags(obj, moment=None):
    moment = moment or now()
    idle = (moment - parse_time(obj["lastMeaningfulActivityAtUtc"])).total_seconds() / 86400
    age = (moment - parse_time(obj["createdAtUtc"])).total_seconds() / 86400
    policy = obj["policy"]
    zombie = idle >= policy["zombieAfterDays"] or (policy["maxLifetimeDays"] is not None and age >= policy["maxLifetimeDays"])
    expired = bool(obj["expiresAtUtc"] and moment >= parse_time(obj["expiresAtUtc"]))
    return {"zombie": zombie, "expired": expired, "idleDays": round(idle, 2), "ageDays": round(age, 2)}


def listing(root):
    result = []
    for path in sorted(folder(root).glob("*.json")):
        try:
            obj = load(root, path.stem)
            result.append({"id": obj["id"], "goal": obj["goal"], "kind": obj["kind"],
                           "status": obj["status"], "claimedBy": obj["claimedBy"], **flags(obj)})
        except TaskHandoffError:
            result.append({"id": path.stem, "status": "invalid", "action": "inspect manually"})
    return result


def claim(root, ident, actor):
    if not actor.strip():
        raise TaskHandoffError("--actor is required")
    path = item_path(root, ident)
    lock = path.with_suffix(".claim-lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise TaskHandoffError("handoff is already being claimed; inspect before retry") from exc
    try:
        with os.fdopen(fd, "w") as f:
            f.write(actor)
        obj = load(root, ident)
        if obj["status"] != "available" or flags(obj)["expired"]:
            raise TaskHandoffError("handoff is not available for claim")
        obj["status"] = "claimed"
        obj["claimedBy"] = actor
        obj["claimId"] = secrets.token_hex(16)
        obj["claimedAtUtc"] = stamp(now())
        obj["lastMeaningfulActivityAtUtc"] = stamp(now())
        atomic_json_write(path, obj)
        return obj
    finally:
        lock.unlink(missing_ok=True)


def complete(root, ident, *, claim_id, outcome, summary, receipt_out=None):
    obj = load(root, ident)
    if obj["status"] != "claimed" or not secrets.compare_digest(obj.get("claimId") or "", claim_id):
        raise TaskHandoffError("claim token mismatch or handoff not claimed")
    if outcome not in ("pass", "fail", "blocked") or not summary.strip():
        raise TaskHandoffError("invalid result")
    if receipt_out is not None and Path(receipt_out).exists():
        raise TaskHandoffError("receipt destination already exists")
    result = {"format": RESULT_FORMAT, "formatVersion": 1, "handoffId": ident,
              "assignmentSha256": obj["assignmentSha256"], "executionStatus": "completed", "outcome": outcome,
              "summary": summary.strip(), "completedAtUtc": stamp(now())}
    result["receiptSha256"] = digest(result)
    obj["status"] = "result-ready"
    obj["result"] = result
    obj["lastMeaningfulActivityAtUtc"] = stamp(now())
    atomic_json_write(item_path(root, ident), obj)
    if receipt_out is not None:
        atomic_json_write(Path(receipt_out), result)
    return result


def close(root, ident, *, claim_id=None, receipt_sha=None, confirm=False):
    obj = load(root, ident)
    if not confirm:
        raise TaskHandoffError("explicit --yes required")
    if not (claim_id and obj.get("claimId") == claim_id or
            receipt_sha and obj.get("result") and obj["result"].get("receiptSha256") == receipt_sha):
        raise TaskHandoffError("claim token or returned receipt required")
    item_path(root, ident).unlink()
    return {"status": "closed-local", "id": ident}


def discard(root, ident, *, confirm=False):
    if not confirm:
        raise TaskHandoffError("explicit --yes required")
    obj = load(root, ident)
    if obj["status"] != "available":
        raise TaskHandoffError("claimed or completed handoff cannot be discarded")
    item_path(root, ident).unlink()
    return {"status": "deleted-local", "id": ident}


def cleanup(root):
    removed = []
    for item in listing(root):
        if item.get("expired") and item.get("status") == "available":
            path = item_path(root, item["id"])
            if load(root, item["id"])["status"] == "available":
                path.unlink()
                removed.append(item["id"])
    return {"removed": removed}
