# Stage C2 — impact-aware affected context

Stage C2 connects the new local context index to the already-qualified Agent Check impact graph. It does not create a second project classifier.

```bash
python agent.py context affected --changed agent_devtools/context/index.py
python agent.py context affected --base HEAD~1
```

The command:

1. resolves the explicit/Git change set;
2. loads the same `agent-tools.json` and `agent-check.policy.json` used by `agent check`;
3. runs the normal impact expansion plus safety/dependency guards;
4. builds a small retrieval query from effective impact ids, selected application groups and changed-path terms;
5. ensures the disposable context index is current;
6. returns bounded, deduplicated context chunks with path/line provenance and match reasons.

If Check policy/config is unavailable, retrieval still works from changed-path terms; `impactAvailable=false` is reported rather than inventing project semantics. If changed files cannot be discovered because Git/base information is unavailable, the caller must provide explicit `--changed` paths.

The affected-context command is read-only. It does not run checks, mutate project source or create project knowledge outside the disposable context database.
