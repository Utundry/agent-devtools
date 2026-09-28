# Stage N2.4 — One-action Handoff / Resume

A lost chat or agent session should not force reconstruction of current work from conversational memory.

`handoff create` produces one manifest-verified bundle containing the profile-aware preservation artifact selected by `preserve`, a compact resume briefing, a human-readable briefing, and an exact working-state fingerprint.

```bash
python devtools/agent/agent.py handoff create
python devtools/agent/agent.py handoff inspect handoff.agent-handoff.zip
python devtools/agent/agent.py handoff resume handoff.agent-handoff.zip
```

`handoff resume` verifies the outer archive, verifies/restores the embedded preservation artifact through existing conflict-safe rules, then builds a fresh resume briefing from the restored target state. Divergent state fails closed unless `--force` is explicitly supplied.

Duplicate ZIP members are rejected fail-closed so archive ambiguity cannot bypass manifest expectations.

**The LLM may lose a session. The project should carry enough verified state to resume mechanically.**
